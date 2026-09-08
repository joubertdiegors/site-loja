"""Auditoria 4A — o pedido criado é exatamente o que o cliente confirmou.

Regressões e garantias conferidas no fechamento do checkout:

* o botão de finalizar acompanha a troca de endereço via HTMX — estado e
  texto — em vez de ficar preso ao primeiro desenho (o blocker da etapa);
* nada que venha do navegador entra numa soma: preço, frete, total, imposto
  e método são recalculados no servidor;
* centavos: ``Decimal`` com ``ROUND_HALF_UP``, sem float, em qualquer
  combinação de preço e quantidade;
* a matriz de opções chega ao OrderItem com a variante, o SKU e o preço
  certos; o mesmo produto em variantes diferentes vira linhas diferentes;
* entrega: método inativo, transportadora inativa, tarifa inativa e país
  inativo não são oferecidos nem aceitos;
* estoque: sem reserva no carrinho; dois pedidos podem nascer para a última
  unidade e a confirmação do pagamento nunca deixa o saldo negativo;
* acesso: pedido e retomada de pagamento só pelo dono; pedido pago ou
  cancelado não «paga de novo»; POST sem CSRF é recusado.
"""

from decimal import Decimal

from django.core import mail
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import CustomerAddress
from apps.cart.models import CartItem
from apps.catalog.models import (
    Color,
    Material,
    ProductOption,
    ProductOptionValue,
    ProductStatus,
    ProductVariant,
)
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_carrier,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services
from apps.orders.models import Order, OrderItem, OrderStatus, PaymentStatus
from apps.shipping.models import ShippingRate

CHECKOUT = reverse("cart:checkout")
ADD = reverse("cart:add")


@override_settings(PAYMENT_PROVIDER="transfer", ORDER_ADMIN_EMAILS=["producao@jd-print.test"])
class AuditBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.be = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.be, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="cliente", email="cliente@example.com")
        self.customer = self.user.customer
        self.address = make_address(self.customer, self.be)
        self.client.force_login(self.user)

    def produto(self, sku, preco="10.00", estoque=9, **kw):
        return make_product(sku=sku, name=sku, price=Decimal(preco), stock_quantity=estoque, status=ProductStatus.ACTIVE, **kw)

    def add(self, produto, variante=None, quantidade=1, **extra):
        variante = variante or produto.default_variant
        return self.client.post(ADD, {"product_id": produto.pk, "variant_id": variante.pk, "quantity": quantidade, **extra})

    def checkout(self, **extra):
        dados = {"shipping_address": self.address.pk, "billing_same_as_shipping": "1", "shipping_method": self.method.pk, "payment_method": "transfer"}
        dados.update(extra)
        return self.client.post(CHECKOUT, dados)

    def partial(self, **params):
        return self.client.get(CHECKOUT, {"partial": "delivery", **params}, HTTP_HX_REQUEST="true")

    def pedido(self):
        return Order.objects.filter(customer=self.customer).order_by("-pk").first()

    def linhas(self):
        """As linhas do carrinho do cliente logado (o carrinho persistente)."""
        return list(CartItem.objects.filter(cart__user=self.user))


# ---------------------------------------------------------------------------
# O botão de finalizar e a troca de endereço (blocker da etapa)
# ---------------------------------------------------------------------------


