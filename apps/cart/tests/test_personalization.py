"""Testes da personalização (foto, texto ou escolha entre os dois)."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cart.cart import CART_SESSION_KEY
from apps.cart.models import CustomizationUpload
from apps.catalog.models import PersonalizationType
from apps.core.testing import LanguageResetMixin, make_product

MEDIA = "/tmp/jdprint-customization-tests"

#: Cabeçalhos reais: a validação olha os bytes, não a extensão.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64


def image(name="foto.png", content=PNG):
    return SimpleUploadedFile(name, content)


@override_settings(MEDIA_ROOT=MEDIA)
class PersonalizationBase(LanguageResetMixin, TestCase):
    personalization = PersonalizationType.NONE

    def setUp(self):
        super().setUp()
        self.product = make_product(
            sku="GATO-01",
            name="Gato Personalizado",
            price=Decimal("12.00"),
            stock_quantity=5,
            personalization_type=self.personalization,
        )

    def add(self, **extra):
        return self.client.post(
            reverse("cart:add"), {"product_id": self.product.pk, **extra}, follow=True
        )

    def cart_items(self):
        return self.client.session.get(CART_SESSION_KEY, {})

    def customization(self):
        items = list(self.cart_items().values())
        return items[0]["customization"] if items else None


class NoPersonalizationTests(PersonalizationBase):
    personalization = PersonalizationType.NONE

    def test_adds_normally(self):
        self.add()

        self.assertEqual(len(self.cart_items()), 1)
        self.assertIsNone(self.customization())

    def test_ignores_personalization_fields_sent_anyway(self):
        self.add(personalization_text="ignorar isto")
        self.assertIsNone(self.customization())


class PhotoPersonalizationTests(PersonalizationBase):
    personalization = PersonalizationType.PHOTO

    def test_without_photo_is_refused(self):
        response = self.add()

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Envie a foto da personalização")

    def test_with_a_valid_photo_is_accepted(self):
        self.add(personalization_photo=image())

        self.assertEqual(len(self.cart_items()), 1)
        self.assertEqual(self.customization()["type"], "photo")
        self.assertIsNotNone(self.customization()["upload_id"])

    def test_file_is_stored_outside_the_session(self):
        self.add(personalization_photo=image())
        upload = CustomizationUpload.objects.get()

        self.assertTrue(upload.file.name.startswith("customizations/"))
        self.assertEqual(upload.extension, "png")
        self.assertEqual(upload.content_type, "image/png")
        # A sessão guarda só a referência, nunca o binário.
        self.assertEqual(self.customization()["upload_id"], upload.pk)

    def test_stored_name_is_not_the_client_name(self):
        self.add(personalization_photo=image("../../etc/passwd.png"))
        upload = CustomizationUpload.objects.get()

        self.assertNotIn("passwd", upload.file.name)
        self.assertNotIn("..", upload.file.name)

    def test_jpeg_and_webp_are_accepted(self):
        self.add(personalization_photo=image("a.jpg", JPEG))
        self.add(personalization_photo=image("b.webp", WEBP))

        extensions = sorted(CustomizationUpload.objects.values_list("extension", flat=True))
        self.assertEqual(extensions, ["jpg", "webp"])

    def test_file_that_is_not_an_image_is_refused(self):
        response = self.add(
            personalization_photo=SimpleUploadedFile("virus.png", b"MZ\x90\x00 executavel")
        )

        self.assertEqual(len(self.cart_items()), 0)
        self.assertEqual(CustomizationUpload.objects.count(), 0)
        self.assertContains(response, "Envie uma imagem")

    def test_extension_alone_does_not_convince(self):
        """PDF renomeado para .png continua sendo recusado."""
        response = self.add(
            personalization_photo=SimpleUploadedFile("doc.png", b"%PDF-1.7 conteudo")
        )

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Envie uma imagem")

    def test_empty_file_is_refused(self):
        response = self.add(personalization_photo=SimpleUploadedFile("vazio.png", b""))

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "vazio")

    @override_settings(CUSTOMIZATION_MAX_UPLOAD_SIZE=1024)
    def test_file_above_the_limit_is_refused(self):
        big = SimpleUploadedFile("grande.png", PNG + b"\x00" * 4096)
        response = self.add(personalization_photo=big)

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "no máximo")

    def test_notes_are_stored(self):
        self.add(personalization_photo=image(), personalization_notes="Nome embaixo, por favor.")
        self.assertEqual(self.customization()["notes"], "Nome embaixo, por favor.")


class TextPersonalizationTests(PersonalizationBase):
    personalization = PersonalizationType.TEXT

    def test_without_text_is_refused(self):
        response = self.add()

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Informe o texto")

    def test_with_text_is_accepted(self):
        self.add(personalization_text="Para a Marie")

        self.assertEqual(len(self.cart_items()), 1)
        self.assertEqual(self.customization()["type"], "text")
        self.assertEqual(self.customization()["text"], "Para a Marie")

    def test_text_above_the_limit_is_refused(self):
        self.product.personalization_text_limit = 10
        self.product.save()

        response = self.add(personalization_text="x" * 11)

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "no máximo")

    def test_text_exactly_at_the_limit_is_accepted(self):
        self.product.personalization_text_limit = 10
        self.product.save()

        self.add(personalization_text="x" * 10)
        self.assertEqual(len(self.cart_items()), 1)

    def test_blank_text_is_refused(self):
        response = self.add(personalization_text="   ")

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Informe o texto")

    @override_settings(CUSTOMIZATION_NOTES_MAX_LENGTH=20)
    def test_notes_above_the_limit_are_refused(self):
        response = self.add(personalization_text="ok", personalization_notes="y" * 21)

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "observações")


class PhotoOrTextPersonalizationTests(PersonalizationBase):
    personalization = PersonalizationType.PHOTO_OR_TEXT

    def test_without_choosing_is_refused(self):
        response = self.add()

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Escolha entre")

    def test_choosing_photo_requires_the_photo(self):
        response = self.add(personalization_mode="photo")

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Envie a foto")

    def test_choosing_photo_with_the_photo_works(self):
        self.add(personalization_mode="photo", personalization_photo=image())

        self.assertEqual(self.customization()["type"], "photo")
        self.assertEqual(CustomizationUpload.objects.count(), 1)

    def test_choosing_text_requires_the_text(self):
        response = self.add(personalization_mode="text")

        self.assertEqual(len(self.cart_items()), 0)
        self.assertContains(response, "Informe o texto")

    def test_choosing_text_with_the_text_works(self):
        self.add(personalization_mode="text", personalization_text="Feliz aniversário")

        self.assertEqual(self.customization()["type"], "text")
        self.assertEqual(self.customization()["text"], "Feliz aniversário")

    def test_text_choice_ignores_a_photo_sent_together(self):
        self.add(
            personalization_mode="text",
            personalization_text="Só o texto",
            personalization_photo=image(),
        )

        self.assertEqual(self.customization()["type"], "text")
        self.assertIsNone(self.customization()["upload_id"])


@override_settings(MEDIA_ROOT=MEDIA)
class PersonalizedCartLineTests(LanguageResetMixin, TestCase):
    """Personalizações diferentes do mesmo produto são linhas diferentes."""

    def setUp(self):
        super().setUp()
        self.product = make_product(
            sku="CANECA",
            name="Caneca personalizada",
            price=Decimal("10.00"),
            stock_quantity=10,
            personalization_type=PersonalizationType.TEXT,
        )

    def add(self, text):
        return self.client.post(
            reverse("cart:add"),
            {"product_id": self.product.pk, "personalization_text": text},
        )

    def test_two_texts_become_two_lines(self):
        self.add("Para a Marie")
        self.add("Para o Paul")

        self.assertEqual(len(self.client.session[CART_SESSION_KEY]), 2)

    def test_the_same_text_increments_the_same_line(self):
        self.add("Para a Marie")
        self.add("Para a Marie")

        items = self.client.session[CART_SESSION_KEY]
        self.assertEqual(len(items), 1)
        self.assertEqual(list(items.values())[0]["quantity"], 2)

    def test_the_cart_page_shows_the_customization(self):
        self.add("Para a Marie")
        response = self.client.get(reverse("cart:detail"))

        self.assertContains(response, "Para a Marie")
        self.assertContains(response, "Texto")

    def test_the_drawer_shows_the_customization(self):
        self.add("Para a Marie")
        response = self.client.get(reverse("cart:drawer"))

        self.assertContains(response, "Para a Marie")
