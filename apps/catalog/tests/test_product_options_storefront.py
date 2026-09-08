"""Etapa 3D — as opções adicionais na loja: página do produto, variante e carrinho.

* a página desenha um grupo por opção adicional (nomes vindos do cadastro,
  na ordem, traduzidos), depois dos eixos fixos, e só quando a opção varia
  e está em todas as variantes ativas;
* o payload leva ``opt-<id>`` por variante, para o JavaScript casar a
  combinação; a ficha técnica tem uma linha por opção;
* o carrinho confere a combinação inteira — eixos fixos + opções — contra a
  variante enviada, e resolve a variante só pelos eixos quando o POST vier
  sem ``variant_id``; combinação inexistente, opção alheia e valor alheio
  são recusados; a variante certa entra no carrinho e o rótulo a mostra;
* produtos sem opções continuam exatamente iguais.
"""

import json
import re
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.cart.cart import CART_SESSION_KEY
from apps.cart.forms import AddToCartForm
from apps.catalog.models import (
    Color,
    Material,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductStatus,
    ProductVariant,
)
from apps.core.testing import LanguageResetMixin, make_category, make_product


def opcao(produto, nome, *valores, sort_order=0, **traducoes):
    o = ProductOption.objects.create(product=produto, name=nome, sort_order=sort_order)
    for idioma, texto in traducoes.items():
        ProductOptionTranslation.objects.create(master=o, language=idioma, name=texto)
    for i, v in enumerate(valores):
        ProductOptionValue.objects.create(option=o, name=v, sort_order=i)
    return o


def valor(o, nome):
    return o.values.get(name=nome)


class Base(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="modelos", name="Modelos")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        self.petg = Material.objects.create(name="PETG")
        self.produto = make_product(sku="SUP", name="Suporte", category=self.categoria, status=ProductStatus.ACTIVE, with_variant=False)

    def variante(self, sku, color=None, size="", material=None, price="10.00", stock=5, choices=None):
        v = ProductVariant.objects.create(
            product=self.produto, sku=sku, color=color, size=size, material=material,
            sale_price=Decimal(price), stock_quantity=stock, weight_grams=Decimal("100"),
        )
        if choices:
            v.set_option_values(choices)
        return v

    def page(self, produto=None, prefixo=""):
        produto = produto or self.produto
        return self.client.get(f"{prefixo}/produtos/{produto.slug}/" if prefixo else produto.get_absolute_url())

    def html(self, **kw):
        return self.page(**kw).content.decode()

    def payload(self, **kw):
        return {p["id"]: p for p in self.page(**kw).context["variant_payload"]}

    def groups(self, **kw):
        return self.page(**kw).context["variant_options"]

    def form(self, **dados):
        dados.setdefault("quantity", "1")
        return AddToCartForm(dados, product=self.produto)

    def add(self, **dados):
        dados.setdefault("product_id", self.produto.pk)
        dados.setdefault("quantity", "1")
        return self.client.post(reverse("cart:add"), dados)

    def cart_items(self):
        return self.client.session.get(CART_SESSION_KEY, {})


# ---------------------------------------------------------------------------
# Sem opções: nada muda
# ---------------------------------------------------------------------------


class NoOptionsTests(Base):
    def test_a_single_variant_page_has_no_groups_and_no_heading(self):
        self.variante("SUP-1")
        html = self.html()
        self.assertNotIn("Opções adicionais", html)
        self.assertNotIn("data-variant-groups", html)
        self.assertIn('name="variant_id"', html)  # o campo oculto de sempre

    def test_fixed_axes_only_keep_their_groups_and_payload(self):
        self.variante("SUP-P", color=self.preto)
        self.variante("SUP-B", color=self.branco)
        grupos = self.groups()
        self.assertEqual([g["key"] for g in grupos], ["color"])
        self.assertNotIn("Opções adicionais", self.html())
        entrada = next(iter(self.payload().values()))
        self.assertEqual(entrada["optionLabels"], {})
        self.assertNotIn("opt-", json.dumps(entrada))
        # o carrinho continua conferindo cor como antes
        preto = self.produto.variants.get(sku="SUP-P")
        self.assertTrue(self.form(variant_id=preto.pk, option_color=str(self.preto.pk)).is_valid())
        self.assertFalse(self.form(variant_id=preto.pk, option_color=str(self.branco.pk)).is_valid())


# ---------------------------------------------------------------------------
# A página
# ---------------------------------------------------------------------------


