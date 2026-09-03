"""A regra de cancelamento, reembolso e estoque — cenário a cenário.

Cinco perguntas decidem tudo o que acontece quando alguém quer desistir de uma
compra: **houve cobrança? a peça já foi feita? já saiu? o prazo legal ainda
corre? o dinheiro já voltou?** Cada classe daqui responde uma parte, e as
respostas juntas são a política da loja.

O que estes testes protegem, acima de tudo, é que **um cancelamento nunca
aconteça pela metade**: aprovar sem parar a produção deixava a oficina
imprimindo uma peça já cancelada, e devolver estoque duas vezes cria unidades
que não existem na prateleira.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cart.cart import CartLine
from apps.catalog.models import ProductVariant
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services
from apps.orders.models import (
    CancellationSettings,
    CancellationStatus,
    FulfillmentStatus,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentStatus,
    RefundStatus,
    StockDecision,
)

EQUIPE = ["equipe@jdprint.test"]
ESTOQUE_INICIAL = 10


@override_settings(ORDER_ADMIN_EMAILS=EQUIPE)
class RegraBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.product = make_product(
            sku="REG-1", name="Vaso", price=Decimal("19.90"),
            stock_quantity=ESTOQUE_INICIAL,
        )
        self.variant = self.product.default_variant

        self.user = make_user(username="ana", email="ana@exemplo.test")
        self.customer = self.user.customer
        self.address = make_address(self.customer, self.country)
        mail.outbox = []

    # -- cenários ----------------------------------------------------------

    def novo_pedido(self, **extra):
        return services.create_order(
            customer=self.customer,
            lines=[
                CartLine(
                    key=f"{self.product.pk}:0:-",
                    product=self.product,
                    variant=self.variant,
                    quantity=1,
                )
            ],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
            **extra,
        )

    def pago(self, **extra):
        """Pago e confirmado — a produção continua parada."""
        order = self.novo_pedido(**extra)
        services.confirm_payment(order)
        order.refresh_from_db()
        return order

    def em_producao(self, **extra):
        order = self.pago(**extra)
        services.change_fulfillment_status(order, FulfillmentStatus.IN_PRODUCTION)
        order.refresh_from_db()
        return order

    def pronto(self, **extra):
        order = self.em_producao(**extra)
        services.change_fulfillment_status(order, FulfillmentStatus.READY)
        order.refresh_from_db()
        return order

    def enviado(self, **extra):
        order = self.pronto(**extra)
        services.mark_shipped(order, "BE123456789")
        order.refresh_from_db()
        return order

    def entregue(self, *, dias_atras: int = 0, **extra):
        order = self.enviado(**extra)
        services.change_fulfillment_status(order, FulfillmentStatus.DELIVERED)
        order.refresh_from_db()
        if dias_atras:
            order.delivered_at = timezone.now() - timedelta(days=dias_atras)
            order.save(update_fields=["delivered_at"])
            order.refresh_from_db()
        return order

    # -- leitura -----------------------------------------------------------

    def saldo(self) -> int:
        return ProductVariant.objects.get(pk=self.variant.pk).stock_quantity

    def eventos(self, order) -> list[str]:
        return list(order.history.values_list("event", flat=True))

    def para_cliente(self):
        return [m for m in mail.outbox if m.to == [self.user.email]]

    def para_equipe(self):
        return [m for m in mail.outbox if m.to == EQUIPE]


# ---------------------------------------------------------------------------
# (a) Cancelamento antes do pagamento
# ---------------------------------------------------------------------------


class SemPagamentoTests(RegraBase):
    """Nada foi cobrado. Não há o que analisar e não há o que devolver."""

    def setUp(self):
        super().setUp()
        self.order = self.novo_pedido()
        mail.outbox = []

    def test_the_customer_cancels_without_asking_anyone(self):
        services.request_cancellation(self.order, "Comprei errado", user=self.user)

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)

    def test_the_decision_is_marked_as_automatic(self):
        services.request_cancellation(self.order, "Comprei errado")

        self.order.refresh_from_db()
        self.assertTrue(self.order.cancellation_auto)
        self.assertIsNone(self.order.cancellation_decided_by)

    def test_the_payment_says_not_charged_and_not_pending(self):
        """"Pendente" faria o cliente ver "Aguardando pagamento" para sempre."""
        services.request_cancellation(self.order, "Comprei errado")

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.NOT_CHARGED)

    def test_there_is_no_refund_to_open(self):
        services.request_cancellation(self.order, "Comprei errado")

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.NONE)
        self.assertEqual(self.order.refund_due, Decimal("0.00"))

    def test_the_stock_never_moved_so_nothing_returns(self):
        services.request_cancellation(self.order, "Comprei errado")

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.stock_returned_at)

    def test_production_is_halted(self):
        services.request_cancellation(self.order, "Comprei errado")

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)

    def test_the_customer_is_told_and_nothing_is_promised(self):
        services.request_cancellation(self.order, "Comprei errado")

        corpo = self.para_cliente()[0].body
        self.assertIn("cancelamento foi aprovado", corpo)
        self.assertIn("Nada foi cobrado", corpo)
        self.assertNotIn("reembolso será feito", corpo)

    def test_no_refund_email_goes_out(self):
        services.request_cancellation(self.order, "Comprei errado")

        assuntos = " | ".join(m.subject for m in self.para_cliente())
        self.assertNotIn("Reembolso", assuntos)

    def test_the_team_is_not_asked_to_decide(self):
        """Não há decisão a tomar — avisar a equipe seria ruído."""
        services.request_cancellation(self.order, "Comprei errado")

        self.assertEqual(self.para_equipe(), [])

    def test_the_history_tells_the_whole_story(self):
        services.request_cancellation(self.order, "Comprei errado")

        eventos = self.eventos(self.order)
        self.assertIn(OrderEvent.CANCELLATION_REQUESTED, eventos)
        self.assertIn(OrderEvent.CANCELLATION_APPROVED, eventos)
        self.assertIn(OrderEvent.PRODUCTION_HALTED, eventos)
        self.assertIn(OrderEvent.CANCELLED, eventos)
        self.assertNotIn(OrderEvent.REFUND_PENDING, eventos)


# ---------------------------------------------------------------------------
# (b) Pago, antes de a produção começar
# ---------------------------------------------------------------------------


class PagoAntesDaProducaoTests(RegraBase):
    """A regra decide sozinha — e abre o reembolso."""

    def setUp(self):
        super().setUp()
        self.order = self.pago()
        mail.outbox = []

    def test_the_stock_left_the_shelf_at_payment(self):
        """Ponto de partida: a baixa acontece na confirmação do pagamento."""
        self.assertEqual(self.saldo(), ESTOQUE_INICIAL - 1)

    def test_it_is_approved_without_a_person(self):
        services.request_cancellation(self.order, "Mudei de ideia")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertTrue(self.order.cancellation_auto)

    def test_the_refund_is_opened_but_not_paid(self):
        services.request_cancellation(self.order, "Mudei de ideia")

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))
        self.assertEqual(self.order.refund_due, self.order.total)

    def test_the_payment_still_says_paid(self):
        """Reembolsar não apaga a cobrança: ela aconteceu."""
        services.request_cancellation(self.order, "Mudei de ideia")

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)

    def test_the_unit_goes_back_to_the_shelf(self):
        """Nada foi consumido — a unidade que saiu na cobrança volta."""
        services.request_cancellation(self.order, "Mudei de ideia")

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)
        self.order.refresh_from_db()
        self.assertEqual(self.order.stock_return_decision, StockDecision.RETURNED)

    def test_the_customer_gets_both_letters(self):
        services.request_cancellation(self.order, "Mudei de ideia")

        assuntos = " | ".join(m.subject for m in self.para_cliente())
        self.assertIn("Cancelamento do pedido", assuntos)
        self.assertIn("Reembolso do pedido", assuntos)

    def test_the_refund_letter_does_not_claim_the_money_is_back(self):
        services.request_cancellation(self.order, "Mudei de ideia")

        corpo = next(m.body for m in self.para_cliente() if "Reembolso" in m.subject)
        self.assertIn("Iniciámos o reembolso", corpo)
        self.assertIn("assim que o reembolso estiver concluído", corpo)


# ---------------------------------------------------------------------------
# (c) e (d) Durante a produção — a decisão é de uma pessoa
# ---------------------------------------------------------------------------


class DuranteAProducaoTests(RegraBase):
    """A peça pode estar pela metade. Quem decide é quem está olhando para ela."""

    def setUp(self):
        super().setUp()
        self.order = self.em_producao()
        services.request_cancellation(self.order, "Mudei de ideia")
        self.order.refresh_from_db()
        mail.outbox = []

    def test_it_waits_for_a_person(self):
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_the_team_is_asked_and_the_customer_is_told_to_wait(self):
        pedido = self.em_producao()
        mail.outbox = []  # o cenário já mandou confirmação e ordem de produção

        services.request_cancellation(pedido, "Mudei de ideia")

        self.assertEqual(len(self.para_equipe()), 1)
        self.assertIn("AÇÃO NECESSÁRIA", self.para_equipe()[0].subject)
        self.assertIn("Recebemos", self.para_cliente()[0].subject)

    def test_approving_without_a_stock_decision_is_refused(self):
        """Não há padrão certo — então a função se recusa a inventar um."""
        with self.assertRaises(services.StockDecisionRequired):
            services.approve_cancellation(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)

    def test_the_plan_says_a_decision_is_needed(self):
        plano = services.cancellation_plan(self.order)

        self.assertFalse(plano.auto)
        self.assertTrue(plano.needs_stock_decision)

    # -- (c) decisão de NÃO devolver ---------------------------------------

    def test_keeping_the_pieces_leaves_the_shelf_untouched(self):
        services.approve_cancellation(self.order, restore_stock=False)

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL - 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.stock_return_decision, StockDecision.KEPT)
        self.assertIsNone(self.order.stock_returned_at)

    def test_keeping_the_pieces_is_written_in_the_history(self):
        services.approve_cancellation(self.order, restore_stock=False)

        entrada = self.order.history.get(event=OrderEvent.STOCK_KEPT)
        self.assertIn("não devolver", entrada.message)
        self.assertFalse(entrada.is_customer_visible)

    # -- (d) decisão de devolver -------------------------------------------

    def test_returning_the_pieces_puts_them_back(self):
        services.approve_cancellation(self.order, restore_stock=True)

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)
        self.order.refresh_from_db()
        self.assertEqual(self.order.stock_return_decision, StockDecision.RETURNED)
        self.assertIsNotNone(self.order.stock_returned_at)

    def test_returning_the_pieces_is_written_in_the_history(self):
        services.approve_cancellation(self.order, restore_stock=True)

        entrada = self.order.history.get(event=OrderEvent.STOCK_RETURNED)
        self.assertIn("1 unidade", entrada.message)
        self.assertFalse(entrada.is_customer_visible)

    # -- comum aos dois -----------------------------------------------------

    def test_either_way_production_stops(self):
        services.approve_cancellation(self.order, restore_stock=False)

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)

    def test_either_way_the_refund_opens(self):
        services.approve_cancellation(self.order, restore_stock=True)

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)

    def test_the_decision_records_who_took_it(self):
        staff = get_user_model().objects.create_user(
            username="joana", email="joana@jdprint.test", password="x", is_staff=True
        )

        services.approve_cancellation(self.order, restore_stock=False, user=staff)

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_decided_by, staff)
        self.assertFalse(self.order.cancellation_auto)
        self.assertIsNotNone(self.order.cancellation_decided_at)

    def test_refusing_lets_the_printer_carry_on(self):
        services.refuse_cancellation(self.order, "A peça já está quase pronta.")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REFUSED)
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.IN_PRODUCTION)
        self.assertEqual(self.order.refund_status, RefundStatus.NONE)
        self.assertEqual(self.saldo(), ESTOQUE_INICIAL - 1)

    def test_refusing_tells_the_customer_why(self):
        services.refuse_cancellation(self.order, "A peça já está quase pronta.")

        corpo = self.para_cliente()[0].body
        self.assertIn("não foi possível cancelar", corpo)
        self.assertIn("A peça já está quase pronta.", corpo)


# ---------------------------------------------------------------------------
# (e) Pedido pronto
# ---------------------------------------------------------------------------


class PedidoProntoTests(RegraBase):
    """A peça existe e está inteira: ela volta — quando o dinheiro voltar."""

    def setUp(self):
        super().setUp()
        self.order = self.pronto()
        services.request_cancellation(self.order, "Mudei de ideia")
        self.order.refresh_from_db()
        mail.outbox = []

    def test_it_still_waits_for_a_person(self):
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)

    def test_no_stock_decision_is_asked(self):
        """A peça está pronta e inteira: a regra já sabe o que fazer com ela."""
        plano = services.cancellation_plan(self.order)

        self.assertFalse(plano.needs_stock_decision)
        self.assertEqual(plano.stock, StockDecision.ON_REFUND)

    def test_approving_does_not_return_the_stock_yet(self):
        """Enquanto o dinheiro não volta, a peça continua reservada ao pedido."""
        services.approve_cancellation(self.order)

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL - 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.stock_return_decision, StockDecision.ON_REFUND)

    def test_the_stock_comes_back_when_the_refund_is_completed(self):
        services.approve_cancellation(self.order)
        self.order.refresh_from_db()

        services.register_refund(self.order, self.order.total, reference="TRF-1")

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)
        self.order.refresh_from_db()
        self.assertEqual(self.order.stock_return_decision, StockDecision.RETURNED)

    def test_a_partial_refund_does_not_bring_it_back_yet(self):
        services.approve_cancellation(self.order)
        self.order.refresh_from_db()

        services.register_refund(self.order, Decimal("5.00"), reference="TRF-AUTO")

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL - 1)


# ---------------------------------------------------------------------------
# (f) (g) (h) Enviado, entregue e o prazo legal
# ---------------------------------------------------------------------------


class PrazoLegalTests(RegraBase):
    """Catorze dias corridos a partir do recebimento — nem 30, nem "até enviar"."""

    def test_the_legal_floor_is_fourteen_days(self):
        self.assertEqual(CancellationSettings.LEGAL_WITHDRAWAL_DAYS, 14)
        self.assertEqual(CancellationSettings.withdrawal_days_value(), 14)

    def test_the_shop_may_offer_more(self):
        CancellationSettings.objects.create(withdrawal_days=30)

        self.assertEqual(CancellationSettings.withdrawal_days_value(), 30)

    def test_the_shop_may_not_offer_less(self):
        from django.core.exceptions import ValidationError

        config = CancellationSettings(withdrawal_days=7)

        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_a_row_saved_below_the_floor_still_yields_the_floor(self):
        """O prazo legal não pode depender de por onde o dado entrou."""
        CancellationSettings.objects.create(withdrawal_days=3)

        self.assertEqual(CancellationSettings.withdrawal_days_value(), 14)

    # -- (f) enviado --------------------------------------------------------

    def test_a_shipped_order_can_still_be_asked_about(self):
        """O prazo nem começou: ele corre do recebimento, não do envio."""
        order = self.enviado()

        self.assertTrue(order.can_request_cancellation)
        self.assertIsNone(order.withdrawal_deadline)

    def test_a_shipped_order_goes_to_a_person(self):
        order = self.enviado()
        mail.outbox = []

        services.request_cancellation(order, "Quero devolver")

        order.refresh_from_db()
        self.assertEqual(order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertEqual(len(self.para_equipe()), 1)

    def test_approving_a_shipped_order_does_not_rewrite_production(self):
        """Ela saiu. "Interrompido" seria mentira no histórico."""
        order = self.enviado()
        services.request_cancellation(order, "Quero devolver")
        order.refresh_from_db()

        services.approve_cancellation(order, restore_stock=False)

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, FulfillmentStatus.SHIPPED)
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    # -- (g) dentro dos 14 dias ---------------------------------------------

    def test_inside_the_window_the_customer_may_ask(self):
        order = self.entregue(dias_atras=3)

        self.assertTrue(order.can_request_cancellation)
        self.assertTrue(services.request_cancellation(order, "Não era o que eu esperava"))

    def test_the_deadline_is_fourteen_days_after_delivery(self):
        order = self.entregue()

        esperado = order.delivered_at + timedelta(days=14)
        self.assertEqual(order.withdrawal_deadline, esperado)

    def test_the_last_day_still_counts(self):
        order = self.entregue(dias_atras=13)

        self.assertTrue(order.can_request_cancellation)

    def test_a_delivered_order_is_completed_until_it_is_cancelled(self):
        order = self.entregue(dias_atras=2)
        self.assertEqual(order.status, OrderStatus.COMPLETED)

        services.request_cancellation(order, "Quero devolver")
        order.refresh_from_db()
        services.approve_cancellation(order, restore_stock=False)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_undoing_a_delivery_stops_the_clock(self):
        """Entrega marcada por engano não deixa o prazo correndo."""
        order = self.entregue()
        self.assertIsNotNone(order.delivered_at)

        services.change_fulfillment_status(order, FulfillmentStatus.SHIPPED)

        order.refresh_from_db()
        self.assertIsNone(order.delivered_at)
        self.assertIsNone(order.withdrawal_deadline)

    # -- (h) fora dos 14 dias -----------------------------------------------

    def test_outside_the_window_the_button_is_gone(self):
        order = self.entregue(dias_atras=20)

        self.assertFalse(order.can_request_cancellation)

    def test_outside_the_window_the_service_says_no(self):
        order = self.entregue(dias_atras=20)
        mail.outbox = []

        with self.assertRaises(services.CancellationRefused):
            services.request_cancellation(order, "Tarde demais")

        order.refresh_from_db()
        self.assertEqual(order.cancellation_status, CancellationStatus.NONE)
        self.assertEqual(mail.outbox, [])

    def test_outside_the_window_the_page_explains_instead_of_crashing(self):
        order = self.entregue(dias_atras=20)
        self.client.force_login(self.user)

        resposta = self.client.post(
            reverse("orders:cancel", kwargs={"number": order.number}),
            {"reason": "Tarde demais"},
            follow=True,
        )

        self.assertContains(resposta, "prazo para cancelar")
        order.refresh_from_db()
        self.assertEqual(order.cancellation_status, CancellationStatus.NONE)

    def test_a_longer_window_configured_by_the_shop_is_honoured(self):
        CancellationSettings.objects.create(withdrawal_days=30)
        order = self.entregue(dias_atras=20)

        self.assertTrue(order.can_request_cancellation)


class PersonalizadoTests(RegraBase):
    """A exceção do artigo 16(c) é modelada — e não vira bloqueio automático."""

    def setUp(self):
        super().setUp()
        self.order = self.pago()
        item = self.order.items.first()
        item.fulfillment_type = "personalized"
        item.personalization_text = "Para a Ana"
        item.save(update_fields=["fulfillment_type", "personalization_text"])
        self.order.refresh_from_db()
        mail.outbox = []

    def test_the_order_is_flagged(self):
        self.assertTrue(self.order.has_personalized_items)
        self.assertTrue(services.cancellation_plan(self.order).personalized)

    def test_the_customer_is_not_blocked_from_asking(self):
        """A exceção tem condições que um `if` não confere. Ela não fecha a porta."""
        self.assertTrue(self.order.can_request_cancellation)
        self.assertTrue(services.request_cancellation(self.order, "Mudei de ideia"))

    def test_it_goes_to_a_person_instead_of_being_auto_approved(self):
        services.request_cancellation(self.order, "Mudei de ideia")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertEqual(len(self.para_equipe()), 1)

    def test_a_made_to_order_item_is_not_a_personalised_one(self):
        """Produto de catálogo impresso sob demanda não é feito sob especificação."""
        pedido = self.pago()
        item = pedido.items.first()
        item.fulfillment_type = "made_to_order"
        item.personalization_text = ""
        item.save(update_fields=["fulfillment_type", "personalization_text"])
        pedido.refresh_from_db()

        self.assertFalse(pedido.has_personalized_items)
        self.assertTrue(services.cancellation_plan(pedido).auto)


# ---------------------------------------------------------------------------
# (i) (j) Reembolso parcial e concluído
# ---------------------------------------------------------------------------


class ReembolsoTests(RegraBase):
    """O sistema registra o que uma pessoa devolveu. Ele não move dinheiro."""

    def setUp(self):
        super().setUp()
        self.order = self.pago()
        services.request_cancellation(self.order, "Mudei de ideia")
        self.order.refresh_from_db()
        self.staff = get_user_model().objects.create_user(
            username="joana", email="joana@jdprint.test", password="x", is_staff=True
        )
        mail.outbox = []

    def test_it_starts_pending_with_nothing_returned(self):
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))

    # -- (i) parcial --------------------------------------------------------

    def test_a_partial_refund_is_partial(self):
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-1", user=self.staff)

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.PARTIAL)
        self.assertEqual(self.order.refunded_amount, Decimal("5.00"))
        self.assertEqual(self.order.refund_due, self.order.total - Decimal("5.00"))

    def test_a_partial_refund_is_not_finished(self):
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-AUTO")

        self.order.refresh_from_db()
        self.assertIsNone(self.order.refunded_at)

    def test_partial_refunds_add_up(self):
        """Duas transferências de verdade — logo, duas referências."""
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-A")
        self.order.refresh_from_db()
        services.register_refund(self.order, Decimal("3.00"), reference="TRF-B")

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("8.00"))
        self.assertEqual(self.order.refund_status, RefundStatus.PARTIAL)

    def test_the_partial_letter_says_what_is_missing(self):
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-AUTO")

        corpo = self.para_cliente()[-1].body
        self.assertIn("Já devolvemos", corpo)
        self.assertIn("Ainda faltam", corpo)

    # -- (j) concluído ------------------------------------------------------

    def test_paying_the_whole_amount_finishes_it(self):
        services.register_refund(
            self.order, self.order.total, reference="TRF-9", user=self.staff
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.DONE)
        self.assertIsNotNone(self.order.refunded_at)
        self.assertEqual(self.order.refund_due, Decimal("0.00"))

    def test_it_records_who_when_and_which_transfer(self):
        services.register_refund(
            self.order, self.order.total, reference="TRF-9", user=self.staff
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_reference, "TRF-9")
        self.assertEqual(self.order.refunded_by, self.staff)

    def test_the_final_letter_says_it_is_done(self):
        services.register_refund(self.order, self.order.total, reference="TRF-AUTO")

        corpo = self.para_cliente()[-1].body
        self.assertIn("está concluído", corpo)

    def test_it_never_returns_more_than_was_charged(self):
        """Um erro de digitação não pode fazer a loja dever ao cliente."""
        services.register_refund(self.order, self.order.total + Decimal("100.00"), reference="TRF-AUTO")

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, self.order.total)

    def test_the_order_status_never_becomes_refunded(self):
        """Reembolso é estado do dinheiro; o pedido continua cancelado."""
        services.register_refund(self.order, self.order.total, reference="TRF-AUTO")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertNotIn("refunded", [c[0] for c in OrderStatus.choices])

    def test_the_history_separates_partial_from_final(self):
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-A")
        self.order.refresh_from_db()
        services.register_refund(self.order, self.order.total, reference="TRF-B")

        eventos = self.eventos(self.order)
        self.assertIn(OrderEvent.REFUND_PARTIAL, eventos)
        self.assertIn(OrderEvent.REFUNDED, eventos)

    def test_a_refund_without_a_cancellation_is_refused(self):
        """Ninguém devolve dinheiro de um pedido que segue o curso normal."""
        outro = self.pago()

        self.assertFalse(services.register_refund(outro, Decimal("5.00"), reference="TRF-AUTO"))

    def test_zero_is_not_a_refund(self):
        self.assertFalse(services.register_refund(self.order, Decimal("0.00"), reference="TRF-AUTO"))


class IdiomaTests(RegraBase):
    """As frases novas seguem o idioma do pedido, como as antigas."""

    IDIOMAS = {
        "fr": {
            "recusa": "il n'a pas été possible",
            "reembolso": "Nous avons lancé le remboursement",
            "sem_cobranca": "a été facturé pour cette commande",
        },
        "nl": {
            "recusa": "was het niet mogelijk",
            "reembolso": "begonnen met de terugbetaling",
            "sem_cobranca": "is niets aangerekend",
        },
        "en": {
            "recusa": "it was not possible to cancel",
            "reembolso": "We have started the refund",
            "sem_cobranca": "Nothing was charged",
        },
    }

    def test_the_refusal_follows_the_order_language(self):
        for idioma, trechos in self.IDIOMAS.items():
            with self.subTest(idioma=idioma):
                order = self.em_producao(language=idioma)
                services.request_cancellation(order, "Motivo")
                order.refresh_from_db()
                mail.outbox = []

                services.refuse_cancellation(order, "Já está na impressora.")

                self.assertIn(trechos["recusa"], self.para_cliente()[0].body)

    def test_the_refund_letter_follows_the_order_language(self):
        for idioma, trechos in self.IDIOMAS.items():
            with self.subTest(idioma=idioma):
                order = self.pago(language=idioma)
                mail.outbox = []

                services.request_cancellation(order, "Motivo")

                corpos = " ".join(m.body for m in self.para_cliente())
                self.assertIn(trechos["reembolso"], corpos)

    def test_the_no_charge_sentence_follows_the_order_language(self):
        for idioma, trechos in self.IDIOMAS.items():
            with self.subTest(idioma=idioma):
                order = self.novo_pedido(language=idioma)
                mail.outbox = []

                services.request_cancellation(order, "Motivo")

                self.assertIn(trechos["sem_cobranca"], self.para_cliente()[0].body)


# ---------------------------------------------------------------------------
# (k) Duplo clique e reprocessamento
# ---------------------------------------------------------------------------


class IdempotenciaTests(RegraBase):
    """Repetir a mesma ação não repete o efeito dela."""

    def test_a_second_request_changes_nothing(self):
        order = self.em_producao()
        services.request_cancellation(order, "Primeira")
        order.refresh_from_db()
        mail.outbox = []

        self.assertFalse(services.request_cancellation(order, "Segunda"))
        self.assertEqual(mail.outbox, [])

    def test_a_second_approval_changes_nothing(self):
        order = self.em_producao()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        services.approve_cancellation(order, restore_stock=True)
        order.refresh_from_db()
        saldo = self.saldo()
        mail.outbox = []

        self.assertFalse(services.approve_cancellation(order, restore_stock=True))

        self.assertEqual(self.saldo(), saldo)
        self.assertEqual(mail.outbox, [])

    def test_the_stock_never_comes_back_twice(self):
        """A marca é do pedido, e é conferida com ele travado."""
        order = self.pago()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)

        self.assertEqual(services.return_stock(order), 0)
        self.assertEqual(services.return_stock(order), 0)
        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)

    def test_returning_stock_that_never_left_does_nothing(self):
        order = self.novo_pedido()

        self.assertEqual(services.return_stock(order), 0)
        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)

    def test_the_approved_event_is_written_once(self):
        order = self.em_producao()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        services.approve_cancellation(order, restore_stock=False)
        services.approve_cancellation(order, restore_stock=False)

        self.assertEqual(
            order.history.filter(event=OrderEvent.CANCELLATION_APPROVED).count(), 1
        )

    def test_a_finished_refund_accepts_nothing_more(self):
        order = self.pago()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        services.register_refund(order, order.total, reference="TRF-AUTO")
        order.refresh_from_db()
        mail.outbox = []

        self.assertFalse(services.register_refund(order, Decimal("1.00"), reference="TRF-AUTO"))
        self.assertEqual(mail.outbox, [])

    def test_a_refusal_cannot_overwrite_a_cancellation_already_approved(self):
        """Duas pessoas decidindo quase juntas: uma aprova, a outra recusa.

        Cada requisição do Admin traz o seu próprio objeto, lido antes de
        qualquer uma agir. A que chega depois decidia sobre uma fotografia
        velha e carimbava "recusado" num pedido já cancelado — com o e-mail de
        recusa saindo depois do de cancelamento.
        """
        order = self.em_producao()
        services.request_cancellation(order, "Motivo")

        # as duas telas do Admin, cada uma com a sua cópia
        de_quem_aprova = Order.objects.get(pk=order.pk)
        de_quem_recusa = Order.objects.get(pk=order.pk)

        self.assertTrue(
            services.approve_cancellation(de_quem_aprova, restore_stock=False)
        )
        mail.outbox = []

        self.assertFalse(services.refuse_cancellation(de_quem_recusa, "Já saiu."))

        order.refresh_from_db()
        self.assertEqual(order.cancellation_status, CancellationStatus.APPROVED)
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(mail.outbox, [])

    def test_an_approval_cannot_overwrite_a_refusal(self):
        """O sentido inverso da mesma corrida."""
        order = self.em_producao()
        services.request_cancellation(order, "Motivo")

        de_quem_aprova = Order.objects.get(pk=order.pk)
        de_quem_recusa = Order.objects.get(pk=order.pk)

        self.assertTrue(services.refuse_cancellation(de_quem_recusa, "Já saiu."))
        saldo = self.saldo()
        mail.outbox = []

        self.assertFalse(
            services.approve_cancellation(de_quem_aprova, restore_stock=True)
        )

        order.refresh_from_db()
        self.assertEqual(order.cancellation_status, CancellationStatus.REFUSED)
        self.assertEqual(order.status, OrderStatus.CONFIRMED)
        self.assertEqual(order.fulfillment_status, FulfillmentStatus.IN_PRODUCTION)
        self.assertEqual(self.saldo(), saldo)
        self.assertEqual(mail.outbox, [])

    def test_two_requests_from_a_stale_pair_produce_one_cancellation(self):
        """O duplo clique do cliente, com duas cópias do mesmo pedido."""
        order = self.em_producao()
        primeira = Order.objects.get(pk=order.pk)
        segunda = Order.objects.get(pk=order.pk)

        self.assertTrue(services.request_cancellation(primeira, "Primeira"))
        mail.outbox = []

        self.assertFalse(services.request_cancellation(segunda, "Segunda"))

        order.refresh_from_db()
        self.assertEqual(
            order.history.filter(event=OrderEvent.CANCELLATION_REQUESTED).count(), 1
        )
        self.assertEqual(order.cancellation_reason, "Primeira")
        self.assertEqual(mail.outbox, [])

    def test_the_same_transfer_is_never_counted_twice(self):
        """O valor é somativo — e por isso o duplo clique dobrava o registro."""
        order = self.pago()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()

        self.assertTrue(services.register_refund(order, Decimal("5.00"), reference="TRF-1"))
        order.refresh_from_db()
        self.assertFalse(services.register_refund(order, Decimal("5.00"), reference="TRF-1"))

        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal("5.00"))

    def test_a_different_transfer_of_the_same_value_does_count(self):
        order = self.pago()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()

        services.register_refund(order, Decimal("5.00"), reference="TRF-1")
        order.refresh_from_db()
        services.register_refund(order, Decimal("5.00"), reference="TRF-2")

        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal("10.00"))

    def test_a_late_payment_failure_does_not_undo_the_cancellation(self):
        """A sessão expirada do provedor chega depois e não reescreve nada."""
        order = self.novo_pedido()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        mail.outbox = []

        services.register_payment_failure(order, reason="sessão expirada")

        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.NOT_CHARGED)
        self.assertFalse(
            order.history.filter(event=OrderEvent.PAYMENT_FAILED).exists()
        )

    def test_an_already_cancelled_order_is_not_cancelled_again(self):
        """A guarda olha `cancelled_at`, e não só o estado da solicitação."""
        order = self.pago()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        order.cancellation_status = CancellationStatus.REQUESTED
        order.save(update_fields=["cancellation_status"])
        saldo = self.saldo()
        mail.outbox = []

        self.assertFalse(services.approve_cancellation(order, restore_stock=True))

        self.assertEqual(self.saldo(), saldo)
        self.assertEqual(mail.outbox, [])

    def test_a_payment_confirmed_mid_decision_is_not_lost(self):
        """O plano é decidido com o pedido travado, não com uma leitura velha.

        Antes desta correção, um pedido não pago lido para o plano e pago logo
        depois era gravado como "não cobrado" — e o dinheiro ficava na conta da
        loja sem reembolso nenhum aberto.
        """
        order = self.novo_pedido()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.NOT_CHARGED)

        # a decisão foi tomada quando ainda não havia pagamento
        self.assertEqual(order.refund_status, RefundStatus.NONE)

        # e a transferência que chega depois abre o reembolso
        services.confirm_payment(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PAID)
        self.assertEqual(order.refund_status, RefundStatus.PENDING)

    def test_confirming_the_payment_twice_takes_the_stock_once(self):
        """A garantia antiga continua de pé depois da mudança."""
        order = self.novo_pedido()
        services.confirm_payment(order)
        order.refresh_from_db()
        services.confirm_payment(order)

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL - 1)


class PagamentoAtrasadoTests(RegraBase):
    """A transferência que cai depois do cancelamento.

    Acontece de verdade numa loja que vende por transferência: o cliente
    cancela um pedido não pago e a ordem bancária dele chega dois dias depois.
    O dinheiro é um fato — mas a compra não existe mais.
    """

    def setUp(self):
        super().setUp()
        self.order = self.novo_pedido()
        services.request_cancellation(self.order, "Mudei de ideia")
        self.order.refresh_from_db()
        mail.outbox = []

    def test_the_payment_is_recorded(self):
        """Negar o pagamento não faz o dinheiro voltar sozinho."""
        services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(self.order.paid_at)

    def test_the_order_stays_cancelled(self):
        services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)

    def test_the_stock_is_not_taken_for_a_cancelled_order(self):
        services.confirm_payment(self.order)

        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.stock_applied_at)

    def test_the_refund_is_opened(self):
        services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)
        self.assertEqual(self.order.refund_due, self.order.total)

    def test_the_team_finds_it_in_the_notes(self):
        services.confirm_payment(self.order)

        nota = self.order.notes.first()
        self.assertIsNotNone(nota)
        self.assertIn("depois", nota.body)

    def test_the_customer_is_not_told_the_purchase_was_confirmed(self):
        services.confirm_payment(self.order)

        assuntos = " | ".join(m.subject for m in self.para_cliente())
        self.assertNotIn("confirmado", assuntos)
        self.assertIn("Reembolso", assuntos)

    def test_the_workshop_gets_no_production_order(self):
        services.confirm_payment(self.order)

        self.assertEqual(self.para_equipe(), [])
        self.order.refresh_from_db()
        self.assertIsNone(self.order.admin_email_sent_at)

    def test_a_second_delivery_of_the_webhook_changes_nothing(self):
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        mail.outbox = []

        services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.notes.count(), 1)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(
            self.order.history.filter(event=OrderEvent.REFUND_PENDING).count(), 1
        )


# ---------------------------------------------------------------------------
# (l) O Admin não contorna a regra
# ---------------------------------------------------------------------------


class AdminNaoContornaTests(RegraBase):
    """O caminho pelo formulário — o problema P1 da auditoria — está fechado."""

    def setUp(self):
        super().setUp()
        self.order = self.em_producao()
        self.staff = get_user_model().objects.create_user(
            username="joana", email="joana@jdprint.test",
            password="senha-de-teste-77", is_staff=True,
        )
        self.staff.user_permissions.set(
            Permission.objects.filter(
                codename__in=["view_order", "change_order"],
                content_type__app_label="orders",
            )
        )
        self.client.force_login(self.staff)
        mail.outbox = []

    def payload(self, **overrides):
        dados = {
            "fulfillment_status": self.order.fulfillment_status,
            "tracking_number": "",
            "is_gift": "",
            "gift_message": "",
        }
        dados.update(overrides)
        for prefixo, total in (
            ("items", self.order.items.count()),
            ("addresses", self.order.addresses.count()),
            ("payments", self.order.payments.count()),
            ("payment_proofs", self.order.payment_proofs.count()),
            ("history", self.order.history.count()),
            ("notes", 0),
        ):
            dados[f"{prefixo}-TOTAL_FORMS"] = str(total)
            dados[f"{prefixo}-INITIAL_FORMS"] = str(total)
            dados[f"{prefixo}-MIN_NUM_FORMS"] = "0"
            dados[f"{prefixo}-MAX_NUM_FORMS"] = "1000"
        return dados

    def salvar(self, **overrides):
        return self.client.post(
            reverse("admin:orders_order_change", args=[self.order.pk]),
            self.payload(**overrides),
            follow=True,
        )

    def test_the_form_no_longer_offers_the_state_fields(self):
        resposta = self.client.get(
            reverse("admin:orders_order_change", args=[self.order.pk])
        )

        html = resposta.content.decode()
        for campo in ("id_status", "id_payment_status", "id_cancellation_status", "id_refund_status"):
            with self.subTest(campo=campo):
                self.assertNotIn(f'name="{campo[3:]}"', html)

    def test_approving_through_the_form_does_nothing(self):
        """O caminho que aprovava sem parar produção, sem evento e sem e-mail."""
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()
        mail.outbox = []

        self.salvar(cancellation_status="approved")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.IN_PRODUCTION)
        self.assertEqual(mail.outbox, [])

    def test_the_order_status_cannot_be_typed(self):
        self.salvar(status="cancelled")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)
        self.assertIsNone(self.order.cancelled_at)

    def test_the_payment_status_cannot_be_typed(self):
        self.salvar(payment_status="not_charged")

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)

    def test_the_refund_cannot_be_typed(self):
        self.salvar(refund_status="refunded", refunded_amount="99.00")

        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.NONE)
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))

    def test_production_can_still_be_moved_by_hand(self):
        """O que era editável e funcionava continua editável e funcionando."""
        self.salvar(fulfillment_status=FulfillmentStatus.READY)

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.READY)

    def test_halted_is_not_in_the_production_list(self):
        resposta = self.client.get(
            reverse("admin:orders_order_change", args=[self.order.pk])
        )

        self.assertNotContains(resposta, 'value="halted"')

    def test_halted_cannot_be_forced_through_the_form(self):
        self.salvar(fulfillment_status=FulfillmentStatus.HALTED)

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.IN_PRODUCTION)

    def test_a_halted_order_does_not_go_back_to_the_queue(self):
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()
        services.approve_cancellation(self.order, restore_stock=False)
        self.order.refresh_from_db()

        with self.assertRaises(services.StatusChangeRefused):
            services.change_fulfillment_status(self.order, FulfillmentStatus.IN_PRODUCTION)

    def registrar_reembolso(self, **extra):
        dados = {
            "action": "action_register_refund",
            "index": "0",
            "_selected_action": [str(self.order.pk)],
            "confirmar": "1",
            "amount": "5.00",
            "reference": "TRF-77",
        }
        dados.update(extra)
        return self.client.post(reverse("admin:orders_order_changelist"), dados)

    def abrir_reembolso(self):
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()
        services.approve_cancellation(self.order, restore_stock=False)
        self.order.refresh_from_db()

    def test_the_refund_action_asks_before_registering(self):
        self.abrir_reembolso()

        resposta = self.client.post(
            reverse("admin:orders_order_changelist"),
            {
                "action": "action_register_refund",
                "index": "0",
                "_selected_action": [str(self.order.pk)],
            },
            follow=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))
        self.assertContains(resposta, "valor devolvido")

    def test_the_refund_action_records_the_transfer(self):
        self.abrir_reembolso()

        self.registrar_reembolso()

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("5.00"))
        self.assertEqual(self.order.refund_reference, "TRF-77")
        self.assertEqual(self.order.refunded_by, self.staff)
        self.assertEqual(self.order.refund_status, RefundStatus.PARTIAL)

    def test_the_refund_action_refuses_an_order_without_an_open_refund(self):
        """Ninguém devolve dinheiro de um pedido que segue o curso normal."""
        self.registrar_reembolso()

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))
        self.assertEqual(self.order.refund_status, RefundStatus.NONE)

    def test_the_refund_action_takes_one_order_at_a_time(self):
        """Um valor e uma referência são de UMA transferência."""
        self.abrir_reembolso()
        outro = self.em_producao()
        services.request_cancellation(outro, "Motivo")
        outro.refresh_from_db()
        services.approve_cancellation(outro, restore_stock=False)

        self.registrar_reembolso(
            _selected_action=[str(self.order.pk), str(outro.pk)]
        )

        self.order.refresh_from_db()
        outro.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))
        self.assertEqual(outro.refunded_amount, Decimal("0.00"))

    def test_the_action_is_the_only_way_in(self):
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()
        mail.outbox = []

        self.client.post(
            reverse("admin:orders_order_changelist"),
            {
                "action": "action_approve_cancellation",
                "index": "0",
                "_selected_action": [str(self.order.pk)],
                "confirmar": "1",
                "resposta": "",
                "restore_stock": "1",
            },
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)
        self.assertEqual(self.saldo(), ESTOQUE_INICIAL)
        self.assertTrue(mail.outbox)


# ---------------------------------------------------------------------------
# Regressões da revisão adversarial
# ---------------------------------------------------------------------------


class LoteNoAdminTests(RegraBase):
    """Aprovar vários pedidos de uma vez não pode misturar as decisões.

    A tela pergunta sobre o estoque quando **algum** pedido do lote precisa da
    resposta — e a resposta era repassada a todos. Bastava um pedido em
    produção no meio da seleção para que a escolha humana sobrescrevesse o
    plano correto dos outros: um pedido pronto devolvia a peça antes do
    reembolso, e um pedido pago e ainda parado perdia a devolução automática.
    """

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user(
            username="joana", email="joana@jdprint.test",
            password="senha-de-teste-77", is_staff=True,
        )
        self.staff.user_permissions.set(
            Permission.objects.filter(
                codename__in=["view_order", "change_order"],
                content_type__app_label="orders",
            )
        )
        self.client.force_login(self.staff)

    def em_analise(self, order):
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        return order

    def aprovar_lote(self, pedidos, restore_stock):
        return self.client.post(
            reverse("admin:orders_order_changelist"),
            {
                "action": "action_approve_cancellation",
                "index": "0",
                "_selected_action": [str(p.pk) for p in pedidos],
                "confirmar": "1",
                "resposta": "",
                "restore_stock": restore_stock,
            },
        )

    def test_a_ready_order_keeps_its_stock_until_the_refund(self):
        """R4: a peça pronta volta quando o dinheiro volta, não na aprovação."""
        pronto = self.em_analise(self.pronto())
        producao = self.em_analise(self.em_producao())
        saldo = self.saldo()

        self.aprovar_lote([pronto, producao], "1")

        pronto.refresh_from_db()
        self.assertEqual(pronto.stock_return_decision, StockDecision.ON_REFUND)
        self.assertIsNone(pronto.stock_returned_at)
        # só a peça do pedido em produção voltou
        self.assertEqual(self.saldo(), saldo + 1)

    def personalizado_parado(self):
        """Pago, produção ainda parada — e personalizado, que é o que o manda
        para a análise humana em vez de ser resolvido pela regra."""
        order = self.pago()
        item = order.items.first()
        item.fulfillment_type = "personalized"
        item.personalization_text = "Para a Ana"
        item.save(update_fields=["fulfillment_type", "personalization_text"])
        order.refresh_from_db()
        return order

    def test_an_untouched_order_gets_its_stock_back_anyway(self):
        """R2: pago e ainda parado devolve sozinho, mesmo com "não" no lote."""
        parado = self.em_analise(self.personalizado_parado())
        producao = self.em_analise(self.em_producao())
        saldo = self.saldo()

        self.aprovar_lote([parado, producao], "0")

        parado.refresh_from_db()
        producao.refresh_from_db()
        self.assertEqual(parado.stock_return_decision, StockDecision.RETURNED)
        self.assertEqual(producao.stock_return_decision, StockDecision.KEPT)
        # voltou a do pedido parado, não a do que estava na impressora
        self.assertEqual(self.saldo(), saldo + 1)


class ReferenciaDoReembolsoTests(RegraBase):
    """A mesma transferência não pode ser contada duas vezes — nunca."""

    def setUp(self):
        super().setUp()
        self.order = self.pago()
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()

    def test_a_reference_used_earlier_is_still_refused(self):
        """Comparar só com a última deixava a primeira entrar de novo."""
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-1")
        self.order.refresh_from_db()
        services.register_refund(self.order, Decimal("5.00"), reference="TRF-2")
        self.order.refresh_from_db()

        self.assertFalse(
            services.register_refund(self.order, Decimal("5.00"), reference="TRF-1")
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("10.00"))

    def test_a_reference_is_required(self):
        """R6: sem ela não há como distinguir dois cliques de dois reembolsos."""
        self.assertFalse(
            services.register_refund(self.order, Decimal("5.00"), reference="")
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))

    def test_a_similar_reference_is_not_confused_with_another(self):
        """"TRF-1" não pode bloquear "TRF-11"."""
        services.register_refund(self.order, Decimal("2.00"), reference="TRF-1")
        self.order.refresh_from_db()

        self.assertTrue(
            services.register_refund(self.order, Decimal("2.00"), reference="TRF-11")
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("4.00"))


class PagamentoTardioNoPainelTests(RegraBase):
    """O que o cliente lê quando o dinheiro chega depois do cancelamento."""

    def setUp(self):
        super().setUp()
        self.order = self.novo_pedido()
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()
        mail.outbox = []

    def test_the_timeline_never_says_the_order_was_confirmed(self):
        services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertNotIn(OrderEvent.CONFIRMED, self.eventos(self.order))
        self.assertIsNone(self.order.confirmed_at)

    def test_the_customer_page_does_not_contradict_itself(self):
        services.confirm_payment(self.order)
        self.client.force_login(self.user)

        resposta = self.client.get(
            reverse("orders:detail", kwargs={"number": self.order.number})
        )

        self.assertNotContains(resposta, "encomenda foi confirmada")
        self.assertContains(resposta, "reembolso")

    def test_a_normal_order_is_still_confirmed(self):
        """A correção não pode ter apagado o caminho normal."""
        pedido = self.pago()

        self.assertIn(OrderEvent.CONFIRMED, self.eventos(pedido))
        self.assertIsNotNone(pedido.confirmed_at)


class HistoricoDaRecusaTests(RegraBase):
    """Uma linha do histórico registra o que aconteceu naquele momento."""

    def test_an_old_refusal_keeps_its_own_words(self):
        order = self.em_producao()
        services.request_cancellation(order, "Primeira")
        order.refresh_from_db()
        services.refuse_cancellation(order, "A peça está na impressora.")
        order.refresh_from_db()

        # o cliente pede de novo, e desta vez a loja escreve outra coisa
        services.request_cancellation(order, "Segunda")
        order.refresh_from_db()
        services.refuse_cancellation(order, "Já foi para a transportadora.")

        primeira = order.history.filter(
            event=OrderEvent.CANCELLATION_REFUSED
        ).order_by("created_at").first()
        self.assertIn("na impressora", primeira.customer_message)


class TelaDeCancelamentoTests(RegraBase):
    """A tela explica o motivo daquele pedido, não uma frase fixa."""

    def test_a_personalised_order_is_not_told_production_started(self):
        order = self.pago()
        item = order.items.first()
        item.fulfillment_type = "personalized"
        item.personalization_text = "Para a Ana"
        item.save(update_fields=["fulfillment_type", "personalization_text"])
        self.client.force_login(self.user)

        resposta = self.client.get(
            reverse("orders:cancel", kwargs={"number": order.number})
        )

        self.assertNotContains(resposta, "produção já começou")

    def test_an_order_in_production_is_told_so(self):
        order = self.em_producao()
        self.client.force_login(self.user)

        resposta = self.client.get(
            reverse("orders:cancel", kwargs={"number": order.number})
        )

        self.assertContains(resposta, "produção já começou")


class TelaDoAdminTests(RegraBase):
    """O que a equipe vê tem de corresponder ao estado real."""

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", "senha-de-teste-77"
        )
        self.client.force_login(self.staff)

    def pagina(self, order):
        return self.client.get(
            reverse("admin:orders_order_change", args=[order.pk])
        )

    def test_the_refund_panel_shows_the_numbers(self):
        """`{:.2f}` dentro de `format_html` levantava ValueError e a seção
        inteira aparecia como um traço."""
        order = self.pago()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        services.register_refund(order, Decimal("5.00"), reference="TRF-9")

        resposta = self.pagina(order)

        self.assertContains(resposta, "Já devolvido")
        self.assertContains(resposta, "5.00")
        self.assertContains(resposta, "TRF-9")

    def test_a_halted_order_is_not_shown_as_not_started(self):
        order = self.em_producao()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        services.approve_cancellation(order, restore_stock=False)

        resposta = self.pagina(order)

        self.assertContains(resposta, "Interrompido")

    def test_halted_is_still_not_offered_on_a_live_order(self):
        resposta = self.pagina(self.em_producao())

        self.assertNotContains(resposta, 'value="halted"')

    def test_a_cancelled_order_is_not_asked_to_resend_the_sale_emails(self):
        """O painel pedia o reenvio de "compra confirmada" de um pedido
        cancelado que recebeu o pagamento atrasado."""
        order = self.novo_pedido()
        services.request_cancellation(order, "Motivo")
        order.refresh_from_db()
        services.confirm_payment(order)
        order.refresh_from_db()

        resposta = self.pagina(order)

        self.assertNotContains(resposta, "NÃO enviado — reenvie")


class AcaoDeReembolsoTests(RegraBase):
    """A mensagem do Admin conta o que realmente aconteceu."""

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", "senha-de-teste-77"
        )
        self.client.force_login(self.staff)
        self.order = self.pago()
        services.request_cancellation(self.order, "Motivo")
        self.order.refresh_from_db()

    def registrar(self, valor, referencia):
        return self.client.post(
            reverse("admin:orders_order_changelist"),
            {
                "action": "action_register_refund",
                "index": "0",
                "_selected_action": [str(self.order.pk)],
                "confirmar": "1",
                "amount": valor,
                "reference": referencia,
            },
            follow=True,
        )

    def test_a_repeated_reference_is_reported_as_nothing_done(self):
        self.registrar("5.00", "TRF-1")

        resposta = self.registrar("5.00", "TRF-1")

        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("5.00"))
        self.assertContains(resposta, "Nada foi registrado")

    def test_a_real_refund_is_reported_as_registered(self):
        resposta = self.registrar("5.00", "TRF-1")

        self.assertContains(resposta, "Reembolso registrado")


class EntregaDesfeitaTests(RegraBase):
    """Voltar de "entregue" pelo caminho do envio também para o relógio."""

    def test_marking_shipped_again_clears_the_delivery_date(self):
        order = self.entregue()
        self.assertIsNotNone(order.delivered_at)

        services.mark_shipped(order, "BE999")

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, FulfillmentStatus.SHIPPED)
        self.assertIsNone(order.delivered_at)
        self.assertTrue(order.can_request_cancellation)