class SubmitButtonFollowsTheAddressTests(AuditBase):
    def setUp(self):
        super().setUp()
        self.pt = make_country("PT", "Portugal", vat_rate="23.00")  # ativo, sem tarifa
        self.sem_tarifa = make_address(self.customer, self.pt, label="Lisboa", city="Lisboa", postal_code="1000-001")
        # O padrão de entrega passa a ser o endereço sem tarifa (o `save()`
        # promove o único endereço a padrão; por isso a troca é direta).
        CustomerAddress.objects.filter(pk=self.address.pk).update(is_default_shipping=False)
        CustomerAddress.objects.filter(pk=self.sem_tarifa.pk).update(is_default_shipping=True)
        self.add(self.produto("BOTAO"))

    def botao(self, html):
        inicio = html.index('id="checkout-submit"')
        return html[html.rindex("<button", 0, inicio):html.index("</button>", inicio)]

    def test_the_page_opens_disabled_when_the_default_address_has_no_rate(self):
        html = self.client.get(CHECKOUT).content.decode()
        botao = self.botao(html)
        self.assertIn("disabled", botao)
        self.assertIn("Confirmar pedido", botao)
        self.assertNotIn("hx-swap-oob", botao)

    def test_switching_to_an_address_with_a_rate_swaps_the_button_enabled(self):
        html = self.partial(shipping_address=self.address.pk).content.decode()
        botao = self.botao(html)
        self.assertIn('hx-swap-oob="true"', botao)
        self.assertNotIn("disabled", botao)
        self.assertIn("Confirmar pedido", botao)  # transferência: não «Pagar»
        self.assertIn("15,90", botao)  # 10,00 + 5,90

    def test_switching_back_to_an_address_without_a_rate_swaps_it_disabled(self):
        html = self.partial(shipping_address=self.sem_tarifa.pk).content.decode()
        botao = self.botao(html)
        self.assertIn('hx-swap-oob="true"', botao)
        self.assertIn("disabled", botao)

    def test_the_whole_flow_completes_after_the_switch(self):
        self.partial(shipping_address=self.address.pk)
        resposta = self.checkout(shipping_address=self.address.pk)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.pedido().tax_country, "BE")

    @override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_apenas_para_o_teste")
    def test_with_a_card_provider_the_label_says_pay(self):
        botao = self.botao(self.partial(shipping_address=self.address.pk).content.decode())
        self.assertIn("Pagar", botao)
        self.assertNotIn("Confirmar pedido", botao)

    def test_the_button_is_never_duplicated_on_the_full_page(self):
        html = self.client.get(CHECKOUT).content.decode()
        self.assertEqual(html.count('id="checkout-submit"'), 1)


# ---------------------------------------------------------------------------
# Preço, frete e total vêm do servidor
# ---------------------------------------------------------------------------


class ServerSideTotalsTests(AuditBase):
    def test_forged_totals_in_the_post_are_ignored(self):
        self.add(self.produto("FORJA", "19.99"), quantidade=2)
        resposta = self.checkout(subtotal="0.01", shipping_total="0.00", total="0.01", tax_total="0", unit_price="0.01", price="0.01", discount_total="39.00")
        self.assertEqual(resposta.status_code, 302)
        pedido = self.pedido()
        self.assertEqual((pedido.subtotal, pedido.shipping_total, pedido.total, pedido.discount_total), (Decimal("39.98"), Decimal("5.90"), Decimal("45.88"), Decimal("0.00")))
        self.assertEqual(pedido.items.get().unit_price, Decimal("19.99"))

    def test_total_is_subtotal_plus_shipping_and_tax_is_included(self):
        self.add(self.produto("A", "29.90"), quantidade=3)
        self.add(self.produto("B", "0.01"), quantidade=7)
        self.add(self.produto("C", "100.01"))
        self.checkout()
        pedido = self.pedido()
        self.assertEqual(pedido.subtotal, Decimal("189.78"))
        self.assertEqual(pedido.total, pedido.subtotal + pedido.shipping_total)
        self.assertEqual(pedido.tax_total, Decimal("33.96"))  # 195,68 × 21/121 = 33,9636… → meio para cima
        for valor in (pedido.subtotal, pedido.shipping_total, pedido.total, pedido.tax_total):
            self.assertIsInstance(valor, Decimal)
            self.assertEqual(valor, valor.quantize(Decimal("0.01")))
        self.assertEqual(sum((i.total for i in pedido.items.all()), Decimal("0")), pedido.subtotal)

    def test_the_summary_on_screen_matches_the_order_created(self):
        self.add(self.produto("TELA", "19.99"), quantidade=3)
        tela = self.client.get(CHECKOUT).context["draft"]
        self.checkout()
        pedido = self.pedido()
        self.assertEqual((tela.subtotal, tela.shipping_total, tela.total), (pedido.subtotal, pedido.shipping_total, pedido.total))

    def test_a_price_change_after_adding_is_the_catalogue_price_at_checkout(self):
        p = self.produto("MUDA", "10.00")
        self.add(p)
        v = p.default_variant
        v.sale_price = Decimal("12.50")
        v.save()
        self.assertIn("12,50", self.client.get(CHECKOUT).content.decode())
        self.checkout()
        self.assertEqual(self.pedido().items.get().unit_price, Decimal("12.50"))