class PageTests(Base):
    def setUp(self):
        super().setUp()
        self.inst = opcao(self.produto, "Instalação", "Mesa", "Parede", sort_order=2, fr="Installation", nl="Installatie", en="Installation")
        self.acab = opcao(self.produto, "Acabamento", "Fosco", "Brilhante", sort_order=1, fr="Finition")
        for nome, fr, nl, en in (("Mesa", "Table", "Tafel", "Table"), ("Parede", "Mur", "Muur", "Wall"), ("Fosco", "Mat", "Mat", "Matte")):
            v = ProductOptionValue.objects.get(name=nome)
            for idioma, texto in (("fr", fr), ("nl", nl), ("en", en)):
                ProductOptionValueTranslation.objects.create(master=v, language=idioma, name=texto)
        self.v01 = self.variante("SUP-V01", price="10.00", choices={self.inst: valor(self.inst, "Mesa"), self.acab: valor(self.acab, "Fosco")})
        self.v02 = self.variante("SUP-V02", price="12.00", choices={self.inst: valor(self.inst, "Parede"), self.acab: valor(self.acab, "Fosco")})
        self.v03 = self.variante("SUP-V03", price="15.00", stock=0, choices={self.inst: valor(self.inst, "Parede"), self.acab: valor(self.acab, "Brilhante")})

    def test_one_group_per_option_in_the_configured_order_after_the_fixed_axes(self):
        grupos = self.groups()
        self.assertEqual([g["key"] for g in grupos], [f"opt-{self.acab.pk}", f"opt-{self.inst.pk}"])  # Acabamento (1) antes de Instalação (2)
        self.assertEqual([g["label"] for g in grupos], ["Acabamento", "Instalação"])
        self.assertEqual([o["label"] for o in grupos[1]["options"]], ["Mesa", "Parede"])
        self.assertTrue(all(g["kind"] == "option" for g in grupos))

    def test_the_heading_and_the_fieldsets_are_on_the_page(self):
        html = self.html()
        self.assertEqual(html.count("product-opt-heading"), 1)
        self.assertIn("Opções adicionais", html)
        self.assertIn(f'data-variant-group="opt-{self.inst.pk}"', html)
        self.assertIn('data-variant-group-kind="option"', html)
        self.assertIn(f'name="option_opt-{self.inst.pk}"', html)
        self.assertIn("<legend", html.split(f'data-variant-group="opt-{self.inst.pk}"', 1)[1][:300])
        # os nomes das opções não estão escritos no template: vêm do cadastro
        self.assertNotIn("Instalação", open("templates/catalog/product_detail.html", encoding="utf-8").read())

    def test_the_payload_carries_the_choice_and_the_label_of_each_option(self):
        p = self.payload()
        k_inst, k_acab = f"opt-{self.inst.pk}", f"opt-{self.acab.pk}"
        self.assertEqual(p[self.v01.pk][k_inst], str(valor(self.inst, "Mesa").pk))
        self.assertEqual(p[self.v03.pk][k_acab], str(valor(self.acab, "Brilhante").pk))
        self.assertEqual(p[self.v02.pk]["optionLabels"], {k_inst: "Parede", k_acab: "Fosco"})
        self.assertEqual(p[self.v02.pk]["label"], "Fosco · Parede")  # ordem das opções
        self.assertFalse(p[self.v03.pk]["available"])

    def test_the_spec_sheet_has_one_row_per_option(self):
        specs = {k: (r, v) for k, r, v in self.page().context["specifications"]}
        self.assertEqual(specs[f"opt-{self.inst.pk}"], ("Instalação", "Mesa"))
        self.assertEqual(specs[f"opt-{self.acab.pk}"], ("Acabamento", "Fosco"))
        self.assertIn(f'data-spec="opt-{self.inst.pk}"', self.html())

    def test_an_option_without_variation_or_not_in_every_variant_is_not_a_group(self):
        modelo = opcao(self.produto, "Modelo", "Simples", "Decorado", sort_order=3)
        self.v01.set_option_values({modelo: valor(modelo, "Simples")})  # só uma variante
        chaves = [g["key"] for g in self.groups()]
        self.assertNotIn(f"opt-{modelo.pk}", chaves)
        for v in (self.v02, self.v03):
            v.set_option_values({modelo: valor(modelo, "Simples")})  # todas, mas não varia
        self.assertNotIn(f"opt-{modelo.pk}", [g["key"] for g in self.groups()])
        self.v03.set_option_values({modelo: valor(modelo, "Decorado")})  # agora varia e está em todas
        self.assertIn(f"opt-{modelo.pk}", [g["key"] for g in self.groups()])
        # o `<select>` continua listando todas as variantes, com o rótulo completo
        self.assertIn("Brilhante · Parede · Decorado", self.html())  # v03, opções na ordem do cadastro

    def test_translations_follow_the_store_language(self):
        esperado = {"/fr": ("Installation", "Mur", "Finition"), "/nl": ("Installatie", "Muur", "Acabamento"), "/en": ("Installation", "Wall", "Acabamento")}
        for prefixo, (opcao_, valor_, fallback) in esperado.items():
            with self.subTest(idioma=prefixo):
                html = self.html(prefixo=prefixo)
                for texto in (opcao_, valor_, fallback):
                    self.assertIn(texto, html)
        html = self.html(prefixo="/fr")
        self.assertIn("Options supplémentaires", html)

    def test_the_page_does_not_cost_a_query_per_option_or_variant(self):
        with CaptureQueriesContext(connection) as poucas:
            self.page()
        modelo = opcao(self.produto, "Modelo", "Simples", "Decorado", "Luxo", sort_order=3)
        cor = opcao(self.produto, "Cor da base", "Natural", "Escura", sort_order=4)
        combinacoes = (("Simples", "Natural"), ("Simples", "Escura"), ("Luxo", "Natural"), ("Decorado", "Escura"))
        for numero, (m, c) in enumerate(combinacoes):
            self.variante(f"SUP-X{numero}", price="9.00", choices={
                self.inst: valor(self.inst, "Mesa"), self.acab: valor(self.acab, "Brilhante"),
                modelo: valor(modelo, m), cor: valor(cor, c),
            })
        with CaptureQueriesContext(connection) as muitas:
            self.page()
        self.assertEqual(len(poucas), len(muitas))


