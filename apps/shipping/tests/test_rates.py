"""Cálculo do frete: faixas de peso, países e múltiplas transportadoras."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.testing import make_carrier, make_country, make_method, make_rate
from apps.shipping import services
from apps.shipping.models import ShippingRate


class WeightRangeTests(TestCase):
    """O limite inferior é inclusivo; o superior, exclusivo."""

    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        self.light = make_rate(self.method, self.country, 0, 2000, "5.90")
        self.heavy = make_rate(self.method, self.country, 2000, 5000, "7.90")

    def test_zero_falls_in_the_first_range(self):
        self.assertTrue(self.light.contains(0))

    def test_boundary_belongs_to_the_upper_range(self):
        # 2000 g é o começo da segunda faixa, não o fim da primeira: sem essa
        # convenção toda tabela teria que ser cadastrada com 1999.
        self.assertFalse(self.light.contains(2000))
        self.assertTrue(self.heavy.contains(2000))

    def test_price_follows_the_weight(self):
        self.assertEqual(services.quote(self.country, 500)[0].price, Decimal("5.90"))
        self.assertEqual(services.quote(self.country, 3000)[0].price, Decimal("7.90"))

    def test_weight_above_every_range_has_no_option(self):
        self.assertEqual(services.quote(self.country, 9000), [])

    def test_open_ended_range_covers_anything(self):
        make_rate(self.method, self.country, 5000, None, "14.90")
        options = services.quote(self.country, 20000)

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].price, Decimal("14.90"))


class OverlapTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 2000, "5.90")

    def test_overlapping_range_is_refused(self):
        """Duas faixas cobrindo o mesmo peso dariam dois preços para o mesmo pedido."""
        rate = ShippingRate(
            method=self.method, country=self.country,
            min_weight_grams=1000, max_weight_grams=3000, price=Decimal("7.90"),
        )

        with self.assertRaises(ValidationError):
            rate.full_clean()

    def test_adjacent_range_is_accepted(self):
        rate = ShippingRate(
            method=self.method, country=self.country,
            min_weight_grams=2000, max_weight_grams=5000, price=Decimal("7.90"),
        )

        rate.full_clean()  # não levanta

    def test_same_range_for_another_country_is_accepted(self):
        france = make_country("FR", "França", vat_rate="20.00")
        rate = ShippingRate(
            method=self.method, country=france,
            min_weight_grams=0, max_weight_grams=2000, price=Decimal("9.90"),
        )

        rate.full_clean()

    def test_max_below_min_is_refused(self):
        rate = ShippingRate(
            method=self.method, country=self.country,
            min_weight_grams=3000, max_weight_grams=1000, price=Decimal("7.90"),
        )

        with self.assertRaises(ValidationError):
            rate.full_clean()


class CountryTests(TestCase):
    def setUp(self):
        self.belgium = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.belgium, 0, 5000, "5.90")

    def test_country_without_rate_has_no_option(self):
        france = make_country("FR", "França")
        self.assertEqual(services.quote(france, 500), [])

    def test_inactive_country_has_no_option(self):
        """Desligar o país no admin o tira do checkout na mesma hora."""
        self.belgium.is_active = False
        self.belgium.save()

        self.assertEqual(services.quote(self.belgium, 500), [])

    def test_no_country_has_no_option(self):
        self.assertEqual(services.quote(None, 500), [])


class MultipleCarrierTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")

        bpost = make_carrier("Bpost", "bpost")
        self.standard = make_method(bpost, "Standard", "standard", 2, 3, sort_order=1)
        make_rate(self.standard, self.country, 0, 5000, "5.90")

        dpd = make_carrier("DPD", "dpd")
        self.express = make_method(dpd, "Express", "express", 1, 1, sort_order=2)
        make_rate(self.express, self.country, 0, 5000, "12.50")

    def test_both_carriers_are_offered(self):
        options = services.quote(self.country, 800)

        self.assertEqual([option.carrier_name for option in options], ["Bpost", "DPD"])

    def test_cheapest_comes_first(self):
        options = services.quote(self.country, 800)

        self.assertEqual(options[0].price, Decimal("5.90"))
        self.assertEqual(options[1].price, Decimal("12.50"))

    def test_inactive_carrier_disappears(self):
        self.express.carrier.is_active = False
        self.express.carrier.save()

        options = services.quote(self.country, 800)
        self.assertEqual(len(options), 1)

    def test_inactive_method_disappears(self):
        self.express.is_active = False
        self.express.save()

        self.assertEqual(len(services.quote(self.country, 800)), 1)

    def test_quote_for_method_finds_the_right_one(self):
        option = services.quote_for_method(self.country, 800, self.express)

        self.assertIsNotNone(option)
        self.assertEqual(option.price, Decimal("12.50"))

    def test_quote_for_method_refuses_a_method_that_does_not_serve(self):
        """É a revalidação do checkout: o método veio de um <input>."""
        france = make_country("FR", "França")

        self.assertIsNone(services.quote_for_method(france, 800, self.express))


class DeliveryEstimateTests(TestCase):
    """O prazo mostrado é produção + transporte, não só transporte."""

    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")

    def test_without_production_the_estimate_is_the_transit(self):
        option = services.quote(self.country, 500)[0]

        self.assertEqual((option.min_days, option.max_days), (2, 3))
        self.assertEqual(option.days_display, "2–3")

    def test_production_is_added_to_the_transit(self):
        option = services.quote(self.country, 500, production_days_value=3)[0]

        self.assertEqual((option.min_days, option.max_days), (5, 6))
        self.assertEqual(option.days_display, "5–6")

    def test_single_day_is_shown_without_a_range(self):
        method = make_method(code="express", name="Express", min_days=1, max_days=1)
        make_rate(method, self.country, 0, 5000, "12.00")

        option = services.quote_for_method(self.country, 500, method)
        self.assertEqual(option.days_display, "1")


class TrackingUrlTests(TestCase):
    def test_link_is_built_from_the_template(self):
        carrier = make_carrier(
            "Bpost", "bpost", tracking_url_template="https://track.example/{tracking}"
        )

        self.assertEqual(carrier.tracking_url("ABC123"), "https://track.example/ABC123")

    def test_carrier_without_template_has_no_link(self):
        carrier = make_carrier("GLS", "gls")

        self.assertEqual(carrier.tracking_url("ABC123"), "")

    def test_no_tracking_number_has_no_link(self):
        carrier = make_carrier(
            "Bpost", "bpost", tracking_url_template="https://track.example/{tracking}"
        )

        self.assertEqual(carrier.tracking_url(""), "")
