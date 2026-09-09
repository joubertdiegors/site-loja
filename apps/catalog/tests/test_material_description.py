"""Etapa 4C.3 — a descrição rica do material, do Admin até a página do produto.

O material é cadastrado uma vez e lido por todos os produtos que o usam. Estes
testes fixam as três coisas que isso exige:

* **um campo por idioma, e só um.** `MaterialTranslation` ganha `description`;
  nenhum campo por assunto (limpeza, calor) e nenhum campo por idioma
  (`description_fr`). Ligar um idioma novo em «Idiomas da loja» o faz aparecer
  no Admin sem tocar em código;
* **HTML que o Admin controla.** O editor produz subtítulo, negrito, lista,
  alinhamento, tamanho e cor; a lista de permissões de `apps.core.richtext`
  recusa o resto — `<script>`, `onerror=`, `javascript:` — ao gravar e de novo
  ao desenhar;
* **referência, não cópia.** O produto aponta para o material; mudar a
  descrição do PLA muda todos os produtos que o usam, e nenhum pedido antigo.
"""

from decimal import Decimal

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation as django_translation

from apps.accounts.models import User
from apps.catalog.models import (
    Material,
    MaterialTranslation,
    Product,
    ProductMaterialComposition,
    ProductStatus,
    ProductVariant,
)
from apps.core.models import SiteLanguage
from apps.core.richtext import sanitize_rich_text
from apps.core.testing import LanguageResetMixin, make_category, make_product

RICO = (
    "<h2>Limpeza</h2>"
    "<p>Limpar com <strong>pano macio</strong> e levemente úmido.</p>"
    "<ul><li>Evitar produtos abrasivos</li></ul>"
    '<p style="text-align: center">Centralizado</p>'
    '<p><span style="color: #4a1a8c; font-size: 1.15rem">Destaque</span></p>'
)


def material(nome, **traducoes):
    """Um material com as traduções pedidas: `pt=("PLA", "<p>...</p>")`."""
    obj = Material.objects.create(name=nome)
    for idioma, dados in traducoes.items():
        texto, descricao = dados if isinstance(dados, tuple) else (dados, "")
        MaterialTranslation.objects.create(
            master=obj, language=idioma, name=texto, description=descricao
        )
    obj.refresh_translations()
    return obj


class MaterialBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="modelos", name="Modelos")

    def produto_com(self, material_obj, sku="PROD", por_variante=False, **kw):
        produto = make_product(
            sku=sku, name=sku, category=self.categoria, status=ProductStatus.ACTIVE, **kw
        )
        if por_variante:
            variante = produto.default_variant
            variante.material = material_obj
            variante.save()
        else:
            ProductMaterialComposition.objects.create(product=produto, material=material_obj)
        return Product.objects.get(pk=produto.pk)

    def pagina(self, produto, prefixo=""):
        url = f"{prefixo}/produtos/{produto.slug}/" if prefixo else produto.get_absolute_url()
        return self.client.get(url)

    def secao(self, produto, prefixo=""):
        """Só o bloco «Sobre os materiais» — a página tem outros `onclick` e
        outra linha chamada «Materiais» (a composição, na ficha técnica)."""
        html = self.pagina(produto, prefixo).content.decode()
        marca = "Sobre os materiais"
        if marca not in html:
            return ""
        corpo = html.split(marca, 1)[1]
        return corpo.split("</details>", 1)[0]


# ---------------------------------------------------------------------------
# O modelo: um campo por idioma
# ---------------------------------------------------------------------------


