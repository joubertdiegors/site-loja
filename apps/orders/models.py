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

import uuid

from django.conf import settings
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, TranslatableMixin, TranslationBase

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------


class OrderStatus(models.TextChoices):
    """Onde o **pedido** está — e nada mais que isso.

    Este campo é **derivado**: quem o escreve é `services.recompute_status`, a
    partir dos outros três. Ele existe como coluna porque a listagem filtra por
    ele e há um índice em `(status, payment_status)`; não existe como decisão.

    Não há "reembolsado" aqui. Reembolso é estado do dinheiro (`refund_status`),
    e ter os dois obrigava a aceitar que discordassem — um pedido "reembolsado"
    que nunca foi cancelado era representável, e acontecia.
    """

    PENDING = "pending", _("Aguardando pagamento")
    CONFIRMED = "confirmed", _("Confirmado")
    COMPLETED = "completed", _("Concluído")
    CANCELLED = "cancelled", _("Cancelado")


class PaymentStatus(models.TextChoices):
    """O dinheiro entrou?

    `NOT_CHARGED` é a resposta para o pedido cancelado antes de pagar: não é
    "pendente" — ninguém está esperando esse dinheiro, e deixá-lo pendente
    fazia o cliente ver "Aguardando pagamento" ao lado de "Cancelado" para
    sempre.

    Os dois valores de reembolso saíram daqui: viraram `RefundStatus`. Um
    pedido reembolsado **continua tendo sido pago** — apagar isso perdia a
    informação de que houve cobrança.
    """

    PENDING = "pending", _("Pendente")
    PAID = "paid", _("Pago")
    FAILED = "failed", _("Recusado")
    NOT_CHARGED = "not_charged", _("Não cobrado")


class RefundStatus(models.TextChoices):
    """O caminho do dinheiro de volta, separado do caminho da ida.

    Solicitar cancelamento não reembolsa nada; aprovar o cancelamento também
    não. O que a aprovação faz é abrir o processo (`PENDING`). Quem devolve o
    dinheiro é uma pessoa, no banco, e o sistema **registra** o que ela fez.
    """

    NONE = "none", _("Sem reembolso")
    PENDING = "pending", _("Reembolso pendente")
    PARTIAL = "partially_refunded", _("Parcialmente reembolsado")
    DONE = "refunded", _("Reembolsado")


class FulfillmentStatus(models.TextChoices):
    """Onde a peça está.

    `HALTED` é terminal e não se digita: só `services.approve_cancellation` o
    escreve. É o que tira a peça da fila da oficina quando um cancelamento é
    aprovado — antes dele, um pedido cancelado continuava "em produção" e
    alguém imprimia.
    """

    NOT_STARTED = "not_started", _("Não iniciado")
    IN_PRODUCTION = "in_production", _("Em produção")
    READY = "ready", _("Pronto")
    SHIPPED = "shipped", _("Enviado")
    DELIVERED = "delivered", _("Entregue")
    HALTED = "halted", _("Interrompido")


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


