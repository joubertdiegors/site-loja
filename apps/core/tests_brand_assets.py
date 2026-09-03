"""As imagens da marca, gerenciáveis pelo Admin.

Antes, uma função procurava `jdprint-logo.svg` dentro de `static/images/logo/` e
a **mesma** imagem servia o cabeçalho claro e o rodapé escuro. Trocar qualquer
uma exigia um commit e um deploy, e o rodapé ficava com a versão errada porque
não havia como ter duas.

O que estes testes protegem, em ordem de importância:

1. **cada imagem no seu lugar** — a do topo não vaza para o rodapé e vice-versa;
2. **o site não quebra sem imagem nenhuma** — uma instalação recém-criada, em
   que ninguém abriu a tela, continua servindo todas as páginas;
3. **a imagem padrão não sobrescreve foto de produto** — ela aparece só onde
   não há foto;
4. **o upload é conferido** — um SVG com `<script>` dentro seria executado no
   mesmo domínio da loja.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import BrandAssets
from apps.core.testing import LanguageResetMixin, make_category, make_product
from apps.core.uploads import validate_brand_image

# Um PNG de 1×1 de verdade: assinatura correta, para o `sniff` reconhecer.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
SVG_LIMPO = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle r="5"/></svg>'
SVG_COM_SCRIPT = (
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/roubar")</script></svg>'
)
GIF = b"GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;"


def imagem(nome="logo.png", conteudo=PNG):
    return SimpleUploadedFile(nome, conteudo, content_type="image/png")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="jdprint-brand-"))
class BrandBase(LanguageResetMixin, TestCase):
    """Uploads num diretório temporário, nunca no `media/` do projeto.

    Sem isto, cada `manage.py test` deixava um `icone.png` e um `padrao.png` na
    pasta de mídia de quem desenvolve — mais uma cópia com sufixo por execução,
    porque o Django renomeia em vez de sobrescrever.
    """

    def config(self, **campos):
        obj = BrandAssets.load()
        for nome, arquivo in campos.items():
            setattr(obj, nome, arquivo)
        obj.save()
        return obj


# ---------------------------------------------------------------------------
# O model
# ---------------------------------------------------------------------------


class ConfiguracaoTests(BrandBase):
    def test_there_is_only_one_row(self):
        """Não é uma galeria: é *a* identidade da loja."""
        BrandAssets.load()
        BrandAssets.load().save()

        self.assertEqual(BrandAssets.objects.count(), 1)

    def test_a_fresh_shop_has_no_row_and_no_url(self):
        """Instalação nova, em que ninguém abriu a tela ainda."""
        self.assertIsNone(BrandAssets.current())
        self.assertEqual(BrandAssets.url_for("header_logo"), "")

    def test_each_image_is_uploaded_independently(self):
        self.config(header_logo=imagem("topo.png"))
        obj = BrandAssets.current()

        self.assertTrue(obj.header_logo)
        self.assertFalse(obj.footer_logo)
        self.assertFalse(obj.favicon)
        self.assertFalse(obj.product_placeholder)

    def test_the_four_images_can_be_different_files(self):
        self.config(
            header_logo=imagem("topo.png"),
            footer_logo=imagem("rodape.png"),
            favicon=imagem("icone.png"),
            product_placeholder=imagem("padrao.png"),
        )
        obj = BrandAssets.current()

        urls = {
            obj.header_logo.url,
            obj.footer_logo.url,
            obj.favicon.url,
            obj.product_placeholder.url,
        }
        self.assertEqual(len(urls), 4)

    def test_replacing_one_leaves_the_others_alone(self):
        self.config(header_logo=imagem("topo.png"), footer_logo=imagem("rodape.png"))
        antes = BrandAssets.current().footer_logo.name

        self.config(header_logo=imagem("topo-novo.png"))

        depois = BrandAssets.current()
        self.assertIn("topo-novo", depois.header_logo.name)
        self.assertEqual(depois.footer_logo.name, antes)

    def test_the_files_live_under_media_brand(self):
        """Fora de `static/`: `static/` é código, e logo não é código."""
        self.config(header_logo=imagem("topo.png"))

        self.assertTrue(BrandAssets.current().header_logo.name.startswith("brand/"))
        self.assertIn("/media/brand/", BrandAssets.url_for("header_logo"))


# ---------------------------------------------------------------------------
# Cada imagem no seu lugar
# ---------------------------------------------------------------------------


class CadaUmaNoSeuLugarTests(BrandBase):
    def home(self):
        return self.client.get(reverse("home:index"))

    def test_the_header_logo_appears_in_the_header(self):
        self.config(header_logo=imagem("topo.png"))

        self.assertContains(self.home(), BrandAssets.current().header_logo.url)

    def test_the_footer_logo_appears_in_the_footer(self):
        self.config(footer_logo=imagem("rodape.png"))

        self.assertContains(self.home(), BrandAssets.current().footer_logo.url)

    def test_the_header_logo_does_not_leak_into_the_footer(self):
        """O erro que a tela veio corrigir: uma imagem servindo os dois."""
        self.config(header_logo=imagem("topo.png"))
        corpo = self.home().content.decode()

        url = BrandAssets.current().header_logo.url
        rodape = corpo[corpo.index("<footer"):]
        self.assertNotIn(url, rodape)

    def test_the_footer_logo_does_not_leak_into_the_header(self):
        self.config(footer_logo=imagem("rodape.png"))
        corpo = self.home().content.decode()

        url = BrandAssets.current().footer_logo.url
        cabecalho = corpo[: corpo.index("</header>")]
        self.assertNotIn(url, cabecalho)

    def test_the_two_logos_can_be_completely_different(self):
        self.config(header_logo=imagem("topo.png"), footer_logo=imagem("rodape.png"))
        corpo = self.home().content.decode()
        obj = BrandAssets.current()

        self.assertIn(obj.header_logo.url, corpo[: corpo.index("</header>")])
        self.assertIn(obj.footer_logo.url, corpo[corpo.index("<footer"):])

    def test_the_favicon_is_used_in_the_icon_link(self):
        self.config(favicon=imagem("icone.png"))

        url = BrandAssets.current().favicon.url
        self.assertContains(self.home(), f'<link rel="icon" href="{url}">')

    def test_the_browser_shortcut_follows_the_configured_icon(self):
        """`/favicon.ico` é pedido sozinho pelo navegador, em toda visita."""
        self.config(favicon=imagem("icone.png"))

        resposta = self.client.get("/favicon.ico")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], BrandAssets.current().favicon.url)

    def test_the_browser_shortcut_falls_back_when_nothing_is_configured(self):
        resposta = self.client.get("/favicon.ico")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], "/static/images/logo/favicon.svg")

    def test_the_favicon_is_independent_of_the_logos(self):
        self.config(header_logo=imagem("topo.png"), favicon=imagem("icone.png"))
        corpo = self.home().content.decode()
        obj = BrandAssets.current()

        self.assertNotIn(f'href="{obj.header_logo.url}"', corpo.split("</head>")[0])
        self.assertIn(f'href="{obj.favicon.url}"', corpo.split("</head>")[0])


# ---------------------------------------------------------------------------
# A imagem padrão dos produtos
# ---------------------------------------------------------------------------


class ImagemPadraoTests(BrandBase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="SEM-FOTO", name="Vaso Sem Foto", category=self.category)

    def loja(self):
        return self.client.get(reverse("catalog:models_shop"))

    def test_a_product_without_a_photo_uses_the_default_image(self):
        self.config(product_placeholder=imagem("padrao.png"))

        self.assertContains(self.loja(), BrandAssets.current().product_placeholder.url)

    def test_a_product_with_its_own_photo_ignores_the_default(self):
        """A imagem padrão não substitui foto nenhuma."""
        from apps.catalog.models import ProductMedia

        ProductMedia.objects.create(
            product=self.product,
            file=SimpleUploadedFile("propria.png", PNG, content_type="image/png"),
            is_primary=True,
        )
        self.config(product_placeholder=imagem("padrao.png"))

        corpo = self.loja().content.decode()
        self.assertIn(self.product.media.get().file.url, corpo)
        self.assertNotIn(BrandAssets.current().product_placeholder.url, corpo)

    def test_without_a_default_the_reserved_space_still_shows(self):
        corpo = self.loja().content.decode()

        self.assertIn("Foto em breve", corpo)

    def test_the_default_replaces_the_reserved_space(self):
        self.config(product_placeholder=imagem("padrao.png"))

        corpo = self.loja().content.decode()
        self.assertNotIn("Foto em breve", corpo)


# ---------------------------------------------------------------------------
# Sem imagem nenhuma, o site continua de pé
# ---------------------------------------------------------------------------


class SemImagemNenhumaTests(BrandBase):
    def test_every_public_page_still_answers(self):
        make_product(sku="X", name="Vaso", category=make_category(slug="m", name="M"))

        for nome in ("home:index", "catalog:models_shop"):
            with self.subTest(pagina=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_the_header_falls_back_to_the_typographic_mark(self):
        corpo = self.client.get(reverse("home:index")).content.decode()

        self.assertIn("Espaço reservado à logo oficial", corpo)

    def test_the_static_file_bridges_the_transition(self):
        """Enquanto ninguém enviou a imagem, o arquivo que já estava no
        repositório continua servindo — senão esta etapa deixaria o site no ar
        sem logo até alguém abrir o Admin."""
        from apps.core.templatetags.jdprint import brand_favicon_url

        self.assertEqual(brand_favicon_url({}), "/static/images/logo/favicon.svg")

    def test_with_no_file_anywhere_no_icon_link_is_generated(self):
        """Melhor nenhum ícone do que um `<link>` apontando para o vazio."""
        from unittest import mock

        from apps.core.templatetags import jdprint

        with mock.patch.dict(jdprint.STATIC_FALLBACKS, {"favicon": ()}, clear=False):
            jdprint._logo_cache.clear()
            self.assertEqual(jdprint.brand_favicon_url({}), "")
        jdprint._logo_cache.clear()

    def test_the_footer_logo_has_no_static_bridge(self):
        """Usar a logo do topo no rodapé escuro é o que esta etapa corrige."""
        from apps.core.templatetags.jdprint import brand_footer_logo_url

        self.assertEqual(brand_footer_logo_url({}), "")

    def test_the_product_default_has_no_static_bridge(self):
        from apps.core.templatetags.jdprint import product_placeholder_url

        self.assertEqual(product_placeholder_url({}), "")

    def test_the_row_may_exist_with_every_field_empty(self):
        BrandAssets.load()

        self.assertEqual(self.client.get(reverse("home:index")).status_code, 200)


# ---------------------------------------------------------------------------
# Validação do upload
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="jdprint-brand-"))
class ValidacaoDoUploadTests(TestCase):
    def validar(self, nome, conteudo):
        validate_brand_image(SimpleUploadedFile(nome, conteudo))

    def test_a_real_png_is_accepted(self):
        self.validar("logo.png", PNG)

    def test_a_clean_svg_is_accepted(self):
        """Logo é desenho vetorial: recusar SVG entregaria marca borrada."""
        self.validar("logo.svg", SVG_LIMPO)

    def test_a_gif_is_accepted(self):
        self.validar("logo.gif", GIF)

    def test_an_svg_carrying_a_script_is_refused(self):
        """Servido do mesmo domínio, ele roda com o cookie de sessão de quem abrir."""
        with self.assertRaises(ValidationError) as erro:
            self.validar("logo.svg", SVG_COM_SCRIPT)

        self.assertIn("código executável", str(erro.exception))

    def test_an_svg_with_an_event_handler_is_refused(self):
        with self.assertRaises(ValidationError):
            self.validar("logo.svg", b'<svg onload="alert(1)"></svg>')

    def test_an_executable_renamed_to_png_is_refused(self):
        """A extensão vem do navegador; a assinatura vem do arquivo."""
        with self.assertRaises(ValidationError) as erro:
            self.validar("logo.png", b"MZ\x90\x00\x03\x00\x00\x00")

        self.assertIn("não reconheci", str(erro.exception).lower())

    def test_a_text_file_renamed_to_png_is_refused(self):
        with self.assertRaises(ValidationError):
            self.validar("logo.png", b"isto e so um texto qualquer")

    @override_settings(BRAND_IMAGE_MAX_UPLOAD_SIZE=64)
    def test_a_file_over_the_limit_is_refused(self):
        with self.assertRaises(ValidationError) as erro:
            self.validar("logo.png", PNG + b"\x00" * 500)

        self.assertIn("limite", str(erro.exception))

    def test_the_model_field_runs_the_validator(self):
        """A conferência tem de estar no campo, não só na tela do Admin."""
        obj = BrandAssets(header_logo=SimpleUploadedFile("logo.svg", SVG_COM_SCRIPT))

        with self.assertRaises(ValidationError):
            obj.full_clean()


# ---------------------------------------------------------------------------
# O Admin
# ---------------------------------------------------------------------------


class AdminTests(BrandBase):
    SENHA = "senha-de-teste-77"

    def setUp(self):
        super().setUp()
        self.chefe = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", self.SENHA
        )

    def tela(self):
        return self.client.get(
            reverse("admin:core_brandassets_change", args=[BrandAssets.load().pk])
        )

    def test_it_lives_in_the_shop_settings_section(self):
        from config.admin import SECOES

        secoes = {str(titulo): chaves for titulo, chaves in SECOES}
        self.assertIn("core.brandassets", secoes["CONFIGURAÇÕES DA LOJA"])

    def test_the_section_shows_it_by_name(self):
        self.client.force_login(self.chefe)

        resposta = self.client.get(reverse("admin:index"))

        self.assertContains(resposta, "LOGOS E IMAGENS")

    def test_the_four_uploads_are_on_the_page(self):
        self.client.force_login(self.chefe)

        corpo = self.tela().content.decode()

        for campo in ("header_logo", "footer_logo", "favicon", "product_placeholder"):
            with self.subTest(campo=campo):
                self.assertIn(f'name="{campo}"', corpo)

    def test_each_one_has_its_own_block(self):
        self.client.force_login(self.chefe)

        corpo = self.tela().content.decode()

        for titulo in (
            "LOGO DO TOPO",
            "LOGO DO RODAPÉ",
            "FAVICON",
            "IMAGEM PADRÃO DOS PRODUTOS",
        ):
            with self.subTest(bloco=titulo):
                self.assertIn(titulo, corpo)

    def test_a_configured_image_is_previewed(self):
        self.config(header_logo=imagem("topo.png"))
        self.client.force_login(self.chefe)

        self.assertContains(self.tela(), BrandAssets.current().header_logo.url)

    def test_an_empty_field_says_so_instead_of_showing_a_broken_image(self):
        self.client.force_login(self.chefe)

        self.assertContains(self.tela(), "Nenhuma imagem cadastrada")

    def test_uploading_through_the_admin_works(self):
        self.client.force_login(self.chefe)
        obj = BrandAssets.load()

        self.client.post(
            reverse("admin:core_brandassets_change", args=[obj.pk]),
            {
                "header_logo": SimpleUploadedFile("topo.png", PNG, content_type="image/png"),
                "footer_logo": "",
                "favicon": "",
                "product_placeholder": "",
            },
            follow=True,
        )

        obj.refresh_from_db()
        self.assertTrue(obj.header_logo)

    def test_the_admin_refuses_a_dangerous_svg(self):
        self.client.force_login(self.chefe)
        obj = BrandAssets.load()

        resposta = self.client.post(
            reverse("admin:core_brandassets_change", args=[obj.pk]),
            {
                "header_logo": SimpleUploadedFile(
                    "logo.svg", SVG_COM_SCRIPT, content_type="image/svg+xml"
                ),
                "footer_logo": "",
                "favicon": "",
                "product_placeholder": "",
            },
        )

        obj.refresh_from_db()
        self.assertFalse(obj.header_logo)
        self.assertContains(resposta, "código executável")

    def test_it_cannot_be_deleted(self):
        """Apagar deixaria o site sem marca. Para tirar uma imagem, limpe o campo."""
        from apps.core.admin import BrandAssetsAdmin

        self.assertFalse(BrandAssetsAdmin(BrandAssets, None).has_delete_permission(None))

    def test_a_second_row_cannot_be_added(self):
        from apps.core.admin import BrandAssetsAdmin

        BrandAssets.load()
        pedido = type("R", (), {"user": self.chefe})()

        self.assertFalse(BrandAssetsAdmin(BrandAssets, None).has_add_permission(pedido))

    def test_staff_without_permission_cannot_open_it(self):
        pessoa = get_user_model().objects.create_user(
            username="recem", email="recem@jdprint.test", password=self.SENHA, is_staff=True
        )
        self.client.force_login(pessoa)

        resposta = self.client.get(
            reverse("admin:core_brandassets_change", args=[BrandAssets.load().pk])
        )

        self.assertIn(resposta.status_code, (302, 403))

    def test_a_viewer_cannot_change_it(self):
        pessoa = get_user_model().objects.create_user(
            username="olheiro", email="olheiro@jdprint.test", password=self.SENHA, is_staff=True
        )
        pessoa.user_permissions.set(
            Permission.objects.filter(
                codename="view_brandassets", content_type__app_label="core"
            )
        )
        self.client.force_login(pessoa)

        corpo = self.tela().content.decode()

        self.assertNotIn('name="header_logo"', corpo)

    def test_a_customer_cannot_reach_it(self):
        cliente = get_user_model().objects.create_user(
            username="ana", email="ana@exemplo.test", password=self.SENHA
        )
        self.client.force_login(cliente)

        resposta = self.client.get(
            reverse("admin:core_brandassets_change", args=[BrandAssets.load().pk])
        )

        self.assertEqual(resposta.status_code, 302)


# ---------------------------------------------------------------------------
# Orçamento de consultas
# ---------------------------------------------------------------------------


class ConsultasTests(BrandBase):
    def test_the_row_is_read_once_per_request_not_once_per_card(self):
        """`product_placeholder_url` roda dentro do laço da grade."""
        category = make_category(slug="modelos", name="Modelos")
        for indice in range(6):
            make_product(sku=f"P{indice}", name=f"Produto {indice}", category=category)
        self.config(product_placeholder=imagem("padrao.png"))

        with self.assertNumQueries(self.consultas_da_loja()):
            self.client.get(reverse("catalog:models_shop"))

    def consultas_da_loja(self) -> int:
        """Mede a própria página — o número exato não é o ponto do teste.

        O que importa é o teste seguinte: acrescentar produtos não acrescenta
        consultas.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("catalog:models_shop"))
        return len(ctx.captured_queries)

    def test_more_products_do_not_add_queries(self):
        category = make_category(slug="modelos", name="Modelos")
        for indice in range(3):
            make_product(sku=f"A{indice}", name=f"A {indice}", category=category)
        self.config(product_placeholder=imagem("padrao.png"))
        poucos = self.consultas_da_loja()

        for indice in range(9):
            make_product(sku=f"B{indice}", name=f"B {indice}", category=category)

        self.assertEqual(self.consultas_da_loja(), poucos)