class TranslationModelTests(MaterialBase):
    def test_a_material_is_created_without_any_translation(self):
        pla = Material.objects.create(name="PLA")
        self.assertEqual(pla.display_name, "PLA")  # cai no nome interno
        self.assertEqual(pla.display_description, "")

    def test_the_description_lives_in_the_translation_not_in_the_material(self):
        campos = {f.name for f in MaterialTranslation._meta.get_fields()}
        self.assertIn("description", campos)
        self.assertIn("language", campos)
        # nenhum campo por idioma, em nenhum dos dois modelos
        for modelo in (Material, MaterialTranslation):
            for campo in modelo._meta.get_fields():
                self.assertNotRegex(campo.name, r"_(pt|fr|nl|en|de|es|it)$")

    def test_creating_and_editing_a_translation(self):
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"))
        traducao = pla.translations.get(language="pt")
        self.assertEqual(traducao.description, "<p>Leve</p>")

        traducao.description = "<p>Leve e versátil</p>"
        traducao.save()
        self.assertEqual(Material.objects.get(pk=pla.pk).display_description, "<p>Leve e versátil</p>")

    def test_several_languages_live_side_by_side(self):
        pla = material(
            "PLA",
            pt=("PLA", "<p>Material leve</p>"),
            fr=("PLA", "<p>Matériau léger</p>"),
            nl=("PLA", "<p>Licht materiaal</p>"),
            en=("PLA", "<p>Light material</p>"),
        )
        self.assertEqual(pla.translations.count(), 4)
        esperado = {
            "pt": "<p>Material leve</p>",
            "fr": "<p>Matériau léger</p>",
            "nl": "<p>Licht materiaal</p>",
            "en": "<p>Light material</p>",
        }
        for idioma, texto in esperado.items():
            with self.subTest(idioma=idioma), django_translation.override(idioma):
                self.assertEqual(Material.objects.get(pk=pla.pk).display_description, texto)

    def test_a_language_switched_on_later_needs_no_code_change(self):
        """Ligar um idioma novo em «Idiomas da loja» basta: nada aqui é fixo."""
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"))
        SiteLanguage.objects.update_or_create(
            code="de", defaults={"is_active": True, "sort_order": 9}
        )
        MaterialTranslation.objects.create(
            master=pla, language="de", name="PLA", description="<p>Leichtes Material</p>"
        )
        pla.refresh_translations()
        with django_translation.override("de"):
            self.assertEqual(
                Material.objects.get(pk=pla.pk).display_description, "<p>Leichtes Material</p>"
            )
        # e o Admin passa a oferecer a linha desse idioma, sem alteração de código
        from django.contrib import admin as django_admin

        from apps.catalog.admin import MaterialTranslationInline

        inline = MaterialTranslationInline(Material, django_admin.site)
        self.assertIn("de", inline._missing_languages(Material.objects.create(name="PETG")))


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


class FallbackTests(MaterialBase):
    def test_the_current_language_wins(self):
        pla = material("PLA", pt=("PLA", "<p>Português</p>"), fr=("PLA", "<p>Français</p>"))
        with django_translation.override("fr"):
            self.assertEqual(Material.objects.get(pk=pla.pk).display_description, "<p>Français</p>")

    def test_without_the_current_language_it_falls_back_to_portuguese(self):
        pla = material("PLA", pt=("PLA", "<p>Português</p>"))
        with django_translation.override("nl"):
            self.assertEqual(Material.objects.get(pk=pla.pk).display_description, "<p>Português</p>")

    def test_without_portuguese_either_nothing_is_shown(self):
        """Nem o idioma pedido nem o padrão: a seção não existe.

        Um nome sem tradução vale em qualquer idioma; um texto, não. Mostrar
        parágrafos em francês a um cliente holandês seria o idioma errado na
        tela, não um fallback.
        """
        pla = material("PLA", fr=("PLA", "<p>Français</p>"))
        with django_translation.override("nl"):
            self.assertEqual(Material.objects.get(pk=pla.pk).display_description, "")
        produto = self.produto_com(pla)
        with django_translation.override("nl"):
            self.assertEqual(Product.objects.get(pk=produto.pk).material_notes, [])

    def test_a_translation_with_a_name_but_no_description_falls_back_too(self):
        pla = material("PLA", pt=("PLA", "<p>Português</p>"), nl=("PLA", ""))
        with django_translation.override("nl"):
            fresco = Material.objects.get(pk=pla.pk)
            self.assertEqual(fresco.display_name, "PLA")
            self.assertEqual(fresco.display_description, "<p>Português</p>")


# ---------------------------------------------------------------------------
# A página do produto
# ---------------------------------------------------------------------------


