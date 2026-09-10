"""«Cores à escolha do cliente» no carrinho: formulário, preço, identidade e merge.

A escolha é uma camada ACIMA da variante: entra na identidade da linha, soma
o adicional ao preço da variante e não toca no estoque. Quem decide é sempre o
servidor — o navegador só manda o id da cor.
"""

from decimal import Decimal

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.cart.cart import CART_SESSION_KEY, Cart
from apps.cart.forms import AddToCartForm
from apps.cart.keys import line_key
from apps.cart.models import CartItem
from apps.catalog.models import (
    Color,
    ColorMode,
    PersonalizationType,
    ProductColor,
    ProductVariant,
)
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_user
from apps.orders import services

ADD = "cart:add"


class PortaRetratoBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="decoracao", name="Decoração")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.dourado = Color.objects.create(name="Dourado", hex_code="#D4AF37")

        self.product = make_product(
            sku="PORTA", name="Porta-Retrato Litofânico", category=self.category,
            with_variant=False, color_mode=ColorMode.CUSTOM,
        )
        self.simples = self.variante("PORTA-V01", "Porta-Retrato", "15.00")
        self.com_foto = self.variante("PORTA-V02", "Porta-Retrato + Foto", "20.00")

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

    # -- ajudantes ---------------------------------------------------------

    def form(self, **dados):
        dados.setdefault("quantity", "1")
        return AddToCartForm(dados, product=type(self.product).objects.get(pk=self.product.pk))

    def add(self, variante, cor=None, quantity=1, **extra):
        dados = {"product_id": self.product.pk, "variant_id": variante.pk, "quantity": quantity, **extra}
        if cor is not None:
            dados["choice_color"] = cor.pk
        return self.client.post(reverse(ADD), dados)

    def cart(self, user=None):
        request = RequestFactory().get("/")
        request.session = self.client.session
        request.user = user or AnonymousUser()
        return Cart(request)

    def lines(self, user=None):
        return self.cart(user).lines()


# ---------------------------------------------------------------------------
# 1. O formulário
# ---------------------------------------------------------------------------


class FormTests(PortaRetratoBase):
    def test_a_valid_colour_is_accepted_with_the_delta_from_the_database(self):
        form = self.form(variant_id=self.com_foto.pk, choice_color=str(self.row_dourado.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.variant, self.com_foto)
        self.assertEqual(form.raw_choices, {"color": self.row_dourado.pk})
        self.assertEqual(form.choices[0].price_delta, Decimal("2.00"))

    def test_the_colour_is_required_when_the_product_offers_it(self):
        form = self.form(variant_id=self.com_foto.pk)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.error_message, "Escolha uma cor antes de continuar.")

    def test_an_id_outside_the_palette_is_refused(self):
        for bruto in ("999999", "abc", str(self.dourado.pk + 5000)):
            with self.subTest(valor=bruto):
                form = self.form(variant_id=self.com_foto.pk, choice_color=bruto)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.error_message, "Esta cor não está disponível.")

    def test_the_palette_of_another_product_is_refused(self):
        outro = make_product(sku="OUTRO", name="Outro", category=self.category, with_variant=False,
                             color_mode=ColorMode.CUSTOM)
        alheia = ProductColor.objects.create(product=outro, color=self.dourado, price_delta=Decimal("0.00"))
        form = self.form(variant_id=self.com_foto.pk, choice_color=str(alheia.pk))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.error_message, "Esta cor não está disponível.")

    def test_money_sent_by_the_browser_is_ignored(self):
        """`price`, `price_delta`, `unit_price`: não são campos, morrem aqui."""
        form = self.form(
            variant_id=self.com_foto.pk, choice_color=str(self.row_dourado.pk),
            price="1.00", price_delta="0.00", unit_price="0.01", total="0.01",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.choices[0].price_delta, Decimal("2.00"))

    def test_custom_with_an_empty_palette_needs_no_colour(self):
        ProductColor.objects.filter(product=self.product).delete()
        form = self.form(variant_id=self.com_foto.pk)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.choices, ())

    def test_the_other_modes_ignore_a_colour_sent_anyway(self):
        for modo in (ColorMode.NONE, ColorMode.SINGLE, ColorMode.MULTI, ColorMode.VARIANT):
            with self.subTest(modo=modo):
                type(self.product).objects.filter(pk=self.product.pk).update(color_mode=modo)
                form = self.form(variant_id=self.com_foto.pk, choice_color=str(self.row_dourado.pk))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.choices, ())
                self.assertEqual(form.raw_choices, {})

    def test_variant_mode_still_resolves_the_colour_through_the_variant(self):
        """«Opção comercial» não mudou: `option_color` confere a variante."""
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.VARIANT)
        ProductVariant.objects.filter(pk=self.simples.pk).update(color=self.branco)
        ProductVariant.objects.filter(pk=self.com_foto.pk).update(color=self.preto)
        certo = self.form(option_color=str(self.preto.pk))
        self.assertTrue(certo.is_valid(), certo.errors)
        self.assertEqual(certo.variant, self.com_foto)
        errado = self.form(variant_id=self.com_foto.pk, option_color=str(self.branco.pk))
        self.assertFalse(errado.is_valid())


