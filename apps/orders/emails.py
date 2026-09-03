"""E-mails do pedido.

Ao cliente, três — ele não precisa de uma sequência de sete mensagens:

1. **confirmação** — quando o pagamento é confirmado. Um e-mail só, completo:
   itens, personalização, endereços, prazo, valores, TVA;
2. **enviado** — transportadora, código e link de rastreio;
3. **dados para transferência** — a conta, o valor, a comunicação e o botão
   para mandar o comprovante. Este é o único que **ninguém dispara sozinho**:
   sai quando uma pessoa da equipe escolhe a conta no Admin e clica. Um e-mail
   com IBAN saindo automático é exatamente o que um golpe imita.

À equipe, três: **pedido novo** (a ordem de produção, com SKU, arquivos e
observações), **aguardando transferência** (alguém precisa mandar os dados) e
**comprovante recebido** (alguém precisa conferir no banco).

O idioma é o **do pedido** (``Order.language``, gravado na compra), não o da
requisição que dispara o envio: quem comprou em ``/fr/`` recebe em francês
mesmo que o disparo venha do admin em português, e continua recebendo em
francês três meses depois.

O envio nunca derruba a operação: se o provedor de e-mail estiver fora do ar, o
pagamento continua confirmado e a falha vai para o log.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.accounts.emails import absolute_url, site_url
from apps.core.languages import is_language_available

# Import tardio no módulo seria circular (models -> emails no envio de
# pedido); aqui não é: `models` não importa `emails` no topo.
from apps.orders.models import OrderEvent, RefundStatus

logger = logging.getLogger(__name__)


def order_language(order) -> str:
    """Idioma do e-mail: o do pedido; depois o do cliente; depois o padrão."""
    for candidate in (order.language, order.customer.user.preferred_language):
        candidate = (candidate or "").strip()
        if candidate and is_language_available(candidate):
            return candidate
    return settings.LANGUAGE_CODE


def order_context(order) -> dict:
    """Tudo que os modelos de e-mail precisam, carregado de uma vez."""
    return {
        # `LANGUAGE_CODE` normalmente vem do processador de contexto `i18n`,
        # que só roda numa requisição. Aqui não há uma — e sem isto o
        # `<html lang="">` do `base_email.html` sai vazio.
        "LANGUAGE_CODE": translation.get_language(),
        "order": order,
        "items": list(order.items.select_related("personalization_upload")),
        "shipping_address": order.shipping_address,
        "billing_address": order.billing_address,
        "customer": order.customer,
        "user": order.customer.user,
        "site_name": "JD PRINT",
        "site_url": site_url(),
        "order_url": absolute_url(order.get_absolute_url()),
    }


def _send(subject: str, template: str, context: dict, recipients: list[str]) -> bool:
    recipients = [address for address in recipients if address]
    if not recipients:
        return False

    text_body = render_to_string(f"emails/{template}.txt", context)
    html_body = render_to_string(f"emails/{template}.html", context)

    message = EmailMultiAlternatives(
        subject="".join(subject.splitlines()),  # cabeçalho não aceita quebra
        body=text_body,
        to=recipients,
    )
    message.attach_alternative(html_body, "text/html")

    try:
        message.send()
    except Exception:  # provedor fora do ar não pode desfazer um pagamento
        logger.exception("Falha ao enviar o e-mail %s do pedido %s", template, context["order"].number)
        return False
    return True


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


def send_order_confirmation_email(order, *, force: bool = False) -> bool:
    """Confirmação para o cliente. Enviada **uma vez** por pedido.

    ``force=True`` é o reenvio pedido pelo administrador (ver
    ``apps.orders.services.resend_email``): manda de novo mesmo com a marca
    preenchida. Nenhum caminho automático usa isso — reenviar sozinho seria
    mandar a mesma confirmação toda vez que o webhook fosse reentregue.
    """
    if order.confirmation_email_sent_at is not None and not force:
        return False

    with translation.override(order_language(order)):
        sent = _send(
            _("Pedido %(number)s confirmado — JD PRINT") % {"number": order.number},
            "order_confirmation",
            order_context(order),
            [order.customer.user.email],
        )

    if sent:
        order.confirmation_email_sent_at = timezone.now()
        order.save(update_fields=["confirmation_email_sent_at", "updated_at"])
    return sent


def send_order_shipped_email(order, *, force: bool = False) -> bool:
    """Aviso de envio, com rastreio quando existe."""
    if order.shipped_email_sent_at is not None and not force:
        return False

    with translation.override(order_language(order)):
        context = order_context(order)
        context["tracking_url"] = order.tracking_url
        sent = _send(
            _("Pedido %(number)s enviado — JD PRINT") % {"number": order.number},
            "order_shipped",
            context,
            [order.customer.user.email],
        )

    if sent:
        order.shipped_email_sent_at = timezone.now()
        order.save(update_fields=["shipped_email_sent_at", "updated_at"])
    return sent


# ---------------------------------------------------------------------------
# Administração
# ---------------------------------------------------------------------------


def admin_recipients() -> list[str]:
    """Quem recebe a ordem de producao.

    Sai da mesma regra de prioridade do resto do e-mail (Admin -> .env), para
    nao acontecer de o servidor vir do Admin e o destinatario do `.env`.
    """
    from apps.core.mailer import admin_recipients as configurados

    return configurados()


def send_admin_order_email(order, *, force: bool = False) -> bool:
    """Ordem de produção. Sempre em português — é a equipe que lê.

    Também é enviada **uma vez**: uma reentrega do webhook não pode fazer a
    oficina receber a mesma ordem duas vezes e imprimir duas.
    """
    if order.admin_email_sent_at is not None and not force:
        return False

    with translation.override(settings.LANGUAGE_CODE):
        context = order_context(order)
        context["admin_url"] = absolute_url(
            f"/admin/orders/order/{order.pk}/change/"
        )
        sent = _send(
            _("[JD PRINT] Novo pedido %(number)s — %(total)s %(currency)s")
            % {"number": order.number, "total": f"{order.total:.2f}", "currency": order.currency},
            "order_admin",
            context,
            admin_recipients(),
        )

    if sent:
        order.admin_email_sent_at = timezone.now()
        order.save(update_fields=["admin_email_sent_at", "updated_at"])
    return sent


def send_transfer_pending_email(order) -> bool:
    """Avisa a loja que um pedido está esperando transferência.

    Não é a ordem de produção (`send_admin_order_email`): esse continua saindo
    quando o pagamento for confirmado, para a oficina não imprimir antes de o
    dinheiro entrar. Aqui o assunto é só um — alguém precisa mandar os dados
    bancários ao cliente.

    Sempre em português, como todo e-mail interno, e para os mesmos
    destinatários dos pedidos.

    Sem marca de "já enviado" de propósito: quem chama é o `TransferProvider`,
    uma vez por tentativa de pagamento. Se o cliente voltar e pedir para pagar
    de novo, a loja é avisada de novo — que é o comportamento certo.
    """
    with translation.override(settings.LANGUAGE_CODE):
        context = order_context(order)
        context["admin_url"] = absolute_url(f"/admin/orders/order/{order.pk}/change/")
        context["accounts"] = _bank_accounts()
        return _send(
            _("[JD PRINT] Pedido %(number)s aguardando transferência — %(total)s %(currency)s")
            % {
                "number": order.number,
                "total": f"{order.total:.2f}",
                "currency": order.currency,
            },
            "order_transfer_pending",
            context,
            admin_recipients(),
        )


def _bank_accounts():
    """As contas que dá para usar hoje.

    Vão no e-mail interno para quem lê saber **se existe** conta cadastrada
    antes de abrir o Admin — não para copiar e colar. Quem manda os dados ao
    cliente é a ação do Admin, que escolhe uma delas.
    """
    from apps.orders.models import BankAccount

    return list(BankAccount.objects.usable())


def send_transfer_details_email(order, account=None, *, user=None) -> bool:
    """Os dados da conta, para o cliente pagar.

    Dois caminhos chegam aqui, e os dois trazem a conta de fora — esta função
    não tem opinião sobre qual usar, e não pode ter:

    * **o checkout**, logo depois de o pedido nascer. A conta é a que o pedido
      copiou (`order.bank_details`), que é a padrão do momento da compra;
    * **o Admin**, quando alguém precisa reenviar ou mandar por outra conta.
      Aí ``account`` vem preenchido e vence a cópia — é o que torna o reenvio
      por outra conta possível.

    Sai no idioma do pedido, como os outros e-mails do cliente. A comunicação
    da transferência é o número do pedido: é por ele que a equipe reconhece o
    dinheiro quando ele entra.

    Marca ``transfer_details_sent_at`` e registra no histórico **qual** conta
    foi usada — com o IBAN mascarado, porque ali o que se quer é identificar a
    conta, não repeti-la. Os dois acontecem aqui, e não em quem chama, porque
    são dois caminhos: um pedido cujos dados saíram sozinhos e outro cujos
    dados foram reenviados à mão têm que deixar o mesmo rastro.

    ``user`` só chega preenchido pela ação do Admin. Vazio no histórico se lê
    como "o sistema" — que é a verdade quando o envio foi automático.

    Devolve ``False`` sem enviar nada se a conta não tiver titular e IBAN: um
    e-mail de cobrança pela metade é pior que nenhum.
    """
    account = account or order.bank_details
    if account is None or not account.is_complete:
        return False

    with translation.override(order_language(order)):
        context = order_context(order)
        context["account"] = account
        context["communication"] = order.number
        context["proof_url"] = absolute_url(
            reverse("orders:payment_proof", kwargs={"number": order.number})
        )
        sent = _send(
            _("Dados para o pagamento do pedido %(number)s — JD PRINT")
            % {"number": order.number},
            "order_transfer_details",
            context,
            [order.customer.user.email],
        )

    if sent:
        order.transfer_details_sent_at = timezone.now()
        order.save(update_fields=["transfer_details_sent_at", "updated_at"])
        with translation.override(settings.LANGUAGE_CODE):
            # O histórico é lido pela equipe, em português — e com o IBAN
            # mascarado: ali o que se quer é identificar a conta, não repeti-la
            # numa linha que muita gente lê.
            order.log(
                OrderEvent.TRANSFER_DETAILS_SENT,
                _("Dados bancários enviados ao cliente (conta: %(conta)s)")
                % {"conta": str(account)},
                user=user,
            )
    return sent


def send_payment_proof_email(order, proof) -> bool:
    """Avisa a equipe de que um comprovante chegou. Em português.

    Avisar **não** é conferir. O e-mail diz isso com todas as letras: o pedido
    continua aguardando pagamento até alguém ver o dinheiro na conta e marcar
    no Admin. Um aviso que parecesse confirmação faria a oficina imprimir
    contra um PDF que qualquer um monta.
    """
    with translation.override(settings.LANGUAGE_CODE):
        context = order_context(order)
        context["proof"] = proof
        context["admin_url"] = absolute_url(f"/admin/orders/order/{order.pk}/change/")
        return _send(
            _("[JD PRINT] Comprovante recebido — pedido %(number)s")
            % {"number": order.number},
            "order_payment_proof",
            context,
            admin_recipients(),
        )


def send_cancellation_requested_email(order) -> bool:
    """Avisa a equipe de que há um cancelamento esperando decisão.

    **Este e-mail existe porque o histórico não é um canal.** O pedido já
    registrava a solicitação, e continuava registrando enquanto ninguém abrisse
    o Admin — que é o mesmo que não avisar. Cancelamento é a única coisa no
    fluxo em que o cliente pede e fica esperando uma pessoa; sem aviso, o
    silêncio da loja é indistinguível de um "não".

    Em português, como todo e-mail interno, e para os mesmos destinatários dos
    pedidos.

    Sem marca de "já enviado" de propósito: `services.request_cancellation` só
    devolve `True` na primeira vez (uma segunda solicitação encontra o pedido
    já em `REQUESTED` e não faz nada), então quem chama já não repete.
    """
    with translation.override(settings.LANGUAGE_CODE):
        context = order_context(order)
        context["admin_url"] = absolute_url(f"/admin/orders/order/{order.pk}/change/")
        context["reason"] = order.cancellation_reason
        return _send(
            _("[JD PRINT] AÇÃO NECESSÁRIA — cancelamento solicitado no pedido %(number)s")
            % {"number": order.number},
            "order_cancellation_requested",
            context,
            admin_recipients(),
        )


def send_cancellation_received_email(order) -> bool:
    """Confirma ao cliente que a solicitação de cancelamento chegou.

    **Não** promete nada: nem aprovação, nem reembolso, nem prazo. A decisão é
    de uma pessoa da equipe e ainda não foi tomada — prometer aqui e recusar
    depois é pior do que não ter escrito. Quem diz o que foi decidido é o
    `send_cancellation_approved_email`.

    Sai no idioma do pedido, como os outros e-mails do cliente. Sem marca de
    "já enviado", pelo mesmo motivo do e-mail da equipe: uma segunda
    solicitação encontra o pedido já em `REQUESTED` e nem chega aqui.
    """
    with translation.override(order_language(order)):
        context = order_context(order)
        return _send(
            _("Recebemos o seu pedido de cancelamento — %(number)s")
            % {"number": order.number},
            "order_cancellation_received",
            context,
            [order.customer.user.email],
        )


def send_cancellation_approved_email(order) -> bool:
    """Diz ao cliente que o cancelamento foi aprovado — e o que vem depois.

    O texto é o mesmo que aparece no acompanhamento do pedido
    (`CancellationSettings.approved_message`), e num lugar só: duas redações da
    mesma política é como um cliente descobre que a loja se contradiz.

    Sai no idioma do pedido, como os outros e-mails do cliente.
    """
    from apps.orders.models import CancellationSettings

    with translation.override(order_language(order)):
        context = order_context(order)
        # O pedido vai junto: é ele que diz se houve cobrança. Sem cobrança, a
        # frase não promete reembolso nenhum.
        context["message"] = CancellationSettings.approved_message(order)
        return _send(
            _("Cancelamento do pedido %(number)s — JD PRINT") % {"number": order.number},
            "order_cancellation_approved",
            context,
            [order.customer.user.email],
        )


def send_cancellation_refused_email(order) -> bool:
    """Diz ao cliente que a loja não vai cancelar — e por quê.

    O corpo é a resposta que a equipe escreveu (`cancellation_decision_note`).
    Sem ela, uma frase neutra: melhor curta e verdadeira do que um motivo
    inventado em nome de quem atende.

    Antes deste e-mail, o cliente que pedia para cancelar descobria a recusa se
    — e quando — abrisse a conta.
    """
    with translation.override(order_language(order)):
        context = order_context(order)
        context["resposta"] = (order.cancellation_decision_note or "").strip()
        return _send(
            _("Sobre o seu pedido de cancelamento — %(number)s") % {"number": order.number},
            "order_cancellation_refused",
            context,
            [order.customer.user.email],
        )


def send_refund_started_email(order) -> bool:
    """Avisa que o reembolso foi aberto. **Ainda não** que o dinheiro voltou.

    A diferença importa: quem lê "reembolsado" e não vê o valor na conta no dia
    seguinte liga para a loja. Aqui a mensagem é que o processo começou, com o
    prazo configurado — e quem confirma a chegada é o
    `send_refund_registered_email`.
    """
    from apps.orders.models import CancellationSettings

    with translation.override(order_language(order)):
        context = order_context(order)
        context["valor"] = f"{order.refund_due:.2f}"
        config = CancellationSettings.current()
        context["prazo"] = config.refund_window if config else ""
        return _send(
            _("Reembolso do pedido %(number)s — JD PRINT") % {"number": order.number},
            "order_refund_started",
            context,
            [order.customer.user.email],
        )


def send_refund_registered_email(order) -> bool:
    """Confirma um reembolso que já saiu — parcial ou o último.

    Um modelo só para os dois casos, porque a diferença é de uma frase e não de
    assunto: o cliente quer saber quanto voltou e se ainda falta alguma coisa.
    Dois modelos quase iguais é como as duas versões passam a divergir.
    """
    with translation.override(order_language(order)):
        context = order_context(order)
        context["reembolsado"] = f"{order.refunded_amount:.2f}"
        context["restante"] = f"{order.refund_due:.2f}"
        context["concluido"] = order.refund_status == RefundStatus.DONE
        return _send(
            _("Reembolso do pedido %(number)s — JD PRINT") % {"number": order.number},
            "order_refund_registered",
            context,
            [order.customer.user.email],
        )


def send_order_emails(order) -> dict:
    """Os dois e-mails da confirmação, na ordem em que importam.

    O do cliente primeiro: se o segundo falhar, quem comprou já foi avisado.

    Cada um decide sozinho se já foi enviado, pela marca que guarda no pedido.
    Chamar esta função de novo — porque o webhook foi reentregue — só manda o
    que ainda não saiu.

    Devolve o que foi enviado **nesta** chamada, para quem chamou saber se
    houve trabalho novo.
    """
    return {
        "confirmation": send_order_confirmation_email(order),
        "admin": send_admin_order_email(order),
    }
