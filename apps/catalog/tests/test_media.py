"""Testes de mídia do produto (fotos, vídeos e GIFs)."""

import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import MediaType, Product, ProductMedia

TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="jdprint-test-media-")


def upload(filename: str, content: bytes = b"conteudo-de-teste"):
    return SimpleUploadedFile(filename, content)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ProductMediaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.product = Product.objects.create(sku="GATO-01")

    def test_product_accepts_several_media_of_different_types(self):
        ProductMedia.objects.create(
            product=self.product, file=upload("foto1.jpg"), media_type=MediaType.IMAGE
        )
        ProductMedia.objects.create(
            product=self.product, file=upload("foto2.png"), media_type=MediaType.IMAGE
        )
        ProductMedia.objects.create(
            product=self.product, file=upload("animacao.gif"), media_type=MediaType.GIF
        )
        ProductMedia.objects.create(
            product=self.product, file=upload("video.mp4"), media_type=MediaType.VIDEO
        )

        self.assertEqual(self.product.media.count(), 4)

    def test_first_media_becomes_primary_automatically(self):
        first = ProductMedia.objects.create(product=self.product, file=upload("foto1.jpg"))
        second = ProductMedia.objects.create(product=self.product, file=upload("foto2.jpg"))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.is_primary)
        self.assertFalse(second.is_primary)

    def test_only_one_primary_per_product(self):
        first = ProductMedia.objects.create(product=self.product, file=upload("foto1.jpg"))
        second = ProductMedia.objects.create(product=self.product, file=upload("foto2.jpg"))

        second.is_primary = True
        second.save()

        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertEqual(self.product.media.filter(is_primary=True).count(), 1)
        self.assertEqual(self.product.media.get(is_primary=True), second)

    def test_primary_media_shortcut(self):
        ProductMedia.objects.create(product=self.product, file=upload("foto1.jpg"))
        self.assertIsNotNone(self.product.primary_media)

    def test_media_is_ordered_by_sort_order(self):
        third = ProductMedia.objects.create(
            product=self.product, file=upload("c.jpg"), sort_order=3
        )
        first = ProductMedia.objects.create(
            product=self.product, file=upload("a.jpg"), sort_order=1
        )
        second = ProductMedia.objects.create(
            product=self.product, file=upload("b.jpg"), sort_order=2
        )

        self.assertEqual(list(self.product.media.all()), [first, second, third])

    def test_extension_must_match_media_type(self):
        media = ProductMedia(
            product=self.product, file=upload("video.mp4"), media_type=MediaType.IMAGE
        )
        with self.assertRaises(ValidationError):
            media.full_clean()

    def test_unsupported_extension_is_rejected(self):
        media = ProductMedia(
            product=self.product, file=upload("modelo.stl"), media_type=MediaType.IMAGE
        )
        with self.assertRaises(ValidationError):
            media.full_clean()

    def test_alt_text_is_stored(self):
        media = ProductMedia.objects.create(
            product=self.product, file=upload("foto1.jpg"), alt_text="Gato branco de pompom"
        )
        self.assertEqual(media.alt_text, "Gato branco de pompom")

    def test_media_is_removed_with_the_product(self):
        ProductMedia.objects.create(product=self.product, file=upload("foto1.jpg"))
        self.product.delete()
        self.assertEqual(ProductMedia.objects.count(), 0)

    def test_files_are_grouped_by_sku(self):
        media = ProductMedia.objects.create(product=self.product, file=upload("foto1.jpg"))
        self.assertTrue(media.file.name.startswith("products/gato-01/"))
