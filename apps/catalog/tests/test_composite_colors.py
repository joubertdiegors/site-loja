"""Cores compostas — «Branco + Azul» como uma cor do catálogo.

A variante continua apontando para **uma** cor (`ProductVariant.color`); o que
muda é que essa cor pode ter componentes. Estes testes guardam:

* a cor simples continua exatamente como era (nome, fallback, hex, bolinha);
* a composta se monta pelas componentes — nome no idioma pedido, hex de cada
  uma, ordem cadastrada — e obedece às regras (duas ou mais, só simples, sem
  repetir, sem composição duplicada);
* a variante, a página, o carrinho, o pedido, o filtro da lista e a paleta
  «à escolha» tratam a composta como qualquer cor.
"""

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from apps.cart.cart import CartLine, load_products, load_variants
from apps.cart.keys import line_key
from apps.catalog.choices import choice_groups, resolve_choices
from apps.catalog.models import (
    Color,
    ColorComponent,
    ColorMode,
    ColorTranslation,
    ProductColor,
    ProductVariant,
    color_prefetches,
    find_equivalent_composition,
    validate_composition,
)
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services


def cor(nome, hexa="", **traducoes):
    color = Color.objects.create(name=nome, hex_code=hexa)
    for idioma, texto in traducoes.items():
        ColorTranslation.objects.create(master=color, language=idioma, name=texto)
    color.refresh_translations()
    return color


def fresh(color):
    return Color.objects.get(pk=color.pk)


def payload(prefix, rows, initial=0):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"{prefix}-{index}-{field}"] = value
    return data


class CoresBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.branco = cor("Branco", "#FFFFFF", pt="Branco", fr="Blanc", nl="Wit", en="White")
        self.azul = cor("Azul", "#0000FF", pt="Azul", fr="Bleu", nl="Blauw", en="Blue")
        self.rosa = cor("Rosa", "#FF69B4", pt="Rosa", fr="Rose")
        self.roxo = cor("Roxo", "#6D2CE0", pt="Roxo")
        self.vermelho = cor("Vermelho", "#FF0000")

    def composta(self, nome, *componentes, hexa="", **traducoes):
        color = cor(nome, hexa, **traducoes)
        color.set_components(componentes)
        return fresh(color)


# ---------------------------------------------------------------------------
# 1. A cor simples não mudou
# ---------------------------------------------------------------------------


class SimpleColourTests(CoresBase):
    def test_a_simple_colour_has_no_components(self):
        self.assertFalse(self.branco.is_composite)
        self.assertEqual(self.branco.component_list, [])
        self.assertEqual(self.branco.hex_codes, ["#FFFFFF"])
        self.assertEqual(self.branco.swatch_background, "#FFFFFF")
        # O CSS da cor simples é o de sempre — nenhum template antigo muda.
        self.assertEqual(self.branco.swatch_style, "background-color: #FFFFFF")

    def test_a_simple_colour_without_hex_paints_nothing(self):
        sem = cor("Sem hex")
        self.assertEqual(sem.hex_codes, [])
        self.assertEqual(sem.swatch_style, "")

    def test_display_name_and_fallback_are_untouched(self):
        self.assertEqual(self.branco.display_name, "Branco")
        with translation.override("fr"):
            self.assertEqual(self.branco.display_name, "Blanc")
        # Sem holandês, o roxo cai no português; sem tradução nenhuma, no nome interno.
        with translation.override("nl"):
            self.assertEqual(self.roxo.display_name, "Roxo")
            self.assertEqual(self.vermelho.display_name, "Vermelho")
        self.assertEqual(self.branco.name_in("en"), "White")

    def test_component_list_does_not_query_for_a_simple_colour(self):
        with self.assertNumQueries(0):
            self.assertEqual(self.branco.component_list, [])
            self.assertEqual(self.branco.hex_codes, ["#FFFFFF"])


# ---------------------------------------------------------------------------
# 2. A composta
# ---------------------------------------------------------------------------


