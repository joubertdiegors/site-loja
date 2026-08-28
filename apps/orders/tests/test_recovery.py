"""AUD-01, AUD-02 e AUD-04 — recuperação, reenvio e registro.

O webhook da Stripe pode ser entregue duas vezes, e uma entrega pode morrer no
meio. O que estes testes cobram é que **nada fique pela metade sem que a
próxima tentativa termine** — e que nada aconteça duas vezes.

O caso que a auditoria encontrou:

    pagamento confirmado -> estoque estoura -> webhook devolve 500
    Stripe reentrega     -> pedido já está pago
                         -> o código voltava cedo e ia embora
                         -> estoque nunca baixava, ninguém era avisado
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import CartLine
from apps.catalog.models import ProductVariant
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services
from apps.orders.models import (
    FulfillmentStatus,
    Order,
    OrderEvent,
    PaymentStatus,
)


class RecoveryBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")

        self.user = make_user(username="diego3d", email="diego@example.com")
        self.address = make_address(self.user.customer, self.country)

        self.product = make_product(
            sku="DINO", name="Dinossauro", price=Decimal("27.90"), stock_quantity=10
        )
        self.variant = self.product.default_variant

        self.order = services.create_order(
            customer=self.user.customer,
            lines=[
                CartLine(
                    key=f"{self.product.pk}:{self.variant.pk}:-",
                    product=self.product,
                    variant=self.variant,
                    quantity=2,
                )
            ],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
            language="pt-br",
        )
        mail.outbox = []

    def stock(self):
        self.variant.refresh_from_db()
        return self.variant.stock_quantity


class HappyPathTests(RecoveryBase):
    """Sem falha nenhuma, tudo acontece uma vez só."""

    def test_everything_runs_once(self):
        services.confirm_payment(self.order)
        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(self.order.stock_applied_at)
        self.assertIsNotNone(self.order.confirmation_email_sent_at)
        self.assertIsNotNone(self.order.admin_email_sent_at)
        self.assertEqual(self.stock(), 8)
        self.assertEqual(len(mail.outbox), 2)

    def test_a_second_delivery_changes_nothing(self):
        """A Stripe reentrega o mesmo evento. Nada pode acontecer duas vezes."""
        services.confirm_payment(self.order)
        mail.outbox = []

        services.confirm_payment(self.order)
        self.order.refresh_from_db()

        self.assertEqual(self.stock(), 8)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_second_delivery_does_not_duplicate_the_history(self):
        services.confirm_payment(self.order)
        services.confirm_payment(self.order)

        self.assertEqual(self.order.history.filter(event=OrderEvent.PAID).count(), 1)
        self.assertEqual(self.order.history.filter(event=OrderEvent.CONFIRMED).count(), 1)


class StockFailureRecoveryTests(RecoveryBase):
    """AUD-01: o pagamento entra, o estoque estoura, a reentrega termina."""

    def test_the_payment_survives_a_stock_failure(self):
        with mock.patch(
            "apps.orders.services.apply_stock", side_effect=RuntimeError("banco caiu")
        ):
            with self.assertRaises(RuntimeError):
                services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertIsNone(self.order.stock_applied_at)

    def test_the_next_delivery_finishes_what_was_left(self):
        """O caso exato da auditoria."""
        with mock.patch(
            "apps.orders.services.apply_stock", side_effect=RuntimeError("banco caiu")
        ):
            with self.assertRaises(RuntimeError):
                services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertIsNone(self.order.stock_applied_at)
        self.assertIsNone(self.order.confirmation_email_sent_at)
        self.assertEqual(self.stock(), 10)  # nada baixou ainda

        # A Stripe reentrega. O pedido já está pago — e é aqui que o código
        # antigo voltava cedo e deixava tudo pela metade.
        services.confirm_payment(self.order)
        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(self.order.stock_applied_at)
        self.assertIsNotNone(self.order.confirmation_email_sent_at)
        self.assertIsNotNone(self.order.admin_email_sent_at)
        self.assertEqual(self.stock(), 8)
        self.assertEqual(len(mail.outbox), 2)

    def test_the_recovery_does_not_pay_twice(self):
        with mock.patch(
            "apps.orders.services.apply_stock", side_effect=RuntimeError("banco caiu")
        ):
            with self.assertRaises(RuntimeError):
                services.confirm_payment(self.order)

        pago_em = Order.objects.get(pk=self.order.pk).paid_at
        services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.paid_at, pago_em)
        self.assertEqual(self.order.history.filter(event=OrderEvent.PAID).count(), 1)

    def test_the_recovery_does_not_discount_the_stock_twice(self):
        services.confirm_payment(self.order)
        self.assertEqual(self.stock(), 8)

        services.confirm_payment(self.order)
        self.assertEqual(self.stock(), 8)


class EmailFailureRecoveryTests(RecoveryBase):
    """Falha no e-mail: pagamento e estoque ficam feitos, o aviso volta depois."""

    def test_a_customer_email_failure_does_not_undo_the_payment(self):
        with mock.patch("apps.orders.emails._send", return_value=False):
            services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(self.order.stock_applied_at)
        self.assertIsNone(self.order.confirmation_email_sent_at)
        self.assertIsNone(self.order.admin_email_sent_at)

    def test_the_next_delivery_sends_the_pending_emails(self):
        with mock.patch("apps.orders.emails._send", return_value=False):
            services.confirm_payment(self.order)

        services.confirm_payment(self.order)
        self.order.refresh_from_db()

        self.assertIsNotNone(self.order.confirmation_email_sent_at)
        self.assertIsNotNone(self.order.admin_email_sent_at)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.stock(), 8)  # e o estoque não baixou de novo

    def test_only_the_email_that_failed_is_retried(self):
        """O do cliente sai, o da produção falha: só o segundo volta depois."""
        real = mail.EmailMultiAlternatives.send

        def so_o_da_producao_falha(mensagem, *args, **kwargs):
            """O assunto da ordem de produção começa com "[JD PRINT] Novo pedido"."""
            if "Novo pedido" in (mensagem.subject or ""):
                raise RuntimeError("provedor fora do ar")
            return real(mensagem, *args, **kwargs)

        with mock.patch.object(mail.EmailMultiAlternatives, "send", so_o_da_producao_falha):
            services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.confirmation_email_sent_at)
        self.assertIsNone(self.order.admin_email_sent_at)

        mail.outbox = []
        services.confirm_payment(self.order)
        self.order.refresh_from_db()

        self.assertIsNotNone(self.order.admin_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)  # só o que faltava


class PaymentFailureTests(RecoveryBase):
    """Falha no pagamento: nada depois dele acontece."""

    def test_nothing_runs_when_the_payment_does_not_go_through(self):
        services.register_payment_failure(self.order, reason="cartão recusado")
        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.FAILED)
        self.assertIsNone(self.order.stock_applied_at)
        self.assertIsNone(self.order.confirmation_email_sent_at)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_later_success_still_completes_everything(self):
        services.register_payment_failure(self.order, reason="cartão recusado")
        services.confirm_payment(self.order)
        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(self.order.stock_applied_at)
        self.assertEqual(self.stock(), 8)


class ResendEmailTests(RecoveryBase):
    """AUD-02 — o administrador reenvia o que falhou."""

    def test_confirmation_is_not_resent_automatically(self):
        services.confirm_payment(self.order)
        mail.outbox = []

        services.confirm_payment(self.order)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_administrator_can_resend_the_confirmation(self):
        services.confirm_payment(self.order)
        mail.outbox = []

        self.assertTrue(services.resend_email(self.order, "confirmation"))
        self.assertEqual(len(mail.outbox), 1)

    def test_resending_works_even_when_the_mark_is_filled(self):
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        antes = self.order.confirmation_email_sent_at
        mail.outbox = []

        services.resend_email(self.order, "confirmation")
        self.order.refresh_from_db()

        self.assertEqual(len(mail.outbox), 1)
        self.assertGreaterEqual(self.order.confirmation_email_sent_at, antes)

    def test_resending_works_when_the_first_attempt_failed(self):
        with mock.patch("apps.orders.emails._send", return_value=False):
            services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertIsNone(self.order.confirmation_email_sent_at)

        services.resend_email(self.order, "confirmation")
        self.order.refresh_from_db()

        self.assertIsNotNone(self.order.confirmation_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_admin_order_can_be_resent(self):
        services.confirm_payment(self.order)
        mail.outbox = []

        self.assertTrue(services.resend_email(self.order, "admin"))
        self.assertEqual(len(mail.outbox), 1)

    def test_the_shipping_notice_can_be_resent(self):
        services.confirm_payment(self.order)
        services.mark_shipped(self.order, "TRACK-1")
        mail.outbox = []

        self.assertTrue(services.resend_email(self.order, "shipped"))
        self.assertEqual(len(mail.outbox), 1)

    def test_resending_is_logged(self):
        services.confirm_payment(self.order)
        staff = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jdprint.test", password="x"
        )

        services.resend_email(self.order, "confirmation", user=staff)

        entrada = self.order.history.filter(event=OrderEvent.EMAIL_RESENT).get()
        self.assertEqual(entrada.created_by, staff)
        self.assertIn("confirmação", entrada.message)
        self.assertFalse(entrada.is_customer_visible)

    def test_a_failed_resend_is_logged_too(self):
        services.confirm_payment(self.order)

        with mock.patch("apps.orders.emails._send", return_value=False):
            self.assertFalse(services.resend_email(self.order, "confirmation"))

        entrada = self.order.history.filter(event=OrderEvent.EMAIL_RESENT).get()
        self.assertIn("Falha", entrada.message)

    def test_resending_changes_nothing_else(self):
        """Reenviar um e-mail é reenviar um e-mail."""
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        antes = {
            "payment_status": self.order.payment_status,
            "status": self.order.status,
            "fulfillment_status": self.order.fulfillment_status,
            "total": self.order.total,
            "stock_applied_at": self.order.stock_applied_at,
        }
        estoque = self.stock()

        services.resend_email(self.order, "confirmation")
        self.order.refresh_from_db()

        for campo, valor in antes.items():
            self.assertEqual(getattr(self.order, campo), valor, campo)
        self.assertEqual(self.stock(), estoque)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            services.resend_email(self.order, "boletim-informativo")


class FulfillmentHistoryTests(RecoveryBase):
    """AUD-04 — mudar a produção deixa rastro, e o absurdo é recusado."""

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jdprint.test", password="senha-de-teste"
        )

    def test_moving_forward_is_recorded(self):
        services.change_fulfillment_status(
            self.order, FulfillmentStatus.IN_PRODUCTION, user=self.staff
        )

        entrada = self.order.history.filter(event=OrderEvent.STATUS_CHANGED).get()
        self.assertEqual(entrada.created_by, self.staff)
        self.assertIn("Não iniciado", entrada.message)
        self.assertIn("Em produção", entrada.message)

    def test_the_new_status_is_saved(self):
        services.change_fulfillment_status(self.order, FulfillmentStatus.READY)
        self.order.refresh_from_db()

        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.READY)

    def test_no_change_no_record(self):
        self.assertFalse(
            services.change_fulfillment_status(
                self.order, FulfillmentStatus.NOT_STARTED, user=self.staff
            )
        )
        self.assertEqual(self.order.history.filter(event=OrderEvent.STATUS_CHANGED).count(), 0)

    def test_going_back_one_step_is_allowed(self):
        """Marquei 'enviado' cedo demais: voltar um passo é correção."""
        services.change_fulfillment_status(self.order, FulfillmentStatus.SHIPPED)

        self.assertTrue(
            services.change_fulfillment_status(
                self.order, FulfillmentStatus.READY, user=self.staff
            )
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.READY)

    def test_a_big_regression_is_refused(self):
        """Entregue → Não iniciado não é correção: é a lista clicada errado."""
        services.change_fulfillment_status(self.order, FulfillmentStatus.DELIVERED)

        with self.assertRaises(services.StatusChangeRefused):
            services.change_fulfillment_status(
                self.order, FulfillmentStatus.NOT_STARTED, user=self.staff
            )

    def test_a_refused_regression_changes_nothing(self):
        services.change_fulfillment_status(self.order, FulfillmentStatus.DELIVERED)
        registros = self.order.history.filter(event=OrderEvent.STATUS_CHANGED).count()

        with self.assertRaises(services.StatusChangeRefused):
            services.change_fulfillment_status(self.order, FulfillmentStatus.NOT_STARTED)

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.DELIVERED)
        self.assertEqual(
            self.order.history.filter(event=OrderEvent.STATUS_CHANGED).count(), registros
        )

    def test_jumping_forward_is_allowed(self):
        """Pular etapas para a frente é rotina: pedido pequeno sai no mesmo dia."""
        self.assertTrue(
            services.change_fulfillment_status(self.order, FulfillmentStatus.SHIPPED)
        )

    def test_the_admin_records_the_change(self):
        self.client.force_login(self.staff)
        url = reverse("admin:orders_order_change", args=[self.order.pk])
        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)

        self.client.post(
            url,
            self.admin_payload(fulfillment_status=FulfillmentStatus.IN_PRODUCTION),
            follow=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.IN_PRODUCTION)
        entrada = self.order.history.filter(event=OrderEvent.STATUS_CHANGED).first()
        self.assertIsNotNone(entrada)
        self.assertEqual(entrada.created_by, self.staff)

    def test_the_admin_refuses_a_big_regression(self):
        services.change_fulfillment_status(self.order, FulfillmentStatus.DELIVERED)
        self.client.force_login(self.staff)

        resposta = self.client.post(
            reverse("admin:orders_order_change", args=[self.order.pk]),
            self.admin_payload(fulfillment_status=FulfillmentStatus.NOT_STARTED),
            follow=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.DELIVERED)
        self.assertContains(resposta, "salto grande demais")

    def admin_payload(self, **overrides):
        """O formulário do pedido, com os inlines vazios que o admin exige."""
        dados = {
            "status": self.order.status,
            "payment_status": self.order.payment_status,
            "fulfillment_status": self.order.fulfillment_status,
            "tracking_number": "",
            "is_gift": "",
            "gift_message": "",
            "cancellation_status": self.order.cancellation_status,
            "cancellation_decision_note": "",
        }
        for prefixo, total in (
            ("items", self.order.items.count()),
            ("addresses", self.order.addresses.count()),
            ("payments", self.order.payments.count()),
            ("history", self.order.history.count()),
            ("notes", 0),
        ):
            dados[f"{prefixo}-TOTAL_FORMS"] = str(total)
            dados[f"{prefixo}-INITIAL_FORMS"] = str(total)
            dados[f"{prefixo}-MIN_NUM_FORMS"] = "0"
            dados[f"{prefixo}-MAX_NUM_FORMS"] = "1000"
        dados.update(overrides)
        return dados


class AdminEmailStatusTests(RecoveryBase):
    """§6 — dá para olhar o pedido e saber quem foi avisado."""

    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jdprint.test", password="senha-de-teste"
        )
        self.client.force_login(self.staff)

    def page(self):
        return self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))

    def test_a_pending_order_shows_nothing_sent_yet(self):
        resposta = self.page()

        self.assertContains(resposta, "AVISOS ENVIADOS")
        self.assertContains(resposta, "ainda não enviado")

    def test_a_paid_order_shows_the_two_confirmations(self):
        services.confirm_payment(self.order)
        resposta = self.page()

        self.assertContains(resposta, "Confirmação ao cliente")
        self.assertContains(resposta, "Ordem de produção")
        self.assertContains(resposta, "enviado em")

    def test_a_failed_email_is_flagged_in_red(self):
        with mock.patch("apps.orders.emails._send", return_value=False):
            services.confirm_payment(self.order)

        resposta = self.page()
        self.assertContains(resposta, "NÃO enviado")

    def test_the_shipping_notice_is_not_flagged_before_shipping(self):
        """Ainda não saiu: não ter ido é o certo, não um problema."""
        services.confirm_payment(self.order)
        resposta = self.page()

        self.assertContains(resposta, "Aviso de envio")
        self.assertContains(resposta, "ainda não enviado")

    def test_the_changelist_flags_the_order_that_needs_attention(self):
        with mock.patch("apps.orders.emails._send", return_value=False):
            services.confirm_payment(self.order)

        resposta = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertContains(resposta, "Confirmação ao cliente")

    def test_the_changelist_opens_with_a_cancellation_requested(self):
        """`format_html` sem argumento derrubava a listagem justamente aqui."""
        services.request_cancellation(self.order, "Comprei errado", user=self.user)

        resposta = self.client.get(reverse("admin:orders_order_changelist"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "solicitado")
