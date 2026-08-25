"""Testes do modelo de produto: criação, unicidade, cálculos e validações."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.catalog.models import (
    Brand,
    Color,
    Material,
    PricingMode,
    Product,
    ProductStatus,
    ProductTranslation,
)
from apps.categories.models import Category, CategoryTranslation


def make_category(slug="modelos", name="Modelos", parent=None):
    category = Category.objects.create(slug=slug, parent=parent)
    CategoryTranslation.objects.create(master=category, language="pt", name=name)
    return category


def make_product(sku="GATO-01", name="Gato Pompom", **kwargs):
    product = Product.objects.create(sku=sku, **kwargs)
    if name:
        ProductTranslation.objects.create(master=product, language="pt", name=name)
        product.refresh_translations()
    return product


class ProductCreationTests(TestCase):
    def test_created_as_draft_by_default(self):
        product = make_product()
        self.assertEqual(product.status, ProductStatus.DRAFT)

    def test_sku_is_normalized_to_uppercase(self):
        product = make_product(sku=" gato-01 ")
        self.assertEqual(product.sku, "GATO-01")

    def test_slug_is_generated_from_sku_when_blank(self):
        product = make_product(sku="GATO-01", name=None)
        self.assertEqual(product.slug, "gato-01")

    def test_explicit_slug_is_kept(self):
        product = make_product(slug="gato-pompom")
        self.assertEqual(product.slug, "gato-pompom")

    def test_defaults(self):
        product = make_product()
        self.assertEqual(product.total_cost, Decimal("0.00"))
        self.assertEqual(product.stock_quantity, 0)
        self.assertFalse(product.is_featured)
        self.assertFalse(product.made_to_order)
        self.assertEqual(product.currency, "EUR")


class UniquenessTests(TestCase):
    def test_duplicate_sku_is_rejected(self):
        make_product(sku="GATO-01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="GATO-01")

    def test_duplicate_sku_is_rejected_case_insensitively(self):
        make_product(sku="GATO-01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="gato-01")

    def test_duplicate_slug_is_rejected(self):
        make_product(sku="GATO-01", slug="gato-pompom")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="GATO-02", slug="gato-pompom")

    def test_generated_slug_never_collides(self):
        first = make_product(sku="GATO-01", name="Gato Pompom", slug="gato-pompom")
        second = Product.objects.create(sku="GATO-02", slug="")
        ProductTranslation.objects.create(master=second, language="pt", name="Gato Pompom")
        second.refresh_translations()
        second.slug = ""
        second.save()
        self.assertNotEqual(second.slug, first.slug)


class CostTests(TestCase):
    def test_total_cost_is_calculated_on_save(self):
        product = make_product(filament_cost=Decimal("3.20"), energy_cost=Decimal("1.80"))
        self.assertEqual(product.total_cost, Decimal("5.00"))

    def test_total_cost_is_recalculated_when_a_cost_changes(self):
        product = make_product(filament_cost=Decimal("3.20"), energy_cost=Decimal("1.80"))
        product.energy_cost = Decimal("2.80")
        product.save()
        product.refresh_from_db()
        self.assertEqual(product.total_cost, Decimal("6.00"))

    def test_cost_components_are_extensible(self):
        product = make_product(filament_cost=Decimal("1.00"), energy_cost=Decimal("2.00"))
        self.assertEqual(set(product.cost_components()), {"filament", "energy"})


class PricingTests(TestCase):
    def test_margin_is_calculated_from_price(self):
        product = make_product(
            filament_cost=Decimal("3.00"),
            energy_cost=Decimal("2.00"),
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("10.00"),
        )
        self.assertEqual(product.total_cost, Decimal("5.00"))
        self.assertEqual(product.profit_margin, Decimal("50.00"))

    def test_price_is_calculated_from_margin(self):
        product = make_product(
            filament_cost=Decimal("3.00"),
            energy_cost=Decimal("2.00"),
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("50.00"),
        )
        self.assertEqual(product.sale_price, Decimal("10.00"))

    def test_saving_repeatedly_does_not_drift(self):
        """Nenhum loop preço -> margem -> preço: o modo define a fonte de verdade."""
        product = make_product(
            filament_cost=Decimal("3.33"),
            energy_cost=Decimal("0.00"),
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("33.33"),
        )
        first_price = product.sale_price
        for _ in range(5):
            product.save()
        self.assertEqual(product.sale_price, first_price)
        self.assertEqual(product.profit_margin, Decimal("33.33"))

    def test_switching_mode_recalculates_the_other_side(self):
        product = make_product(
            filament_cost=Decimal("5.00"),
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("10.00"),
        )
        self.assertEqual(product.profit_margin, Decimal("50.00"))

        product.pricing_mode = PricingMode.MARGIN
        product.profit_margin = Decimal("75.00")
        product.save()
        self.assertEqual(product.sale_price, Decimal("20.00"))

    def test_effective_margin_reflects_the_stored_price(self):
        product = make_product(
            filament_cost=Decimal("3.33"),
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("33.33"),
        )
        self.assertIsNotNone(product.effective_margin)
        self.assertLess(abs(product.effective_margin - Decimal("33.33")), Decimal("0.10"))

    def test_zero_cost_yields_one_hundred_percent_margin(self):
        product = make_product(sku="P-100", sale_price=Decimal("10.00"))
        self.assertEqual(product.total_cost, Decimal("0.00"))
        self.assertEqual(product.profit_margin, Decimal("100.00"))

    def test_selling_below_cost_yields_negative_margin(self):
        product = make_product(
            sku="P-NEG", filament_cost=Decimal("10.00"), sale_price=Decimal("8.00")
        )
        self.assertEqual(product.profit_margin, Decimal("-25.00"))

    def test_margin_is_cleared_when_price_is_removed(self):
        product = make_product(sku="P-CLR", sale_price=Decimal("10.00"))
        self.assertIsNotNone(product.profit_margin)
        product.sale_price = None
        product.save()
        self.assertIsNone(product.profit_margin)

    def test_profit_per_unit(self):
        product = make_product(
            filament_cost=Decimal("4.00"),
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("11.00"),
        )
        self.assertEqual(product.profit, Decimal("7.00"))


class ValidationTests(TestCase):
    def test_negative_cost_is_rejected_by_validation(self):
        product = Product(sku="X-1", filament_cost=Decimal("-1.00"))
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("filament_cost", context.exception.message_dict)

    def test_negative_cost_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="X-1", filament_cost=Decimal("-1.00"))

    def test_negative_price_is_rejected(self):
        product = Product(sku="X-2", sale_price=Decimal("-5.00"))
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("sale_price", context.exception.message_dict)

    def test_negative_weight_and_dimensions_are_rejected(self):
        product = Product(sku="X-3", weight_grams=Decimal("-1"), width=Decimal("-2"))
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("weight_grams", context.exception.message_dict)
        self.assertIn("width", context.exception.message_dict)

    def test_margin_input_out_of_bounds_is_rejected(self):
        product = Product(
            sku="X-4", pricing_mode=PricingMode.MARGIN, profit_margin=Decimal("100.00")
        )
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("profit_margin", context.exception.message_dict)

    def test_negative_print_time_is_rejected(self):
        product = Product(sku="X-5", print_time=timedelta(hours=-1))
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("print_time", context.exception.message_dict)

    def test_margin_mode_requires_a_margin(self):
        product = Product(sku="X-6", pricing_mode=PricingMode.MARGIN)
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("profit_margin", context.exception.message_dict)

    def test_active_product_requires_category_price_and_name(self):
        product = make_product(sku="X-7", name=None)
        product.status = ProductStatus.ACTIVE
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        errors = context.exception.message_dict
        self.assertIn("category", errors)
        self.assertIn("sale_price", errors)
        self.assertIn("status", errors)

    def test_active_product_with_minimum_information_is_valid(self):
        category = make_category()
        product = make_product(
            sku="X-8",
            name="Gato Pompom",
            category=category,
            filament_cost=Decimal("2.00"),
            sale_price=Decimal("9.90"),
        )
        product.status = ProductStatus.ACTIVE
        product.full_clean()
        product.save()
        self.assertEqual(product.status, ProductStatus.ACTIVE)


class MadeToOrderTests(TestCase):
    def test_lead_time_is_required(self):
        product = Product(sku="ENC-1", made_to_order=True)
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("production_lead_time_days", context.exception.message_dict)

    def test_made_to_order_product_is_available_without_stock(self):
        product = make_product(
            sku="ENC-2", made_to_order=True, production_lead_time_days=5, stock_quantity=0
        )
        self.assertTrue(product.is_available)

    def test_regular_product_without_stock_is_unavailable(self):
        product = make_product(sku="ENC-3", stock_quantity=0)
        self.assertFalse(product.is_available)

    def test_backorder_makes_product_available(self):
        product = make_product(sku="ENC-4", stock_quantity=0, allow_backorder=True)
        self.assertTrue(product.is_available)

    def test_negative_stock_is_impossible(self):
        with self.assertRaises((IntegrityError, ValueError)), transaction.atomic():
            Product.objects.create(sku="ENC-5", stock_quantity=-1)


class RelationTests(TestCase):
    def test_product_belongs_to_a_category(self):
        root = make_category(slug="modelos", name="Modelos")
        animals = make_category(slug="animais", name="Animais", parent=root)
        product = make_product(category=animals)

        self.assertEqual(product.category, animals)
        self.assertEqual(product.category.parent, root)
        self.assertIn(product, root.children.first().products.all())

    def test_category_with_products_cannot_be_deleted(self):
        category = make_category()
        make_product(category=category)
        with self.assertRaises(ProtectedError):
            category.delete()

    def test_product_with_multiple_materials(self):
        pla = Material.objects.create(name="PLA")
        wood = Material.objects.create(name="Madeira")
        product = make_product()
        product.materials.set([pla, wood])

        self.assertEqual(product.materials.count(), 2)
        self.assertIn(product, pla.products.all())

    def test_product_with_multiple_colors(self):
        black = Color.objects.create(name="Preto", hex_code="#000000")
        white = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        product = make_product()
        product.colors.set([black, white])

        self.assertEqual(product.colors.count(), 2)
        self.assertEqual(black.rgb, (0, 0, 0))

    def test_brand_is_optional(self):
        product = make_product()
        self.assertIsNone(product.brand)

        brand = Brand.objects.create(name="Bambu Lab")
        product.brand = brand
        product.save()
        self.assertEqual(Product.objects.get(pk=product.pk).brand, brand)


class PhysicalInformationTests(TestCase):
    def test_dimensions_are_stored_separately(self):
        product = make_product(
            width=Decimal("50"), height=Decimal("20"), depth=Decimal("5"), dimension_unit="mm"
        )
        self.assertEqual(product.width, Decimal("50"))
        self.assertIn("mm", product.dimensions_display())

    def test_dimensions_are_convertible_to_mm(self):
        product = make_product(width=Decimal("5"), height=Decimal("2"), dimension_unit="cm")
        width_mm, height_mm, depth_mm = product.dimensions_in_mm()
        self.assertEqual(width_mm, Decimal("50"))
        self.assertEqual(height_mm, Decimal("20"))
        self.assertIsNone(depth_mm)

    def test_weight_is_stored_in_grams(self):
        product = make_product(weight_grams=Decimal("35"))
        self.assertEqual(product.weight_kg, Decimal("0.035"))

    def test_print_time_is_a_duration(self):
        product = make_product(print_time=timedelta(hours=2, minutes=35))
        product.refresh_from_db()
        self.assertEqual(product.print_time, timedelta(hours=2, minutes=35))
        self.assertEqual(product.print_time_display(), "2h 35min")


class FeaturedTests(TestCase):
    def test_featured_queryset_respects_order_and_status(self):
        category = make_category()
        common = {
            "category": category,
            "sale_price": Decimal("10.00"),
            "status": ProductStatus.ACTIVE,
        }
        second = make_product(sku="F-2", is_featured=True, featured_order=2, **common)
        first = make_product(sku="F-1", is_featured=True, featured_order=1, **common)
        make_product(sku="F-3", is_featured=False, **common)
        make_product(sku="F-4", is_featured=True, featured_order=0, status=ProductStatus.DRAFT)

        self.assertEqual(list(Product.objects.featured()), [first, second])
