"""Etapa 3C — as opções adicionais no Admin.

A seção OPÇÕES ADICIONAIS da ficha (cards, modais, endpoints), os `<select>`
dinâmicos do modal da variante, a coluna «Opções» da tabela, a exclusão com
RESTRICT respondida com mensagem, e as permissões dos endpoints novos.

Nada de model ou migration: é a interface sobre a 3B.
"""

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.catalog.models import (
    Color,
    Material,
    PricingMode,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductVariant,
    ProductVariantOptionValue,
)
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_variant
from apps.core.tests_admin_duplicate import campos_do_formulario

User = get_user_model()


class Base(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser(username="ana", email="ana@jdprint.test", password="senha-bem-comprida")
        self.client.force_login(self.staff)
        self.categoria = make_category(slug="religioso", name="Religioso")
        self.preto = Color.objects.create(name="Preto", hex_code="#111111")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        self.produto = make_product(
            sku="REL-LEAO-001", name="Leão de Judá", category=self.categoria, price=Decimal("29.90"),
            color=self.preto, material=self.pla, size="25 cm", stock_quantity=5, weight_grams=Decimal("100"),
        )
        self.variante = self.produto.variants.get()
        self.outro = make_product(sku="OUTRO-001", name="Outro", category=self.categoria, price=Decimal("5"))

    # -- URLs ---------------------------------------------------------------
    def change_url(self, produto=None):
        return reverse("admin:catalog_product_change", args=[(produto or self.produto).pk])

    def option_save_url(self, produto=None):
        return reverse("admin:catalog_product_option_save", args=[(produto or self.produto).pk])

    def option_delete_url(self, opcao, produto=None):
        return reverse("admin:catalog_product_option_delete", args=[(produto or self.produto).pk, opcao.pk])

    def value_save_url(self, opcao, produto=None):
        return reverse("admin:catalog_product_option_value_save", args=[(produto or self.produto).pk, opcao.pk])

    def value_delete_url(self, opcao, valor, produto=None):
        return reverse("admin:catalog_product_option_value_delete", args=[(produto or self.produto).pk, opcao.pk, valor.pk])

    # -- atalhos ------------------------------------------------------------
    def opcao(self, nome, *valores, produto=None, sort_order=0, **traducoes):
        o = ProductOption.objects.create(product=produto or self.produto, name=nome, sort_order=sort_order)
        for idioma, texto in traducoes.items():
            ProductOptionTranslation.objects.create(master=o, language=idioma, name=texto)
        for i, v in enumerate(valores):
            ProductOptionValue.objects.create(option=o, name=v, sort_order=i)
        return o

    @staticmethod
    def valor(opcao, nome):
        return opcao.values.get(name=nome)

    def post_json(self, url, dados=None):
        resposta = self.client.post(url, dados or {})
        return resposta.status_code, resposta.json()

    def variant_payload(self, **extra):
        dados = {
            "sku": self.variante.sku, "sort_order": "0", "is_active": "on",
            "color": str(self.preto.pk), "size": "25 cm", "material": str(self.pla.pk),
            "filament_cost": "1.00", "energy_cost": "0.00", "pricing_mode": PricingMode.PRICE,
            "sale_price": "29.90", "profit_margin": "", "stock_quantity": "5", "allow_backorder": "",
            "made_to_order": "", "production_lead_time_days": "", "weight_grams": "100", "print_time": "",
            "width": "", "height": "", "depth": "", "dimension_unit": "mm", "variant_id": str(self.variante.pk),
        }
        dados.update(extra)
        return {k: v for k, v in dados.items() if v != ""}


# ---------------------------------------------------------------------------
# A seção da ficha
# ---------------------------------------------------------------------------


class SectionTests(Base):
    def form_html(self, produto=None):
        html = self.client.get(self.change_url(produto)).content.decode()
        return html.split('id="product_form"', 1)[1].split("</form>", 1)[0]

    def section(self, produto=None):
        return self.form_html(produto).split('id="sec-opcoes"', 1)[1].split('id="sec-variantes"', 1)[0]

    def test_the_section_sits_right_before_the_variants(self):
        corpo = self.form_html()
        self.assertLess(corpo.index('id="sec-opcoes"'), corpo.index('id="sec-variantes"'))
        self.assertLess(corpo.index('id="sec-personalizacao"'), corpo.index('id="sec-opcoes"'))
        self.assertIn("OPÇÕES ADICIONAIS", self.section())
        self.assertIn("7 · Opções", self.client.get(self.change_url()).content.decode().replace('</span>', ''))

    def test_a_product_without_options_says_so_and_offers_the_button(self):
        secao = self.section()
        self.assertIn("Nenhuma opção adicional", secao)
        self.assertIn("data-option-add", secao)
        self.assertIn("data-option-modal", secao)
        self.assertIn("data-value-modal", secao)

    def test_the_cards_show_options_values_order_and_translations(self):
        self.opcao("Acabamento", "Fosco", "Brilhante", sort_order=2, fr="Finition")
        self.opcao("Instalação", "Mesa", "Parede", sort_order=1)
        secao = self.section()
        cards = re.findall(r'data-option-name="([^"]+)"', secao)
        self.assertEqual(cards, ["Instalação", "Acabamento"])  # pela ordem, não pela criação
        self.assertEqual(re.findall(r'data-value-name="([^"]+)"', secao), ["Mesa", "Parede", "Fosco", "Brilhante"])
        self.assertIn('data-option-name-fr="Finition"', secao)
        self.assertIn('data-option-name-nl=""', secao)
        self.assertIn(">2<", secao.split("options-heading", 1)[1].split("</summary>", 1)[0])  # o selo

    def test_the_urls_of_the_endpoints_are_on_the_page(self):
        secao = self.section()
        for nome in ("data-option-save-url", "data-option-delete-url", "data-value-save-url", "data-value-delete-url"):
            with self.subTest(nome=nome):
                self.assertIn(nome, self.form_html())
        self.assertIn("product_options_admin.js", self.client.get(self.change_url()).content.decode())

    def test_the_add_page_asks_to_save_first(self):
        html = self.client.get(reverse("admin:catalog_product_add")).content.decode()
        secao = html.split('id="sec-opcoes"', 1)[1].split('id="sec-variantes"', 1)[0]
        self.assertIn("Salve o produto primeiro", secao)
        self.assertNotIn("data-option-add", secao)
        self.assertNotIn("data-option-save-url", secao)

    def test_names_are_escaped(self):
        self.opcao('<b>Ins"talação</b>', "<i>Mesa</i>")
        secao = self.section()
        self.assertNotIn("<b>Ins", secao)
        self.assertIn("&lt;b&gt;Ins&quot;talação", secao)
        self.assertIn("&lt;i&gt;Mesa", secao)


# ---------------------------------------------------------------------------
# Opções
# ---------------------------------------------------------------------------


class OptionEndpointTests(Base):
    def test_create_with_translations(self):
        status, r = self.post_json(self.option_save_url(), {"name": " Instalação ", "sort_order": "1", "name_fr": "Installation", "name_nl": "", "name_en": "Installation"})
        self.assertEqual(status, 200, r)
        self.assertTrue(r["ok"] and r["created"])
        opcao = ProductOption.objects.get(pk=r["id"])
        self.assertEqual((opcao.product, opcao.name, opcao.sort_order), (self.produto, "Instalação", 1))
        self.assertEqual(dict(opcao.translations.values_list("language", "name")), {"fr": "Installation", "en": "Installation"})
        with translation.override("nl"):
            self.assertEqual(opcao.display_name, "Instalação")  # fallback PT

    def test_edit_updates_name_order_and_translations(self):
        opcao = self.opcao("Instalação", fr="Installation", en="Installation")
        status, r = self.post_json(self.option_save_url(), {"option_id": opcao.pk, "name": "Fixação", "sort_order": "5", "name_fr": "Fixation", "name_en": ""})
        self.assertEqual(status, 200, r)
        self.assertFalse(r["created"])
        opcao.refresh_from_db()
        self.assertEqual((opcao.name, opcao.sort_order), ("Fixação", 5))
        self.assertEqual(dict(opcao.translations.values_list("language", "name")), {"fr": "Fixation"})  # EN apagada

    def test_several_options_and_ordering(self):
        for nome, ordem in (("Modelo", "3"), ("Instalação", "1"), ("Acabamento", "2")):
            self.post_json(self.option_save_url(), {"name": nome, "sort_order": ordem})
        self.assertEqual(list(self.produto.options.values_list("name", flat=True)), ["Instalação", "Acabamento", "Modelo"])

    def test_a_duplicate_name_is_refused(self):
        self.opcao("Instalação")
        status, r = self.post_json(self.option_save_url(), {"name": "Instalação", "sort_order": "0"})
        self.assertEqual(status, 400)
        self.assertIn("Já existe uma opção com este nome", r["errors"]["name"][0])
        self.assertEqual(ProductOption.objects.count(), 1)

    def test_an_empty_name_is_refused(self):
        status, r = self.post_json(self.option_save_url(), {"name": "  ", "sort_order": "0"})
        self.assertEqual(status, 400)
        self.assertIn("name", r["errors"])

    def test_delete_a_free_option(self):
        opcao = self.opcao("Instalação", "Mesa", "Parede")
        status, r = self.post_json(self.option_delete_url(opcao))
        self.assertEqual(status, 200, r)
        self.assertFalse(ProductOption.objects.filter(pk=opcao.pk).exists())
        self.assertEqual(ProductOptionValue.objects.count(), 0)

    def test_delete_an_option_in_use_is_refused_with_a_message(self):
        opcao = self.opcao("Instalação", "Parede")
        self.variante.set_option_values({opcao: self.valor(opcao, "Parede")})
        status, r = self.post_json(self.option_delete_url(opcao))
        self.assertEqual(status, 400)
        self.assertIn("está sendo usada por uma ou mais variantes", r["detail"])
        self.assertTrue(ProductOption.objects.filter(pk=opcao.pk).exists())
        self.assertEqual(ProductVariantOptionValue.objects.count(), 1)


class ValueEndpointTests(Base):
    def setUp(self):
        super().setUp()
        self.instalacao = self.opcao("Instalação")

    def test_create_edit_and_translate(self):
        status, r = self.post_json(self.value_save_url(self.instalacao), {"name": "Parede", "sort_order": "2", "name_fr": "Mur", "name_nl": "Muur", "name_en": "Wall"})
        self.assertEqual(status, 200, r)
        parede = ProductOptionValue.objects.get(pk=r["id"])
        self.assertEqual((parede.option, parede.name, parede.sort_order), (self.instalacao, "Parede", 2))
        with translation.override("fr"):
            self.assertEqual(parede.display_name, "Mur")
        status, r = self.post_json(self.value_save_url(self.instalacao), {"value_id": parede.pk, "name": "Na parede", "sort_order": "1", "name_fr": ""})
        self.assertEqual(status, 200, r)
        parede.refresh_from_db()
        parede.refresh_translations()
        self.assertEqual((parede.name, parede.sort_order), ("Na parede", 1))
        self.assertFalse(parede.translations.filter(language="fr").exists())
        with translation.override("fr"):
            self.assertEqual(parede.display_name, "Na parede")

    def test_several_values_keep_the_order(self):
        for nome, ordem in (("Parede", "2"), ("Mesa", "1"), ("Teto", "3")):
            self.post_json(self.value_save_url(self.instalacao), {"name": nome, "sort_order": ordem})
        self.assertEqual([v.name for v in self.instalacao.values.all()], ["Mesa", "Parede", "Teto"])

    def test_a_duplicate_value_is_refused(self):
        self.post_json(self.value_save_url(self.instalacao), {"name": "Mesa", "sort_order": "0"})
        status, r = self.post_json(self.value_save_url(self.instalacao), {"name": "Mesa", "sort_order": "1"})
        self.assertEqual(status, 400)
        self.assertIn("Já existe um valor com este nome", r["errors"]["name"][0])

    def test_delete_a_free_value_and_refuse_one_in_use(self):
        ProductOptionValue.objects.create(option=self.instalacao, name="Mesa")
        parede = ProductOptionValue.objects.create(option=self.instalacao, name="Parede")
        self.variante.set_option_values({self.instalacao: parede})
        status, r = self.post_json(self.value_delete_url(self.instalacao, self.valor(self.instalacao, "Mesa")))
        self.assertEqual(status, 200, r)
        status, r = self.post_json(self.value_delete_url(self.instalacao, parede))
        self.assertEqual(status, 400)
        self.assertIn("está sendo usado por uma ou mais variantes", r["detail"])
        self.assertTrue(ProductOptionValue.objects.filter(pk=parede.pk).exists())


# ---------------------------------------------------------------------------
# Permissões e IDOR
# ---------------------------------------------------------------------------


class SecurityTests(Base):
    def test_get_is_refused_everywhere(self):
        opcao = self.opcao("Instalação", "Mesa")
        for url in (self.option_save_url(), self.option_delete_url(opcao), self.value_save_url(opcao), self.value_delete_url(opcao, self.valor(opcao, "Mesa"))):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        self.client.logout()
        resposta = self.client.post(self.option_save_url(), {"name": "X"})
        self.assertIn(resposta.status_code, (302, 403))
        self.assertEqual(ProductOption.objects.count(), 0)

    def test_a_staff_user_without_change_permission_gets_403(self):
        leitor = User.objects.create_user(username="leitor", email="leitor@jdprint.test", password="senha-bem-comprida", is_staff=True)
        leitor.user_permissions.add(Permission.objects.get(codename="view_product"))
        self.client.force_login(leitor)
        opcao = self.opcao("Instalação", "Mesa")
        for url in (self.option_save_url(), self.option_delete_url(opcao), self.value_save_url(opcao), self.value_delete_url(opcao, self.valor(opcao, "Mesa"))):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"name": "X"}).status_code, 403)
        self.assertEqual(ProductOption.objects.count(), 1)
        self.assertEqual(ProductOptionValue.objects.count(), 1)

    def test_an_option_of_another_product_is_out_of_reach(self):
        alheia = self.opcao("Instalação", "Mesa", produto=self.outro)
        mesa = self.valor(alheia, "Mesa")
        # Editar, apagar, criar valor e apagar valor pela URL do produto errado: 404.
        self.assertEqual(self.client.post(self.option_save_url(), {"option_id": alheia.pk, "name": "Hack", "sort_order": "0"}).status_code, 404)
        self.assertEqual(self.client.post(self.option_delete_url(alheia)).status_code, 404)
        self.assertEqual(self.client.post(self.value_save_url(alheia), {"name": "Hack"}).status_code, 404)
        self.assertEqual(self.client.post(self.value_delete_url(alheia, mesa)).status_code, 404)
        alheia.refresh_from_db()
        self.assertEqual(alheia.name, "Instalação")
        self.assertTrue(ProductOptionValue.objects.filter(pk=mesa.pk).exists())

    def test_a_value_of_another_option_is_out_of_reach(self):
        instalacao = self.opcao("Instalação", "Mesa")
        acabamento = self.opcao("Acabamento", "Fosco")
        fosco = self.valor(acabamento, "Fosco")
        self.assertEqual(self.client.post(self.value_save_url(instalacao), {"value_id": fosco.pk, "name": "Hack"}).status_code, 404)
        self.assertEqual(self.client.post(self.value_delete_url(instalacao, fosco)).status_code, 404)
        fosco.refresh_from_db()
        self.assertEqual(fosco.name, "Fosco")

    def test_csrf_is_enforced(self):
        from django.test import Client

        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.staff)
        self.assertEqual(cliente.post(self.option_save_url(), {"name": "X"}).status_code, 403)