class CompositeColourTests(CoresBase):
    def test_two_components(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul, hexa="#FFFFFF")
        self.assertTrue(bicolor.is_composite)
        self.assertEqual([c.name for c in bicolor.component_list], ["Branco", "Azul"])
        self.assertEqual(bicolor.hex_codes, ["#FFFFFF", "#0000FF"])
        self.assertEqual(
            bicolor.swatch_background,
            "linear-gradient(90deg, #FFFFFF 0%, #FFFFFF 50%, #0000FF 50%, #0000FF 100%)",
        )
        self.assertTrue(bicolor.swatch_style.startswith("background: linear-gradient"))

    def test_three_and_four_components_keep_the_order(self):
        tricolor = self.composta("Tricolor", self.rosa, self.branco, self.azul)
        self.assertEqual([c.name for c in tricolor.component_list], ["Rosa", "Branco", "Azul"])
        self.assertEqual(tricolor.hex_codes, ["#FF69B4", "#FFFFFF", "#0000FF"])
        self.assertIn("33.3333%", tricolor.swatch_background)

        quatro = self.composta("Quatro", self.branco, self.azul, self.vermelho, self.roxo)
        self.assertEqual(len(quatro.component_list), 4)
        self.assertIn("#FF0000 50%, #FF0000 75%", quatro.swatch_background)

    def test_reordering_the_components(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        bicolor.set_components([self.azul, self.branco])
        self.assertEqual([c.name for c in fresh(bicolor).component_list], ["Azul", "Branco"])
        self.assertEqual(fresh(bicolor).hex_codes, ["#0000FF", "#FFFFFF"])

    def test_the_name_is_composed_from_the_components_in_the_requested_language(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        self.assertEqual(bicolor.display_name, "Branco + Azul")
        with translation.override("fr"):
            self.assertEqual(bicolor.display_name, "Blanc + Bleu")
        self.assertEqual(bicolor.name_in("nl"), "Wit + Blauw")
        self.assertEqual(bicolor.name_in("en"), "White + Blue")

    def test_a_component_without_the_language_falls_back_like_before(self):
        """«Rosa + Roxo» em neerlandês: cada componente cai no seu fallback."""
        dupla = self.composta("Rosa + Roxo", self.rosa, self.roxo)
        with translation.override("nl"):
            self.assertEqual(dupla.display_name, "Rosa + Roxo")
        self.assertEqual(dupla.name_in("fr"), "Rose + Roxo")

    def test_an_own_translation_wins_in_its_language_only(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul, fr="Bicolore")
        with translation.override("fr"):
            self.assertEqual(bicolor.display_name, "Bicolore")
        with translation.override("nl"):
            self.assertEqual(bicolor.display_name, "Wit + Blauw")
        self.assertEqual(bicolor.display_name, "Branco + Azul")

    def test_hex_falls_back_to_the_composite_own_hex(self):
        sem_hex_a = cor("Cinza claro")
        sem_hex_b = cor("Cinza escuro")
        composta = self.composta("Cinzas", sem_hex_a, sem_hex_b, hexa="#888888")
        self.assertEqual(composta.hex_codes, ["#888888"])
        self.assertEqual(composta.swatch_background, "#888888")

    def test_back_to_simple(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        bicolor.set_components([])
        self.assertFalse(fresh(bicolor).is_composite)
        self.assertEqual(fresh(bicolor).component_list, [])
        self.assertEqual(ColorComponent.objects.filter(color=bicolor).count(), 0)

    def test_the_flag_follows_the_rows(self):
        composta = cor("Duo")
        ColorComponent.objects.create(color=composta, component=self.branco, sort_order=0)
        self.assertTrue(fresh(composta).is_composite)
        ColorComponent.objects.create(color=composta, component=self.azul, sort_order=1)
        ColorComponent.objects.filter(color=composta).first().delete()
        self.assertTrue(fresh(composta).is_composite)
        ColorComponent.objects.filter(color=composta).first().delete()
        self.assertFalse(fresh(composta).is_composite)

    def test_deleting_a_component_colour_in_use_is_protected(self):
        from django.db.models import ProtectedError

        self.composta("Branco + Azul", self.branco, self.azul)
        with self.assertRaises(ProtectedError):
            self.azul.delete()

    def test_deleting_the_composite_keeps_the_components(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        bicolor.delete()
        self.assertTrue(Color.objects.filter(pk__in=[self.branco.pk, self.azul.pk]).count() == 2)
        self.assertEqual(ColorComponent.objects.count(), 0)


# ---------------------------------------------------------------------------
# 3. As regras
# ---------------------------------------------------------------------------


class CompositionRulesTests(CoresBase):
    def test_a_single_component_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "pelo menos duas componentes"):
            cor("Só uma").set_components([self.branco])

    def test_a_composite_cannot_be_a_component(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        with self.assertRaisesMessage(ValidationError, "já é uma cor composta"):
            cor("Aninhada").set_components([bicolor, self.rosa])
        with self.assertRaises(ValidationError):
            ColorComponent(color=cor("Outra"), component=bicolor).save()

    def test_a_repeated_component_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "aparece duas vezes"):
            cor("Repetida").set_components([self.branco, self.branco])

    def test_a_colour_cannot_contain_itself(self):
        alvo = cor("Eu mesma")
        with self.assertRaisesMessage(ValidationError, "de si mesma"):
            alvo.set_components([alvo, self.branco])
        with self.assertRaises((ValidationError, IntegrityError)):
            ColorComponent(color=alvo, component=alvo).save()

    def test_an_equivalent_composition_is_refused_whatever_the_order(self):
        self.composta("Branco + Azul", self.branco, self.azul)
        with self.assertRaisesMessage(ValidationError, "mesmas componentes"):
            cor("Azul + Branco").set_components([self.azul, self.branco])
        self.assertIsNotNone(find_equivalent_composition([self.azul.pk, self.branco.pk]))
        self.assertIsNone(find_equivalent_composition([self.azul.pk, self.branco.pk, self.rosa.pk]))

    def test_a_colour_used_as_component_cannot_become_composite(self):
        self.composta("Branco + Azul", self.branco, self.azul)
        with self.assertRaisesMessage(ValidationError, "é componente de"):
            self.branco.set_components([self.rosa, self.roxo])

    def test_the_same_pair_twice_is_refused_by_the_database(self):
        composta = cor("Duo")
        ColorComponent.objects.create(color=composta, component=self.branco)
        with self.assertRaises(IntegrityError):
            ColorComponent.objects.create(color=composta, component=self.branco)

    def test_validate_composition_accepts_the_empty_list(self):
        validate_composition(self.branco, [])


# ---------------------------------------------------------------------------
# 4. A variante, a página, o carrinho e o pedido
# ---------------------------------------------------------------------------


class VariantWithCompositeTests(CoresBase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="fidgets", name="Fidgets")
        self.branco_azul = self.composta("Branco + Azul", self.branco, self.azul)
        self.branco_rosa = self.composta("Branco + Rosa", self.branco, self.rosa)
        self.product = make_product(
            sku="FIDGET", name="Fidget Articulado", category=self.category, with_variant=False,
            color_mode=ColorMode.VARIANT,
        )
        self.v1 = ProductVariant.objects.create(
            product=self.product, sku="FIDGET-V01", color=self.branco_azul, size="10 cm",
            sale_price=Decimal("12.00"), stock_quantity=5, weight_grams=Decimal("40"),
        )
        self.v2 = ProductVariant.objects.create(
            product=self.product, sku="FIDGET-V02", color=self.branco_rosa, size="10 cm",
            sale_price=Decimal("12.00"), stock_quantity=5, weight_grams=Decimal("40"),
        )
        self.v3 = ProductVariant.objects.create(
            product=self.product, sku="FIDGET-V03", color=self.branco, size="10 cm",
            sale_price=Decimal("11.00"), stock_quantity=5, weight_grams=Decimal("40"),
        )
        self.product.refresh_from_db()

    def test_the_variant_points_to_one_colour_and_keeps_everything_else(self):
        self.assertEqual(self.v1.color, self.branco_azul)
        self.assertEqual(self.v1.label, "Branco + Azul · 10 cm")
        self.assertEqual(self.v1.sku, "FIDGET-V01")
        self.assertEqual(self.v1.sale_price, Decimal("12.00"))
        self.assertEqual(self.v1.stock_quantity, 5)
        campo = ProductVariant._meta.get_field("color")
        self.assertTrue(campo.many_to_one)
        self.assertFalse(campo.many_to_many)

    def test_composite_and_simple_are_different_combinations(self):
        """«Branco + Azul» e «Branco» no mesmo tamanho são duas variantes legítimas."""
        for variante in (self.v1, self.v2, self.v3):
            variante.full_clean()
        repetida = ProductVariant(product=self.product, sku="FIDGET-V04", color=self.branco_azul,
                                  size="10 cm", sale_price=Decimal("1.00"))
        with self.assertRaises(ValidationError):
            repetida.full_clean()

    def test_the_product_offers_the_composite_colours(self):
        self.assertEqual(
            [c.name for c in self.product.available_colors], ["Branco + Azul", "Branco + Rosa", "Branco"]
        )
        self.assertEqual([c.name for c in self.product.display_colors][:2], ["Branco + Azul", "Branco + Rosa"])

    def test_the_page_paints_the_composite_swatch_and_names_it(self):
        resposta = self.client.get(self.product.get_absolute_url())
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        self.assertIn('data-variant-group="color"', html)
        self.assertIn("linear-gradient(90deg, #FFFFFF 0%, #FFFFFF 50%, #0000FF 50%, #0000FF 100%)", html)
        self.assertIn("Branco + Azul", html)
        grupo = next(g for g in resposta.context["variant_options"] if g["key"] == "color")
        self.assertEqual(
            [o["label"] for o in grupo["options"]], ["Branco + Azul", "Branco + Rosa", "Branco"]
        )
        self.assertTrue(grupo["options"][0]["swatch"].startswith("background: linear-gradient"))
        self.assertEqual(grupo["options"][2]["swatch"], "background-color: #FFFFFF")
        payload_ = {v["id"]: v for v in resposta.context["variant_payload"]}
        self.assertEqual(payload_[self.v1.pk]["colorLabel"], "Branco + Azul")
        self.assertEqual(payload_[self.v1.pk]["color"], str(self.branco_azul.pk))

    def test_the_page_speaks_the_customers_language(self):
        resposta = self.client.get(f"/fr/produtos/{self.product.slug}/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Blanc + Bleu")
        self.assertContains(resposta, "Blanc + Rose")

    def test_the_card_paints_the_composite(self):
        resposta = self.client.get("/categorias/fidgets/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "linear-gradient(90deg, #FFFFFF 0%, #FFFFFF 50%, #0000FF 50%, #0000FF 100%)")

    def test_add_to_cart_by_colour_axis_resolves_the_composite_variant(self):
        resposta = self.client.post(
            reverse("cart:add"),
            {"product_id": self.product.pk, "option_color": str(self.branco_azul.pk), "option_size": "10 cm", "quantity": 1},
        )
        self.assertEqual(resposta.status_code, 302)
        pagina = self.client.get(reverse("cart:detail"))
        self.assertContains(pagina, "Branco + Azul")
        self.assertContains(pagina, "linear-gradient(90deg, #FFFFFF 0%, #FFFFFF 50%, #0000FF 50%, #0000FF 100%)")
        self.assertContains(pagina, "12,00")

    def test_the_order_freezes_the_composite_name(self):
        country = make_country("BE", vat_rate="21.00")
        method = make_method(min_days=2, max_days=3)
        make_rate(method, country, 0, 5000, "5.90")
        make_bank_account()
        user = make_user(username="cliente", email="cliente@example.com")
        address = make_address(user.customer, country)
        items = {"x": {"product_id": self.product.pk, "variant_id": self.v1.pk}}
        produto = load_products(items)[self.product.pk]
        variante = load_variants(items)[self.v1.pk]
        linha = CartLine(key=line_key(produto.pk, variante.pk, None), product=produto, variant=variante, quantity=1)

        with translation.override("fr"):
            pedido = services.create_order(
                customer=user.customer, lines=[linha], shipping_address=address,
                billing_address=address, shipping_method=method, language="fr",
            )
        item = pedido.items.get()
        self.assertEqual(item.color_name, "Blanc + Bleu")
        self.assertEqual(item.variant_label, "Blanc + Bleu · 10 cm")

        # Renomear e desmontar a composta depois não muda o pedido.
        self.branco_azul.set_components([])
        ColorTranslation.objects.filter(master=self.azul).update(name="Cyan")
        item.refresh_from_db()
        self.assertEqual(item.color_name, "Blanc + Bleu")

    def test_a_long_composite_name_is_cut_to_the_order_column(self):
        """`color_name` tem 60 caracteres; o nome composto pode passar disso.

        No SQLite o texto maior entraria calado; no PostgreSQL derrubaria o
        checkout. O pedido guarda o começo do nome, como `colors_snapshot`.
        """
        longa = self.composta("AB", cor("A" * 60, "#444444"), cor("B" * 60, "#555555"))
        variante = ProductVariant.objects.create(
            product=self.product, sku="FIDGET-V04", color=longa, size="10 cm",
            sale_price=Decimal("12.00"), stock_quantity=5, weight_grams=Decimal("40"),
        )
        self.assertEqual(len(longa.display_name), 123)

        country = make_country("BE", vat_rate="21.00")
        method = make_method(min_days=2, max_days=3)
        make_rate(method, country, 0, 5000, "5.90")
        make_bank_account()
        user = make_user(username="cliente", email="cliente@example.com")
        address = make_address(user.customer, country)
        items = {"x": {"product_id": self.product.pk, "variant_id": variante.pk}}
        produto = load_products(items)[self.product.pk]
        variante = load_variants(items)[variante.pk]
        linha = CartLine(key=line_key(produto.pk, variante.pk, None), product=produto, variant=variante, quantity=1)

        pedido = services.create_order(
            customer=user.customer, lines=[linha], shipping_address=address,
            billing_address=address, shipping_method=method, language="pt",
        )
        item = pedido.items.get()
        limite = type(item)._meta.get_field("color_name").max_length
        self.assertEqual(limite, 60)
        self.assertEqual(item.color_name, longa.display_name[:60])
        self.assertEqual(len(item.color_name), 60)
        self.assertEqual(item.variant_label, f"{longa.display_name} · 10 cm")


# ---------------------------------------------------------------------------
# 5. O filtro da lista de produtos
# ---------------------------------------------------------------------------


class AdminListFilterTests(CoresBase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(self.admin)
        self.category = make_category(slug="fidgets", name="Fidgets")
        self.branco_azul = self.composta("Branco + Azul", self.branco, self.azul)
        self.branco_rosa = self.composta("Branco + Rosa", self.branco, self.rosa)

        self.fidget = make_product(sku="FIDGET", name="Fidget", category=self.category, with_variant=False)
        ProductVariant.objects.create(product=self.fidget, sku="FIDGET-V01", color=self.branco_azul,
                                      sale_price=Decimal("12.00"), stock_quantity=5)
        self.laco = make_product(sku="LACO", name="Laço", category=self.category, with_variant=False)
        ProductVariant.objects.create(product=self.laco, sku="LACO-V01", color=self.branco_rosa,
                                      sale_price=Decimal("8.00"), stock_quantity=5)
        self.vaso = make_product(sku="VASO", name="Vaso", category=self.category, with_variant=False)
        ProductVariant.objects.create(product=self.vaso, sku="VASO-V01", color=self.roxo,
                                      sale_price=Decimal("8.00"), stock_quantity=5)
        # A paleta também pode apontar para uma composta.
        self.quadro = make_product(sku="QUADRO", name="Quadro", category=self.category,
                                   price=Decimal("30.00"), variant_sku="QUADRO-V01", color_mode=ColorMode.MULTI)
        ProductColor.objects.create(product=self.quadro, color=self.branco_azul)

    def skus(self, **parametros):
        resposta = self.client.get(reverse("admin:catalog_product_changelist"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return sorted(p.sku for p in resposta.context["cl"].result_list), resposta

    def test_a_component_finds_the_composite_variants(self):
        self.assertEqual(self.skus(cor=str(self.azul.pk))[0], ["FIDGET", "QUADRO"])
        self.assertEqual(self.skus(cor=str(self.branco.pk))[0], ["FIDGET", "LACO", "QUADRO"])
        self.assertEqual(self.skus(cor=str(self.rosa.pk))[0], ["LACO"])
        self.assertEqual(self.skus(cor=str(self.roxo.pk))[0], ["VASO"])

    def test_the_composite_itself_still_filters(self):
        self.assertEqual(self.skus(cor=str(self.branco_azul.pk))[0], ["FIDGET", "QUADRO"])

    def test_the_components_are_offered_as_options(self):
        _skus, resposta = self.skus()
        grupo = next(g for g in resposta.context["jd_grupos"] if g["parametro"] == "cor")
        rotulos = [o["rotulo"] for o in grupo["opcoes"]]
        for esperado in ("Azul", "Branco", "Rosa", "Roxo", "Branco + Azul", "Branco + Rosa"):
            self.assertIn(esperado, rotulos)
        self.assertNotIn("Vermelho", rotulos)
        contagens = {o["rotulo"]: o["contagem"] for o in grupo["opcoes"]}
        self.assertEqual(contagens["Azul"], 2)
        self.assertEqual(contagens["Branco"], 3)


# ---------------------------------------------------------------------------
# 6. «Cores à escolha do cliente» aceita a composta
# ---------------------------------------------------------------------------


class CustomerChoiceCompatibilityTests(CoresBase):
    def test_a_composite_in_the_palette_is_a_choice_with_its_gradient(self):
        category = make_category(slug="decoracao", name="Decoração")
        branco_azul = self.composta("Branco + Azul", self.branco, self.azul)
        produto = make_product(sku="PORTA", name="Porta-Retrato", category=category,
                               price=Decimal("20.00"), variant_sku="PORTA-V01", color_mode=ColorMode.CUSTOM)
        ProductColor.objects.create(product=produto, color=self.branco, sort_order=0)
        row = ProductColor.objects.create(product=produto, color=branco_azul, sort_order=1, price_delta=Decimal("1.00"))
        produto = type(produto).objects.get(pk=produto.pk)

        grupo = choice_groups(produto)[0]
        composta = grupo.options[1]
        self.assertEqual(composta.value_label, "Branco + Azul")
        self.assertEqual(composta.text, "Cor: Branco + Azul (+ € 1,00)")
        self.assertTrue(composta.swatch_style.startswith("background: linear-gradient"))
        self.assertEqual(composta.hex_code, "")
        escolha = resolve_choices(produto, {"color": row.pk})[0]
        self.assertEqual(escolha.price_delta, Decimal("1.00"))
        with translation.override("fr"):
            self.assertEqual(escolha.value_label, "Blanc + Bleu")


# ---------------------------------------------------------------------------
# 7. O Admin de Cores
# ---------------------------------------------------------------------------


class ColorAdminTests(CoresBase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(self.admin)

    def dados(self, nome, componentes, traducoes=None, initial=0):
        if traducoes is None:
            traducoes = (("pt", nome),)
        return {
            "name": nome,
            "slug": "",
            "hex_code": "#FFFFFF",
            "is_active": "on",
            **payload("translations", [{"language": idioma, "name": texto} for idioma, texto in traducoes]),
            **payload("component_links", [
                {"component": str(componente.pk), "sort_order": str(ordem)}
                for ordem, componente in enumerate(componentes)
            ], initial=initial),
            "_save": "Salvar",
        }

    def test_the_change_page_offers_the_components_inline(self):
        resposta = self.client.get(reverse("admin:catalog_color_change", args=[self.branco.pk]))
        self.assertContains(resposta, "COMPONENTES")
        self.assertContains(resposta, "component_links-TOTAL_FORMS")
        self.assertContains(resposta, "data-swatch")

    def test_creating_a_composite_through_the_admin(self):
        resposta = self.client.post(reverse("admin:catalog_color_add"), self.dados("Branco + Azul", [self.branco, self.azul]))
        self.assertEqual(resposta.status_code, 302, resposta.content[:600] if resposta.status_code != 302 else "")
        criada = Color.objects.get(name="Branco + Azul")
        self.assertTrue(criada.is_composite)
        self.assertEqual([c.name for c in criada.component_list], ["Branco", "Azul"])

    def test_reordering_through_the_admin(self):
        composta = self.composta("Branco + Azul", self.branco, self.azul)
        links = list(composta.component_links.order_by("sort_order"))
        dados = self.dados("Branco + Azul", [], traducoes=())
        dados.update(payload("translations", []))
        dados.update(payload("component_links", [
            {"id": links[0].pk, "component": str(self.branco.pk), "sort_order": "1"},
            {"id": links[1].pk, "component": str(self.azul.pk), "sort_order": "0"},
        ], initial=2))
        resposta = self.client.post(reverse("admin:catalog_color_change", args=[composta.pk]), dados)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual([c.name for c in fresh(composta).component_list], ["Azul", "Branco"])

    def test_one_component_is_refused_with_a_message(self):
        resposta = self.client.post(reverse("admin:catalog_color_add"), self.dados("Quase", [self.branco]))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "pelo menos duas componentes")
        self.assertFalse(Color.objects.filter(name="Quase").exists())

    def test_a_duplicate_composition_is_refused_with_a_message(self):
        self.composta("Branco + Azul", self.branco, self.azul)
        resposta = self.client.post(reverse("admin:catalog_color_add"), self.dados("Azul + Branco", [self.azul, self.branco]))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "mesmas componentes")

    def test_the_component_select_lists_only_simple_colours(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        resposta = self.client.get(reverse("admin:catalog_color_change", args=[self.rosa.pk]))
        html = resposta.content.decode()
        opcoes = re.findall(r'name="component_links-__prefix__-component"[^>]*>(.*?)</select>', html, re.S)
        self.assertTrue(opcoes)
        self.assertNotIn(f'value="{bicolor.pk}"', opcoes[0])
        self.assertNotIn(f'value="{self.rosa.pk}"', opcoes[0])
        self.assertIn(f'value="{self.branco.pk}"', opcoes[0])

    def test_the_list_shows_the_composition_and_the_gradient(self):
        self.composta("Branco + Azul", self.branco, self.azul)
        resposta = self.client.get(reverse("admin:catalog_color_changelist"))
        self.assertContains(resposta, "Branco + Azul")
        self.assertContains(resposta, "linear-gradient(90deg")
        self.assertContains(resposta, "simples")

    def test_the_product_sheet_select_carries_the_gradient(self):
        category = make_category(slug="fidgets", name="Fidgets")
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        produto = make_product(sku="FIDGET", name="Fidget", category=category, with_variant=False,
                               color_mode=ColorMode.MULTI)
        ProductColor.objects.create(product=produto, color=bicolor)
        resposta = self.client.get(reverse("admin:catalog_product_change", args=[produto.pk]))
        self.assertContains(resposta, 'data-swatch="linear-gradient(90deg')


# ---------------------------------------------------------------------------
# 8. O custo em consultas: nunca uma por cor composta
# ---------------------------------------------------------------------------


class CompositeQueryCostTests(CoresBase):
    """`component_list` lê uma vez por instância; com prefetch, nenhuma."""

    def test_the_components_are_read_once_per_instance(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        # Ligações com a componente numa consulta, traduções das componentes na outra.
        with self.assertNumQueries(2):
            self.assertEqual([c.name for c in bicolor.component_list], ["Branco", "Azul"])
        # Só as traduções próprias da composta (vazias) ainda custam uma.
        with self.assertNumQueries(1):
            self.assertEqual(bicolor.display_name, "Branco + Azul")
        with self.assertNumQueries(0):
            self.assertEqual(bicolor.name_in("fr"), "Blanc + Bleu")
            self.assertEqual(bicolor.hex_codes, ["#FFFFFF", "#0000FF"])
            self.assertTrue(bicolor.swatch_style.startswith("background: linear-gradient"))

    def test_prefetched_components_cost_nothing(self):
        self.composta("Branco + Azul", self.branco, self.azul)
        carregada = Color.objects.prefetch_related(*color_prefetches("")).get(name="Branco + Azul")
        with self.assertNumQueries(0):
            self.assertEqual(carregada.display_name, "Branco + Azul")
            self.assertEqual(carregada.name_in("nl"), "Wit + Blauw")
            self.assertEqual(carregada.hex_codes, ["#FFFFFF", "#0000FF"])

    def test_set_components_refreshes_the_cached_list(self):
        bicolor = self.composta("Branco + Azul", self.branco, self.azul)
        self.assertEqual([c.name for c in bicolor.component_list], ["Branco", "Azul"])
        bicolor.set_components([self.azul, self.branco, self.rosa])
        self.assertEqual([c.name for c in bicolor.component_list], ["Azul", "Branco", "Rosa"])
        bicolor.set_components([])
        self.assertEqual(bicolor.component_list, [])


class CompositePageCostTests(CoresBase):
    """As páginas e o Admin não crescem em consultas com mais cores compostas."""

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="fidgets", name="Fidgets")
        self.product = make_product(
            sku="FIDGET", name="Fidget", category=self.category, with_variant=False,
            color_mode=ColorMode.VARIANT,
        )
        self.compostas = [
            self.composta("Branco + Azul", self.branco, self.azul),
            self.composta("Branco + Rosa", self.branco, self.rosa),
        ]
        for indice, composta in enumerate(self.compostas):
            self.variante(indice, composta)
        self.admin = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")

    def variante(self, indice, composta):
        return ProductVariant.objects.create(
            product=self.product, sku=f"FIDGET-V{indice:02d}", color=composta, size=f"{indice} cm",
            sale_price=Decimal("12.00"), stock_quantity=5, weight_grams=Decimal("40"),
        )

    def mais_compostas(self):
        """Quatro compostas novas, sem repetir composição."""
        novas = []
        for indice in range(4):
            extra = cor(f"Extra {indice}", f"#{indice + 1}{indice + 1}{indice + 1}{indice + 1}{indice + 1}{indice + 1}")
            par = self.roxo if indice % 2 else self.vermelho
            novas.append(self.composta(f"Composta {indice}", extra, par))
        return novas

    def consultas(self, client, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.get(url)
        with CaptureQueriesContext(connection) as capturadas:
            resposta = client.get(url)
        self.assertEqual(resposta.status_code, 200)
        return len(capturadas)

    def test_the_product_page_does_not_pay_per_composite(self):
        url = self.product.get_absolute_url()
        base = self.consultas(self.client, url)
        for indice, composta in enumerate(self.mais_compostas(), start=2):
            self.variante(indice, composta)
        self.assertEqual(self.consultas(self.client, url), base)
        self.assertContains(self.client.get(url), "Extra 3 + Roxo")

    def test_the_category_page_does_not_pay_per_composite(self):
        url = self.category.get_absolute_url()
        base = self.consultas(self.client, url)
        for indice, composta in enumerate(self.mais_compostas(), start=2):
            self.variante(indice, composta)
        self.assertEqual(self.consultas(self.client, url), base)

    def test_the_cart_does_not_pay_per_composite(self):
        def no_carrinho(variante):
            self.client.post(reverse("cart:add"), {
                "product_id": self.product.pk, "option_color": str(variante.color_id),
                "option_size": variante.size, "quantity": 1,
            })

        for variante in self.product.variants.all():
            no_carrinho(variante)
        base = self.consultas(self.client, reverse("cart:detail"))
        for indice, composta in enumerate(self.mais_compostas(), start=2):
            no_carrinho(self.variante(indice, composta))
        self.assertEqual(self.consultas(self.client, reverse("cart:detail")), base)
        self.assertContains(self.client.get(reverse("cart:detail")), "Extra 3 + Roxo")

    def test_the_admin_product_sheet_does_not_pay_per_composite_in_the_catalogue(self):
        self.client.force_login(self.admin)
        paleta = make_product(sku="QUADRO", name="Quadro", category=self.category,
                              price=Decimal("30.00"), variant_sku="QUADRO-V01", color_mode=ColorMode.MULTI)
        for indice, composta in enumerate(self.compostas):
            ProductColor.objects.create(product=paleta, color=composta, sort_order=indice)
        url = reverse("admin:catalog_product_change", args=[paleta.pk])
        base = self.consultas(self.client, url)
        self.mais_compostas()
        self.assertEqual(self.consultas(self.client, url), base)
        self.assertContains(self.client.get(url), 'data-swatch="linear-gradient(90deg')

    def test_the_admin_variant_list_does_not_pay_per_composite(self):
        self.client.force_login(self.admin)
        url = reverse("admin:catalog_productvariant_changelist")
        base = self.consultas(self.client, url)
        for indice, composta in enumerate(self.mais_compostas(), start=2):
            self.variante(indice, composta)
        self.assertEqual(self.consultas(self.client, url), base)
        self.assertContains(self.client.get(url), "Extra 3 + Roxo")
