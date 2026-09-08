"""Etapa 3E — as opções adicionais no pedido: snapshot, telas, e-mails, imutabilidade.

O pedido é uma fotografia. ``OrderItem.options_snapshot`` (3B) guarda o texto
que o cliente leu, no idioma em que comprou («Instalação: Parede · Acabamento:
Fosco»); a partir daí toda tela — Admin, conta do cliente, e-mails HTML e
texto, retomar pagamento — lê **esse texto**, e nunca o produto, a opção, o
valor ou a tradução de hoje.

Estes testes fecham o caminho inteiro e depois mexem em tudo que poderia
vazar para o histórico: renomeiam produto, opção, valor, traduções, variante;
tiram as escolhas da variante; trocam cor, tamanho, material, preço e SKU.
O pedido tem de continuar exatamente igual.
"""

from decimal import Decimal

from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from apps.cart.cart import CART_SESSION_KEY, CartLine, load_variants
from apps.catalog.models import (
    OPTIONS_TEXT_SEPARATOR,
    Color,
    ColorTranslation,
    Material,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services
from apps.orders.emails import (
    send_admin_order_email,
    send_cancellation_approved_email,
    send_order_confirmation_email,
    send_order_shipped_email,
)
from apps.orders.models import FulfillmentStatus, Order, OrderItem, PaymentStatus

ADMINS = ["producao@jd-print.test"]


def opcao(produto, nome, *valores, sort_order=0, **traducoes):
    o = ProductOption.objects.create(product=produto, name=nome, sort_order=sort_order)
    for idioma, texto in traducoes.items():
        ProductOptionTranslation.objects.create(master=o, language=idioma, name=texto)
    for i, v in enumerate(valores):
        ProductOptionValue.objects.create(option=o, name=v, sort_order=i)
    return o


def traduzir_valor(valor, **traducoes):
    for idioma, texto in traducoes.items():
        ProductOptionValueTranslation.objects.create(master=valor, language=idioma, name=texto)
    return valor


def valor(o, nome):
    return o.values.get(name=nome)


@override_settings(ORDER_ADMIN_EMAILS=ADMINS)
class SnapshotBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="cliente", email="cliente@example.com")
        self.customer = self.user.customer
        self.address = make_address(self.customer, self.country)

        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        ColorTranslation.objects.create(master=self.branco, language="pt", name="Branco")
        ColorTranslation.objects.create(master=self.branco, language="fr", name="Blanc")
        self.pla = Material.objects.create(name="PLA")
        self.produto = make_product(sku="REL-LEAO-001", name="Suporte Leão de Judá", with_variant=False, status=ProductStatus.ACTIVE)
        self.variante = ProductVariant.objects.create(
            product=self.produto, sku="REL-LEAO-001-V02", color=self.branco, size="25 cm", material=self.pla,
            sale_price=Decimal("32.00"), stock_quantity=10, weight_grams=Decimal("150"), production_lead_time_days=2,
        )

    # -- as opções do cenário ---------------------------------------------------

    def duas_opcoes(self):
        self.inst = opcao(self.produto, "Instalação", "Mesa", "Parede", sort_order=1, fr="Installation", nl="Installatie", en="Installation")
        self.acab = opcao(self.produto, "Acabamento", "Fosco", "Brilhante", sort_order=2, fr="Finition", nl="Afwerking", en="Finish")
        traduzir_valor(valor(self.inst, "Parede"), fr="Mur", nl="Muur", en="Wall")
        traduzir_valor(valor(self.acab, "Fosco"), fr="Mat", nl="Mat", en="Matte")
        self.variante.set_option_values({self.inst: valor(self.inst, "Parede"), self.acab: valor(self.acab, "Fosco")})

    # -- o pedido -----------------------------------------------------------------

    def linha(self, variante=None, quantidade=1, customization=None):
        variante = variante or self.variante
        variante = load_variants({"x": {"variant_id": variante.pk}})[variante.pk]
        chave = f"{variante.product_id}:{variante.pk}:" + ("c" if customization else "-")
        return CartLine(key=chave, product=variante.product, variant=variante, quantity=quantidade, customization=customization, upload=None)

    def pedido(self, *linhas, language="pt-br"):
        linhas = linhas or (self.linha(),)
        with translation.override(language):
            return services.create_order(
                customer=self.customer, lines=list(linhas), shipping_address=self.address,
                billing_address=self.address, shipping_method=self.method, language=language,
            )

    def item(self, order=None):
        return (order or self.pedido()).items.get()

    def fresh(self, item):
        return OrderItem.objects.get(pk=item.pk)

    # -- as telas ----------------------------------------------------------------------

    def staff(self):
        if not hasattr(self, "_chefe"):
            self._chefe = make_user(username="chefe", email="chefe@example.com", is_staff=True)
            self._chefe.is_superuser = True
            self._chefe.save()
        self.client.force_login(self._chefe)

    def admin_html(self, order):
        self.staff()
        return self.client.get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()

    def conta_html(self, order):
        self.client.force_login(self.user)
        return self.client.get(reverse("orders:detail", args=[order.number])).content.decode()

    def emails(self, order):
        """(texto, html) do e-mail de confirmação e do administrativo."""
        mail.outbox.clear()
        send_order_confirmation_email(order, force=True)
        send_admin_order_email(order, force=True)
        cliente, admin = mail.outbox[0], mail.outbox[1]
        return (cliente.body, cliente.alternatives[0][0]), (admin.body, admin.alternatives[0][0])


