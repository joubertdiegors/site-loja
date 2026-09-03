"""Saneamento dos estados que deixaram de existir.

Três valores saíram dos enums nesta etapa, e pedidos antigos podem carregá-los:

* ``OrderStatus.REFUNDED`` — o ciclo do pedido não fala mais de reembolso;
* ``PaymentStatus.REFUNDED`` e ``PaymentStatus.PARTIALLY_REFUNDED`` — foram para
  o campo próprio ``refund_status``.

Nada é apagado. O que a migration faz é **traduzir**: um pedido que estava
"reembolsado" no pagamento passa a estar "pago" com o reembolso concluído — que
é o que sempre foi verdade, só que dito em dois campos em vez de um. O
histórico não é tocado.

Além da tradução, ela acerta três coisas que o código novo passa a garantir e
que os dados antigos não têm:

* ``stock_taken`` por item, sem o qual a devolução de estoque não sabe quanto
  devolver;
* ``cancelled_at`` nos pedidos que foram cancelados pelo formulário do Admin e
  ficaram sem data nenhuma;
* ``fulfillment_status = halted`` nos pedidos já cancelados que ficaram com a
  produção andando (o problema P2 da auditoria);
* ``status`` recalculado a partir dos fatos, agora que ele é derivado.

Reversível: a função de volta desfaz a tradução do reembolso. Ela não repõe
``stock_taken``, porque o campo desaparece junto com a migration anterior.
"""

from django.db import migrations, models


