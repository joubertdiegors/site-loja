"""Pedidos: o que foi comprado, por quanto, para onde e em que estado.

A regra que organiza este arquivo inteiro é uma só: **um pedido antigo não
pode mudar porque o catálogo mudou**. Preço, nome, SKU, variante, prazo de
produção, endereço, alíquota de TVA e moeda são todos copiados no momento da
compra. O produto continua referenciado (para o admin abrir a ficha), mas
nenhuma tela precisa dele para reconstruir o pedido.

Três estados independentes em vez de um só (ver ``docs/ARQUITETURA.md``):

* ``status``            — o ciclo comercial do pedido;
* ``payment_status``    — o dinheiro;
* ``fulfillment_status``— a produção e a entrega.

Um pedido pago que ainda não saiu da oficina é ``confirmed`` + ``paid`` +
``in_production``. Com um campo só, esse estado não teria nome.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------


class OrderStatus(models.TextChoices):
    PENDING = "pending", _("Aguardando pagamento")
    CONFIRMED = "confirmed", _("Confirmado")
    COMPLETED = "completed", _("Concluído")
    CANCELLED = "cancelled", _("Cancelado")
    REFUNDED = "refunded", _("Reembolsado")


class PaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pendente")
    PAID = "paid", _("Pago")
    FAILED = "failed", _("Recusado")
    PARTIALLY_REFUNDED = "partially_refunded", _("Parcialmente reembolsado")
    REFUNDED = "refunded", _("Reembolsado")


class FulfillmentStatus(models.TextChoices):
    NOT_STARTED = "not_started", _("Não iniciado")
    IN_PRODUCTION = "in_production", _("Em produção")
    READY = "ready", _("Pronto")
    SHIPPED = "shipped", _("Enviado")
    DELIVERED = "delivered", _("Entregue")


class CancellationStatus(models.TextChoices):
    """Estado da **solicitação** de cancelamento — não do pedido.

    Está separado de ``OrderStatus`` de propósito. Um pedido com cancelamento
    pedido continua ``confirmed``: ele foi pago, está na fila de produção e só
    sai de lá quando alguém decidir. Misturar as duas coisas obrigaria a
    inventar um estado de pedido que não descreve nem o dinheiro nem a
    produção, e faria "cancelamento recusado" perder o estado anterior.
    """

    NONE = "none", _("Sem solicitação")
    REQUESTED = "requested", _("Cancelamento solicitado")
    APPROVED = "approved", _("Cancelamento aprovado")
    REFUSED = "refused", _("Cancelamento recusado")


class OrderEvent(models.TextChoices):
    """O que entra na linha do tempo do pedido."""

    CREATED = "created", _("Pedido criado")
    PAYMENT_STARTED = "payment_started", _("Pagamento iniciado")
    PAID = "paid", _("Pagamento confirmado")
    PAYMENT_FAILED = "payment_failed", _("Pagamento recusado")
    CONFIRMED = "confirmed", _("Pedido confirmado")
    IN_PRODUCTION = "in_production", _("Em produção")
    READY = "ready", _("Pronto para envio")
    SHIPPED = "shipped", _("Enviado")
    DELIVERED = "delivered", _("Entregue")
    COMPLETED = "completed", _("Concluído")
    CANCELLATION_REQUESTED = "cancellation_requested", _("Cancelamento solicitado")
    CANCELLATION_APPROVED = "cancellation_approved", _("Cancelamento aprovado")
    CANCELLATION_REFUSED = "cancellation_refused", _("Cancelamento recusado")
    CANCELLED = "cancelled", _("Pedido cancelado")
    REFUNDED = "refunded", _("Reembolsado")
    STOCK_SHORTAGE = "stock_shortage", _("Estoque insuficiente")
    EMAIL_RESENT = "email_resent", _("E-mail reenviado")
    STATUS_CHANGED = "status_changed", _("Situação alterada")
    NOTE = "note", _("Observação")


# ---------------------------------------------------------------------------
# Numeração
# ---------------------------------------------------------------------------


class OrderNumberSequence(models.Model):
    """Contador do número público, um por ano.

    O número do pedido não pode ser o PK: o PK vaza quantos pedidos a loja já
    teve, muda se um dia houver importação de dados e não tem ano. E não pode
    ser ``count() + 1``: dois checkouts simultâneos leriam o mesmo total.

    Aqui a próxima numeração sai de um ``SELECT ... FOR UPDATE`` sobre uma
    linha só — o banco serializa, e cada pedido recebe um número.
    """

    year = models.PositiveIntegerField("ano", unique=True)
    last_number = models.PositiveIntegerField("último número", default=0)

    class Meta:
        verbose_name = "sequência de numeração"
        verbose_name_plural = "sequências de numeração"
        ordering = ("-year",)

    def __str__(self) -> str:
        return f"{self.year}: {self.last_number}"


def next_order_number(year: int | None = None) -> str:
    """Devolve ``JD-2026-000001`` e reserva esse número.

    Precisa rodar dentro da transação que cria o pedido: se a criação falhar, o
    número volta a ficar livre em vez de deixar um buraco na contabilidade.
    """
    year = year or timezone.now().year
    prefix = getattr(settings, "ORDER_NUMBER_PREFIX", "JD")

    with transaction.atomic():
        sequence, _created = OrderNumberSequence.objects.select_for_update().get_or_create(
            year=year
        )
        sequence.last_number += 1
        sequence.save(update_fields=["last_number"])

    return f"{prefix}-{year}-{sequence.last_number:06d}"


# ---------------------------------------------------------------------------
# Pedido
# ---------------------------------------------------------------------------


class OrderQuerySet(models.QuerySet):
    def for_user(self, user):
        """Os pedidos **daquele** usuário. Nunca filtrar por id vindo da URL."""
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(customer__user=user)

    def paid(self):
        return self.filter(payment_status=PaymentStatus.PAID)

    def with_details(self):
        return self.select_related(
            "customer", "customer__user", "shipping_method", "shipping_method__carrier"
        ).prefetch_related("items", "addresses")


class Order(TimeStampedModel):
    number = models.CharField(
        "número", max_length=24, unique=True, editable=False, help_text="JD-2026-000001"
    )
    customer = models.ForeignKey(
        "accounts.Customer",
        verbose_name="cliente",
        related_name="orders",
        on_delete=models.PROTECT,
    )

    status = models.CharField(
        "situação", max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING,
        db_index=True,
    )
    payment_status = models.CharField(
        "pagamento", max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING,
        db_index=True,
    )
    fulfillment_status = models.CharField(
        "produção", max_length=20, choices=FulfillmentStatus.choices,
        default=FulfillmentStatus.NOT_STARTED, db_index=True,
    )

    # -- dinheiro ----------------------------------------------------------
    #
    # Tudo em Decimal e tudo copiado. `currency` no pedido, e não em settings,
    # porque um pedido de 2026 em euro tem que continuar valendo euro depois de
    # a loja passar a vender em outra moeda.
    currency = models.CharField("moeda", max_length=3, default="EUR")
    subtotal = models.DecimalField("subtotal", max_digits=10, decimal_places=2, default=ZERO)
    discount_total = models.DecimalField("desconto", max_digits=10, decimal_places=2, default=ZERO)
    shipping_total = models.DecimalField("frete", max_digits=10, decimal_places=2, default=ZERO)
    tax_total = models.DecimalField("TVA", max_digits=10, decimal_places=2, default=ZERO)
    total = models.DecimalField("total", max_digits=10, decimal_places=2, default=ZERO)

    # -- imposto -----------------------------------------------------------
    #
    # A alíquota fica gravada no pedido. Se a Bélgica mudar a TVA amanhã, a
    # fatura de hoje continua auditável.
    tax_country = models.CharField(
        "país do imposto", max_length=2, blank=True, help_text="ISO do país de entrega."
    )
    tax_rate = models.DecimalField("alíquota (%)", max_digits=5, decimal_places=2, default=ZERO)
    prices_include_tax = models.BooleanField(
        "preços com TVA incluída",
        default=True,
        help_text="No varejo B2C da UE o preço anunciado inclui a TVA.",
    )

    # -- entrega -----------------------------------------------------------
    shipping_method = models.ForeignKey(
        "shipping.ShippingMethod",
        verbose_name="método de entrega",
        related_name="orders",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    shipping_method_label = models.CharField(
        "método (snapshot)", max_length=160, blank=True,
        help_text="Nome da transportadora e do método no dia da compra.",
    )
    shipping_min_days = models.PositiveIntegerField("prazo mínimo (dias úteis)", default=0)
    shipping_max_days = models.PositiveIntegerField("prazo máximo (dias úteis)", default=0)
    production_days = models.PositiveIntegerField("prazo de produção (dias úteis)", default=0)
    total_weight_grams = models.PositiveIntegerField("peso total (g)", default=0)
    tracking_number = models.CharField("código de rastreio", max_length=80, blank=True)

    # -- presente ----------------------------------------------------------
    is_gift = models.BooleanField(
        "enviar como presente",
        default=False,
        help_text="A entrega vai para outra pessoa; a fatura continua com o cliente.",
    )
    gift_message = models.TextField(
        "mensagem do presente",
        blank=True,
        help_text="Reservado: ainda não é pedido no checkout.",
    )

    # -- cancelamento ------------------------------------------------------
    cancellation_status = models.CharField(
        "cancelamento", max_length=20, choices=CancellationStatus.choices,
        default=CancellationStatus.NONE,
    )
    cancellation_reason = models.TextField("motivo do cliente", blank=True)
    cancellation_requested_at = models.DateTimeField("solicitado em", null=True, blank=True)
    cancellation_decision_note = models.TextField("resposta da loja", blank=True)
    cancellation_decided_at = models.DateTimeField("decidido em", null=True, blank=True)

    # -- outros ------------------------------------------------------------
    language = models.CharField(
        "idioma do cliente",
        max_length=10,
        blank=True,
        help_text="Idioma em que o pedido foi feito. Os e-mails saem nele.",
    )
    customer_note = models.TextField("observação do cliente", blank=True)
    paid_at = models.DateTimeField("pago em", null=True, blank=True)
    confirmed_at = models.DateTimeField("confirmado em", null=True, blank=True)
    shipped_at = models.DateTimeField("enviado em", null=True, blank=True)
    cancelled_at = models.DateTimeField("cancelado em", null=True, blank=True)

    # Controle de envio de e-mail: o webhook pode chegar duas vezes, e cada
    # e-mail tem a sua própria marca. Sem a do administrativo, uma reentrega
    # mandaria a mesma ordem de produção de novo — e alguém imprimiria duas.
    confirmation_email_sent_at = models.DateTimeField(
        "e-mail de confirmação enviado em", null=True, blank=True
    )
    admin_email_sent_at = models.DateTimeField(
        "ordem de produção enviada em", null=True, blank=True
    )
    shipped_email_sent_at = models.DateTimeField(
        "e-mail de envio enviado em", null=True, blank=True
    )
    stock_applied_at = models.DateTimeField(
        "estoque baixado em",
        null=True,
        blank=True,
        help_text="Preenchido uma única vez, na confirmação do pagamento.",
    )

    objects = OrderQuerySet.as_manager()

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("status", "payment_status"), name="order_status_idx"),
        ]

    def __str__(self) -> str:
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_order_number()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("orders:detail", kwargs={"number": self.number})

    # -- endereços ---------------------------------------------------------

    def _address(self, kind: str):
        for address in self.addresses.all():
            if address.kind == kind:
                return address
        return None

    @property
    def shipping_address(self):
        return self._address(AddressKind.SHIPPING)

    @property
    def billing_address(self):
        return self._address(AddressKind.BILLING)

    # -- leitura -----------------------------------------------------------

    @property
    def currency_symbol(self) -> str:
        return {"EUR": "€", "USD": "$", "GBP": "£"}.get(self.currency, self.currency)

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items.all())

    @property
    def is_paid(self) -> bool:
        return self.payment_status == PaymentStatus.PAID

    @property
    def is_cancelled(self) -> bool:
        return self.status == OrderStatus.CANCELLED

    @property
    def delivery_days_display(self) -> str:
        low, high = self.shipping_min_days, self.shipping_max_days
        if not high:
            return ""
        return str(low) if low == high else f"{low}–{high}"

    @property
    def tracking_url(self) -> str:
        if not self.tracking_number or self.shipping_method is None:
            return ""
        return self.shipping_method.carrier.tracking_url(self.tracking_number)

    @property
    def can_request_cancellation(self) -> bool:
        """O cliente pode pedir cancelamento?

        Só faz sentido enquanto a loja ainda pode agir: pedido vivo, sem
        solicitação em aberto e antes de sair para entrega.
        """
        if self.status in {OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.COMPLETED}:
            return False
        if self.cancellation_status in {CancellationStatus.REQUESTED, CancellationStatus.APPROVED}:
            return False
        return self.fulfillment_status in {
            FulfillmentStatus.NOT_STARTED,
            FulfillmentStatus.IN_PRODUCTION,
            FulfillmentStatus.READY,
        }

    # -- escrita -----------------------------------------------------------

    def log(self, event: str, message: str = "", user=None, visible: bool = True):
        """Registra um evento na linha do tempo."""
        return OrderStatusHistory.objects.create(
            order=self,
            event=event,
            message=message,
            created_by=user if (user is not None and user.is_authenticated) else None,
            is_customer_visible=visible,
        )


# ---------------------------------------------------------------------------
# Endereço copiado para o pedido
# ---------------------------------------------------------------------------


class AddressKind(models.TextChoices):
    BILLING = "billing", _("Faturamento")
    SHIPPING = "shipping", _("Entrega")


class OrderAddress(models.Model):
    """Cópia do endereço no momento da compra.

    Não é uma FK para ``CustomerAddress``: se fosse, corrigir o número da casa
    hoje mudaria a nota fiscal do pedido de três meses atrás — e apagar o
    endereço deixaria o pedido sem destino. ``source`` guarda de onde a cópia
    veio, mas só para referência; nada é lido de lá.
    """

    order = models.ForeignKey(
        Order, verbose_name="pedido", related_name="addresses", on_delete=models.CASCADE
    )
    kind = models.CharField("tipo", max_length=10, choices=AddressKind.choices)
    source = models.ForeignKey(
        "accounts.CustomerAddress",
        verbose_name="endereço de origem",
        related_name="order_addresses",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Apenas referência. O pedido usa os dados copiados abaixo.",
    )

    first_name = models.CharField("nome", max_length=80)
    last_name = models.CharField("sobrenome", max_length=80)
    company_name = models.CharField("empresa", max_length=120, blank=True)
    vat_number = models.CharField("NIF / VAT", max_length=20, blank=True)
    phone = models.CharField("telefone", max_length=25, blank=True)
    street = models.CharField("endereço", max_length=160)
    street_extra = models.CharField("complemento", max_length=120, blank=True)
    postal_code = models.CharField("código postal", max_length=16)
    city = models.CharField("cidade", max_length=80)
    region = models.CharField("região", max_length=80, blank=True)
    country_code = models.CharField("país (ISO)", max_length=2)
    country_name = models.CharField("país", max_length=80)

    class Meta:
        verbose_name = "endereço do pedido"
        verbose_name_plural = "endereços do pedido"
        ordering = ("order", "kind")
        constraints = [
            models.UniqueConstraint(fields=("order", "kind"), name="order_address_unique_kind"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.full_name}, {self.city}"

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    def lines(self) -> list[str]:
        parts = [
            self.full_name,
            self.company_name,
            self.street,
            self.street_extra,
            " ".join(part for part in (self.postal_code, self.city) if part).strip(),
            self.region,
            self.country_name,
        ]
        return [part for part in parts if part]

    @classmethod
    def from_customer_address(cls, order, address, kind: str) -> "OrderAddress":
        """Cria a cópia. Uma linha por tipo, por pedido."""
        return cls.objects.create(
            order=order,
            kind=kind,
            source=address,
            first_name=address.first_name,
            last_name=address.last_name,
            company_name=address.company_name,
            vat_number=address.vat_number,
            phone=address.phone,
            street=address.street,
            street_extra=address.street_extra,
            postal_code=address.postal_code,
            city=address.city,
            region=address.region,
            country_code=address.country.iso_code,
            country_name=address.country.name,
        )


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


class FulfillmentType(models.TextChoices):
    """Como esta linha é atendida — decidido no momento da compra.

    Vale um campo próprio porque a resposta muda a fila da oficina e não pode
    ser recalculada depois: um produto que hoje é "sob encomenda" pode ter
    virado item de prateleira, e o pedido antigo continua tendo sido sob
    encomenda.
    """

    STOCK = "stock", _("Do estoque")
    MADE_TO_ORDER = "made_to_order", _("Produzido sob encomenda")
    PERSONALIZED = "personalized", _("Personalizado")


class OrderItem(models.Model):
    """Uma linha do pedido — com tudo copiado.

    ``PROTECT`` em produto, variante e arquivo: catálogo se **desativa**
    (``status = inactive``), não se apaga. Um pedido que aponta para um produto
    apagado seria um pedido pela metade, e o arquivo enviado pelo cliente
    precisa continuar existindo enquanto houver pedido usando ele.
    """

    order = models.ForeignKey(
        Order, verbose_name="pedido", related_name="items", on_delete=models.CASCADE
    )

    # Referência ao catálogo de hoje (para o admin abrir a ficha).
    product = models.ForeignKey(
        "catalog.Product",
        verbose_name="produto",
        related_name="order_items",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        verbose_name="opção",
        related_name="order_items",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )

    # Snapshot — é daqui que toda tela lê.
    product_name = models.CharField("produto (snapshot)", max_length=200)
    sku = models.CharField("SKU (snapshot)", max_length=64, blank=True)
    variant_label = models.CharField("opção (snapshot)", max_length=160, blank=True)
    color_name = models.CharField("cor", max_length=60, blank=True)
    size_name = models.CharField("tamanho", max_length=60, blank=True)
    material_name = models.CharField("material", max_length=80, blank=True)

    quantity = models.PositiveIntegerField("quantidade", default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField("preço unitário", max_digits=10, decimal_places=2, default=ZERO)
    total = models.DecimalField("total da linha", max_digits=10, decimal_places=2, default=ZERO)

    unit_weight_grams = models.PositiveIntegerField("peso unitário (g)", default=0)
    fulfillment_type = models.CharField(
        "tipo de atendimento", max_length=20, choices=FulfillmentType.choices,
        default=FulfillmentType.STOCK,
    )
    production_days = models.PositiveIntegerField(
        "prazo de produção (dias úteis)",
        default=0,
        help_text="Snapshot: o prazo que valia quando o pedido foi feito.",
    )

    # Personalização — mesmo vocabulário do carrinho (none/photo/text).
    personalization_type = models.CharField("tipo de personalização", max_length=20, blank=True)
    personalization_text = models.TextField("texto", blank=True)
    personalization_notes = models.TextField("observações", blank=True)
    personalization_upload = models.ForeignKey(
        "cart.CustomizationUpload",
        verbose_name="arquivo enviado",
        related_name="order_items",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )

    class Meta:
        verbose_name = "item do pedido"
        verbose_name_plural = "itens do pedido"
        ordering = ("order", "pk")

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product_name}"

    @property
    def has_personalization(self) -> bool:
        return bool(self.personalization_type and self.personalization_type != "none")

    @property
    def description(self) -> str:
        """Nome + opção, como aparece na fatura e no e-mail."""
        if self.variant_label:
            return f"{self.product_name} — {self.variant_label}"
        return self.product_name

    @property
    def total_weight_grams(self) -> int:
        return self.unit_weight_grams * self.quantity


# ---------------------------------------------------------------------------
# Histórico e notas
# ---------------------------------------------------------------------------


class OrderStatusHistory(models.Model):
    """Linha do tempo do pedido.

    Serve ao cliente (o que aconteceu com a minha compra) e à loja (quem mexeu
    e quando). ``is_customer_visible`` separa os dois: uma decisão interna
    entra no histórico sem aparecer na conta do cliente.
    """

    order = models.ForeignKey(
        Order, verbose_name="pedido", related_name="history", on_delete=models.CASCADE
    )
    event = models.CharField("evento", max_length=32, choices=OrderEvent.choices)
    message = models.CharField("detalhe", max_length=300, blank=True)
    created_at = models.DateTimeField("quando", auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="por",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Vazio quando foi o sistema (webhook, rotina).",
    )
    is_customer_visible = models.BooleanField("visível para o cliente", default=True)

    class Meta:
        verbose_name = "evento do pedido"
        verbose_name_plural = "histórico do pedido"
        ordering = ("created_at", "pk")

    def __str__(self) -> str:
        return f"{self.order.number} · {self.get_event_display()}"


class OrderNote(TimeStampedModel):
    """Nota interna. **Nunca** aparece para o cliente.

    Não é o histórico: histórico é o que aconteceu, nota é o que a equipe
    precisa lembrar ("cliente confirmou que quer a fonte maior").
    """

    order = models.ForeignKey(
        Order, verbose_name="pedido", related_name="notes", on_delete=models.CASCADE
    )
    body = models.TextField("nota")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="autor",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = "nota interna"
        verbose_name_plural = "notas internas"
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return f"{self.order.number}: {self.body[:40]}"


# ---------------------------------------------------------------------------
# Pagamento
# ---------------------------------------------------------------------------


class PaymentState(models.TextChoices):
    CREATED = "created", _("Criado")
    PROCESSING = "processing", _("Processando")
    SUCCEEDED = "succeeded", _("Confirmado")
    FAILED = "failed", _("Recusado")
    CANCELLED = "cancelled", _("Cancelado")
    REFUNDED = "refunded", _("Reembolsado")


class Payment(TimeStampedModel):
    """A tentativa de pagamento de um pedido.

    Uma linha por tentativa: cartão recusado e nova tentativa são dois
    registros, e o pedido continua um só.

    **Nada de dados de cartão aqui.** Número, validade e CVV nunca chegam ao
    nosso servidor — o cliente os digita na página da Stripe. O que guardamos
    são identificadores opacos (``pi_...``, ``cs_...``) e o resultado.
    """

    order = models.ForeignKey(
        Order, verbose_name="pedido", related_name="payments", on_delete=models.CASCADE
    )
    provider = models.CharField("provedor", max_length=20, default="stripe")
    provider_session_id = models.CharField(
        "sessão do provedor", max_length=255, blank=True, db_index=True,
        help_text="Stripe Checkout Session (cs_...).",
    )
    provider_payment_id = models.CharField(
        "pagamento do provedor", max_length=255, blank=True, db_index=True,
        help_text="Stripe PaymentIntent (pi_...).",
    )
    amount = models.DecimalField("valor", max_digits=10, decimal_places=2, default=ZERO)
    currency = models.CharField("moeda", max_length=3, default="EUR")
    status = models.CharField(
        "situação", max_length=20, choices=PaymentState.choices, default=PaymentState.CREATED
    )
    method_label = models.CharField(
        "meio de pagamento", max_length=60, blank=True, help_text="Ex.: Cartão · Visa ····4242."
    )
    failure_message = models.CharField("motivo da recusa", max_length=300, blank=True)
    paid_at = models.DateTimeField("pago em", null=True, blank=True)

    class Meta:
        verbose_name = "pagamento"
        verbose_name_plural = "pagamentos"
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return f"{self.order.number} · {self.get_status_display()}"


class BankTransferSettings(TimeStampedModel):
    """Os dados bancários que a equipe manda ao cliente.

    Uma linha só (o padrão de `EmailSettings` e `FooterSettings`): a loja tem
    uma conta, não uma tabela de contas.

    Hoje eles não aparecem sozinhos para o cliente — quem envia é uma pessoa,
    depois de ver o pedido. O cadastro existe para que essa pessoa não precise
    procurar o IBAN num papel, e para que o dia de mostrá-los na tela seja um
    template a mais, não um model novo.

    **Nada de dados bancários do cliente aqui.** O que a loja recebe é uma
    transferência; o IBAN de quem paga nunca chega a este servidor.
    """

    beneficiary = models.CharField("titular da conta", max_length=140, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC/SWIFT", max_length=15, blank=True)
    instructions = models.TextField(
        "instruções de pagamento",
        blank=True,
        help_text="Texto que a equipe copia no e-mail. Ex.: use o número do pedido na comunicação.",
    )

    class Meta:
        verbose_name = "dados para transferência"
        verbose_name_plural = "PAGAMENTO — dados para transferência"

    def __str__(self) -> str:
        return self.beneficiary or "dados para transferência"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "BankTransferSettings":
        return cls.objects.get_or_create(pk=1)[0]

    @classmethod
    def current(cls) -> "BankTransferSettings | None":
        return cls.objects.filter(pk=1).first()

    @property
    def is_complete(self) -> bool:
        """Tem o mínimo para alguém conseguir transferir?"""
        return bool(self.beneficiary and self.iban)


class WebhookEvent(models.Model):
    """Um evento já processado do provedor de pagamento.

    É o que torna o webhook idempotente: a Stripe **reenvia** eventos quando
    não recebe 200 rápido o bastante, e receber o mesmo ``evt_...`` duas vezes
    não pode baixar o estoque duas vezes nem mandar dois e-mails.

    A unicidade é do banco (``unique``), não de um ``if`` — duas entregas
    simultâneas do mesmo evento chegam em processos diferentes.
    """

    provider = models.CharField("provedor", max_length=20, default="stripe")
    event_id = models.CharField("id do evento", max_length=255)
    event_type = models.CharField("tipo", max_length=80, blank=True)
    order = models.ForeignKey(
        Order,
        verbose_name="pedido",
        related_name="webhook_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    received_at = models.DateTimeField("recebido em", auto_now_add=True)
    processed_at = models.DateTimeField("processado em", null=True, blank=True)

    class Meta:
        verbose_name = "evento do provedor"
        verbose_name_plural = "eventos do provedor"
        ordering = ("-received_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "event_id"), name="webhook_event_unique_per_provider"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id}"
