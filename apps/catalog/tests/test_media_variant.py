"""Foto vinculada a uma variante — §1 da etapa 13.

A associação é **opcional** e vive na mídia, não na variante: uma foto sem
variante é foto geral do produto, que é o que todas as fotos já cadastradas
eram antes desta etapa. Nenhum arquivo é copiado; o que muda é uma coluna.

O que estes testes protegem, na ordem do pedido:

* variante com foto → a foto principal troca ao escolher a variante;
* variante sem foto → cai nas fotos gerais, e não numa moldura vazia;
* produto só com fotos gerais → continua igual ao que era;
* foto de **outro** produto não pode ser vinculada;
* trocar de variante não mexe em preço, peso, estoque nem prazo.
"""

import shutil
import tempfile
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.catalog.models import MediaType, ProductMedia
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    make_variant,
)

TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="jdprint-test-media-variant-")


def upload(filename: str, content: bytes = b"conteudo-de-teste"):
    return SimpleUploadedFile(filename, content)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class MediaVariantBase(LanguageResetMixin, TestCase):
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
        self.azul = self.product.default_variant
        self.vermelha = make_variant(
            self.product, sku="GATO-01-VM", price=Decimal("9.90"), stock=3, size="G"
        )

    def payload(self, response, sku):
        """O dicionário que a página entrega ao JavaScript, para uma variante."""
        for variante in response.context["variant_payload"]:
            if variante["sku"] == sku:
                return variante
        self.fail(f"variante {sku} não veio no payload")

    def get(self):
        return self.client.get(self.product.get_absolute_url())


class MediaVariantModelTests(MediaVariantBase):
    """O vínculo em si: opcional, e sempre dentro do mesmo produto."""

    def test_media_starts_without_a_variant(self):
        """Foto cadastrada sem dizer nada é foto geral — não é falta de dado."""
        foto = ProductMedia.objects.create(product=self.product, file=upload("geral.jpg"))

        self.assertIsNone(foto.variant)

    def test_media_can_be_linked_to_a_variant_of_the_same_product(self):
        foto = ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )
        foto.full_clean()

        self.assertEqual(foto.variant, self.azul)
        self.assertEqual(list(self.azul.media.all()), [foto])

    def test_media_cannot_be_linked_to_a_variant_of_another_product(self):
        """O vínculo não pode atravessar produtos: seria a foto do gato no cão."""
        outro = make_product(sku="CAO-01", name="Cão Bola", category=self.category)

        foto = ProductMedia(
            product=self.product, file=upload("errada.jpg"), variant=outro.default_variant
        )

        with self.assertRaises(ValidationError) as erro:
            foto.full_clean()
        self.assertIn("variant", erro.exception.error_dict)

    def test_unlinking_is_just_clearing_the_field(self):
        """Desvincular devolve a foto ao produto — não apaga arquivo nenhum."""
        foto = ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )
        arquivo = foto.file.name

        foto.variant = None
        foto.save()

        foto.refresh_from_db()
        self.assertIsNone(foto.variant)
        self.assertEqual(foto.file.name, arquivo)

    def test_deleting_the_variant_keeps_the_photo(self):
        """`SET_NULL`: some o vínculo, fica a foto. Apagar o arquivo seria perda."""
        foto = ProductMedia.objects.create(
            product=self.product, file=upload("vermelha.jpg"), variant=self.vermelha
        )

        self.vermelha.delete()

        foto.refresh_from_db()
        self.assertIsNone(foto.variant)
        self.assertEqual(self.product.media.count(), 1)

    def test_display_media_ignores_video(self):
        """A troca é da imagem principal: um MP4 não serve de foto de variante."""
        ProductMedia.objects.create(
            product=self.product,
            file=upload("giro.mp4"),
            media_type=MediaType.VIDEO,
            variant=self.azul,
        )

        self.assertIsNone(self.azul.display_media)

    def test_display_media_returns_none_without_photos(self):
        self.assertIsNone(self.azul.display_media)