# ---------------------------------------------------------------------------
# A matriz de opções
# ---------------------------------------------------------------------------


class OptionsMatrixTests(AuditBase):
    def setUp(self):
        super().setUp()
        self.p = make_product(sku="MAT", name="Matriz", status=ProductStatus.ACTIVE, with_variant=False)
        self.inst = ProductOption.objects.create(product=self.p, name="Instalação", sort_order=1)
        self.acab = ProductOption.objects.create(product=self.p, name="Acabamento", sort_order=2)
        mesa, parede = (ProductOptionValue.objects.create(option=self.inst, name=n, sort_order=i) for i, n in enumerate(("Mesa", "Parede")))
        fosco, brilh = (ProductOptionValue.objects.create(option=self.acab, name=n, sort_order=i) for i, n in enumerate(("Fosco", "Brilhante")))
        self.tabela = {("Mesa", "Fosco"): "30.00", ("Mesa", "Brilhante"): "32.00", ("Parede", "Fosco"): "34.00", ("Parede", "Brilhante"): "35.00"}
        self.valores = {"Mesa": mesa, "Parede": parede, "Fosco": fosco, "Brilhante": brilh}
        self.variantes = {}
        for n, ((i, a), preco) in enumerate(self.tabela.items(), start=1):
            v = ProductVariant.objects.create(product=self.p, sku=f"MAT-V{n:02d}", sale_price=Decimal(preco), stock_quantity=5, weight_grams=Decimal("100"))
            v.set_option_values({self.inst: self.valores[i], self.acab: self.valores[a]})
            self.variantes[(i, a)] = v

    def test_each_combination_reaches_the_order_with_its_own_sku_and_price(self):
        for (i, a), preco in self.tabela.items():
            with self.subTest(combinacao=(i, a)):
                for linha in self.linhas():
                    self.client.post(reverse("cart:remove"), {"line": linha.line_key})
                self.client.post(ADD, {"product_id": self.p.pk, f"option_opt-{self.inst.pk}": self.valores[i].pk, f"option_opt-{self.acab.pk}": self.valores[a].pk, "quantity": 1})
                self.assertEqual(self.checkout().status_code, 302)
                item = self.pedido().items.get()
                self.assertEqual((item.variant, item.sku, item.unit_price), (self.variantes[(i, a)], self.variantes[(i, a)].sku, Decimal(preco)))
                self.assertEqual(item.options_snapshot, f"Instalação: {i} · Acabamento: {a}")
                self.assertEqual(item.variant_label, f"{i} · {a}")

    def test_four_combinations_in_one_cart_are_four_lines(self):
        for (i, a) in self.tabela:
            self.client.post(ADD, {"product_id": self.p.pk, f"option_opt-{self.inst.pk}": self.valores[i].pk, f"option_opt-{self.acab.pk}": self.valores[a].pk, "quantity": 1})
        self.assertEqual(len(self.linhas()), 4)
        self.checkout()
        pedido = self.pedido()
        self.assertEqual(pedido.items.count(), 4)
        self.assertEqual(pedido.subtotal, Decimal("131.00"))
        self.assertEqual(sorted(i.sku for i in pedido.items.all()), ["MAT-V01", "MAT-V02", "MAT-V03", "MAT-V04"])

    def test_a_variant_id_with_the_options_of_another_variant_is_refused(self):
        self.client.post(ADD, {"product_id": self.p.pk, "variant_id": self.variantes[("Mesa", "Fosco")].pk,
                               f"option_opt-{self.inst.pk}": self.valores["Parede"].pk, f"option_opt-{self.acab.pk}": self.valores["Brilhante"].pk, "quantity": 1})
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual(Order.objects.count(), 0)


# ---------------------------------------------------------------------------
# Entrega
# ---------------------------------------------------------------------------