# ---------------------------------------------------------------------------
# 2. Preço e identidade da linha
# ---------------------------------------------------------------------------


class CartLineTests(PortaRetratoBase):
    def test_unit_price_is_the_variant_plus_the_delta(self):
        self.add(self.com_foto, self.row_dourado)
        (linha,) = self.lines()
        self.assertEqual(linha.base_unit_price, Decimal("20.00"))
        self.assertEqual(linha.price_adjustment, Decimal("2.00"))
        self.assertEqual(linha.unit_price, Decimal("22.00"))
        self.assertEqual(linha.total, Decimal("22.00"))
        self.assertEqual(linha.choices_text, "Cor: Dourado (+ € 2,00)")
        self.assertTrue(linha.has_choices)

    def test_a_colour_without_delta_keeps_the_variant_price(self):
        self.add(self.com_foto, self.row_branco)
        (linha,) = self.lines()
        self.assertEqual(linha.unit_price, Decimal("20.00"))
        self.assertEqual(linha.price_adjustment, Decimal("0.00"))
        self.assertEqual(linha.choices_text, "Cor: Branco")

    def test_the_variant_price_is_never_touched(self):
        self.add(self.com_foto, self.row_dourado)
        self.com_foto.refresh_from_db()
        self.assertEqual(self.com_foto.sale_price, Decimal("20.00"))

    def test_the_line_key_carries_the_choice(self):
        self.add(self.com_foto, self.row_dourado)
        (chave,) = self.client.session[CART_SESSION_KEY]
        self.assertEqual(chave, f"{self.product.pk}:{self.com_foto.pk}:-:color={self.row_dourado.pk}")
        self.assertEqual(chave, line_key(self.product.pk, self.com_foto.pk, None, {"color": self.row_dourado.pk}))

    def test_a_line_without_choice_keeps_the_old_key_format(self):
        ProductColor.objects.filter(product=self.product).delete()
        self.add(self.com_foto)
        (chave,) = self.client.session[CART_SESSION_KEY]
        self.assertEqual(chave, f"{self.product.pk}:{self.com_foto.pk}:-")
        self.assertEqual(line_key(self.product.pk, self.com_foto.pk, None), chave)
        self.assertEqual(line_key(self.product.pk, self.com_foto.pk, None, {}), chave)

    def test_white_and_gold_are_two_lines(self):
        self.add(self.com_foto, self.row_branco)
        self.add(self.com_foto, self.row_dourado)
        linhas = self.lines()
        self.assertEqual(len(linhas), 2)
        self.assertEqual(sorted(l.unit_price for l in linhas), [Decimal("20.00"), Decimal("22.00")])
        self.assertEqual(self.cart().subtotal, Decimal("42.00"))

    def test_the_same_colour_twice_is_one_line(self):
        self.add(self.com_foto, self.row_dourado)
        self.add(self.com_foto, self.row_dourado, quantity=2)
        (linha,) = self.lines()
        self.assertEqual(linha.quantity, 3)
        self.assertEqual(linha.total, Decimal("66.00"))

    def test_the_same_colour_on_two_variants_is_two_lines(self):
        self.add(self.simples, self.row_dourado)
        self.add(self.com_foto, self.row_dourado)
        self.assertEqual(len(self.lines()), 2)

    def test_colour_and_personalization_coexist_and_both_count_in_the_identity(self):
        type(self.product).objects.filter(pk=self.product.pk).update(personalization_type=PersonalizationType.TEXT)
        self.add(self.com_foto, self.row_dourado, personalization_text="Marie")
        self.add(self.com_foto, self.row_dourado, personalization_text="Paul")
        self.add(self.com_foto, self.row_dourado, personalization_text="Marie")
        linhas = sorted(self.lines(), key=lambda l: l.customization_text)
        self.assertEqual([(l.customization_text, l.quantity) for l in linhas], [("Marie", 2), ("Paul", 1)])
        for linha in linhas:
            self.assertEqual(linha.unit_price, Decimal("22.00"))
            self.assertEqual(linha.choices_text, "Cor: Dourado (+ € 2,00)")
            self.assertTrue(linha.has_customization)

    def test_the_stock_ceiling_is_the_variants_whatever_the_colour(self):
        self.add(self.com_foto, self.row_dourado, quantity=50)
        (linha,) = self.lines()
        self.assertEqual(linha.quantity, 10)
        self.assertEqual(linha.max_quantity, 10)
        self.com_foto.refresh_from_db()
        self.assertEqual(self.com_foto.stock_quantity, 10)

    def test_a_forged_colour_never_reaches_the_cart(self):
        resposta = self.add(self.com_foto, quantity=1, choice_color="999999")
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_cart_add_validates_the_choice_by_itself(self):
        """`Cart.add` é a porta de quem grava sem passar pelo formulário."""
        cart = self.cart()
        produto = type(self.product).objects.get(pk=self.product.pk)
        recusa = cart.add(produto, variant=self.com_foto, choices={"color": 999999})
        self.assertFalse(recusa.ok)
        faltou = cart.add(produto, variant=self.com_foto)
        self.assertFalse(faltou.ok)
        self.assertEqual(faltou.message, "Escolha uma cor antes de continuar.")
        aceito = cart.add(produto, variant=self.com_foto, choices={"color": str(self.row_dourado.pk)})
        self.assertTrue(aceito.ok)


