"""O checkout como o cliente o percorre: HTTP, formulário e autorização.

Duas coisas concentram a atenção aqui:

* **quem paga tem conta** — mas o visitante nunca é jogado para fora com a
  compra pela metade;
* **nada que vem do navegador entra numa soma** — preço, frete e imposto são
  sempre recalculados no servidor.
"""

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders.models import Order, OrderStatus, PaymentStatus
from apps.orders.payments.base import PaymentStart

CHECKOUT = reverse("cart:checkout")


class FakeStart:
    """Substitui a ida à Stripe: nenhum teste toca a rede."""

    def __init__(self, url="https://checkout.stripe.test/sessao"):
        self.url = url

    def __call__(self, order, request=None):
        from apps.orders.models import Payment, PaymentState

        payment = Payment.objects.create(
            order=order, provider="stripe", amount=order.total, currency=order.currency,
            provider_session_id=f"cs_test_{order.pk}", status=PaymentState.PROCESSING,
        )
        return PaymentStart(redirect_url=self.url, payment=payment)


class CheckoutBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")

        self.user = make_user(username="diego3d", email="diego@example.com")
        self.customer = self.user.customer
        self.address = make_address(self.customer, self.country)

        self.product = make_product(
            sku="P1", name="Vaso Espiral", price=Decimal("19.90"), stock_quantity=10
        )
        self.product.weight_grams = Decimal("300")
        self.product.save()

    def add_to_cart(self, quantity=1):
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": quantity})

    def payload(self, **overrides):
        data = {
            "shipping_address": self.address.pk,
            "billing_same_as_shipping": "1",
            "shipping_method": self.method.pk,
        }
        data.update(overrides)
        return data


