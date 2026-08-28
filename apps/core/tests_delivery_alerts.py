"""AUD-05 — país ativo que não fecha checkout precisa aparecer no Admin.

Ativar um país é um clique; cadastrar a tabela de preços dele é outra tarefa,
noutra tela. Entre as duas cabe um país que aparece no cadastro de endereço,
aceita o cliente até o checkout e lá diz que não há entrega — enquanto a loja
não fica sabendo de nada.

O alerta não bloqueia: cadastrar o país antes da tarifa é uma ordem de trabalho
legítima. O que não pode é isso passar despercebido.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.admin import DeliveryCountryAdmin
from apps.core.models import DeliveryCountry
from apps.core.testing import make_country, make_method, make_rate
from apps.shipping.models import ShippingRate


class ShippingAlertBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jd.test", password="senha-de-teste"
        )
        self.client.force_login(self.staff)
        self.admin = DeliveryCountryAdmin(DeliveryCountry, None)

    def problema(self, country):
        recarregado = (
            DeliveryCountry.objects.prefetch_related("shipping_rates__method__carrier")
            .get(pk=country.pk)
        )
        return self.admin.shipping_problem(recarregado)

    def changelist(self):
        return self.client.get(reverse("admin:core_deliverycountry_changelist"))


class CountryWithoutRateTests(ShippingAlertBase):
    def test_an_active_country_without_any_rate_is_a_problem(self):
        alemanha = make_country("DE", "Alemanha")

        self.assertIn("nenhuma tarifa", self.problema(alemanha))

    def test_a_country_with_a_rate_is_fine(self):
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, None, "5.90")

        self.assertEqual(self.problema(belgica), "")

    def test_a_rate_of_an_inactive_method_does_not_count(self):
        """Tarifa existe, mas o método está desligado: o cliente não vê nada."""
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, None, "5.90")
        metodo.is_active = False
        metodo.save()

        self.assertIn("nenhuma tarifa", self.problema(belgica))

    def test_an_inactive_rate_does_not_count(self):
        belgica = make_country("BE")
        metodo = make_method()
        rate = make_rate(metodo, belgica, 0, None, "5.90")
        rate.is_active = False
        rate.save()

        self.assertIn("nenhuma tarifa", self.problema(belgica))

    def test_a_rate_of_an_inactive_carrier_does_not_count(self):
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, None, "5.90")
        metodo.carrier.is_active = False
        metodo.carrier.save()

        self.assertIn("nenhuma tarifa", self.problema(belgica))


class WeightCeilingTests(ShippingAlertBase):
    """§15 — pedido acima da maior faixa também fica sem entrega."""

    def test_a_table_with_a_ceiling_is_flagged(self):
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, 2000, "5.90")

        aviso = self.problema(belgica)
        self.assertIn("2000 g", aviso)
        self.assertIn("mais pesado", aviso)

    def test_a_last_band_without_a_ceiling_solves_it(self):
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, 2000, "5.90")
        ShippingRate.objects.create(
            method=metodo, country=belgica, min_weight_grams=2000,
            max_weight_grams=None, price=Decimal("12.90"),
        )

        self.assertEqual(self.problema(belgica), "")

    def test_the_highest_ceiling_is_the_one_reported(self):
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, 2000, "5.90")
        ShippingRate.objects.create(
            method=metodo, country=belgica, min_weight_grams=2000,
            max_weight_grams=5000, price=Decimal("9.90"),
        )

        self.assertIn("5000 g", self.problema(belgica))


class AdminSurfaceTests(ShippingAlertBase):
    """O aviso é por país, não global.

    A migration `core.0004_seed_delivery_countries` já semeia vários países
    ativos, e numa instalação nova nenhum deles tem tarifa — o aviso aparece
    para eles com razão. Por isso os testes olham o nome do país que estão
    examinando, e não a simples presença do texto.
    """

    def test_the_changelist_warns_about_the_country(self):
        make_country("DE", "Alemanha")

        resposta = self.changelist()

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Alemanha")
        self.assertContains(resposta, "sem entrega possível")

    def test_the_warning_says_what_to_do(self):
        make_country("DE", "Alemanha")

        self.assertContains(self.changelist(), "Frete › Tarifas")

    def test_a_healthy_country_produces_no_warning(self):
        belgica = make_country("BE", "Bélgica")
        metodo = make_method()
        make_rate(metodo, belgica, 0, None, "5.90")

        corpo = self.changelist().content.decode()
        aviso = self.warning_line(corpo)
        self.assertNotIn("Bélgica (BE)", aviso)

    def test_an_inactive_country_is_not_warned_about(self):
        """País desligado não fecha checkout — e não deveria mesmo."""
        alemanha = make_country("DE", "Alemanha")
        alemanha.is_active = False
        alemanha.save()

        self.assertNotIn("Alemanha (DE)", self.warning_line(self.changelist().content.decode()))

    @staticmethod
    def warning_line(corpo: str) -> str:
        """Só o trecho do aviso, para não confundir com o resto da página."""
        marca = "sem entrega possível"
        if marca not in corpo:
            return ""
        inicio = corpo.index(marca)
        return corpo[inicio : inicio + 1200]

    def test_the_column_shows_the_state_of_each_country(self):
        make_country("DE", "Alemanha")
        belgica = make_country("BE")
        metodo = make_method()
        make_rate(metodo, belgica, 0, None, "5.90")

        resposta = self.changelist()
        self.assertContains(resposta, "sem tarifa")
        self.assertContains(resposta, "ok")

    def test_the_column_marks_an_inactive_country(self):
        alemanha = make_country("DE", "Alemanha")
        alemanha.is_active = False
        alemanha.save()

        self.assertContains(self.changelist(), "país inativo")

    def test_the_rate_count_ignores_what_does_not_work(self):
        belgica = make_country("BE")
        metodo = make_method()
        rate = make_rate(metodo, belgica, 0, None, "5.90")
        rate.is_active = False
        rate.save()

        recarregado = DeliveryCountry.objects.prefetch_related(
            "shipping_rates__method__carrier"
        ).get(pk=belgica.pk)
        self.assertEqual(self.admin.rate_count(recarregado), 0)

    def test_the_warning_does_not_block_the_admin(self):
        """Cadastrar o país antes da tarifa continua sendo permitido."""
        make_country("DE", "Alemanha")

        self.assertEqual(self.changelist().status_code, 200)
