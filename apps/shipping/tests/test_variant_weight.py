"""O peso do frete e o prazo saem SEMPRE da variante.

Este é o ponto crítico da etapa 8. Antes, o peso era do produto: duas peças do
mesmo modelo em tamanhos diferentes cobravam o mesmo frete, e a diferença saía
do bolso da loja ou do cliente. Agora cada variante carrega o seu peso e o seu
prazo, e o cálculo os lê de lá.

O cenário dos números é o do enunciado da etapa: 2 × 150 g + 1 × 400 g = 700 g.
"""

from decimal import Decimal

from django.test import TestCase

from apps.cart.cart import CartLine
from apps.catalog.models import ProductVariant
from apps.core.testing import make_country, make_method, make_product, make_rate
from apps.shipping import services


class VariantWeightTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="PECA", name="Peça", with_variant=False)
        self.leve = ProductVariant.objects.create(
            product=self.product,
            sku="PECA-10",
            size="10 cm",
            sale_price=Decimal("19.90"),
            weight_grams=Decimal("150"),
            production_lead_time_days=2,
            stock_quantity=10,
        )
        self.pesada = ProductVariant.objects.create(
            product=self.product,
            sku="PECA-25",
            size="25 cm",
            sale_price=Decimal("27.90"),
            weight_grams=Decimal("400"),
            production_lead_time_days=4,
            stock_quantity=10,
        )
        self.linhas = [
            CartLine(key="a", product=self.product, variant=self.leve, quantity=2),
            CartLine(key="b", product=self.product, variant=self.pesada, quantity=1),
        ]

    def test_each_variant_has_its_own_weight(self):
        self.assertEqual(services.line_weight_grams(self.product, self.leve), 150)
        self.assertEqual(services.line_weight_grams(self.product, self.pesada), 400)

    def test_cart_weight_is_the_scenario_of_the_brief(self):
        """2 × 150 g + 1 × 400 g = 700 g."""
        self.assertEqual(services.cart_weight_grams(self.linhas), 700)

    def test_a_line_without_variant_weighs_nothing(self):
        """Não existe linha comercial sem variante — e se existisse, não pesaria."""
        sem = CartLine(key="c", product=self.product, variant=None, quantity=3)
        self.assertEqual(services.cart_weight_grams([sem]), 0)

    def test_a_variant_without_a_registered_weight_weighs_nothing(self):
        """Melhor um frete zerado que o administrador vê do que um inventado."""
        sem_peso = ProductVariant.objects.create(
            product=self.product,
            sku="PECA-X",
            size="sem peso",
            sale_price=Decimal("9.90"),
            stock_quantity=1,
        )
        linha = CartLine(key="d", product=self.product, variant=sem_peso, quantity=5)
        self.assertEqual(services.cart_weight_grams([linha]), 0)

    def test_the_product_is_not_consulted_for_weight(self):
        """A prova por ausência: o produto não tem peso a oferecer."""
        self.assertFalse(hasattr(self.product, "weight_grams"))


class VariantProductionDaysTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="PECA", name="Peça", with_variant=False)
        self.rapida = ProductVariant.objects.create(
            product=self.product,
            sku="PECA-R",
            size="10 cm",
            sale_price=Decimal("19.90"),
            weight_grams=Decimal("150"),
            production_lead_time_days=2,
            stock_quantity=10,
        )
        self.lenta = ProductVariant.objects.create(
            product=self.product,
            sku="PECA-L",
            size="25 cm",
            sale_price=Decimal("27.90"),
            weight_grams=Decimal("400"),
            production_lead_time_days=4,
            stock_quantity=10,
        )
        self.linhas = [
            CartLine(key="a", product=self.product, variant=self.rapida, quantity=2),
            CartLine(key="b", product=self.product, variant=self.lenta, quantity=1),
        ]

    def test_production_is_the_longest_lead_time_not_the_sum(self):
        """A oficina imprime em paralelo: somar daria um prazo que não acontece."""
        self.assertEqual(services.production_days(self.linhas), 4)

    def test_one_line_uses_its_own_lead_time(self):
        self.assertEqual(services.production_days([self.linhas[0]]), 2)

    def test_a_line_without_variant_has_no_lead_time(self):
        sem = CartLine(key="c", product=self.product, variant=None, quantity=1)
        self.assertEqual(services.production_days([sem]), 0)

    def test_total_estimate_is_production_plus_transit(self):
        pais = make_country("BE", vat_rate="21.00")
        metodo = make_method(min_days=2, max_days=3)
        make_rate(metodo, pais, 0, 5000, "5.90")

        opcao = services.quote_for_method(
            pais,
            services.cart_weight_grams(self.linhas),
            metodo,
            production_days_value=services.production_days(self.linhas),
        )

        self.assertEqual((opcao.min_days, opcao.max_days), (6, 7))
        self.assertEqual((opcao.transit_min_days, opcao.transit_max_days), (2, 3))

    def test_the_heavier_variant_can_change_the_shipping_band(self):
        """O peso da variante escolhida muda a faixa — e portanto o preço."""
        pais = make_country("BE", vat_rate="21.00")
        metodo = make_method(min_days=2, max_days=3)
        make_rate(metodo, pais, 0, 500, "4.90")
        make_rate(metodo, pais, 501, 5000, "7.90")

        so_leve = [CartLine(key="a", product=self.product, variant=self.rapida, quantity=2)]
        com_pesada = self.linhas

        barato = services.quote_for_method(
            pais, services.cart_weight_grams(so_leve), metodo
        )
        caro = services.quote_for_method(
            pais, services.cart_weight_grams(com_pesada), metodo
        )

        self.assertEqual(services.cart_weight_grams(so_leve), 300)
        self.assertEqual(barato.price, Decimal("4.90"))
        self.assertEqual(caro.price, Decimal("7.90"))
