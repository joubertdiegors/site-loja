"""A cor é uma só; o nome muda com o idioma.

A variante não é duplicada por idioma — o que tem tradução é a `Color`, pela
mesma mecânica de `Product` e `Category`: uma linha por idioma numa tabela
filha, com o fallback da casa (idioma pedido → português → qualquer uma).
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Color, ColorTranslation, ProductStatus, ProductVariant
from apps.core.testing import LanguageResetMixin, make_category, make_product


def traduzir(color, **nomes):
    for idioma, nome in nomes.items():
        ColorTranslation.objects.create(master=color, language=idioma, name=nome)
    color.refresh_translations()
    return color


class ColorTranslationTests(TestCase):
    def setUp(self):
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        traduzir(self.preto, pt="Preto", fr="Noir", nl="Zwart", en="Black")

    def test_the_four_languages(self):
        esperado = {"pt": "Preto", "fr": "Noir", "nl": "Zwart", "en": "Black"}
        for idioma, nome in esperado.items():
            with self.subTest(idioma=idioma):
                self.assertEqual(self.preto.tr("name", language=idioma), nome)

    def test_one_colour_serves_every_language(self):
        """Nada de uma cor por idioma: é o mesmo registro."""
        self.assertEqual(Color.objects.count(), 1)
        self.assertEqual(self.preto.translations.count(), 4)

    def test_the_internal_name_is_not_the_customer_name(self):
        """`name` é do Admin; `display_name` é da loja."""
        self.assertEqual(self.preto.name, "Preto")
        self.assertEqual(self.preto.tr("name", language="fr"), "Noir")

    def test_missing_language_falls_back_to_portuguese(self):
        turquesa = Color.objects.create(name="Turquesa", hex_code="#00CED1")
        traduzir(turquesa, pt="Turquesa")

        self.assertEqual(turquesa.tr("name", language="fr"), "Turquesa")

    def test_colour_without_any_translation_falls_back_to_the_internal_name(self):
        """Melhor o nome interno do que um seletor com espaço em branco."""
        crua = Color.objects.create(name="Sem tradução", hex_code="#123456")

        self.assertEqual(crua.display_name, "Sem tradução")

    def test_a_language_cannot_be_registered_twice(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ColorTranslation.objects.create(master=self.preto, language="fr", name="Noir foncé")

    def test_an_empty_name_is_refused_by_the_database(self):
        outra = Color.objects.create(name="Outra")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ColorTranslation.objects.create(master=outra, language="pt", name="")

    def test_translations_die_with_the_colour(self):
        self.preto.delete()
        self.assertEqual(ColorTranslation.objects.count(), 0)


class ColorInTheStoreTests(LanguageResetMixin, TestCase):
    """O cliente vê a cor no idioma da loja, em toda tela que a mostra."""

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.preto = traduzir(
            Color.objects.create(name="Preto", hex_code="#000000"),
            pt="Preto", fr="Noir", nl="Zwart", en="Black",
        )
        self.branco = traduzir(
            Color.objects.create(name="Branco", hex_code="#FFFFFF"),
            pt="Branco", fr="Blanc", nl="Wit", en="White",
        )
        self.product = make_product(
            sku="DINO",
            name="Dinossauro",
            category=self.category,
            status=ProductStatus.ACTIVE,
            with_variant=False,
            # Etapa 2B: a cor é opção comercial (uma variante por cor); é o que
            # faz o card continuar mostrando as cores das variantes.
            color_mode="variant",
        )
        for sku, cor, tamanho in (
            ("DINO-P", self.preto, "25 cm"),
            ("DINO-B", self.branco, "30 cm"),
        ):
            ProductVariant.objects.create(
                product=self.product, sku=sku, color=cor, size=tamanho,
                sale_price=Decimal("27.90"), stock_quantity=5,
            )
        self.product.refresh_from_db()

    def test_the_variant_label_uses_the_translated_colour(self):
        variante = ProductVariant.objects.get(sku="DINO-P")

        self.assertEqual(variante.label, "Preto · 25 cm")
        with self.settings(LANGUAGE_CODE="fr"):
            from django.utils import translation as django_translation

            with django_translation.override("fr"):
                variante = ProductVariant.objects.select_related("color").get(sku="DINO-P")
                self.assertEqual(variante.label, "Noir · 25 cm")

    def test_the_product_page_shows_the_colour_in_french(self):
        resposta = self.client.get("/fr" + self.product.get_absolute_url())

        self.assertContains(resposta, "Noir")
        self.assertContains(resposta, "Blanc")
        self.assertNotContains(resposta, ">Preto<")

    def test_the_product_page_shows_the_colour_in_dutch(self):
        resposta = self.client.get("/nl" + self.product.get_absolute_url())

        self.assertContains(resposta, "Zwart")
        self.assertContains(resposta, "Wit")

    def test_the_product_page_shows_the_colour_in_english(self):
        resposta = self.client.get("/en" + self.product.get_absolute_url())

        self.assertContains(resposta, "Black")
        self.assertContains(resposta, "White")

    def test_portuguese_is_the_default_url(self):
        resposta = self.client.get(self.product.get_absolute_url())

        self.assertContains(resposta, "Preto")
        self.assertContains(resposta, "Branco")

    def test_the_specification_row_is_translated(self):
        resposta = self.client.get("/fr" + self.product.get_absolute_url())
        ficha = {chave: valor for chave, _rotulo, valor in resposta.context["specifications"]}

        self.assertEqual(ficha["cor"], "Noir")

    def test_the_card_swatch_is_translated(self):
        resposta = self.client.get("/fr/modelos/")

        self.assertContains(resposta, "Noir")

    def test_reading_the_colours_does_not_cost_a_query_per_variant(self):
        """A tradução da cor vem no prefetch; sem isso, cada variante custaria uma."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.product.get_absolute_url())
        base = len(consultas)

        for indice in range(6):
            cor = traduzir(
                Color.objects.create(name=f"Cor {indice}"), pt=f"Cor {indice}", fr=f"Couleur {indice}"
            )
            ProductVariant.objects.create(
                product=self.product, sku=f"DINO-X{indice}", color=cor, size=f"{indice} cm",
                sale_price=Decimal("10.00"), stock_quantity=1,
            )

        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.product.get_absolute_url())

        self.assertEqual(len(consultas), base)


