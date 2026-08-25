"""Testes das páginas e ações do carrinho (com e sem HTMX)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import CART_SESSION_KEY, line_key
from apps.catalog.models import ProductStatus
from apps.core.testing import LanguageResetMixin, make_category, make_product

HTMX = {"HTTP_HX_REQUEST": "true"}


def quantity_in(session, product, variant=None, customization=None):
    """Quantidade de uma linha na sessão, pela identidade produto+variante."""
    key = line_key(product.pk, variant.pk if variant else None, customization)
    return session.get(CART_SESSION_KEY, {}).get(key, {}).get("quantity", 0)


class AddToCartTests(LanguageResetMixin, TestCase):
    def setUp(self):
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category,
            price=Decimal("8.90"), stock_quantity=10,
        )

    def add(self, product=None, **extra):
        return self.client.post(
            reverse("cart:add"),
            {"product_id": (product or self.product).pk, **extra},
        )

    def test_add_redirects_without_htmx(self):
        response = self.add(next="/modelos/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/modelos/")
        self.assertEqual(quantity_in(self.client.session, self.product), 1)

    def test_add_shows_a_message(self):
        response = self.client.post(
            reverse("cart:add"), {"product_id": self.product.pk, "next": "/modelos/"}, follow=True
        )
        self.assertContains(response, "Produto adicionado ao carrinho")

    def test_add_with_htmx_returns_the_pieces(self):
        response = self.client.post(reverse("cart:add"), {"product_id": self.product.pk}, **HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hx-swap-oob")
        self.assertContains(response, "cart-link")
        self.assertContains(response, "Produto adicionado ao carrinho")

    def test_add_twice_increments(self):
        self.add()
        self.add()

        self.assertEqual(quantity_in(self.client.session, self.product), 2)

    def test_add_specific_quantity(self):
        self.add(quantity=3)
        self.assertEqual(quantity_in(self.client.session, self.product), 3)

    def test_invalid_quantity_falls_back_to_one(self):
        self.add(quantity="abc")
        self.assertEqual(quantity_in(self.client.session, self.product), 1)

    def test_out_of_stock_product_is_refused(self):
        sold_out = make_product(sku="ESG-01", name="Esgotado", stock_quantity=0)
        response = self.client.post(
            reverse("cart:add"), {"product_id": sold_out.pk}, follow=True
        )

        self.assertContains(response, "Produto esgotado")
        self.assertEqual(quantity_in(self.client.session, sold_out), 0)

    def test_made_to_order_product_can_be_added_without_stock(self):
        product = make_product(
            sku="ENC-01", name="Sob encomenda", stock_quantity=0,
            made_to_order=True, production_lead_time_days=5,
        )
        self.add(product)

        self.assertEqual(quantity_in(self.client.session, product), 1)

    def test_stock_limit_is_enforced(self):
        limited = make_product(sku="LIM-01", name="Limitado", stock_quantity=2)
        self.add(limited, quantity=9)

        self.assertEqual(quantity_in(self.client.session, limited), 2)

    def test_inactive_product_returns_404(self):
        draft = make_product(sku="RAS-01", name="Rascunho", status=ProductStatus.DRAFT)
        response = self.client.post(reverse("cart:add"), {"product_id": draft.pk})

        self.assertEqual(response.status_code, 404)

    def test_unknown_product_returns_404(self):
        response = self.client.post(reverse("cart:add"), {"product_id": 999999})
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("cart:add"))
        self.assertEqual(response.status_code, 405)

    def test_external_next_is_ignored(self):
        response = self.client.post(
            reverse("cart:add"),
            {"product_id": self.product.pk, "next": "https://exemplo-malicioso.test/"},
        )
        self.assertEqual(response.url, reverse("cart:detail"))


class UpdateAndRemoveTests(LanguageResetMixin, TestCase):
    def setUp(self):
        self.product = make_product(sku="GATO-01", name="Gato", price=Decimal("8.90"), stock_quantity=5)
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2})

    def quantity(self):
        return quantity_in(self.client.session, self.product)

    @property
    def line(self):
        return line_key(self.product.pk, None, None)

    def test_increment(self):
        self.client.post(reverse("cart:update"), {"line": self.line, "action": "increment"})
        self.assertEqual(self.quantity(), 3)

    def test_decrement(self):
        self.client.post(reverse("cart:update"), {"line": self.line, "action": "decrement"})
        self.assertEqual(self.quantity(), 1)

    def test_decrement_to_zero_removes_the_line(self):
        for _ in range(2):
            self.client.post(reverse("cart:update"), {"line": self.line, "action": "decrement"})

        self.assertEqual(self.quantity(), 0)
        self.assertEqual(self.client.session.get(CART_SESSION_KEY), {})

    def test_set_absolute_quantity(self):
        self.client.post(reverse("cart:update"), {"line": self.line, "quantity": 4})
        self.assertEqual(self.quantity(), 4)

    def test_increment_stops_at_the_stock(self):
        self.client.post(reverse("cart:update"), {"line": self.line, "quantity": 99})
        self.assertEqual(self.quantity(), 5)

    def test_remove(self):
        self.client.post(reverse("cart:remove"), {"line": self.line})
        self.assertEqual(self.quantity(), 0)

    def test_update_with_htmx_returns_the_panel(self):
        response = self.client.post(
            reverse("cart:update"), {"line": self.line, "action": "increment"}, **HTMX
        )

        self.assertContains(response, 'id="cart-panel"')
        self.assertContains(response, "Subtotal")


class CartPageTests(LanguageResetMixin, TestCase):
    def setUp(self):
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", price=Decimal("8.90"), stock_quantity=10
        )

    def test_empty_cart_page(self):
        response = self.client.get(reverse("cart:detail"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seu carrinho está vazio")

    def test_cart_page_lists_products_and_subtotal(self):
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2})
        response = self.client.get(reverse("cart:detail"))

        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "17,80")
        self.assertContains(response, "Subtotal")

    def test_header_counter_shows_total_units(self):
        other = make_product(sku="P-2", name="Dragão", price=Decimal("24.50"), stock_quantity=10)
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2})
        self.client.post(reverse("cart:add"), {"product_id": other.pk, "quantity": 3})

        response = self.client.get(reverse("cart:detail"))
        # 2 unidades + 3 unidades = 5 no contador (e não "2 linhas").
        self.assertContains(response, 'data-cart-count="5"')

    def test_counter_is_absent_when_cart_is_empty(self):
        response = self.client.get("/")
        self.assertNotContains(response, "data-cart-count")

    def test_checkout_responds_to_visitors(self):
        """A etapa 7 trocou o "em construção" pelo checkout de verdade.

        A URL é a mesma e continua respondendo 200 para quem não entrou — o
        visitante vê o resumo e o convite para se identificar, em vez de um
        redirecionamento seco que faz a compra se perder.
        """
        response = self.client.get(reverse("cart:checkout"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checkout")

    def test_checkout_keeps_the_cart(self):
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk})
        self.client.get(reverse("cart:checkout"))

        self.assertEqual(quantity_in(self.client.session, self.product), 1)


class CartLanguageTests(LanguageResetMixin, TestCase):
    def test_cart_page_in_french(self):
        response = self.client.get("/fr/carrinho/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="fr"')

    def test_add_message_follows_the_language(self):
        product = make_product(sku="GATO-01", name="Gato", stock_quantity=5)
        response = self.client.post(
            "/fr/carrinho/adicionar/",
            {"product_id": product.pk, "next": "/fr/modelos/"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