class ProductPageTests(MaterialBase):
    def test_the_description_appears_in_the_accordion(self):
        pla = material("PLA", pt=("PLA", RICO))
        produto = self.produto_com(pla)
        html = self.pagina(produto).content.decode()

        self.assertIn("Sobre os materiais", html)
        self.assertIn("product-material-name", html)
        self.assertIn(">PLA<", html)
        self.assertIn("<h2>Limpeza</h2>", html)
        self.assertIn("<strong>pano macio</strong>", html)
        self.assertIn("<li>Evitar produtos abrasivos</li>", html)
        self.assertIn('style="text-align: center"', html)
        self.assertIn("color: #4a1a8c", html)

    def test_the_section_shows_content_and_never_template_comments(self):
        """`{# ... #}` só comenta uma linha; com três, o Django imprime tudo.

        Foi o que aconteceu na primeira versão desta seção — o comentário
        apareceu como texto na página, no celular. O teste fixa que o corpo da
        seção tem só o que o lojista escreveu.
        """
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"))
        produto = self.produto_com(pla)
        secao = self.secao(produto)
        self.assertIn("<p>Leve</p>", secao)
        for vazamento in ("{#", "#}", "{% comment", "endcomment", "rótulo da composição"):
            with self.subTest(vazamento=vazamento):
                self.assertNotIn(vazamento, secao)

    def test_a_material_reached_through_the_variant_counts_too(self):
        petg = material("PETG", pt=("PETG", "<p>Resistente</p>"))
        produto = self.produto_com(petg, sku="VAR", por_variante=True)
        self.assertEqual([m.pk for m in produto.material_notes], [petg.pk])
        self.assertIn("<p>Resistente</p>", self.pagina(produto).content.decode())

    def test_a_material_without_description_draws_no_section(self):
        pla = material("PLA", pt="PLA")
        produto = self.produto_com(pla)
        html = self.pagina(produto).content.decode()
        self.assertEqual(produto.material_notes, [])
        self.assertNotIn("product-material-name", html)
        self.assertNotIn("Sobre os materiais", html)
        # a linha da composição na ficha técnica continua sendo outra coisa
        self.assertIn("Materiais", html)

    def test_the_page_follows_the_language_and_the_fallback(self):
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"), fr=("PLA", "<p>Léger</p>"))
        produto = self.produto_com(pla)
        self.assertIn("<p>Léger</p>", self.pagina(produto, "/fr").content.decode())
        self.assertIn("<p>Leve</p>", self.pagina(produto, "/nl").content.decode())  # fallback

    def test_two_materials_are_two_blocks_in_composition_order(self):
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"))
        petg = material("PETG", pt=("PETG", "<p>Resistente</p>"))
        produto = make_product(sku="MIX", name="Mix", category=self.categoria, status=ProductStatus.ACTIVE)
        ProductMaterialComposition.objects.create(product=produto, material=pla, percentage=Decimal("80"), sort_order=0)
        ProductMaterialComposition.objects.create(product=produto, material=petg, percentage=Decimal("20"), sort_order=1)
        produto = Product.objects.get(pk=produto.pk)

        self.assertEqual([m.pk for m in produto.material_notes], [pla.pk, petg.pk])
        html = self.pagina(produto).content.decode()
        self.assertEqual(html.count("product-material-name"), 2)
        self.assertLess(html.index("<p>Leve</p>"), html.index("<p>Resistente</p>"))

    def test_the_same_material_serves_many_products_by_reference(self):
        pla = material("PLA", pt=("PLA", "<p>Antes</p>"))
        um = self.produto_com(pla, sku="UM")
        outro = self.produto_com(pla, sku="OUTRO")
        for produto in (um, outro):
            self.assertIn("<p>Antes</p>", self.pagina(produto).content.decode())

        # a descrição é editada uma vez, no material
        traducao = pla.translations.get(language="pt")
        traducao.description = "<p>Depois</p>"
        traducao.save()

        for produto in (um, outro):
            with self.subTest(produto=produto.sku):
                html = self.pagina(Product.objects.get(pk=produto.pk)).content.decode()
                self.assertIn("<p>Depois</p>", html)
                self.assertNotIn("<p>Antes</p>", html)
        # e nada foi copiado para dentro do produto
        for campo in (f.name for f in Product._meta.get_fields()):
            self.assertNotIn("material_description", campo)

    def test_the_section_does_not_cost_a_query_per_material(self):
        materiais = [material(f"M{n}", pt=(f"M{n}", f"<p>Texto {n}</p>")) for n in range(4)]
        produto = make_product(sku="Q", name="Q", category=self.categoria, status=ProductStatus.ACTIVE)
        ProductMaterialComposition.objects.create(product=produto, material=materiais[0])
        with CaptureQueriesContext(connection) as um:
            self.pagina(Product.objects.get(pk=produto.pk))
        for n, m in enumerate(materiais[1:], start=1):
            ProductMaterialComposition.objects.create(product=produto, material=m, sort_order=n)
        with CaptureQueriesContext(connection) as quatro:
            resposta = self.pagina(Product.objects.get(pk=produto.pk))
        self.assertEqual(resposta.content.decode().count("product-material-name"), 4)
        self.assertEqual(len(um), len(quatro))


# ---------------------------------------------------------------------------
# Segurança do conteúdo
# ---------------------------------------------------------------------------


