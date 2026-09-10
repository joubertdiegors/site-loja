"""«Cores à escolha do cliente» — modelo, camada de escolhas, Admin e página.

O que estes testes guardam:

* só o modo «custom» oferece escolha, e só com paleta: os outros quatro modos
  continuam sendo o que eram;
* a escolha é resolvida por **id** contra a paleta do produto, e o adicional
  sai do banco;
* a página desenha o grupo de escolha **separado** dos eixos da variante,
  abre sem cor marcada e com o preço da variante — escolher é do cliente;
* o Admin cadastra o adicional na própria paleta.
"""

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.catalog.choices import (
    COLOR_GROUP,
    ChoiceError,
    choice_groups,
    choices_text,
    money_delta,
    price_adjustment,
    raw_from,
    resolve_choices,
)
from apps.catalog.models import (
    Color,
    ColorMode,
    ColorTranslation,
    ProductColor,
    ProductVariant,
)
from apps.core.admin_mixins import duplicable_values
from apps.core.testing import LanguageResetMixin, make_category, make_product


def cor(nome, hexa, **traducoes):
    color = Color.objects.create(name=nome, hex_code=hexa)
    for idioma, texto in traducoes.items():
        ColorTranslation.objects.create(master=color, language=idioma, name=texto)
    color.refresh_translations()
    return color


class PortaRetratoBase(LanguageResetMixin, TestCase):
    """O cenário do enunciado: quatro variantes comerciais, cinco cores, uma com adicional."""

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="decoracao", name="Decoração")
        self.branco = cor("Branco", "#FFFFFF", pt="Branco", fr="Blanc", nl="Wit", en="White")
        self.preto = cor("Preto", "#000000", pt="Preto", fr="Noir", nl="Zwart", en="Black")
        self.dourado = cor("Dourado", "#D4AF37", pt="Dourado", fr="Doré", nl="Goud", en="Gold")

        self.product = make_product(
            sku="PORTA", name="Porta-Retrato Litofânico", category=self.category,
            with_variant=False, color_mode=ColorMode.CUSTOM,
        )
        self.simples = self.variante("PORTA-V01", "Porta-Retrato", "15.00")
        self.com_foto = self.variante("PORTA-V02", "Porta-Retrato + Foto", "20.00")
        self.com_led = self.variante("PORTA-V03", "Porta-Retrato + LED", "25.00")
        self.completo = self.variante("PORTA-V04", "Porta-Retrato + Foto + LED", "30.00")

        self.row_branco = ProductColor.objects.create(product=self.product, color=self.branco, sort_order=0)
        self.row_preto = ProductColor.objects.create(product=self.product, color=self.preto, sort_order=1)
        self.row_dourado = ProductColor.objects.create(
            product=self.product, color=self.dourado, sort_order=2, price_delta=Decimal("2.00")
        )
        self.product.refresh_from_db()

    def variante(self, sku, rotulo, preco, stock=10):
        return ProductVariant.objects.create(
            product=self.product, sku=sku, size=rotulo, sale_price=Decimal(preco),
            stock_quantity=stock, weight_grams=Decimal("200"),
        )

    def produto(self):
        """Uma instância fresca, sem cache de relação."""
        return type(self.product).objects.get(pk=self.product.pk)


# ---------------------------------------------------------------------------
# 1. Modelo
# ---------------------------------------------------------------------------


class ModelTests(PortaRetratoBase):
    def test_price_delta_defaults_to_zero(self):
        self.assertEqual(self.row_branco.price_delta, Decimal("0.00"))
        self.assertEqual(self.row_dourado.price_delta, Decimal("2.00"))

    def test_custom_offers_the_palette_in_order(self):
        produto = self.produto()
        self.assertTrue(produto.offers_customer_colors)
        self.assertEqual(
            [row.color.name for row in produto.customer_color_rows], ["Branco", "Preto", "Dourado"]
        )

    def test_the_other_modes_offer_nothing_even_with_a_palette(self):
        """A paleta continua descritiva nos modos descritivos, e ignorada nos outros."""
        for modo in (ColorMode.NONE, ColorMode.SINGLE, ColorMode.MULTI, ColorMode.VARIANT):
            with self.subTest(modo=modo):
                type(self.product).objects.filter(pk=self.product.pk).update(color_mode=modo)
                produto = self.produto()
                self.assertEqual(produto.customer_color_rows, [])
                self.assertFalse(produto.offers_customer_colors)
                self.assertEqual(choice_groups(produto), [])

    def test_custom_with_an_empty_palette_offers_nothing(self):
        """Não se inventa cor: sem paleta a compra segue como antes desta etapa."""
        ProductColor.objects.filter(product=self.product).delete()
        produto = self.produto()
        self.assertEqual(produto.customer_color_rows, [])
        self.assertEqual(choice_groups(produto), [])

    def test_display_colors_and_colors_text_are_unchanged_in_custom(self):
        """O card e a ficha continuam dizendo «Cores à escolha» (etapa 2B)."""
        produto = self.produto()
        self.assertEqual(produto.display_colors, [])
        self.assertTrue(produto.has_custom_colors)
        self.assertEqual(produto.colors_text, "Cores à escolha")

    def test_duplication_copies_the_delta(self):
        self.assertEqual(duplicable_values(self.row_dourado)["price_delta"], Decimal("2.00"))


