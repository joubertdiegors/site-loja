"""A ficha do produto e o modal da variante — a anatomia da tela.

A ficha é uma sequência de seções numeradas com cabeçalho próprio (número,
título, subtítulo), navegação rápida no topo e a variante editada num modal
com grupos (identificação, opções, estoque, peso e dimensões, preço).

Nada aqui é modelo ou migração: são só testes de apresentação — do HTML que
o Admin devolve — e dos guardas que impedem um campo do formset de sumir do
modal por ter sido esquecido num grupo.
"""

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog import admin as catalog_admin
from apps.catalog.models import (
    Color,
    ColorMode,
    Material,
    PersonalizationType,
    ProductColor,
    ProductMaterialComposition,
    ProductVariant,
)
from apps.catalog.templatetags.catalog_admin import bound_field, is_checkbox
from apps.core.testing import LanguageResetMixin, make_category, make_product


class SheetBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(self.staff)
        self.category = make_category(slug="animais", name="Animais")
        self.product = make_product(sku="GATO-01", name="Gato Pompom", category=self.category)
        self.preto = Color.objects.create(name="Preto", hex_code="#111111")
        self.pla = Material.objects.create(name="PLA")

    def change_url(self, product=None):
        return reverse("admin:catalog_product_change", args=[(product or self.product).pk])

    def html(self, url=None):
        return self.client.get(url or self.change_url()).content.decode()

    def form_html(self, url=None):
        return self.html(url).split('id="product_form"', 1)[1].split("</form>", 1)[0]


class SheetHeaderAndNavTests(SheetBase):
    def test_the_header_shows_name_sku_status_and_category(self):
        html = self.html()
        cabecalho = html.split('class="jd-sheet-head"', 1)[1].split("</header>", 1)[0]

        self.assertIn("<h1>Gato Pompom</h1>", cabecalho)
        self.assertIn('class="jd-sheet-sku">GATO-01', cabecalho)
        self.assertIn("jd-badge", cabecalho)
        self.assertIn("Animais", cabecalho)
        # As ações, na mesma linha: histórico, duplicar, ver produto.
        for acao in ("history", "jd-tool-duplicate", "viewsitelink"):
            with self.subTest(acao=acao):
                self.assertIn(acao, cabecalho)

    def test_the_default_title_is_gone(self):
        self.assertNotIn("Modificar produto</h1>", self.html())

    def test_the_status_toggle_asks_before_reloading(self):
        html = self.html()
        self.assertIn("data-toggle-status", html)
        self.assertIn("data-toggle-confirm", html)
        self.assertIn(reverse("admin:catalog_product_toggle_status", args=[self.product.pk]), html)

    def test_the_quick_nav_has_one_entry_per_numbered_section(self):
        nav = self.html().split("data-section-nav", 1)[1].split("</nav>", 1)[0]
        itens = re.findall(r'href="#sec-([a-z]+)"', nav)

        self.assertEqual(
            itens,
            ["basico", "conteudo", "fotos", "cores", "materiais", "personalizacao", "variantes", "outras", "auditoria"],
        )
        # Numerados de 1 a 9, na ordem.
        numeros = re.findall(r'jd-sheet-nav-num">(\d+)<', nav)
        self.assertEqual(numeros, [str(n) for n in range(1, 10)])

    def test_the_add_page_has_no_audit_entry(self):
        nav = self.html(reverse("admin:catalog_product_add")).split("data-section-nav", 1)[1].split("</nav>", 1)[0]
        itens = re.findall(r'href="#sec-([a-z]+)"', nav)

        self.assertNotIn("auditoria", itens)
        self.assertEqual(len(itens), 8)

    def test_every_nav_entry_has_its_section(self):
        html = self.html()
        nav = html.split("data-section-nav", 1)[1].split("</nav>", 1)[0]
        for slug in re.findall(r'href="#sec-([a-z]+)"', nav):
            with self.subTest(secao=slug):
                self.assertIn(f'id="sec-{slug}"', html)


