"""TVA: a alíquota vem do país e o imposto está **dentro** do preço.

Se algum dia alguém trocar a conta por "somar 21% no fim", estes testes caem —
que é exatamente o objetivo. O preço anunciado numa loja B2C da UE tem que ser
o preço final.
"""

from decimal import Decimal

from django.test import TestCase

from apps.core.testing import make_country
from apps.orders import taxes


class IncludedTaxTests(TestCase):
    def test_tax_is_extracted_not_added(self):
        # €121,00 com 21% contém €21,00 de imposto — não €25,41.
        self.assertEqual(taxes.included_tax(Decimal("121.00"), Decimal("21")), Decimal("21.00"))

    def test_net_plus_tax_returns_the_gross(self):
        gross = Decimal("121.00")
        tax = taxes.included_tax(gross, Decimal("21"))

        self.assertEqual(gross - tax, Decimal("100.00"))

    def test_zero_rate_means_zero_tax(self):
        self.assertEqual(taxes.included_tax(Decimal("100.00"), Decimal("0")), Decimal("0.00"))

    def test_zero_amount_means_zero_tax(self):
        self.assertEqual(taxes.included_tax(Decimal("0.00"), Decimal("21")), Decimal("0.00"))

    def test_rounding_is_half_up_on_cents(self):
        # 21% de 10,00 embutidos = 1,7355... -> 1,74
        self.assertEqual(taxes.included_tax(Decimal("10.00"), Decimal("21")), Decimal("1.74"))

    def test_no_float_takes_part(self):
        result = taxes.included_tax(Decimal("19.90"), Decimal("21"))

        self.assertIsInstance(result, Decimal)


class BreakdownTests(TestCase):
    def setUp(self):
        self.belgium = make_country("BE", vat_rate="21.00")
        self.france = make_country("FR", "França", vat_rate="20.00")

    def test_rate_comes_from_the_destination_country(self):
        """Venda a distância B2C na UE: vale a alíquota de quem recebe."""
        belgian = taxes.breakdown(country=self.belgium, subtotal=Decimal("100.00"))
        french = taxes.breakdown(country=self.france, subtotal=Decimal("100.00"))

        self.assertEqual(belgian.rate, Decimal("21.00"))
        self.assertEqual(french.rate, Decimal("20.00"))

    def test_shipping_is_taxed_with_the_goods(self):
        result = taxes.breakdown(
            country=self.belgium, subtotal=Decimal("100.00"), shipping=Decimal("5.90")
        )

        self.assertEqual(result.taxable, Decimal("105.90"))
        self.assertEqual(result.amount, taxes.included_tax(Decimal("105.90"), Decimal("21")))

    def test_discount_lowers_the_taxable_amount(self):
        result = taxes.breakdown(
            country=self.belgium, subtotal=Decimal("100.00"), discount=Decimal("10.00")
        )

        self.assertEqual(result.taxable, Decimal("90.00"))

    def test_total_never_goes_negative(self):
        result = taxes.breakdown(
            country=self.belgium, subtotal=Decimal("10.00"), discount=Decimal("50.00")
        )

        self.assertEqual(result.taxable, Decimal("0.00"))

    def test_country_code_is_kept(self):
        result = taxes.breakdown(country=self.france, subtotal=Decimal("100.00"))

        self.assertEqual(result.country_code, "FR")

    def test_without_country_there_is_no_tax_yet(self):
        """Antes de o cliente escolher o endereço não há alíquota que valha."""
        result = taxes.breakdown(country=None, subtotal=Decimal("100.00"))

        self.assertEqual(result.rate, Decimal("0.00"))
        self.assertEqual(result.amount, Decimal("0.00"))
        self.assertEqual(result.taxable, Decimal("100.00"))

    def test_net_is_the_accounting_base(self):
        result = taxes.breakdown(country=self.belgium, subtotal=Decimal("121.00"))

        self.assertEqual(result.net, Decimal("100.00"))

    def test_rate_is_never_hardcoded(self):
        """Mudar a alíquota no admin muda a conta — sem tocar em código."""
        self.belgium.vat_rate = Decimal("6.00")
        self.belgium.save()

        result = taxes.breakdown(country=self.belgium, subtotal=Decimal("106.00"))

        self.assertEqual(result.rate, Decimal("6.00"))
        self.assertEqual(result.amount, Decimal("6.00"))