# ---------------------------------------------------------------------------
# A matriz e o carrinho
# ---------------------------------------------------------------------------


class MatrixTests(Base):
    """V01 = Mesa + Fosco, V02 = Parede + Fosco, V03 = Parede + Brilhante; não existe Mesa + Brilhante."""

    def setUp(self):
        super().setUp()
        self.inst = opcao(self.produto, "Instalação", "Mesa", "Parede", sort_order=1)
        self.acab = opcao(self.produto, "Acabamento", "Fosco", "Brilhante", sort_order=2)
        self.mesa, self.parede = valor(self.inst, "Mesa"), valor(self.inst, "Parede")
        self.fosco, self.brilhante = valor(self.acab, "Fosco"), valor(self.acab, "Brilhante")
        self.v01 = self.variante("SUP-V01", choices={self.inst: self.mesa, self.acab: self.fosco})
        self.v02 = self.variante("SUP-V02", choices={self.inst: self.parede, self.acab: self.fosco})
        self.v03 = self.variante("SUP-V03", stock=0, choices={self.inst: self.parede, self.acab: self.brilhante})
        self.k_inst, self.k_acab = f"option_opt-{self.inst.pk}", f"option_opt-{self.acab.pk}"

    def escolha(self, inst, acab, **extra):
        return {self.k_inst: str(inst.pk), self.k_acab: str(acab.pk), **extra}

    def test_the_three_valid_combinations_resolve_without_variant_id(self):
        for inst, acab, esperada in ((self.mesa, self.fosco, self.v01), (self.parede, self.fosco, self.v02), (self.parede, self.brilhante, self.v03)):
            with self.subTest(variante=esperada.sku):
                form = self.form(**self.escolha(inst, acab))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.variant, esperada)

    def test_the_combination_that_does_not_exist_is_refused(self):
        form = self.form(**self.escolha(self.mesa, self.brilhante))
        self.assertFalse(form.is_valid())
        self.assertIn("variant_id", form.errors)

    def test_a_partial_choice_is_refused_when_more_than_one_variant_matches(self):
        form = self.form(**{self.k_inst: str(self.parede.pk)})  # Parede: V02 ou V03
        self.assertFalse(form.is_valid())
        self.assertIn("variant_id", form.errors)
        self.assertEqual(self.produto.variants.count(), 3)  # nada foi «adivinhado»

    def test_the_variant_id_must_agree_with_the_options(self):
        self.assertTrue(self.form(variant_id=self.v01.pk, **self.escolha(self.mesa, self.fosco)).is_valid())
        form = self.form(variant_id=self.v01.pk, **self.escolha(self.parede, self.fosco))
        self.assertFalse(form.is_valid())
        self.assertIn("Esta combinação não está disponível", str(form.errors))

    def test_a_variant_without_a_choice_is_not_the_one_asked_with_it(self):
        sem = self.variante("SUP-SEM")  # sem escolha nenhuma
        form = self.form(**{self.k_inst: str(self.mesa.pk)})  # só Mesa: só V01 tem Mesa... e Fosco
        # V01 tem Mesa+Fosco, o POST pediu só Mesa: não é «a» combinação -> falta escolher
        self.assertFalse(form.is_valid())
        form = self.form(variant_id=sem.pk, **{self.k_inst: str(self.mesa.pk)})
        self.assertFalse(form.is_valid())  # a variante sem escolha não é «Mesa»
        self.assertTrue(self.form(variant_id=sem.pk).is_valid())

    def test_the_variant_id_alone_still_works(self):
        form = self.form(variant_id=self.v02.pk)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant, self.v02)

    def test_the_right_variant_enters_the_cart_and_two_choices_are_two_lines(self):
        self.add(variant_id=self.v01.pk, **self.escolha(self.mesa, self.fosco))
        self.add(variant_id=self.v02.pk, **self.escolha(self.parede, self.fosco))
        itens = self.cart_items()
        self.assertEqual(sorted(i["variant_id"] for i in itens.values()), sorted([self.v01.pk, self.v02.pk]))
        self.assertEqual(len(itens), 2)
        html = self.client.get(reverse("cart:detail")).content.decode()
        painel = html.split("cart-items", 1)[1].split("cart-aside", 1)[0] if "cart-items" in html else html
        chips = re.findall(r'<li class="cart-chip">\s*(.*?)\s*</li>', painel, re.S)
        self.assertEqual(chips.count("Mesa"), 1)
        self.assertEqual(chips.count("Parede"), 1)
        self.assertEqual(chips.count("Fosco"), 2)
        gaveta = self.client.get(reverse("cart:drawer")).content.decode()
        self.assertIn("Parede · Fosco", gaveta)
        self.assertIn("Mesa · Fosco", gaveta)

    def test_an_impossible_combination_never_enters_the_cart(self):
        self.add(**self.escolha(self.mesa, self.brilhante))
        self.assertEqual(self.cart_items(), {})
        self.add(variant_id=self.v01.pk, **self.escolha(self.mesa, self.brilhante))
        self.assertEqual(self.cart_items(), {})

    def test_stock_still_rules(self):
        self.add(variant_id=self.v03.pk, **self.escolha(self.parede, self.brilhante))  # esgotada
        self.assertEqual(self.cart_items(), {})
        self.add(variant_id=self.v02.pk, **self.escolha(self.parede, self.fosco))
        self.assertEqual(len(self.cart_items()), 1)

    def test_the_cart_line_price_is_the_variant_price_whatever_the_post_says(self):
        self.add(variant_id=self.v02.pk, price="0.01", sale_price="0.01", **self.escolha(self.parede, self.fosco))
        linha = next(iter(self.cart_items().values()))
        self.assertEqual(linha["variant_id"], self.v02.pk)
        html = self.client.get(reverse("cart:detail")).content.decode()
        self.assertIn("10,00", html)
        self.assertNotIn("0,01", html)


