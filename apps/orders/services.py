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

from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.cart.cart import max_quantity_for
from apps.catalog.models import ProductStatus
from apps.orders import taxes
from apps.orders.models import (
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
)
from apps.shipping import services as shipping_services

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

        if line.variant is not None and not line.variant.is_active:
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

    options = shipping_services.quote(country, weight, production)
    option = None
    if method is not None:
        option = shipping_services.quote_for_method(country, weight, method, production)
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
    if line.has_customization:
        return FulfillmentType.PERSONALIZED
    if line.product.made_to_order:
        return FulfillmentType.MADE_TO_ORDER
    return FulfillmentType.STOCK


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
        color_name=(variant.color.name if variant is not None and variant.color_id else ""),
        size_name=(variant.size if variant is not None else ""),
        material_name=(variant.material.name if variant is not None and variant.material_id else ""),
        quantity=line.quantity,
        unit_price=taxes.money(line.unit_price),
        total=taxes.money(line.total),
        unit_weight_grams=shipping_services.line_weight_grams(product, variant),
        fulfillment_type=_fulfillment_type(line),
        production_days=product.production_lead_time_days or 0,
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
    is_gift: bool = False,
    customer_note: str = "",
    language: str = "",
) -> Order:
    """Cria o pedido a partir do carrinho. Levanta ``CheckoutError`` se não der.

    Tudo em uma transação: ou nasce o pedido inteiro (com número, itens e
    endereços), ou não nasce nada — inclusive o número, que volta a ficar livre.
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

    # O método vem de um <input>. O preço dele é recalculado aqui, no servidor.
    option = shipping_services.quote_for_method(country, weight, shipping_method, production)
    if option is None:
        raise CheckoutError(_("Escolha uma forma de entrega válida para este endereço."))

    subtotal = taxes.money(sum((line.total for line in lines), ZERO))
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
    )

    OrderAddress.from_customer_address(order, shipping_address, AddressKind.SHIPPING)
    OrderAddress.from_customer_address(order, billing_address, AddressKind.BILLING)

    OrderItem.objects.bulk_create([_item_from_line(order, line) for line in lines])

    order.log(OrderEvent.CREATED, _("Pedido criado."))
    return order


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
    from apps.catalog.models import Product, ProductVariant

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

            if item.variant_id:
                target = ProductVariant.objects.select_for_update().filter(pk=item.variant_id).first()
            elif item.product_id:
                target = Product.objects.select_for_update().filter(pk=item.product_id).first()
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


def confirm_payment(order: Order, payment=None, *, method_label: str = "") -> bool:
    """Marca o pedido como pago e confirmado. Idempotente.

    É chamada pelo webhook — nunca pela página de retorno do cliente. Se o
    pedido já estava pago, não faz nada e devolve ``False``: é exatamente o
    caso de a Stripe reenviar o mesmo evento.
    """
    from apps.orders.emails import send_order_emails

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        already_paid = locked.payment_status == PaymentStatus.PAID
        if not already_paid:
            now = timezone.now()
            locked.payment_status = PaymentStatus.PAID
            locked.paid_at = now
            if locked.status == OrderStatus.PENDING:
                locked.status = OrderStatus.CONFIRMED
                locked.confirmed_at = now
            if locked.fulfillment_status == FulfillmentStatus.NOT_STARTED:
                locked.fulfillment_status = FulfillmentStatus.IN_PRODUCTION
            locked.save(
                update_fields=[
                    "payment_status", "paid_at", "status", "confirmed_at",
                    "fulfillment_status", "updated_at",
                ]
            )

    order.refresh_from_db()
    if already_paid:
        return False

    if payment is not None:
        payment.status = PaymentState.SUCCEEDED
        payment.paid_at = order.paid_at
        if method_label:
            payment.method_label = method_label
        payment.save(update_fields=["status", "paid_at", "method_label", "updated_at"])

    order.log(OrderEvent.PAID, _("Pagamento confirmado."))
    order.log(OrderEvent.CONFIRMED, _("Pedido confirmado."))

    apply_stock(order)
    send_order_emails(order)
    return True


def register_payment_failure(order: Order, payment=None, reason: str = "") -> None:
    """Pagamento recusado. O pedido continua ``pending``: dá para tentar de novo."""
    if payment is not None:
        payment.status = PaymentState.FAILED
        payment.failure_message = (reason or "")[:300]
        payment.save(update_fields=["status", "failure_message", "updated_at"])

    if order.payment_status == PaymentStatus.PAID:
        return

    order.payment_status = PaymentStatus.FAILED
    order.save(update_fields=["payment_status", "updated_at"])
    order.log(OrderEvent.PAYMENT_FAILED, (reason or "")[:300])


# ---------------------------------------------------------------------------
# Expedição
# ---------------------------------------------------------------------------


def mark_shipped(order: Order, tracking_number: str = "", user=None) -> bool:
    """Marca como enviado e dispara o e-mail de envio (uma vez só)."""
    from apps.orders.emails import send_order_shipped_email

    if order.fulfillment_status == FulfillmentStatus.SHIPPED and order.shipped_email_sent_at:
        return False

    order.fulfillment_status = FulfillmentStatus.SHIPPED
    order.shipped_at = order.shipped_at or timezone.now()
    if tracking_number:
        order.tracking_number = tracking_number.strip()
    order.save(update_fields=["fulfillment_status", "shipped_at", "tracking_number", "updated_at"])

    # Recarrega antes de montar o e-mail: o objeto em memória pode carregar
    # relações lidas há muito tempo (a transportadora, por exemplo, que é de
    # onde sai o link de rastreio).
    order.refresh_from_db()

    order.log(OrderEvent.SHIPPED, order.tracking_number, user=user)
    send_order_shipped_email(order)
    return True


# ---------------------------------------------------------------------------
# Cancelamento
# ---------------------------------------------------------------------------


def request_cancellation(order: Order, reason: str, user=None) -> bool:
    """O cliente **pede**; quem decide é a loja."""
    if not order.can_request_cancellation:
        return False

    order.cancellation_status = CancellationStatus.REQUESTED
    order.cancellation_reason = reason or ""
    order.cancellation_requested_at = timezone.now()
    order.save(
        update_fields=[
            "cancellation_status", "cancellation_reason", "cancellation_requested_at", "updated_at",
        ]
    )
    order.log(OrderEvent.CANCELLATION_REQUESTED, (reason or "")[:300], user=user)
    return True


def approve_cancellation(order: Order, note: str = "", user=None) -> bool:
    """Aprova o cancelamento. **Não** reembolsa: reembolso é ação à parte."""
    if order.cancellation_status != CancellationStatus.REQUESTED:
        return False

    now = timezone.now()
    order.cancellation_status = CancellationStatus.APPROVED
    order.cancellation_decision_note = note or ""
    order.cancellation_decided_at = now
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    order.save(
        update_fields=[
            "cancellation_status", "cancellation_decision_note", "cancellation_decided_at",
            "status", "cancelled_at", "updated_at",
        ]
    )
    order.log(OrderEvent.CANCELLATION_APPROVED, (note or "")[:300], user=user)
    order.log(OrderEvent.CANCELLED, _("Pedido cancelado."), user=user)
    return True


def refuse_cancellation(order: Order, note: str = "", user=None) -> bool:
    """Recusa: o pedido volta ao curso normal, com o motivo registrado."""
    if order.cancellation_status != CancellationStatus.REQUESTED:
        return False

    order.cancellation_status = CancellationStatus.REFUSED
    order.cancellation_decision_note = note or ""
    order.cancellation_decided_at = timezone.now()
    order.save(
        update_fields=[
            "cancellation_status", "cancellation_decision_note", "cancellation_decided_at",
            "updated_at",
        ]
    )
    order.log(OrderEvent.CANCELLATION_REFUSED, (note or "")[:300], user=user)
    return True