# ---------------------------------------------------------------------------
# O snapshot
# ---------------------------------------------------------------------------


class SnapshotContentTests(SnapshotBase):
    def test_an_item_without_options_has_an_empty_snapshot_and_no_lines(self):
        item = self.item()
        self.assertEqual(item.options_snapshot, "")
        self.assertEqual(item.options_lines, [])
        self.assertEqual(item.variant_label, "Branco · 25 cm · PLA")

    def test_one_two_and_three_options(self):
        inst = opcao(self.produto, "Instalação", "Parede", sort_order=1)
        acab = opcao(self.produto, "Acabamento", "Fosco", sort_order=2)
        modelo = opcao(self.produto, "Modelo", "Decorado", sort_order=3)
        escolhas = {}
        esperado = []
        for o, v in ((inst, "Parede"), (acab, "Fosco"), (modelo, "Decorado")):
            escolhas[o] = valor(o, v)
            esperado.append(f"{o.name}: {v}")
            self.variante.set_option_values(escolhas)
            with self.subTest(opcoes=len(escolhas)):
                item = self.item()
                self.assertEqual(item.options_lines, esperado)
                self.assertEqual(item.options_snapshot, OPTIONS_TEXT_SEPARATOR.join(esperado))
                self.assertEqual(item.variant_label, "Branco · 25 cm · PLA · " + " · ".join(v for _o, v in ((inst, "Parede"), (acab, "Fosco"), (modelo, "Decorado"))[: len(escolhas)]))

    def test_the_order_is_the_options_sort_order_not_the_alphabet(self):
        z = opcao(self.produto, "Zona", "Norte", sort_order=1)
        a = opcao(self.produto, "Acabamento", "Fosco", sort_order=2)
        m = opcao(self.produto, "Modelo", "Simples", sort_order=3)
        self.variante.set_option_values({m: valor(m, "Simples"), a: valor(a, "Fosco"), z: valor(z, "Norte")})
        self.assertEqual(self.item().options_lines, ["Zona: Norte", "Acabamento: Fosco", "Modelo: Simples"])
        z.sort_order = 9
        z.save()
        self.assertEqual(self.item().options_lines, ["Acabamento: Fosco", "Modelo: Simples", "Zona: Norte"])

    def test_the_snapshot_is_in_the_language_of_the_purchase(self):
        self.duas_opcoes()
        esperado = {
            "pt-br": ["Instalação: Parede", "Acabamento: Fosco"],
            "fr": ["Installation: Mur", "Finition: Mat"],
            "nl": ["Installatie: Muur", "Afwerking: Mat"],
            "en": ["Installation: Wall", "Finish: Matte"],
        }
        itens = {}
        for idioma, linhas in esperado.items():
            with self.subTest(idioma=idioma):
                itens[idioma] = self.item(self.pedido(language=idioma))
                self.assertEqual(itens[idioma].options_lines, linhas)
        # Quatro pedidos, quatro snapshots independentes — o último não mexe no primeiro.
        for idioma, linhas in esperado.items():
            self.assertEqual(self.fresh(itens[idioma]).options_lines, linhas)
        self.assertEqual(itens["fr"].variant_label, "Blanc · 25 cm · PLA · Mur · Mat")

    def test_a_missing_translation_falls_back_to_portuguese_never_to_none(self):
        self.duas_opcoes()
        modelo = opcao(self.produto, "Modelo", "Decorado", sort_order=3)  # sem tradução alguma
        self.variante.set_option_values({self.inst: valor(self.inst, "Parede"), self.acab: valor(self.acab, "Fosco"), modelo: valor(modelo, "Decorado")})
        ProductOptionValueTranslation.objects.filter(master=valor(self.inst, "Parede"), language="nl").delete()  # valor sem NL
        item = self.item(self.pedido(language="nl"))
        self.assertEqual(item.options_lines, ["Installatie: Parede", "Afwerking: Mat", "Modelo: Decorado"])
        for lixo in ("None", "null", "undefined", "object Object"):
            self.assertNotIn(lixo, item.options_snapshot)

    def test_an_option_the_variant_does_not_answer_is_simply_absent(self):
        self.duas_opcoes()
        opcao(self.produto, "Modelo", "Simples", "Decorado", sort_order=3)  # o produto tem, a variante não escolheu
        item = self.item()
        self.assertEqual(item.options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        self.assertNotIn("Modelo", item.options_snapshot)
        self.assertNotIn("Não definido", item.options_snapshot)

    def test_the_snapshot_is_never_empty_when_the_variant_has_options(self):
        self.duas_opcoes()
        self.assertTrue(self.item().options_snapshot)

    def test_the_old_axes_and_the_other_snapshots_are_untouched(self):
        self.duas_opcoes()
        item = self.item()
        self.assertEqual((item.color_name, item.size_name, item.material_name), ("Branco", "25 cm", "PLA"))
        self.assertEqual(item.sku, "REL-LEAO-001-V02")
        self.assertEqual(item.unit_price, Decimal("32.00"))
        self.assertEqual(item.product_name, "Suporte Leão de Judá")
        self.assertEqual(item.production_days, 2)

    def test_the_lines_split_only_on_the_separator_the_catalogue_uses(self):
        item = OrderItem(options_snapshot="Instalação: Parede · Acabamento: Fosco")
        self.assertEqual(item.options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        self.assertEqual(OrderItem(options_snapshot="").options_lines, [])
        self.assertEqual(OrderItem(options_snapshot="  ").options_lines, [])


# ---------------------------------------------------------------------------
# Imutabilidade
# ---------------------------------------------------------------------------


class ImmutabilityTests(SnapshotBase):
    """O teste obrigatório da etapa: muda-se tudo, o pedido não muda."""

    def setUp(self):
        super().setUp()
        self.duas_opcoes()
        self.order = self.pedido()
        self.antes = self.retrato(self.order.items.get())

    def retrato(self, item):
        return {
            "product_name": item.product_name, "sku": item.sku, "variant_label": item.variant_label,
            "color_name": item.color_name, "size_name": item.size_name, "material_name": item.material_name,
            "options_snapshot": item.options_snapshot, "options_lines": item.options_lines,
            "unit_price": item.unit_price, "total": item.total, "production_days": item.production_days,
        }

    def assert_unchanged(self):
        item = self.fresh(self.order.items.get())
        self.assertEqual(self.retrato(item), self.antes)
        self.assertEqual(item.options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        self.assertEqual(item.variant_label, "Branco · 25 cm · PLA · Parede · Fosco")

    def test_renaming_the_product_does_not_touch_the_order(self):
        ProductTranslation.objects.filter(master=self.produto).update(name="Outro nome")
        self.produto.sku = "NOVO-SKU"
        self.produto.save()
        self.assert_unchanged()

    def test_renaming_the_option_does_not_touch_the_order(self):
        self.inst.name = "Montagem"
        self.inst.save()
        self.assert_unchanged()

    def test_renaming_the_value_does_not_touch_the_order(self):
        parede = valor(self.inst, "Parede")
        parede.name = "Muro"
        parede.save()
        self.assert_unchanged()

    def test_changing_the_translations_does_not_touch_the_order(self):
        ProductOptionTranslation.objects.filter(master=self.inst).update(name="Montage")
        ProductOptionValueTranslation.objects.filter(master=valor(self.inst, "Parede")).update(name="Muro")
        ProductOptionValueTranslation.objects.create(master=valor(self.inst, "Parede"), language="pt", name="Muro")
        self.assert_unchanged()

    def test_changing_the_variant_does_not_touch_the_order(self):
        preto = Color.objects.create(name="Preto", hex_code="#000000")
        petg = Material.objects.create(name="PETG")
        self.variante.color = preto
        self.variante.size = "30 cm"
        self.variante.material = petg
        self.variante.sku = "REL-LEAO-001-V09"
        self.variante.sale_price = Decimal("99.00")
        self.variante.production_lead_time_days = 9
        self.variante.save()
        self.variante.set_option_values({self.inst: valor(self.inst, "Mesa"), self.acab: valor(self.acab, "Brilhante")})
        self.assert_unchanged()
        self.assertEqual(self.variante.label, "Preto · 30 cm · PETG · Mesa · Brilhante")  # o vivo mudou; o pedido, não

    def test_removing_the_choices_from_the_variant_does_not_touch_the_order(self):
        self.variante.set_option_values({self.inst: None, self.acab: None})
        self.assertEqual(ProductVariant.objects.get(pk=self.variante.pk).option_links, [])
        self.assert_unchanged()

    def test_everything_at_once_and_every_screen_still_reads_the_snapshot(self):
        ProductTranslation.objects.filter(master=self.produto).update(name="Leão renomeado")
        self.inst.name = "Montagem"
        self.inst.save()
        muro = valor(self.inst, "Parede")
        muro.name = "Muro"
        muro.save()
        ProductOptionTranslation.objects.all().update(name="X")
        ProductOptionValueTranslation.objects.all().update(name="Y")
        self.variante.size = "30 cm"
        self.variante.sale_price = Decimal("1.00")
        self.variante.save()
        self.variante.set_option_values({self.inst: valor(self.inst, "Mesa")})
        self.assert_unchanged()
        for nome, html in (("admin", self.admin_html(self.order)), ("conta", self.conta_html(self.order))):
            with self.subTest(tela=nome):
                self.assertIn("Instalação: Parede", html)
                self.assertIn("Acabamento: Fosco", html)
                self.assertIn("Branco · 25 cm · PLA · Parede · Fosco", html)
                self.assertNotIn("Montagem", html)
                self.assertNotIn("Muro", html)
        (txt, html), (atxt, ahtml) = self.emails(self.order)
        for corpo in (txt, html, atxt, ahtml):
            self.assertIn("Instalação: Parede", corpo)
            self.assertNotIn("Montagem", corpo)
            self.assertNotIn("Muro", corpo)

    def test_the_screens_never_ask_the_catalogue_for_the_options(self):
        self.assert_unchanged()
        alvos = ("catalog_productoption", "catalog_productvariantoptionvalue")
        with CaptureQueriesContext(connection) as consultas:
            self.admin_html(self.order)
            self.conta_html(self.order)
            self.emails(self.order)
            self.client.get(reverse("orders:retry_payment", args=[self.order.number]))
        for consulta in consultas:
            for alvo in alvos:
                self.assertNotIn(alvo, consulta["sql"].lower())


# ---------------------------------------------------------------------------
# Telas: Admin, conta, retomar pagamento
# ---------------------------------------------------------------------------


class AdminTests(SnapshotBase):
    def test_an_item_with_options_shows_them_under_the_product(self):
        self.duas_opcoes()
        order = self.pedido()
        html = self.admin_html(order)
        bloco = html.split("4. ITENS E VALORES", 1)[1]
        self.assertIn("Instalação: Parede<br>Acabamento: Fosco", bloco)
        self.assertIn('class="jd-opcoes-rotulo">Opções:</span>', bloco)
        self.assertIn("Suporte Leão de Judá — Branco · 25 cm · PLA · Parede · Fosco", bloco)
        self.assertIn("REL-LEAO-001-V02", bloco)

    def test_an_item_without_options_shows_a_dash(self):
        html = self.admin_html(self.pedido())
        self.assertIn("Opções:</span> —", html)

    def test_an_old_order_shows_a_dash_and_nothing_is_reconstructed(self):
        self.duas_opcoes()
        order = self.pedido()
        OrderItem.objects.filter(order=order).update(options_snapshot="")  # um pedido de antes das opções
        html = self.admin_html(order)
        self.assertIn("Opções:</span> —", html)
        self.assertNotIn("Instalação: Parede", html)
        item = self.fresh(order.items.get())
        self.assertEqual(item.options_snapshot, "")  # continua vazio: nada foi inventado

    def test_a_multilingual_order_shows_the_language_it_was_bought_in(self):
        self.duas_opcoes()
        fr = self.pedido(language="fr")
        nl = self.pedido(language="nl")
        self.assertIn("Installation: Mur<br>Finition: Mat", self.admin_html(fr))
        self.assertIn("Installatie: Muur<br>Afwerking: Mat", self.admin_html(nl))

    def test_long_names_and_many_options_are_escaped_and_wrap(self):
        longa = opcao(self.produto, "Instalação do suporte na superfície escolhida pelo cliente <b>x</b>", "Parede de alvenaria com bucha e parafuso de aço inoxidável & suporte", sort_order=1)
        outras = [opcao(self.produto, f"Opção {n}", f"Valor {n}", sort_order=n + 1) for n in range(2, 7)]
        escolhas = {longa: longa.values.get()}
        escolhas.update({o: o.values.get() for o in outras})
        self.variante.set_option_values(escolhas)
        html = self.admin_html(self.pedido())
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("parafuso de aço inoxidável &amp; suporte", html)
        self.assertIn("Opção 6: Valor 6", html)
        self.assertIn(".jd-opcoes { display: block; margin-top: 2px; overflow-wrap: anywhere;", html)  # a regra vive no <style> da ficha


class CustomerScreensTests(SnapshotBase):
    def test_the_account_page_lists_the_options(self):
        self.duas_opcoes()
        html = self.conta_html(self.pedido())
        self.assertIn("Instalação: Parede", html)
        self.assertIn("Acabamento: Fosco", html)
        self.assertIn("Branco · 25 cm · PLA · Parede · Fosco", html)

    def test_the_account_page_of_an_old_order_is_quiet(self):
        order = self.pedido()
        html = self.conta_html(order)
        self.assertNotIn("Instalação", html)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[order.number])).status_code, 200)

    def test_the_retry_payment_page_lists_the_options(self):
        self.duas_opcoes()
        order = self.pedido()
        self.client.force_login(self.user)
        response = self.client.get(reverse("orders:retry_payment", args=[order.number]))
        if response.status_code == 200:
            self.assertIn("Instalação: Parede", response.content.decode())
        else:
            self.assertIn(response.status_code, (302, 404))  # a transferência não «retenta» — a página não é dela

    def test_the_checkout_summary_shows_the_chosen_options_before_the_order(self):
        self.duas_opcoes()
        self.client.force_login(self.user)
        self.client.post(reverse("cart:add"), {"product_id": self.produto.pk, "variant_id": self.variante.pk, "quantity": 1,
                                               f"option_opt-{self.inst.pk}": valor(self.inst, "Parede").pk, f"option_opt-{self.acab.pk}": valor(self.acab, "Fosco").pk})
        html = self.client.get(reverse("cart:checkout")).content.decode()
        self.assertIn("Branco · 25 cm · PLA · Parede · Fosco", html)


