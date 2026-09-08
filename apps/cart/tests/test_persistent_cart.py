"""Carrinho persistente: sessão para o visitante, banco para quem entrou.

O que estes testes protegem, além do óbvio: a identidade da linha continua
sendo **produto + variante + personalização** dos dois lados. Se o merge
somasse por produto, "caneca com o nome Marie" e "caneca com o nome Paul"
virariam duas canecas iguais.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import Cart as CartFacade
from apps.cart.keys import line_key
from apps.cart.models import Cart, CartItem
from apps.cart.storage import CART_SESSION_KEY
from apps.catalog.models import Color, PersonalizationType, ProductVariant
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_user


class PersistentCartTestCase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product_a = make_product(
            sku="A", name="Produto A", category=self.category, price=Decimal("10.00"), stock_quantity=10
        )
        self.product_b = make_product(
            sku="B", name="Produto B", category=self.category, price=Decimal("20.00"), stock_quantity=10
        )
        self.product_c = make_product(
            sku="C", name="Produto C", category=self.category, price=Decimal("30.00"), stock_quantity=10
        )
        self.user = make_user(username="maria", password="vaso-facetado-77")

    def add(self, product, quantity=1, **extra):
        return self.client.post(
            reverse("cart:add"), {"product_id": product.pk, "quantity": quantity, **extra}
        )

    def login(self):
        return self.client.post(
            reverse("accounts:login"), {"username": "maria", "password": "vaso-facetado-77"}
        )

    def stored(self, user=None):
        """{SKU: quantidade} do carrinho persistente."""
        return {
            item.product.sku: item.quantity
            for item in CartItem.objects.filter(cart__user=user or self.user).select_related("product")
        }


class VisitorCartTests(PersistentCartTestCase):
    def test_visitor_cart_lives_in_the_session(self):
        self.add(self.product_a, 2)

        self.assertEqual(len(self.client.session[CART_SESSION_KEY]), 1)
        self.assertFalse(Cart.objects.exists())

    def test_visitor_cart_creates_no_database_row(self):
        """Quem talvez nunca volte não precisa deixar linha em tabela alguma."""
        self.add(self.product_a, 1)
        self.client.get(reverse("cart:detail"))

        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)


class AuthenticatedCartTests(PersistentCartTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_adding_writes_to_the_database(self):
        self.add(self.product_a, 2)

        self.assertEqual(self.stored(), {"A": 2})
        self.assertFalse(self.client.session.get(CART_SESSION_KEY))

    def test_empty_cart_creates_nothing(self):
        self.client.get(reverse("cart:detail"))

        self.assertFalse(Cart.objects.exists())

    def test_only_one_cart_per_user(self):
        self.add(self.product_a, 1)
        self.add(self.product_b, 1)

        self.assertEqual(Cart.objects.filter(user=self.user).count(), 1)

    def test_updating_the_quantity_persists(self):
        self.add(self.product_a, 1)
        key = line_key(self.product_a.pk, self.product_a.default_variant.pk, None)

        self.client.post(reverse("cart:update"), {"line": key, "quantity": 4})

        self.assertEqual(self.stored(), {"A": 4})

    def test_removing_a_line_deletes_the_row(self):
        self.add(self.product_a, 1)
        key = line_key(self.product_a.pk, self.product_a.default_variant.pk, None)

        self.client.post(reverse("cart:remove"), {"line": key})

        self.assertEqual(CartItem.objects.count(), 0)
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

    def test_cart_survives_a_new_session(self):
        self.add(self.product_a, 3)

        self.client.logout()
        self.client.force_login(self.user)

        response = self.client.get(reverse("cart:detail"))
        self.assertEqual(response.context["cart"].total_quantity, 3)

    def test_logging_out_leaves_the_cart_behind(self):
        self.add(self.product_a, 3)

        self.client.logout()

        response = self.client.get(reverse("cart:detail"))
        self.assertEqual(response.context["cart"].total_quantity, 0)
        self.assertEqual(self.stored(), {"A": 3})

    def test_another_user_never_sees_this_cart(self):
        self.add(self.product_a, 2)
        other = make_user(username="joao", password="vaso-facetado-77")

        self.client.logout()
        self.client.force_login(other)

        response = self.client.get(reverse("cart:detail"))
        self.assertEqual(response.context["cart"].total_quantity, 0)
        self.assertEqual(self.stored(), {"A": 2})

    def test_deactivated_product_leaves_the_cart(self):
        self.add(self.product_a, 1)
        self.add(self.product_b, 1)

        self.product_a.status = "draft"
        self.product_a.save(update_fields=["status"])

        response = self.client.get(reverse("cart:detail"))
        self.assertEqual(response.context["cart"].total_quantity, 1)

    def test_header_counter_costs_one_query(self):
        """O contador aparece em toda página: não pode carregar as linhas."""
        self.add(self.product_a, 2)
        self.add(self.product_b, 1)

        with self.assertNumQueries(1):
            self.fresh_cart().total_quantity

    def test_reading_the_lines_has_a_query_budget(self):
        """Três produtos não podem custar três vezes o mesmo trabalho."""
        self.add(self.product_a, 1)
        self.add(self.product_b, 1)
        self.add(self.product_c, 1)

        # 1 carrinho + 1 itens + 1 produtos + 6 prefetches. Subiu um na etapa
        # 19: as fotos das variantes, para a linha mostrar a peça que foi
        # comprada e não a foto de abertura do produto. E dois na etapa 2B: a
        # paleta de cores e a composição de materiais do produto, que a linha
        # mostra quando a variante não tem cor/material próprio. Continua
        # **fixo** — não cresce com a quantidade de linhas, que é o que importa.
        with self.assertNumQueries(11):
            lines = self.fresh_cart().lines()
            [(line.display_name, line.unit_price) for line in lines]

    def fresh_cart(self):
        """Um carrinho recém-instanciado, sem nada em cache."""
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.user
        request.session = {}
        return CartFacade(request)


class CartMergeTests(PersistentCartTestCase):
    def test_visitor_cart_moves_to_the_account_on_login(self):
        self.add(self.product_a, 2)
        self.add(self.product_b, 1)

        self.login()

        self.assertEqual(self.stored(), {"A": 2, "B": 1})
        self.assertFalse(self.client.session.get(CART_SESSION_KEY))

    def test_identical_lines_are_summed(self):
        self.client.force_login(self.user)
        self.add(self.product_a, 1)
        self.add(self.product_c, 3)
        self.client.logout()

        self.add(self.product_a, 2)
        self.add(self.product_b, 1)
        self.login()

        self.assertEqual(self.stored(), {"A": 3, "B": 1, "C": 3})

    def test_merge_respects_the_stock(self):
        limited = make_product(
            sku="LIM", name="Últimas peças", category=self.category, price=Decimal("9.00"), stock_quantity=5
        )
        self.client.force_login(self.user)
        self.add(limited, 4)
        self.client.logout()
        self.add(limited, 4)

        response = self.login()

        self.assertEqual(self.stored(), {"LIM": 5})
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any("Últimas peças" in message for message in messages))

    def test_a_plain_transfer_says_nothing(self):
        """Cadastrar e ver o carrinho junto não é surpresa: nada de aviso."""
        self.add(self.product_a, 1)

        response = self.login()

        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertNotIn("Seu carrinho foi recuperado.", messages)

    def test_meeting_two_carts_is_announced(self):
        """Ver itens que não foram postos neste navegador, sim."""
        self.client.force_login(self.user)
        self.add(self.product_c, 1)
        self.client.logout()
        self.add(self.product_a, 1)

        response = self.login()

        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("Seu carrinho foi recuperado.", messages)

    def test_nothing_happens_when_the_visitor_cart_is_empty(self):
        self.client.force_login(self.user)
        self.add(self.product_a, 1)
        self.client.logout()

        self.login()

        self.assertEqual(self.stored(), {"A": 1})

    def test_merge_keeps_the_cart_of_the_right_user(self):
        other = make_user(username="joao", password="vaso-facetado-77")
        self.client.force_login(other)
        self.add(self.product_c, 1)
        self.client.logout()

        self.add(self.product_a, 1)
        self.login()

        self.assertEqual(self.stored(), {"A": 1})
        self.assertEqual(self.stored(other), {"C": 1})


class MergeKeepsLinesApartTests(PersistentCartTestCase):
    """Variante e personalização diferentes = linhas diferentes, no merge também."""

    def setUp(self):
        super().setUp()
        self.black = Color.objects.create(name="Preto", hex_code="#000000")
        self.white = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.variant_black = ProductVariant.objects.create(
            product=self.product_a, sku="A-PRETO", color=self.black, stock_quantity=5
        )
        self.variant_white = ProductVariant.objects.create(
            product=self.product_a, sku="A-BRANCO", color=self.white, stock_quantity=5
        )
        self.engraved = make_product(
            sku="NOME",
            name="Chaveiro com nome",
            category=self.category,
            price=Decimal("7.90"),
            stock_quantity=20,
            personalization_type=PersonalizationType.TEXT,
            personalization_text_limit=20,
        )

    def test_different_variants_are_not_merged(self):
        self.client.force_login(self.user)
        self.add(self.product_a, 1, variant_id=self.variant_black.pk)
        self.client.logout()
        self.add(self.product_a, 1, variant_id=self.variant_white.pk)

        self.login()

        quantities = sorted(
            (item.variant.sku, item.quantity)
            for item in CartItem.objects.select_related("variant")
        )
        self.assertEqual(quantities, [("A-BRANCO", 1), ("A-PRETO", 1)])

    def test_same_variant_is_merged(self):
        self.client.force_login(self.user)
        self.add(self.product_a, 1, variant_id=self.variant_black.pk)
        self.client.logout()
        self.add(self.product_a, 2, variant_id=self.variant_black.pk)

        self.login()

        self.assertEqual(CartItem.objects.count(), 1)
        self.assertEqual(CartItem.objects.get().quantity, 3)

    def test_different_texts_are_not_merged(self):
        self.client.force_login(self.user)
        self.add(self.engraved, 1, personalization_text="Marie")
        self.client.logout()
        self.add(self.engraved, 1, personalization_text="Paul")

        self.login()

        texts = sorted(CartItem.objects.values_list("customization_text", flat=True))
        self.assertEqual(texts, ["Marie", "Paul"])

    def test_same_text_is_merged(self):
        self.client.force_login(self.user)
        self.add(self.engraved, 1, personalization_text="Marie")
        self.client.logout()
        self.add(self.engraved, 2, personalization_text="Marie")

        self.login()

        self.assertEqual(CartItem.objects.count(), 1)
        self.assertEqual(CartItem.objects.get().quantity, 3)


class VisitorHintTests(PersistentCartTestCase):
    """O convite discreto na gaveta — convite, nunca obstáculo."""

    def test_visitor_with_items_sees_the_hint(self):
        self.add(self.product_a, 1)

        response = self.client.get(reverse("cart:drawer"))

        self.assertContains(response, "Não perca seu carrinho")
        self.assertContains(response, reverse("accounts:register"))

    def test_empty_visitor_cart_shows_no_hint(self):
        response = self.client.get(reverse("cart:drawer"))

        self.assertNotContains(response, "Não perca seu carrinho")

    def test_authenticated_customer_does_not_see_it(self):
        self.client.force_login(self.user)
        self.add(self.product_a, 1)

        response = self.client.get(reverse("cart:drawer"))

        self.assertNotContains(response, "Não perca seu carrinho")

    def test_visitor_can_still_buy(self):
        """Nada é bloqueado: o carrinho do visitante continua funcionando."""
        response = self.add(self.product_a, 2)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.session[CART_SESSION_KEY][line_key(self.product_a.pk, self.product_a.default_variant.pk, None)][
                "quantity"
            ],
            2,
        )