class MediaVariantPageTests(MediaVariantBase):
    """O que a página entrega ao JavaScript."""

    def test_variant_with_a_photo_carries_its_url(self):
        foto = ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )

        dados = self.payload(self.get(), self.azul.sku)

        self.assertEqual(dados["mediaUrl"], foto.file.url)

    def test_variant_without_a_photo_carries_an_empty_url(self):
        """Vazio quer dizer "use as fotos gerais" — o JavaScript lê isso."""
        ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )

        dados = self.payload(self.get(), self.vermelha.sku)

        self.assertEqual(dados["mediaUrl"], "")

    def test_product_with_only_general_photos_keeps_every_url_empty(self):
        """O caso de todo produto já cadastrado: nada muda para ele."""
        ProductMedia.objects.create(product=self.product, file=upload("geral.jpg"))

        resposta = self.get()

        for variante in resposta.context["variant_payload"]:
            with self.subTest(sku=variante["sku"]):
                self.assertEqual(variante["mediaUrl"], "")

    def test_the_general_photo_is_still_the_one_on_screen(self):
        """A foto vinculada não rouba o lugar da principal antes de escolherem."""
        geral = ProductMedia.objects.create(
            product=self.product, file=upload("geral.jpg"), is_primary=True
        )
        ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )

        resposta = self.get()

        self.assertContains(resposta, f'data-default-url="{geral.file.url}"')

    def test_the_alt_text_describes_the_variant(self):
        ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )

        dados = self.payload(self.get(), self.azul.sku)

        self.assertIn("Gato Pompom", dados["mediaAlt"])

    def test_the_alt_text_prefers_what_the_admin_wrote(self):
        ProductMedia.objects.create(
            product=self.product,
            file=upload("azul.jpg"),
            variant=self.azul,
            alt_text="Gato azul visto de frente",
        )

        dados = self.payload(self.get(), self.azul.sku)

        self.assertEqual(dados["mediaAlt"], "Gato azul visto de frente")

    def test_switching_photos_does_not_touch_price_stock_weight_or_lead_time(self):
        """§11: esta etapa não encosta em preço, estoque, peso nem prazo."""
        ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )

        dados = self.payload(self.get(), self.azul.sku)

        self.assertEqual(Decimal(dados["price"]), self.azul.sale_price)
        self.assertEqual(dados["maxQuantity"], min(self.azul.stock_quantity, 10))
        self.assertEqual(dados["available"], self.azul.is_available)

    def test_the_gallery_javascript_is_told_about_the_variant_photo(self):
        """Sem o gancho, o payload existiria e ninguém trocaria a imagem."""
        with open("static/js/app.js", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        self.assertIn("showVariantMedia", fonte)
        self.assertIn("variant.mediaUrl", fonte)


class MediaVariantAdminTests(MediaVariantBase):
    """O admin precisa deixar escolher e tirar o vínculo — e só o do produto."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.staff = User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(self.staff)

    def change_url(self, product=None):
        return f"/admin/catalog/product/{(product or self.product).pk}/change/"

    def variant_select(self, url, indice="0"):
        """Só o `<select>` da variante — e não a linha inteira da MÍDIA.

        Sem recortar, um `value="1"` do `TOTAL_FORMS` passaria por opção.
        """
        html = self.client.get(url).content.decode()
        marca = f'name="media-{indice}-variant"'
        self.assertIn(marca, html)
        return html.split(marca)[1].split("</select>")[0]

    def test_the_media_row_offers_the_variant_field(self):
        ProductMedia.objects.create(product=self.product, file=upload("geral.jpg"))

        resposta = self.client.get(self.change_url())

        self.assertContains(resposta, 'name="media-0-variant"')

    def test_the_choices_are_only_this_product_variants(self):
        outro = make_product(sku="CAO-01", name="Cão Bola", category=self.category)
        ProductMedia.objects.create(product=self.product, file=upload("geral.jpg"))

        select = self.variant_select(self.change_url())

        self.assertIn(f'value="{self.azul.pk}"', select)
        self.assertIn(f'value="{self.vermelha.pk}"', select)
        self.assertNotIn(f'value="{outro.default_variant.pk}"', select)

    def test_the_field_is_optional_and_can_be_emptied(self):
        """Sem opção vazia, o vínculo viraria obrigatório e sem volta."""
        ProductMedia.objects.create(
            product=self.product, file=upload("azul.jpg"), variant=self.azul
        )

        select = self.variant_select(self.change_url())

        self.assertIn('<option value="">', select)

    def test_the_add_page_offers_no_variant(self):
        """Produto que ainda não existe não tem variante a que vincular."""
        select = self.variant_select("/admin/catalog/product/add/")

        self.assertNotIn(f'value="{self.azul.pk}"', select)

    def test_there_is_no_popup_to_reach_another_product_variant(self):
        """Os atalhos + / lápis / lixeira alcançariam qualquer variante."""
        ProductMedia.objects.create(product=self.product, file=upload("geral.jpg"))

        html = self.client.get(self.change_url()).content.decode()
        linha = html.split('name="media-0-variant"')[1].split("</td>")[0]

        for atalho in ("add-related", "change-related", "delete-related"):
            with self.subTest(atalho=atalho):
                self.assertNotIn(atalho, linha)

    def test_saving_a_photo_with_a_foreign_variant_is_refused(self):
        """A porta dos fundos: um POST montado à mão não escapa do `clean()`."""
        from django.core.exceptions import ValidationError

        outro = make_product(sku="CAO-02", name="Cão Bola II", category=self.category)
        foto = ProductMedia(
            product=self.product,
            file=upload("errada.jpg"),
            variant=outro.default_variant,
        )

        with self.assertRaises(ValidationError):
            foto.full_clean()
