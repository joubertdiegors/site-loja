"""Organização da tela do produto no Admin.

Duas exigências, e as duas são sobre não fazer o administrador rolar:

* **toda** seção recolhe e expande;
* a ordem é IDENTIFICAÇÃO → CLASSIFICAÇÃO → PERSONALIZAÇÃO → CONTEÚDO →
  VARIANTES → MÍDIA → AUDITORIA, com AUDITORIA por último.

A ordem mora em `ProductAdmin.SECTION_ORDER`, e `_page_layout` monta a partir
dela a lista única que o `change_form.html` do catálogo percorre. Hoje ela
coincide com a ordem natural do Django (todos os fieldsets, depois todos os
inlines); na etapa 13 não coincidia, e pode voltar a não coincidir. Estes
testes seguram a ordem pedida em vez de a ordem que o Django der de brinde.

CONFIGURAÇÕES deixou de existir: "produto em destaque" e "ordem no destaque"
passaram para CLASSIFICAÇÃO.

O recolher usa `<details>`/`<summary>` do próprio HTML — nada de JavaScript
para abrir e fechar. Teclado, leitor de tela e "buscar na página" do navegador
já funcionam de graça.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.catalog.models import ProductStatus
from apps.core.testing import LanguageResetMixin, make_category, make_product

#: Na ordem em que devem aparecer, de cima para baixo.
ORDEM = (
    "IDENTIFICAÇÃO",
    "CLASSIFICAÇÃO",
    "PERSONALIZAÇÃO",
    "CONTEÚDO",
    "VARIANTES",
    "MÍDIA",
    "AUDITORIA",
)


class AdminSectionsBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(self.staff)

        self.category = make_category(slug="animais", name="Animais")
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category
        )

    def change_url(self, product=None):
        return f"/admin/catalog/product/{(product or self.product).pk}/change/"

    def form_html(self, url=None):
        """Só o formulário do produto.

        Pelo `id`, e não pelo primeiro `<form>` da página: o cabeçalho do Admin
        traz um formulário de logout antes deste, e o menu lateral tem `<h2>`.
        """
        html = self.client.get(url or self.change_url()).content.decode()
        self.assertIn('id="product_form"', html)
        return html.split('id="product_form"', 1)[1].split("</form>", 1)[0]

    def headings(self, url=None):
        titulos = re.findall(r"<h2[^>]*>(.*?)</h2>", self.form_html(url), re.S)
        return [re.sub(r"<[^>]+>", "", t).strip() for t in titulos]

    def section_titles(self, url=None):
        """Os títulos das oito seções, sem o cabeçalho do objeto."""
        encontrados = []
        for titulo in self.headings(url):
            for nome in ORDEM:
                if titulo.startswith(nome):
                    encontrados.append(nome)
                    break
        return encontrados


class SectionOrderTests(AdminSectionsBase):
    def test_every_section_is_on_the_page(self):
        titulos = self.section_titles()

        for nome in ORDEM:
            with self.subTest(secao=nome):
                self.assertIn(nome, titulos)

    def test_the_sections_come_in_the_requested_order(self):
        self.assertEqual(self.section_titles(), list(ORDEM))

    def test_content_comes_right_after_personalization(self):
        """A costura entre o último fieldset e o primeiro inline."""
        titulos = self.section_titles()

        self.assertEqual(titulos[titulos.index("PERSONALIZAÇÃO") + 1], "CONTEÚDO")

    def test_variants_come_before_media(self):
        """VARIANTES antes de MÍDIA: é o que se vende antes do que se mostra."""
        titulos = self.section_titles()

        self.assertLess(titulos.index("VARIANTES"), titulos.index("MÍDIA"))

    def test_there_is_no_settings_section_anymore(self):
        """CONFIGURAÇÕES sumiu — e não deixou uma seção vazia no lugar."""
        self.assertNotIn("CONFIGURAÇÕES", self.headings())

    def test_featured_moved_to_classification(self):
        """Os dois campos do destaque foram juntos.

        `is_featured` sem `featured_order` deixaria a ordem da prateleira da
        home sem tela para editar — e é ela que ordena o destaque.
        """
        corpo = self.form_html()
        classificacao = corpo.split("CLASSIFICAÇÃO", 1)[1].split("PERSONALIZAÇÃO", 1)[0]

        self.assertIn('name="is_featured"', classificacao)
        self.assertIn('name="featured_order"', classificacao)

    def test_the_featured_fields_are_still_editable(self):
        """Escondê-los do formulário os tornaria ineditáveis sem aviso."""
        from apps.catalog.admin import ProductAdmin
        from django.contrib.admin.helpers import flatten_fieldsets

        campos = flatten_fieldsets(ProductAdmin.fieldsets)

        self.assertIn("is_featured", campos)
        self.assertIn("featured_order", campos)

    def test_audit_is_the_last_one(self):
        self.assertEqual(self.section_titles()[-1], "AUDITORIA")

    def test_no_section_is_drawn_twice(self):
        """Repetir um inline daria dois campos com o mesmo `name` no formulário."""
        titulos = self.section_titles()

        self.assertEqual(len(titulos), len(set(titulos)))

    def test_the_add_page_has_every_section_but_the_audit(self):
        """Produto que ainda não foi criado não tem histórico a mostrar."""
        titulos = self.section_titles("/admin/catalog/product/add/")

        self.assertEqual(titulos, [nome for nome in ORDEM if nome != "AUDITORIA"])

    def test_a_section_missing_from_the_order_would_still_be_drawn(self):
        """A lista é a ordem, não a permissão de existir."""
        from apps.catalog import admin as catalog_admin

        original = catalog_admin.SECTION_ORDER
        catalog_admin.SECTION_ORDER = ("IDENTIFICAÇÃO",)
        try:
            titulos = self.section_titles()
        finally:
            catalog_admin.SECTION_ORDER = original

        self.assertEqual(titulos[0], "IDENTIFICAÇÃO")
        for nome in ORDEM:
            with self.subTest(secao=nome):
                self.assertIn(nome, titulos)


class SectionCollapseTests(AdminSectionsBase):
    def test_every_section_collapses(self):
        corpo = self.form_html()

        self.assertEqual(corpo.count("<details"), len(ORDEM))
        self.assertEqual(corpo.count("<summary"), len(ORDEM))

    def test_the_collapsing_is_plain_html(self):
        """`<details>` do próprio HTML: teclado e leitor de tela de graça."""
        corpo = self.form_html()

        self.assertNotIn("collapse-toggle", corpo)
        self.assertIn("<details", corpo)

    def test_the_working_sections_start_open(self):
        """Quem abriu o produto quer editá-lo, não caçar onde clicar."""
        corpo = self.form_html()
        abertos = re.findall(r"<details(\s+open)?>", corpo)

        # Sete abertas; só a AUDITORIA, que é histórico, começa fechada.
        self.assertEqual(sum(1 for aberto in abertos if aberto), len(ORDEM) - 1)

    def test_the_audit_starts_closed(self):
        corpo = self.form_html()
        # Do `aria-labelledby` do fieldset até o `<h2>`: é aí que fica o
        # `<details>` — sem o `open` que as outras sete têm.
        auditoria = corpo.split("auditoria-heading", 1)[1].split("</h2>", 1)[0]

        self.assertIn("<details>", auditoria)
        self.assertNotIn("<details open>", auditoria)

    def test_a_section_with_an_error_opens_by_itself(self):
        """O administrador não pode ter que abrir oito seções atrás do erro."""
        resposta = self.client.post(
            self.change_url(),
            {
                "sku": "",  # IDENTIFICAÇÃO
                "status": ProductStatus.DRAFT.value,
                "slug": self.product.slug,
                "category": self.category.pk,
                "currency": "EUR",
                "personalization_type": "none",
                "personalization_text_limit": 0,
                "featured_order": 0,
                "translations-TOTAL_FORMS": "1",
                "translations-INITIAL_FORMS": "1",
                "translations-MIN_NUM_FORMS": "1",
                "translations-MAX_NUM_FORMS": "1000",
                "translations-0-id": self.product.translations.get(language="pt").pk,
                "translations-0-master": self.product.pk,
                "translations-0-language": "pt",
                "translations-0-name": "Gato Pompom",
                "translations-0-short_description": "",
                "translations-0-description": "",
                "translations-0-extra_information": "",
                "media-TOTAL_FORMS": "0",
                "media-INITIAL_FORMS": "0",
                "media-MIN_NUM_FORMS": "0",
                "media-MAX_NUM_FORMS": "1000",
                "variants-TOTAL_FORMS": "0",
                "variants-INITIAL_FORMS": "0",
                "variants-MIN_NUM_FORMS": "0",
                "variants-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.content.decode().split("<form", 1)[1]
        identificacao = corpo.split("IDENTIFICAÇÃO", 1)[0][-400:]

        self.assertNotIn("<details>", identificacao)


class SectionMarkupTests(AdminSectionsBase):
    def test_each_section_is_labelled_for_screen_readers(self):
        corpo = self.form_html()

        self.assertGreaterEqual(corpo.count("aria-labelledby"), len(ORDEM))

    def test_the_fieldset_override_only_adds_the_open_state(self):
        """A cópia do template do Django não pode ter perdido nada pelo caminho."""
        with open("templates/admin/includes/fieldset.html", encoding="utf-8") as arquivo:
            nosso = arquivo.read()

        # O que a cópia acrescenta.
        self.assertIn("start-open", nosso)
        self.assertIn("<details", nosso)
        # O que ela não podia perder: os erros, e o texto de ajuda.
        self.assertIn("line.errors", nosso)
        self.assertIn("field.errors", nosso)
        self.assertIn("help_text", nosso)