# ---------------------------------------------------------------------------
# 2. A camada de escolhas
# ---------------------------------------------------------------------------


class ChoiceGroupsTests(PortaRetratoBase):
    def test_one_group_for_the_colour(self):
        grupos = choice_groups(self.produto())
        self.assertEqual(len(grupos), 1)
        grupo = grupos[0]
        self.assertEqual(grupo.key, COLOR_GROUP)
        self.assertEqual(grupo.label, "Cor")
        self.assertTrue(grupo.required)
        self.assertEqual([o.value_label for o in grupo.options], ["Branco", "Preto", "Dourado"])
        self.assertEqual([o.value_id for o in grupo.options], [self.row_branco.pk, self.row_preto.pk, self.row_dourado.pk])

    def test_each_option_carries_hex_delta_and_text(self):
        opcoes = {o.value_label: o for o in choice_groups(self.produto())[0].options}
        self.assertEqual(opcoes["Dourado"].hex_code, "#D4AF37")
        self.assertEqual(opcoes["Dourado"].price_delta, Decimal("2.00"))
        self.assertEqual(opcoes["Dourado"].delta_display, "+ € 2,00")
        self.assertEqual(opcoes["Dourado"].text, "Cor: Dourado (+ € 2,00)")
        self.assertEqual(opcoes["Dourado"].value_text, "Dourado (+ € 2,00)")
        self.assertEqual(opcoes["Branco"].delta_display, "")
        self.assertEqual(opcoes["Branco"].text, "Cor: Branco")

    def test_money_delta(self):
        self.assertEqual(money_delta(Decimal("2.00"), "€"), "+ € 2,00")
        self.assertEqual(money_delta(Decimal("-1.50"), "€"), "− € 1,50")
        self.assertEqual(money_delta(Decimal("0"), "€"), "")
        self.assertEqual(money_delta(None, "€"), "")

    def test_labels_follow_the_customers_language(self):
        with translation.override("fr"):
            grupo = choice_groups(self.produto())[0]
            self.assertEqual(grupo.label, "Couleur")
            self.assertEqual([o.value_label for o in grupo.options], ["Blanc", "Noir", "Doré"])
            self.assertEqual(grupo.options[2].text, "Couleur: Doré (+ € 2,00)")


