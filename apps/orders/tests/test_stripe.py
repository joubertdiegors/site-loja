"""Stripe: sessão de pagamento, assinatura do webhook e idempotência.

Nenhum teste toca a rede. A criação da sessão é interceptada no ponto exato em
que a biblioteca da Stripe seria chamada, e a assinatura do webhook é gerada
aqui com o **mesmo** HMAC que a Stripe usa — de modo que a verificação
exercitada é a real (``stripe.Webhook.construct_event``), não uma imitação.

O que estes testes protegem, em ordem de gravidade:

1. um POST sem assinatura válida **não** confirma pedido nenhum;
2. o mesmo evento entregue duas vezes não baixa estoque nem manda e-mail duas
   vezes;
3. chegar à página de sucesso não é pagar.
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cart.cart import CartLine
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
    OrderStatus,
    Payment,
    PaymentState,
    PaymentStatus,
    WebhookEvent,
)
from apps.orders.payments import PaymentError, get_provider
from apps.orders.payments.stripe_provider import StripeProvider

WEBHOOK_URL = reverse("stripe_webhook")
SECRET = "whsec_teste_jdprint"


def sign(payload: str, secret: str = SECRET, timestamp: int | None = None) -> str:
    """Cabeçalho ``Stripe-Signature`` legítimo para este corpo.

    É o mesmo esquema da Stripe: HMAC-SHA256 sobre ``timestamp.corpo``.
    """
    timestamp = timestamp or int(time.time())
    signed = f"{timestamp}.{payload}".encode()
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def checkout_completed(order, event_id="evt_1", payment_status="paid"):
    return {
        "id": event_id,
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_test_{order.pk}",
                "object": "checkout.session",
                "payment_status": payment_status,
                "payment_intent": f"pi_test_{order.pk}",
                "client_reference_id": order.number,
                "payment_method_types": ["card"],
                "metadata": {"order_number": order.number},
            }
        },
    }


#: A loja destes testes roda na Stripe. Estava implícito no padrão do
#: `settings.py` e vinha de graça enquanto o `.env` local não declarava
#: `PAYMENT_PROVIDER`; com o `.env` alinhado ao modelo oficial (transferência),
#: o implícito virou o provedor errado. Um teste da Stripe diz que testa a
#: Stripe.
@override_settings(PAYMENT_PROVIDER="stripe")
class StripeTestCase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")

        self.user = make_user(username="diego3d", email="diego@example.com")
        self.address = make_address(self.user.customer, self.country)
        self.product = make_product(
            sku="P1", name="Vaso Espiral", price=Decimal("19.90"), stock_quantity=10
        )
        self.variant = self.product.default_variant

        self.order = services.create_order(
            customer=self.user.customer,
            lines=[CartLine(key="k", product=self.product, variant=self.variant, quantity=2)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
            language="pt-br",
        )
        self.payment = Payment.objects.create(
            order=self.order,
            provider="stripe",
            amount=self.order.total,
            currency=self.order.currency,
            provider_session_id=f"cs_test_{self.order.pk}",
            status=PaymentState.PROCESSING,
        )
        mail.outbox = []

    def post_event(self, event, secret=SECRET, signature=None):
        payload = json.dumps(event)
        return self.client.post(
            WEBHOOK_URL,
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature if signature is not None else sign(payload, secret),
        )


# ---------------------------------------------------------------------------
# Criação da sessão
# ---------------------------------------------------------------------------


@override_settings(STRIPE_SECRET_KEY="sk_test_falsa", SITE_URL="https://jd-print.test")
class CheckoutSessionTests(StripeTestCase):
    def test_provider_is_stripe(self):
        self.assertIsInstance(get_provider(), StripeProvider)

    def test_without_a_key_the_payment_is_unavailable(self):
        """Melhor avisar do que fingir que existe um gateway."""
        with override_settings(STRIPE_SECRET_KEY=""):
            provider = StripeProvider()

            self.assertFalse(provider.is_configured)
            with self.assertRaises(PaymentError):
                provider.start(self.order)

    def test_session_is_created_with_the_order_amounts(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "cs_test_novo", "url": "https://checkout.stripe.test/x", "payment_intent": None}

        with mock.patch("stripe.checkout.Session.create", side_effect=fake_create):
            start = StripeProvider().start(self.order)

        self.assertEqual(start.redirect_url, "https://checkout.stripe.test/x")
        self.assertEqual(captured["mode"], "payment")
        self.assertEqual(captured["client_reference_id"], self.order.number)
        self.assertEqual(captured["line_items"][0]["quantity"], 2)
        self.assertEqual(captured["line_items"][0]["price_data"]["unit_amount"], 1990)

    def test_shipping_goes_as_a_shipping_option(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "cs_1", "url": "https://x.test", "payment_intent": None}

        with mock.patch("stripe.checkout.Session.create", side_effect=fake_create):
            StripeProvider().start(self.order)

        rate = captured["shipping_options"][0]["shipping_rate_data"]
        self.assertEqual(rate["fixed_amount"]["amount"], 590)
        self.assertEqual(rate["fixed_amount"]["currency"], "eur")

    def test_return_urls_come_from_site_url(self):
        """Nunca do cabeçalho Host: bastaria um Host falso para desviar o cliente."""
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "cs_1", "url": "https://x.test", "payment_intent": None}

        with mock.patch("stripe.checkout.Session.create", side_effect=fake_create):
            StripeProvider().start(self.order)

        self.assertTrue(captured["success_url"].startswith("https://jd-print.test/"))
        self.assertIn(self.order.number, captured["success_url"])
        self.assertTrue(captured["cancel_url"].startswith("https://jd-print.test/"))

    def test_idempotency_key_prevents_a_double_charge(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "cs_1", "url": "https://x.test", "payment_intent": None}

        with mock.patch("stripe.checkout.Session.create", side_effect=fake_create):
            StripeProvider().start(self.order)

        self.assertIn(self.order.number, captured["idempotency_key"])

    def test_locale_follows_the_order_language(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "cs_1", "url": "https://x.test", "payment_intent": None}

        self.order.language = "fr"
        self.order.save()

        with mock.patch("stripe.checkout.Session.create", side_effect=fake_create):
            StripeProvider().start(self.order)

        self.assertEqual(captured["locale"], "fr")

    def test_failure_marks_the_attempt_and_does_not_leak_the_error(self):
        with mock.patch("stripe.checkout.Session.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(PaymentError) as raised:
                StripeProvider().start(self.order)

        self.assertNotIn("boom", str(raised.exception))
        self.assertTrue(
            Payment.objects.filter(order=self.order, status=PaymentState.FAILED).exists()
        )

    def test_no_card_data_is_ever_stored(self):
        """O cartão é digitado no domínio da Stripe. Aqui só ficam ids opacos."""
        columns = {field.name for field in Payment._meta.fields}

        self.assertTrue(
            columns.isdisjoint({"card_number", "cvv", "cvc", "expiry", "pan", "card_holder"})
        )


# ---------------------------------------------------------------------------
# Webhook: assinatura
# ---------------------------------------------------------------------------


@override_settings(STRIPE_SECRET_KEY="sk_test_falsa", STRIPE_WEBHOOK_SECRET=SECRET)
class WebhookSignatureTests(StripeTestCase):
    def test_valid_signature_confirms_the_order(self):
        response = self.post_event(checkout_completed(self.order))

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_missing_signature_is_refused(self):
        response = self.post_event(checkout_completed(self.order), signature="")

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)

    def test_wrong_secret_is_refused(self):
        """Sem isto, quem descobrisse a URL declararia pedidos como pagos."""
        response = self.post_event(checkout_completed(self.order), secret="whsec_do_atacante")

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)

    def test_tampered_body_is_refused(self):
        event = checkout_completed(self.order)
        payload = json.dumps(event)
        signature = sign(payload)

        event["data"]["object"]["payment_status"] = "paid"
        event["id"] = "evt_adulterado"
        response = self.client.post(
            WEBHOOK_URL,
            data=json.dumps(event),  # corpo diferente do que foi assinado
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)

    def test_old_signature_is_refused(self):
        payload = json.dumps(checkout_completed(self.order))
        old = sign(payload, timestamp=int(time.time()) - 3600)

        response = self.client.post(
            WEBHOOK_URL, data=payload, content_type="application/json",
            HTTP_STRIPE_SIGNATURE=old,
        )

        self.assertEqual(response.status_code, 400)

    def test_without_a_configured_secret_nothing_passes(self):
        with override_settings(STRIPE_WEBHOOK_SECRET=""):
            response = self.post_event(checkout_completed(self.order))

        self.assertEqual(response.status_code, 400)

    def test_webhook_only_accepts_post(self):
        self.assertEqual(self.client.get(WEBHOOK_URL).status_code, 405)

    def test_webhook_has_no_language_prefix(self):
        """A Stripe não é um navegador: nada de /fr/ na URL de callback."""
        self.assertEqual(WEBHOOK_URL, "/pagamento/stripe/webhook/")


# ---------------------------------------------------------------------------
# Webhook: idempotência e efeitos
# ---------------------------------------------------------------------------


@override_settings(STRIPE_SECRET_KEY="sk_test_falsa", STRIPE_WEBHOOK_SECRET=SECRET)
class WebhookIdempotencyTests(StripeTestCase):
    def test_the_same_event_twice_changes_nothing_twice(self):
        event = checkout_completed(self.order, event_id="evt_repetido")

        first = self.post_event(event)
        second = self.post_event(event)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(WebhookEvent.objects.filter(event_id="evt_repetido").count(), 1)

    def test_stock_falls_only_once(self):
        event = checkout_completed(self.order, event_id="evt_estoque")

        self.post_event(event)
        self.post_event(event)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

    def test_emails_are_sent_only_once(self):
        event = checkout_completed(self.order, event_id="evt_email")

        self.post_event(event)
        count_after_first = len(mail.outbox)
        self.post_event(event)

        self.assertEqual(len(mail.outbox), count_after_first)

    def test_a_different_event_for_a_paid_order_is_harmless(self):
        """Dois eventos distintos (completed + async_succeeded) podem chegar."""
        self.post_event(checkout_completed(self.order, event_id="evt_a"))
        paid_at = Order.objects.get(pk=self.order.pk).paid_at

        second = checkout_completed(self.order, event_id="evt_b")
        second["type"] = "checkout.session.async_payment_succeeded"
        self.post_event(second)

        order = Order.objects.get(pk=self.order.pk)
        self.assertEqual(order.paid_at, paid_at)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

    def test_unfinished_session_does_not_confirm(self):
        event = checkout_completed(self.order, event_id="evt_unpaid", payment_status="unpaid")

        response = self.post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)

    def test_unknown_event_type_is_accepted_and_ignored(self):
        event = checkout_completed(self.order, event_id="evt_outro")
        event["type"] = "customer.created"

        response = self.post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)

    def test_expired_session_marks_the_failure(self):
        event = checkout_completed(self.order, event_id="evt_exp", payment_status="unpaid")
        event["type"] = "checkout.session.expired"

        self.post_event(event)

        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.FAILED)
        self.assertEqual(self.payment.status, PaymentState.FAILED)

    def test_failure_keeps_the_order_alive_for_a_new_attempt(self):
        event = checkout_completed(self.order, event_id="evt_falha", payment_status="unpaid")
        event["type"] = "checkout.session.async_payment_failed"

        self.post_event(event)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertFalse(self.order.is_paid)

    def test_event_for_an_unknown_order_is_accepted_and_ignored(self):
        event = checkout_completed(self.order, event_id="evt_orfao")
        event["data"]["object"]["id"] = "cs_desconhecido"
        event["data"]["object"]["payment_intent"] = "pi_desconhecido"
        event["data"]["object"]["metadata"] = {"order_number": "JD-2026-999999"}
        event["data"]["object"]["client_reference_id"] = "JD-2026-999999"

        response = self.post_event(event)

        self.assertEqual(response.status_code, 200)


@override_settings(STRIPE_SECRET_KEY="sk_test_falsa", STRIPE_WEBHOOK_SECRET=SECRET)
class WebhookEffectTests(StripeTestCase):
    def test_confirmation_moves_the_three_states(self):
        self.post_event(checkout_completed(self.order))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        # A produção continua onde estava: pagar não é começar a imprimir.
        # Quem começa é a oficina, e é ela que marca — é essa distinção que
        # permite cancelar sozinho um pedido pago e ainda parado.
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.NOT_STARTED)

    def test_payment_row_is_updated(self):
        self.post_event(checkout_completed(self.order))

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentState.SUCCEEDED)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.payment.provider_payment_id, f"pi_test_{self.order.pk}")

    def test_history_records_the_payment(self):
        self.post_event(checkout_completed(self.order))

        events = list(self.order.history.values_list("event", flat=True))
        self.assertIn("paid", events)
        self.assertIn("confirmed", events)

    def test_customer_and_admin_are_both_notified(self):
        self.post_event(checkout_completed(self.order))

        recipients = [address for message in mail.outbox for address in message.to]
        self.assertIn("diego@example.com", recipients)
        self.assertEqual(len(mail.outbox), 2)

    def test_the_processed_event_is_linked_to_the_order(self):
        self.post_event(checkout_completed(self.order, event_id="evt_link"))

        record = WebhookEvent.objects.get(event_id="evt_link")
        self.assertEqual(record.order_id, self.order.pk)
        self.assertIsNotNone(record.processed_at)

    def test_a_crash_while_applying_lets_stripe_retry(self):
        """Se o processamento falhar, o evento não pode ficar marcado como feito."""
        with mock.patch(
            "apps.orders.payments.stripe_provider.StripeProvider.handle",
            side_effect=RuntimeError("falha"),
        ):
            response = self.post_event(checkout_completed(self.order, event_id="evt_crash"))

        self.assertEqual(response.status_code, 500)
        self.assertFalse(WebhookEvent.objects.filter(event_id="evt_crash").exists())