# ---------------------------------------------------------------------------
# 3. O carrinho persistente e o merge
# ---------------------------------------------------------------------------


class PersistentCartTests(PortaRetratoBase):
    def setUp(self):
        super().setUp()
        self.user = make_user(username="maria", password="vaso-facetado-77")

    def login(self):
        return self.client.post(reverse("accounts:login"), {"username": "maria", "password": "vaso-facetado-77"})

    def test_the_choice_is_stored_with_the_row(self):
        self.client.force_login(self.user)
        self.add(self.com_foto, self.row_dourado)
        item = CartItem.objects.get(cart__user=self.user)
        self.assertEqual(item.choices, {"color": self.row_dourado.pk})
        self.assertEqual(item.line_key, f"{self.product.pk}:{self.com_foto.pk}:-:color={self.row_dourado.pk}")
        (linha,) = self.lines(self.user)
        self.assertEqual(linha.unit_price, Decimal("22.00"))
        self.assertEqual(linha.choices_text, "Cor: Dourado (+ € 2,00)")

    def test_a_row_without_choice_stores_an_empty_dict(self):
        ProductColor.objects.filter(product=self.product).delete()
        self.client.force_login(self.user)
        self.add(self.com_foto)
        self.assertEqual(CartItem.objects.get(cart__user=self.user).choices, {})

    def test_merge_sums_the_same_colour_and_keeps_different_colours_apart(self):
        # Na conta: Dourado × 1. Na sessão do visitante: Dourado × 2 e Branco × 1.
        self.client.force_login(self.user)
        self.add(self.com_foto, self.row_dourado)
        self.client.logout()
        self.add(self.com_foto, self.row_dourado, quantity=2)
        self.add(self.com_foto, self.row_branco)

        self.login()

        itens = {item.choices["color"]: item.quantity for item in CartItem.objects.filter(cart__user=self.user)}
        self.assertEqual(itens, {self.row_dourado.pk: 3, self.row_branco.pk: 1})
        self.assertFalse(self.client.session.get(CART_SESSION_KEY))


# ---------------------------------------------------------------------------
# 4. O catálogo muda depois de a linha existir
# ---------------------------------------------------------------------------