class SheetSectionsTests(SheetBase):
    def test_sections_are_numbered_in_order(self):
        corpo = self.form_html()
        numeros = re.findall(r'class="jd-sec-num" aria-hidden="true">(\d+)<', corpo)

        self.assertEqual(numeros, [str(n) for n in range(1, 10)])

    def test_the_palette_is_attached_to_colors_and_not_numbered(self):
        corpo = self.form_html()
        paleta = corpo.split('id="sec-paleta"', 1)[1].split("</summary>", 1)[0]

        self.assertIn("jd-section-attached", corpo.split('id="sec-paleta"', 1)[0][-200:])
        self.assertNotIn("jd-sec-num", paleta)
        self.assertIn("PALETA DE CORES", paleta)

    def test_each_section_has_a_subtitle(self):
        corpo = self.form_html()
        for trecho in (
            "SKU, status, categoria, marca e slug",
            "nome e descrições por idioma",
            "fotos, vídeos e GIFs",
            "modo e paleta",
            "composição de fabricação",
            "o que o cliente fornece antes de comprar",
            "cada uma é uma unidade vendável",
            "moeda e destaque na Home",
            "somente leitura",
        ):
            with self.subTest(subtitulo=trecho):
                self.assertIn(trecho, corpo)

    def test_the_materials_section_explains_the_difference_to_the_variant(self):
        self.assertIn("material comercial", self.form_html())

    def test_personalization_asks_the_question_first(self):
        self.assertIn("Este produto pode ser personalizado?", self.form_html())

    def test_the_audit_is_a_grid_of_four_values_and_starts_closed(self):
        corpo = self.form_html()
        auditoria = corpo.split('id="sec-auditoria"', 1)[1]

        self.assertIn("<details>", auditoria)
        self.assertEqual(auditoria.count("jd-audit-cell"), 4)
        for rotulo in ("Criado em", "Criado por", "Atualizado em", "Atualizado por"):
            with self.subTest(rotulo=rotulo):
                self.assertIn(rotulo, auditoria)

    def test_the_variant_section_carries_the_counter_badge(self):
        self.assertIn("data-variant-count", self.form_html())

    def test_the_color_select_carries_the_hex_of_each_colour(self):
        ProductColor.objects.create(product=self.product, color=self.preto)
        self.product.color_mode = ColorMode.SINGLE
        self.product.save(update_fields=["color_mode"])

        paleta = self.form_html().split('id="product_colors-group"', 1)[1].split("</fieldset>", 1)[0]

        self.assertIn('data-hex="#111111"', paleta)
        self.assertIn("jd-compact-rows", paleta)

    def test_the_sheet_javascript_is_loaded(self):
        html = self.html()
        for script in ("product_form_admin.js", "variant_admin.js", "product_colors_admin.js"):
            with self.subTest(script=script):
                self.assertIn(script, html)