class VisitorTests(CheckoutBase):
    """Visitante monta carrinho à vontade; só não fecha a compra."""

    def test_checkout_answers_the_visitor(self):
        response = self.client.get(CHECKOUT)

        self.assertEqual(response.status_code, 200)

    def test_visitor_sees_the_invitation_to_identify(self):
        self.add_to_cart()

        response = self.client.get(CHECKOUT)

        self.assertContains(response, reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:register"))

    def test_visitor_still_sees_the_order_summary(self):
        """Nada de beco sem saída: a compra continua à vista."""
        self.add_to_cart(2)

        response = self.client.get(CHECKOUT)

        self.assertContains(response, "Vaso Espiral")
        self.assertContains(response, "39,80")

    def test_visitor_cannot_create_an_order(self):
        self.add_to_cart()

        response = self.client.post(CHECKOUT, self.payload())

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertFalse(Order.objects.exists())

    def test_visitor_keeps_the_cart(self):
        self.add_to_cart()
        self.client.get(CHECKOUT)

        response = self.client.get(reverse("cart:detail"))
        self.assertContains(response, "Vaso Espiral")

    def test_empty_cart_shows_the_empty_state(self):
        response = self.client.get(CHECKOUT)

        self.assertContains(response, "Ver modelos")


class CustomerCheckoutTests(CheckoutBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_form_is_offered_to_the_customer(self):
        self.add_to_cart()

        response = self.client.get(CHECKOUT)

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["form"])
        self.assertContains(response, self.address.street)

    def test_delivery_options_are_shown(self):
        self.add_to_cart()

        response = self.client.get(CHECKOUT)

        self.assertContains(response, "Standard")
        self.assertContains(response, "5,90")

    def test_summary_shows_the_estimate(self):
        self.product.production_lead_time_days = 3
        self.product.save()
        self.add_to_cart()

        response = self.client.get(CHECKOUT)

        self.assertContains(response, "5–6")

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_order_is_created_and_the_customer_goes_to_the_gateway(self):
        self.add_to_cart(2)

        response = self.client.post(CHECKOUT, self.payload())

        order = Order.objects.get()
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.subtotal, Decimal("39.80"))
        self.assertEqual(order.total, Decimal("45.70"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://checkout.stripe.test/sessao")

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_cart_is_emptied_only_after_the_order_exists(self):
        self.add_to_cart()

        self.client.post(CHECKOUT, self.payload())

        self.assertTrue(Order.objects.exists())
        self.assertEqual(self.client.get(reverse("cart:detail")).context["cart"].total_quantity, 0)

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_order_starts_unpaid(self):
        """Chegar ao gateway não é pagar."""
        self.add_to_cart()

        self.client.post(CHECKOUT, self.payload())

        order = Order.objects.get()
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.payment_status, PaymentStatus.PENDING)
        self.assertIsNone(order.paid_at)

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_gift_flag_travels_to_the_order(self):
        self.add_to_cart()

        self.client.post(CHECKOUT, self.payload(is_gift="1"))

        self.assertTrue(Order.objects.get().is_gift)

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_separate_billing_address_is_used(self):
        billing = make_address(
            self.customer, self.country, label="Empresa", street="Rue Facture 9",
            company_name="JD SRL",
        )
        self.add_to_cart()

        self.client.post(
            CHECKOUT,
            self.payload(billing_same_as_shipping="", billing_address=billing.pk),
        )

        order = Order.objects.get()
        self.assertEqual(order.shipping_address.street, self.address.street)
        self.assertEqual(order.billing_address.street, "Rue Facture 9")

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_customer_note_is_kept(self):
        self.add_to_cart()

        self.client.post(CHECKOUT, self.payload(customer_note="Sem embalagem, por favor."))

        self.assertEqual(Order.objects.get().customer_note, "Sem embalagem, por favor.")

    def test_no_address_no_order(self):
        self.address.delete()
        self.add_to_cart()

        response = self.client.post(CHECKOUT, {"shipping_method": self.method.pk})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_empty_cart_creates_nothing(self):
        response = self.client.post(CHECKOUT, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())


class ForgedInputTests(CheckoutBase):
    """Nada que vem do navegador decide preço, dono ou destino."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.add_to_cart()

    def test_another_customers_address_is_refused(self):
        stranger = make_user(username="outra", email="outra@example.com")
        theirs = make_address(stranger.customer, self.country, street="Rua Alheia 1")

        response = self.client.post(CHECKOUT, self.payload(shipping_address=theirs.pk))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_method_that_does_not_serve_the_destination_is_refused(self):
        other = make_method(code="express", name="Express")  # sem tarifa cadastrada

        response = self.client.post(CHECKOUT, self.payload(shipping_method=other.pk))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_nonexistent_address_is_refused(self):
        response = self.client.post(CHECKOUT, self.payload(shipping_address=999999))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    @mock.patch("apps.orders.payments.stripe_provider.StripeProvider.start", new=FakeStart())
    def test_price_comes_from_the_catalogue_not_from_the_form(self):
        response = self.client.post(
            CHECKOUT, self.payload(total="1.00", subtotal="1.00", shipping_total="0.00")
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.subtotal, Decimal("19.90"))
        self.assertEqual(order.shipping_total, Decimal("5.90"))

    def test_stock_is_revalidated_at_the_last_moment(self):
        """O carrinho pode estar aberto há três dias."""
        self.product.stock_quantity = 0
        self.product.save()

        response = self.client.post(CHECKOUT, self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())


class DeliveryPartialTests(CheckoutBase):
    """Trocar de endereço recalcula frete e imposto sem recarregar a página."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.add_to_cart()

        self.france = make_country("FR", "França", vat_rate="20.00")
        self.french_address = make_address(
            self.customer, self.france, label="Paris", city="Paris", postal_code="75001"
        )
        make_rate(self.method, self.france, 0, 5000, "12.90")

    def test_partial_returns_only_the_delivery_block(self):
        response = self.client.get(
            CHECKOUT, {"partial": "delivery"}, **{"HTTP_HX_REQUEST": "true"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "orders/_delivery.html")
        self.assertNotContains(response, "<html")

    def test_changing_the_country_changes_the_price(self):
        response = self.client.get(
            CHECKOUT,
            {"partial": "delivery", "shipping_address": self.french_address.pk},
            **{"HTTP_HX_REQUEST": "true"},
        )

        self.assertContains(response, "12,90")

    def test_summary_comes_back_out_of_band(self):
        response = self.client.get(
            CHECKOUT, {"partial": "delivery"}, **{"HTTP_HX_REQUEST": "true"}
        )

        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, "checkout-summary")

    def test_country_without_rate_offers_nothing(self):
        germany = make_country("DE", "Alemanha")
        german_address = make_address(self.customer, germany, label="Berlim", city="Berlin")

        response = self.client.get(
            CHECKOUT,
            {"partial": "delivery", "shipping_address": german_address.pk},
            **{"HTTP_HX_REQUEST": "true"},
        )

        self.assertContains(response, "Ainda não temos tarifa cadastrada")