class CatalogChangesTests(PortaRetratoBase):
    def test_a_colour_removed_from_the_palette_drops_the_line(self):
        self.add(self.com_foto, self.row_dourado)
        self.row_dourado.delete()
        self.assertEqual(self.lines(), [])
        # Numa requisição de verdade a limpeza é gravada na sessão.
        resposta = self.client.get(reverse("cart:detail"))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_a_mode_change_drops_a_line_whose_choice_no_longer_exists(self):
        self.add(self.com_foto, self.row_dourado)
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.SINGLE)
        # No modo «Uma cor» a paleta é descrição: a chave `color` deixa de ser
        # oferecida, é ignorada, e a linha segue — sem adicional.
        (linha,) = self.lines()
        self.assertEqual(linha.choices, ())
        self.assertEqual(linha.unit_price, Decimal("20.00"))

    def test_a_product_that_starts_requiring_a_colour_keeps_the_line_and_the_checkout_complains(self):
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.NONE)
        self.add(self.com_foto)
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.CUSTOM)

        (linha,) = self.lines()
        self.assertEqual(linha.choices, ())
        problemas = services.validate_lines([linha])
        self.assertEqual(len(problemas), 1)
        self.assertIn("precisa da escolha: Cor", problemas[0].message)

    def test_a_changed_delta_is_read_on_the_next_load(self):
        """Antes do pedido o adicional é sempre o de agora — não há nada congelado."""
        self.add(self.com_foto, self.row_dourado)
        ProductColor.objects.filter(pk=self.row_dourado.pk).update(price_delta=Decimal("3.50"))
        (linha,) = self.lines()
        self.assertEqual(linha.unit_price, Decimal("23.50"))


# ---------------------------------------------------------------------------
# 5. O que já existia continua igual
# ---------------------------------------------------------------------------


class UnchangedBehaviourTests(PortaRetratoBase):
    def test_variant_mode_lines_have_no_choices(self):
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.VARIANT)
        ProductVariant.objects.filter(pk=self.com_foto.pk).update(color=self.preto)
        self.add(self.com_foto)
        (linha,) = self.lines()
        self.assertEqual(linha.choices, ())
        self.assertEqual(linha.choices_text, "")
        self.assertEqual(linha.unit_price, Decimal("20.00"))
        self.assertEqual(linha.key, f"{self.product.pk}:{self.com_foto.pk}:-")

    def test_single_and_multi_and_none_lines_are_untouched(self):
        for modo in (ColorMode.SINGLE, ColorMode.MULTI, ColorMode.NONE):
            with self.subTest(modo=modo):
                self.client.session.flush()
                type(self.product).objects.filter(pk=self.product.pk).update(color_mode=modo)
                self.add(self.com_foto)
                (linha,) = self.lines()
                self.assertEqual(linha.choices, ())
                self.assertEqual(linha.unit_price, Decimal("20.00"))
                self.assertEqual(linha.key, f"{self.product.pk}:{self.com_foto.pk}:-")
                self.cart().clear()

    def test_the_cart_page_shows_the_chosen_colour(self):
        self.add(self.com_foto, self.row_dourado)
        resposta = self.client.get(reverse("cart:detail"))
        self.assertContains(resposta, "Cor: Dourado (+ € 2,00)")
        self.assertContains(resposta, "#D4AF37")
        self.assertNotContains(resposta, "Cores à escolha")
        self.assertContains(resposta, "22,00")


# ---------------------------------------------------------------------------
# 5. Desconto, preço final e a compra sem cor
# ---------------------------------------------------------------------------


