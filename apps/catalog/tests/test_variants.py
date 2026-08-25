"""Testes das variantes de produto."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Color, Material, Product, ProductVariant
from apps.core.testing import make_category, make_product


def make_variant(product, sku="VAR-1", **kwargs):
    return ProductVariant.objects.create(product=product, sku=sku, **kwargs)


class VariantBasicsTests(TestCase):
    def setUp(self):
        self.product = make_product(
            sku="CANECA", name="Caneca", price=Decimal("15.00"), stock_quantity=7,
            weight_grams=Decimal("300"),
        )
        self.black = Color.objects.create(name="Preto", hex_code="#000000")
        self.white = Color.objects.create(name="Branco", hex_code="#FFFFFF")

    def test_product_without_variants_behaves_as_before(self):
        self.assertFalse(self.product.has_variants)
        self.assertEqual(self.product.available_stock, 7)
        self.assertTrue(self.product.is_available)
        self.assertEqual(self.product.price_range, (Decimal("15.00"), Decimal("15.00")))

    def test_variant_inherits_price_from_the_product(self):
        variant = make_variant(self.product, color=self.black, size="300 ml")
        self.assertEqual(variant.effective_price, Decimal("15.00"))

    def test_variant_can_override_the_price(self):
        variant = make_variant(
            self.product, color=self.black, size="500 ml", sale_price=Decimal("18.00")
        )
        self.assertEqual(variant.effective_price, Decimal("18.00"))

    def test_variant_inherits_weight(self):
        variant = make_variant(self.product, size="300 ml")
        self.assertEqual(variant.effective_weight, Decimal("300"))

    def test_variant_can_override_weight_and_dimensions(self):
        variant = make_variant(
            self.product, size="500 ml", weight_grams=Decimal("420"), width=Decimal("9")
        )
        width, height, depth = variant.effective_dimensions

        self.assertEqual(variant.effective_weight, Decimal("420"))
        self.assertEqual(width, Decimal("9"))
        self.assertIsNone(height)

    def test_label(self):
        variant = make_variant(self.product, color=self.black, size="300 ml")
        self.assertEqual(variant.label, "Preto · 300 ml")

    def test_stock_is_per_variant(self):
        black = make_variant(self.product, sku="V-1", color=self.black, stock_quantity=10)
        white = make_variant(self.product, sku="V-2", color=self.white, stock_quantity=0)

        self.assertTrue(black.is_available)
        self.assertFalse(white.is_available)

    def test_product_stock_is_the_sum_of_the_variants(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=10)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=5)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.available_stock, 15)

    def test_product_is_available_when_any_variant_is(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=0)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=3)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertTrue(product.is_available)

    def test_product_is_unavailable_when_no_variant_is(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=0)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=0)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertFalse(product.is_available)

    def test_inactive_variant_is_ignored(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=5, is_active=False)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertFalse(product.has_variants)
        self.assertEqual(product.available_stock, 7)  # volta ao estoque do produto

    def test_price_range(self):
        make_variant(self.product, sku="V-1", size="300 ml", sale_price=Decimal("15.00"))
        make_variant(self.product, sku="V-2", size="500 ml", sale_price=Decimal("18.00"))

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.price_range, (Decimal("15.00"), Decimal("18.00")))
        self.assertTrue(product.has_price_range)

    def test_made_to_order_variant_ignores_stock(self):
        self.product.made_to_order = True
        self.product.production_lead_time_days = 5
        self.product.save()
        variant = make_variant(self.product, size="300 ml", stock_quantity=0)

        self.assertTrue(variant.is_available)


class VariantValidationTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", price=Decimal("15.00"))
        self.color = Color.objects.create(name="Preto", hex_code="#000000")

    def test_sku_is_unique(self):
        make_variant(self.product, sku="V-1", size="300 ml")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(product=self.product, sku="V-1", size="500 ml")

    def test_sku_is_normalized(self):
        variant = make_variant(self.product, sku=" v-9 ", size="300 ml")
        self.assertEqual(variant.sku, "V-9")

    def test_variant_needs_at_least_one_axis(self):
        variant = ProductVariant(product=self.product, sku="V-2")
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("size", context.exception.message_dict)

    def test_duplicate_combination_is_rejected(self):
        make_variant(self.product, sku="V-1", color=self.color, size="300 ml")
        duplicate = ProductVariant(
            product=self.product, sku="V-2", color=self.color, size="300 ml"
        )
        with self.assertRaises(ValidationError) as context:
            duplicate.full_clean()
        self.assertIn("size", context.exception.message_dict)

    def test_negative_price_is_rejected(self):
        variant = ProductVariant(
            product=self.product, sku="V-3", size="300 ml", sale_price=Decimal("-1.00")
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("sale_price", context.exception.message_dict)

    def test_negative_price_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(
                product=self.product, sku="V-4", size="300 ml", sale_price=Decimal("-1.00")
            )

    def test_variants_die_with_the_product(self):
        make_variant(self.product, sku="V-1", size="300 ml")
        self.product.delete()
        self.assertEqual(ProductVariant.objects.count(), 0)

    def test_color_in_use_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        make_variant(self.product, sku="V-1", color=self.color)
        with self.assertRaises(ProtectedError):
            self.color.delete()


class VariantShopTests(TestCase):
    """O card do Shop precisa refletir as variantes."""

    def setUp(self):
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(
            sku="CANECA", name="Caneca", category=self.category,
            price=Decimal("15.00"), stock_quantity=0,
        )

    def test_card_sends_to_the_product_page_when_there_are_variants(self):
        make_variant(self.product, sku="V-1", size="300 ml", stock_quantity=5)
        response = self.client.get("/modelos/")

        self.assertContains(response, "Escolher opções")
        self.assertContains(response, self.product.get_absolute_url())

    def test_card_shows_sold_out_when_no_variant_has_stock(self):
        make_variant(self.product, sku="V-1", size="300 ml", stock_quantity=0)
        response = self.client.get("/modelos/")

        self.assertContains(response, "Esgotado")

    def test_variant_material_and_color_are_selectable(self):
        color = Color.objects.create(name="Preto", hex_code="#000000")
        material = Material.objects.create(name="PLA")
        variant = make_variant(
            self.product, sku="V-1", color=color, material=material, size="300 ml",
            stock_quantity=3,
        )

        self.assertEqual(variant.label, "Preto · 300 ml · PLA")
