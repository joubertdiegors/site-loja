"""Regras do pedido: validar, criar, confirmar, enviar, cancelar.

As views não decidem nada disto. Elas recebem POST, chamam uma função daqui e
mostram o resultado — o que permite testar a regra sem HTTP e reaproveitá-la
no admin e no webhook, que não têm requisição nenhuma.

A ordem do checkout, que é a parte delicada, está em ``create_order``:

1. revalidar produto, variante, preço e estoque **agora** (o carrinho pode
   estar aberto há três dias);
2. recalcular frete e imposto no servidor (nada vem do formulário);
3. copiar endereços;
4. criar pedido + itens, tudo dentro de uma transação;
5. **não** baixar estoque — isso só acontece quando o pagamento é confirmado
   (``apply_stock``, chamado pelo webhook).
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.cart.cart import max_quantity_for
from apps.catalog.choices import choice_groups
from apps.catalog.models import ProductStatus
from apps.orders import taxes
from apps.orders.models import (
    BankAccount,
    AddressKind,
    CancellationStatus,
    FulfillmentStatus,
    FulfillmentType,
    Order,
    OrderAddress,
    OrderEvent,
    OrderItem,
    OrderStatus,
    PaymentState,
    PaymentStatus,
    RefundStatus,
    StockDecision,
)
from apps.shipping import services as shipping_services

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Validação do carrinho
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CartProblem:
    line_key: str
    message: str


def validate_lines(lines) -> list[CartProblem]:
    """O que impede este carrinho de virar pedido, agora.

    Chamada duas vezes de propósito: ao abrir o checkout (para o cliente ver o
    problema antes de preencher tudo) e dentro da transação de criação (porque
    entre uma coisa e outra o estoque pode ter acabado).
    """
    problems: list[CartProblem] = []

    for line in lines:
        product = line.product
        name = product.display_name

        if product.status != ProductStatus.ACTIVE:
            problems.append(CartProblem(line.key, _("%(name)s não está mais disponível.") % {"name": name}))
            continue

        # Toda linha comercial tem variante: é dela que vêm preço, estoque,
        # peso e prazo. Uma linha sem variante é uma linha sem o que vender.
        if line.variant is None:
            problems.append(
                CartProblem(line.key, _("A opção escolhida de %(name)s saiu do catálogo.") % {"name": name})
            )
            continue

        if not line.variant.is_active:
            problems.append(
                CartProblem(line.key, _("A opção escolhida de %(name)s saiu do catálogo.") % {"name": name})
            )
            continue

        if line.unit_price <= ZERO:
            problems.append(
                CartProblem(line.key, _("%(name)s está sem preço definido.") % {"name": name})
            )
            continue

        available = max_quantity_for(product, line.variant)
        if available <= 0:
            problems.append(CartProblem(line.key, _("%(name)s está esgotado.") % {"name": name}))
        elif line.quantity > available:
            problems.append(
                CartProblem(
                    line.key,
                    _("Só temos %(count)s unidade(s) de %(name)s.")
                    % {"count": available, "name": name},
                )
            )

        if product.needs_personalization and not line.has_customization:
            problems.append(
                CartProblem(line.key, _("%(name)s precisa de personalização.") % {"name": name})
            )

        # «Cores à escolha do cliente»: o produto oferece a escolha e a linha
        # não a tem — o item entrou antes de o produto pedir cor, ou a cor
        # escolhida saiu da paleta. O carrinho já recalculou o adicional a
        # partir do banco; o que falta aqui é só a decisão do cliente.
        for grupo in choice_groups(product):
            if grupo.required and not any(c.key == grupo.key for c in line.choices):
                problems.append(
                    CartProblem(
                        line.key,
                        _("%(name)s precisa da escolha: %(group)s.")
                        % {"name": name, "group": grupo.label},
                    )
                )

    return problems


# ---------------------------------------------------------------------------
# Rascunho: o que o checkout mostra antes de existir pedido
# ---------------------------------------------------------------------------


@dataclass
class OrderDraft:
    """Os números do pedido antes de ele existir.

    Mesmo cálculo que ``create_order`` grava — vive aqui para o resumo do
    checkout não ter uma segunda versão da conta.
    """

    lines: list
    country: object = None
    shipping_option: object = None
    subtotal: Decimal = ZERO
    discount_total: Decimal = ZERO
    shipping_total: Decimal = ZERO
    tax: object = None
    total: Decimal = ZERO
    weight_grams: int = 0
    production_days: int = 0
    options: list = field(default_factory=list)

    @property
    def has_shipping(self) -> bool:
        return self.shipping_option is not None

    @property
    def is_free_shipping(self) -> bool:
        """A entrega saiu zero pelo frete grátis do país (e não por tarifa zero)."""
        return bool(self.shipping_option is not None and self.shipping_option.free_shipping)

    @property
    def min_days(self) -> int:
        return self.shipping_option.min_days if self.shipping_option else self.production_days

    @property
    def max_days(self) -> int:
        return self.shipping_option.max_days if self.shipping_option else self.production_days

    @property
    def days_display(self) -> str:
        return self.shipping_option.days_display if self.shipping_option else ""


def build_draft(lines, country=None, method=None) -> OrderDraft:
    """Monta os totais para um conjunto de linhas, país e método."""
    lines = list(lines)
    subtotal = taxes.money(sum((line.total for line in lines), ZERO))
    weight = shipping_services.cart_weight_grams(lines)
    production = shipping_services.production_days(lines)

    # O subtotal entra no cálculo por causa do frete grátis do país; ele já
    # inclui os adicionais das escolhas do cliente (`line.total`).
    options = shipping_services.quote(country, weight, production, subtotal=subtotal)
    option = None
    if method is not None:
        option = shipping_services.quote_for_method(
            country, weight, method, production, subtotal=subtotal
        )
    if option is None and options:
        # Nenhuma escolha ainda: fica a mais barata (a lista vem ordenada por
        # preço). O cliente troca em um clique, e ninguém encara um botão
        # "Pagar" desabilitado sem entender o que falta.
        option = options[0]

    shipping_total = taxes.money(option.price) if option else ZERO
    tax = taxes.breakdown(country=country, subtotal=subtotal, shipping=shipping_total)

    return OrderDraft(
        lines=lines,
        country=country,
        shipping_option=option,
        subtotal=subtotal,
        shipping_total=shipping_total,
        tax=tax,
        total=tax.taxable,
        weight_grams=weight,
        production_days=production,
        options=options,
    )


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------


class CheckoutError(Exception):
    """O pedido não pode ser criado. A mensagem é para o cliente."""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.message = message
        self.problems = problems or []


def _fulfillment_type(line) -> str:
    """Como esta linha é atendida — decidido pela variante comprada."""
    if line.has_customization:
        return FulfillmentType.PERSONALIZED
    if line.variant is not None and line.variant.made_to_order:
        return FulfillmentType.MADE_TO_ORDER
    return FulfillmentType.STOCK


def _fit_snapshot(value: str, field: str) -> str:
    """O texto cortado ao ``max_length`` do campo de ``OrderItem``.

    O mesmo cuidado de ``colors_snapshot[:255]``, sem repetir o número: o
    limite vem do campo. O nome de uma cor composta («Branco Pérola + Azul
    Marinho + …») pode passar dos 60 caracteres de ``color_name``, e no
    PostgreSQL um texto maior que a coluna derruba o checkout.
    """
    limite = OrderItem._meta.get_field(field).max_length
    return value[:limite] if limite else value


def _item_from_line(order: Order, line) -> OrderItem:
    """Copia a linha do carrinho para dentro do pedido.

    Tudo que a tela do pedido mostra sai daqui — nunca do produto de hoje.
    """
    variant = line.variant
    product = line.product
    customization = line.customization or {}

    return OrderItem(
        order=order,
        product=product,
        variant=variant,
        product_name=product.display_name,
        sku=(variant.sku if variant is not None else product.sku) or "",
        variant_label=variant.label if variant is not None else "",
        # No idioma do cliente, como o nome do produto logo acima: o pedido
        # guarda o que ele leu, não o nome interno do Admin.
        color_name=_fit_snapshot(
            variant.color.display_name if variant is not None and variant.color_id else "",
            "color_name",
        ),
        size_name=(variant.size if variant is not None else ""),
        material_name=(
            variant.material.display_name
            if variant is not None and variant.material_id
            else ""
        ),
        # A descrição do produto (cores e composição), congelada aqui: mudar o
        # cadastro depois não muda o que o cliente comprou.
        colors_snapshot=product.colors_text[:255],
        materials_snapshot=product.materials_text[:255],
        # Etapa 3B: as escolhas da variante nas opções adicionais, congeladas
        # com os nomes que o cliente leu. Renomear a opção depois não muda isto.
        options_snapshot=variant.options_text if variant is not None else "",
        # «Cores à escolha do cliente»: a escolha feita acima da variante e o
        # adicional que valia agora — mudar o adicional da cor amanhã não
        # mexe neste pedido. `unit_price` já traz o adicional somado.
        choices_snapshot=line.choices_text,
        price_adjustment=taxes.money(line.price_adjustment),
        quantity=line.quantity,
        unit_price=taxes.money(line.unit_price),
        total=taxes.money(line.total),
        unit_weight_grams=shipping_services.line_weight_grams(product, variant),
        fulfillment_type=_fulfillment_type(line),
        production_days=shipping_services.variant_production_days(variant),
        personalization_type=customization.get("type", "") or "",
        personalization_text=customization.get("text", "") or "",
        personalization_notes=customization.get("notes", "") or "",
        personalization_upload=line.upload,
    )


@transaction.atomic
def create_order(
    *,
    customer,
    lines,
    shipping_address,
    billing_address,
    shipping_method,
    payment_method: str = "",
    is_gift: bool = False,
    customer_note: str = "",
    language: str = "",
    checkout_token: str = "",
) -> Order:
    """Cria o pedido a partir do carrinho. Levanta ``CheckoutError`` se não der.

    Tudo em uma transação: ou nasce o pedido inteiro (com número, itens e
    endereços), ou não nasce nada — inclusive o número, que volta a ficar livre.

    ``payment_method`` é validado aqui, e não só no formulário: este é o último
    ponto por onde todo pedido passa, e um `Order` gravado com um método que a
    loja não aceita seria um pedido que ninguém sabe cobrar.
    """
    lines = list(lines)
    if not lines:
        raise CheckoutError(_("Seu carrinho está vazio."))

    problems = validate_lines(lines)
    if problems:
        raise CheckoutError(_("Alguns itens precisam da sua atenção."), problems)

    if shipping_address is None or billing_address is None:
        raise CheckoutError(_("Escolha o endereço de entrega e o de faturamento."))

    country = shipping_address.country
    if not country.is_active:
        raise CheckoutError(_("Ainda não entregamos neste país."))

    weight = shipping_services.cart_weight_grams(lines)
    production = shipping_services.production_days(lines)
    # Antes do frete: o subtotal decide o frete grátis do país de destino.
    subtotal = taxes.money(sum((line.total for line in lines), ZERO))

    # O método vem de um <input>. O preço dele é recalculado aqui, no servidor
    # — inclusive o frete grátis, que nunca vem do navegador.
    option = shipping_services.quote_for_method(
        country, weight, shipping_method, production, subtotal=subtotal
    )
    if option is None:
        raise CheckoutError(_("Escolha uma forma de entrega válida para este endereço."))

    metodo, conta = _resolve_payment(payment_method)

    shipping_total = taxes.money(option.price)
    tax = taxes.breakdown(country=country, subtotal=subtotal, shipping=shipping_total)

    order = Order.objects.create(
        customer=customer,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.NOT_STARTED,
        currency=(lines[0].product.currency or "EUR").upper(),
        subtotal=subtotal,
        discount_total=ZERO,
        shipping_total=shipping_total,
        tax_total=tax.amount,
        total=tax.taxable,
        tax_country=tax.country_code,
        tax_rate=tax.rate,
        prices_include_tax=True,
        shipping_method=option.method,
        shipping_method_label=option.label,
        shipping_min_days=option.min_days,
        shipping_max_days=option.max_days,
        production_days=production,
        total_weight_grams=weight,
        is_gift=bool(is_gift),
        customer_note=customer_note or "",
        language=language or "",
        payment_method=metodo.code,
        checkout_token=checkout_token or "",
        # A conta vai **copiada**, não referenciada: trocar a conta padrão
        # amanhã não pode reescrever o IBAN que este cliente recebeu hoje.
        bank_account=conta,
        bank_beneficiary=conta.beneficiary if conta else "",
        bank_iban=conta.iban if conta else "",
        bank_bic=conta.bic if conta else "",
        bank_instructions=conta.instructions if conta else "",
    )

    OrderAddress.from_customer_address(order, shipping_address, AddressKind.SHIPPING)
    OrderAddress.from_customer_address(order, billing_address, AddressKind.BILLING)

    OrderItem.objects.bulk_create([_item_from_line(order, line) for line in lines])

    order.log(OrderEvent.CREATED, _("Pedido criado."))
    return order


def _resolve_payment(payment_method: str):
    """(forma de pagamento, conta bancária) — ou ``CheckoutError``.

    Duas recusas, e as duas com mensagem para o cliente:

    * **método inválido ou indisponível.** O código vem de um ``<input>``; o que
      decide é ``available_checkout_methods()``, no servidor. Um método
      "em breve" chega aqui exatamente como um código inventado, e sai igual;
    * **transferência sem conta padrão ativa.** Sem ela não há para onde mandar
      o dinheiro, e escolher outra conta sozinho seria pior: o cliente
      transferiria para uma conta que ninguém decidiu usar.
    """
    from apps.orders.payments import TRANSFER, get_checkout_method, method_for_provider

    if payment_method:
        # Veio escolha: ela é conferida contra o que a loja aceita agora.
        metodo = get_checkout_method(payment_method)
        if metodo is None:
            raise CheckoutError(_("Escolha uma forma de pagamento disponível."))
    else:
        # Não veio: quem chamou não é uma tela. Vale o provedor configurado.
        metodo = method_for_provider()
        if metodo is None:
            raise CheckoutError(_("Nenhuma forma de pagamento está configurada."))

    if metodo.code != TRANSFER:
        return metodo, None

    conta = BankAccount.objects.default_for_orders()
    if conta is None:
        raise CheckoutError(
            _(
                "O pagamento por transferência está indisponível neste momento. "
                "Entre em contato com a gente e concluímos o seu pedido."
            )
        )
    return metodo, conta


def validate_order_items(order: Order) -> list[str]:
    """O que impede este pedido de ser pago **agora**.

    O pedido guarda tudo copiado — nome, preço, peso — de propósito: ele não
    muda porque o catálogo mudou. Mas "posso pagar?" é uma pergunta do
    presente, e a resposta vem do catálogo de agora: entre criar o pedido e
    voltar para pagá-lo podem ter passado dias, e a peça pode ter esgotado ou
    saído de linha.

    Devolve frases prontas para o cliente. Lista vazia quer dizer "pode pagar".

    Não usa `validate_lines`: aquela fala de linhas de carrinho, com `key`,
    `unit_price` e `product`; aqui o que existe são itens de pedido, e o preço
    já está travado. O que se pergunta é só disponibilidade.
    """
    problemas: list[str] = []

    for item in order.items.select_related("product", "variant"):
        nome = item.product_name

        if item.product is None or item.product.status != ProductStatus.ACTIVE:
            problemas.append(_("%(name)s não está mais disponível.") % {"name": nome})
            continue

        if item.variant is None or not item.variant.is_active:
            problemas.append(
                _("A opção escolhida de %(name)s saiu do catálogo.") % {"name": nome}
            )
            continue

        # Sob encomenda não depende de prateleira: a peça é impressa para este
        # pedido, e foi assim que ele foi fechado.
        if item.fulfillment_type == FulfillmentType.MADE_TO_ORDER:
            continue

        disponivel = max_quantity_for(item.product, item.variant)
        if disponivel <= 0:
            problemas.append(_("%(name)s está esgotado.") % {"name": nome})
        elif item.quantity > disponivel:
            problemas.append(
                _("Só temos %(count)s unidade(s) de %(name)s.")
                % {"count": disponivel, "name": nome}
            )

    return problemas


def refresh_transfer_account(order: Order):
    """Aponta o pedido para a conta padrão **de hoje**, para uma nova tentativa.

    A cópia no pedido existe para que trocar a conta padrão não reescreva
    pedidos antigos — isso continua valendo para tudo o que é passivo. Uma nova
    tentativa de pagamento não é passiva: o cliente vai transferir agora, e
    mandá-lo para uma conta que a loja parou de usar seria pior do que não ter
    cópia nenhuma.

    O rastro da tentativa anterior não se perde: cada envio de dados escreve no
    histórico qual conta foi usada, com o IBAN mascarado.

    Devolve a conta, ou ``None`` quando não há conta padrão ativa.
    """
    conta = BankAccount.objects.default_for_orders()
    if conta is None:
        return None

    order.bank_account = conta
    order.bank_beneficiary = conta.beneficiary
    order.bank_iban = conta.iban
    order.bank_bic = conta.bic
    order.bank_instructions = conta.instructions
    order.save(
        update_fields=[
            "bank_account", "bank_beneficiary", "bank_iban",
            "bank_bic", "bank_instructions", "updated_at",
        ]
    )
    return conta


# ---------------------------------------------------------------------------
# Estoque
# ---------------------------------------------------------------------------


def apply_stock(order: Order) -> list[str]:
    """Baixa o estoque do pedido. Roda **uma vez**, na confirmação do pagamento.

    Não reservamos nada quando o produto entra no carrinho — carrinho não é
    compromisso. A baixa acontece aqui, com ``select_for_update`` para dois
    pagamentos simultâneos não lerem o mesmo saldo.

    Se faltar estoque neste ponto, o dinheiro **já foi cobrado**: recusar o
    pedido seria pior para o cliente do que avisar a produção. Então o pedido
    segue, o saldo nunca fica negativo e a falta entra no histórico e numa nota
    interna para alguém decidir (reembolso é ação administrativa explícita).

    Devolve a lista de faltas encontradas — vazia quando correu tudo bem.
    """
    from apps.catalog.models import ProductVariant

    if order.stock_applied_at is not None:
        return []

    shortages: list[str] = []

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.stock_applied_at is not None:  # outra entrega do webhook chegou primeiro
            return []

        for item in locked.items.select_related("product", "variant"):
            if item.fulfillment_type == FulfillmentType.MADE_TO_ORDER:
                continue  # sob encomenda não consome saldo: é produzido

            # O estoque é da variante. Item antigo sem variante (nenhum
            # existe, mas o campo é nulável) simplesmente não move saldo.
            if item.variant_id:
                target = ProductVariant.objects.select_for_update().filter(pk=item.variant_id).first()
            else:
                target = None

            if target is None:
                continue

            if getattr(target, "allow_backorder", False):
                continue

            available = target.stock_quantity
            taken = min(available, item.quantity)
            if taken < item.quantity:
                shortages.append(
                    _("%(name)s: faltaram %(count)s unidade(s).")
                    % {"name": item.description, "count": item.quantity - taken}
                )
            if taken:
                target.stock_quantity = available - taken
                target.save(update_fields=["stock_quantity"])

            # Quanto saiu **desta linha**. Sem este número, devolver o saldo
            # depois seria um chute: a quantidade pedida não é a quantidade
            # tirada quando houve falta, e itens sob encomenda não tiram nada.
            if item.stock_taken != taken:
                item.stock_taken = taken
                item.save(update_fields=["stock_taken"])

        locked.stock_applied_at = timezone.now()
        locked.save(update_fields=["stock_applied_at", "updated_at"])
        order.stock_applied_at = locked.stock_applied_at

    if shortages:
        order.log(
            OrderEvent.STOCK_SHORTAGE,
            " ".join(shortages)[:300],
            visible=False,
        )
        from apps.orders.models import OrderNote

        OrderNote.objects.create(
            order=order,
            body=_("Estoque insuficiente na confirmação do pagamento:\n%(list)s")
            % {"list": "\n".join(shortages)},
        )

    return shortages


# ---------------------------------------------------------------------------
# Confirmação do pagamento
# ---------------------------------------------------------------------------


def confirm_payment(order: Order, payment=None, *, method_label: str = "", user=None) -> bool:
    """Confirma o pagamento e leva o pedido até o fim. Idempotente **por etapa**.

    São três etapas em sequência, e cada uma se protege sozinha:

    ::

        pagamento          -> payment_status / paid_at
            estoque        -> stock_applied_at
                e-mails    -> confirmation_email_sent_at, admin_email_sent_at

    Chamar esta função de novo é seguro: cada etapa já feita é pulada. E é
    justamente por isso que ela **não** volta cedo quando o pedido já está
    pago.

    O curto-circuito antigo (``if already_paid: return False``) protegia o
    pedido, não as etapas. Bastava a gravação do pagamento passar e a baixa de
    estoque estourar logo depois: a reentrega do webhook via o pedido pago,
    voltava na hora, e estoque e e-mails nunca aconteciam. O pedido ficava
    pago, sem baixa e sem ninguém avisado — e nada tentaria de novo.

    Devolve ``True`` se **alguma** etapa foi executada nesta chamada.

    É chamada pelo webhook, nunca pela página de retorno do cliente.
    """
    from apps.orders.emails import send_order_emails

    novidade = _mark_paid(order, payment, method_label=method_label, user=user)
    order.refresh_from_db()

    if order.cancelled_at is not None:
        # O pedido já estava cancelado quando o dinheiro chegou. Acontece com
        # transferência: o cliente cancela e a ordem bancária dele cai dois
        # dias depois.
        #
        # O pagamento é registrado — ele aconteceu, e negá-lo não faz o dinheiro
        # voltar sozinho. O que **não** acontece é o resto: estoque não baixa
        # para um pedido cancelado, e ninguém recebe "compra confirmada" de uma
        # compra que não existe mais. Em vez disso, abre-se o reembolso.
        return _abrir_reembolso_de_pedido_cancelado(order, user=user) or novidade

    # As duas etapas seguintes rodam sempre. Se já foram feitas, elas mesmas
    # percebem; se ficaram pendentes de uma tentativa anterior, é aqui que
    # finalmente acontecem.
    estoque_pendente = order.stock_applied_at is None
    apply_stock(order)
    order.refresh_from_db()

    enviados = send_order_emails(order)

    return bool(novidade or estoque_pendente or any(enviados.values()))


def _abrir_reembolso_de_pedido_cancelado(order: Order, *, user=None) -> bool:
    """O dinheiro chegou tarde: abre o reembolso e deixa o rastro para a equipe.

    Idempotente pela própria transição: com o reembolso já aberto, uma segunda
    entrega do webhook não abre outro nem manda outro e-mail.
    """
    from apps.orders.models import OrderNote

    if order.refund_status != RefundStatus.NONE:
        return False

    order.refund_status = RefundStatus.PENDING
    order.save(update_fields=["refund_status", "updated_at"])
    order.log(
        OrderEvent.REFUND_PENDING,
        _("Pagamento recebido depois do cancelamento. Reembolso de %(valor)s aberto.")
        % {"valor": f"{order.refund_due:.2f}"},
        user=user,
    )
    OrderNote.objects.create(
        order=order,
        body=_(
            "O pagamento deste pedido entrou **depois** do cancelamento. "
            "O valor precisa ser devolvido ao cliente e registrado pela ação "
            "“Registrar reembolso já feito”."
        ),
    )
    _avisar(
        "send_refund_started_email", order,
        "Falha ao avisar o cliente do reembolso do pedido cancelado %s",
    )
    return True


def _mark_paid(order: Order, payment=None, *, method_label: str = "", user=None) -> bool:
    """Etapa 1: o pagamento. Devolve ``True`` se foi agora que ele entrou.

    O ``select_for_update`` é o que impede duas entregas simultâneas do webhook
    de marcarem o pedido como pago duas vezes e escreverem dois eventos no
    histórico.
    """
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.payment_status == PaymentStatus.PAID:
            return False

        now = timezone.now()
        locked.payment_status = PaymentStatus.PAID
        locked.paid_at = now
        # Um pedido cancelado que recebe o dinheiro depois **não** é confirmado:
        # o pagamento é um fato e fica registrado, mas a compra não voltou a
        # existir. Sem esta condição, `confirmed_at` era preenchido e o evento
        # CONFIRMED escrevia "A sua encomenda foi confirmada" na linha do tempo
        # de um pedido que o cliente acabara de cancelar.
        confirmando = locked.cancelled_at is None
        if confirmando and locked.confirmed_at is None:
            locked.confirmed_at = now
        locked.status = locked.derived_status
        # A produção **não** anda aqui. Pagar não é começar a imprimir: quem
        # começa é a oficina, e é ela que marca. Enquanto o pedido está pago e
        # parado, cancelar é uma decisão automática — nada foi consumido. Com o
        # avanço automático de antes esse estado não existia, e todo
        # cancelamento de pedido pago virava análise manual.
        locked.save(
            update_fields=[
                "payment_status", "paid_at", "status", "confirmed_at", "updated_at",
            ]
        )
        paid_at = locked.paid_at

    if payment is not None:
        payment.status = PaymentState.SUCCEEDED
        payment.paid_at = paid_at
        if method_label:
            payment.method_label = method_label
        payment.save(update_fields=["status", "paid_at", "method_label", "updated_at"])

    order.refresh_from_db()
    # `user` só chega preenchido na confirmação manual (o Admin). Pelo webhook
    # ele é `None`, que no histórico se lê como "o sistema" — que é a verdade.
    order.log(OrderEvent.PAID, _("Pagamento confirmado."), user=user)
    if confirmando:
        order.log(OrderEvent.CONFIRMED, _("Pedido confirmado."), user=user)
    return True


def recompute_status(order: Order, *, save: bool = True) -> str:
    """Recalcula `Order.status` a partir dos fatos, e devolve o valor.

    O `status` deixou de ser uma decisão de quem opera: ele é consequência de
    três coisas que **são** decisões — o dinheiro entrou, a peça saiu, o pedido
    foi cancelado. Enquanto era um `select` no formulário, era possível gravar
    "reembolsado" num pedido que ninguém cancelou e que continuava em produção.

    Chamado ao fim de todo serviço que mexe em pagamento, produção ou
    cancelamento. Escreve só quando muda, para não sujar `updated_at` à toa.
    """
    novo = order.derived_status
    if order.status == novo:
        return novo

    order.status = novo
    if save:
        order.save(update_fields=["status", "updated_at"])
    return novo


def return_stock(order: Order, *, user=None, motivo: str = "") -> int:
    """Devolve ao saldo exatamente o que a confirmação do pagamento tirou.

    **Uma vez só.** A marca é `stock_returned_at`, gravada dentro da mesma
    transação e conferida com o pedido travado: dois cliques simultâneos no
    botão de aprovar não devolvem o dobro.

    Devolve quantas unidades voltaram — zero quando não havia o que devolver
    (pedido não pago, item sob encomenda, ou saldo já devolvido).
    """
    from apps.catalog.models import ProductVariant

    if order.stock_applied_at is None or order.stock_returned_at is not None:
        return 0

    devolvidas = 0
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.stock_applied_at is None or locked.stock_returned_at is not None:
            return 0

        for item in locked.items.select_related("variant"):
            if not item.stock_taken or not item.variant_id:
                continue
            target = (
                ProductVariant.objects.select_for_update()
                .filter(pk=item.variant_id)
                .first()
            )
            if target is None:
                continue
            target.stock_quantity = target.stock_quantity + item.stock_taken
            target.save(update_fields=["stock_quantity"])
            devolvidas += item.stock_taken

        agora = timezone.now()
        locked.stock_returned_at = agora
        locked.stock_return_decision = StockDecision.RETURNED
        locked.save(
            update_fields=["stock_returned_at", "stock_return_decision", "updated_at"]
        )
        order.stock_returned_at = agora
        order.stock_return_decision = StockDecision.RETURNED

    detalhe = _("%(count)s unidade(s) devolvida(s) ao estoque.") % {"count": devolvidas}
    if motivo:
        detalhe = f"{detalhe} {motivo}"
    order.log(OrderEvent.STOCK_RETURNED, detalhe[:300], user=user, visible=False)
    return devolvidas


def register_payment_failure(order: Order, payment=None, reason: str = "") -> None:
    """Pagamento recusado. O pedido continua ``pending``: dá para tentar de novo."""
    if payment is not None:
        payment.status = PaymentState.FAILED
        payment.failure_message = (reason or "")[:300]
        payment.save(update_fields=["status", "failure_message", "updated_at"])

    if order.payment_status == PaymentStatus.PAID:
        return
    if order.cancelled_at is not None:
        # O pedido já foi cancelado e o pagamento marcado como "não cobrado".
        # A sessão expirada do provedor chegando depois não pode reescrever isso
        # — e muito menos dizer ao cliente que o pagamento dele foi recusado.
        return

    order.payment_status = PaymentStatus.FAILED
    order.save(update_fields=["payment_status", "updated_at"])
    recompute_status(order)
    order.log(OrderEvent.PAYMENT_FAILED, (reason or "")[:300])


# ---------------------------------------------------------------------------
# Expedição
# ---------------------------------------------------------------------------


def mark_shipped(order: Order, tracking_number: str = "", user=None) -> bool:
    """Marca como enviado e dispara o e-mail de envio (uma vez só)."""
    from apps.orders.emails import send_order_shipped_email

    if order.fulfillment_status == FulfillmentStatus.SHIPPED and order.shipped_email_sent_at:
        return False

    if order.fulfillment_status == FulfillmentStatus.HALTED:
        return False

    campos = ["fulfillment_status", "shipped_at", "tracking_number", "updated_at"]
    if order.fulfillment_status == FulfillmentStatus.DELIVERED:
        # Marcar "enviado" num pedido já entregue é corrigir um engano. A data
        # da entrega tem de sair junto, como sai em `change_fulfillment_status`
        # — senão o prazo legal continua correndo a partir de uma entrega que a
        # loja acabou de dizer que não aconteceu.
        order.delivered_at = None
        campos.append("delivered_at")

    order.fulfillment_status = FulfillmentStatus.SHIPPED
    order.shipped_at = order.shipped_at or timezone.now()
    if tracking_number:
        order.tracking_number = tracking_number.strip()
    order.save(update_fields=campos)
    recompute_status(order)

    # Recarrega antes de montar o e-mail: o objeto em memória pode carregar
    # relações lidas há muito tempo (a transportadora, por exemplo, que é de
    # onde sai o link de rastreio).
    order.refresh_from_db()

    order.log(OrderEvent.SHIPPED, order.tracking_number, user=user)
    send_order_shipped_email(order)
    return True


# ---------------------------------------------------------------------------
# Reenvio de e-mail (AUD-02)
# ---------------------------------------------------------------------------


#: Os três e-mails do pedido e como reenviar cada um.
#:
#: A chave é o que o Admin manda; o valor é (rótulo, função, campo da marca).
#: Ter isto num mapa e não em três `if` é o que faz o Admin, o histórico e o
#: painel de situação falarem dos mesmos três e-mails.
RESENDABLE_EMAILS = {
    "confirmation": ("confirmação ao cliente", "confirmation_email_sent_at"),
    "admin": ("ordem de produção", "admin_email_sent_at"),
    "shipped": ("aviso de envio", "shipped_email_sent_at"),
}


def resend_email(order: Order, kind: str, user=None) -> bool:
    """Reenvia um dos e-mails do pedido, a pedido do administrador.

    Existe porque o envio pode falhar sem derrubar nada: o provedor está fora
    do ar, o pagamento é confirmado assim mesmo (é o certo), e o pedido fica
    pago com ``confirmation_email_sent_at`` vazio. Sem esta ação, a única saída
    seria mexer no banco à mão.

    **Só envia quando alguém pede.** Nenhum caminho automático chama isto — um
    reenvio automático mandaria a mesma confirmação a cada reentrega do
    webhook. Por isso ``force=True``: quando o administrador clica, ele quer
    que vá de novo mesmo com a marca preenchida.

    **Não toca** em pagamento, estoque, situação, valores ou snapshot. Reenviar
    um e-mail é reenviar um e-mail.
    """
    from apps.orders import emails as order_emails

    if kind not in RESENDABLE_EMAILS:
        raise ValueError(f"E-mail desconhecido: {kind}")

    senders = {
        "confirmation": order_emails.send_order_confirmation_email,
        "admin": order_emails.send_admin_order_email,
        "shipped": order_emails.send_order_shipped_email,
    }
    label, _campo = RESENDABLE_EMAILS[kind]

    order.refresh_from_db()
    sent = senders[kind](order, force=True)

    order.log(
        OrderEvent.EMAIL_RESENT,
        (
            _("Reenviado: %(label)s.") % {"label": label}
            if sent
            else _("Falha ao reenviar: %(label)s.") % {"label": label}
        ),
        user=user,
        visible=False,
    )
    return sent


# ---------------------------------------------------------------------------
# Situação da produção (AUD-04)
# ---------------------------------------------------------------------------


#: A produção anda nesta ordem. O índice é o que permite dizer "voltou".
FULFILLMENT_ORDER = (
    FulfillmentStatus.NOT_STARTED,
    FulfillmentStatus.IN_PRODUCTION,
    FulfillmentStatus.READY,
    FulfillmentStatus.SHIPPED,
    FulfillmentStatus.DELIVERED,
)


class StatusChangeRefused(Exception):
    """Regressão grande demais para ser um acerto de digitação."""


def fulfillment_step(status: str) -> int:
    try:
        return FULFILLMENT_ORDER.index(status)
    except ValueError:
        return 0


def change_fulfillment_status(order: Order, new_status: str, user=None) -> bool:
    """Muda a situação da produção, com registro de quem, de onde e para onde.

    Duas coisas que faltavam (AUD-04):

    1. **registro.** Mexer no campo pela tela do Admin gravava e pronto: o
       histórico não sabia que a produção tinha andado, nem quem tinha mexido.
       Agora toda mudança vira um evento com autor, estado anterior e novo;

    2. **freio para o absurdo.** Voltar um passo é acerto legítimo — marquei
       "enviado" cedo demais. Voltar de "Entregue" para "Não iniciado" não é
       correção nenhuma: é a lista de opções clicada errado. Regressões de mais
       de um passo são recusadas.

    Devolve ``True`` se algo mudou. Levanta ``StatusChangeRefused`` quando a
    regressão é grande demais — quem chama decide como avisar.
    """
    anterior = order.fulfillment_status
    if anterior == new_status:
        return False

    # `HALTED` não se digita. Ele é consequência de um cancelamento aprovado, e
    # é terminal: sair dele à mão devolveria à fila da oficina uma peça que a
    # loja já disse ao cliente que não seria feita.
    if new_status == FulfillmentStatus.HALTED:
        raise StatusChangeRefused(
            _(
                "“Interrompido” não se escolhe aqui: ele vem da aprovação de um "
                "cancelamento. Use a ação “Aprovar cancelamento”."
            )
        )
    if anterior == FulfillmentStatus.HALTED:
        raise StatusChangeRefused(
            _(
                "Este pedido foi cancelado e a produção está interrompida. "
                "Reabrir exige um pedido novo."
            )
        )

    de = fulfillment_step(anterior)
    para = fulfillment_step(new_status)
    if de - para > 1:
        raise StatusChangeRefused(
            _(
                "Voltar de “%(de)s” para “%(para)s” é um salto grande demais "
                "para ser uma correção. Volte um passo de cada vez."
            )
            % {
                "de": FulfillmentStatus(anterior).label,
                "para": FulfillmentStatus(new_status).label,
            }
        )

    order.fulfillment_status = new_status
    campos = ["fulfillment_status", "updated_at"]
    if new_status == FulfillmentStatus.DELIVERED and order.delivered_at is None:
        # É desta data que corre o prazo legal de arrependimento. Sem ela o
        # prazo nunca começaria a contar, e o direito ficaria aberto para
        # sempre — o que é tão errado quanto fechá-lo cedo demais.
        order.delivered_at = timezone.now()
        campos.append("delivered_at")
    elif anterior == FulfillmentStatus.DELIVERED:
        # A entrega foi marcada por engano e alguém está corrigindo. Deixar a
        # data faria o prazo correr a partir de algo que não aconteceu.
        order.delivered_at = None
        campos.append("delivered_at")
    order.save(update_fields=campos)
    recompute_status(order)
    order.log(
        OrderEvent.STATUS_CHANGED,
        _("Produção: %(de)s → %(para)s")
        % {
            "de": FulfillmentStatus(anterior).label,
            "para": FulfillmentStatus(new_status).label,
        },
        user=user,
        visible=False,
    )
    return True


# ---------------------------------------------------------------------------
# Cancelamento
# ---------------------------------------------------------------------------


def _avisar(funcao: str, order: Order, erro: str) -> bool:
    """Dispara um e-mail sem deixar que a falha dele desfaça o que já aconteceu.

    A regra do projeto inteiro: provedor de e-mail fora do ar não apaga uma
    venda, não desfaz um cancelamento e não some com uma decisão. O que falha é
    o aviso, e aviso tem conserto — a informação está no pedido.
    """
    from apps.orders import emails

    try:
        return bool(getattr(emails, funcao)(order))
    except Exception:  # noqa: BLE001
        logger.exception(erro, order.number)
        return False


class StockDecisionRequired(Exception):
    """A aprovação precisa de uma decisão de estoque que ninguém tomou.

    Levantada, e não contornada com um padrão, porque não existe padrão certo:
    uma peça pela metade na impressora pode virar sucata ou voltar ao saldo, e
    só quem está olhando para ela sabe qual das duas.
    """


class CancellationRefused(Exception):
    """O pedido não pode mais ser cancelado — o prazo legal fechou."""


@dataclass(frozen=True)
class CancellationPlan:
    """O que este cancelamento implica, decidido num lugar só.

    Existe para que a mesma pergunta — "isto é automático? o estoque volta?" —
    tenha a mesma resposta na página do cliente, na ação do Admin e no teste.
    Antes a regra estava espalhada em três `if` que discordavam entre si.
    """

    auto: bool
    reason: str
    stock: str
    needs_stock_decision: bool
    refund: bool
    personalized: bool

    @property
    def manual(self) -> bool:
        return not self.auto


def cancellation_plan(order: Order) -> CancellationPlan:
    """A regra de cancelamento da loja, em um lugar só.

    ::

        não pago                       -> automático, nada a devolver, sem reembolso
        pago, produção não começou     -> automático, saldo volta, reembolso aberto
        pago, em produção              -> análise humana, decisão de estoque obrigatória
        pago, pronto                   -> análise humana, saldo volta no reembolso
        enviado ou entregue            -> análise humana, decisão de estoque obrigatória

    **Personalizado é sempre análise humana.** O artigo 16(c) da diretiva
    2011/83/UE tira o direito de arrependimento dos bens feitos sob
    especificação do cliente — mas a exceção tem condições (informação prévia,
    personalização de verdade) que nenhum `if` confere. Então o pedido não é
    recusado automaticamente: ele vai para alguém decidir, com o sinalizador
    aceso.
    """
    personalizado = order.has_personalized_items
    pago = order.payment_status == PaymentStatus.PAID
    tem_saldo_fora = order.stock_applied_at is not None and order.stock_returned_at is None
    producao = order.fulfillment_status

    if producao in {FulfillmentStatus.SHIPPED, FulfillmentStatus.DELIVERED}:
        # A peça está com o cliente. Isto é uma devolução, e nenhuma parte dela
        # é automática — nem quando não houve cobrança: as peças saíram, e o
        # que acontece com elas depende do que voltar.
        return CancellationPlan(
            auto=False,
            reason=_("A encomenda já saiu: é uma devolução, e o estoque depende do que voltar."),
            stock=StockDecision.NONE,
            needs_stock_decision=tem_saldo_fora,
            refund=pago,
            personalized=personalizado,
        )

    if not pago:
        # Sem cobrança não há reembolso, e sem produção não há o que devolver:
        # o saldo só sai na confirmação do pagamento. O `tem_saldo_fora` cobre
        # o caso raro de um pedido que foi pago, teve o pagamento revertido e
        # ficou com o saldo baixado.
        return CancellationPlan(
            auto=not personalizado,
            reason=_("Nada foi cobrado por esta encomenda."),
            stock=StockDecision.RETURNED if tem_saldo_fora else StockDecision.NONE,
            needs_stock_decision=False,
            refund=False,
            personalized=personalizado,
        )

    if producao == FulfillmentStatus.NOT_STARTED:
        return CancellationPlan(
            auto=not personalizado,
            reason=_("A produção ainda não começou."),
            stock=StockDecision.RETURNED if tem_saldo_fora else StockDecision.NONE,
            needs_stock_decision=False,
            refund=True,
            personalized=personalizado,
        )

    if producao == FulfillmentStatus.READY:
        # A peça existe e está inteira: ela volta para a prateleira. Mas volta
        # quando o dinheiro voltar, não antes — enquanto o reembolso não sai, a
        # peça está reservada àquele pedido.
        return CancellationPlan(
            auto=False,
            reason=_("A encomenda está pronta: a peça volta ao estoque quando o reembolso for concluído."),
            stock=StockDecision.ON_REFUND,
            needs_stock_decision=False,
            refund=True,
            personalized=personalizado,
        )

    # Em produção: a peça pode estar pela metade. Ninguém decide isto por quem
    # está olhando para a impressora.
    return CancellationPlan(
        auto=False,
        reason=_("A produção já começou: alguém precisa decidir o que fazer com a peça."),
        stock=StockDecision.NONE,
        needs_stock_decision=tem_saldo_fora,
        refund=True,
        personalized=personalizado,
    )


def _pode_decidir(order: Order, *, auto: bool) -> bool:
    """Este pedido ainda está esperando uma decisão de cancelamento?

    Escrito uma vez e usado nos dois lados da trava: antes dela, para não abrir
    transação à toa; **depois** dela, sobre a linha travada, que é onde a
    resposta vale. Foi a falta dessa segunda conferência que deixava uma
    aprovação e uma recusa simultâneas se sobrescreverem.
    """
    if order.cancelled_at is not None:
        return False
    if order.cancellation_status == CancellationStatus.APPROVED:
        return False
    # O caminho automático nasce do próprio `request_cancellation`, que acabou
    # de gravar `REQUESTED`; os demais exigem uma solicitação em aberto.
    return auto or order.cancellation_status == CancellationStatus.REQUESTED


def request_cancellation(order: Order, reason: str, user=None) -> bool:
    """O cliente pede. Conforme a regra, a loja já responde ou manda analisar.

    Quando o plano é automático — nada cobrado, ou pago sem a produção ter
    começado — o cancelamento acontece **aqui**, e o cliente recebe a resposta
    em vez de um "vamos analisar" seguido de um "aprovado" trinta segundos
    depois.

    Levanta `CancellationRefused` quando o prazo legal já fechou: é a única
    situação em que a loja não tem o que decidir.
    """
    if order.cancellation_status in {CancellationStatus.REQUESTED, CancellationStatus.APPROVED}:
        return False
    if not order.can_request_cancellation:
        raise CancellationRefused(
            _("O prazo para cancelar ou devolver esta encomenda já passou.")
        )

    # A trava aqui é o que impede o duplo clique de gravar duas solicitações e
    # mandar dois pares de e-mail: sem ela, as duas requisições leriam o mesmo
    # `NONE` e as duas passariam.
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.cancellation_status in {
            CancellationStatus.REQUESTED,
            CancellationStatus.APPROVED,
        }:
            return False

        locked.cancellation_status = CancellationStatus.REQUESTED
        locked.cancellation_reason = reason or ""
        locked.cancellation_requested_at = timezone.now()
        locked.save(
            update_fields=[
                "cancellation_status", "cancellation_reason",
                "cancellation_requested_at", "updated_at",
            ]
        )

    order.refresh_from_db()
    order.log(OrderEvent.CANCELLATION_REQUESTED, (reason or "")[:300], user=user)

    plano = cancellation_plan(order)
    if plano.auto:
        approve_cancellation(order, user=user, auto=True)
        return True

    # Dois avisos, e os dois na hora.
    #
    # A equipe precisa saber **agora**: o cliente está esperando uma decisão de
    # uma pessoa. E o cliente precisa saber que o pedido dele chegou — quem
    # clica em "cancelar" e não recebe nada assume que não funcionou.
    #
    # Independentes de propósito: um falhando não impede o outro, e nenhum dos
    # dois desfaz a solicitação, que já está gravada e aparece em destaque no
    # Admin.
    _avisar(
        "send_cancellation_requested_email", order,
        "Falha ao avisar a equipe do cancelamento solicitado no pedido %s",
    )
    _avisar(
        "send_cancellation_received_email", order,
        "Falha ao confirmar ao cliente a solicitação de cancelamento do pedido %s",
    )
    return True


def approve_cancellation(
    order: Order,
    note: str = "",
    user=None,
    *,
    restore_stock: bool | None = None,
    auto: bool = False,
) -> bool:
    """**A** operação de cancelamento. Não há outra.

    Cancelar não é escrever um campo: é parar a produção, decidir o estoque,
    fechar a cobrança ou abrir o reembolso, recalcular o pedido e avisar o
    cliente. Tudo isso acontece aqui, dentro de uma transação — ou nada
    acontece. Enquanto essas coisas estavam espalhadas, o formulário do Admin
    conseguia gravar "aprovado" e deixar a peça na fila da oficina.

    ``restore_stock`` é a decisão humana sobre a peça. Quando o plano a exige e
    ela não vem, a função levanta `StockDecisionRequired` em vez de escolher —
    devolver ao saldo uma peça que virou sucata cria estoque que não existe.

    Idempotente: uma segunda chamada encontra o pedido já cancelado e devolve
    `False`, sem segundo e-mail e sem segunda devolução de estoque.
    """
    if not _pode_decidir(order, auto=auto):
        return False

    # Uma primeira leitura, só para poder recusar cedo e não abrir transação
    # à toa quando falta a decisão de estoque.
    if cancellation_plan(order).needs_stock_decision and restore_stock is None:
        raise StockDecisionRequired(
            _(
                "Este pedido já entrou em produção. Antes de aprovar, diga se as "
                "peças voltam ao estoque."
            )
        )

    agora = timezone.now()
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        # A mesma condição de entrada, reconferida sobre a linha travada. Sem
        # esta linha, uma recusa gravada entre a leitura e a trava era
        # silenciosamente sobrescrita por esta aprovação.
        if not _pode_decidir(locked, auto=auto):
            return False

        # O plano é decidido **aqui**, com o pedido travado. Calculá-lo lá fora
        # e aplicá-lo aqui é decidir com uma fotografia velha: uma transferência
        # confirmada entre as duas linhas fazia a aprovação gravar "não cobrado"
        # sobre um pedido que acabara de ser pago, e o reembolso nunca abria.
        plano = cancellation_plan(locked)
        if plano.needs_stock_decision and restore_stock is None:
            raise StockDecisionRequired(
                _(
                    "Este pedido já entrou em produção. Antes de aprovar, diga se as "
                    "peças voltam ao estoque."
                )
            )

        # A decisão explícita manda sobre o plano: quem está olhando para a peça
        # sabe mais do que a regra.
        if restore_stock is None:
            decisao = plano.stock
        else:
            decisao = StockDecision.RETURNED if restore_stock else StockDecision.KEPT

        locked.cancellation_status = CancellationStatus.APPROVED
        locked.cancellation_decision_note = note or ""
        locked.cancellation_decided_at = agora
        locked.cancellation_decided_by = user
        locked.cancellation_auto = auto
        locked.cancelled_at = agora

        # A produção para — mas só quando ainda há o que parar. Uma encomenda
        # entregue não volta a "interrompida": ela foi entregue, e o histórico
        # não se reescreve.
        parou = locked.fulfillment_status in {
            FulfillmentStatus.NOT_STARTED,
            FulfillmentStatus.IN_PRODUCTION,
            FulfillmentStatus.READY,
        }
        if parou:
            locked.fulfillment_status = FulfillmentStatus.HALTED

        if plano.refund:
            # Só abre o que ainda não existe: uma reaprovação não joga um
            # reembolso já concluído de volta para "pendente".
            if locked.refund_status == RefundStatus.NONE:
                locked.refund_status = RefundStatus.PENDING
        else:
            # Ninguém está esperando este dinheiro. Deixar "pendente" fazia o
            # cliente ver "Aguardando pagamento" ao lado de "Cancelado".
            locked.payment_status = PaymentStatus.NOT_CHARGED

        if decisao != StockDecision.RETURNED:
            locked.stock_return_decision = decisao

        locked.status = locked.derived_status
        locked.save(
            update_fields=[
                "cancellation_status", "cancellation_decision_note", "cancellation_decided_at",
                "cancellation_decided_by", "cancellation_auto", "cancelled_at",
                "fulfillment_status", "payment_status", "refund_status",
                "stock_return_decision", "status", "updated_at",
            ]
        )

    order.refresh_from_db()

    if decisao == StockDecision.RETURNED:
        return_stock(order, user=user, motivo=str(_("Cancelamento aprovado.")))
        order.refresh_from_db()

    detalhe = note or plano.reason
    if auto:
        detalhe = _("Aprovado automaticamente: %(motivo)s") % {"motivo": plano.reason}
    order.log(OrderEvent.CANCELLATION_APPROVED, str(detalhe)[:300], user=user)

    if parou:
        order.log(
            OrderEvent.PRODUCTION_HALTED,
            _("Produção interrompida pelo cancelamento."),
            user=user,
            visible=False,
        )
    if decisao == StockDecision.KEPT:
        order.log(
            OrderEvent.STOCK_KEPT,
            _("Decidido não devolver as peças ao estoque."),
            user=user,
            visible=False,
        )
    if decisao == StockDecision.ON_REFUND:
        order.log(
            OrderEvent.STOCK_KEPT,
            _("As peças voltam ao estoque quando o reembolso for concluído."),
            user=user,
            visible=False,
        )
    order.log(OrderEvent.CANCELLED, _("Pedido cancelado."), user=user)

    if plano.refund:
        order.log(
            OrderEvent.REFUND_PENDING,
            _("Reembolso de %(valor)s aberto.") % {"valor": f"{order.refund_due:.2f}"},
            user=user,
        )

    _avisar(
        "send_cancellation_approved_email", order,
        "Falha ao avisar o cliente do cancelamento aprovado do pedido %s",
    )
    if plano.refund:
        _avisar(
            "send_refund_started_email", order,
            "Falha ao avisar o cliente da abertura do reembolso do pedido %s",
        )
    return True


def refuse_cancellation(order: Order, note: str = "", user=None) -> bool:
    """Recusa: o pedido volta ao curso normal, com a resposta registrada.

    A ``note`` não é anotação interna — ela **é** o que o cliente lê, no e-mail
    e no acompanhamento. Por isso vale a pena escrevê-la para uma pessoa.
    """
    if not _pode_decidir(order, auto=False):
        return False

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        # Reconferido sobre a linha travada, como na aprovação: era esta a
        # conferência que faltava, e sem ela uma recusa conseguia carimbar
        # "recusado" num pedido que outra pessoa acabara de cancelar.
        if not _pode_decidir(locked, auto=False):
            return False

        locked.cancellation_status = CancellationStatus.REFUSED
        locked.cancellation_decision_note = note or ""
        locked.cancellation_decided_at = timezone.now()
        locked.cancellation_decided_by = user
        locked.save(
            update_fields=[
                "cancellation_status", "cancellation_decision_note",
                "cancellation_decided_at", "cancellation_decided_by", "updated_at",
            ]
        )

    order.refresh_from_db()
    order.log(OrderEvent.CANCELLATION_REFUSED, (note or "")[:300], user=user)

    _avisar(
        "send_cancellation_refused_email", order,
        "Falha ao avisar o cliente da recusa do cancelamento do pedido %s",
    )
    return True


# ---------------------------------------------------------------------------
# Reembolso
# ---------------------------------------------------------------------------


#: Como a referência da transferência fica gravada no histórico.
#:
#: Delimitada de propósito: sem os colchetes, procurar "TRF-1" encontraria
#: também "TRF-11", e um reembolso legítimo seria recusado como repetido.
REFUND_REF_TAG = "[ref:%s]"


def register_refund(
    order: Order,
    amount,
    *,
    reference: str,
    user=None,
) -> bool:
    """Registra um reembolso **já feito** no banco. Não move dinheiro.

    Quem devolve é uma pessoa, numa transferência. O que o sistema faz é
    guardar o que ela fez — valor, data, referência e autor — e manter a conta:
    somando os registros, sabe-se o que já voltou e o que falta.

    Parcial e total não são escolhas de quem registra: são o resultado da
    soma. Quando o acumulado alcança o total do pedido, o reembolso está
    concluído — e, se a decisão de estoque era "quando o reembolso sair", é
    aqui que as peças voltam à prateleira.

    Devolve `False` quando não há reembolso aberto ou o valor não é positivo.
    """
    valor = Decimal(str(amount)).quantize(Decimal("0.01"))
    if valor <= ZERO:
        return False
    referencia = (reference or "").strip()[:140]
    if not referencia:
        # Sem referência não há como distinguir um segundo reembolso de um
        # segundo clique — e é justamente por isso que ela é obrigatória.
        return False
    if order.refund_status not in {RefundStatus.PENDING, RefundStatus.PARTIAL}:
        return False

    agora = timezone.now()
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.refund_status not in {RefundStatus.PENDING, RefundStatus.PARTIAL}:
            return False

        # A mesma transferência não entra duas vezes.
        #
        # O valor é somativo por natureza — dois reembolsos de cinco euros são
        # dez —, e é por isso que um clique repetido dobrava o registro sem que
        # nada percebesse. A referência do banco é única por transferência:
        # repetida, é o mesmo dinheiro contado de novo.
        #
        # A conferência é contra o **histórico inteiro**, e não contra a última
        # referência gravada: com três transferências A, B, A, comparar só com a
        # última deixava a primeira entrar de novo.
        if locked.history.filter(
            event__in=[OrderEvent.REFUNDED, OrderEvent.REFUND_PARTIAL],
            message__contains=REFUND_REF_TAG % referencia,
        ).exists():
            return False

        # Nunca mais do que foi cobrado: um erro de digitação não pode fazer o
        # pedido dever dinheiro ao cliente.
        acumulado = min(locked.refunded_amount + valor, locked.total)
        creditado = acumulado - locked.refunded_amount
        locked.refunded_amount = acumulado
        locked.refund_reference = referencia or locked.refund_reference
        locked.refunded_by = user or locked.refunded_by

        concluido = acumulado >= locked.total
        locked.refund_status = RefundStatus.DONE if concluido else RefundStatus.PARTIAL
        if concluido:
            locked.refunded_at = agora

        locked.status = locked.derived_status
        locked.save(
            update_fields=[
                "refunded_amount", "refund_reference", "refunded_by", "refund_status",
                "refunded_at", "status", "updated_at",
            ]
        )
        decisao = locked.stock_return_decision

    order.refresh_from_db()

    order.log(
        OrderEvent.REFUNDED if order.refund_status == RefundStatus.DONE else OrderEvent.REFUND_PARTIAL,
        (
            _("%(valor)s reembolsado(s). %(ref)s")
            % {
                "valor": f"{creditado:.2f} {order.currency}",
                "ref": REFUND_REF_TAG % referencia,
            }
        )[:300],
        user=user,
    )

    if order.refund_status == RefundStatus.DONE and decisao == StockDecision.ON_REFUND:
        return_stock(order, user=user, motivo=str(_("Reembolso concluído.")))
        order.refresh_from_db()

    _avisar(
        "send_refund_registered_email", order,
        "Falha ao avisar o cliente do reembolso do pedido %s",
    )
    return True
