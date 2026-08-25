"""Testes da página de detalhes do produto."""

from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import (
    Color,
    Material,
    MediaType,
    PersonalizationType,
    ProductMedia,
    ProductStatus,
    ProductVariant,
)
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    translate_product,
)


class ProductPageBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.models = make_category(slug="modelos", name="Modelos")
        self.animals = make_category(slug="animais", name="Animais", parent=self.models)
        self.product = make_product(
            sku="GATO-01",
            name="Gato Pompom",
            category=self.animals,
            price=Decimal("8.90"),
            stock_quantity=8,
        )

    def get(self, product=None):
        return self.client.get((product or self.product).get_absolute_url())


class ProductPageTests(ProductPageBase):
    def test_page_responds(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/product_detail.html")

    def test_url_uses_the_slug(self):
        self.assertEqual(self.product.get_absolute_url(), "/produtos/gato-pompom/")

    def test_unknown_slug_returns_404(self):
        self.assertEqual(self.client.get("/produtos/nao-existe/").status_code, 404)

    def test_inactive_product_is_not_public(self):
        self.product.status = ProductStatus.DRAFT
        self.product.save()
        self.assertEqual(self.get().status_code, 404)

    def test_shows_name_price_and_heading(self):
        response = self.get()

        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "8,90")
        self.assertContains(response, "<h1")

    def test_seo(self):
        response = self.get()

        self.assertContains(response, "Gato Pompom | JD PRINT")
        self.assertContains(response, 'name="description"')

    def test_breadcrumb_follows_the_category_tree(self):
        response = self.get()
        names = [category.name for category in response.context["breadcrumb"]]

        self.assertEqual(names, ["Modelos", "Animais"])

    def test_add_to_cart_form_points_to_the_cart(self):
        response = self.get()

        self.assertContains(response, "/carrinho/adicionar/")
        self.assertContains(response, "Adicionar ao carrinho")

    def test_quantity_field_respects_the_stock(self):
        response = self.get()
        self.assertEqual(response.context["max_quantity"], 8)

    def test_specifications_only_list_what_exists(self):
        Material.objects.create(name="PLA")
        self.product.materials.add(Material.objects.get(name="PLA"))
        self.product.print_time = timedelta(hours=2, minutes=30)
        self.product.weight_grams = Decimal("35")
        self.product.save()

        labels = [str(label) for label, _value in self.get().context["specifications"]]

        self.assertIn("Material", labels)
        self.assertIn("Peso", labels)
        self.assertIn("Tempo de impressão", labels)
        self.assertNotIn("Dimensões", labels)  # produto sem dimensões


class ProductStockStateTests(ProductPageBase):
    def test_in_stock(self):
        self.assertEqual(self.product.stock_state, "in")
        self.assertContains(self.get(), "Em estoque")

    def test_low_stock(self):
        self.product.stock_quantity = 2
        self.product.save()

        self.assertEqual(self.product.stock_state, "low")
        self.assertContains(self.get(), "Últimas unidades")

    def test_out_of_stock(self):
        self.product.stock_quantity = 0
        self.product.save()

        self.assertEqual(self.product.stock_state, "out")
        self.assertContains(self.get(), "Esgotado")

    def test_made_to_order(self):
        self.product.made_to_order = True
        self.product.production_lead_time_days = 5
        self.product.stock_quantity = 0
        self.product.save()

        self.assertEqual(self.product.stock_state, "made_to_order")
        response = self.get()
        self.assertContains(response, "Produzido sob encomenda")
        self.assertContains(response, "5 dias")


@override_settings(MEDIA_ROOT="/tmp/jdprint-product-page")
class ProductGalleryTests(ProductPageBase):
    def upload(self, name):
        return SimpleUploadedFile(name, b"conteudo")

    def test_gallery_puts_the_primary_image_first(self):
        first = ProductMedia.objects.create(product=self.product, file=self.upload("a.jpg"))
        principal = ProductMedia.objects.create(
            product=self.product, file=self.upload("b.jpg"), is_primary=True
        )

        gallery = self.get().context["gallery"]
        self.assertEqual(gallery[0], principal)
        self.assertIn(first, gallery)

    def test_thumbnails_appear_when_there_is_more_than_one(self):
        ProductMedia.objects.create(product=self.product, file=self.upload("a.jpg"))
        ProductMedia.objects.create(product=self.product, file=self.upload("b.jpg"))

        self.assertContains(self.get(), "data-gallery-thumb")

    def test_video_is_supported(self):
        media = ProductMedia.objects.create(
            product=self.product, file=self.upload("v.mp4"), media_type=MediaType.VIDEO
        )
        response = self.get()

        self.assertContains(response, media.file.url)
        self.assertContains(response, "<video")

    def test_product_without_media_does_not_break(self):
        response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foto em breve")


