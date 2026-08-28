"""Testes da variante — a **unidade comercial vendável**.

Preço, custo, margem, estoque, peso, dimensões, tempo de impressão e prazo de
produção vivem aqui. Nada disso é herdado do produto: desde a etapa 8 o valor
da variante **é** o valor, e produto sem variante não vende.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import (
    Color,
    Material,
    PricingMode,
    Product,
    ProductStatus,
    ProductVariant,
)
from apps.core.testing import make_category, make_product


def make_variant(product, sku="VAR-1", price=Decimal("15.00"), **kwargs):
    kwargs.setdefault("sale_price", price)
    return ProductVariant.objects.create(product=product, sku=sku, **kwargs)


class VariantCarriesTheCommercialDataTests(TestCase):
    """O que era do produto agora é da variante — e só dela."""

    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", with_variant=False)

    def test_price_is_the_variants_own(self):
        variant = make_variant(self.product, price=Decimal("18.00"))
        self.assertEqual(variant.sale_price, Decimal("18.00"))

    def test_price_is_not_inherited_from_the_product(self):
        """Não existe mais herança: sem preço próprio, a variante não tem preço."""
        variant = ProductVariant(product=self.product, sku="V-SEM", size="300 ml")
        self.assertIsNone(variant.sale_price)

    def test_weight_is_the_variants_own(self):
        variant = make_variant(self.product, weight_grams=Decimal("420"))
        self.assertEqual(variant.weight_grams, Decimal("420"))
        self.assertEqual(variant.weight_kg, Decimal("0.420"))

    def test_weight_is_not_inherited_from_the_product(self):
        variant = make_variant(self.product, sku="V-LEVE")
        self.assertIsNone(variant.weight_grams)

    def test_two_sizes_have_two_weights(self):
        """O ponto da etapa 8: 25 cm não pesa o que pesa 10 cm."""
        pequena = make_variant(
            self.product, sku="V-P", size="10 cm", weight_grams=Decimal("150")
        )
        grande = make_variant(
            self.product, sku="V-G", size="25 cm", weight_grams=Decimal("400")
        )

        self.assertEqual(pequena.weight_grams, Decimal("150"))
        self.assertEqual(grande.weight_grams, Decimal("400"))

    def test_dimensions_are_the_variants_own(self):
        variant = make_variant(
            self.product,
            width=Decimal("50"),
            height=Decimal("20"),
            depth=Decimal("5"),
            dimension_unit="mm",
        )
        width, height, depth = variant.dimensions

        self.assertEqual((width, height, depth), (Decimal("50"), Decimal("20"), Decimal("5")))
        self.assertIn("mm", variant.dimensions_display())

    def test_dimensions_are_convertible_to_mm(self):
        variant = make_variant(
            self.product, width=Decimal("5"), height=Decimal("2"), dimension_unit="cm"
        )
        width_mm, height_mm, depth_mm = variant.dimensions_in_mm()

        self.assertEqual(width_mm, Decimal("50"))
        self.assertEqual(height_mm, Decimal("20"))
        self.assertIsNone(depth_mm)

    def test_print_time_is_a_duration(self):
        variant = make_variant(self.product, print_time=timedelta(hours=2, minutes=35))
        variant.refresh_from_db()

        self.assertEqual(variant.print_time, timedelta(hours=2, minutes=35))
        self.assertEqual(variant.print_time_display(), "2h 35min")

    def test_production_lead_time_is_the_variants_own(self):
        rapida = make_variant(self.product, sku="V-R", size="P", production_lead_time_days=2)
        lenta = make_variant(self.product, sku="V-L", size="G", production_lead_time_days=6)

        self.assertEqual(rapida.production_lead_time_days, 2)
        self.assertEqual(lenta.production_lead_time_days, 6)


class VariantCostAndPricingTests(TestCase):
    """Custo, margem e preço — a mesma matemática de antes, na tabela certa."""

    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", with_variant=False)

    def make(self, **kwargs):
        kwargs.setdefault("sku", "V-1")
        kwargs.setdefault("product", self.product)
        return ProductVariant.objects.create(**kwargs)

    def test_total_cost_is_calculated_on_save(self):
        variant = self.make(filament_cost=Decimal("3.20"), energy_cost=Decimal("1.80"))
        self.assertEqual(variant.total_cost, Decimal("5.00"))

    def test_total_cost_is_recalculated_when_a_cost_changes(self):
        variant = self.make(filament_cost=Decimal("3.20"), energy_cost=Decimal("1.80"))
        variant.energy_cost = Decimal("2.80")
        variant.save()
        variant.refresh_from_db()
        self.assertEqual(variant.total_cost, Decimal("6.00"))

    def test_cost_components_are_extensible(self):
        variant = self.make(filament_cost=Decimal("1.00"), energy_cost=Decimal("2.00"))
        self.assertEqual(set(variant.cost_components()), {"filament", "energy"})

    def test_margin_is_calculated_from_price(self):
        variant = self.make(
            filament_cost=Decimal("3.00"),
            energy_cost=Decimal("2.00"),
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("10.00"),
        )
        self.assertEqual(variant.total_cost, Decimal("5.00"))
        self.assertEqual(variant.profit_margin, Decimal("50.00"))

    def test_price_is_calculated_from_margin(self):
        variant = self.make(
            filament_cost=Decimal("3.00"),
            energy_cost=Decimal("2.00"),
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("50.00"),
        )
        self.assertEqual(variant.sale_price, Decimal("10.00"))

    def test_saving_repeatedly_does_not_drift(self):
        """Nenhum loop preço -> margem -> preço: o modo define a fonte da verdade."""
        variant = self.make(
            filament_cost=Decimal("3.33"),
            energy_cost=Decimal("0.00"),
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("33.33"),
        )
        first_price = variant.sale_price
        for _ in range(5):
            variant.save()
        self.assertEqual(variant.sale_price, first_price)
        self.assertEqual(variant.profit_margin, Decimal("33.33"))

    def test_switching_mode_recalculates_the_other_side(self):
        variant = self.make(
            filament_cost=Decimal("5.00"),
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("10.00"),
        )
        self.assertEqual(variant.profit_margin, Decimal("50.00"))

        variant.pricing_mode = PricingMode.MARGIN
        variant.profit_margin = Decimal("75.00")
        variant.save()
        self.assertEqual(variant.sale_price, Decimal("20.00"))

    def test_effective_margin_reflects_the_stored_price(self):
        variant = self.make(
            filament_cost=Decimal("3.33"),
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("33.33"),
        )
        self.assertIsNotNone(variant.effective_margin)
        self.assertLess(abs(variant.effective_margin - Decimal("33.33")), Decimal("0.10"))

    def test_zero_cost_yields_one_hundred_percent_margin(self):
        variant = self.make(sale_price=Decimal("10.00"))
        self.assertEqual(variant.total_cost, Decimal("0.00"))
        self.assertEqual(variant.profit_margin, Decimal("100.00"))

    def test_selling_below_cost_yields_negative_margin(self):
        variant = self.make(filament_cost=Decimal("10.00"), sale_price=Decimal("8.00"))
        self.assertEqual(variant.profit_margin, Decimal("-25.00"))

    def test_margin_is_cleared_when_price_is_removed(self):
        variant = self.make(sale_price=Decimal("10.00"))
        self.assertIsNotNone(variant.profit_margin)

        variant.sale_price = None
        variant.save()
        self.assertIsNone(variant.profit_margin)

    def test_profit_per_unit(self):
        variant = self.make(
            filament_cost=Decimal("4.00"),
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("11.00"),
        )
        self.assertEqual(variant.profit, Decimal("7.00"))


class VariantAvailabilityTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", with_variant=False)
        self.black = Color.objects.create(name="Preto", hex_code="#000000")
        self.white = Color.objects.create(name="Branco", hex_code="#FFFFFF")

    def test_stock_is_per_variant(self):
        black = make_variant(self.product, sku="V-1", color=self.black, stock_quantity=10)
        white = make_variant(self.product, sku="V-2", color=self.white, stock_quantity=0)

        self.assertTrue(black.is_available)
        self.assertFalse(white.is_available)

    def test_made_to_order_variant_ignores_stock(self):
        variant = make_variant(
            self.product, stock_quantity=0, made_to_order=True, production_lead_time_days=5
        )
        self.assertTrue(variant.is_available)
        self.assertEqual(variant.stock_state, "made_to_order")

    def test_backorder_variant_ignores_stock(self):
        variant = make_variant(self.product, stock_quantity=0, allow_backorder=True)
        self.assertTrue(variant.is_available)
        self.assertEqual(variant.stock_state, "in")

    def test_regular_variant_without_stock_is_unavailable(self):
        variant = make_variant(self.product, stock_quantity=0)
        self.assertFalse(variant.is_available)
        self.assertEqual(variant.stock_state, "out")

    def test_low_stock_is_announced(self):
        variant = make_variant(self.product, stock_quantity=2)
        self.assertEqual(variant.stock_state, "low")

    def test_inactive_variant_is_never_available(self):
        variant = make_variant(self.product, stock_quantity=10, is_active=False)
        self.assertFalse(variant.is_available)

    def test_negative_stock_is_impossible(self):
        with self.assertRaises((IntegrityError, ValueError)), transaction.atomic():
            ProductVariant.objects.create(
                product=self.product, sku="V-NEG", sale_price=Decimal("1.00"), stock_quantity=-1
            )


class VariantLabelTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", with_variant=False)
        self.black = Color.objects.create(name="Preto", hex_code="#000000")
        self.pla = Material.objects.create(name="PLA")

    def test_label_joins_the_axes(self):
        variant = make_variant(self.product, color=self.black, size="300 ml")
        self.assertEqual(variant.label, "Preto · 300 ml")

    def test_label_includes_the_material(self):
        variant = make_variant(
            self.product, color=self.black, size="300 ml", material=self.pla
        )
        self.assertEqual(variant.label, "Preto · 300 ml · PLA")

    def test_single_option_variant_has_an_empty_label(self):
        """Produto de opção única não precisa de rótulo — mas precisa aparecer."""
        variant = make_variant(self.product)
        self.assertEqual(variant.label, "")
        self.assertEqual(variant.display_label, "Caneca")


class VariantValidationTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", with_variant=False)
        self.color = Color.objects.create(name="Preto", hex_code="#000000")

    def test_sku_is_unique(self):
        make_variant(self.product, sku="V-1", size="300 ml")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(
                product=self.product, sku="V-1", size="500 ml", sale_price=Decimal("1.00")
            )

    def test_sku_is_normalized(self):
        variant = make_variant(self.product, sku=" v-9 ", size="300 ml")
        self.assertEqual(variant.sku, "V-9")

    def test_sku_is_required(self):
        variant = ProductVariant(product=self.product, sku="", sale_price=Decimal("1.00"))
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("sku", context.exception.message_dict)

    def test_single_option_variant_is_legitimate(self):
        """Todos os eixos vazios é o produto que só tem um jeito de ser vendido."""
        variant = ProductVariant(
            product=self.product, sku="V-UNICA", sale_price=Decimal("10.00")
        )
        variant.full_clean()

    def test_duplicate_combination_is_rejected(self):
        make_variant(self.product, sku="V-1", color=self.color, size="300 ml")
        duplicate = ProductVariant(
            product=self.product,
            sku="V-2",
            color=self.color,
            size="300 ml",
            sale_price=Decimal("10.00"),
        )
        with self.assertRaises(ValidationError) as context:
            duplicate.full_clean()
        self.assertIn("size", context.exception.message_dict)

    def test_two_single_option_variants_are_rejected(self):
        make_variant(self.product, sku="V-1")
        duplicate = ProductVariant(
            product=self.product, sku="V-2", sale_price=Decimal("10.00")
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_active_variant_needs_a_price(self):
        variant = ProductVariant(product=self.product, sku="V-3", size="300 ml")
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("sale_price", context.exception.message_dict)

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

    def test_negative_cost_is_rejected_by_validation(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-5",
            size="300 ml",
            sale_price=Decimal("10.00"),
            filament_cost=Decimal("-1.00"),
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("filament_cost", context.exception.message_dict)

    def test_negative_cost_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(
                product=self.product,
                sku="V-6",
                size="300 ml",
                sale_price=Decimal("10.00"),
                filament_cost=Decimal("-1.00"),
            )

    def test_negative_weight_and_dimensions_are_rejected(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-7",
            size="300 ml",
            sale_price=Decimal("10.00"),
            weight_grams=Decimal("-1"),
            width=Decimal("-2"),
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        errors = context.exception.message_dict
        self.assertIn("weight_grams", errors)
        self.assertIn("width", errors)

    def test_negative_print_time_is_rejected(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-8",
            size="300 ml",
            sale_price=Decimal("10.00"),
            print_time=timedelta(hours=-1),
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("print_time", context.exception.message_dict)

    def test_margin_mode_requires_a_margin(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-9",
            size="300 ml",
            pricing_mode=PricingMode.MARGIN,
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("profit_margin", context.exception.message_dict)

    def test_margin_input_out_of_bounds_is_rejected(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-10",
            size="300 ml",
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("100.00"),
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("profit_margin", context.exception.message_dict)

    def test_made_to_order_requires_a_lead_time(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-11",
            size="300 ml",
            sale_price=Decimal("10.00"),
            made_to_order=True,
        )
        with self.assertRaises(ValidationError) as context:
            variant.full_clean()
        self.assertIn("production_lead_time_days", context.exception.message_dict)

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
            sku="CANECA",
            name="Caneca",
            category=self.category,
            status=ProductStatus.ACTIVE,
            with_variant=False,
        )

    def test_card_sends_to_the_product_page_when_there_are_options(self):
        make_variant(self.product, sku="V-1", size="300 ml", stock_quantity=5)
        make_variant(self.product, sku="V-2", size="500 ml", stock_quantity=5)
        response = self.client.get("/modelos/")

        self.assertContains(response, "Escolher opções")
        self.assertContains(response, self.product.get_absolute_url())

    def test_card_shows_sold_out_when_no_variant_has_stock(self):
        make_variant(self.product, sku="V-1", size="300 ml", stock_quantity=0)
        response = self.client.get("/modelos/")

        self.assertContains(response, "Esgotado")

    def test_product_without_variant_is_out_of_the_shop(self):
        response = self.client.get("/modelos/")

        self.assertNotContains(response, self.product.get_absolute_url())

    def test_card_shows_the_variant_price(self):
        make_variant(self.product, sku="V-1", price=Decimal("17.50"), stock_quantity=5)
        response = self.client.get("/modelos/")

        self.assertContains(response, "17,50")

    def test_card_shows_a_range_when_the_variants_differ(self):
        make_variant(self.product, sku="V-1", size="P", price=Decimal("15.00"), stock_quantity=5)
        make_variant(self.product, sku="V-2", size="G", price=Decimal("22.00"), stock_quantity=5)
        response = self.client.get("/modelos/")

        self.assertContains(response, "a partir de")
        self.assertContains(response, "15,00")

    def test_variant_material_and_color_are_selectable(self):
        color = Color.objects.create(name="Preto", hex_code="#000000")
        material = Material.objects.create(name="PLA")
        variant = make_variant(
            self.product,
            sku="V-1",
            color=color,
            material=material,
            size="300 ml",
            stock_quantity=3,
        )

        self.assertEqual(variant.label, "Preto · 300 ml · PLA")


class VariantSkuTests(TestCase):
    """SKU da variante é global: dois produtos não podem repetir a mesma peça."""

    def test_sku_is_unique_across_products(self):
        first = make_product(sku="P-1", name="Um", with_variant=False)
        second = make_product(sku="P-2", name="Dois", with_variant=False)
        make_variant(first, sku="COMPARTILHADO")

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(
                product=second, sku="COMPARTILHADO", sale_price=Decimal("10.00")
            )

    def test_migrated_product_kept_its_sku_on_the_variant(self):
        """A migração 0004 reaproveita o SKU do produto na variante criada."""
        product = make_product(sku="ANTIGO-01", name="Antigo")

        self.assertEqual(product.default_variant.sku, "ANTIGO-01")
        self.assertEqual(Product.objects.get(pk=product.pk).sku, "ANTIGO-01")


class VariantCostChangeTests(TestCase):
    """Mexer no custo recalcula o lado derivado — e só ele.

    ``pricing_mode`` é onde o administrador declara o que está digitando. É por
    isso que não existe ciclo margem → preço → margem: só o outro lado é
    reescrito, nunca o que ele digitou.
    """

    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca", with_variant=False)

    def make(self, **kwargs):
        kwargs.setdefault("sku", "V-1")
        kwargs.setdefault("product", self.product)
        return ProductVariant.objects.create(**kwargs)

    def test_raising_the_cost_in_price_mode_eats_the_margin(self):
        """Modo preço: o preço é a entrada, a margem cede."""
        variant = self.make(
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("10.00"),
            filament_cost=Decimal("5.00"),
        )
        self.assertEqual(variant.profit_margin, Decimal("50.00"))

        variant.filament_cost = Decimal("8.00")
        variant.save()

        self.assertEqual(variant.sale_price, Decimal("10.00"))
        self.assertEqual(variant.profit_margin, Decimal("20.00"))

    def test_raising_the_cost_in_margin_mode_raises_the_price(self):
        """Modo margem: a margem é a entrada, o preço sobe junto com o custo."""
        variant = self.make(
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("50.00"),
            filament_cost=Decimal("5.00"),
        )
        self.assertEqual(variant.sale_price, Decimal("10.00"))

        variant.filament_cost = Decimal("8.00")
        variant.save()

        self.assertEqual(variant.profit_margin, Decimal("50.00"))
        self.assertEqual(variant.sale_price, Decimal("16.00"))

    def test_a_second_cost_component_counts(self):
        variant = self.make(
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("50.00"),
            filament_cost=Decimal("3.00"),
            energy_cost=Decimal("2.00"),
        )

        self.assertEqual(variant.total_cost, Decimal("5.00"))
        self.assertEqual(variant.sale_price, Decimal("10.00"))

    def test_margin_is_not_markup(self):
        """50% de margem sobre custo 5 é preço 10 — não 7,50."""
        variant = self.make(
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("50.00"),
            filament_cost=Decimal("5.00"),
        )

        self.assertEqual(variant.sale_price, Decimal("10.00"))
        self.assertNotEqual(variant.sale_price, Decimal("7.50"))

    def test_rounding_is_commercial_half_up(self):
        """Custo 3,33 a 33,33% dá 4,9948 — grava 4,99, não 4,98."""
        variant = self.make(
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("33.33"),
            filament_cost=Decimal("3.33"),
        )

        self.assertEqual(variant.sale_price, Decimal("4.99"))
        self.assertEqual(variant.sale_price.as_tuple().exponent, -2)

    def test_zero_cost_keeps_the_price_and_gives_full_margin(self):
        variant = self.make(pricing_mode=PricingMode.PRICE, sale_price=Decimal("9.90"))

        self.assertEqual(variant.total_cost, Decimal("0.00"))
        self.assertEqual(variant.profit_margin, Decimal("100.00"))
        self.assertEqual(variant.profit, Decimal("9.90"))

    def test_zero_price_has_no_margin(self):
        """Margem sobre preço zero é indefinida — não é zero, é nada."""
        variant = self.make(
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("0.00"),
            filament_cost=Decimal("2.00"),
            is_active=False,
        )

        self.assertIsNone(variant.profit_margin)

    def test_a_margin_of_one_hundred_percent_is_refused(self):
        """Seria dividir por zero. O formulário recusa antes de calcular."""
        variant = ProductVariant(
            product=self.product,
            sku="V-100",
            size="P",
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("100.00"),
        )
        with self.assertRaises(ValidationError) as contexto:
            variant.full_clean()

        self.assertIn("profit_margin", contexto.exception.message_dict)

    def test_a_negative_margin_input_is_refused(self):
        variant = ProductVariant(
            product=self.product,
            sku="V-NEG",
            size="P",
            pricing_mode=PricingMode.MARGIN,
            profit_margin=Decimal("-10.00"),
        )
        with self.assertRaises(ValidationError) as contexto:
            variant.full_clean()

        self.assertIn("profit_margin", contexto.exception.message_dict)

    def test_selling_below_cost_is_allowed_and_shows_a_negative_margin(self):
        """Liquidação é legítima. O que não pode é a margem sumir."""
        variant = self.make(
            pricing_mode=PricingMode.PRICE,
            sale_price=Decimal("8.00"),
            filament_cost=Decimal("10.00"),
        )

        self.assertEqual(variant.profit_margin, Decimal("-25.00"))
        self.assertEqual(variant.profit, Decimal("-2.00"))
