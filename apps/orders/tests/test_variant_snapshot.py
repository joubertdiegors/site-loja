"""O pedido guarda a variante comprada — inclusive a cor no idioma do cliente.

O snapshot existe para que o pedido de hoje continue legível daqui a dois anos,
com o catálogo tendo mudado de preço, de cor e de nome. Estes testes fecham o
caminho inteiro:

    ProductVariant -> CartLine -> checkout -> OrderItem

e provam que cada elo carrega os dados da variante escolhida, não os de outra e
não os do produto.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import translation as django_translation

from apps.cart.cart import CartLine
from apps.catalog.models import Color, ColorTranslation, Material, ProductVariant
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services


def traduzir(color, **nomes):
    for idioma, nome in nomes.items():
        ColorTranslation.objects.create(master=color, language=idioma, name=nome)
    color.refresh_translations()
    return color


class VariantSnapshotBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 500, "4.90")
        make_rate(self.method, self.country, 501, 5000, "7.90")
        make_bank_account()

        self.user = make_user(username="diego3d", email="diego@example.com")
        self.address = make_address(self.user.customer, self.country)

        self.preto = traduzir(
            Color.objects.create(name="Preto", hex_code="#000000"),
            pt="Preto", fr="Noir", nl="Zwart", en="Black",
        )
        self.branco = traduzir(
            Color.objects.create(name="Branco", hex_code="#FFFFFF"),
            pt="Branco", fr="Blanc", nl="Wit", en="White",
        )
        self.pla = Material.objects.create(name="PLA")

        self.product = make_product(
            sku="DINO", name="Dinossauro", with_variant=False
        )
        self.leve = ProductVariant.objects.create(
            product=self.product, sku="DINO-PRETO-25", color=self.preto,
            material=self.pla, size="25 cm", sale_price=Decimal("27.90"),
            weight_grams=Decimal("150"), production_lead_time_days=2, stock_quantity=10,
        )
        self.pesada = ProductVariant.objects.create(
            product=self.product, sku="DINO-BRANCO-30", color=self.branco,
            material=self.pla, size="30 cm", sale_price=Decimal("32.90"),
            weight_grams=Decimal("400"), production_lead_time_days=5, stock_quantity=5,
        )
        self.product.refresh_from_db()

    def linha(self, variante, quantidade=1):
        return CartLine(
            key=f"{self.product.pk}:{variante.pk}:-",
            product=self.product,
            variant=variante,
            quantity=quantidade,
        )

    def pedido(self, linhas, language=""):
        return services.create_order(
            customer=self.user.customer,
            lines=linhas,
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
            language=language,
        )


class OrderItemKeepsTheChosenVariantTests(VariantSnapshotBase):
    def test_the_item_points_to_the_chosen_variant(self):
        item = self.pedido([self.linha(self.pesada)]).items.get()

        self.assertEqual(item.variant, self.pesada)
        self.assertEqual(item.sku, "DINO-BRANCO-30")

    def test_price_weight_and_lead_time_are_the_chosen_variants(self):
        item = self.pedido([self.linha(self.pesada)]).items.get()

        self.assertEqual(item.unit_price, Decimal("32.90"))
        self.assertEqual(item.unit_weight_grams, 400)
        self.assertEqual(item.production_days, 5)

    def test_the_sibling_variant_data_never_leaks_in(self):
        """A irmã custa 27,90 e pesa 150 g. Nada disso pode aparecer aqui."""
        item = self.pedido([self.linha(self.pesada)]).items.get()

        self.assertNotEqual(item.unit_price, Decimal("27.90"))
        self.assertNotEqual(item.unit_weight_grams, 150)
        self.assertNotEqual(item.production_days, 2)

    def test_the_axes_are_copied(self):
        item = self.pedido([self.linha(self.pesada)]).items.get()

        self.assertEqual(item.color_name, "Branco")
        self.assertEqual(item.size_name, "30 cm")
        self.assertEqual(item.material_name, "PLA")

    def test_the_colour_is_copied_in_the_customers_language(self):
        """O pedido guarda o que o cliente leu, não o nome interno do Admin."""
        with django_translation.override("fr"):
            item = self.pedido([self.linha(self.pesada)], language="fr").items.get()

        self.assertEqual(item.color_name, "Blanc")

    def test_the_snapshot_survives_a_colour_rename(self):
        item = self.pedido([self.linha(self.pesada)]).items.get()

        traducao = self.branco.translations.get(language="pt")
        traducao.name = "Branco Gelo"
        traducao.save()
        self.branco.name = "BRANCO-INTERNO"
        self.branco.save()

        item.refresh_from_db()
        self.assertEqual(item.color_name, "Branco")

    def test_the_snapshot_survives_the_variant_changing(self):
        pedido = self.pedido([self.linha(self.pesada)])

        self.pesada.sale_price = Decimal("99.00")
        self.pesada.weight_grams = Decimal("1000")
        self.pesada.size = "40 cm"
        self.pesada.production_lead_time_days = 30
        self.pesada.save()

        item = pedido.items.get()
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal("32.90"))
        self.assertEqual(item.unit_weight_grams, 400)
        self.assertEqual(item.size_name, "30 cm")
        self.assertEqual(item.production_days, 5)


class ShippingFollowsTheChosenVariantTests(VariantSnapshotBase):
    """Trocar de variante muda o peso — e o peso muda a faixa de frete."""

    def test_the_light_variant_falls_in_the_cheap_band(self):
        pedido = self.pedido([self.linha(self.leve, 2)])

        self.assertEqual(pedido.total_weight_grams, 300)
        self.assertEqual(pedido.shipping_total, Decimal("4.90"))

    def test_the_heavy_variant_falls_in_the_expensive_band(self):
        """Mesmo produto, mesma quantidade — outra variante, outro frete."""
        pedido = self.pedido([self.linha(self.pesada, 2)])

        self.assertEqual(pedido.total_weight_grams, 800)
        self.assertEqual(pedido.shipping_total, Decimal("7.90"))

    def test_mixing_variants_sums_their_own_weights(self):
        pedido = self.pedido([self.linha(self.leve, 2), self.linha(self.pesada, 1)])

        self.assertEqual(pedido.total_weight_grams, 700)

    def test_production_is_the_slowest_variant(self):
        pedido = self.pedido([self.linha(self.leve, 2), self.linha(self.pesada, 1)])

        self.assertEqual(pedido.production_days, 5)
        self.assertEqual(pedido.shipping_min_days, 7)
        self.assertEqual(pedido.shipping_max_days, 8)

    def test_stock_falls_on_the_chosen_variant_only(self):
        pedido = self.pedido([self.linha(self.pesada, 2)])
        services.apply_stock(pedido)

        self.leve.refresh_from_db()
        self.pesada.refresh_from_db()
        self.assertEqual(self.pesada.stock_quantity, 3)
        self.assertEqual(self.leve.stock_quantity, 10)