class ResolveChoicesTests(PortaRetratoBase):
    def test_a_valid_id_resolves_with_the_delta_from_the_database(self):
        escolhas = resolve_choices(self.produto(), {"color": str(self.row_dourado.pk)})
        self.assertEqual(len(escolhas), 1)
        self.assertEqual(escolhas[0].value_id, self.row_dourado.pk)
        self.assertEqual(escolhas[0].price_delta, Decimal("2.00"))
        self.assertEqual(price_adjustment(escolhas), Decimal("2.00"))
        self.assertEqual(choices_text(escolhas), "Cor: Dourado (+ € 2,00)")
        self.assertEqual(raw_from(escolhas), {"color": self.row_dourado.pk})

    def test_a_colour_without_delta_costs_nothing(self):
        escolhas = resolve_choices(self.produto(), {"color": self.row_branco.pk})
        self.assertEqual(price_adjustment(escolhas), Decimal("0.00"))

    def test_missing_is_refused_as_missing(self):
        with self.assertRaises(ChoiceError) as contexto:
            resolve_choices(self.produto(), {})
        self.assertEqual(contexto.exception.reason, "missing")
        self.assertEqual(contexto.exception.message, "Escolha uma cor antes de continuar.")

    def test_an_unknown_id_is_refused_as_invalid(self):
        for bruto in ("999999", "abc", "", "-1", "1.5"):
            with self.subTest(valor=bruto):
                try:
                    resolve_choices(self.produto(), {"color": bruto})
                except ChoiceError as erro:
                    self.assertIn(erro.reason, {"invalid", "missing"})
                else:
                    self.fail("aceitou um id que não é da paleta")

    def test_a_colour_id_is_refused_even_if_it_is_a_global_colour(self):
        """Só a PALETA deste produto vale — nunca a tabela de cores inteira."""
        with self.assertRaises(ChoiceError) as contexto:
            resolve_choices(self.produto(), {"color": self.dourado.pk + 10_000})
        self.assertEqual(contexto.exception.reason, "invalid")

    def test_the_row_of_another_products_palette_is_refused(self):
        outro = make_product(sku="OUTRO", name="Outro", category=self.category, with_variant=False,
                             color_mode=ColorMode.CUSTOM)
        alheia = ProductColor.objects.create(product=outro, color=self.dourado, price_delta=Decimal("9.00"))
        with self.assertRaises(ChoiceError) as contexto:
            resolve_choices(self.produto(), {"color": alheia.pk})
        self.assertEqual(contexto.exception.reason, "invalid")

    def test_keys_the_product_does_not_offer_are_ignored(self):
        escolhas = resolve_choices(self.produto(), {"color": self.row_preto.pk, "material": "7", "opt-3": "9"})
        self.assertEqual([c.key for c in escolhas], ["color"])

    def test_products_without_a_choice_ignore_a_colour_sent_anyway(self):
        """Um produto «Uma cor» não ganha escolha porque alguém mandou `color`."""
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.SINGLE)
        self.assertEqual(resolve_choices(self.produto(), {"color": self.row_branco.pk}), ())

    def test_custom_with_an_empty_palette_needs_no_choice(self):
        ProductColor.objects.filter(product=self.product).delete()
        self.assertEqual(resolve_choices(self.produto(), {}), ())
        self.assertEqual(resolve_choices(self.produto(), {"color": "5"}), ())


# ---------------------------------------------------------------------------
# 3. Admin
# ---------------------------------------------------------------------------


