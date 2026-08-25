"""Testes do carrinho de sessão (a classe ``Cart``, sem HTTP)."""

from decimal import Decimal

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings

from apps.cart.cart import Cart, max_quantity_for
from apps.catalog.models import ProductStatus
from apps.core.testing import make_category, make_product


def make_request():
    request = RequestFactory().get("/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


class CartBasicsTests(TestCase):
    def setUp(self):
        self.request = make_request()
        self.cart = Cart(self.request)
        self.product = make_product(sku="P-1", name="Gato Pompom", price=Decimal("8.90"), stock_quantity=10)

    def test_starts_empty(self):
        self.assertTrue(self.cart.is_empty)
        self.assertEqual(self.cart.total_quantity, 0)
        self.assertEqual(self.cart.subtotal, Decimal("0.00"))
        self.assertEqual(self.cart.lines(), [])

    def test_add_product(self):
        result = self.cart.add(self.product)

        self.assertTrue(result.ok)
        self.assertEqual(self.cart.total_quantity, 1)
        self.assertEqual(len(self.cart.lines()), 1)

    def test_adding_twice_increments_the_same_line(self):
        self.cart.add(self.product)
        self.cart.add(self.product)

        self.assertEqual(len(self.cart.lines()), 1)
        self.assertEqual(self.cart.total_quantity, 2)

    def test_add_several_units_at_once(self):
        self.cart.add(self.product, quantity=3)
        self.assertEqual(self.cart.total_quantity, 3)

    def test_counter_sums_units_not_lines(self):
        other = make_product(sku="P-2", name="Dragão", price=Decimal("24.50"), stock_quantity=10)
        self.cart.add(self.product, quantity=2)
        self.cart.add(other, quantity=3)

        self.assertEqual(len(self.cart.lines()), 2)
        self.assertEqual(self.cart.total_quantity, 5)

    def test_subtotal_uses_decimal(self):
        self.cart.add(self.product, quantity=2)

        self.assertEqual(self.cart.subtotal, Decimal("17.80"))
        self.assertIsInstance(self.cart.subtotal, Decimal)

    def test_subtotal_of_several_lines(self):
        other = make_product(sku="P-2", name="Dragão", price=Decimal("24.50"), stock_quantity=10)
        self.cart.add(self.product, quantity=2)
        self.cart.add(other)

        self.assertEqual(self.cart.subtotal, Decimal("42.30"))

    def test_line_totals(self):
        self.cart.add(self.product, quantity=2)
        [line] = self.cart.lines()

        self.assertEqual(line.unit_price, Decimal("8.90"))
        self.assertEqual(line.total, Decimal("17.80"))
        self.assertEqual(line.quantity, 2)

    def test_survives_a_new_cart_instance(self):
        self.cart.add(self.product, quantity=2)
        other_instance = Cart(self.request)

        self.assertEqual(other_instance.total_quantity, 2)


class CartQuantityTests(TestCase):
    def setUp(self):
        self.request = make_request()
        self.cart = Cart(self.request)
        self.product = make_product(sku="P-1", name="Gato", price=Decimal("8.90"), stock_quantity=10)

    def test_set_quantity(self):
        result = self.cart.add(self.product)
        self.cart.set_quantity(result.key, 4)

        self.assertEqual(self.cart.total_quantity, 4)

    def test_quantity_zero_removes_the_line(self):
        result = self.cart.add(self.product)
        self.cart.set_quantity(result.key, 0)

        self.assertTrue(self.cart.is_empty)
        self.assertEqual(self.cart.lines(), [])

    def test_negative_quantity_removes_the_line(self):
        result = self.cart.add(self.product)
        self.cart.set_quantity(result.key, -3)

        self.assertTrue(self.cart.is_empty)

    def test_remove(self):
        added = self.cart.add(self.product)
        result = self.cart.remove(added.key)

        self.assertTrue(result.ok)
        self.assertTrue(self.cart.is_empty)

    def test_removing_something_absent(self):
        result = self.cart.remove("999:0:-")
        self.assertFalse(result.ok)

    def test_clear(self):
        self.cart.add(self.product, quantity=3)
        self.cart.clear()

        self.assertTrue(self.cart.is_empty)


class CartStockTests(TestCase):
    def setUp(self):
        self.request = make_request()
        self.cart = Cart(self.request)

    def test_out_of_stock_product_cannot_be_added(self):
        product = make_product(sku="P-0", name="Esgotado", stock_quantity=0)
        result = self.cart.add(product)

        self.assertFalse(result.ok)
        self.assertTrue(self.cart.is_empty)

    def test_quantity_is_capped_at_the_stock(self):
        product = make_product(sku="P-2", name="Dois", stock_quantity=2)
        result = self.cart.add(product, quantity=5)

        self.assertTrue(result.ok)
        self.assertEqual(result.level, "warning")
        self.assertEqual(self.cart.total_quantity, 2)

    def test_repeated_adds_do_not_pass_the_stock(self):
        product = make_product(sku="P-2", name="Dois", stock_quantity=2)
        self.cart.add(product, quantity=2)
        self.cart.add(product, quantity=2)

        self.assertEqual(self.cart.total_quantity, 2)

    def test_made_to_order_ignores_stock(self):
        product = make_product(
            sku="ENC-1", name="Sob encomenda", stock_quantity=0,
            made_to_order=True, production_lead_time_days=5,
        )
        result = self.cart.add(product, quantity=3)

        self.assertTrue(result.ok)
        self.assertEqual(self.cart.total_quantity, 3)

    def test_backorder_ignores_stock(self):
        product = make_product(sku="BO-1", name="Sob demanda", stock_quantity=0, allow_backorder=True)
        result = self.cart.add(product)

        self.assertTrue(result.ok)

    @override_settings(CART_MAX_QUANTITY_PER_LINE=5)
    def test_ceiling_per_line(self):
        product = make_product(
            sku="ENC-2", name="Sob encomenda", made_to_order=True, production_lead_time_days=3
        )
        self.cart.add(product, quantity=50)

        self.assertEqual(self.cart.total_quantity, 5)

    def test_max_quantity_helper(self):
        limited = make_product(sku="L-1", name="Limitado", stock_quantity=2)
        made_to_order = make_product(
            sku="L-2", name="Encomenda", made_to_order=True, production_lead_time_days=2
        )

        self.assertEqual(max_quantity_for(limited), 2)
        self.assertGreater(max_quantity_for(made_to_order), 2)

    def test_set_quantity_above_stock_is_capped(self):
        product = make_product(sku="P-3", name="Três", stock_quantity=3)
        added = self.cart.add(product)
        result = self.cart.set_quantity(added.key, 10)

        self.assertEqual(result.level, "warning")
        self.assertEqual(self.cart.total_quantity, 3)


class CartIntegrityTests(TestCase):
    """O carrinho não pode mostrar produto que saiu do ar."""

    def setUp(self):
        self.request = make_request()
        self.cart = Cart(self.request)

    def test_inactive_product_cannot_be_added(self):
        product = make_product(sku="D-1", name="Rascunho", status=ProductStatus.DRAFT)
        result = self.cart.add(product)

        self.assertFalse(result.ok)

    def test_product_that_became_inactive_disappears_from_the_cart(self):
        product = make_product(sku="A-1", name="Ativo", stock_quantity=5)
        self.cart.add(product, quantity=2)

        product.status = ProductStatus.INACTIVE
        product.save()

        fresh = Cart(self.request)
        self.assertEqual(fresh.lines(), [])
        self.assertEqual(fresh.total_quantity, 0)

    def test_deleted_product_disappears_from_the_cart(self):
        product = make_product(sku="A-2", name="Ativo", stock_quantity=5)
        self.cart.add(product)
        product.delete()

        fresh = Cart(self.request)
        self.assertEqual(fresh.lines(), [])

    def test_lines_do_not_query_per_item(self):
        category = make_category(slug="modelos", name="Modelos")
        for index in range(2):
            self.cart.add(make_product(sku=f"Q-{index}", name=f"P{index}", category=category, stock_quantity=5))

        baseline = self.count_queries()

        for index in range(2, 6):
            self.cart.add(make_product(sku=f"Q-{index}", name=f"P{index}", category=category, stock_quantity=5))

        # Mais itens no carrinho, mesmo número de consultas.
        with self.assertNumQueries(baseline):
            Cart(self.request).lines()

    def count_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            Cart(self.request).lines()
        return len(captured)

    def test_counter_does_not_touch_the_database(self):
        product = make_product(sku="Q-3", name="Três", stock_quantity=5)
        self.cart.add(product, quantity=2)

        fresh = Cart(self.request)
        with self.assertNumQueries(0):
            self.assertEqual(fresh.total_quantity, 2)
