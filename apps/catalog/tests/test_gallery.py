"""Galeria e lupa da página do produto — §2 da etapa 13.

Duas coisas, e as duas são sobre não esconder o produto de quem quer comprá-lo:

* a foto **não pode ser cortada** — `object-contain`, nunca `object-cover`;
* clicar abre a foto inteira numa lupa, que fecha no ESC, no fundo e no X, e
  anda entre as fotos sem estourar a viewport.

O que é comportamento de teclado e de clique vive em `static/js/app.js` e é
verificado no navegador; o que dá para provar aqui é que a marcação que o
JavaScript procura existe, e que ela existe **só quando há foto**.
"""

import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import MediaType, ProductMedia
from apps.core.testing import LanguageResetMixin, make_category, make_product

TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="jdprint-test-gallery-")


def upload(filename: str, content: bytes = b"conteudo-de-teste"):
    return SimpleUploadedFile(filename, content)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class GalleryBase(LanguageResetMixin, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="animais", name="Animais")
        self.product = make_product(
            sku="GATO-01",
            name="Gato Pompom",
            category=self.category,
            price=Decimal("8.90"),
            stock_quantity=8,
        )

    def add_photo(self, nome="foto.jpg", **kwargs):
        return ProductMedia.objects.create(
            product=self.product, file=upload(nome), **kwargs
        )

    def get(self):
        return self.client.get(self.product.get_absolute_url())

    def gallery_html(self):
        html = self.get().content.decode()
        return html.split("data-gallery", 1)[1].split("</section>", 1)[0]


class NoCropTests(GalleryBase):
    """A foto inteira, sempre. Cortar esconde o produto que se está vendendo."""

    def test_the_main_photo_is_contained_not_cropped(self):
        self.add_photo("frente.jpg")

        galeria = self.gallery_html()

        self.assertIn("object-contain", galeria)
        self.assertNotIn("object-cover", galeria)

    def test_the_thumbnails_are_contained_too(self):
        self.add_photo("frente.jpg")
        self.add_photo("verso.jpg")

        galeria = self.gallery_html()
        miniaturas = galeria.split("data-gallery-thumb")[1]

        self.assertIn("object-contain", miniaturas)
        self.assertNotIn("object-cover", miniaturas)

    def test_the_frame_keeps_its_square_so_the_page_does_not_jump(self):
        """A moldura é fixa; quem se ajusta é a imagem — não a altura da página."""
        self.add_photo("frente.jpg")

        self.assertIn("aspect-square", self.gallery_html())


class LightboxTests(GalleryBase):
    """A lupa: abre no clique, fecha no X/fundo/ESC, anda entre as fotos."""

    def test_the_lightbox_markup_is_there_when_there_are_photos(self):
        self.add_photo("frente.jpg")

        galeria = self.gallery_html()

        for marca in (
            "data-lightbox",
            "data-lightbox-backdrop",
            "data-lightbox-close",
            "data-lightbox-prev",
            "data-lightbox-next",
            "data-lightbox-media",
            "data-lightbox-counter",
        ):
            with self.subTest(marca=marca):
                self.assertIn(marca, galeria)

    def test_there_is_no_lightbox_without_photos(self):
        """Sem foto não há o que ampliar — e um diálogo vazio confundiria."""
        self.assertNotIn("data-lightbox", self.gallery_html())

    def test_the_main_photo_announces_itself_as_clickable(self):
        self.add_photo("frente.jpg")

        galeria = self.gallery_html()

        self.assertIn("cursor-zoom-in", galeria)
        self.assertIn('role="button"', galeria)
        self.assertIn('tabindex="0"', galeria)

    def test_the_lightbox_is_a_dialog_for_screen_readers(self):
        self.add_photo("frente.jpg")

        lupa = self.gallery_html().split("data-lightbox", 1)[1]

        self.assertIn('role="dialog"', lupa)
        self.assertIn('aria-modal="true"', lupa)

    def test_the_lightbox_starts_hidden(self):
        """Sem `hidden` ela cobriria a página antes de alguém pedir."""
        self.add_photo("frente.jpg")

        abertura = self.gallery_html().split("data-lightbox", 1)[1][:120]

        self.assertIn("hidden", abertura)

    def test_the_default_photo_is_remembered_for_the_way_back(self):
        """É para ela que a galeria volta quando a variante não tem foto."""
        foto = self.add_photo("frente.jpg")

        galeria = self.gallery_html()

        self.assertIn(f'data-default-url="{foto.file.url}"', galeria)
        self.assertIn("data-default-alt=", galeria)
        self.assertIn('data-default-type="IMAGE"', galeria)

    def test_a_video_is_not_offered_to_the_lightbox_as_a_photo(self):
        """Vídeo tem `controls`; ampliá-lo como imagem daria moldura preta."""
        self.add_photo("giro.mp4", media_type=MediaType.VIDEO)

        galeria = self.gallery_html()

        self.assertIn("<video", galeria)
        self.assertIn('data-default-type="VIDEO"', galeria)

    def test_the_javascript_knows_how_to_close_and_navigate(self):
        """O que o navegador faz mora aqui; sem isto a marcação seria decoração."""
        with open("static/js/app.js", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        for gancho in ("data-lightbox", "Escape", "ArrowLeft", "ArrowRight"):
            with self.subTest(gancho=gancho):
                self.assertIn(gancho, fonte)

    def test_the_stylesheet_keeps_the_photo_inside_the_viewport(self):
        """Uma foto de 4000 px não pode empurrar a tela nem sair dela."""
        with open("static/src/input.css", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        bloco = fonte.split(".jd-lightbox-stage", 1)[1].split("}", 1)[0]

        self.assertIn("max-", bloco)
        self.assertIn("vh", fonte.split(".jd-lightbox-stage", 1)[1][:400])