class AdminTests(PortaRetratoBase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(self.admin)

    def test_the_palette_has_the_delta_column(self):
        resposta = self.client.get(reverse("admin:catalog_product_change", args=[self.product.pk]))
        html = resposta.content.decode()
        self.assertIn('name="product_colors-2-price_delta"', html)
        self.assertIn('value="2.00"', html)
        self.assertIn("Cores à escolha do cliente", html)
        self.assertIn("product_colors_admin.js", html)

    def test_the_section_explains_the_custom_mode(self):
        resposta = self.client.get(reverse("admin:catalog_product_change", args=[self.product.pk]))
        self.assertContains(resposta, "oferece as cores da PALETA na compra")

    def paleta(self, linhas, modo=None):
        """O formset da paleta, montado como o Admin o monta, só com estas linhas."""
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        from apps.catalog.admin import ProductColorInline

        produto = self.produto()
        if modo is not None:
            produto.color_mode = modo
        ProductColor.objects.filter(product=produto).delete()
        inline = ProductColorInline(type(produto), django_admin.site)
        request = RequestFactory().post("/")
        request.user = self.admin
        FormSet = inline.get_formset(request, produto)
        prefixo = FormSet.get_default_prefix()
        dados = {
            f"{prefixo}-TOTAL_FORMS": str(len(linhas)),
            f"{prefixo}-INITIAL_FORMS": "0",
            f"{prefixo}-MIN_NUM_FORMS": "0",
            f"{prefixo}-MAX_NUM_FORMS": "1000",
        }
        for indice, (cor_, delta) in enumerate(linhas):
            dados[f"{prefixo}-{indice}-color"] = str(cor_.pk)
            dados[f"{prefixo}-{indice}-price_delta"] = delta
            dados[f"{prefixo}-{indice}-sort_order"] = str(indice)
        return FormSet(dados, instance=produto, prefix=prefixo)

    def test_positive_zero_and_negative_deltas_are_accepted(self):
        formset = self.paleta([(self.branco, "0.00"), (self.preto, "2.00"), (self.dourado, "-1.00")])
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(
            [form.cleaned_data["price_delta"] for form in formset.forms],
            [Decimal("0.00"), Decimal("2.00"), Decimal("-1.00")],
        )
        formset.save()
        self.assertEqual(
            list(ProductColor.objects.filter(product=self.product).values_list("price_delta", flat=True)),
            [Decimal("0.00"), Decimal("2.00"), Decimal("-1.00")],
        )

    def test_a_discount_that_zeroes_the_cheapest_variant_is_refused(self):
        """A mais barata custa 15,00: −15,00 é recusado, −14,99 passa."""
        formset = self.paleta([(self.branco, "0.00"), (self.dourado, "-15.00")])
        self.assertFalse(formset.is_valid())
        self.assertIn("preço zero ou negativo", str(formset.forms[1].errors["price_delta"]))
        self.assertIn("Porta-Retrato", str(formset.forms[1].errors["price_delta"]))
        self.assertTrue(self.paleta([(self.dourado, "-14.99")]).is_valid())

    def test_the_discount_guard_only_applies_to_the_custom_mode(self):
        self.assertTrue(self.paleta([(self.dourado, "-15.00")], modo=ColorMode.MULTI).is_valid())

    def test_a_composite_colour_goes_into_the_palette_like_any_other(self):
        azul = cor("Azul", "#0000FF", pt="Azul")
        composta = cor("Branco + Azul", "")
        composta.set_components([self.branco, azul])
        formset = self.paleta([(self.branco, "0.00"), (composta, "1.00")])
        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()
        self.assertTrue(
            ProductColor.objects.filter(product=self.product, color=composta, price_delta=Decimal("1.00")).exists()
        )

    def test_the_javascript_shows_the_palette_for_custom_and_the_delta_only_there(self):
        """Contrato com o script: sem ele a coluna ficaria escondida no modo certo."""
        from pathlib import Path

        from django.conf import settings

        script = (Path(settings.BASE_DIR) / "static" / "admin" / "js" / "product_colors_admin.js").read_text(encoding="utf-8")
        self.assertIn('modo.value === "custom"', script)
        self.assertIn("field-price_delta", script)


# ---------------------------------------------------------------------------
# 4. A página do produto
# ---------------------------------------------------------------------------


class ProductPageTests(PortaRetratoBase):
    def pagina(self):
        resposta = self.client.get(self.product.get_absolute_url())
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_the_choice_group_is_drawn_apart_from_the_variant_axes(self):
        html = self.pagina().content.decode()
        self.assertIn('data-choice-group="color"', html)
        self.assertNotIn('data-variant-group="color"', html)
        for row in (self.row_branco, self.row_preto, self.row_dourado):
            self.assertIn(f'name="choice_color" value="{row.pk}"', html)
        self.assertIn("Dourado", html)
        self.assertIn("+ € 2,00", html)

    def radio(self, html, row):
        """O `<input>` da cor na paleta (não o `<option>` da variante de mesmo id)."""
        achado = re.search(r'name="choice_color" value="%d"[^>]*>' % row.pk, html, re.S)
        self.assertIsNotNone(achado, f"sem radio para a cor {row.pk}")
        return achado.group(0)

    def test_no_colour_opens_checked_and_the_price_is_the_variants(self):
        """Escolher é do cliente: a página abre sem cor e com o preço da variante."""
        resposta = self.pagina()
        self.assertEqual(resposta.context["initial_price"], Decimal("15.00"))
        html = resposta.content.decode()
        for row in (self.row_branco, self.row_preto, self.row_dourado):
            self.assertNotIn("checked", self.radio(html, row))
            # Sem `required` do navegador: a mensagem é a nossa, e o botão fica vivo.
            self.assertNotIn("required", self.radio(html, row))
        self.assertIn('data-choice-delta="2.00"', self.radio(html, self.row_dourado))
        self.assertIn("data-choice-message", html)
        self.assertIn("Escolha uma cor antes de continuar.", html)

    def test_the_opening_price_ignores_the_deltas_whatever_the_order(self):
        ProductColor.objects.filter(pk=self.row_dourado.pk).update(sort_order=0)
        ProductColor.objects.filter(pk=self.row_branco.pk).update(sort_order=5)
        resposta = self.pagina()
        self.assertEqual(resposta.context["initial_price"], Decimal("15.00"))
        self.assertContains(resposta, "15,00")

    def test_the_buy_button_stays_enabled_without_a_colour(self):
        html = self.pagina().content.decode()
        botao = re.search(r"<button[^>]*data-add-button[^>]*>", html).group(0)
        self.assertNotIn("disabled", botao)

    def test_each_option_shows_the_swatch_the_name_and_the_delta(self):
        vermelho = cor("Vermelho", "#FF0000", pt="Vermelho")
        ProductColor.objects.create(product=self.product, color=vermelho, sort_order=3, price_delta=Decimal("-1.00"))
        resposta = self.pagina()
        self.assertContains(resposta, 'class="product-choice-dot" aria-hidden="true" style="background-color: #D4AF37"')
        self.assertContains(resposta, '<small class="product-choice-delta">+ € 2,00</small>')
        self.assertContains(resposta, '<small class="product-choice-delta">− € 1,00</small>')
        self.assertContains(resposta, "Vermelho")

    def test_a_composite_colour_is_offered_with_its_gradient(self):
        azul = cor("Azul", "#0000FF", pt="Azul")
        composta = cor("Branco + Azul", "")
        composta.set_components([self.branco, azul])
        ProductColor.objects.create(product=self.product, color=composta, sort_order=3, price_delta=Decimal("1.00"))
        resposta = self.pagina()
        self.assertContains(resposta, "background: linear-gradient(90deg, #FFFFFF 0%, #FFFFFF 50%, #0000FF 50%, #0000FF 100%)")
        self.assertContains(resposta, "Branco + Azul")

    def test_the_payload_carries_only_ids_and_deltas(self):
        resposta = self.pagina()
        self.assertEqual(
            resposta.context["choice_payload"],
            {"color": {str(self.row_branco.pk): "0.00", str(self.row_preto.pk): "0.00", str(self.row_dourado.pk): "2.00"}},
        )
        self.assertEqual(resposta.context["price_format"]["symbol"], "€")
        self.assertContains(resposta, 'id="choice-data"')
        self.assertContains(resposta, 'id="price-format"')

    def test_the_variant_selector_is_untouched(self):
        """As quatro variantes comerciais continuam no `<select>`, sem cor."""
        html = self.pagina().content.decode()
        for variante in (self.simples, self.com_foto, self.com_led, self.completo):
            self.assertIn(f'<option value="{variante.pk}"', html)
        self.assertIn('data-variant-group="size"', html)

    def test_the_other_modes_draw_no_choice_group(self):
        for modo in (ColorMode.NONE, ColorMode.SINGLE, ColorMode.MULTI):
            with self.subTest(modo=modo):
                type(self.product).objects.filter(pk=self.product.pk).update(color_mode=modo)
                resposta = self.pagina()
                self.assertNotContains(resposta, "data-choice-group")
                self.assertEqual(resposta.context["customer_choice_groups"], [])
                self.assertEqual(resposta.context["initial_price"], Decimal("15.00"))

    def test_variant_mode_keeps_the_colour_as_a_variant_axis(self):
        """«Opção comercial»: a cor resolve variante, como sempre — e nada de escolha."""
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.VARIANT)
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=self.branco)
        ProductVariant.objects.filter(pk=self.com_foto.pk).update(color=self.preto)
        ProductVariant.objects.filter(pk=self.com_led.pk).update(color=self.branco)
        ProductVariant.objects.filter(pk=self.completo.pk).update(color=self.preto)
        resposta = self.pagina()
        self.assertContains(resposta, 'data-variant-group="color"')
        self.assertNotContains(resposta, "data-choice-group")

    def test_custom_with_an_empty_palette_draws_nothing_new(self):
        ProductColor.objects.filter(product=self.product).delete()
        resposta = self.pagina()
        self.assertNotContains(resposta, "data-choice-group")
        self.assertEqual(resposta.context["initial_price"], Decimal("15.00"))

    def test_the_choice_texts_follow_the_language(self):
        with translation.override("fr"):
            resposta = self.client.get(f"/fr/produtos/{self.product.slug}/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Doré (+ € 2,00)")