def sanear(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    OrderStatusHistory = apps.get_model("orders", "OrderStatusHistory")

    # -- 1. o cancelamento antigo ganha a data que nunca teve --------------
    #
    # Primeiro de tudo, porque as etapas seguintes leem `cancelled_at`: o
    # reembolso usa-o como data, e o ciclo do pedido é derivado dele.
    #
    # O caminho pelo formulário gravava "cancelado" sem data nenhuma. Como o
    # `status` passa a ser derivado dela, sanear sem preenchê-la devolveria à
    # vida pedidos que a loja já tinha cancelado — e que o cliente já sabe que
    # estão cancelados.
    #
    # `status='refunded'` entra aqui também: o valor saiu do enum, e um pedido
    # reembolsado é, por definição, um pedido que não seguiu adiante.
    #
    # A data escolhida é a melhor aproximação disponível, nesta ordem: quando a
    # decisão foi tomada, quando o pedido foi mexido pela última vez, quando ele
    # foi criado. Nenhuma delas é inventada.
    for order in Order.objects.filter(cancelled_at=None).filter(
        models.Q(status="cancelled")
        | models.Q(status="refunded")
        | models.Q(cancellation_status="approved")
    ):
        order.cancelled_at = (
            order.cancellation_decided_at or order.updated_at or order.created_at
        )
        order.save(update_fields=["cancelled_at"])

    # -- 2. o reembolso sai do pagamento e vai para o campo próprio ---------
    for order in Order.objects.filter(payment_status="refunded"):
        order.payment_status = "paid"
        order.refund_status = "refunded"
        order.refunded_amount = order.total
        order.refunded_at = order.cancelled_at or order.updated_at
        order.save(
            update_fields=[
                "payment_status", "refund_status", "refunded_amount", "refunded_at",
            ]
        )

    for order in Order.objects.filter(payment_status="partially_refunded"):
        order.payment_status = "paid"
        order.refund_status = "partially_refunded"
        # O valor devolvido não está gravado em lugar nenhum nos dados antigos.
        # Zero é a única resposta honesta: quem conferir vê que falta registrar,
        # em vez de ler um número inventado pela migration.
        order.save(update_fields=["payment_status", "refund_status"])

    # -- 3. quanto cada item tirou do saldo --------------------------------
    #
    # Os pedidos com estoque baixado precisam do número para poder devolvê-lo.
    # Reconstruído com a mesma regra do `apply_stock`: só itens de estoque, e só
    # variantes que não aceitam encomenda sem saldo.
    #
    # Pedidos que registraram falta de estoque ficam de fora, com zero. Ali a
    # baixa foi menor que a quantidade e não há como saber quanto — e devolver
    # a mais cria saldo que não existe na prateleira, que é o erro mais caro
    # dos dois.
    com_falta = set(
        OrderStatusHistory.objects.filter(event="stock_shortage").values_list(
            "order_id", flat=True
        )
    )
    for order in Order.objects.exclude(stock_applied_at=None).prefetch_related("items"):
        if order.pk in com_falta:
            continue
        for item in order.items.all():
            if item.fulfillment_type == "made_to_order" or not item.variant_id:
                continue
            if getattr(item.variant, "allow_backorder", False):
                continue
            if item.stock_taken != item.quantity:
                item.stock_taken = item.quantity
                item.save(update_fields=["stock_taken"])

    # -- 4. pedido cancelado não fica na fila da oficina -------------------
    #
    # O filtro é `cancelled_at`, preenchido na etapa 1, e **não** `status` — que
    # só a etapa 6 recalcula. Filtrando por `status`, um pedido antigo marcado
    # como "reembolsado" não casava com nada aqui, e saía da migration cancelado
    # e ainda em produção: exatamente a combinação que esta etapa existe para
    # eliminar.
    Order.objects.filter(
        cancelled_at__isnull=False,
        fulfillment_status__in=["not_started", "in_production", "ready"],
    ).update(fulfillment_status="halted")

    # -- 4b. o dinheiro dos cancelamentos antigos precisa ter para onde ir -
    #
    # Um pedido pago e cancelado antes desta etapa ficava com
    # `refund_status='none'` e valor a devolver maior que zero — e
    # `register_refund` só aceita reembolso já aberto, então não havia caminho
    # nenhum para registrar aquela devolução.
    #
    # Abrir como "pendente" não inventa nada: diz que há dinheiro a devolver e
    # que ninguém registrou a devolução ainda, que é a verdade. Quem já foi
    # traduzido na etapa 2 (reembolso concluído ou parcial) fica de fora.
    Order.objects.filter(
        cancelled_at__isnull=False,
        payment_status="paid",
        refund_status="none",
    ).update(refund_status="pending")

    # -- 5. a data de entrega, sem a qual o prazo legal nunca fecha --------
    #
    # A janela de arrependimento corre da entrega, e `is_within_withdrawal_window`
    # trata data vazia como "o prazo nem começou". Sem este preenchimento, todo
    # pedido entregue antes desta etapa aceitaria pedido de devolução para
    # sempre.
    #
    # A melhor aproximação é a data de envio: é o mais perto da entrega que os
    # dados antigos guardam. Ela **antecipa** o fim do prazo em alguns dias, o
    # que seria injusto — então o fallback é a última alteração do pedido, que
    # nunca é anterior ao envio.
    for order in Order.objects.filter(
        fulfillment_status="delivered", delivered_at=None
    ):
        order.delivered_at = order.updated_at or order.shipped_at or order.created_at
        order.save(update_fields=["delivered_at"])

    # -- 6. o ciclo do pedido, agora derivado ------------------------------
    #
    # A mesma regra de `Order.derived_status`, escrita à mão: uma migration não
    # pode chamar o model de hoje, que amanhã muda.
    for order in Order.objects.all():
        if order.cancelled_at is not None:
            novo = "cancelled"
        elif order.fulfillment_status == "delivered":
            novo = "completed"
        elif order.payment_status == "paid":
            novo = "confirmed"
        else:
            novo = "pending"

        # Um pedido cancelado sem cobrança não fica "aguardando pagamento".
        if novo == "cancelled" and order.payment_status in {"pending", "failed"}:
            order.payment_status = "not_charged"
            order.save(update_fields=["payment_status"])

        if order.status != novo:
            order.status = novo
            order.save(update_fields=["status"])


def desfazer(apps, schema_editor):
    """Volta o reembolso para dentro do pagamento.

    `stock_taken` e `halted` não são desfeitos aqui: os dois desaparecem com a
    migration anterior, que é quem cria os campos.
    """
    Order = apps.get_model("orders", "Order")

    Order.objects.filter(refund_status="refunded").update(payment_status="refunded")
    Order.objects.filter(refund_status="partially_refunded").update(
        payment_status="partially_refunded"
    )
    # `not_charged` volta a "pendente": é o valor de onde ele veio na maioria
    # dos casos. O "recusado" de um pedido depois cancelado não é recuperável —
    # a informação não foi guardada em lugar nenhum, e inventá-la seria pior.
    Order.objects.filter(payment_status="not_charged").update(payment_status="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0011_cancellation_refund_states"),
    ]

    operations = [
        migrations.RunPython(sanear, desfazer),
    ]