class ProductTranslationPageTests(ProductPageBase):
    def setUp(self):
        super().setUp()
        translate_product(self.product, "fr", "Chat Pompon", "Marque-page en chat.")

    def test_portuguese(self):
        self.assertContains(self.get(), "Gato Pompom")

    def test_french(self):
        response = self.client.get("/fr/produtos/gato-pompom/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chat Pompon")
        self.assertContains(response, 'lang="fr"')

    def test_falls_back_to_portuguese(self):
        response = self.client.get("/nl/produtos/gato-pompom/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gato Pompom")

    def test_language_switch_stays_on_the_product(self):
        response = self.client.post(
            "/i18n/setlang/", {"language": "fr", "next": self.product.get_absolute_url()}
        )
        self.assertEqual(response["Location"], "/fr/produtos/gato-pompom/")


class ProductVariantPageTests(ProductPageBase):
    def setUp(self):
        super().setUp()
        self.black = Color.objects.create(name="Preto", hex_code="#000000")
        self.white = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.small = ProductVariant.objects.create(
            product=self.product, sku="V-P-15", color=self.black, size="15 cm",
            stock_quantity=4, sale_price=Decimal("8.90"),
        )
        self.large = ProductVariant.objects.create(
            product=self.product, sku="V-B-20", color=self.white, size="20 cm",
            stock_quantity=2, sale_price=Decimal("12.90"),
        )

    def test_variants_are_listed(self):
        response = self.get()

        self.assertContains(response, "Preto")
        self.assertContains(response, "20 cm")
        self.assertEqual(len(response.context["variants"]), 2)

    def test_option_groups_only_include_axes_that_vary(self):
        keys = [group["key"] for group in self.get().context["variant_options"]]

        self.assertIn("color", keys)
        self.assertIn("size", keys)
        self.assertNotIn("material", keys)  # nenhuma variante define material

    def test_price_range_is_shown(self):
        response = self.get()

        self.assertContains(response, "8,90")
        self.assertContains(response, "12,90")

    def test_variant_select_exists_for_no_javascript(self):
        self.assertContains(self.get(), 'name="variant_id"')

    def test_variant_data_is_embedded_safely(self):
        """json_script: o JSON chega íntegro no navegador, sem escape de aspas."""
        response = self.get()

        self.assertContains(response, 'id="variant-data"')
        self.assertContains(response, '"maxQuantity"')
        self.assertNotContains(response, "&quot;maxQuantity&quot;")

    def test_payload_carries_availability(self):
        payload = self.get().context["variant_payload"]
        by_sku = {item["label"]: item for item in payload}

        self.assertTrue(by_sku["Preto · 15 cm"]["available"])
        self.assertEqual(by_sku["Branco · 20 cm"]["maxQuantity"], 2)


class ProductQueryTests(ProductPageBase):
    def test_query_count_does_not_grow_with_the_media(self):
        for index in range(3):
            ProductVariant.objects.create(
                product=self.product, sku=f"V-{index}", size=f"{index} cm", stock_quantity=1
            )

        baseline = self.count_queries()

        for index in range(3, 8):
            ProductVariant.objects.create(
                product=self.product, sku=f"V-{index}", size=f"{index} cm", stock_quantity=1
            )

        with self.assertNumQueries(baseline):
            self.get()

    def count_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.get()
        return len(captured)


class PersonalizationPageTests(ProductPageBase):
    def test_normal_product_has_no_personalization_block(self):
        self.assertNotContains(self.get(), "Escolha sua personalização")

    def test_photo_product_shows_the_upload(self):
        self.product.personalization_type = PersonalizationType.PHOTO
        self.product.save()

        response = self.get()
        self.assertContains(response, "Enviar sua foto")
        self.assertContains(response, 'name="personalization_photo"')

    def test_text_product_shows_the_textarea_and_the_limit(self):
        self.product.personalization_type = PersonalizationType.TEXT
        self.product.personalization_text_limit = 120
        self.product.save()

        response = self.get()
        self.assertContains(response, 'name="personalization_text"')
        self.assertContains(response, "120")

    def test_photo_or_text_shows_the_choice(self):
        self.product.personalization_type = PersonalizationType.PHOTO_OR_TEXT
        self.product.save()

        response = self.get()
        self.assertContains(response, "Enviar uma foto")
        self.assertContains(response, "Informar um texto")

    def test_notes_field_is_always_there_when_personalizing(self):
        self.product.personalization_type = PersonalizationType.TEXT
        self.product.save()

        self.assertContains(self.get(), 'name="personalization_notes"')