class SanitizationTests(MaterialBase):
    PERIGOS = (
        ("<script>alert(1)</script>", "script"),
        ('<p onclick="alert(1)">x</p>', "onclick"),
        ('<img src=x onerror="alert(1)">', "onerror"),
        ('<a href="javascript:alert(1)">x</a>', "javascript:"),
        ('<a href="java\nscript:alert(1)">x</a>', "javascript quebrado"),
        ('<iframe src="//mal.example"></iframe>', "iframe"),
        ('<p style="background:url(javascript:alert(1))">x</p>', "url() no estilo"),
        ("<svg/onload=alert(1)>", "svg"),
        ('<form action="/x"><input name="p"></form>', "formulário"),
        ('<p style="position:fixed;top:0;left:0">x</p>', "posicionamento"),
    )

    def test_the_sanitizer_refuses_every_dangerous_shape(self):
        for entrada, caso in self.PERIGOS:
            with self.subTest(caso=caso):
                limpo = sanitize_rich_text(entrada)
                for proibido in ("<script", "onclick", "onerror", "onload", "javascript:", "<iframe", "<svg", "<form", "position", "url("):
                    self.assertNotIn(proibido, limpo.lower())

    def test_what_the_editor_produces_survives_intact(self):
        limpo = sanitize_rich_text(RICO)
        for pedaco in (
            "<h2>Limpeza</h2>",
            "<strong>pano macio</strong>",
            "<ul><li>Evitar produtos abrasivos</li></ul>",
            "text-align: center",
            "color: #4a1a8c",
            "font-size: 1.15rem",
        ):
            with self.subTest(pedaco=pedaco):
                self.assertIn(pedaco, limpo)

    def test_old_tags_become_semantic_ones(self):
        self.assertEqual(sanitize_rich_text("<b>a</b><i>b</i>"), "<strong>a</strong><em>b</em>")

    def test_an_empty_editor_is_stored_as_empty(self):
        for vazio in ("", "   ", "<p></p>", "<p><br></p>", "<br>"):
            with self.subTest(valor=repr(vazio)):
                self.assertEqual(sanitize_rich_text(vazio), "")

    def test_dangerous_content_never_reaches_the_product_page(self):
        """Mesmo gravado direto no banco, ele não chega à tela executável."""
        pla = material("PLA", pt=("PLA", '<p>Ok</p><script>alert(1)</script><p onclick="x">Y</p>'))
        produto = self.produto_com(pla)
        secao = self.secao(produto)
        self.assertIn("<p>Ok</p>", secao)
        self.assertNotIn("<script", secao)
        self.assertNotIn("alert(1)", secao)
        self.assertNotIn("onclick", secao)

    def test_the_admin_form_stores_it_already_clean(self):
        from apps.catalog.admin import MaterialTranslationForm

        pla = Material.objects.create(name="PLA")
        form = MaterialTranslationForm(
            {
                "master": pla.pk,
                "language": "pt",
                "name": "PLA",
                "description": '<h2>Calor</h2><script>alert(1)</script><p onmouseover="x">Evitar</p>',
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        traducao = form.save(commit=False)
        traducao.master = pla
        traducao.save()
        self.assertEqual(traducao.description, "<h2>Calor</h2><p>Evitar</p>")


# ---------------------------------------------------------------------------
# O Admin
# ---------------------------------------------------------------------------


class AdminTests(MaterialBase):
    SENHA = "senha-de-teste-77"

    def setUp(self):
        super().setUp()
        self.chefe = User.objects.create_superuser("chefe", "chefe@jdprint.test", self.SENHA)
        self.client.force_login(self.chefe)
        self.pla = material("PLA", pt=("PLA", "<p>Leve</p>"))

    def url(self, nome, *args):
        return reverse(f"admin:catalog_material_{nome}", args=args)

    def test_the_change_page_offers_the_editor_for_each_language(self):
        html = self.client.get(self.url("change", self.pla.pk)).content.decode()
        self.assertIn("data-rich-text-widget", html)
        self.assertIn("rich_text_admin.js", html)
        self.assertIn("jdprint_richtext.css", html)
        self.assertIn("TRADUÇÕES", html)
        # a linha existente vem preenchida
        self.assertIn("&lt;p&gt;Leve&lt;/p&gt;", html)

    def test_the_blank_rows_follow_the_active_store_languages(self):
        from django.contrib import admin as django_admin

        from apps.catalog.admin import MaterialTranslationInline

        inline = MaterialTranslationInline(Material, django_admin.site)
        faltando = inline._missing_languages(self.pla)
        ativos = {SiteLanguage.objects.filter(is_active=True).count()}
        self.assertNotIn("pt", faltando)  # já traduzido
        self.assertTrue(set(faltando) <= {"fr", "nl", "en"})
        self.assertEqual(inline.get_extra(None, self.pla), len(faltando))
        self.assertTrue(ativos)

    def test_saving_the_material_with_a_new_translation(self):
        dados = {
            "name": "PLA",
            "slug": "pla",
            "description": "",
            "is_active": "on",
            "translations-TOTAL_FORMS": "2",
            "translations-INITIAL_FORMS": "1",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-id": str(self.pla.translations.get(language="pt").pk),
            "translations-0-master": str(self.pla.pk),
            "translations-0-language": "pt",
            "translations-0-name": "PLA",
            "translations-0-description": "<p>Leve e versátil</p>",
            "translations-1-id": "",
            "translations-1-master": str(self.pla.pk),
            "translations-1-language": "fr",
            "translations-1-name": "PLA",
            "translations-1-description": "<h2>Nettoyage</h2><script>alert(1)</script>",
        }
        resposta = self.client.post(self.url("change", self.pla.pk), dados)
        self.assertEqual(resposta.status_code, 302, getattr(resposta, "context", None))

        self.pla.refresh_from_db()
        self.pla.refresh_translations()
        self.assertEqual(self.pla.translations.get(language="pt").description, "<p>Leve e versátil</p>")
        self.assertEqual(self.pla.translations.get(language="fr").description, "<h2>Nettoyage</h2>")

    def test_a_staff_user_without_permission_cannot_open_or_save(self):
        leitor = User.objects.create_user(
            "leitor", "leitor@jdprint.test", self.SENHA, is_staff=True
        )
        leitor.user_permissions.add(
            Permission.objects.get(codename="view_material", content_type__app_label="catalog")
        )
        self.client.force_login(leitor)
        antes = self.pla.translations.get(language="pt").description

        resposta = self.client.get(self.url("change", self.pla.pk))
        self.assertEqual(resposta.status_code, 200)  # vê, em modo leitura
        self.assertNotIn("data-rich-text-widget", resposta.content.decode())

        self.client.post(self.url("change", self.pla.pk), {"name": "PLA", "slug": "pla"})
        self.assertEqual(self.pla.translations.get(language="pt").description, antes)

        self.client.force_login(User.objects.create_user("zero", "z@x.test", self.SENHA, is_staff=True))
        self.assertEqual(self.client.get(self.url("change", self.pla.pk)).status_code, 403)

    def test_the_list_says_which_languages_already_have_a_description(self):
        html = self.client.get(self.url("changelist")).content.decode()
        self.assertIn("descrição", html.lower())
        self.assertIn(">PT<", html)


# ---------------------------------------------------------------------------
# Nada do que existia mudou
# ---------------------------------------------------------------------------


class NoRegressionTests(MaterialBase):
    def test_existing_translations_keep_working_without_a_description(self):
        pla = material("PLA", pt="PLA", fr="PLA-FR")
        self.assertEqual(pla.translations.count(), 2)
        self.assertEqual(pla.display_description, "")
        with django_translation.override("fr"):
            self.assertEqual(Material.objects.get(pk=pla.pk).display_name, "PLA-FR")

    def test_the_field_is_optional_and_defaults_to_empty(self):
        campo = MaterialTranslation._meta.get_field("description")
        self.assertTrue(campo.blank)
        traducao = MaterialTranslation.objects.create(
            master=Material.objects.create(name="Resina"), language="pt", name="Resina"
        )
        self.assertEqual(traducao.description, "")

    def test_the_variant_specs_still_show_the_material_name(self):
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"))
        produto = self.produto_com(pla, sku="SPEC", por_variante=True)
        html = self.pagina(produto).content.decode()
        self.assertIn('data-spec="material"', html)
        self.assertIn("PLA", html)

    def test_the_composition_line_is_untouched(self):
        pla = material("PLA", pt=("PLA", "<p>Leve</p>"))
        produto = make_product(sku="COMP", name="Comp", category=self.categoria, status=ProductStatus.ACTIVE)
        ProductMaterialComposition.objects.create(product=produto, material=pla, percentage=Decimal("80"))
        self.assertEqual(Product.objects.get(pk=produto.pk).materials_text, "PLA 80%")

    def test_the_order_snapshot_does_not_learn_about_the_description(self):
        """A descrição é catálogo de hoje; o pedido guarda o que foi comprado."""
        from apps.orders.models import OrderItem

        campos = {f.name for f in OrderItem._meta.get_fields()}
        self.assertIn("material_name", campos)
        self.assertNotIn("material_description", campos)