# ---------------------------------------------------------------------------
# A variante: os selects dinâmicos, o modal e o formset
# ---------------------------------------------------------------------------


class VariantModalTests(Base):
    def modal_html(self, produto=None):
        html = self.client.get(self.change_url(produto)).content.decode()
        return html.split('class="jd-variant-modals"', 1)[1]

    def test_without_options_the_modal_has_no_dynamic_block(self):
        modal = self.modal_html()
        self.assertNotIn("data-variant-options", modal)
        self.assertNotIn("Eixos fixos", modal)
        self.assertNotIn("opt_", modal)

    def test_with_options_the_modal_shows_one_select_per_option_with_the_choice(self):
        instalacao = self.opcao("Instalação", "Mesa", "Parede", sort_order=1)
        acabamento = self.opcao("Acabamento", "Fosco", "Brilhante", sort_order=2)
        self.variante.set_option_values({instalacao: self.valor(instalacao, "Parede")})
        completo = self.modal_html()
        modal = completo.split("</footer>", 1)[0]

        self.assertIn("Eixos fixos", modal)
        self.assertIn("Opções adicionais", modal)
        self.assertIn(f'name="variants-0-opt_{instalacao.pk}"', modal)
        self.assertIn(f'name="variants-0-opt_{acabamento.pk}"', modal)
        self.assertIn(f'data-variant-option="opt_{instalacao.pk}"', modal)
        self.assertRegex(modal, rf'<option value="{self.valor(instalacao, "Parede").pk}" selected')
        self.assertIn("Não definido", modal)
        # A ordem dos selects é a das opções.
        self.assertLess(modal.index(f"opt_{instalacao.pk}"), modal.index(f"opt_{acabamento.pk}"))
        # Os eixos fixos continuam lá, uma vez cada.
        for campo in ("-color\"", "-size\"", "-material\""):
            self.assertEqual(modal.count(f'name="variants-0{campo}'), 1)
        # E o molde do «Adicionar variante» também tem os selects.
        self.assertIn(f'name="variants-__prefix__-opt_{instalacao.pk}"', completo)

    def test_the_table_has_the_options_column(self):
        html = self.client.get(self.change_url()).content.decode()
        cabecalho = html.split('class="jd-variant-table"')[1].split("<thead>")[1].split("</thead>")[0]
        self.assertIn("Opções", cabecalho)
        self.assertIn('"options"', open("static/admin/js/variant_admin.js", encoding="utf-8").read())

    def test_saving_through_the_modal_sets_the_choices(self):
        instalacao = self.opcao("Instalação", "Mesa", "Parede")
        acabamento = self.opcao("Acabamento", "Fosco")
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        status, r = self.post_json(url, self.variant_payload(**{f"opt_{instalacao.pk}": str(self.valor(instalacao, "Parede").pk), f"opt_{acabamento.pk}": str(self.valor(acabamento, "Fosco").pk)}))
        self.assertEqual(status, 200, r)
        self.assertEqual(r["fields"][f"opt_{instalacao.pk}"], str(self.valor(instalacao, "Parede").pk))
        v = ProductVariant.objects.get(pk=self.variante.pk)
        self.assertEqual(v.option_labels, ["Parede", "Fosco"])
        # Limpar uma escolha: «Não definido».
        status, r = self.post_json(url, self.variant_payload(**{f"opt_{instalacao.pk}": "", f"opt_{acabamento.pk}": str(self.valor(acabamento, "Fosco").pk)}))
        self.assertEqual(status, 200, r)
        self.assertEqual(r["fields"][f"opt_{instalacao.pk}"], "")
        self.assertEqual(ProductVariant.objects.get(pk=self.variante.pk).option_labels, ["Fosco"])

    def test_the_eight_axis_combinations_accept_options(self):
        instalacao = self.opcao("Instalação", "Mesa", "Parede")
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        casos = {
            "nenhum": {}, "cor": {"color": self.branco.pk}, "tamanho": {"size": "10 cm"}, "material": {"material": self.pla.pk},
            "cor+tamanho": {"color": self.branco.pk, "size": "10 cm"}, "cor+material": {"color": self.branco.pk, "material": self.pla.pk},
            "tamanho+material": {"size": "10 cm", "material": self.pla.pk},
            "cor+tamanho+material": {"color": self.branco.pk, "size": "10 cm", "material": self.pla.pk},
        }
        for numero, (nome, eixos) in enumerate(casos.items()):
            with self.subTest(combinacao=nome):
                dados = self.variant_payload(sku=f"COMB-{numero}", variant_id="", color="", size="", material="")
                dados.pop("variant_id", None)
                dados.update({k: str(v) for k, v in eixos.items()})
                dados[f"opt_{instalacao.pk}"] = str(self.valor(instalacao, "Parede").pk)
                status, r = self.post_json(url, dados)
                self.assertEqual(status, 200, r)
                v = ProductVariant.objects.get(sku=f"COMB-{numero}")
                self.assertEqual(v.option_labels, ["Parede"])
                self.assertEqual((v.color_id, v.size, v.material_id), (eixos.get("color"), eixos.get("size", ""), eixos.get("material")))

    def test_a_duplicate_combination_with_the_same_choice_is_refused(self):
        instalacao = self.opcao("Instalação", "Mesa", "Parede")
        self.variante.set_option_values({instalacao: self.valor(instalacao, "Parede")})
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        dados = self.variant_payload(sku="REL-LEAO-001-V02", **{f"opt_{instalacao.pk}": str(self.valor(instalacao, "Parede").pk)})
        dados.pop("variant_id")
        status, r = self.post_json(url, dados)
        self.assertEqual(status, 400)
        self.assertIn("Já existe uma variante com esta combinação", str(r["errors"]))
        self.assertFalse(ProductVariant.objects.filter(sku="REL-LEAO-001-V02").exists())
        # Com a outra escolha, passa: é outra variante.
        dados[f"opt_{instalacao.pk}"] = str(self.valor(instalacao, "Mesa").pk)
        status, r = self.post_json(url, dados)
        self.assertEqual(status, 200, r)
        self.assertEqual(ProductVariant.objects.get(sku="REL-LEAO-001-V02").option_labels, ["Mesa"])

    def test_a_value_of_another_option_or_product_is_refused(self):
        instalacao = self.opcao("Instalação", "Mesa")
        acabamento = self.opcao("Acabamento", "Fosco")
        alheia = self.opcao("Instalação", "Parede", produto=self.outro)
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        status, r = self.post_json(url, self.variant_payload(**{f"opt_{instalacao.pk}": str(self.valor(acabamento, "Fosco").pk)}))
        self.assertEqual(status, 400)
        self.assertIn(f"opt_{instalacao.pk}", r["errors"])
        # Uma opção de outro produto não tem campo: o valor enviado é ignorado, nada é gravado.
        status, r = self.post_json(url, self.variant_payload(**{f"opt_{alheia.pk}": str(self.valor(alheia, "Parede").pk)}))
        self.assertEqual(status, 200, r)
        self.assertEqual(ProductVariantOptionValue.objects.count(), 0)

    def test_saving_the_whole_product_form_keeps_and_sets_the_choices(self):
        """O caminho do formset (o «Salvar» do produto) grava as escolhas também."""
        instalacao = self.opcao("Instalação", "Mesa", "Parede")
        html = self.client.get(self.change_url()).content.decode()
        campos = campos_do_formulario(html, "product_form")
        self.assertEqual(campos[f"variants-0-opt_{instalacao.pk}"], "")
        campos[f"variants-0-opt_{instalacao.pk}"] = str(self.valor(instalacao, "Mesa").pk)
        campos["_continue"] = "Salvar"
        resposta = self.client.post(self.change_url(), campos)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(ProductVariant.objects.get(pk=self.variante.pk).option_labels, ["Mesa"])
        # Salvar de novo sem mexer mantém a escolha (o formulário chega preenchido).
        html = self.client.get(self.change_url()).content.decode()
        campos = campos_do_formulario(html, "product_form")
        self.assertEqual(campos[f"variants-0-opt_{instalacao.pk}"], str(self.valor(instalacao, "Mesa").pk))
        campos["_continue"] = "Salvar"
        self.assertEqual(self.client.post(self.change_url(), campos).status_code, 302)
        self.assertEqual(ProductVariant.objects.get(pk=self.variante.pk).option_labels, ["Mesa"])

    def test_the_standalone_variant_page_does_not_touch_the_choices(self):
        instalacao = self.opcao("Instalação", "Mesa")
        self.variante.set_option_values({instalacao: self.valor(instalacao, "Mesa")})
        url = reverse("admin:catalog_productvariant_change", args=[self.variante.pk])
        html = self.client.get(url).content.decode()
        self.assertNotIn("opt_", html.split('id="productvariant_form"', 1)[1])
        campos = campos_do_formulario(html, "productvariant_form")
        campos["_continue"] = "Salvar"
        self.assertEqual(self.client.post(url, campos).status_code, 302)
        self.assertEqual(ProductVariant.objects.get(pk=self.variante.pk).option_labels, ["Mesa"])

    def test_the_sheet_does_not_cost_a_query_per_variant_for_the_options(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        instalacao = self.opcao("Instalação", "Mesa", "Parede")
        for n in range(4):
            v = make_variant(self.produto, sku=f"MAIS-{n}", price=Decimal("1"), stock=1, size=f"{n} cm")
            v.set_option_values({instalacao: self.valor(instalacao, "Parede")})
        with CaptureQueriesContext(connection) as poucas:
            self.client.get(self.change_url())
        for n in range(4, 9):
            v = make_variant(self.produto, sku=f"MAIS-{n}", price=Decimal("1"), stock=1, size=f"{n} cm")
            v.set_option_values({instalacao: self.valor(instalacao, "Mesa")})
        with CaptureQueriesContext(connection) as muitas:
            self.client.get(self.change_url())
        # As linhas do inline continuam custando o que custavam (o select de
        # cor/material por linha é anterior); as opções não acrescentam por variante.
        self.assertLessEqual(len(muitas) - len(poucas), 5 * 4)


class CompatibilityTests(Base):
    def test_quick_add_and_the_rest_of_the_sheet_are_untouched(self):
        html = self.client.get(reverse("admin:catalog_product_quick_add")).content.decode()
        self.assertNotIn("opt_", html)
        self.assertNotIn("data-options-inline", html)
        ficha = self.client.get(self.change_url()).content.decode()
        for trecho in ("PALETA DE CORES", "MATERIAIS", 'name="color_mode"', "VARIANTES", "data-variant-add"):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, ficha)
        self.assertEqual(ProductVariant.objects.get(pk=self.variante.pk).sku, "REL-LEAO-001")
