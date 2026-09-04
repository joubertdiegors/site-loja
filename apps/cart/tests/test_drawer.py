"""Testes da gaveta lateral do carrinho (mini-cart)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import line_key
from apps.core.testing import LanguageResetMixin, make_category, make_product

HTMX = {"HTTP_HX_REQUEST": "true"}


class DrawerPresenceTests(LanguageResetMixin, TestCase):
    """A gaveta existe em todas as páginas, fechada e fora da ordem de foco."""

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category,
            price=Decimal("8.90"), stock_quantity=10,
        )

    def test_drawer_is_on_every_page(self):
        for page in ("/", "/modelos/", "/carrinho/", self.product.get_absolute_url()):
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertContains(response, 'id="cart-drawer"')
                self.assertContains(response, 'id="cart-drawer-body"')

    def test_drawer_starts_closed_and_inert(self):
        response = self.client.get("/")

        self.assertContains(response, "inert")
        self.assertContains(response, "translate-x-full")

    def test_drawer_is_a_dialog_with_a_title(self):
        response = self.client.get("/")

        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'aria-labelledby="cart-drawer-title"')
        self.assertContains(response, "Seu carrinho")

    def test_drawer_has_a_close_button_and_an_overlay(self):
        response = self.client.get("/")

        self.assertContains(response, "data-cart-close")
        self.assertContains(response, "data-cart-overlay")
        self.assertContains(response, "Fechar carrinho")

    def test_cart_icon_opens_the_drawer_but_stays_a_link(self):
        response = self.client.get("/")

        self.assertContains(response, "data-cart-open")
        self.assertContains(response, 'aria-haspopup="dialog"')
        # Sem JavaScript o mesmo ícone continua levando à página do carrinho.
        self.assertContains(response, f'href="{reverse("cart:detail")}"')

    def test_empty_drawer_message(self):
        response = self.client.get(reverse("cart:drawer"))

        self.assertContains(response, "Seu carrinho está vazio")
        self.assertNotContains(response, "Subtotal")

    def test_drawer_lists_the_products(self):
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2})
        response = self.client.get(reverse("cart:drawer"))

        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "17,80")
        self.assertContains(response, "Subtotal")
        # O rodapé fixo: total, o botão para o checkout e a saída sem comprar.
        self.assertContains(response, "Total")
        self.assertContains(response, "Fechar conta")
        self.assertContains(response, reverse("cart:checkout"))
        self.assertContains(response, "Continuar comprando")


class DrawerUpdateTests(LanguageResetMixin, TestCase):
    """As ações do carrinho devolvem a gaveta atualizada, sem recarregar."""

    def setUp(self):
        super().setUp()
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", price=Decimal("8.90"), stock_quantity=10
        )
        self.other = make_product(
            sku="DRAG-01", name="Dragão", price=Decimal("24.50"), stock_quantity=10
        )

    @property
    def line(self):
        return line_key(self.product.pk, self.product.default_variant.pk, None)

    def add(self, product=None, **extra):
        return self.client.post(
            reverse("cart:add"),
            {"product_id": (product or self.product).pk, **extra},
            **HTMX,
        )

    def test_adding_returns_the_drawer_and_the_counter(self):
        response = self.add()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cart-drawer-body"')
        self.assertContains(response, 'id="cart-link"')
        self.assertContains(response, "hx-swap-oob")

    def test_adding_asks_the_drawer_to_open(self):
        response = self.add()
        self.assertIn("jd:cart-open", response.headers.get("HX-Trigger", ""))

    def test_adding_shows_the_toast(self):
        response = self.add()
        self.assertContains(response, "Produto adicionado ao carrinho")

    def test_a_refused_add_does_not_open_the_drawer(self):
        sold_out = make_product(sku="ESG-01", name="Esgotado", stock_quantity=0)
        response = self.add(sold_out)

        self.assertNotIn("jd:cart-open", response.headers.get("HX-Trigger", ""))
        self.assertContains(response, "Produto esgotado")

    def test_incrementing_updates_the_drawer(self):
        self.add()
        response = self.client.post(
            reverse("cart:update"), {"line": self.line, "action": "increment"}, **HTMX
        )

        self.assertContains(response, 'id="cart-drawer-body"')
        self.assertContains(response, "17,80")

    def test_decrementing_updates_the_subtotal(self):
        self.add(quantity=3)
        response = self.client.post(
            reverse("cart:update"), {"line": self.line, "action": "decrement"}, **HTMX
        )

        self.assertContains(response, "17,80")

    def test_removing_empties_the_drawer(self):
        self.add()
        response = self.client.post(reverse("cart:remove"), {"line": self.line}, **HTMX)

        self.assertContains(response, "Seu carrinho está vazio")

    def test_counter_shows_total_units(self):
        self.add(quantity=2)
        response = self.add(self.other, quantity=3)

        self.assertContains(response, 'data-cart-count="5"')

    def test_the_page_is_not_reloaded(self):
        """A resposta é um pedaço, não uma página inteira."""
        response = self.add()

        self.assertNotContains(response, "<html")
        self.assertNotContains(response, "<footer")

    def test_without_htmx_it_returns_to_the_current_page(self):
        response = self.client.post(
            reverse("cart:add"), {"product_id": self.product.pk, "next": "/modelos/"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/modelos/")