# ---------------------------------------------------------------------------
# 5. O rótulo da escolha («Cor do pompom») e as duas cores na mesma página
# ---------------------------------------------------------------------------


def rotular(product, **rotulos):
    """Grava o rótulo da cor à escolha por idioma, criando a tradução se faltar."""
    from apps.catalog.models import ProductTranslation

    nome_pt = product.tr("name", language="pt") or product.sku
    for idioma, texto in rotulos.items():
        ProductTranslation.objects.update_or_create(
            master=product, language=idioma,
            defaults={"name": nome_pt, "color_choice_label": texto},
        )
    product.refresh_translations()


class ChoiceLabelTests(PortaRetratoBase):
    """O rótulo mora na tabela de traduções e cai em «Cor» quando falta no idioma."""

    def test_the_label_is_stored_per_language_in_the_translation_table(self):
        from apps.catalog.models import ProductTranslation

        rotular(self.product, pt="Cor do pompom", nl="Kleur van de pompon")
        self.assertEqual(
            dict(
                ProductTranslation.objects.filter(master=self.product)
                .exclude(color_choice_label="")
                .values_list("language", "color_choice_label")
            ),
            {"pt": "Cor do pompom", "nl": "Kleur van de pompon"},
        )
        self.assertIn("color_choice_label", type(self.product).translatable_fields)

    def test_the_label_follows_the_language_and_falls_back_to_cor(self):
        rotular(self.product, pt="Cor do pompom", fr="Couleur du pompon", en="Pom-pom colour")
        produto = self.produto()
        esperado = {"pt": "Cor do pompom", "fr": "Couleur du pompon", "en": "Pom-pom colour", "nl": "Kleur"}
        for idioma, texto in esperado.items():
            with self.subTest(idioma=idioma), translation.override(idioma):
                self.assertEqual(produto.color_choice_label, texto)
                self.assertEqual(str(choice_groups(produto)[0].label), texto)

    def test_without_any_label_the_group_is_called_cor(self):
        for idioma, texto in (("pt", "Cor"), ("fr", "Couleur"), ("nl", "Kleur"), ("en", "Colour")):
            with self.subTest(idioma=idioma), translation.override(idioma):
                self.assertEqual(self.produto().color_choice_label, texto)

    def test_the_choice_text_carries_the_label_in_the_current_language(self):
        rotular(self.product, pt="Cor do pompom")
        escolha = resolve_choices(self.produto(), {"color": self.row_dourado.pk})[0]
        self.assertEqual(escolha.text, "Cor do pompom: Dourado (+ € 2,00)")
        with translation.override("fr"):
            self.assertEqual(escolha.text, "Couleur: Doré (+ € 2,00)")