class DiscountAndRefusalTests(PortaRetratoBase):
    """Desconto é permitido; preço final zero ou negativo, não. E sem cor nada entra."""

    def setUp(self):
        super().setUp()
        self.vermelho = Color.objects.create(name="Vermelho", hex_code="#FF0000")
        self.row_vermelho = ProductColor.objects.create(
            product=self.product, color=self.vermelho, sort_order=3, price_delta=Decimal("-1.00")
        )

    def fresh(self):
        return type(self.product).objects.get(pk=self.product.pk)

    def test_a_discount_lowers_the_unit_price(self):
        self.add(self.simples, self.row_vermelho)
        linha = self.lines()[0]
        self.assertEqual(linha.base_unit_price, Decimal("15.00"))
        self.assertEqual(linha.price_adjustment, Decimal("-1.00"))
        self.assertEqual(linha.unit_price, Decimal("14.00"))
        self.assertEqual(linha.choices_text, "Cor: Vermelho (− € 1,00)")

    def test_a_discount_that_zeroes_the_price_is_refused(self):
        ProductColor.objects.filter(pk=self.row_vermelho.pk).update(price_delta=Decimal("-15.00"))
        resposta = self.add(self.simples, self.row_vermelho)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.lines(), [])

        request = RequestFactory().get("/")
        request.session = self.client.session
        request.user = AnonymousUser()
        cart = Cart(request)
        recusa = cart.add(self.fresh(), variant=self.simples, choices={"color": self.row_vermelho.pk})
        self.assertFalse(recusa.ok)
        self.assertEqual(recusa.message, "Esta cor não está disponível.")
        # Na variante de 20,00 o mesmo desconto ainda deixa preço: aceito, a 5,00.
        aceito = cart.add(self.fresh(), variant=self.com_foto, choices={"color": self.row_vermelho.pk})
        self.assertTrue(aceito.ok)
        self.assertEqual(Cart(request).lines()[0].unit_price, Decimal("5.00"))

    def test_a_discount_that_grew_past_the_price_drops_the_line(self):
        self.add(self.simples, self.row_vermelho)
        self.assertEqual(len(self.lines()), 1)
        ProductColor.objects.filter(pk=self.row_vermelho.pk).update(price_delta=Decimal("-15.00"))
        self.assertEqual(self.lines(), [])

    def test_the_merge_drops_a_line_whose_discount_swallowed_the_price(self):
        from apps.cart.merge import clamp_items

        items = {
            "k": {
                "product_id": self.product.pk, "variant_id": self.simples.pk, "quantity": 1,
                "customization": None, "choices": {"color": self.row_vermelho.pk},
            }
        }
        self.assertEqual(len(clamp_items(items)[0]), 1)
        ProductColor.objects.filter(pk=self.row_vermelho.pk).update(price_delta=Decimal("-15.00"))
        clamped, _limitados, dropped = clamp_items(items)
        self.assertEqual(clamped, {})
        self.assertEqual(dropped, 1)

    def test_htmx_add_without_colour_answers_the_message_and_adds_nothing(self):
        resposta = self.client.post(
            reverse(ADD),
            {"product_id": self.product.pk, "variant_id": self.simples.pk, "quantity": 1},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Escolha uma cor antes de continuar.")
        self.assertContains(resposta, "toast-error")
        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_a_plain_add_without_colour_redirects_with_the_message_and_adds_nothing(self):
        resposta = self.client.post(
            reverse(ADD),
            {
                "product_id": self.product.pk, "variant_id": self.simples.pk, "quantity": 1,
                "next": self.product.get_absolute_url(),
            },
            follow=True,
        )
        self.assertContains(resposta, "Escolha uma cor antes de continuar.")
        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_the_card_sends_to_the_page_instead_of_adding_blind(self):
        resposta = self.client.get(self.category.get_absolute_url())
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Escolher cor")
        self.assertNotContains(resposta, f'name="variant_id" value="{self.simples.pk}"')


# ---------------------------------------------------------------------------
# 6. O rótulo da escolha no carrinho, ao lado da cor da variante
# ---------------------------------------------------------------------------


class ChoiceLabelInTheCartTests(PortaRetratoBase):
    def setUp(self):
        super().setUp()
        from apps.catalog.models import ProductTranslation

        ProductTranslation.objects.filter(master=self.product, language="pt").update(
            color_choice_label="Cor do pompom"
        )
        ProductVariant.objects.filter(pk=self.com_foto.pk).update(color=self.branco)

    def test_the_cart_and_the_drawer_show_the_labelled_choice_apart_from_the_variant_colour(self):
        self.add(self.com_foto, self.row_dourado)
        linha = self.lines()[0]
        self.assertEqual(linha.choices_text, "Cor do pompom: Dourado (+ € 2,00)")
        self.assertEqual(linha.variant.color.display_name, "Branco")
        self.assertEqual(linha.unit_price, Decimal("22.00"))

        pagina = self.client.get(reverse("cart:detail"))
        self.assertContains(pagina, "Cor do pompom: Dourado (+ € 2,00)")
        self.assertContains(pagina, "#FFFFFF")  # a bolinha da cor da variante
        self.assertNotContains(pagina, "Cor: Dourado")

        gaveta = self.client.get(reverse("cart:drawer"))
        self.assertContains(gaveta, "Cor do pompom: Dourado (+ € 2,00)")
        self.assertContains(gaveta, "Branco · Porta-Retrato + Foto")

    def test_the_identity_price_and_grouping_did_not_change(self):
        self.add(self.com_foto, self.row_dourado)
        self.add(self.com_foto, self.row_dourado)
        self.add(self.com_foto, self.row_branco)
        linhas = self.lines()
        self.assertEqual([(l.quantity, l.choices_text) for l in linhas],
                         [(2, "Cor do pompom: Dourado (+ € 2,00)"), (1, "Cor do pompom: Branco")])
        self.assertEqual(linhas[0].key, line_key(self.product.pk, self.com_foto.pk, None, {"color": self.row_dourado.pk}))