class SecurityTests(Base):
    def setUp(self):
        super().setUp()
        self.inst = opcao(self.produto, "Instalação", "Mesa", "Parede")
        self.acab = opcao(self.produto, "Acabamento", "Fosco")
        self.v01 = self.variante("SUP-V01", choices={self.inst: valor(self.inst, "Mesa")})
        self.v02 = self.variante("SUP-V02", choices={self.inst: valor(self.inst, "Parede")})
        self.outro = make_product(sku="OUTRO", name="Outro", category=self.categoria, status=ProductStatus.ACTIVE, price=Decimal("5"), stock_quantity=5)
        self.alheia = opcao(self.outro, "Instalação", "Teto")
        self.k = f"option_opt-{self.inst.pk}"

    def test_an_option_of_another_product_is_refused(self):
        form = self.form(variant_id=self.v01.pk, **{f"option_opt-{self.alheia.pk}": str(valor(self.alheia, "Teto").pk)})
        self.assertFalse(form.is_valid())
        form = self.form(**{f"option_opt-{self.alheia.pk}": str(valor(self.alheia, "Teto").pk)})
        self.assertFalse(form.is_valid())

    def test_a_value_of_another_option_or_product_is_refused(self):
        for estranho in (valor(self.acab, "Fosco").pk, valor(self.alheia, "Teto").pk, 999999):
            with self.subTest(valor=estranho):
                self.assertFalse(self.form(variant_id=self.v01.pk, **{self.k: str(estranho)}).is_valid())
                self.assertFalse(self.form(**{self.k: str(estranho)}).is_valid())

    def test_a_variant_of_another_product_is_refused(self):
        alheia = self.outro.default_variant
        form = self.form(variant_id=alheia.pk, **{self.k: str(valor(self.inst, "Mesa").pk)})
        self.assertFalse(form.is_valid())
        self.add(variant_id=alheia.pk, **{self.k: str(valor(self.inst, "Mesa").pk)})
        self.assertEqual(self.cart_items(), {})

    def test_garbage_in_the_option_fields_is_refused_not_ignored(self):
        for lixo in ("abc", "1 OR 1=1", "-1"):
            with self.subTest(lixo=lixo):
                self.assertFalse(self.form(variant_id=self.v01.pk, **{self.k: lixo}).is_valid())
        self.assertFalse(self.form(variant_id=self.v01.pk, **{"option_opt-abc": "1"}).is_valid())

    def test_an_inactive_product_or_variant_cannot_be_bought(self):
        self.v01.is_active = False
        self.v01.save()
        self.add(variant_id=self.v01.pk, **{self.k: str(valor(self.inst, "Mesa").pk)})
        self.assertEqual(self.cart_items(), {})
        self.produto.status = ProductStatus.DRAFT
        self.produto.save()
        self.add(variant_id=self.v02.pk, **{self.k: str(valor(self.inst, "Parede").pk)})
        self.assertEqual(self.cart_items(), {})
        self.assertEqual(self.page().status_code, 404)