class StockDecision(models.TextChoices):
    """O que se decidiu fazer com o estoque de um pedido cancelado.

    Existe como campo, e não só como evento no histórico, porque é uma decisão
    que se **consulta**: "quais cancelamentos ainda estão sem decisão de
    estoque?" é a pergunta de quem fecha o mês.
    """

    NONE = "", _("Sem decisão")
    RETURNED = "returned", _("Devolvido ao estoque")
    KEPT = "kept", _("Não devolvido (peça perdida)")
    ON_REFUND = "on_refund", _("Devolver quando o reembolso for confirmado")


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
    STOCK_RETURNED = "stock_returned", _("Estoque devolvido")
    STOCK_KEPT = "stock_kept", _("Estoque não devolvido")
    PRODUCTION_HALTED = "production_halted", _("Produção interrompida")
    REFUND_PENDING = "refund_pending", _("Reembolso pendente")
    REFUND_PARTIAL = "refund_partial", _("Reembolso parcial")
    EMAIL_RESENT = "email_resent", _("E-mail reenviado")
    TRANSFER_DETAILS_SENT = "transfer_details_sent", _("Dados bancários enviados")
    PAYMENT_PROOF_RECEIVED = "payment_proof_received", _("Comprovante recebido")
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
    cancellation_decision_note = models.TextField(
        "resposta ao cliente",
        blank=True,
        help_text=(
            "Vai no e-mail de recusa, com estas palavras. Não é nota interna: "
            "para isso existem as notas do pedido."
        ),
    )
    cancellation_decided_at = models.DateTimeField("decidido em", null=True, blank=True)
    cancellation_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="decidido por",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    cancellation_auto = models.BooleanField(
        "decidido pela regra",
        default=False,
        help_text="Aprovado sem análise humana, porque a regra do momento permitia.",
    )

    # -- reembolso ---------------------------------------------------------
    #
    # Separado do pagamento de propósito: um pedido reembolsado continua tendo
    # sido pago. Quem executa a devolução é uma pessoa, no banco; o que está
    # aqui é o registro do que ela fez — e é por ele que se sabe o que ainda
    # falta devolver.
    refund_status = models.CharField(
        "reembolso", max_length=20, choices=RefundStatus.choices, default=RefundStatus.NONE
    )
    refunded_amount = models.DecimalField(
        "valor reembolsado", max_digits=10, decimal_places=2, default=ZERO
    )
    refunded_at = models.DateTimeField(
        "reembolsado em", null=True, blank=True,
        help_text="Quando o reembolso foi concluído (não quando foi aberto).",
    )
    refund_reference = models.CharField(
        "referência do reembolso", max_length=140, blank=True,
        help_text="Identificador da transferência de volta, ou do provedor.",
    )
    refunded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="reembolsado por",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # -- estoque do cancelamento -------------------------------------------
    stock_return_decision = models.CharField(
        "decisão sobre o estoque", max_length=20, choices=StockDecision.choices, blank=True,
        default=StockDecision.NONE,
    )
    stock_returned_at = models.DateTimeField(
        "estoque devolvido em", null=True, blank=True,
        help_text="Marca de idempotência: com ela preenchida, o saldo não volta duas vezes.",
    )

    # -- pagamento ---------------------------------------------------------
    #
    # Como o cliente escolheu pagar. Fica no pedido, e não só no `Payment`,
    # porque é a pergunta que a operação faz o tempo todo ("quais pedidos por
    # transferência ainda não foram pagos?") e porque um pedido pode ter mais de
    # uma tentativa de pagamento, e nenhuma delas é a escolha.
    payment_method = models.CharField(
        "forma de pagamento",
        max_length=20,
        blank=True,
        db_index=True,
        help_text="Escolhida pelo cliente no checkout. Ex.: transfer, card.",
    )

    # A chave da finalização que criou este pedido — uma por tela de checkout
    # aberta, não uma por clique.
    #
    # É o que torna "concluir pedido" idempotente. Dois cliques no mesmo botão
    # (ou a mesma requisição repetida pelo navegador, ou um F5 no POST) chegam
    # com a **mesma** chave: o segundo encontra o pedido do primeiro e vai para
    # ele, em vez de criar um pedido gêmeo com outro e-mail, outra tentativa de
    # pagamento e outra baixa de estoque.
    #
    # A garantia é do banco, e não de um `if`: dois cliques simultâneos são duas
    # requisições em processos diferentes, e nenhum `filter().exists()` em
    # Python enxerga a linha que o outro ainda não gravou. A constraint é
    # parcial porque pedido antigo — e pedido nascido fora do checkout — tem a
    # chave vazia, e vazio se repete à vontade.
    checkout_token = models.CharField(
        "chave da finalização",
        max_length=64,
        blank=True,
        db_index=True,
        editable=False,
    )

    # Os dados da conta que o cliente recebeu — **copiados**, como o preço, o
    # endereço e a alíquota. Trocar a conta padrão amanhã não pode reescrever o
    # IBAN que um cliente recebeu por e-mail há três meses: ele transferiu para
    # aquela conta, e é contra aquela conta que a equipe confere.
    #
    # A chave estrangeira fica ao lado da cópia pelo mesmo motivo de
    # `shipping_method` + `shipping_method_label`: ela serve para o Admin abrir
    # a ficha, e a cópia é o que vale.
    bank_account = models.ForeignKey(
        "orders.BankAccount",
        verbose_name="conta bancária",
        related_name="orders",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    bank_beneficiary = models.CharField("titular da conta", max_length=140, blank=True)
    bank_iban = models.CharField("IBAN", max_length=40, blank=True)
    bank_bic = models.CharField("BIC/SWIFT", max_length=15, blank=True)
    bank_instructions = models.TextField("instruções de pagamento", blank=True)

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
    delivered_at = models.DateTimeField(
        "entregue em", null=True, blank=True,
        help_text="É desta data que corre o prazo legal de arrependimento.",
    )
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
    # Transferência: quando alguém da equipe mandou os dados bancários ao
    # cliente. Fica em branco até alguém clicar — nada aqui é automático, e é
    # essa marca que a lista de pedidos mostra para a equipe saber o que ainda
    # falta enviar.
    transfer_details_sent_at = models.DateTimeField(
        "dados bancários enviados em", null=True, blank=True
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
        constraints = [
            models.UniqueConstraint(
                fields=["checkout_token"],
                condition=~models.Q(checkout_token=""),
                name="order_unique_checkout_token",
            ),
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
    def derived_status(self) -> str:
        """O `status` calculado a partir dos fatos. Ver `services.recompute_status`.

        A ordem das perguntas é a ordem da importância: um pedido cancelado é
        cancelado mesmo que tenha sido entregue antes; um entregue está
        concluído mesmo que o dinheiro ainda esteja voltando.
        """
        if self.cancelled_at is not None:
            return OrderStatus.CANCELLED
        if self.fulfillment_status == FulfillmentStatus.DELIVERED:
            return OrderStatus.COMPLETED
        if self.payment_status == PaymentStatus.PAID:
            return OrderStatus.CONFIRMED
        return OrderStatus.PENDING

    @property
    def refund_due(self):
        """Quanto ainda falta devolver. Zero quando não há nada a devolver."""
        if self.payment_status != PaymentStatus.PAID:
            return ZERO
        return max(self.total - self.refunded_amount, ZERO)

    @property
    def has_personalized_items(self) -> bool:
        """Há item feito **sob especificação do cliente**?

        Importa por causa do artigo 16(c) da diretiva europeia 2011/83/UE
        (VI.53 do Código de Direito Económico belga): o direito de
        arrependimento não se aplica a bens confeccionados segundo as
        especificações do consumidor ou claramente personalizados.

        Repare no que **não** entra: `MADE_TO_ORDER` é um produto de catálogo
        que só é impresso depois da compra — não foi o cliente que o
        especificou, e a exceção não o alcança. Só conta o que o cliente
        personalizou.

        E isto não bloqueia nada sozinho: é um sinalizador que manda o pedido
        para análise humana (`services.cancellation_plan`). A exceção legal tem
        condições — informação prévia, personalização real — que um `if` não
        sabe conferir.
        """
        return any(
            item.fulfillment_type == FulfillmentType.PERSONALIZED or item.has_personalization
            for item in self.items.all()
        )

    @property
    def withdrawal_deadline(self):
        """Até quando corre o prazo legal de arrependimento. `None` antes da entrega.

        Catorze dias corridos a partir do **recebimento** — não da compra, não
        do envio. É o prazo da diretiva 2011/83/UE, e é configurável no Admin
        só para o caso de a loja querer oferecer mais do que a lei exige;
        oferecer menos não é uma opção.
        """
        if self.delivered_at is None:
            return None
        return self.delivered_at + timedelta(days=CancellationSettings.withdrawal_days_value())

    @property
    def is_within_withdrawal_window(self) -> bool:
        """O prazo de arrependimento ainda está aberto?

        Antes da entrega ele nem começou a correr — e o cliente pode desistir
        desde a compra. Depois dela, conta-se do recebimento.
        """
        if self.delivered_at is None:
            return True
        return timezone.now() <= self.withdrawal_deadline

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
        """O cliente pode pedir cancelamento ou devolução?

        Mudou de forma: antes o corte era a expedição — quem tinha recebido a
        encomenda não tinha por onde pedir. Agora o corte é o **prazo legal**,
        que só começa a correr na entrega. Um pedido entregue há três dias pode
        ser devolvido; um entregue há dois meses, não.

        Pedir não é ter direito garantido: um pedido personalizado, ou fora de
        um caso previsto, vai para análise humana. Quem decide isso é
        `services.cancellation_plan`, e a recusa é uma decisão registrada — não
        um botão que some.
        """
        if self.status == OrderStatus.CANCELLED:
            return False
        if self.cancellation_status in {CancellationStatus.REQUESTED, CancellationStatus.APPROVED}:
            return False
        if self.fulfillment_status == FulfillmentStatus.HALTED:
            return False
        return self.is_within_withdrawal_window

    @property
    def bank_details(self):
        """A conta **do pedido**, montada a partir da cópia. ``None`` sem cópia.

        Devolve um ``BankAccount`` que nunca foi gravado. Não é truque: é o
        objeto certo, com o `is_complete` e o `masked_iban` que o e-mail e o
        Admin já sabem ler — só que preenchido com o que o pedido guardou, e não
        com o que a tabela diz hoje. Uma segunda classe com os mesmos quatro
        campos e as mesmas duas propriedades seria a mesma coisa com outro nome.
        """
        if not (self.bank_beneficiary or self.bank_iban):
            return None
        return BankAccount(
            beneficiary=self.bank_beneficiary,
            iban=self.bank_iban,
            bic=self.bank_bic,
            instructions=self.bank_instructions,
        )

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
    # 500 desde a etapa 3B: com as opções adicionais o rótulo cresce («Preto ·
    # 25 cm · PLA · Parede · Fosco»), e um texto maior que o campo falha no
    # PostgreSQL em vez de ser cortado. Nada é cortado ao gravar.
    variant_label = models.CharField("opção (snapshot)", max_length=500, blank=True)
    color_name = models.CharField("cor", max_length=60, blank=True)
    size_name = models.CharField("tamanho", max_length=60, blank=True)
    material_name = models.CharField("material", max_length=80, blank=True)
    # Etapa 2B: a descrição visual e a composição do PRODUTO no momento da
    # compra («Preto + Branco», «PLA 80% + PETG 20%»). Os três acima
    # continuam sendo os eixos da VARIANTE comprada; estes dois nascem vazios
    # nos pedidos anteriores e nunca são preenchidos retroativamente.
    colors_snapshot = models.CharField("cores (snapshot)", max_length=255, blank=True, default="")
    materials_snapshot = models.CharField(
        "composição de materiais (snapshot)", max_length=255, blank=True, default=""
    )
    # Etapa 3B: as opções adicionais da VARIANTE comprada, no idioma do cliente
    # («Instalação: Parede · Acabamento: Fosco»). Texto sem limite: nunca é
    # cortado. Pedidos anteriores ficam vazios e nunca são preenchidos.
    options_snapshot = models.TextField("opções adicionais (snapshot)", blank=True, default="")

    quantity = models.PositiveIntegerField("quantidade", default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField("preço unitário", max_digits=10, decimal_places=2, default=ZERO)
    total = models.DecimalField("total da linha", max_digits=10, decimal_places=2, default=ZERO)

    unit_weight_grams = models.PositiveIntegerField("peso unitário (g)", default=0)
    stock_taken = models.PositiveIntegerField(
        "unidades tiradas do saldo",
        default=0,
        help_text=(
            "Quantas unidades a confirmação do pagamento realmente tirou do "
            "estoque. Pode ser menor que a quantidade — houve falta — ou zero "
            "(sob encomenda, ou variante que aceita encomenda sem saldo)."
        ),
    )
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
    def options_lines(self) -> list[str]:
        """O snapshot das opções, uma por linha: ``["Instalação: Parede", "Acabamento: Fosco"]``.

        Etapa 3E. Lê **só** o texto gravado na compra — no idioma em que o
        cliente comprou — e nunca o produto, a opção ou o valor de hoje. Vazio
        nos itens sem opção e nos pedidos anteriores às opções adicionais, que
        nunca são preenchidos retroativamente.
        """
        from apps.catalog.models import OPTIONS_TEXT_SEPARATOR

        return [
            parte.strip()
            for parte in (self.options_snapshot or "").split(OPTIONS_TEXT_SEPARATOR)
            if parte.strip()
        ]

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

    @property
    def customer_message(self) -> str:
        """A frase que o cliente lê — ou ``""`` quando não há o que dizer a ele.

        Vem do **evento**, nunca de ``message``. Aquele campo é a anotação da
        equipe: nele cabe o IBAN mascarado da conta usada, o nome do arquivo que
        o cliente mandou, o motivo interno de uma recusa. São coisas úteis para
        quem opera e ruído — às vezes constrangedor — para quem comprou.

        O rastreio é a exceção que confirma a regra: ali o detalhe **é** a
        informação que o cliente quer, então ele entra na frase.
        """
        if self.event == OrderEvent.CANCELLATION_APPROVED:
            # A política é configurável e pode mudar; a frase é montada na hora
            # da leitura, e não gravada no evento. Um pedido cancelado antes de
            # a loja definir o prazo passa a mostrá-lo assim que ele existir.
            #
            # O pedido vai junto: é ele que diz se houve cobrança — e, sem
            # cobrança, a frase não fala em reembolso.
            return CancellationSettings.approved_message(self.order)

        if self.event == OrderEvent.CANCELLATION_REFUSED:
            # A resposta escrita pela loja **é** a mensagem. Sem ela, uma frase
            # neutra: melhor curta do que inventada em nome de quem atende.
            #
            # Vem de `self.message` — a resposta gravada **neste** evento — e
            # não do campo do pedido: lendo o campo, uma recusa antiga passava a
            # exibir o texto de uma decisão posterior, e a linha do tempo se
            # reescrevia sozinha.
            resposta = (self.message or "").strip()
            if resposta:
                return resposta
            return _(
                "Não foi possível cancelar a encomenda. Entrámos em contacto por e-mail."
            )

        if self.event == OrderEvent.SHIPPED and self.message.strip():
            return _("A sua encomenda foi enviada. Código de rastreio: %(code)s") % {
                "code": self.message.strip()
            }
        return CUSTOMER_EVENT_MESSAGES.get(self.event, "")


#: O que cada evento diz **ao cliente**.
#:
#: A mensagem interna (`OrderStatusHistory.message`) é escrita para a equipe e
#: carrega o que a equipe precisa: qual conta foi usada, o nome do arquivo que
#: chegou, o motivo de uma recusa. Nada disso é comunicação — "Dados bancários
#: enviados ao cliente (conta: Joubert Diego · LT14 ···· 2545)" é uma anotação
#: de operação que vazou para a tela de quem comprou.
#:
#: Aqui mora a outra metade: uma frase por evento, escrita para quem comprou e
#: traduzida no momento em que a página é montada — e não no momento em que o
#: evento aconteceu, que é o que faz o mesmo pedido ser lido em francês por
#: quem trocou de idioma depois.
#:
#: Evento que não está neste mapa não aparece para o cliente. É de propósito:
#: acrescentar um evento interno não cria, sozinho, uma linha na tela dele.
CUSTOMER_EVENT_MESSAGES = {
    OrderEvent.CREATED: _("Recebemos a sua encomenda."),
    OrderEvent.TRANSFER_DETAILS_SENT: _(
        "Enviámos para o seu e-mail os dados para pagamento por transferência bancária."
    ),
    OrderEvent.PAYMENT_PROOF_RECEIVED: _(
        "Recebemos o seu comprovativo de pagamento e vamos analisá-lo."
    ),
    OrderEvent.PAID: _("O seu pagamento foi confirmado."),
    OrderEvent.CONFIRMED: _("A sua encomenda foi confirmada."),
    OrderEvent.IN_PRODUCTION: _("Começámos a preparar a sua encomenda."),
    OrderEvent.READY: _("A sua encomenda está pronta para envio."),
    OrderEvent.SHIPPED: _("A sua encomenda foi enviada."),
    OrderEvent.DELIVERED: _("A sua encomenda foi entregue."),
    OrderEvent.COMPLETED: _("Encomenda concluída. Obrigado!"),
    OrderEvent.CANCELLATION_REQUESTED: _(
        "Recebemos o seu pedido de cancelamento e vamos analisá-lo. "
        "Assim que houver uma decisão, avisamos por e-mail."
    ),
    OrderEvent.REFUND_PENDING: _(
        "O reembolso da sua encomenda foi iniciado."
    ),
    OrderEvent.REFUND_PARTIAL: _(
        "Parte do valor da sua encomenda foi reembolsada."
    ),
    OrderEvent.CANCELLATION_APPROVED: _("O seu pedido de cancelamento foi aprovado."),
    OrderEvent.CANCELLATION_REFUSED: _(
        "Não foi possível cancelar a encomenda. Entrámos em contacto por e-mail."
    ),
    OrderEvent.CANCELLED: _("A sua encomenda foi cancelada."),
    OrderEvent.REFUNDED: _("O valor da sua encomenda foi reembolsado."),
    OrderEvent.PAYMENT_FAILED: _("Não conseguimos concluir o pagamento."),
}


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


class BankAccountQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def usable(self):
        """As que dá para mandar: ativas e com o mínimo preenchido."""
        return self.active().exclude(beneficiary="").exclude(iban="")

    def default_for_orders(self):
        """A conta que o checkout usa sozinho — ou ``None``.

        Três condições, e todas as três importam: marcada como padrão, **ativa**
        e completa. Uma conta padrão que foi desativada não volta a ser usada
        por estar marcada: desativar é justamente dizer "não use mais esta".

        Devolver ``None`` é uma resposta, não uma falha. Quem chama decide o que
        fazer — e no checkout a decisão é recusar a compra com uma mensagem,
        nunca escolher outra conta por conta própria. Mandar o cliente
        transferir para uma conta que ninguém escolheu é pior que não vender.
        """
        return self.usable().filter(is_default=True).first()


class BankAccount(TimeStampedModel):
    """Uma conta para onde o cliente transfere.

    Era um singleton (`save()` fixando `pk=1`, o padrão de `EmailSettings` e
    `FooterSettings`) enquanto os dados só apareciam num e-mail interno, para a
    equipe copiar à mão. A partir do momento em que quem atende **escolhe** a
    conta antes de mandar, uma linha só deixa de responder à pergunta: não há
    escolha entre uma coisa.

    Nada aqui vai sozinho para o cliente. Quem manda é uma pessoa, no Admin,
    depois de olhar o pedido — ver `OrderAdmin.action_send_transfer_details`.

    **Nada de dados bancários do cliente aqui.** O que a loja recebe é uma
    transferência; o IBAN de quem paga nunca chega a este servidor.
    """

    label = models.CharField(
        "nome da conta",
        max_length=80,
        blank=True,
        help_text="Como a conta aparece na hora de escolher. Ex.: Principal, Belfius PME.",
    )
    beneficiary = models.CharField("titular da conta", max_length=140, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC/SWIFT", max_length=15, blank=True)
    instructions = models.TextField(
        "instruções de pagamento",
        blank=True,
        help_text="Vai no e-mail do cliente. Ex.: use o número do pedido na comunicação.",
    )
    is_default = models.BooleanField(
        "conta padrão",
        default=False,
        help_text=(
            "A conta que os pedidos por transferência usam automaticamente. "
            "Só uma pode ser padrão — marcar outra desmarca esta."
        ),
    )
    is_active = models.BooleanField(
        "ativa", default=True, help_text="Desmarque para tirar da lista sem apagar o histórico."
    )
    sort_order = models.PositiveIntegerField("ordem", default=0)

    objects = BankAccountQuerySet.as_manager()

    class Meta:
        verbose_name = "conta bancária"
        verbose_name_plural = "PAGAMENTO — contas bancárias"
        ordering = ("sort_order", "label", "pk")
        constraints = [
            # Índice parcial: `is_default` só precisa ser único **entre as
            # linhas em que ele é verdadeiro**, o que é a forma de dizer "no
            # máximo uma padrão" que o banco entende. O `save()` abaixo desmarca
            # a anterior para que marcar a segunda seja um gesto normal e não um
            # erro; a constraint é a rede para o que não passa pelo `save()` —
            # `update()` em massa, SQL cru, uma migração de dados distraída.
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="bank_account_single_default",
            ),
        ]

    def __str__(self) -> str:
        nome = self.label or self.beneficiary or "conta bancária"
        return f"{nome} · {self.masked_iban}" if self.iban else nome

    @property
    def is_complete(self) -> bool:
        """Tem o mínimo para alguém conseguir transferir?"""
        return bool(self.beneficiary and self.iban)

    @property
    def masked_iban(self) -> str:
        """O IBAN sem o miolo: ``BE68 ···· 6879``.

        Serve para a listagem do Admin e para o registro no histórico do
        pedido — os dois lugares em que se quer **identificar** a conta, não
        usá-la. O IBAN inteiro só aparece onde ele é a informação: na tela de
        cadastro e no e-mail que o cliente pediu.
        """
        limpo = (self.iban or "").replace(" ", "")
        if len(limpo) <= 8:
            return limpo
        return f"{limpo[:4]} ···· {limpo[-4:]}"

    @property
    def is_usable(self) -> bool:
        """Dá para usar esta conta num pedido agora?"""
        return bool(self.is_active and self.is_complete)

    def save(self, *args, **kwargs):
        """Marcar esta como padrão desmarca a anterior, na mesma transação.

        Sem isto, a segunda conta marcada esbarraria na constraint e o
        administrador veria um erro de banco por ter feito exatamente o que a
        tela oferece. A ordem importa: desmarcar primeiro, gravar depois — o
        índice parcial não tolera as duas verdadeiras nem por um instante.
        """
        with transaction.atomic():
            if self.is_default:
                outras = BankAccount.objects.filter(is_default=True)
                if self.pk:
                    outras = outras.exclude(pk=self.pk)
                outras.update(is_default=False)
            super().save(*args, **kwargs)


def payment_proof_upload_to(instance, filename: str) -> str:
    """Nome gerado por nós, nunca o do cliente.

    Mesmo desenho de `customization_upload_to`: o nome original fica em
    `original_name` para exibição, e o arquivo em disco recebe um nome
    aleatório com a extensão que o **conteúdo** provou ter.

    A pasta importa tanto quanto o nome. Só `products/` e `banners/` são
    servidas publicamente (ver `config/urls.py`); tudo o mais só sai pela view
    que confere quem está pedindo.
    """
    extension = (instance.extension or "bin").lower()
    return f"payment-proofs/{uuid.uuid4().hex}.{extension}"


class PaymentProof(TimeStampedModel):
    """O comprovante que o cliente envia depois de transferir.

    **Um por pedido**, garantido pelo banco. O cliente envia o comprovante uma
    vez; se mandou o arquivo errado, quem resolve é a equipe — apagar e
    substituir sozinho abriria a porta para trocar o documento depois de a
    equipe já ter olhado, e um comprovante que muda não é um comprovante.

    Continua sendo tabela própria, e não uma coluna no pedido, porque é o que
    guarda o **quando** e o **o quê** (nome original, tipo, tamanho) sem
    inchar um model que já tem quarenta campos — e porque o dia em que a loja
    aceitar pagamento em parcelas, a regra vira "um por parcela" mexendo numa
    constraint, não na forma dos dados.

    Também não é `CustomizationUpload`. Aquele é do carrinho: preso à sessão de
    quem monta o pedido e aos itens dele, e é a foto da peça que será impressa.
    Este chega depois do checkout, pertence ao pedido e é documento financeiro.
    O que os dois compartilham é a **proteção**, e essa mora em
    `apps.core.uploads`.

    Receber um comprovante **não** confirma pagamento. Quem confere se o
    dinheiro entrou é a equipe, no banco, e depois no Admin.
    """

    order = models.ForeignKey(
        Order, verbose_name="pedido", related_name="payment_proofs", on_delete=models.CASCADE
    )
    file = models.FileField("arquivo", upload_to=payment_proof_upload_to)
    original_name = models.CharField("nome original", max_length=200, blank=True)
    content_type = models.CharField("tipo detectado", max_length=60, blank=True)
    extension = models.CharField("extensão", max_length=10, blank=True)
    size_bytes = models.PositiveIntegerField("tamanho (bytes)", default=0)

    class Meta:
        verbose_name = "comprovante de pagamento"
        verbose_name_plural = "comprovantes de pagamento"
        ordering = ("-created_at", "-pk")
        constraints = [
            # A tela recusa o segundo envio antes de chegar aqui; esta linha é
            # para o que não passa pela tela — dois POST simultâneos, um script,
            # um comando. Sem ela, "somente um comprovante" seria uma promessa
            # da interface, não do sistema.
            models.UniqueConstraint(fields=["order"], name="payment_proof_one_per_order"),
        ]

    def __str__(self) -> str:
        return f"{self.order.number} · {self.original_name or self.file.name}"

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.0f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"


class CancellationSettings(TranslatableMixin, TimeStampedModel):
    """O que a loja diz ao cliente quando aprova um cancelamento.

    **Uma linha só** (`pk=1`), como `EmailSettings` e `FooterSettings`: não é
    uma lista de políticas, é *a* política.

    ## Por que o prazo é um campo, e não uma frase no código

    "O reembolso chega em até X dias" é a pergunta que o cliente faz, e a
    resposta depende de coisas que o código não sabe: o contrato do banco, o
    meio de pagamento, o volume da loja. Escrever um número no template seria
    inventar uma promessa em nome de quem atende — e mudá-la exigiria um
    deploy.

    Por isso o prazo mora aqui, em branco por padrão. **Sem prazo cadastrado, a
    mensagem simplesmente não fala em prazo**: ela diz que o reembolso será
    feito e que o tempo depende da instituição. Melhor uma frase incompleta e
    verdadeira do que um número inventado.

    ## Por que a nota extra é traduzível e o resto não

    O corpo da mensagem é o mesmo para todo mundo e vive no código, traduzido
    como o resto da interface — assim ele acompanha a loja nos quatro idiomas
    sem ninguém precisar reescrevê-lo quatro vezes. O que varia de caso a caso
    (uma condição da loja, uma instrução de contato) é a **nota**, e essa é por
    idioma: quem escreve em português não deve deixar o cliente francês sem
    resposta.
    """

    translatable_fields = ("extra_note",)

    refund_days_min = models.PositiveIntegerField(
        "prazo mínimo de reembolso (dias)",
        null=True,
        blank=True,
        help_text=(
            "Deixe em branco enquanto a loja não tiver um prazo definido: sem "
            "ele, a mensagem não promete prazo nenhum."
        ),
    )
    refund_days_max = models.PositiveIntegerField(
        "prazo máximo de reembolso (dias)",
        null=True,
        blank=True,
        help_text="Preencha os dois para a mensagem falar em faixa (ex.: 5 a 10 dias).",
    )
    withdrawal_days = models.PositiveIntegerField(
        "prazo de arrependimento (dias corridos)",
        default=14,
        help_text=(
            "Contados a partir da <b>entrega</b>. Catorze dias é o mínimo legal "
            "na Bélgica (diretiva 2011/83/UE). A loja pode oferecer mais; "
            "menos, não."
        ),
    )

    class Meta:
        verbose_name = "política de cancelamento"
        verbose_name_plural = "CANCELAMENTO — texto enviado ao cliente"

    def __str__(self) -> str:
        return "Política de cancelamento"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    #: O piso legal do prazo de arrependimento, em dias corridos a partir da
    #: entrega. Diretiva 2011/83/UE, artigo 9.º; na Bélgica, livro VI do Código
    #: de Direito Económico. Não é configuração: é o mínimo que a loja pode
    #: oferecer, e o Admin recusa qualquer valor abaixo dele.
    LEGAL_WITHDRAWAL_DAYS = 14

    def clean(self):
        super().clean()
        if (
            self.refund_days_min is not None
            and self.refund_days_max is not None
            and self.refund_days_max < self.refund_days_min
        ):
            raise ValidationError(
                {"refund_days_max": "O prazo máximo não pode ser menor que o mínimo."}
            )
        if self.withdrawal_days < self.LEGAL_WITHDRAWAL_DAYS:
            raise ValidationError(
                {
                    "withdrawal_days": (
                        f"O prazo legal de arrependimento é de "
                        f"{self.LEGAL_WITHDRAWAL_DAYS} dias corridos a partir da "
                        f"entrega. A loja pode oferecer mais, nunca menos."
                    )
                }
            )

    @classmethod
    def load(cls) -> "CancellationSettings":
        obj, _criado = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def current(cls) -> "CancellationSettings | None":
        return cls.objects.filter(pk=1).prefetch_related("translations").first()

    @classmethod
    def withdrawal_days_value(cls) -> int:
        """O prazo em vigor — o da loja, nunca abaixo do legal.

        O `max` não é desconfiança do formulário: a linha pode ter sido gravada
        por uma migration antiga, por um shell ou por uma carga de dados, e
        nenhum desses caminhos passa pelo `clean`. O prazo legal não pode
        depender de por onde o dado entrou.
        """
        config = cls.current()
        if config is None:
            return cls.LEGAL_WITHDRAWAL_DAYS
        return max(config.withdrawal_days, cls.LEGAL_WITHDRAWAL_DAYS)

    @property
    def extra_note(self) -> str:
        return self.tr("extra_note", default="")

    @property
    def refund_window(self) -> str:
        """O prazo, em texto — ou ``""`` quando não há prazo cadastrado.

        Sem preposição: o texto entra **dentro** de "O reembolso será feito em
        …", e um "de" aqui produzia "será feito em de 5 a 10 dias úteis".
        """
        if self.refund_days_min is None and self.refund_days_max is None:
            return ""
        # Um teto só — venha ele do mínimo ou do máximo — é a mesma frase, e
        # por isso o mesmo msgid: "até N dias úteis".
        if self.refund_days_min is None:
            return _("até %(days)s dias úteis") % {"days": self.refund_days_max}
        if self.refund_days_max is None or self.refund_days_max == self.refund_days_min:
            return _("até %(days)s dias úteis") % {"days": self.refund_days_min}
        return _("%(min)s a %(max)s dias úteis") % {
            "min": self.refund_days_min,
            "max": self.refund_days_max,
        }

    @classmethod
    def approved_message(cls, order=None) -> str:
        """A frase inteira que o cliente lê ao ter o cancelamento aprovado.

        Montada aqui, e num lugar só, porque ela sai por dois canais — o e-mail
        e o acompanhamento do pedido — e os dois têm de dizer exatamente a
        mesma coisa. Duas redações da mesma política é como um cliente descobre
        que a loja se contradiz.

        **O `order` decide se há reembolso a prometer.** Sem ele — ou com um
        pedido que nunca foi cobrado — a frase do reembolso não sai: prometer
        devolver dinheiro que nunca saiu da conta de ninguém é o tipo de erro
        que gera uma resposta irritada e uma ligação.

        Traduzida no momento da leitura: o pedido pode ser lido em francês
        semanas depois de ter sido cancelado.
        """
        config = cls.current()
        partes = [
            _("O seu pedido de cancelamento foi aprovado e a encomenda foi cancelada."),
        ]

        houve_cobranca = order is None or order.payment_status == PaymentStatus.PAID
        if not houve_cobranca:
            partes.append(
                _("Nada foi cobrado por esta encomenda, portanto não há reembolso a fazer.")
            )
            nota = config.extra_note.strip() if config else ""
            if nota:
                partes.append(nota)
            return " ".join(str(parte) for parte in partes)

        prazo = config.refund_window if config else ""
        if prazo:
            partes.append(
                _("O reembolso será feito em %(prazo)s.") % {"prazo": prazo}
            )
        else:
            partes.append(_("O reembolso será feito."))

        partes.append(
            _(
                "O tempo até o valor aparecer na sua conta depende do meio de "
                "pagamento e do seu banco."
            )
        )

        nota = config.extra_note.strip() if config else ""
        if nota:
            partes.append(nota)

        # `str()` em cada pedaço, e não `" ".join(partes)` direto: o `_` deste
        # módulo é `gettext_lazy`, e um proxy preguiçoso não é `str` — o join
        # levantaria `TypeError` justamente no e-mail de cancelamento. Resolver
        # aqui também é o que dá a tradução certa: a frase é montada dentro do
        # `translation.override` de quem envia.
        return " ".join(str(parte) for parte in partes)


class CancellationSettingsTranslation(TranslationBase):
    master = models.ForeignKey(
        CancellationSettings,
        verbose_name="política",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    extra_note = models.TextField(
        "observação extra",
        blank=True,
        help_text=(
            "Acrescentada ao fim da mensagem, neste idioma. Deixe em branco "
            "para o cliente receber só o texto padrão."
        ),
    )

    class Meta:
        verbose_name = "tradução da política"
        verbose_name_plural = "traduções da política"
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="cancellation_settings_unique_language"
            ),
        ]


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
