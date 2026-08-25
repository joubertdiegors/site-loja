"""Testes das funções puras de custo/preço/margem."""

from decimal import Decimal

from django.test import SimpleTestCase

from apps.catalog import pricing


class TotalCostTests(SimpleTestCase):
    def test_sums_components(self):
        total = pricing.total_cost({"filament": Decimal("3.20"), "energy": Decimal("1.80")})
        self.assertEqual(total, Decimal("5.00"))

    def test_extra_components_are_included(self):
        # Simula a inclusão futura de mão de obra e embalagem.
        total = pricing.total_cost(
            {
                "filament": Decimal("3.20"),
                "energy": Decimal("1.80"),
                "labor": Decimal("2.00"),
                "packaging": Decimal("0.50"),
            }
        )
        self.assertEqual(total, Decimal("7.50"))

    def test_empty_components(self):
        self.assertEqual(pricing.total_cost({}), Decimal("0.00"))


class MarginTests(SimpleTestCase):
    def test_margin_from_price(self):
        self.assertEqual(
            pricing.margin_from_price(Decimal("5.00"), Decimal("10.00")), Decimal("50.00")
        )

    def test_margin_is_not_markup(self):
        # Markup seria 100%; margem sobre o preço de venda é 50%.
        self.assertNotEqual(
            pricing.margin_from_price(Decimal("5.00"), Decimal("10.00")), Decimal("100.00")
        )

    def test_margin_undefined_for_zero_price(self):
        self.assertIsNone(pricing.margin_from_price(Decimal("5.00"), Decimal("0.00")))

    def test_negative_margin_when_selling_below_cost(self):
        self.assertEqual(
            pricing.margin_from_price(Decimal("10.00"), Decimal("8.00")), Decimal("-25.00")
        )


class PriceFromMarginTests(SimpleTestCase):
    def test_price_from_margin(self):
        self.assertEqual(
            pricing.price_from_margin(Decimal("5.00"), Decimal("50.00")), Decimal("10.00")
        )

    def test_round_trip_is_stable(self):
        price = pricing.price_from_margin(Decimal("7.35"), Decimal("42.00"))
        margin = pricing.margin_from_price(Decimal("7.35"), price)
        self.assertLess(abs(margin - Decimal("42.00")), Decimal("0.05"))

    def test_rejects_margin_out_of_bounds(self):
        with self.assertRaises(ValueError):
            pricing.price_from_margin(Decimal("5.00"), Decimal("100.00"))
        with self.assertRaises(ValueError):
            pricing.price_from_margin(Decimal("5.00"), Decimal("-1.00"))

    def test_rounds_to_two_places(self):
        price = pricing.price_from_margin(Decimal("3.33"), Decimal("33.33"))
        self.assertEqual(price.as_tuple().exponent, -2)


class ProfitTests(SimpleTestCase):
    def test_profit(self):
        self.assertEqual(pricing.profit(Decimal("5.00"), Decimal("12.50")), Decimal("7.50"))