class DeliveryRulesTests(AuditBase):
    def setUp(self):
        super().setUp()
        self.add(self.produto("ENT"))

    def test_an_inactive_method_is_neither_offered_nor_accepted(self):
        self.method.is_active = False
        self.method.save()
        self.assertEqual(self.client.get(CHECKOUT).context["draft"].options, [])
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual(Order.objects.count(), 0)

    def test_an_inactive_carrier_takes_its_methods_along(self):
        self.method.carrier.is_active = False
        self.method.carrier.save()
        self.assertEqual(self.client.get(CHECKOUT).context["draft"].options, [])
        self.assertEqual(self.checkout().status_code, 200)

    def test_an_inactive_rate_is_not_offered(self):
        ShippingRate.objects.update(is_active=False)
        self.assertEqual(self.client.get(CHECKOUT).context["draft"].options, [])
        self.assertEqual(self.checkout().status_code, 200)

    def test_weight_picks_the_band_and_a_weight_out_of_every_band_offers_nothing(self):
        make_rate(self.method, self.be, 5000, 10000, "14.90")
        pesado = self.produto("PESADO", weight_grams=Decimal("6000"))
        self.add(pesado)
        opcoes = self.client.get(CHECKOUT).context["draft"].options
        self.assertEqual([o.price for o in opcoes], [Decimal("14.90")])  # 100 g + 6000 g = 6100 g
        self.add(pesado)  # 12 100 g: nenhuma faixa
        self.assertEqual(self.client.get(CHECKOUT).context["draft"].options, [])
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual(Order.objects.count(), 0)

    def test_changing_the_method_changes_the_shipping_total_on_the_order(self):
        expresso = make_method(carrier=make_carrier("DPD", "dpd"), name="Expresso", code="expresso", min_days=1, max_days=1)
        make_rate(expresso, self.be, 0, 5000, "12.50")
        self.assertIn("12,50", self.partial(shipping_address=self.address.pk, shipping_method=expresso.pk).content.decode())
        self.checkout(shipping_method=expresso.pk)
        pedido = self.pedido()
        self.assertEqual((pedido.shipping_total, pedido.shipping_method_id, pedido.shipping_method_label), (Decimal("12.50"), expresso.pk, expresso.label))

    def test_a_method_id_that_is_not_a_number_does_not_break_the_page(self):
        self.assertEqual(self.client.get(CHECKOUT + "?shipping_method=abc").status_code, 200)
        self.assertEqual(self.partial(shipping_address="abc").status_code, 200)

    def test_an_address_in_an_inactive_country_is_refused(self):
        de = make_country("DE", "Alemanha", is_active=False)
        make_rate(self.method, de, 0, 5000, "9.90")
        alema = make_address(self.customer, de, label="Berlim", city="Berlin")
        self.assertEqual(self.client.get(CHECKOUT + f"?shipping_address={alema.pk}").context["draft"].options, [])
        self.assertEqual(self.checkout(shipping_address=alema.pk).status_code, 200)
        self.assertEqual(Order.objects.count(), 0)


# ---------------------------------------------------------------------------
# Estoque e concorrência
# ---------------------------------------------------------------------------


