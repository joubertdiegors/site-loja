"""«Cores à escolha do cliente» no pedido: snapshot, preço congelado, telas e e-mails.

O pedido é uma fotografia. A escolha do cliente («Cor: Dourado (+ € 2,00)») e o
adicional que valia na compra ficam gravados no ``OrderItem``; mudar a paleta,
o adicional ou a cor depois não muda o que o cliente comprou nem o que pagou.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from apps.cart.cart import CartLine, load_products, load_variants
from apps.cart.keys import line_key
from apps.catalog.choices import resolve_choices
from apps.catalog.models import (
    Color,
    ColorMode,
    ColorTranslation,
    PersonalizationType,
    ProductColor,
    ProductVariant,
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
from apps.orders.emails import send_admin_order_email, send_order_confirmation_email
from apps.orders.models import OrderItem

ADMINS = ["producao@jd-print.test"]


def cor(nome, hexa, **traducoes):
    color = Color.objects.create(name=nome, hex_code=hexa)
    for idioma, texto in traducoes.items():
        ColorTranslation.objects.create(master=color, language=idioma, name=texto)
    color.refresh_translations()
    return color


@override_settings(ORDER_ADMIN_EMAILS=ADMINS)
class OrderBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="cliente", email="cliente@example.com")
        self.customer = self.user.customer
        self.address = make_address(self.customer, self.country)

        self.category = make_category(slug="decoracao", name="Decoração")
        self.branco = cor("Branco", "#FFFFFF", pt="Branco", fr="Blanc")
        self.dourado = cor("Dourado", "#D4AF37", pt="Dourado", fr="Doré")
        self.product = make_product(
            sku="PORTA", name="Porta-Retrato Litofânico", category=self.category,
            with_variant=False, color_mode=ColorMode.CUSTOM,
        )
        self.com_foto = ProductVariant.objects.create(
            product=self.product, sku="PORTA-V02", size="Porta-Retrato + Foto",
            sale_price=Decimal("20.00"), stock_quantity=10, weight_grams=Decimal("200"),
        )
        self.row_branco = ProductColor.objects.create(product=self.product, color=self.branco, sort_order=0)
        self.row_dourado = ProductColor.objects.create(
            product=self.product, color=self.dourado, sort_order=1, price_delta=Decimal("2.00")
        )
        self.product.refresh_from_db()

    # -- linhas e pedido ---------------------------------------------------

    def linha(self, row=None, quantidade=1, customization=None):
        """Uma linha como o carrinho a montaria: produto e variante do prefetch, escolha resolvida."""
        items = {"x": {"product_id": self.product.pk, "variant_id": self.com_foto.pk}}
        produto = load_products(items)[self.product.pk]
        variante = load_variants(items)[self.com_foto.pk]
        raw = {"color": row.pk} if row is not None else {}
        escolhas = resolve_choices(produto, raw) if row is not None else ()
        return CartLine(
            key=line_key(produto.pk, variante.pk, customization, raw),
            product=produto, variant=variante, quantity=quantidade,
            customization=customization, upload=None, choices=escolhas,
        )

    def pedido(self, *linhas, language="pt-br"):
        with translation.override(language):
            return services.create_order(
                customer=self.customer, lines=list(linhas), shipping_address=self.address,
                billing_address=self.address, shipping_method=self.method, language=language,
            )


# ---------------------------------------------------------------------------
# 1. O snapshot
# ---------------------------------------------------------------------------


class SnapshotTests(OrderBase):
    def test_the_item_freezes_the_choice_and_the_final_price(self):
        pedido = self.pedido(self.linha(self.row_dourado, quantidade=2))
        item = pedido.items.get()
        self.assertEqual(item.variant, self.com_foto)
        self.assertEqual(item.variant_label, "Porta-Retrato + Foto")
        self.assertEqual(item.choices_snapshot, "Cor: Dourado (+ € 2,00)")
        self.assertEqual(item.choices_lines, ["Cor: Dourado (+ € 2,00)"])
        self.assertEqual(item.price_adjustment, Decimal("2.00"))
        self.assertEqual(item.unit_price, Decimal("22.00"))
        self.assertEqual(item.base_unit_price, Decimal("20.00"))
        self.assertEqual(item.total, Decimal("44.00"))
        self.assertEqual(pedido.subtotal, Decimal("44.00"))
        # O que a variante É continua no lugar de sempre — e sem cor, porque
        # a cor não é um eixo desta variante.
        self.assertEqual(item.color_name, "")
        self.assertEqual(item.options_snapshot, "")
        self.assertEqual(item.colors_snapshot, "Cores à escolha")

    def test_a_colour_without_delta(self):
        item = self.pedido(self.linha(self.row_branco)).items.get()
        self.assertEqual(item.choices_snapshot, "Cor: Branco")
        self.assertEqual(item.price_adjustment, Decimal("0.00"))
        self.assertEqual(item.unit_price, Decimal("20.00"))

    def test_the_snapshot_is_in_the_customers_language(self):
        item = self.pedido(self.linha(self.row_dourado), language="fr").items.get()
        self.assertEqual(item.choices_snapshot, "Couleur: Doré (+ € 2,00)")

    def test_changing_the_delta_later_does_not_touch_the_order(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        ProductColor.objects.filter(pk=self.row_dourado.pk).update(price_delta=Decimal("3.00"))
        item = OrderItem.objects.get(pk=pedido.items.get().pk)
        self.assertEqual(item.choices_snapshot, "Cor: Dourado (+ € 2,00)")
        self.assertEqual(item.unit_price, Decimal("22.00"))
        self.assertEqual(item.price_adjustment, Decimal("2.00"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.subtotal, Decimal("22.00"))

    def test_renaming_or_removing_the_colour_later_does_not_touch_the_order(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        ColorTranslation.objects.filter(master=self.dourado, language="pt").update(name="Ouro")
        self.row_dourado.delete()
        type(self.product).objects.filter(pk=self.product.pk).update(color_mode=ColorMode.NONE)
        item = OrderItem.objects.get(pk=pedido.items.get().pk)
        self.assertEqual(item.choices_lines, ["Cor: Dourado (+ € 2,00)"])
        self.assertEqual(item.unit_price, Decimal("22.00"))

    def test_a_line_without_choice_leaves_the_new_fields_empty(self):
        ProductColor.objects.filter(product=self.product).delete()
        self.product.refresh_from_db()
        item = self.pedido(self.linha()).items.get()
        self.assertEqual(item.choices_snapshot, "")
        self.assertEqual(item.choices_lines, [])
        self.assertEqual(item.price_adjustment, Decimal("0.00"))
        self.assertEqual(item.unit_price, Decimal("20.00"))

    def test_colour_and_personalization_together(self):
        type(self.product).objects.filter(pk=self.product.pk).update(personalization_type=PersonalizationType.TEXT)
        personalizacao = {"type": "text", "upload_id": None, "text": "Marie", "notes": ""}
        item = self.pedido(self.linha(self.row_dourado, customization=personalizacao)).items.get()
        self.assertEqual(item.choices_snapshot, "Cor: Dourado (+ € 2,00)")
        self.assertEqual(item.personalization_type, "text")
        self.assertEqual(item.personalization_text, "Marie")
        self.assertEqual(item.unit_price, Decimal("22.00"))
        self.assertEqual(item.fulfillment_type, "personalized")


# ---------------------------------------------------------------------------
# 2. Revalidação, estoque e retomar pagamento
# ---------------------------------------------------------------------------


class CheckoutTests(OrderBase):
    def test_a_line_missing_a_required_choice_is_refused(self):
        with self.assertRaises(services.CheckoutError) as contexto:
            self.pedido(self.linha())
        self.assertIn("precisa da escolha: Cor", contexto.exception.problems[0].message)

    def test_the_stock_comes_off_the_variant_whatever_the_colour(self):
        pedido = self.pedido(self.linha(self.row_branco, quantidade=2), self.linha(self.row_dourado, quantidade=3))
        services.confirm_payment(pedido, method_label="teste")
        self.com_foto.refresh_from_db()
        self.assertEqual(self.com_foto.stock_quantity, 5)
        self.assertEqual(sorted(i.stock_taken for i in pedido.items.all()), [2, 3])

    def test_retry_payment_shows_the_frozen_choice_and_price(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        ProductColor.objects.filter(pk=self.row_dourado.pk).update(price_delta=Decimal("9.00"))
        self.client.force_login(self.user)
        resposta = self.client.get(reverse("orders:retry_payment", kwargs={"number": pedido.number}))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Cor: Dourado (+ € 2,00)")
        # 22,00 do item + 5,90 de entrega: o total congelado — e não 29,00 + 5,90.
        self.assertContains(resposta, "27,90")
        self.assertEqual(resposta.context["order"].items.get().unit_price, Decimal("22.00"))
        self.assertEqual(resposta.context["order"].total, Decimal("27.90"))


# ---------------------------------------------------------------------------
# 3. Onde a escolha aparece
# ---------------------------------------------------------------------------


class DisplayTests(OrderBase):
    def test_the_confirmation_and_admin_emails_show_the_choice(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        mail.outbox = []
        send_order_confirmation_email(pedido)
        send_admin_order_email(pedido)
        self.assertEqual(len(mail.outbox), 2)
        for mensagem in mail.outbox:
            self.assertIn("Cor: Dourado (+ € 2,00)", mensagem.body)
            for html, _tipo in mensagem.alternatives:
                self.assertIn("Cor: Dourado (+ € 2,00)", html)

    def test_the_customer_account_shows_the_choice(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        self.client.force_login(self.user)
        resposta = self.client.get(reverse("orders:detail", kwargs={"number": pedido.number}))
        self.assertContains(resposta, "Cor: Dourado (+ € 2,00)")

    def test_the_admin_shows_the_choice(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        adm = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(adm)
        resposta = self.client.get(reverse("admin:orders_order_change", args=[pedido.pk]))
        self.assertContains(resposta, "Cor: Dourado (+ € 2,00)")
        self.assertContains(resposta, "Escolhas")

    def test_orders_without_choice_do_not_get_the_label(self):
        ProductColor.objects.filter(product=self.product).delete()
        self.product.refresh_from_db()
        pedido = self.pedido(self.linha())
        adm = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(adm)
        resposta = self.client.get(reverse("admin:orders_order_change", args=[pedido.pk]))
        self.assertNotContains(resposta, "Escolhas:")


# ---------------------------------------------------------------------------
# 5. Desconto e cor composta, congelados
# ---------------------------------------------------------------------------


class DiscountAndCompositeTests(OrderBase):
    def test_a_discount_is_frozen_with_the_final_price(self):
        vermelho = cor("Vermelho", "#FF0000", pt="Vermelho", fr="Rouge")
        row = ProductColor.objects.create(
            product=self.product, color=vermelho, sort_order=2, price_delta=Decimal("-1.00")
        )
        self.product.refresh_from_db()
        pedido = self.pedido(self.linha(row))
        item = pedido.items.get()
        self.assertEqual(item.price_adjustment, Decimal("-1.00"))
        self.assertEqual(item.unit_price, Decimal("19.00"))
        self.assertEqual(item.total, Decimal("19.00"))
        self.assertEqual(item.choices_snapshot, "Cor: Vermelho (− € 1,00)")
        ProductColor.objects.filter(pk=row.pk).update(price_delta=Decimal("-5.00"))
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal("19.00"))
        self.assertEqual(item.price_adjustment, Decimal("-1.00"))

    def test_a_composite_colour_choice_is_frozen_in_the_customers_language(self):
        azul = cor("Azul", "#0000FF", pt="Azul", fr="Bleu")
        composta = cor("Branco + Azul", "")
        composta.set_components([self.branco, azul])
        row = ProductColor.objects.create(
            product=self.product, color=composta, sort_order=2, price_delta=Decimal("1.00")
        )
        self.product.refresh_from_db()
        pedido = self.pedido(self.linha(row), language="fr")
        item = pedido.items.get()
        self.assertEqual(item.choices_snapshot, "Couleur: Blanc + Bleu (+ € 1,00)")
        self.assertEqual(item.unit_price, Decimal("21.00"))
        # Desmontar a composta e renomear a componente depois não mexe no pedido.
        composta.set_components([])
        ColorTranslation.objects.filter(master=azul).update(name="Cyan")
        item.refresh_from_db()
        self.assertEqual(item.choices_snapshot, "Couleur: Blanc + Bleu (+ € 1,00)")


# ---------------------------------------------------------------------------
# 6. O rótulo da escolha, congelado no pedido
# ---------------------------------------------------------------------------


class ChoiceLabelInTheOrderTests(OrderBase):
    def setUp(self):
        super().setUp()
        from apps.catalog.models import ProductTranslation

        ProductTranslation.objects.filter(master=self.product, language="pt").update(
            color_choice_label="Cor do pompom"
        )
        ProductTranslation.objects.create(
            master=self.product, language="fr", name="Cadre photo", color_choice_label="Couleur du pompon"
        )
        ProductVariant.objects.filter(pk=self.com_foto.pk).update(color=self.branco)
        self.product.refresh_from_db()

    def test_the_label_is_frozen_in_the_customers_language_apart_from_the_variant_colour(self):
        pedido = self.pedido(self.linha(self.row_dourado), language="fr")
        item = pedido.items.get()
        self.assertEqual(item.choices_snapshot, "Couleur du pompon: Doré (+ € 2,00)")
        self.assertEqual(item.choices_lines, ["Couleur du pompon: Doré (+ € 2,00)"])
        self.assertEqual(item.color_name, "Blanc")
        self.assertEqual(item.variant_label, "Blanc · Porta-Retrato + Foto")
        self.assertEqual(item.unit_price, Decimal("22.00"))
        self.assertEqual(item.price_adjustment, Decimal("2.00"))

    def test_without_a_label_in_the_language_the_order_says_cor_translated(self):
        pedido = self.pedido(self.linha(self.row_dourado), language="nl")
        self.assertEqual(pedido.items.get().choices_snapshot, "Kleur: Dourado (+ € 2,00)")

    def test_renaming_the_label_later_does_not_touch_the_order(self):
        from apps.catalog.models import ProductTranslation

        pedido = self.pedido(self.linha(self.row_dourado))
        ProductTranslation.objects.filter(master=self.product, language="pt").update(
            color_choice_label="Cor do laço"
        )
        item = pedido.items.get()
        item.refresh_from_db()
        self.assertEqual(item.choices_snapshot, "Cor do pompom: Dourado (+ € 2,00)")

    def test_emails_account_admin_and_retry_show_the_labelled_choice(self):
        pedido = self.pedido(self.linha(self.row_dourado))
        mail.outbox = []
        send_order_confirmation_email(pedido)
        send_admin_order_email(pedido)
        for mensagem in mail.outbox:
            self.assertIn("Cor do pompom: Dourado (+ € 2,00)", mensagem.body)
            self.assertIn("Branco · Porta-Retrato + Foto", mensagem.body)
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get(reverse("orders:detail", kwargs={"number": pedido.number})),
            "Cor do pompom: Dourado (+ € 2,00)",
        )
        self.assertContains(
            self.client.get(reverse("orders:retry_payment", kwargs={"number": pedido.number})),
            "Cor do pompom: Dourado (+ € 2,00)",
        )
        adm = get_user_model().objects.create_superuser("adm", "adm@jdprint.test", "senha-de-teste-77")
        self.client.force_login(adm)
        self.assertContains(
            self.client.get(reverse("admin:orders_order_change", args=[pedido.pk])),
            "Cor do pompom: Dourado (+ € 2,00)",
        )