class TwoColourPageTests(PortaRetratoBase):
    """Cor da variante e cor à escolha na mesma página: duas perguntas, dois títulos."""

    VARIACAO = '<p class="product-opt-heading product-opt-heading-first">Variação</p>'
    ESCOLHA = '<p class="product-opt-heading">Sua escolha</p>'

    def pagina(self, prefixo=""):
        resposta = self.client.get(prefixo + self.product.get_absolute_url())
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def colorir_variantes(self, primeira=None, segunda=None):
        """Duas cores de variante, preenchidas em todas: o eixo «Cor» aparece."""
        primeira = primeira or self.branco
        segunda = segunda or self.preto
        for variante, cor_ in (
            (self.simples, primeira), (self.com_foto, segunda), (self.com_led, primeira), (self.completo, segunda),
        ):
            ProductVariant.objects.filter(pk=variante.pk).update(color=cor_)

    def test_only_the_customer_choice_shows_the_label_and_no_headings(self):
        rotular(self.product, pt="Cor do pompom")
        resposta = self.pagina()
        self.assertContains(resposta, "Cor do pompom")
        self.assertNotContains(resposta, 'data-variant-group="color"')
        self.assertNotContains(resposta, self.VARIACAO)
        self.assertNotContains(resposta, self.ESCOLHA)
        self.assertFalse(resposta.context["two_colour_selections"])

    def test_only_the_customer_choice_without_a_label_says_cor(self):
        html = self.pagina().content.decode()
        legenda = re.search(r'data-choice-group="color"[^>]*>\s*<legend[^>]*>(.*?)</legend>', html, re.S).group(1)
        self.assertIn("Cor", re.sub(r"<[^>]+>", "", legenda))
        self.assertNotIn(self.ESCOLHA, html)

    def test_only_the_variant_colour_is_unchanged(self):
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.VARIANT)
        self.colorir_variantes()
        resposta = self.pagina()
        self.assertContains(resposta, 'data-variant-group="color"')
        self.assertNotContains(resposta, "data-choice-group")
        self.assertNotContains(resposta, self.VARIACAO)
        self.assertNotContains(resposta, self.ESCOLHA)
        self.assertFalse(resposta.context["two_colour_selections"])

    def test_both_colours_get_the_two_headings_and_the_label(self):
        rotular(self.product, pt="Cor do pompom", fr="Couleur du pompon")
        self.colorir_variantes()
        resposta = self.pagina()
        self.assertTrue(resposta.context["two_colour_selections"])
        html = resposta.content.decode()
        self.assertIn(self.VARIACAO, html)
        self.assertIn(self.ESCOLHA, html)
        # A ordem na página: Variação → eixo da variante → Sua escolha → paleta.
        self.assertLess(html.index(self.VARIACAO), html.index('data-variant-group="color"'))
        self.assertLess(html.index('data-variant-group="color"'), html.index(self.ESCOLHA))
        self.assertLess(html.index(self.ESCOLHA), html.index('data-choice-group="color"'))
        self.assertIn("Cor do pompom", html)
        with translation.override("fr"):
            resposta = self.pagina()
        self.assertContains(resposta, "Déclinaison")
        self.assertContains(resposta, "Votre choix")
        self.assertContains(resposta, "Couleur du pompon")

    def test_both_colours_without_a_label_still_get_the_headings(self):
        self.colorir_variantes()
        resposta = self.pagina()
        self.assertContains(resposta, self.VARIACAO)
        self.assertContains(resposta, self.ESCOLHA)

    def test_a_composite_variant_colour_with_a_customer_choice(self):
        azul = cor("Azul", "#0000FF", pt="Azul")
        composta = cor("Branco + Azul", "")
        composta.set_components([self.branco, azul])
        rotular(self.product, pt="Cor do pompom")
        self.colorir_variantes(primeira=composta, segunda=self.preto)
        resposta = self.pagina()
        html = resposta.content.decode()
        self.assertTrue(resposta.context["two_colour_selections"])
        self.assertIn(
            'class="variant-swatch" style="background: linear-gradient(90deg, #FFFFFF 0%, #FFFFFF 50%, '
            '#0000FF 50%, #0000FF 100%)"',
            html,
        )
        self.assertIn(self.VARIACAO, html)
        self.assertIn(self.ESCOLHA, html)
        self.assertIn("Cor do pompom", html)
        self.assertIn('data-variant-choice="color">Branco + Azul</b>', html)

    def test_the_same_colour_in_both_sets_stays_two_different_fields(self):
        """«Branco» na variante e «Branco» na paleta: o eixo troca a variante, a escolha não."""
        self.colorir_variantes()
        html = self.pagina().content.decode()
        self.assertIn(f'name="option_color" value="{self.branco.pk}"', html)
        self.assertIn(f'name="choice_color" value="{self.row_branco.pk}"', html)
        self.assertIn(self.VARIACAO, html)
        self.assertIn(self.ESCOLHA, html)


