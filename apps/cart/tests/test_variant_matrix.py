"""A combinação de eixos precisa existir — e quem decide é o servidor.

O cenário de todos os testes é o do enunciado: três variantes que **não** formam
uma grade completa.

    Preto  / 25 cm
    Branco / 30 cm
    Roxo   / 25 cm

Cor e tamanho variam, então a página desenha os dois seletores. Mas
``Branco + 25 cm`` não existe: nenhuma variante tem essa combinação. O
JavaScript desabilita o botão; estes testes provam que, mesmo sem JavaScript
nenhum — um POST montado à mão, um script, um cliente curioso — o servidor
recusa a compra em vez de vender a variante "mais próxima".
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import CART_SESSION_KEY
from apps.cart.forms import AddToCartForm
from apps.catalog.models import Color, Material, ProductStatus, ProductVariant
from apps.core.testing import LanguageResetMixin, make_category, make_product


class MatrixBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.roxo = Color.objects.create(name="Roxo", hex_code="#6D2CE0")
        self.pla = Material.objects.create(name="PLA")

        self.product = make_product(
            sku="DINO",
            name="Dinossauro",
            category=self.category,
            status=ProductStatus.ACTIVE,
            with_variant=False,
        )
        self.preto25 = self.variant("DINO-PRETO-25", self.preto, "25 cm", "27.90", "150", 2, 10)
        self.branco30 = self.variant("DINO-BRANCO-30", self.branco, "30 cm", "32.90", "300", 3, 5)
        self.roxo25 = self.variant("DINO-ROXO-25", self.roxo, "25 cm", "21.90", "220", 4, 0)
        self.product.refresh_from_db()

    def variant(self, sku, color, size, price, weight, days, stock):
        return ProductVariant.objects.create(
            product=self.product,
            sku=sku,
            color=color,
            material=self.pla,
            size=size,
            sale_price=Decimal(price),
            weight_grams=Decimal(weight),
            production_lead_time_days=days,
            stock_quantity=stock,
        )

    def build(self, **dados):
        dados.setdefault("quantity", "1")
        return AddToCartForm(dados, product=self.product)


class ValidCombinationTests(MatrixBase):
    def test_variant_id_alone_is_enough(self):
        form = self.build(variant_id=self.preto25.pk)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant, self.preto25)

    def test_axes_matching_the_variant_are_accepted(self):
        form = self.build(
            variant_id=self.preto25.pk,
            option_color=str(self.preto.pk),
            option_size="25 cm",
            option_material=str(self.pla.pk),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant, self.preto25)

    def test_axes_alone_resolve_to_exactly_one_variant(self):
        """Sem `variant_id`, a combinação é resolvida aqui — e tem que ser única."""
        form = self.build(option_color=str(self.branco.pk), option_size="30 cm")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant, self.branco30)

    def test_the_order_of_the_axes_does_not_matter(self):
        """Cor→tamanho e tamanho→cor levam à mesma variante."""
        cor_primeiro = self.build(option_color=str(self.branco.pk), option_size="30 cm")
        tamanho_primeiro = self.build(option_size="30 cm", option_color=str(self.branco.pk))

        self.assertTrue(cor_primeiro.is_valid(), cor_primeiro.errors)
        self.assertTrue(tamanho_primeiro.is_valid(), tamanho_primeiro.errors)
        self.assertEqual(cor_primeiro.variant, tamanho_primeiro.variant)

    def test_each_size_resolves_with_its_own_colour(self):
        """Os dois caminhos do enunciado, um de cada lado."""
        do_branco = self.build(option_color=str(self.branco.pk), option_size="30 cm")
        do_trinta = self.build(option_size="30 cm", option_color=str(self.branco.pk))

        self.assertTrue(do_branco.is_valid())
        self.assertTrue(do_trinta.is_valid())
        self.assertEqual(do_branco.variant.sku, "DINO-BRANCO-30")


class ForgedCombinationTests(MatrixBase):
    """POST montado à mão. O JavaScript não participa."""

    def test_combination_that_does_not_exist_is_refused(self):
        """Branco só existe em 30 cm. Branco + 25 cm não é venda nenhuma."""
        form = self.build(option_color=str(self.branco.pk), option_size="25 cm")

        self.assertFalse(form.is_valid())
        self.assertIn("variant_id", form.errors)

    def test_nothing_is_sold_when_the_combination_does_not_exist(self):
        """Nem a variante mais próxima, nem a primeira, nem o produto base."""
        form = self.build(option_color=str(self.branco.pk), option_size="25 cm")
        form.is_valid()

        self.assertIsNone(form.variant)

    def test_axes_that_disagree_with_the_variant_id_are_refused(self):
        """`variant_id` diz Preto/25; os eixos dizem Branco. Um dos dois mente."""
        form = self.build(
            variant_id=self.preto25.pk,
            option_color=str(self.branco.pk),
            option_size="25 cm",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("variant_id", form.errors)

    def test_a_partial_choice_is_refused(self):
        """Só a cor Preto ainda casa com uma variante; só o tamanho 25 cm, com duas."""
        form = self.build(option_size="25 cm")

        self.assertFalse(form.is_valid())
        self.assertIn("variant_id", form.errors)

    def test_a_variant_of_another_product_is_refused(self):
        outro = make_product(sku="OUTRO", name="Outro", with_variant=True)
        form = self.build(variant_id=outro.default_variant.pk)

        self.assertFalse(form.is_valid())

    def test_an_inactive_variant_is_refused(self):
        self.branco30.is_active = False
        self.branco30.save()

        form = self.build(variant_id=self.branco30.pk)
        self.assertFalse(form.is_valid())

    def test_an_unknown_size_is_refused(self):
        form = self.build(option_color=str(self.preto.pk), option_size="99 cm")

        self.assertFalse(form.is_valid())

    def test_an_unknown_colour_is_refused(self):
        outra = Color.objects.create(name="Turquesa", hex_code="#00CED1")
        form = self.build(option_color=str(outra.pk), option_size="25 cm")

        self.assertFalse(form.is_valid())


class ForgedPostThroughTheViewTests(MatrixBase):
    """O mesmo, pela URL — é por ela que um POST forjado chega de verdade."""

    def add(self, **dados):
        dados.setdefault("product_id", self.product.pk)
        dados.setdefault("quantity", "1")
        return self.client.post(reverse("cart:add"), dados)

    def cart_items(self):
        return self.client.session.get(CART_SESSION_KEY, {})

    def test_a_valid_combination_enters_the_cart(self):
        self.add(option_color=str(self.branco.pk), option_size="30 cm")

        itens = list(self.cart_items().values())
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["variant_id"], self.branco30.pk)

    def test_an_impossible_combination_never_enters_the_cart(self):
        self.add(option_color=str(self.branco.pk), option_size="25 cm")

        self.assertEqual(self.cart_items(), {})

    def test_disagreeing_axes_never_enter_the_cart(self):
        self.add(
            variant_id=self.preto25.pk,
            option_color=str(self.branco.pk),
            option_size="25 cm",
        )

        self.assertEqual(self.cart_items(), {})

    def test_the_customer_is_told_why(self):
        """A recusa vira mensagem, não um silêncio."""
        resposta = self.client.post(
            reverse("cart:add"),
            {
                "product_id": self.product.pk,
                "option_color": str(self.branco.pk),
                "option_size": "25 cm",
                "quantity": "1",
            },
            follow=True,
        )

        self.assertContains(resposta, "combinação não está disponível")


class ChosenVariantDataTests(MatrixBase):
    """A variante escolhida traz os dados dela — nunca os da vizinha."""

    def add_and_line(self, **dados):
        dados.setdefault("product_id", self.product.pk)
        dados.setdefault("quantity", "1")
        self.client.post(reverse("cart:add"), dados)
        resposta = self.client.get(reverse("cart:detail"))
        return resposta.context["cart"].lines()[0]

    def test_price_is_the_chosen_variants(self):
        linha = self.add_and_line(option_color=str(self.branco.pk), option_size="30 cm")

        self.assertEqual(linha.unit_price, Decimal("32.90"))

    def test_weight_is_the_chosen_variants(self):
        linha = self.add_and_line(option_color=str(self.branco.pk), option_size="30 cm")

        self.assertEqual(linha.unit_weight_grams, 300)

    def test_production_days_are_the_chosen_variants(self):
        linha = self.add_and_line(option_color=str(self.branco.pk), option_size="30 cm")

        self.assertEqual(linha.production_days, 3)

    def test_stock_ceiling_is_the_chosen_variants(self):
        """Branco tem 5 em estoque; pedir 9 traz 5, não os 10 do Preto."""
        self.client.post(
            reverse("cart:add"),
            {
                "product_id": self.product.pk,
                "option_color": str(self.branco.pk),
                "option_size": "30 cm",
                "quantity": "9",
            },
        )
        resposta = self.client.get(reverse("cart:detail"))

        self.assertEqual(resposta.context["cart"].lines()[0].quantity, 5)

    def test_an_out_of_stock_combination_is_refused(self):
        """Roxo/25 existe no catálogo, mas está esgotado."""
        self.client.post(
            reverse("cart:add"),
            {
                "product_id": self.product.pk,
                "option_color": str(self.roxo.pk),
                "option_size": "25 cm",
                "quantity": "1",
            },
        )

        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})


class ProductPageOptionsTests(MatrixBase):
    """A página entrega ao JavaScript a matriz inteira, não uma lista por eixo."""

    def get(self):
        return self.client.get(self.product.get_absolute_url())

    def test_both_axes_are_offered(self):
        chaves = [grupo["key"] for grupo in self.get().context["variant_options"]]

        self.assertIn("color", chaves)
        self.assertIn("size", chaves)

    def test_the_material_axis_is_not_offered_because_it_does_not_vary(self):
        """Todas as variantes são PLA: um seletor de uma opção só é ruído."""
        chaves = [grupo["key"] for grupo in self.get().context["variant_options"]]

        self.assertNotIn("material", chaves)

    def test_every_option_of_every_axis_is_listed(self):
        """Nenhuma cor some por não combinar com o tamanho aberto."""
        grupos = {g["key"]: g for g in self.get().context["variant_options"]}
        cores = [o["label"] for o in grupos["color"]["options"]]
        tamanhos = [o["label"] for o in grupos["size"]["options"]]

        self.assertEqual(sorted(cores), ["Branco", "Preto", "Roxo"])
        self.assertEqual(sorted(tamanhos), ["25 cm", "30 cm"])

    def test_the_payload_carries_the_axes_of_each_variant(self):
        """É deste mapa que o JavaScript deduz o que é possível."""
        payload = {item["sku"]: item for item in self.get().context["variant_payload"]}

        self.assertEqual(payload["DINO-BRANCO-30"]["color"], str(self.branco.pk))
        self.assertEqual(payload["DINO-BRANCO-30"]["size"], "30 cm")
        self.assertEqual(payload["DINO-PRETO-25"]["size"], "25 cm")

    def test_the_payload_has_one_entry_per_variant(self):
        self.assertEqual(len(self.get().context["variant_payload"]), 3)

    def test_the_buttons_start_hidden_for_visitors_without_javascript(self):
        """Sem JavaScript o `<select>` é a interface — e ele só lista o que existe."""
        resposta = self.get()

        self.assertContains(resposta, "data-variant-groups")
        self.assertContains(resposta, 'name="variant_id"')

    def test_the_select_lists_only_real_variants(self):
        """A prova de que o caminho sem JavaScript não monta combinação inexistente."""
        html = self.get().content.decode()
        select = html.split("<select ")[1].split("</select>")[0]

        self.assertIn("data-variant-select", select)
        self.assertEqual(select.count("<option "), 3)


class PartiallyFilledAxisTests(MatrixBase):
    """Eixo preenchido pela metade não vira botão.

    Se virasse, a variante sem aquele eixo não teria botão que a alcançasse — e
    o ``<select>`` que a alcançaria está escondido quando há botões. Ela
    existiria no catálogo e não haveria clique que a vendesse.
    """

    def setUp(self):
        super().setUp()
        self.sem_cor = ProductVariant.objects.create(
            product=self.product,
            sku="DINO-SEM-COR",
            material=self.pla,
            size="15 cm",
            sale_price=Decimal("15.90"),
            weight_grams=Decimal("90"),
            stock_quantity=3,
        )
        self.product.refresh_from_db()

    def get(self):
        return self.client.get(self.product.get_absolute_url())

    def test_the_colour_axis_is_dropped(self):
        chaves = [grupo["key"] for grupo in self.get().context["variant_options"]]

        self.assertNotIn("color", chaves)

    def test_the_size_axis_survives_because_every_variant_has_one(self):
        chaves = [grupo["key"] for grupo in self.get().context["variant_options"]]

        self.assertIn("size", chaves)

    def test_the_select_still_reaches_every_variant(self):
        html = self.get().content.decode()
        select = html.split("<select ")[1].split("</select>")[0]

        self.assertEqual(select.count("<option "), 4)
        self.assertIn("DINO-SEM-COR", [v.sku for v in self.product.active_variants()])

    def test_the_colourless_variant_can_still_be_bought(self):
        form = self.build(variant_id=self.sem_cor.pk)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant, self.sem_cor)


class ClickableUnavailableOptionsTests(MatrixBase):
    """A opção que não combina fica riscada — não desabilitada.

    O comportamento é do JavaScript, e é no navegador que ele se prova. O que
    dá para cobrar aqui é o que o servidor entrega a ele: as opções todas no
    DOM, sem `disabled`, e o mapa de variantes com o que o cálculo precisa.
    """

    def get(self):
        return self.client.get(self.product.get_absolute_url())

    def test_no_option_button_comes_disabled_from_the_server(self):
        html = self.get().content.decode()
        botoes = html.split("data-variant-groups")[1].split("data-variant-message")[0]

        self.assertIn("data-variant-option", botoes)
        self.assertNotIn("disabled", botoes)

    def test_the_javascript_never_disables_an_option(self):
        """O guarda contra a regra antiga voltar sem ninguém notar."""
        from pathlib import Path

        from django.conf import settings

        fonte = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text(
            encoding="utf-8"
        )
        bloco = fonte.split("Seletor de variantes")[1].split("Personalização")[0]

        self.assertNotIn("input.disabled = !", bloco)
        self.assertIn("input.disabled = false", bloco)

    def test_the_javascript_marks_the_option_instead(self):
        from pathlib import Path

        from django.conf import settings

        fonte = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("variant-option-unavailable", fonte)
        self.assertIn("aria-disabled", fonte)

    def test_the_javascript_picks_the_best_real_variant(self):
        """A troca automática existe e é feita sobre as variantes reais."""
        from pathlib import Path

        from django.conf import settings

        fonte = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function bestVariant(", fonte)
        self.assertIn("function fitsCurrent(", fonte)

    def test_the_stylesheet_keeps_the_option_clickable(self):
        """Riscada e mais fraca, mas sem `cursor: not-allowed`."""
        from pathlib import Path

        from django.conf import settings

        css = (Path(settings.BASE_DIR) / "static" / "src" / "input.css").read_text(
            encoding="utf-8"
        )
        regra = css.split(".variant-option-unavailable {")[1].split("}")[0]

        self.assertIn("line-through", regra)
        self.assertNotIn("cursor-not-allowed", regra)

    def test_the_payload_carries_what_the_switch_needs(self):
        """`available` é o critério de desempate quando duas variantes servem."""
        payload = {item["sku"]: item for item in self.get().context["variant_payload"]}

        self.assertIn("available", payload["DINO-PRETO-25"])
        self.assertTrue(payload["DINO-PRETO-25"]["available"])
        self.assertFalse(payload["DINO-ROXO-25"]["available"])  # esgotada

    def test_the_payload_carries_every_axis_of_every_variant(self):
        """É deste mapa que o JavaScript deduz a troca automática."""
        for item in self.get().context["variant_payload"]:
            with self.subTest(sku=item["sku"]):
                self.assertIn("color", item)
                self.assertIn("size", item)
                self.assertIn("material", item)
                self.assertIn("id", item)

    def test_the_payload_carries_everything_the_screen_shows(self):
        """Trocar de variante troca preço, estoque, peso, prazo e ficha."""
        item = next(
            i
            for i in self.get().context["variant_payload"]
            if i["sku"] == "DINO-BRANCO-30"
        )

        self.assertEqual(item["priceDisplay"], "€ 32,90")
        self.assertEqual(item["stock"], 5)
        self.assertEqual(item["weightGrams"], "300")
        self.assertEqual(item["productionDays"], 3)
        self.assertEqual(item["maxQuantity"], 5)

    def test_the_switch_hint_is_offered_to_the_javascript(self):
        self.assertContains(self.get(), "data-switch-label")


class ThreeAxisMatrixTests(LanguageResetMixin, TestCase):
    """A lógica não pode ser específica de cor × tamanho.

    Aqui há três eixos que variam. O servidor precisa oferecer os três e
    aceitar a resolução por eixos nos três — é isso que faz a regra valer para
    um "acabamento" que venha depois.
    """

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        self.petg = Material.objects.create(name="PETG")

        self.product = make_product(
            sku="TRI",
            name="Três eixos",
            category=self.category,
            status=ProductStatus.ACTIVE,
            with_variant=False,
        )
        for sku, cor, tamanho, material, preco in (
            ("TRI-A", self.preto, "25 cm", self.pla, "10.00"),
            ("TRI-B", self.preto, "25 cm", self.petg, "12.00"),
            ("TRI-C", self.branco, "30 cm", self.pla, "14.00"),
        ):
            ProductVariant.objects.create(
                product=self.product,
                sku=sku,
                color=cor,
                size=tamanho,
                material=material,
                sale_price=Decimal(preco),
                weight_grams=Decimal("100"),
                stock_quantity=5,
            )
        self.product.refresh_from_db()

    def get(self):
        return self.client.get(self.product.get_absolute_url())

    def build(self, **dados):
        dados.setdefault("quantity", "1")
        return AddToCartForm(dados, product=self.product)

    def test_the_three_axes_are_offered(self):
        chaves = [grupo["key"] for grupo in self.get().context["variant_options"]]

        self.assertEqual(sorted(chaves), ["color", "material", "size"])

    def test_every_option_of_every_axis_is_listed(self):
        grupos = {g["key"]: g for g in self.get().context["variant_options"]}

        self.assertEqual(len(grupos["color"]["options"]), 2)
        self.assertEqual(len(grupos["size"]["options"]), 2)
        self.assertEqual(len(grupos["material"]["options"]), 2)

    def test_the_payload_carries_the_three_axes(self):
        payload = {item["sku"]: item for item in self.get().context["variant_payload"]}

        self.assertEqual(payload["TRI-B"]["color"], str(self.preto.pk))
        self.assertEqual(payload["TRI-B"]["size"], "25 cm")
        self.assertEqual(payload["TRI-B"]["material"], str(self.petg.pk))

    def test_a_complete_three_axis_combination_resolves(self):
        form = self.build(
            option_color=str(self.preto.pk),
            option_size="25 cm",
            option_material=str(self.petg.pk),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant.sku, "TRI-B")

    def test_a_partial_three_axis_choice_is_refused(self):
        """Preto sozinho ainda casa com duas variantes: falta escolher."""
        form = self.build(option_color=str(self.preto.pk))

        self.assertFalse(form.is_valid())

    def test_an_impossible_three_axis_combination_is_refused(self):
        """Branco só existe em 30 cm com PLA."""
        form = self.build(
            option_color=str(self.branco.pk),
            option_size="30 cm",
            option_material=str(self.petg.pk),
        )

        self.assertFalse(form.is_valid())

    def test_the_material_axis_alone_can_identify_a_variant(self):
        """PETG só existe numa combinação: o eixo material resolve sozinho."""
        form = self.build(option_material=str(self.petg.pk))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant.sku, "TRI-B")

    def test_no_option_of_the_three_axes_comes_disabled(self):
        html = self.get().content.decode()
        botoes = html.split("data-variant-groups")[1].split("data-variant-message")[0]

        self.assertNotIn("disabled", botoes)