# ---------------------------------------------------------------------------
# Eixos fixos + opções
# ---------------------------------------------------------------------------


class MixedAxesTests(Base):
    def test_every_mix_of_fixed_axes_and_options_resolves(self):
        inst = opcao(self.produto, "Instalação", "Mesa", "Parede", sort_order=1)
        acab = opcao(self.produto, "Acabamento", "Fosco", "Brilhante", sort_order=2)
        modelo = opcao(self.produto, "Modelo", "Simples", "Decorado", sort_order=3)
        mesa, parede = valor(inst, "Mesa"), valor(inst, "Parede")
        casos = {
            "cor+opção": dict(color=self.preto),
            "tamanho+opção": dict(size="25 cm"),
            "material+opção": dict(material=self.pla),
            "cor+tamanho+material+opção": dict(color=self.preto, size="25 cm", material=self.pla),
        }
        for nome, eixos in casos.items():
            with self.subTest(caso=nome):
                a = self.variante(f"{nome}-A"[:30], choices={inst: mesa}, **eixos)
                b = self.variante(f"{nome}-B"[:30], choices={inst: parede}, **eixos)
                pedido = {f"option_opt-{inst.pk}": str(parede.pk)}
                if "color" in eixos:
                    pedido["option_color"] = str(self.preto.pk)
                if "size" in eixos:
                    pedido["option_size"] = "25 cm"
                if "material" in eixos:
                    pedido["option_material"] = str(self.pla.pk)
                form = self.form(**pedido)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.variant, b)
                self.assertIn(f"opt-{inst.pk}", [g["key"] for g in self.groups()])
                a.delete()
                b.delete()

        # cor + tamanho + material + 2 e 3 opções
        for n, extras in ((2, {acab: valor(acab, "Fosco")}), (3, {acab: valor(acab, "Fosco"), modelo: valor(modelo, "Decorado")})):
            with self.subTest(opcoes=n):
                a = self.variante(f"TRI-{n}-A", color=self.preto, size="25 cm", material=self.pla, choices={inst: mesa, **extras})
                b = self.variante(f"TRI-{n}-B", color=self.branco, size="25 cm", material=self.pla, choices={inst: parede, **extras})
                pedido = {
                    "option_color": str(self.branco.pk), "option_size": "25 cm", "option_material": str(self.pla.pk),
                    f"option_opt-{inst.pk}": str(parede.pk),
                    **{f"option_opt-{o.pk}": str(v.pk) for o, v in extras.items()},
                }
                form = self.form(**pedido)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.variant, b)
                self.assertEqual(ProductVariant.objects.get(pk=b.pk).label.split(" · ")[:3], ["Branco", "25 cm", "PLA"])
                a.delete()
                b.delete()

    def test_the_groups_keep_the_fixed_axes_first(self):
        inst = opcao(self.produto, "Instalação", "Mesa", "Parede")
        self.variante("A", color=self.preto, choices={inst: valor(inst, "Mesa")})
        self.variante("B", color=self.branco, choices={inst: valor(inst, "Parede")})
        self.assertEqual([g["key"] for g in self.groups()], ["color", f"opt-{inst.pk}"])
        html = self.html()
        self.assertLess(html.index('data-variant-group="color"'), html.index("product-opt-heading"))
