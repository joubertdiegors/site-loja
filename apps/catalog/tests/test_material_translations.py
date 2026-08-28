"""O material é um só; o nome muda com o idioma.

Mesmo mecanismo da cor (etapa 9) e do produto: uma linha por idioma numa tabela
filha, com o fallback da casa. A variante continua apontando para **um**
`Material` — nada é duplicado por idioma.

"PLA" e "PETG" são nomes próprios e ficam iguais nos quatro idiomas. O que a
etapa 10 resolve de verdade é "Resina" e "Madeira", que apareciam em português
numa loja francesa.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import translation as django_translation

from apps.cart.cart import CartLine
from apps.catalog.models import (
    Material,
    MaterialTranslation,
    ProductStatus,
    ProductVariant,
)
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services


def traduzir(material, **nomes):
    for idioma, nome in nomes.items():
        MaterialTranslation.objects.create(master=material, language=idioma, name=nome)
    material.refresh_translations()
    return material


class MaterialTranslationTests(TestCase):
    def setUp(self):
        self.resina = traduzir(
            Material.objects.create(name="Resina"),
            pt="Resina", fr="Résine", nl="Hars", en="Resin",
        )

    def test_the_four_languages(self):
        esperado = {"pt": "Resina", "fr": "Résine", "nl": "Hars", "en": "Resin"}
        for idioma, nome in esperado.items():
            with self.subTest(idioma=idioma):
                self.assertEqual(self.resina.tr("name", language=idioma), nome)

    def test_one_material_serves_every_language(self):
        self.assertEqual(Material.objects.count(), 1)
        self.assertEqual(self.resina.translations.count(), 4)

    def test_the_internal_name_is_not_the_customer_name(self):
        self.assertEqual(self.resina.name, "Resina")
        with django_translation.override("fr"):
            self.assertEqual(self.resina.display_name, "Résine")

    def test_creating_a_material_with_translations(self):
        novo = traduzir(
            Material.objects.create(name="Madeira"),
            pt="Madeira", fr="Bois", nl="Hout", en="Wood",
        )

        self.assertEqual(novo.tr("name", language="nl"), "Hout")
        self.assertEqual(novo.tr("name", language="en"), "Wood")

    def test_editing_a_translation(self):
        traducao = self.resina.translations.get(language="fr")
        traducao.name = "Résine époxy"
        traducao.save()
        self.resina.refresh_translations()

        self.assertEqual(self.resina.tr("name", language="fr"), "Résine époxy")

    def test_missing_language_falls_back_to_portuguese(self):
        parcial = traduzir(Material.objects.create(name="Cortiça"), pt="Cortiça")

        self.assertEqual(parcial.tr("name", language="fr"), "Cortiça")
        self.assertEqual(parcial.tr("name", language="nl"), "Cortiça")

    def test_material_without_any_translation_falls_back_to_the_internal_name(self):
        cru = Material.objects.create(name="Sem tradução")

        self.assertEqual(cru.display_name, "Sem tradução")

    def test_a_language_cannot_be_registered_twice(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MaterialTranslation.objects.create(
                master=self.resina, language="fr", name="Résine dure"
            )

    def test_an_empty_name_is_refused_by_the_database(self):
        outro = Material.objects.create(name="Outro")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MaterialTranslation.objects.create(master=outro, language="pt", name="")

    def test_translations_die_with_the_material(self):
        self.resina.delete()
        self.assertEqual(MaterialTranslation.objects.count(), 0)

    def test_a_proper_noun_is_the_same_in_every_language(self):
        """PLA não se traduz — e o cadastro precisa aceitar isso sem gambiarra."""
        pla = traduzir(
            Material.objects.create(name="PLA"), pt="PLA", fr="PLA", nl="PLA", en="PLA"
        )

        for idioma in ("pt", "fr", "nl", "en"):
            self.assertEqual(pla.tr("name", language=idioma), "PLA")


class MaterialInTheStoreTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.resina = traduzir(
            Material.objects.create(name="Resina"),
            pt="Resina", fr="Résine", nl="Hars", en="Resin",
        )
        self.madeira = traduzir(
            Material.objects.create(name="Madeira"),
            pt="Madeira", fr="Bois", nl="Hout", en="Wood",
        )
        self.product = make_product(
            sku="DINO",
            name="Dinossauro",
            category=self.category,
            status=ProductStatus.ACTIVE,
            with_variant=False,
        )
        for sku, material, tamanho in (
            ("DINO-R", self.resina, "25 cm"),
            ("DINO-M", self.madeira, "30 cm"),
        ):
            ProductVariant.objects.create(
                product=self.product, sku=sku, material=material, size=tamanho,
                sale_price=Decimal("27.90"), stock_quantity=5,
            )
        self.product.refresh_from_db()

    def test_the_variant_label_uses_the_translated_material(self):
        variante = ProductVariant.objects.select_related("material").get(sku="DINO-R")

        self.assertEqual(variante.label, "25 cm · Resina")
        with django_translation.override("fr"):
            variante = ProductVariant.objects.select_related("material").get(sku="DINO-R")
            self.assertEqual(variante.label, "25 cm · Résine")

    def test_the_product_page_shows_the_material_in_french(self):
        resposta = self.client.get("/fr" + self.product.get_absolute_url())

        self.assertContains(resposta, "Résine")
        self.assertContains(resposta, "Bois")

    def test_the_product_page_shows_the_material_in_dutch(self):
        resposta = self.client.get("/nl" + self.product.get_absolute_url())

        self.assertContains(resposta, "Hars")
        self.assertContains(resposta, "Hout")

    def test_the_product_page_shows_the_material_in_english(self):
        resposta = self.client.get("/en" + self.product.get_absolute_url())

        self.assertContains(resposta, "Resin")
        self.assertContains(resposta, "Wood")

    def test_portuguese_is_the_default_url(self):
        resposta = self.client.get(self.product.get_absolute_url())

        self.assertContains(resposta, "Resina")
        self.assertContains(resposta, "Madeira")

    def test_the_specification_row_is_translated(self):
        resposta = self.client.get("/fr" + self.product.get_absolute_url())
        ficha = {chave: valor for chave, _rotulo, valor in resposta.context["specifications"]}

        self.assertEqual(ficha["material"], "Résine")

    def test_reading_the_materials_does_not_cost_a_query_per_variant(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.product.get_absolute_url())
        base = len(consultas)

        for indice in range(6):
            material = traduzir(
                Material.objects.create(name=f"Material {indice}"),
                pt=f"Material {indice}", fr=f"Matériau {indice}",
            )
            ProductVariant.objects.create(
                product=self.product, sku=f"DINO-X{indice}", material=material,
                size=f"{indice} cm", sale_price=Decimal("10.00"), stock_quantity=1,
            )

        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.product.get_absolute_url())

        self.assertEqual(len(consultas), base)


class MaterialInTheOrderSnapshotTests(LanguageResetMixin, TestCase):
    """O pedido guarda o material no idioma em que o cliente comprou."""

    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        self.user = make_user(username="diego3d", email="diego@example.com")
        self.address = make_address(self.user.customer, self.country)

        self.madeira = traduzir(
            Material.objects.create(name="Madeira"),
            pt="Madeira", fr="Bois", nl="Hout", en="Wood",
        )
        self.product = make_product(sku="DINO", name="Dinossauro", with_variant=False)
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="DINO-M", material=self.madeira, size="30 cm",
            sale_price=Decimal("32.90"), weight_grams=Decimal("300"), stock_quantity=5,
        )
        self.product.refresh_from_db()

    def pedido(self, language=""):
        linha = CartLine(
            key=f"{self.product.pk}:{self.variant.pk}:-",
            product=self.product,
            variant=self.variant,
            quantity=1,
        )
        return services.create_order(
            customer=self.user.customer,
            lines=[linha],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
            language=language,
        )

    def test_the_material_is_copied_in_portuguese(self):
        item = self.pedido().items.get()

        self.assertEqual(item.material_name, "Madeira")

    def test_the_material_is_copied_in_the_customers_language(self):
        with django_translation.override("fr"):
            item = self.pedido(language="fr").items.get()

        self.assertEqual(item.material_name, "Bois")

    def test_the_material_is_copied_in_dutch(self):
        with django_translation.override("nl"):
            item = self.pedido(language="nl").items.get()

        self.assertEqual(item.material_name, "Hout")

    def test_the_snapshot_survives_a_material_rename(self):
        item = self.pedido().items.get()

        traducao = self.madeira.translations.get(language="pt")
        traducao.name = "Madeira maciça"
        traducao.save()
        self.madeira.name = "MADEIRA-INTERNO"
        self.madeira.save()

        item.refresh_from_db()
        self.assertEqual(item.material_name, "Madeira")

    def test_the_snapshot_survives_the_material_being_deleted_from_the_variant(self):
        item = self.pedido().items.get()

        self.variant.material = None
        self.variant.save()

        item.refresh_from_db()
        self.assertEqual(item.material_name, "Madeira")


class MaterialAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@jdprint.test", password="senha-de-teste"
        )
        self.client.force_login(self.user)
        self.resina = traduzir(
            Material.objects.create(name="Resina"), pt="Resina", fr="Résine"
        )

    def test_the_change_page_offers_the_translations(self):
        resposta = self.client.get(
            reverse("admin:catalog_material_change", args=[self.resina.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "TRADUÇÕES")
        self.assertContains(resposta, "Résine")

    def test_the_changelist_shows_which_languages_are_missing(self):
        resposta = self.client.get(reverse("admin:catalog_material_changelist"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Résine")
        self.assertContains(resposta, "NL")

    def test_a_material_can_be_created_with_its_translations(self):
        payload = {
            "name": "Cortiça",
            "slug": "cortica",
            "description": "",
            "is_active": "on",
            "translations-TOTAL_FORMS": "2",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-language": "pt",
            "translations-0-name": "Cortiça",
            "translations-0-id": "",
            "translations-0-master": "",
            "translations-1-language": "fr",
            "translations-1-name": "Liège",
            "translations-1-id": "",
            "translations-1-master": "",
        }
        self.client.post(reverse("admin:catalog_material_add"), payload, follow=True)

        material = Material.objects.get(name="Cortiça")
        self.assertEqual(material.tr("name", language="fr"), "Liège")
        self.assertEqual(material.tr("name", language="pt"), "Cortiça")

    def test_an_existing_translation_can_be_edited_through_the_admin(self):
        traducao = self.resina.translations.get(language="fr")
        payload = {
            "name": "Resina",
            "slug": self.resina.slug,
            "description": "",
            "is_active": "on",
            "translations-TOTAL_FORMS": "2",
            "translations-INITIAL_FORMS": "2",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-id": str(self.resina.translations.get(language="pt").pk),
            "translations-0-master": str(self.resina.pk),
            "translations-0-language": "pt",
            "translations-0-name": "Resina",
            "translations-1-id": str(traducao.pk),
            "translations-1-master": str(self.resina.pk),
            "translations-1-language": "fr",
            "translations-1-name": "Résine dure",
        }
        self.client.post(
            reverse("admin:catalog_material_change", args=[self.resina.pk]),
            payload,
            follow=True,
        )

        self.resina.refresh_translations()
        self.assertEqual(self.resina.tr("name", language="fr"), "Résine dure")