# ---------------------------------------------------------------------------
# E-mails
# ---------------------------------------------------------------------------


class EmailTests(SnapshotBase):
    def test_confirmation_and_admin_emails_carry_the_options_in_text_and_html(self):
        self.duas_opcoes()
        (txt, html), (atxt, ahtml) = self.emails(self.pedido())
        for corpo in (txt, atxt):
            self.assertIn("Opções:", corpo)
            self.assertIn("      Instalação: Parede\n      Acabamento: Fosco\n", corpo)
        for corpo in (html, ahtml):
            self.assertIn(">Instalação: Parede</span>", corpo)
            self.assertIn(">Acabamento: Fosco</span>", corpo)
            self.assertIn("Branco · 25 cm · PLA · Parede · Fosco", corpo)

    def test_the_emails_keep_the_language_of_the_purchase(self):
        self.duas_opcoes()
        (txt, html), (atxt, ahtml) = self.emails(self.pedido(language="fr"))
        for corpo in (txt, html):
            self.assertIn("Installation: Mur", corpo)
            self.assertIn("Finition: Mat", corpo)
            self.assertIn("Options", corpo) if corpo is txt else None
        # O administrativo sai em português (idioma da loja), mas o snapshot é o do cliente.
        self.assertIn("Installation: Mur", atxt)
        self.assertIn("Installation: Mur", ahtml)

    def test_an_old_order_email_has_no_options_block(self):
        (txt, html), (atxt, ahtml) = self.emails(self.pedido())
        for corpo in (txt, atxt):
            self.assertNotIn("Opções:", corpo)
        self.assertNotIn("Instalação", html + ahtml)

    def test_the_html_email_escapes_the_snapshot(self):
        o = opcao(self.produto, "Modelo <script>", "Decorado & <i>x</i>", sort_order=1)
        self.variante.set_option_values({o: o.values.get()})
        (_txt, html), (_atxt, ahtml) = self.emails(self.pedido())
        for corpo in (html, ahtml):
            self.assertIn("Modelo &lt;script&gt;: Decorado &amp; &lt;i&gt;x&lt;/i&gt;", corpo)
            self.assertNotIn("<script>", corpo)

    def test_the_other_emails_still_go_out_for_an_order_with_options(self):
        self.duas_opcoes()
        order = self.pedido()
        mail.outbox.clear()
        services.confirm_payment(order)
        services.mark_shipped(order, "BE123")
        self.assertTrue(send_order_shipped_email(order, force=True))
        self.assertTrue(len(mail.outbox) >= 1)
        self.assertEqual(self.fresh(order.items.get()).options_lines, ["Instalação: Parede", "Acabamento: Fosco"])


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------


