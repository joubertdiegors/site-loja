"""O caminho do cliente: produto → variante → quantidade → carrinho — etapa 19.

A matriz de variantes tem testes próprios (`test_variant_matrix.py`) e o
carrinho também (`test_views.py`, `test_cart.py`). O que está aqui é o que a
etapa 19 mexeu ou passou a garantir:

1. **a ficha técnica acompanha a variante inteira** — o defeito conhecido da
   etapa 13 era que cor, tamanho e material ficavam com o valor da variante
   com que a página abriu;
2. **a linha do carrinho mostra a peça comprada** — a foto da variante, quando
   ela tem uma;
3. **nada que venha do cliente derruba o servidor nem decide preço** — id
   estranho é 404, não 500; quantidade estranha é recusada ou limitada; preço
   é sempre do banco.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import CART_SESSION_KEY
from apps.catalog.models import (
    Color,
    Material,
    MediaType,
    ProductMedia,
    ProductStatus,
)
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    make_variant,
)

ADD = "cart:add"


class FlowBase(LanguageResetMixin, TestCase):
    """Um produto com dois eixos e uma variante sem tamanho.

    A variante que abre a página é a **primeira** e não tem tamanho: é
    exatamente o caso em que a linha "Tamanho" não existia no HTML e trocar de
    variante não tinha onde escrever.
    """

    def setUp(self):
        # Sem o `super()`, o idioma ativado por um teste anterior vaza para
        # este — e `get_absolute_url()` sairia com o prefixo errado.
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        self.petg = Material.objects.create(name="PETG")

        self.product = make_product(
            sku="VASO-01", name="Vaso Espiral", category=self.category, with_variant=False
        )
        self.sem_tamanho = make_variant(
            self.product, sku="VASO-01-A", price=Decimal("19.90"), stock=10,
            color=self.preto, material=self.pla, sort_order=1,
        )
        self.com_tamanho = make_variant(
            self.product, sku="VASO-01-B", price=Decimal("24.50"), stock=4,
            color=self.branco, material=self.petg, size="30 cm", sort_order=2,
        )
        # `get_absolute_url()` monta a URL com o prefixo do idioma ATIVO.
        # Guardada aqui, em portugues, ela serve de base para os outros.
        self.url = self.product.get_absolute_url()

    def page(self, url=None):
        response = self.client.get(url or self.url)
        self.assertEqual(response.status_code, 200)
        return response

    def specs(self, response=None):
        response = response or self.page()
        return {key: (label, value) for key, label, value in response.context["specifications"]}

    def payload(self, variant, response=None):
        response = response or self.page()
        for entry in response.context["variant_payload"]:
            if entry["id"] == variant.pk:
                return entry
        self.fail(f"variante {variant.pk} não está no payload")

    def add(self, **extra):
        return self.client.post(reverse(ADD), {"product_id": self.product.pk, **extra})

    def lines(self):
        return self.client.get(reverse("cart:detail")).context["cart"].lines()


# ---------------------------------------------------------------------------
# 1. A ficha técnica
# ---------------------------------------------------------------------------


class SpecSheetTests(FlowBase):
    """O defeito da etapa 13, com o teste que faltava."""

    def test_the_axis_rows_exist_even_when_the_open_variant_lacks_the_axis(self):
        """A variante de abertura não tem tamanho; a linha precisa existir.

        Sem ela no HTML, trocar para a variante de 30 cm não teria onde
        escrever — que era exatamente o defeito.
        """
        specs = self.specs()

        self.assertIn("tamanho", specs)
        self.assertEqual(specs["tamanho"][1], "", "nasce vazia, e o template a esconde")

    def test_the_open_variant_fills_its_own_axes(self):
        specs = self.specs()

        self.assertEqual(specs["cor"][1], "Preto")
        self.assertEqual(specs["material"][1], "PLA")

    def test_the_payload_carries_the_names_and_not_only_the_ids(self):
        """Sem o nome, o JavaScript não tem o que escrever na ficha."""
        entrada = self.payload(self.com_tamanho)

        self.assertEqual(entrada["colorLabel"], "Branco")
        self.assertEqual(entrada["materialLabel"], "PETG")
        self.assertEqual(entrada["sizeLabel"], "30 cm")

    def test_every_variant_carries_the_three_labels(self):
        for entrada in self.page().context["variant_payload"]:
            with self.subTest(variante=entrada["id"]):
                for chave in ("colorLabel", "sizeLabel", "materialLabel"):
                    self.assertIn(chave, entrada)

    def test_a_variant_without_the_axis_sends_an_empty_label(self):
        """Vazio é o sinal de "esconda a linha", não ausência de dado."""
        self.assertEqual(self.payload(self.sem_tamanho)["sizeLabel"], "")

    def test_the_labels_follow_the_language(self):
        from apps.catalog.models import ColorTranslation

        ColorTranslation.objects.create(master=self.preto, language="fr", name="Noir")

        response = self.page(f"/fr{self.url}")

        self.assertEqual(self.payload(self.sem_tamanho, response)["colorLabel"], "Noir")

    def test_the_rows_carry_the_keys_the_javascript_looks_for(self):
        """`data-spec="cor"` é o gancho: mudar a chave quebra a troca."""
        html = self.page().content.decode()

        for chave in ("cor", "tamanho", "material", "peso", "dimensoes", "impressao", "referencia"):
            with self.subTest(chave=chave):
                self.assertIn(f'data-spec="{chave}"', html)

    def test_a_product_with_a_single_axis_does_not_grow_empty_rows(self):
        """Produto que não tem tamanho em variante nenhuma não ganha a linha."""
        simples = make_product(
            sku="CHAV-01", name="Chaveiro", category=self.category,
            price=Decimal("5.00"), stock_quantity=3,
        )

        response = self.client.get(simples.get_absolute_url())
        chaves = [key for key, _label, _valor in response.context["specifications"]]

        self.assertNotIn("tamanho", chaves)
        self.assertNotIn("cor", chaves)


# ---------------------------------------------------------------------------
# 2. A foto da linha do carrinho
# ---------------------------------------------------------------------------


class CartLineImageTests(FlowBase):
    def foto(self, variant=None, nome="foto.jpg", principal=False):
        return ProductMedia.objects.create(
            product=self.product,
            variant=variant,
            media_type=MediaType.IMAGE,
            file=nome,
            is_primary=principal,
        )

    def test_the_line_shows_the_photo_of_the_bought_variant(self):
        """Quem comprou o preto tem de ver o preto."""
        self.foto(nome="abertura.jpg", principal=True)
        propria = self.foto(variant=self.com_tamanho, nome="branco.jpg")

        self.add(variant_id=self.com_tamanho.pk)

        self.assertEqual(self.lines()[0].display_media, propria)

    def test_without_its_own_photo_it_falls_back_to_the_product(self):
        """Caso comum, não falta de dado."""
        abertura = self.foto(nome="abertura.jpg", principal=True)

        self.add(variant_id=self.sem_tamanho.pk)

        self.assertEqual(self.lines()[0].display_media, abertura)

    def test_two_variants_of_the_same_product_show_their_own_photos(self):
        preta = self.foto(variant=self.sem_tamanho, nome="preta.jpg")
        branca = self.foto(variant=self.com_tamanho, nome="branca.jpg")

        self.add(variant_id=self.sem_tamanho.pk)
        self.add(variant_id=self.com_tamanho.pk)

        fotos = {linha.variant.pk: linha.display_media for linha in self.lines()}
        self.assertEqual(fotos[self.sem_tamanho.pk], preta)
        self.assertEqual(fotos[self.com_tamanho.pk], branca)

    def test_a_product_without_any_photo_does_not_break(self):
        self.add(variant_id=self.sem_tamanho.pk)

        self.assertIsNone(self.lines()[0].display_media)


# ---------------------------------------------------------------------------
# 3. O que vem do cliente
# ---------------------------------------------------------------------------


class UntrustedInputTests(FlowBase):
    """Nada que o navegador mande pode derrubar o servidor nem decidir preço."""

    def test_a_non_numeric_product_id_is_404_and_not_500(self):
        """`pk="abc"` levanta ValueError no ORM antes de qualquer consulta."""
        for valor in ("abc", "1;drop", "", "  ", "1.5", "9e9", "-1"):
            with self.subTest(product_id=valor):
                resposta = self.client.post(reverse(ADD), {"product_id": valor, "quantity": "1"})
                self.assertEqual(resposta.status_code, 404)

    def test_an_unknown_product_is_404(self):
        self.assertEqual(
            self.client.post(reverse(ADD), {"product_id": 10**9}).status_code, 404
        )

    def test_a_draft_product_is_404(self):
        self.product.status = ProductStatus.DRAFT
        self.product.save(update_fields=["status"])

        self.assertEqual(self.add(variant_id=self.sem_tamanho.pk).status_code, 404)

    def test_a_non_numeric_variant_id_is_refused_without_selling_anything(self):
        resposta = self.add(variant_id="abc")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_an_unknown_variant_is_refused(self):
        self.add(variant_id=10**9)

        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_a_variant_of_another_product_is_refused(self):
        outro = make_product(
            sku="OUTRO-01", name="Outro", category=self.category,
            price=Decimal("9.00"), stock_quantity=5,
        )

        self.add(variant_id=outro.default_variant.pk)

        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_a_deactivated_variant_is_refused(self):
        self.com_tamanho.is_active = False
        self.com_tamanho.save(update_fields=["is_active"])

        self.add(variant_id=self.com_tamanho.pk)

        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_without_choosing_a_variant_nothing_is_sold(self):
        """Adivinhar a cor pelo cliente seria pior do que recusar."""
        self.add()

        self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})

    def test_zero_and_negative_quantities_never_become_a_purchase_of_zero(self):
        for valor in ("0", "-1", "-999"):
            with self.subTest(quantity=valor):
                self.client.session.flush()
                self.add(variant_id=self.sem_tamanho.pk, quantity=valor)
                linhas = self.lines()
                self.assertTrue(linhas)
                self.assertGreaterEqual(linhas[0].quantity, 1)

    def test_a_nonsense_quantity_becomes_one(self):
        for valor in ("abc", "1e9", "", "1.7", "NaN"):
            with self.subTest(quantity=valor):
                self.client.session.flush()
                self.add(variant_id=self.sem_tamanho.pk, quantity=valor)
                self.assertEqual(self.lines()[0].quantity, 1)

    def test_the_stock_is_the_ceiling_even_when_the_client_asks_for_more(self):
        self.add(variant_id=self.com_tamanho.pk, quantity="99")

        self.assertEqual(self.lines()[0].quantity, 4)

    def test_the_client_cannot_send_the_price(self):
        """Preço é do banco. Sempre."""
        self.add(
            variant_id=self.com_tamanho.pk,
            price="0.01",
            unit_price="0.01",
            sale_price="0.01",
            total="0.01",
        )

        linha = self.lines()[0]
        self.assertEqual(linha.unit_price, Decimal("24.50"))
        self.assertEqual(linha.total, Decimal("24.50"))

    def test_the_session_never_stores_a_price(self):
        """Se o preço morasse na sessão, mexer nela mudaria o que se paga."""
        self.add(variant_id=self.com_tamanho.pk)

        guardado = self.client.session[CART_SESSION_KEY]
        for item in guardado.values():
            self.assertEqual(set(item) - {"customization"}, {"product_id", "variant_id", "quantity"})

    def test_a_price_changed_in_the_admin_reaches_the_open_cart(self):
        """O carrinho lê o preço a cada leitura, não uma cópia da adição."""
        self.add(variant_id=self.com_tamanho.pk)
        self.com_tamanho.sale_price = Decimal("30.00")
        self.com_tamanho.save(update_fields=["sale_price"])

        self.assertEqual(self.lines()[0].unit_price, Decimal("30.00"))

    def test_csrf_is_required(self):
        cliente = self.client_class(enforce_csrf_checks=True)

        resposta = cliente.post(
            reverse(ADD), {"product_id": self.product.pk, "variant_id": self.sem_tamanho.pk}
        )

        self.assertEqual(resposta.status_code, 403)

    def test_a_get_does_not_change_the_cart(self):
        self.assertEqual(self.client.get(reverse(ADD)).status_code, 405)

    def test_an_unknown_line_key_does_not_break_update_or_remove(self):
        for rota in ("cart:update", "cart:remove"):
            with self.subTest(rota=rota):
                resposta = self.client.post(reverse(rota), {"line": "nao-existe", "quantity": "3"})
                self.assertEqual(resposta.status_code, 302)

    def test_a_visitor_without_an_account_can_buy(self):
        self.add(variant_id=self.sem_tamanho.pk)

        self.assertEqual(self.lines()[0].quantity, 1)


# ---------------------------------------------------------------------------
# 4. A página de compra
# ---------------------------------------------------------------------------


class ProductPageFlowTests(FlowBase):
    def test_the_add_button_is_disabled_while_the_request_is_in_flight(self):
        """Dois cliques rápidos mandavam dois POST.

        Por seletor, e não por `find`: o botão fica na linha de compra, fora
        do formulário (ver `test_the_buy_row_belongs_to_the_form_by_attribute`).
        """
        html = self.page().content.decode()

        self.assertIn('hx-disabled-elt="[data-add-button]"', html)
        self.assertIn("data-add-button", html)

    def test_the_buy_row_belongs_to_the_form_by_attribute(self):
        """Quantidade e botão vivem fora do `<form>` (o coração é outro
        formulário, e formulário dentro de formulário não existe) e apontam
        para ele por `form="add-to-cart"` — para o navegador e para o HTMX
        eles continuam sendo do formulário."""
        html = self.page().content.decode()

        self.assertIn('id="add-to-cart"', html)
        self.assertIn('name="quantity" form="add-to-cart"', html)
        self.assertIn('form="add-to-cart" class="product-add"', html)
        formulario = html.split('id="add-to-cart"', 1)[1].split("</form>", 1)[0]
        self.assertNotIn('class="product-add"', formulario)
        self.assertNotIn('id="quantity"', formulario)

    def test_the_quantity_starts_at_one_and_is_capped_by_the_stock(self):
        response = self.page()
        html = response.content.decode()

        self.assertEqual(response.context["max_quantity"], 10)
        self.assertIn('value="1" min="1"', html)
        self.assertIn('max="10"', html)

    def test_the_customer_stays_on_the_product_page_with_htmx(self):
        resposta = self.client.post(
            reverse(ADD),
            {"product_id": self.product.pk, "variant_id": self.sem_tamanho.pk},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(resposta.status_code, 200)
        # A resposta e so o que mudou: o contador do cabecalho, a gaveta e
        # o aviso. Nenhuma pagina nova -- o cliente nao saiu do produto.
        corpo = resposta.content.decode()
        self.assertIn('data-cart-count="1"', corpo)
        self.assertNotIn("<html", corpo)

    def test_without_htmx_it_goes_back_to_the_product(self):
        resposta = self.add(variant_id=self.sem_tamanho.pk, next=self.url)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.url, self.url)

    def test_the_counter_follows_what_was_added(self):
        self.add(variant_id=self.sem_tamanho.pk, quantity="3")

        resposta = self.page()

        self.assertContains(resposta, 'data-cart-count="3"')
        self.assertEqual(resposta.context["cart"].total_quantity, 3)

    def test_the_same_product_in_two_variants_is_two_lines(self):
        self.add(variant_id=self.sem_tamanho.pk, quantity="2")
        self.add(variant_id=self.com_tamanho.pk, quantity="1")

        linhas = self.lines()

        self.assertEqual(len(linhas), 2)
        self.assertEqual({linha.quantity for linha in linhas}, {1, 2})

    def test_the_same_variant_twice_is_one_line(self):
        self.add(variant_id=self.sem_tamanho.pk, quantity="2")
        self.add(variant_id=self.sem_tamanho.pk, quantity="3")

        linhas = self.lines()

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0].quantity, 5)

    def test_the_cart_page_shows_the_variant_of_each_line(self):
        self.add(variant_id=self.com_tamanho.pk)

        html = self.client.get(reverse("cart:detail")).content.decode()

        self.assertIn("Vaso Espiral", html)
        self.assertIn("Branco", html)
        self.assertIn("30 cm", html)
        self.assertIn("24,50", html)


class PurchaseLanguageTests(FlowBase):
    """Os quatro idiomas, no caminho inteiro."""

    def test_the_error_message_follows_the_language(self):
        esperado = {
            "/fr": "Choisissez",
            "/nl": "Kies",
            "/en": "Choose",
        }
        for prefixo, trecho in esperado.items():
            with self.subTest(idioma=prefixo):
                resposta = self.client.post(
                    f"{prefixo}/carrinho/adicionar/",
                    {"product_id": self.product.pk},
                    HTTP_HX_REQUEST="true",
                )
                self.assertContains(resposta, trecho)

    def test_the_cart_page_answers_in_the_four_languages(self):
        self.add(variant_id=self.sem_tamanho.pk)

        for prefixo in ("", "/fr", "/nl", "/en"):
            with self.subTest(idioma=prefixo or "pt"):
                resposta = self.client.get(f"{prefixo}/carrinho/")
                self.assertEqual(resposta.status_code, 200)
                self.assertContains(resposta, "Vaso Espiral")

    def test_the_product_page_answers_in_the_four_languages(self):
        for prefixo in ("", "/fr", "/nl", "/en"):
            with self.subTest(idioma=prefixo or "pt"):
                resposta = self.client.get(f"{prefixo}{self.url}")
                self.assertEqual(resposta.status_code, 200)