class StockTests(AuditBase):
    def test_two_customers_and_the_last_unit(self):
        """Sem reserva no carrinho: os dois pedidos nascem; a confirmação nunca deixa o saldo negativo."""
        p = self.produto("ULTIMA", estoque=1)
        self.add(p)
        self.assertEqual(self.checkout().status_code, 302)
        pedido_a = self.pedido()

        outro = make_user(username="outro", email="outro@example.com")
        endereco_b = make_address(outro.customer, self.be)
        cliente_b = Client()
        cliente_b.force_login(outro)
        cliente_b.post(ADD, {"product_id": p.pk, "variant_id": p.default_variant.pk, "quantity": 1})
        resposta = cliente_b.post(CHECKOUT, {"shipping_address": endereco_b.pk, "billing_same_as_shipping": "1", "shipping_method": self.method.pk, "payment_method": "transfer"})
        self.assertEqual(resposta.status_code, 302)
        pedido_b = Order.objects.get(customer=outro.customer)

        services.confirm_payment(pedido_a)
        p.default_variant.refresh_from_db()
        self.assertEqual(p.default_variant.stock_quantity, 0)
        faltas = services.apply_stock(pedido_b)
        p.default_variant.refresh_from_db()
        self.assertEqual(p.default_variant.stock_quantity, 0)  # nunca negativo
        self.assertEqual(len(faltas), 1)
        self.assertEqual(OrderItem.objects.get(order=pedido_b).stock_taken, 0)
        self.assertEqual(OrderItem.objects.get(order=pedido_a).stock_taken, 1)

    def test_a_third_customer_is_stopped_at_the_checkout_once_the_stock_is_gone(self):
        p = self.produto("FIM", estoque=1)
        self.add(p)
        v = p.default_variant
        v.stock_quantity = 0
        v.save()
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(len(self.linhas()), 1)  # o carrinho não é apagado numa recusa

    def test_confirming_the_payment_twice_takes_the_stock_once(self):
        p = self.produto("DUAS", estoque=5)
        self.add(p, quantidade=2)
        self.checkout()
        pedido = self.pedido()
        services.confirm_payment(pedido)
        services.confirm_payment(Order.objects.get(pk=pedido.pk))
        services.apply_stock(Order.objects.get(pk=pedido.pk))
        p.default_variant.refresh_from_db()
        self.assertEqual(p.default_variant.stock_quantity, 3)

    def test_the_cart_never_exceeds_the_stock(self):
        p = self.produto("LIM", estoque=2)
        self.add(p, quantidade=5)
        self.assertTrue(all(linha.quantity <= 2 for linha in self.linhas()))
        self.add(p, quantidade=2)
        self.add(p, quantidade=1)  # 2 + 1 > estoque: fica em 2
        self.assertEqual([linha.quantity for linha in self.linhas()], [2])


# ---------------------------------------------------------------------------
# Criação do pedido: transação e carrinho
# ---------------------------------------------------------------------------


class OrderCreationTests(AuditBase):
    def test_a_failure_after_the_order_row_leaves_nothing(self):
        from unittest import mock

        self.add(self.produto("FALHA"))
        with mock.patch("apps.orders.services.OrderItem.objects.bulk_create", side_effect=RuntimeError("banco caiu")):
            with self.assertRaises(RuntimeError):
                self.checkout()
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (0, 0))
        self.assertEqual(len(self.linhas()), 1)  # o carrinho continua lá

    def test_a_refused_checkout_keeps_the_cart_and_a_successful_one_empties_it(self):
        self.add(self.produto("CARR"))
        self.assertEqual(self.checkout(shipping_method=999999).status_code, 200)
        self.assertEqual(len(self.linhas()), 1)
        self.assertEqual(self.checkout().status_code, 302)
        self.assertEqual(self.linhas(), [])

    def test_the_order_starts_in_the_state_the_rest_of_the_system_expects(self):
        self.add(self.produto("EST"))
        self.checkout()
        pedido = self.pedido()
        self.assertEqual((pedido.status, pedido.payment_status), (OrderStatus.PENDING, PaymentStatus.PENDING))
        self.assertEqual(pedido.payment_method, "transfer")
        self.assertTrue(pedido.bank_iban)
        self.assertIsNone(pedido.stock_applied_at)
        self.assertTrue(services.cancellation_plan(pedido))  # o cancelamento entende o estado


# ---------------------------------------------------------------------------
# Acesso e retomada de pagamento
# ---------------------------------------------------------------------------