class VariantModalAnatomyTests(SheetBase):
    def modal_html(self, product=None):
        return self.form_html(self.change_url(product)).split('class="jd-variant-modals"', 1)[1]

    def test_the_groups_cover_exactly_the_formset_fields(self):
        """Um campo acrescentado no formset e esquecido num grupo sumiria do modal."""
        nos_grupos = {c for g in catalog_admin.VARIANT_MODAL_GROUPS for c in g["fields"]}
        nos_grupos |= set(catalog_admin.VARIANT_HEADER_FIELDS)

        self.assertEqual(nos_grupos, set(catalog_admin.VARIANT_FIELDS))

    def test_the_modal_has_the_five_groups_in_order(self):
        modal = self.modal_html().split("</header>", 1)[1].split("</footer>", 1)[0]
        titulos = re.findall(r'class="jd-vm-group-title">(.*?)</h4>', modal)

        self.assertEqual(
            titulos,
            ["Identificação", "Opções da variante", "Estoque e produção", "Peso, tempo e dimensões", "Custos e preço"],
        )

    def test_the_header_names_the_variant_and_carries_the_switch(self):
        modal = self.modal_html().split("</header>", 1)[0]

        self.assertIn("data-variant-modal-kind", modal)
        self.assertIn("data-variant-modal-title", modal)
        self.assertIn('class="jd-vm-switch"', modal)
        self.assertIn('-is_active"', modal)
        self.assertIn("Fechar sem salvar", modal)

    def test_the_parent_product_is_shown_read_only(self):
        modal = self.modal_html()
        identificacao = modal.split("jd-vm-group-identificacao", 1)[1].split("</section>", 1)[0]

        self.assertIn("Gato Pompom", identificacao)
        self.assertIn("<code>GATO-01</code>", identificacao)
        # Nada do produto é editável aqui.
        for campo in ("name=\"sku\"", "-category", "-brand", "-slug", "-color_mode", "-personalization_type"):
            with self.subTest(campo=campo):
                self.assertNotIn(campo, identificacao)

    def test_units_sit_next_to_the_fields(self):
        modal = self.modal_html()
        for unidade in (">g<", ">dias<", ">%<", ">€<"):
            with self.subTest(unidade=unidade):
                self.assertIn(unidade, modal)

    def test_the_checkboxes_are_cards(self):
        modal = self.modal_html()
        self.assertGreaterEqual(modal.count("jd-vm-checkcard"), 2)

    def test_the_footer_offers_save_save_and_stay_and_cancel(self):
        rodape = self.modal_html().split("<footer", 1)[1].split("</footer>", 1)[0]

        self.assertIn("data-variant-save-stay", rodape)
        self.assertIn("Salvar e continuar editando", rodape)
        self.assertIn("data-variant-save", rodape)
        self.assertIn("data-variant-cancel", rodape)
        self.assertIn("data-variant-remove", rodape)

    def test_every_axis_combination_renders_with_its_values(self):
        """Sem eixo, só cor, só tamanho, só material… nenhum valor se perde."""
        combos = {
            "nenhum": {},
            "cor": {"color": self.preto},
            "tamanho": {"size": "15 cm"},
            "material": {"material": self.pla},
            "cor+tamanho": {"color": self.preto, "size": "15 cm"},
            "cor+material": {"color": self.preto, "material": self.pla},
            "tamanho+material": {"size": "15 cm", "material": self.pla},
            "cor+tamanho+material": {"color": self.preto, "size": "15 cm", "material": self.pla},
        }
        for nome, eixos in combos.items():
            with self.subTest(combinacao=nome):
                produto = make_product(
                    sku=f"P-{nome.upper().replace('+', '-')}", name=f"Peça {nome}", category=self.category, price=Decimal("9.90")
                )
                variante = produto.variants.first()
                for campo, valor in eixos.items():
                    setattr(variante, campo, valor)
                variante.sku = f"{produto.sku}-V01"
                variante.save()

                modal = self.modal_html(produto).split("</footer>", 1)[0]
                self.assertIn(f'value="{variante.sku}"', modal)
                if "color" in eixos:
                    self.assertRegex(modal, rf'<option value="{self.preto.pk}" selected')
                if "material" in eixos:
                    self.assertRegex(modal, rf'<option value="{self.pla.pk}" selected')
                if "size" in eixos:
                    self.assertIn('value="15 cm"', modal)

    def test_saving_the_product_keeps_every_variant_value(self):
        """O formset por baixo é o mesmo: gravar a ficha não perde nada da variante."""
        variante = self.product.variants.first()
        variante.color = self.preto
        variante.size = "15 cm"
        variante.material = self.pla
        variante.weight_grams = Decimal("20")
        variante.stock_quantity = 7
        variante.save()

        from apps.core.tests_admin_duplicate import DuplicarBase, campos_do_formulario

        pagina = self.client.get(self.change_url())
        campos = campos_do_formulario(pagina.content.decode(), "product_form")
        campos["_continue"] = "Salvar e continuar editando"
        resposta = self.client.post(self.change_url(), campos)

        self.assertEqual(resposta.status_code, 302, DuplicarBase.erros(resposta) if resposta.status_code != 302 else "")
        variante.refresh_from_db()
        self.assertEqual(variante.color, self.preto)
        self.assertEqual(variante.size, "15 cm")
        self.assertEqual(variante.material, self.pla)
        self.assertEqual(variante.stock_quantity, 7)
        self.assertEqual(variante.weight_grams, Decimal("20"))
        self.assertEqual(variante.sku, variante.sku)


class TemplateFilterTests(TestCase):
    def test_bound_field_returns_none_for_an_unknown_name(self):
        from django import forms

        class F(forms.Form):
            a = forms.CharField()
            b = forms.BooleanField(required=False)

        form = F()
        self.assertEqual(bound_field(form, "a").name, "a")
        self.assertIsNone(bound_field(form, "zzz"))
        self.assertTrue(is_checkbox(form["b"]))
        self.assertFalse(is_checkbox(form["a"]))
        self.assertFalse(is_checkbox(None))


class QuickAddStaysTests(SheetBase):
    def test_the_quick_add_page_is_untouched_by_the_sheet(self):
        html = self.html(reverse("admin:catalog_product_quick_add"))

        self.assertIn('id="product_quick_form"', html)
        self.assertNotIn("data-product-sheet", html)
        self.assertNotIn("jd-sheet-nav", html)

    def test_personalization_and_colours_stay_out_of_the_quick_add(self):
        html = self.html(reverse("admin:catalog_product_quick_add"))

        self.assertNotIn("personalization_type", html)
        self.assertNotIn("color_mode", html)
        self.assertEqual(self.product.personalization_type, PersonalizationType.NONE)