class ColorAdminTests(TestCase):
    """O administrador precisa ver qual idioma está preenchendo."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@jdprint.test", password="senha-de-teste"
        )
        self.client.force_login(self.user)
        self.preto = traduzir(
            Color.objects.create(name="Preto", hex_code="#000000"),
            pt="Preto", fr="Noir",
        )

    def test_the_change_page_offers_the_translations(self):
        from django.urls import reverse

        resposta = self.client.get(reverse("admin:catalog_color_change", args=[self.preto.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "TRADUÇÕES")
        self.assertContains(resposta, "Noir")

    def test_the_changelist_shows_which_languages_are_missing(self):
        from django.urls import reverse

        resposta = self.client.get(reverse("admin:catalog_color_changelist"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Noir")
        self.assertContains(resposta, "NL")  # o idioma que falta, marcado

    def test_a_colour_can_be_created_with_its_translations(self):
        from django.urls import reverse

        payload = {
            "name": "Turquesa",
            "slug": "turquesa",
            "hex_code": "#00CED1",
            "is_active": "on",
            "translations-TOTAL_FORMS": "2",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-language": "pt",
            "translations-0-name": "Turquesa",
            "translations-0-id": "",
            "translations-0-master": "",
            "translations-1-language": "fr",
            "translations-1-name": "Turquoise",
            "translations-1-id": "",
            "translations-1-master": "",
            # O inline de componentes (cores compostas): vazio = cor simples.
            "component_links-TOTAL_FORMS": "0",
            "component_links-INITIAL_FORMS": "0",
            "component_links-MIN_NUM_FORMS": "0",
            "component_links-MAX_NUM_FORMS": "1000",
        }
        self.client.post(reverse("admin:catalog_color_add"), payload, follow=True)

        cor = Color.objects.get(name="Turquesa")
        self.assertEqual(cor.tr("name", language="fr"), "Turquoise")
        self.assertEqual(cor.tr("name", language="pt"), "Turquesa")