class AccessTests(AuditBase):
    def setUp(self):
        super().setUp()
        self.add(self.produto("ACESSO"))
        self.checkout()
        self.order = self.pedido()

    def outro_cliente(self):
        outro = make_user(username="outro", email="outro@example.com")
        c = Client()
        c.force_login(outro)
        return c

    def test_another_customer_cannot_see_pay_or_cancel_the_order(self):
        c = self.outro_cliente()
        for nome in ("orders:detail", "orders:confirmation", "orders:retry_payment", "orders:cancel"):
            with self.subTest(rota=nome):
                self.assertEqual(c.get(reverse(nome, args=[self.order.number])).status_code, 404)
                self.assertIn(c.post(reverse(nome, args=[self.order.number]), {"payment_method": "transfer"}).status_code, (404, 405))

    def test_an_anonymous_visitor_is_sent_to_login(self):
        c = Client()
        self.assertEqual(c.get(reverse("orders:retry_payment", args=[self.order.number])).status_code, 302)
        self.assertEqual(c.post(CHECKOUT, {"shipping_address": self.address.pk}).status_code, 302)
        self.assertEqual(Order.objects.count(), 1)

    def test_a_post_without_csrf_is_refused(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.user)
        self.assertEqual(c.post(CHECKOUT, {"shipping_address": self.address.pk, "shipping_method": self.method.pk}).status_code, 403)
        self.assertEqual(c.post(ADD, {"product_id": 1, "quantity": 1}).status_code, 403)

    def test_retry_does_not_create_a_second_order_nor_bypass_the_state(self):
        url = reverse("orders:retry_payment", args=[self.order.number])
        self.client.post(url, {"payment_method": "transfer"})
        self.assertEqual(Order.objects.count(), 1)
        services.confirm_payment(self.order)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, {"payment_method": "transfer"}).status_code, 302)
        self.assertEqual(Order.objects.count(), 1)

    def test_retry_is_stopped_by_the_stock_of_today(self):
        v = self.order.items.get().variant
        v.stock_quantity = 0
        v.save()
        url = reverse("orders:retry_payment", args=[self.order.number])
        antes = self.order.payments.count()
        resposta = self.client.post(url, {"payment_method": "transfer"})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["can_pay"])
        self.assertEqual(self.order.payments.count(), antes)  # nenhuma tentativa nova

    def test_retry_ignores_a_forged_payment_state(self):
        url = reverse("orders:retry_payment", args=[self.order.number])
        self.client.post(url, {"payment_method": "transfer", "payment_status": "paid", "status": "paid", "total": "0.01"})
        pedido = Order.objects.get(pk=self.order.pk)
        self.assertEqual(pedido.payment_status, PaymentStatus.PENDING)
        self.assertEqual(pedido.total, self.order.total)

    def test_a_cancelled_order_cannot_be_paid(self):
        services.request_cancellation(self.order, "Mudei de ideia")
        pedido = Order.objects.get(pk=self.order.pk)
        self.assertTrue(pedido.is_cancelled)
        url = reverse("orders:retry_payment", args=[self.order.number])
        self.assertEqual(self.client.post(url, {"payment_method": "transfer"}).status_code, 302)
        self.assertNotEqual(Order.objects.get(pk=self.order.pk).payment_status, PaymentStatus.PAID)
        self.assertEqual(self.order.payments.count(), Order.objects.get(pk=self.order.pk).payments.count())


# ---------------------------------------------------------------------------
# Idiomas e performance
# ---------------------------------------------------------------------------


class LanguageTests(AuditBase):
    def test_the_checkout_and_the_order_follow_the_language(self):
        esperado = {"/fr": ("Adresse de livraison", "fr"), "/nl": ("Bezorgadres", "nl"), "/en": ("Delivery address", "en")}
        for prefixo, (texto, idioma) in esperado.items():
            with self.subTest(idioma=idioma):
                self.add(self.produto(f"L{idioma}"))
                html = self.client.get(prefixo + CHECKOUT).content.decode()
                self.assertIn(texto, html)
                mail.outbox.clear()
                resposta = self.client.post(prefixo + CHECKOUT, {"shipping_address": self.address.pk, "billing_same_as_shipping": "1", "shipping_method": self.method.pk, "payment_method": "transfer"})
                self.assertEqual(resposta.status_code, 302)
                self.assertEqual(self.pedido().language, idioma)


class QueryCountTests(AuditBase):
    def test_the_checkout_does_not_cost_a_query_per_line(self):
        produtos = [self.produto(f"Q{n}") for n in range(4)]
        self.add(produtos[0])
        with CaptureQueriesContext(connection) as um:
            self.client.get(CHECKOUT)
        for p in produtos[1:]:
            self.add(p)
        with CaptureQueriesContext(connection) as quatro:
            self.client.get(CHECKOUT)
        self.assertEqual(len(um), len(quatro))