class SecurityTests(SnapshotBase):
    def setUp(self):
        super().setUp()
        self.duas_opcoes()
        self.client.force_login(self.user)

    def comprar(self, **extra):
        dados = {"product_id": self.produto.pk, "variant_id": self.variante.pk, "quantity": 1,
                 f"option_opt-{self.inst.pk}": valor(self.inst, "Parede").pk, f"option_opt-{self.acab.pk}": valor(self.acab, "Fosco").pk}
        self.client.post(reverse("cart:add"), dados)
        checkout = {"shipping_address": self.address.pk, "billing_same_as_shipping": "1", "shipping_method": self.method.pk, **extra}
        return self.client.post(reverse("cart:checkout"), checkout)

    def test_the_customer_cannot_send_the_snapshot_the_price_or_the_names(self):
        self.comprar(options_snapshot="Instalação: Teto", unit_price="0.01", price="0.01", variant_label="Ouro",
                     option_name="Hack", value_name="Hack", **{f"option_opt-{self.inst.pk}_label": "Teto"})
        item = Order.objects.get().items.get()
        self.assertEqual(item.options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        self.assertEqual(item.unit_price, Decimal("32.00"))
        self.assertEqual(item.variant_label, "Branco · 25 cm · PLA · Parede · Fosco")
        self.assertNotIn("Teto", item.options_snapshot + item.variant_label)

    def test_manipulated_ids_never_reach_the_order(self):
        outro = make_product(sku="OUTRO", name="Outro", price=Decimal("5"), stock_quantity=5)
        alheia = opcao(outro, "Instalação", "Teto")
        casos = {
            "opção alheia": {f"option_opt-{alheia.pk}": valor(alheia, "Teto").pk},
            "valor alheio": {f"option_opt-{self.inst.pk}": valor(alheia, "Teto").pk},
            "valor de outra opção": {f"option_opt-{self.inst.pk}": valor(self.acab, "Fosco").pk},
            "variante alheia": {"variant_id": outro.default_variant.pk},
            "combinação inexistente": {f"option_opt-{self.inst.pk}": valor(self.inst, "Mesa").pk},
        }
        for nome, dados in casos.items():
            with self.subTest(caso=nome):
                base = {"product_id": self.produto.pk, "variant_id": self.variante.pk, "quantity": 1,
                        f"option_opt-{self.inst.pk}": valor(self.inst, "Parede").pk, f"option_opt-{self.acab.pk}": valor(self.acab, "Fosco").pk}
                base.update(dados)
                self.client.post(reverse("cart:add"), base)
                self.assertEqual(self.client.session.get(CART_SESSION_KEY, {}), {})
        self.assertEqual(Order.objects.count(), 0)

    def test_the_snapshot_is_built_by_the_server_from_the_variant(self):
        self.comprar()
        item = Order.objects.get().items.get()
        self.assertEqual(item.options_snapshot, self.variante.options_text)
        self.assertEqual(item.variant, self.variante)


# ---------------------------------------------------------------------------
# Fluxos: produção, cancelamento, reembolso, personalização, transação
# ---------------------------------------------------------------------------


class FlowTests(SnapshotBase):
    def test_production_with_every_axis_and_options(self):
        self.duas_opcoes()
        order = self.pedido(self.linha(quantidade=2))
        services.confirm_payment(order)
        order.refresh_from_db()
        self.variante.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PAID)
        self.assertEqual(self.variante.stock_quantity, 8)
        order.fulfillment_status = FulfillmentStatus.values[1]
        order.save()
        services.mark_shipped(order, "BE999")
        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, FulfillmentStatus.SHIPPED)
        item = self.fresh(order.items.get())
        self.assertEqual(item.stock_taken, 2)
        self.assertEqual(item.options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        self.assertEqual(item.variant_label, "Branco · 25 cm · PLA · Parede · Fosco")
        self.assertEqual((item.color_name, item.size_name, item.material_name), ("Branco", "25 cm", "PLA"))

    def test_cancellation_and_refund_leave_the_item_intact(self):
        self.duas_opcoes()
        order = self.pedido()
        antes = self.fresh(order.items.get())
        services.confirm_payment(order)
        mail.outbox.clear()
        services.request_cancellation(order, "Mudei de ideia")
        order.refresh_from_db()
        if order.cancellation_status != "approved":
            services.approve_cancellation(order, "ok")
            order.refresh_from_db()
        services.register_refund(order, order.total, reference="REF-1")
        order.refresh_from_db()
        depois = self.fresh(order.items.get())
        for campo in ("options_snapshot", "variant_label", "sku", "unit_price", "total", "product_name"):
            self.assertEqual(getattr(depois, campo), getattr(antes, campo))
        self.assertEqual(order.items.count(), 1)
        self.assertTrue(send_cancellation_approved_email(order))
        corpo = mail.outbox[-1].body
        self.assertIn(order.number, corpo)
        self.assertIn("Instalação: Parede", self.admin_html(order))

    def test_personalisation_and_options_live_side_by_side(self):
        self.duas_opcoes()
        self.produto.personalization_type = "text"
        self.produto.save()
        linha = self.linha(customization={"type": "text", "text": "Feliz aniversário", "notes": "letra grande"})
        item = self.item(self.pedido(linha))
        self.assertEqual(item.personalization_type, "text")
        self.assertEqual(item.personalization_text, "Feliz aniversário")
        self.assertEqual(item.personalization_notes, "letra grande")
        self.assertEqual(item.options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        (txt, html), (atxt, ahtml) = self.emails(item.order)
        for corpo in (txt, html, atxt, ahtml):
            self.assertIn("Feliz aniversário", corpo)
            self.assertIn("Instalação: Parede", corpo)

    def test_two_variants_of_the_same_product_are_two_items_with_their_own_snapshot(self):
        self.duas_opcoes()
        mesa = ProductVariant.objects.create(product=self.produto, sku="REL-LEAO-001-V01", color=self.branco, size="25 cm", material=self.pla,
                                             sale_price=Decimal("30.00"), stock_quantity=5, weight_grams=Decimal("150"))
        mesa.set_option_values({self.inst: valor(self.inst, "Mesa"), self.acab: valor(self.acab, "Fosco")})
        order = self.pedido(self.linha(), self.linha(mesa))
        por_sku = {i.sku: i for i in order.items.all()}
        self.assertEqual(por_sku["REL-LEAO-001-V02"].options_lines, ["Instalação: Parede", "Acabamento: Fosco"])
        self.assertEqual(por_sku["REL-LEAO-001-V01"].options_lines, ["Instalação: Mesa", "Acabamento: Fosco"])

    def test_the_order_is_all_or_nothing(self):
        self.duas_opcoes()
        self.variante.stock_quantity = 0
        self.variante.save()
        with self.assertRaises(services.CheckoutError):
            self.pedido()
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class QueryCountTests(SnapshotBase):
    def test_creating_the_order_does_not_cost_a_query_per_option_or_variant(self):
        def cenario(n_opcoes, n_variantes):
            produto = make_product(sku=f"Q{n_opcoes}{n_variantes}", name="Q", with_variant=False, status=ProductStatus.ACTIVE)
            opcoes = [opcao(produto, f"Opção {i}", "A", "B", sort_order=i) for i in range(n_opcoes)]
            variantes = []
            for v in range(n_variantes):
                var = ProductVariant.objects.create(product=produto, sku=f"Q{n_opcoes}{n_variantes}-{v}", size=f"{v} cm", sale_price=Decimal("9"), stock_quantity=9, weight_grams=Decimal("10"))
                var.set_option_values({o: valor(o, "A" if v % 2 else "B") for o in opcoes})
                variantes.append(var)
            carregadas = load_variants({str(i): {"variant_id": var.pk} for i, var in enumerate(variantes)})
            return [CartLine(key=f"{produto.pk}:{var.pk}:-", product=carregadas[var.pk].product, variant=carregadas[var.pk], quantity=1, customization=None, upload=None) for var in variantes]

        pequeno = cenario(1, 1)
        grande = cenario(4, 4)
        with CaptureQueriesContext(connection) as poucas:
            services.create_order(customer=self.customer, lines=pequeno, shipping_address=self.address, billing_address=self.address, shipping_method=self.method, language="pt-br")
        with CaptureQueriesContext(connection) as muitas:
            services.create_order(customer=self.customer, lines=grande, shipping_address=self.address, billing_address=self.address, shipping_method=self.method, language="pt-br")
        # Cada item é um INSERT e uma baixa de estoque; fora isso, o custo não cresce com as opções.
        self.assertLessEqual(len(muitas) - len(poucas), 3 * 4)
        for consulta in muitas:
            self.assertNotIn("catalog_productoption", consulta["sql"].lower())

    def test_the_admin_and_the_emails_do_not_grow_with_the_options(self):
        self.duas_opcoes()
        um = self.pedido()
        for n in range(3, 8):
            o = opcao(self.produto, f"Opção {n}", "A", sort_order=n)
            escolhas = {l.option: l.value for l in self.variante.option_links}
            escolhas[o] = valor(o, "A")
            self.variante.set_option_values(escolhas)
        muitos = self.pedido()
        self.assertEqual(len(muitos.items.get().options_lines), 7)

        def medir(order):
            self.admin_html(order)  # aquece: sessão, primeiro envio (carimbos de «enviado em»)
            self.emails(order)
            with CaptureQueriesContext(connection) as consultas:
                self.admin_html(order)
                self.emails(order)
            return len(consultas)

        self.assertEqual(medir(um), medir(muitos))
