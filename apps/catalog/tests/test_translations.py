"""Testes de conteúdo multilíngue."""

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import translation

from apps.catalog.models import Product, ProductTranslation


class ProductTranslationTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="GATO-01")
        ProductTranslation.objects.create(
            master=self.product,
            language="pt",
            name="Gato Pompom",
            short_description="Marcador de página em forma de gato.",
        )
        ProductTranslation.objects.create(
            master=self.product,
            language="fr",
            name="Chat Pompom",
            short_description="Marque-page en forme de chat.",
        )
        ProductTranslation.objects.create(
            master=self.product, language="nl", name="Pompoen Kat"
        )
        self.product.refresh_translations()

    def test_one_row_per_language(self):
        self.assertEqual(self.product.translations.count(), 3)
        self.assertEqual(self.product.available_languages(), ["fr", "nl", "pt"])

    def test_reads_the_requested_language(self):
        self.assertEqual(self.product.name_in("pt"), "Gato Pompom")
        self.assertEqual(self.product.name_in("fr"), "Chat Pompom")
        self.assertEqual(self.product.name_in("nl"), "Pompoen Kat")

    def test_falls_back_to_portuguese(self):
        # Alemão ainda não foi traduzido.
        self.assertEqual(self.product.name_in("de"), "Gato Pompom")

    def test_falls_back_to_sku_without_any_translation(self):
        empty = Product.objects.create(sku="SEM-NOME")
        self.assertEqual(empty.display_name, "SEM-NOME")

    def test_follows_the_active_interface_language(self):
        with translation.override("fr"):
            self.assertEqual(self.product.display_name, "Chat Pompom")
        with translation.override("pt-br"):
            self.assertEqual(self.product.display_name, "Gato Pompom")

    def test_short_description_falls_back(self):
        self.assertEqual(
            self.product.tr("short_description", language="nl"),
            "Marcador de página em forma de gato.",
        )

    def test_duplicate_language_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductTranslation.objects.create(
                master=self.product, language="pt", name="Outro nome"
            )

    def test_technical_fields_are_not_duplicated_per_language(self):
        translation_fields = {field.name for field in ProductTranslation._meta.get_fields()}
        for technical in ("sku", "sale_price", "weight_grams", "stock_quantity", "print_time"):
            self.assertNotIn(technical, translation_fields)

    def test_translations_are_removed_with_the_product(self):
        self.product.delete()
        self.assertEqual(ProductTranslation.objects.count(), 0)

    def test_prefetch_avoids_extra_queries(self):
        products = list(Product.objects.with_translations())
        with self.assertNumQueries(0):
            [item.display_name for item in products]
