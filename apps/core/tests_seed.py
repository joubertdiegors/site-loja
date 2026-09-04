"""Testes do comando de dados de demonstração."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.catalog.models import Product, ProductStatus
from apps.categories.models import Category
from apps.home.models import HomeBanner, HomeSection


# O test runner do Django roda com DEBUG=False; o comando exige DEBUG=True
# (ou --forcar) de propósito, então o ligamos aqui.
@override_settings(DEBUG=True)
class SeedDemoDataTests(TestCase):
    def seed(self, *args):
        output = StringIO()
        call_command("seed_demo_data", *args, stdout=output)
        return output.getvalue()

    def test_creates_catalogue_and_home(self):
        self.seed()

        self.assertGreater(Product.objects.count(), 0)
        self.assertGreater(Category.objects.count(), 0)
        self.assertGreater(HomeSection.objects.count(), 0)
        # O de boas-vindas (ativo) e os dois outros desenhos (Poster Pop e
        # Bento Criativo), inativos, para o Admin ver como ficam.
        self.assertEqual(HomeBanner.objects.count(), 3)
        self.assertEqual(HomeBanner.objects.filter(is_active=True).count(), 1)
        self.assertEqual(
            set(HomeBanner.objects.values_list("layout", flat=True)),
            {"editorial", "poster_pop", "bento_criativo"},
        )

    def test_products_are_active_and_named(self):
        self.seed()
        product = Product.objects.get(sku="DEMO-GATO-01")

        self.assertEqual(product.status, ProductStatus.ACTIVE)
        self.assertEqual(product.name_in("pt"), "Marcador de Página — Gato Pompom")
        self.assertEqual(product.name_in("fr"), "Marque-page — Chat Pompon")
        self.assertEqual(product.slug, "marcador-de-pagina-gato-pompom")

    def test_no_fake_media_is_created(self):
        self.seed()
        for product in Product.objects.all():
            self.assertEqual(product.media.count(), 0)

    def test_running_twice_changes_nothing(self):
        self.seed()
        counts = (Product.objects.count(), HomeSection.objects.count(), Category.objects.count())

        self.seed()

        self.assertEqual(
            counts,
            (Product.objects.count(), HomeSection.objects.count(), Category.objects.count()),
        )

    def test_does_not_touch_existing_products(self):
        from apps.core.testing import make_product

        mine = make_product(sku="MEU-01", name="Produto do dono")
        self.seed()

        mine.refresh_from_db()
        self.assertEqual(mine.name_in("pt"), "Produto do dono")

    def test_home_renders_after_seeding(self):
        self.seed()
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Destaques de Modelos")
        self.assertContains(response, "Filamentos em Destaque")

    def test_remove_only_deletes_demo_data(self):
        from apps.core.testing import make_product

        mine = make_product(sku="MEU-01", name="Produto do dono")
        self.seed()
        self.seed("--remover")

        self.assertFalse(Product.objects.filter(sku__startswith="DEMO-").exists())
        self.assertTrue(Product.objects.filter(pk=mine.pk).exists())
        self.assertEqual(HomeSection.objects.count(), 0)
        # Categorias, materiais e cores são preservados de propósito.
        self.assertGreater(Category.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_refuses_to_run_in_production_without_the_flag(self):
        with self.assertRaises(CommandError):
            self.seed()

    @override_settings(DEBUG=False)
    def test_runs_in_production_with_the_flag(self):
        self.seed("--forcar")
        self.assertGreater(Product.objects.count(), 0)


# ---------------------------------------------------------------------------
# Etapa 20: o seed passou a cobrir a base inteira da loja
#
# As classes acima continuam valendo — elas guardam o catálogo e a Home. O que
# entra aqui é o resto (idiomas, país, entrega, cards, chamada, faixa do topo,
# rodapé) e a garantia de idempotência item a item.
# ---------------------------------------------------------------------------

from decimal import Decimal

from apps.catalog.models import Brand, Color, Material, ProductVariant
from apps.core.models import DeliveryCountry, SiteLanguage
from apps.home.models import HomeCallout, HomeCard
from apps.shipping.models import ShippingCarrier, ShippingMethod, ShippingRate
from apps.storefront.models import (
    FooterColumn,
    FooterLink,
    FooterSettings,
    InstitutionalPage,
    TopBarItem,
)


#: O comando recusa rodar com DEBUG=False sem `--forcar`; nos testes DEBUG é
#: False, então o parâmetro é o que o servidor de verdade também usaria.
def seed():
    call_command("seed_demo_data", "--forcar", stdout=StringIO())


#: Tudo que o seed toca. Uma contagem por modelo, para comparar antes e depois.
def contagens():
    return {
        "idiomas": SiteLanguage.objects.count(),
        "paises": DeliveryCountry.objects.count(),
        "categorias": Category.objects.count(),
        "materiais": Material.objects.count(),
        "cores": Color.objects.count(),
        "marcas": Brand.objects.count(),
        "produtos": Product.objects.count(),
        "variantes": ProductVariant.objects.count(),
        "transportadoras": ShippingCarrier.objects.count(),
        "metodos": ShippingMethod.objects.count(),
        "tarifas": ShippingRate.objects.count(),
        "banners": HomeBanner.objects.count(),
        "secoes": HomeSection.objects.count(),
        "cards": HomeCard.objects.count(),
        "chamadas": HomeCallout.objects.count(),
        "faixa_topo": TopBarItem.objects.count(),
        "colunas_rodape": FooterColumn.objects.count(),
        "links_rodape": FooterLink.objects.count(),
        "rodape": FooterSettings.objects.count(),
        "paginas": InstitutionalPage.objects.count(),
    }


class SeedContentTests(TestCase):
    """O que uma instalação nova ganha."""

    @classmethod
    def setUpTestData(cls):
        seed()

    def test_the_four_store_languages_are_there(self):
        codes = set(SiteLanguage.objects.values_list("code", flat=True))

        self.assertTrue({"pt-br", "fr", "nl", "en"} <= codes)

    def test_belgium_can_receive_orders(self):
        belgica = DeliveryCountry.objects.get(iso_code="BE")

        self.assertTrue(belgica.is_active)
        self.assertEqual(belgica.vat_rate, Decimal("21.00"))

    def test_the_four_materials_are_there(self):
        nomes = set(Material.objects.values_list("name", flat=True))

        self.assertTrue({"PLA", "PETG", "TPU", "ABS"} <= nomes)

    def test_there_are_categories_colours_and_brands(self):
        self.assertGreaterEqual(Category.objects.count(), 3)
        self.assertGreaterEqual(Color.objects.count(), 3)
        self.assertGreaterEqual(Brand.objects.count(), 4)

    def test_there_are_four_demo_products_each_with_a_variant(self):
        produtos = Product.objects.filter(sku__startswith="DEMO-")

        self.assertGreaterEqual(produtos.count(), 4)
        for produto in produtos:
            with self.subTest(sku=produto.sku):
                self.assertTrue(produto.variants.filter(is_active=True).exists())

    def test_the_carriers_are_bpost_and_dpd(self):
        nomes = set(ShippingCarrier.objects.values_list("name", flat=True))

        self.assertTrue({"BPost", "DPD"} <= nomes)

    def test_belgium_has_shipping_rates(self):
        tarifas = ShippingRate.objects.filter(country__iso_code="BE")

        self.assertGreaterEqual(ShippingMethod.objects.count(), 3)
        self.assertGreaterEqual(tarifas.count(), 6)
        for tarifa in tarifas:
            with self.subTest(tarifa=tarifa.pk):
                self.assertGreater(tarifa.price, Decimal("0"))

    def test_the_home_has_a_banner_cards_and_a_callout(self):
        self.assertGreaterEqual(HomeBanner.objects.count(), 1)
        self.assertEqual(HomeCard.objects.count(), 3)
        self.assertEqual(HomeCallout.objects.count(), 1)

    def test_the_cards_and_the_callout_speak_the_four_languages(self):
        for card in HomeCard.objects.all():
            with self.subTest(card=card.internal_name):
                idiomas = set(card.translations.values_list("language", flat=True))
                self.assertEqual(idiomas, {"pt", "fr", "nl", "en"})

        chamada = HomeCallout.objects.get()
        self.assertEqual(
            set(chamada.translations.values_list("language", flat=True)),
            {"pt", "fr", "nl", "en"},
        )

    def test_the_top_bar_is_filled_in_the_four_languages(self):
        self.assertEqual(TopBarItem.objects.count(), 3)
        for item in TopBarItem.objects.all():
            with self.subTest(item=item.internal_name):
                idiomas = set(item.translations.values_list("language", flat=True))
                self.assertEqual(idiomas, {"pt", "fr", "nl", "en"})

    def test_the_footer_has_texts_contact_and_the_pages_column(self):
        rodape = FooterSettings.objects.get()

        self.assertTrue(rodape.contact_email)
        self.assertEqual(
            set(rodape.translations.values_list("language", flat=True)),
            {"pt", "fr", "nl", "en"},
        )
        coluna = FooterColumn.objects.get(internal_name="paginas-institucionais")
        self.assertEqual(coluna.links.count(), 4)

    def test_the_footer_links_point_to_pages_and_not_to_typed_addresses(self):
        """Endereço digitado quebraria o idioma; a referência não."""
        for link in FooterLink.objects.filter(column__internal_name="paginas-institucionais"):
            with self.subTest(link=link.pk):
                self.assertIsNotNone(link.page_id)
                self.assertEqual(link.url, "")

    def test_the_four_institutional_pages_have_content(self):
        """Vêm da migration de conteúdo; o seed não as duplica."""
        self.assertEqual(InstitutionalPage.objects.count(), 4)
        for pagina in InstitutionalPage.objects.all():
            with self.subTest(pagina=pagina.slug):
                self.assertEqual(pagina.translations.count(), 4)

    def test_the_home_answers_after_the_seed(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_the_shop_answers_after_the_seed(self):
        self.assertEqual(self.client.get("/modelos/").status_code, 200)


class SeedIdempotencyTests(TestCase):
    """Rodar de novo não pode duplicar nem apagar nada."""

    def test_running_it_twice_changes_no_count(self):
        seed()
        antes = contagens()

        seed()

        self.assertEqual(contagens(), antes)

    def test_running_it_three_times_changes_no_count(self):
        seed()
        seed()
        antes = contagens()

        seed()

        self.assertEqual(contagens(), antes)

    def test_no_translation_is_duplicated(self):
        seed()
        seed()

        for card in HomeCard.objects.all():
            with self.subTest(card=card.internal_name):
                idiomas = list(card.translations.values_list("language", flat=True))
                self.assertEqual(len(idiomas), len(set(idiomas)))

        for item in TopBarItem.objects.all():
            with self.subTest(item=item.internal_name):
                idiomas = list(item.translations.values_list("language", flat=True))
                self.assertEqual(len(idiomas), len(set(idiomas)))

    def test_it_never_overwrites_what_the_administrator_wrote(self):
        seed()
        card = HomeCard.objects.first()
        traducao = card.translations.get(language="pt")
        traducao.title = "Título escrito pelo administrador"
        traducao.save()

        tarifa = ShippingRate.objects.first()
        tarifa.price = Decimal("99.00")
        tarifa.save()

        seed()

        traducao.refresh_from_db()
        tarifa.refresh_from_db()
        self.assertEqual(traducao.title, "Título escrito pelo administrador")
        self.assertEqual(tarifa.price, Decimal("99.00"))

    def test_a_deactivated_language_stays_deactivated(self):
        """Reativar o que o administrador desligou seria decidir por ele."""
        seed()
        SiteLanguage.objects.filter(code="nl").update(is_active=False)

        seed()

        self.assertFalse(SiteLanguage.objects.get(code="nl").is_active)

    @override_settings(DEBUG=False)
    def test_it_refuses_to_run_in_production_without_being_told_to(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_demo_data", stdout=StringIO())
