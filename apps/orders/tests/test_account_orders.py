"""A área de pedidos do cliente — e a fronteira entre um cliente e outro.

Um pedido carrega endereço, telefone e o que a pessoa comprou. O número é
difícil de adivinhar, mas número em URL não é credencial: a consulta é sempre
filtrada pelo dono.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import CartLine
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
from apps.orders.models import CancellationStatus, FulfillmentStatus, OrderEvent


def make_order(user, country, method, product, **overrides):
    address = user.customer.addresses.first() or make_address(user.customer, country)
    options = {
        "customer": user.customer,
        "lines": [
            CartLine(
                key=f"{product.pk}:0:-",
                product=product,
                variant=product.default_variant,
                quantity=1,
            )
        ],
        "shipping_address": address,
        "billing_address": address,
        "shipping_method": method,
    }
    options.update(overrides)
    return services.create_order(**options)


class OrdersBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.product = make_product(
            sku="P1", name="Vaso Espiral", price=Decimal("19.90"), stock_quantity=10
        )

        self.owner = make_user(username="dono", email="dono@example.com")
        self.intruder = make_user(username="intruso", email="intruso@example.com")
        make_address(self.owner.customer, self.country)
        make_address(self.intruder.customer, self.country, city="Antwerpen")

        self.order = make_order(self.owner, self.country, self.method, self.product)


class OrderListTests(OrdersBase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("accounts:orders"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_customer_sees_their_own_order(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:orders"))

        self.assertContains(response, self.order.number)

    def test_customer_does_not_see_someone_elses_order(self):
        self.client.force_login(self.intruder)

        response = self.client.get(reverse("accounts:orders"))

        self.assertNotContains(response, self.order.number)

    def test_empty_state_when_there_are_no_orders(self):
        self.client.force_login(self.intruder)

        response = self.client.get(reverse("accounts:orders"))

        self.assertContains(response, "Ainda não há pedidos")


class OrderDetailTests(OrdersBase):
    def url(self, order=None):
        return reverse("orders:detail", kwargs={"number": (order or self.order).number})

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_owner_sees_the_details(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertContains(response, self.order.number)
        self.assertContains(response, "Vaso Espiral")
        self.assertContains(response, "25,80")

    def test_another_customer_gets_a_404(self):
        """Nunca 403: um 403 confirmaria que o pedido existe."""
        self.client.force_login(self.intruder)

        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 404)

    def test_unknown_number_is_a_404(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("orders:detail", kwargs={"number": "JD-2026-999999"}))

        self.assertEqual(response.status_code, 404)

    def test_internal_history_is_not_shown_to_the_customer(self):
        self.order.log(OrderEvent.NOTE, "Combinar fonte maior com a produção.", visible=False)
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertNotContains(response, "Combinar fonte maior")

    def test_visible_history_is_shown(self):
        """O acompanhamento fala com o cliente, não com a operação.

        Antes a tela imprimia o rótulo técnico do evento ("Pedido criado") e o
        `message` interno logo abaixo — e é por ali que sairiam o IBAN
        mascarado da conta e o nome do arquivo que o cliente mandou. Agora sai
        a frase escrita para ele.
        """
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertContains(response, "Recebemos a sua encomenda")

    def test_the_internal_message_never_reaches_the_customer(self):
        """A anotação da equipe fica no Admin. Aqui, nunca."""
        self.order.log(
            OrderEvent.TRANSFER_DETAILS_SENT,
            "Dados bancários enviados ao cliente (conta: Interna · LT14 ···· 2545)",
        )
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertNotContains(response, "LT14")
        self.assertNotContains(response, "conta: Interna")
        self.assertContains(response, "Enviámos para o seu e-mail os dados")

    def test_the_tracking_code_is_the_exception(self):
        """No envio o detalhe **é** a informação que o cliente quer."""
        self.order.log(OrderEvent.SHIPPED, "BE123456789")
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertContains(response, "BE123456789")
        self.assertContains(response, "Código de rastreio")

    def test_an_event_without_a_customer_message_draws_no_empty_line(self):
        self.order.log(OrderEvent.STATUS_CHANGED, "não iniciado → em produção")
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertNotContains(response, "não iniciado")

    def test_addresses_come_from_the_snapshot(self):
        self.client.force_login(self.owner)
        address = self.owner.customer.addresses.first()
        address.street = "Rua Mudada 99"
        address.save()

        response = self.client.get(self.url())

        self.assertContains(response, "Rue du Test 12")
        self.assertNotContains(response, "Rua Mudada 99")


class CancellationRequestTests(OrdersBase):
    def url(self):
        return reverse("orders:cancel", kwargs={"number": self.order.number})

    def test_another_customer_cannot_open_the_form(self):
        self.client.force_login(self.intruder)

        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_another_customer_cannot_request_the_cancellation(self):
        self.client.force_login(self.intruder)

        response = self.client.post(self.url(), {"reason": "Quero cancelar o pedido alheio"})

        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.NONE)

    def test_owner_can_request_it(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url(), {"reason": "Comprei o tamanho errado"})

        self.assertRedirects(response, self.order.get_absolute_url())
        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_reason, "Comprei o tamanho errado")

    def test_reason_is_required(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url(), {"reason": ""})

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.NONE)

    def test_an_unpaid_order_is_cancelled_right_away(self):
        """Nada foi cobrado: não há o que analisar, e a página já responde."""
        self.client.force_login(self.owner)

        self.client.post(self.url(), {"reason": "Mudei de ideia"})

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)

    def test_a_paid_order_in_production_waits_for_a_person(self):
        from apps.orders import services

        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        services.change_fulfillment_status(self.order, FulfillmentStatus.IN_PRODUCTION)
        self.client.force_login(self.owner)

        self.client.post(self.url(), {"reason": "Mudei de ideia"})

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertNotEqual(self.order.status, "cancelled")

    def test_a_shipped_order_can_still_be_asked_about(self):
        """O prazo legal só começa na entrega — enviado ainda dá para pedir."""
        from apps.orders import services

        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        self.order.fulfillment_status = FulfillmentStatus.SHIPPED
        self.order.save(update_fields=["fulfillment_status"])
        self.client.force_login(self.owner)

        response = self.client.post(self.url(), {"reason": "Quero devolver"})

        self.assertRedirects(response, self.order.get_absolute_url())
        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)


class RetryPaymentTests(OrdersBase):
    def url(self):
        return reverse("orders:retry_payment", kwargs={"number": self.order.number})

    def test_another_customer_cannot_pay_someone_elses_order(self):
        self.client.force_login(self.intruder)

        response = self.client.post(self.url())

        self.assertEqual(response.status_code, 404)

    def test_retry_opens_a_page_that_asks_again(self):
        """"Pagar agora" deixou de ser um POST cego.

        Um pedido pode ficar dias esperando, e nesse intervalo o estoque, as
        formas de pagamento e a conta bancária mudam sozinhos. A tela existe
        para perguntar de novo — ver `PagarAgoraTests`.
        """
        self.client.force_login(self.owner)

        resposta = self.client.get(self.url())

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Forma de pagamento")


class ConfirmationPageTests(OrdersBase):
    def url(self):
        return reverse("orders:confirmation", kwargs={"number": self.order.number})

    def test_opening_the_page_does_not_confirm_anything(self):
        """A URL de sucesso é só uma navegação: qualquer um pode abri-la."""
        self.client.force_login(self.owner)

        self.client.get(self.url())

        self.order.refresh_from_db()
        self.assertFalse(self.order.is_paid)

    def test_an_unpaid_card_order_shows_that_we_are_waiting(self):
        """O ramo do gateway: o dinheiro já saiu e falta a confirmação chegar.

        O pedido tem que dizer que é de cartão — a página escolhe a mensagem
        por `payment_method`, e o cenário desta classe nasce com o provedor do
        ambiente. Antes isso vinha de graça do padrão do settings; passou a ser
        dito em voz alta quando o `.env` alinhou o provedor à transferência.

        O ramo da transferência tem cobertura própria em
        `test_payment_flow.PaginaDeSucessoTests`.
        """
        self.order.payment_method = "card"
        self.order.save(update_fields=["payment_method"])
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertContains(response, "Estamos confirmando")

    def test_paid_order_shows_the_confirmation(self):
        services.confirm_payment(self.order)
        self.client.force_login(self.owner)

        response = self.client.get(self.url())

        self.assertContains(response, "Pedido confirmado!")
        self.assertContains(response, self.order.number)

    def test_another_customer_cannot_open_it(self):
        self.client.force_login(self.intruder)

        self.assertEqual(self.client.get(self.url()).status_code, 404)