class ChoiceLabelAdminTests(PortaRetratoBase):
    """O campo no modal de conteúdo e o aviso da cor repetida."""

    AVISO = "também está sendo usada nas variantes deste produto"

    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(self.admin)
        self.url = reverse("admin:catalog_product_change", args=[self.product.pk])

    def test_the_content_modal_offers_the_label_per_language(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('name="translations-0-color_choice_label"', html)
        self.assertIn("Cor do pompom", html)  # o help_text dá o exemplo

    def test_the_modal_save_stores_the_label(self):
        from apps.catalog.models import ProductTranslation

        resposta = self.client.post(
            reverse("admin:catalog_product_content_save", args=[self.product.pk]),
            {
                "language": "fr", "name": "Cadre photo", "short_description": "", "description": "",
                "extra_information": "", "color_choice_label": "Couleur du pompon",
            },
        )
        self.assertEqual(resposta.status_code, 200, resposta.content[:300])
        self.assertEqual(
            ProductTranslation.objects.get(master=self.product, language="fr").color_choice_label,
            "Couleur du pompon",
        )

    def test_the_content_javascript_sends_the_label(self):
        from pathlib import Path

        from django.conf import settings

        script = (Path(settings.BASE_DIR) / "static" / "admin" / "js" / "content_admin.js").read_text(encoding="utf-8")
        self.assertIn('"color_choice_label"', script)

    def test_a_colour_in_both_sets_raises_a_non_blocking_warning(self):
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=self.preto)
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "«Preto»: esta cor " + self.AVISO)
        self.assertContains(resposta, "Verifique se as duas cores representam partes diferentes do produto.")
        self.assertContains(resposta, "warning")

    def test_two_repeated_colours_are_both_named(self):
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=self.preto)
        ProductVariant.objects.filter(pk=self.com_foto.pk).update(color=self.branco)
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "«Branco», «Preto»: estas cores também estão sendo usadas")

    def test_no_warning_when_the_sets_do_not_overlap(self):
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=cor("Verde", "#00FF00", pt="Verde"))
        self.assertNotContains(self.client.get(self.url), self.AVISO)

    def test_no_warning_outside_the_custom_mode(self):
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=self.preto)
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.MULTI)
        self.assertNotContains(self.client.get(self.url), self.AVISO)

    def test_the_warning_does_not_block_saving_and_follows_to_the_list(self):
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=self.preto)
        traducao = self.product.translations.get(language="pt")
        payload = {
            "sku": self.product.sku,
            "status": "active",
            "slug": self.product.slug,
            "category": self.category.pk,
            "currency": "EUR",
            "color_mode": ColorMode.CUSTOM.value,
            "personalization_type": "none",
            "personalization_text_limit": 0,
            "featured_order": 0,
            "translations-TOTAL_FORMS": "1",
            "translations-INITIAL_FORMS": "1",
            "translations-MIN_NUM_FORMS": "1",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-id": traducao.pk,
            "translations-0-master": self.product.pk,
            "translations-0-language": "pt",
            "translations-0-name": traducao.name,
            "translations-0-short_description": "",
            "translations-0-description": "",
            "translations-0-extra_information": "",
            "translations-0-color_choice_label": "Cor do pompom",
            "media-TOTAL_FORMS": "0", "media-INITIAL_FORMS": "0",
            "media-MIN_NUM_FORMS": "0", "media-MAX_NUM_FORMS": "1000",
            "product_colors-TOTAL_FORMS": "1", "product_colors-INITIAL_FORMS": "1",
            "product_colors-MIN_NUM_FORMS": "0", "product_colors-MAX_NUM_FORMS": "1000",
            "product_colors-0-id": self.row_preto.pk,
            "product_colors-0-product": self.product.pk,
            "product_colors-0-color": self.preto.pk,
            "product_colors-0-price_delta": "0.00",
            "product_colors-0-sort_order": "1",
            "material_composition-TOTAL_FORMS": "0", "material_composition-INITIAL_FORMS": "0",
            "material_composition-MIN_NUM_FORMS": "0", "material_composition-MAX_NUM_FORMS": "1000",
            "variants-TOTAL_FORMS": "4", "variants-INITIAL_FORMS": "4",
            "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
            "_save": "Salvar",
        }
        # Um produto ativo exige uma variante ativa no próprio POST: vão as quatro.
        for indice, variante in enumerate(self.product.variants.order_by("pk")):
            payload.update({
                f"variants-{indice}-id": variante.pk, f"variants-{indice}-product": self.product.pk,
                f"variants-{indice}-sku": variante.sku, f"variants-{indice}-sort_order": "0",
                f"variants-{indice}-is_active": "on", f"variants-{indice}-pricing_mode": variante.pricing_mode,
                f"variants-{indice}-sale_price": str(variante.sale_price),
                f"variants-{indice}-stock_quantity": str(variante.stock_quantity),
                f"variants-{indice}-filament_cost": "0", f"variants-{indice}-energy_cost": "0",
                f"variants-{indice}-dimension_unit": "mm", f"variants-{indice}-size": variante.size,
                f"variants-{indice}-color": str(variante.color_id or ""),
            })
        resposta = self.client.post(self.url, payload, follow=True)
        if not resposta.redirect_chain:
            erros = {"form": resposta.context["adminform"].form.errors}
            for inline in resposta.context["inline_admin_formsets"]:
                erros[inline.formset.prefix] = (inline.formset.errors, inline.formset.non_form_errors())
            self.fail(f"o formulário não gravou: {erros}")
        self.assertContains(resposta, "«Preto»: esta cor " + self.AVISO)
        self.assertEqual(
            self.product.translations.get(language="pt").color_choice_label, "Cor do pompom"
        )
