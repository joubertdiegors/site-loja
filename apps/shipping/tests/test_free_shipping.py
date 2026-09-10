"""Frete grátis **por país**, para **uma modalidade**.

A regra mora no país de destino: a partir de X €, a modalidade Y da
transportadora Z sai zero. As outras modalidades continuam com o preço da
tabela, e o cliente escolhe no checkout entre a grátis e as pagas.

    Bélgica: € 50,00 · Mondial Relay / Ponto de coleta

    Mondial Relay · Ponto de coleta   →  Grátis
    Bpost · Standard                  →  € 5,90
    Bpost · Express                   →  € 12,90

Ela é aplicada num lugar só, dentro de `shipping.services.quote()`. Como o
resumo do checkout (`build_draft`) e a criação do pedido (`create_order`) usam
o mesmo motor, os dois chegam ao mesmo número — e o navegador não participa da
conta.

O que estes testes guardam:

* só a modalidade configurada sai de graça; transportadora diferente ou outra
  modalidade da mesma transportadora continuam pagas;
* cada país tem o seu limite e a sua modalidade, e um não interfere no outro;
* abaixo do limite o frete é o de sempre; no limite exato e acima, zero;
* a regra zera o que existe e **não inventa modalidade**: destino sem tarifa
  continua sem opção;
* a configuração incompleta ou incoerente é recusada no servidor;
* a TVA acompanha, o pedido grava zero e congela, e o Stripe recebe a linha.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.cart.cart import CartLine, load_products, load_variants
from apps.cart.keys import line_key
from apps.catalog.choices import resolve_choices
from apps.catalog.models import Color, ColorMode, ProductColor, ProductVariant
from apps.core.models import DeliveryCountry
from apps.core.testing import (
    make_address,
    make_bank_account,
    make_carrier,
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services as order_services
from apps.orders import taxes
from apps.shipping import services


class FreteGratisBase(TestCase):
    """Bélgica: € 50,00 com Mondial Relay / Ponto de coleta. França: € 60,00 com Bpost / Standard."""

    def setUp(self):
        self.bpost = make_carrier(name="Bpost", code="bpost")
        self.mondial = make_carrier(name="Mondial Relay", code="mondial")
        self.standard = make_method(self.bpost, name="Standard", code="standard")
        self.express = make_method(self.bpost, name="Express", code="express", min_days=1, max_days=2)
        self.ponto = make_method(self.mondial, name="Ponto de coleta", code="ponto")

        self.be = make_country("BE", name="Bélgica", vat_rate="21.00")
        self.fr = make_country("FR", name="França", vat_rate="20.00")
        self.nl = make_country("NL", name="Países Baixos", vat_rate="21.00")  # regra desligada

        for pais in (self.be, self.fr, self.nl):
            make_rate(self.standard, pais, 0, 2000, "5.90")
            make_rate(self.express, pais, 0, 2000, "12.90")
            make_rate(self.ponto, pais, 0, 2000, "4.90")

        self.configurar(self.be, "50.00", self.mondial, self.ponto)
        self.configurar(self.fr, "60.00", self.bpost, self.standard)

    @staticmethod
    def configurar(pais, minimo, transportadora, modalidade):
        pais.free_shipping_enabled = True
        pais.free_shipping_min_subtotal = Decimal(minimo)
        pais.free_shipping_carrier = transportadora
        pais.free_shipping_method = modalidade
        pais.full_clean()
        pais.save()
        return pais

    def precos(self, pais, subtotal, peso=500):
        """``{código da modalidade: (preço, é grátis)}`` para este pedido."""
        opcoes = services.quote(pais, peso, subtotal=Decimal(subtotal))
        return {o.method.code: (o.price, o.free_shipping) for o in opcoes}


# ---------------------------------------------------------------------------
# 1. Só a modalidade configurada sai de graça
# ---------------------------------------------------------------------------


class ModalidadeGratisTests(FreteGratisBase):
    def test_the_configured_method_is_free_and_the_others_stay_paid(self):
        precos = self.precos(self.be, "60.00")
        self.assertEqual(precos["ponto"], (Decimal("0.00"), True))
        self.assertEqual(precos["standard"], (Decimal("5.90"), False))
        self.assertEqual(precos["express"], (Decimal("12.90"), False))

    def test_the_customer_still_sees_every_method(self):
        """A grátis não esconde as pagas: o cliente escolhe."""
        opcoes = services.quote(self.be, 500, subtotal=Decimal("60.00"))
        self.assertEqual(len(opcoes), 3)

    def test_the_free_method_leads_the_list(self):
        """É a que o checkout marca quando o cliente ainda não escolheu."""
        opcoes = services.quote(self.be, 500, subtotal=Decimal("60.00"))
        self.assertEqual(opcoes[0].method.code, "ponto")
        self.assertEqual([o.method.code for o in opcoes], ["ponto", "standard", "express"])

    def test_another_carrier_of_the_same_price_is_not_free(self):
        """A Bélgica dá o Ponto de coleta da Mondial; o da Bpost não existe de graça."""
        outro = make_method(self.bpost, name="Ponto Bpost", code="ponto-bpost")
        make_rate(outro, self.be, 0, 2000, "4.90")
        precos = self.precos(self.be, "60.00")
        self.assertEqual(precos["ponto"], (Decimal("0.00"), True))
        self.assertEqual(precos["ponto-bpost"], (Decimal("4.90"), False))

    def test_another_method_of_the_same_carrier_is_not_free(self):
        expresso_mondial = make_method(self.mondial, name="Express", code="mondial-express")
        make_rate(expresso_mondial, self.be, 0, 2000, "9.90")
        precos = self.precos(self.be, "60.00")
        self.assertEqual(precos["ponto"], (Decimal("0.00"), True))
        self.assertEqual(precos["mondial-express"], (Decimal("9.90"), False))

    def test_each_country_frees_its_own_method(self):
        """Bélgica dá o Ponto de coleta; França dá o Standard da Bpost."""
        na_belgica = self.precos(self.be, "100.00")
        na_franca = self.precos(self.fr, "100.00")
        self.assertEqual((na_belgica["ponto"][0], na_belgica["standard"][0]), (Decimal("0.00"), Decimal("5.90")))
        self.assertEqual((na_franca["standard"][0], na_franca["ponto"][0]), (Decimal("0.00"), Decimal("4.90")))

    def test_a_country_with_the_rule_off_pays_everything(self):
        precos = self.precos(self.nl, "10000.00")
        self.assertEqual({v[0] for v in precos.values()}, {Decimal("5.90"), Decimal("12.90"), Decimal("4.90")})
        self.assertFalse(any(v[1] for v in precos.values()))


# ---------------------------------------------------------------------------
# 2. O limite
# ---------------------------------------------------------------------------


class LimiteTests(FreteGratisBase):
    def test_below_the_threshold_everything_is_paid(self):
        precos = self.precos(self.be, "49.99")
        self.assertEqual(precos["ponto"], (Decimal("4.90"), False))
        self.assertEqual(precos["standard"], (Decimal("5.90"), False))

    def test_exactly_the_threshold_is_already_free(self):
        self.assertEqual(self.precos(self.be, "50.00")["ponto"], (Decimal("0.00"), True))

    def test_above_the_threshold_is_free(self):
        self.assertEqual(self.precos(self.be, "120.00")["ponto"], (Decimal("0.00"), True))

    def test_each_country_has_its_own_threshold(self):
        """€ 55,00: alcança a Bélgica (50) e não a França (60)."""
        self.assertEqual(self.precos(self.be, "55.00")["ponto"][0], Decimal("0.00"))
        self.assertEqual(self.precos(self.fr, "55.00")["standard"][0], Decimal("5.90"))
        self.assertEqual(self.precos(self.fr, "60.00")["standard"][0], Decimal("0.00"))

    def test_the_model_answers_the_question_by_itself(self):
        self.assertFalse(self.be.free_shipping_applies(Decimal("49.99")))
        self.assertTrue(self.be.free_shipping_applies(Decimal("50.00")))
        self.assertFalse(self.be.free_shipping_applies(None))
        self.assertEqual(self.be.free_shipping_method_id_for(Decimal("50.00")), self.ponto.pk)
        self.assertIsNone(self.be.free_shipping_method_id_for(Decimal("49.99")))
        self.assertIsNone(self.nl.free_shipping_method_id_for(Decimal("999.00")))


# ---------------------------------------------------------------------------
# 3. Configuração incompleta ou incoerente
# ---------------------------------------------------------------------------


class ConfiguracaoTests(FreteGratisBase):
    def erros(self, **campos):
        pais = DeliveryCountry(iso_code="ZZ", is_active=True, free_shipping_enabled=True, **campos)
        with self.assertRaises(ValidationError) as erro:
            pais.full_clean()
        return erro.exception.error_dict

    def test_enabling_without_anything_lists_the_three_gaps(self):
        erros = self.erros()
        self.assertEqual(
            set(erros),
            {"free_shipping_min_subtotal", "free_shipping_carrier", "free_shipping_method"},
        )

    def test_a_method_of_another_carrier_is_refused(self):
        """A modalidade tem que ser da transportadora escolhida — conferido no servidor."""
        erros = self.erros(
            free_shipping_min_subtotal=Decimal("50.00"),
            free_shipping_carrier=self.mondial,
            free_shipping_method=self.standard,  # Standard é da Bpost
        )
        self.assertIn("free_shipping_method", erros)
        self.assertIn("Bpost", str(erros["free_shipping_method"][0]))

    def test_a_coherent_pair_is_accepted(self):
        pais = DeliveryCountry(
            iso_code="ZZ", is_active=True, free_shipping_enabled=True,
            free_shipping_min_subtotal=Decimal("70.00"),
            free_shipping_carrier=self.mondial, free_shipping_method=self.ponto,
        )
        pais.full_clean()  # não levanta

    def test_the_rule_off_needs_nothing(self):
        DeliveryCountry(iso_code="ZZ", is_active=True).full_clean()  # não levanta

    def test_a_half_saved_rule_never_frees_anything(self):
        """Rede de baixo: dado gravado fora do Admin não zera frete por engano."""
        for campos in (
            {"free_shipping_method": None},
            {"free_shipping_carrier": None},
            {"free_shipping_min_subtotal": Decimal("0.00")},
        ):
            with self.subTest(campos=list(campos)):
                DeliveryCountry.objects.filter(pk=self.be.pk).update(**campos)
                pais = DeliveryCountry.objects.get(pk=self.be.pk)
                self.assertIsNone(pais.free_shipping_method_id_for(Decimal("500.00")))
                self.assertEqual(self.precos(pais, "500.00")["ponto"][0], Decimal("4.90"))
                self.configurar(pais, "50.00", self.mondial, self.ponto)

    def test_deleting_the_method_does_not_break_the_country(self):
        self.ponto.delete()
        pais = DeliveryCountry.objects.get(pk=self.be.pk)
        self.assertIsNone(pais.free_shipping_method_id)
        self.assertEqual(self.precos(pais, "500.00")["standard"][0], Decimal("5.90"))


# ---------------------------------------------------------------------------
# 4. O motor: zera o que existe, nunca inventa modalidade
# ---------------------------------------------------------------------------


class MotorTests(FreteGratisBase):
    def test_a_destination_without_a_rate_has_no_option_even_above_the_threshold(self):
        pt = make_country("PT", name="Portugal")
        self.configurar(pt, "50.00", self.mondial, self.ponto)
        self.assertEqual(services.quote(pt, 500, subtotal=Decimal("500.00")), [])

    def test_the_free_method_without_a_rate_for_the_weight_simply_is_not_there(self):
        """Nada de modalidade inventada: o Ponto de coleta some, as pagas ficam."""
        make_rate(self.standard, self.be, 2000, 5000, "9.90")
        make_rate(self.express, self.be, 2000, 5000, "19.90")
        precos = self.precos(self.be, "500.00", peso=3000)
        self.assertNotIn("ponto", precos)
        self.assertEqual(precos["standard"], (Decimal("9.90"), False))

    def test_a_weight_above_every_range_has_no_option(self):
        self.assertEqual(services.quote(self.be, 9000, subtotal=Decimal("500.00")), [])

    def test_without_a_subtotal_the_rule_does_not_apply(self):
        self.assertEqual(services.quote(self.be, 500)[0].price, Decimal("4.90"))
        self.assertFalse(any(o.free_shipping for o in services.quote(self.be, 500)))

    def test_quote_for_method_follows_the_same_rule(self):
        gratis = services.quote_for_method(self.be, 500, self.ponto, subtotal=Decimal("80.00"))
        paga = services.quote_for_method(self.be, 500, self.standard, subtotal=Decimal("80.00"))
        abaixo = services.quote_for_method(self.be, 500, self.ponto, subtotal=Decimal("10.00"))
        self.assertEqual((gratis.price, gratis.free_shipping), (Decimal("0.00"), True))
        self.assertEqual((paga.price, paga.free_shipping), (Decimal("5.90"), False))
        self.assertEqual((abaixo.price, abaixo.free_shipping), (Decimal("4.90"), False))

    def test_an_inactive_country_has_no_option(self):
        DeliveryCountry.objects.filter(pk=self.be.pk).update(is_active=False)
        pais = DeliveryCountry.objects.get(pk=self.be.pk)
        self.assertEqual(services.quote(pais, 500, subtotal=Decimal("500")), [])

    def test_a_rate_that_costs_zero_is_not_marked_as_free_shipping(self):
        """Tarifa zerada no cadastro é outra coisa: a tela não diz «Grátis» por ela."""
        pt = make_country("PT", name="Portugal")
        make_rate(self.standard, pt, 0, 2000, "0.00")
        opcao = services.quote(pt, 500, subtotal=Decimal("10.00"))[0]
        self.assertEqual((opcao.price, opcao.free_shipping), (Decimal("0.00"), False))


# ---------------------------------------------------------------------------
# 5. O checkout e o pedido
# ---------------------------------------------------------------------------


class CheckoutBase(FreteGratisBase):
    def setUp(self):
        super().setUp()
        make_bank_account()
        self.categoria = make_category(slug="vasos", name="Vasos")
        self.user = make_user(username="cliente", email="cliente@example.com")
        self.endereco = make_address(self.user.customer, self.be)
        self.produto = make_product(
            sku="VASO-01", name="Vaso", category=self.categoria, with_variant=False
        )
        self.variante = ProductVariant.objects.create(
            product=self.produto, sku="VASO-01-V01", sale_price=Decimal("30.00"),
            stock_quantity=10, weight_grams=Decimal("200"),
        )

    def linhas(self, quantidade=1, raw=None):
        items = {"x": {"product_id": self.produto.pk, "variant_id": self.variante.pk}}
        produto = load_products(items)[self.produto.pk]
        variante = load_variants(items)[self.variante.pk]
        escolhas = resolve_choices(produto, raw) if raw else ()
        return [
            CartLine(
                key=line_key(produto.pk, variante.pk, None, raw),
                product=produto, variant=variante, quantity=quantidade, choices=escolhas,
            )
        ]

    def pedido(self, linhas, metodo=None, endereco=None):
        endereco = endereco or self.endereco
        return order_services.create_order(
            customer=self.user.customer, lines=linhas, shipping_address=endereco,
            billing_address=endereco, shipping_method=metodo or self.ponto, language="pt-br",
        )


class ResumoDoCheckoutTests(CheckoutBase):
    def test_below_the_threshold_the_draft_charges_the_shipping(self):
        draft = order_services.build_draft(self.linhas(1), country=self.be, method=self.ponto)
        self.assertEqual((draft.subtotal, draft.shipping_total), (Decimal("30.00"), Decimal("4.90")))
        self.assertFalse(draft.is_free_shipping)
        self.assertEqual(draft.total, Decimal("34.90"))

    def test_above_the_threshold_the_configured_method_is_free(self):
        draft = order_services.build_draft(self.linhas(2), country=self.be, method=self.ponto)
        self.assertEqual((draft.subtotal, draft.shipping_total), (Decimal("60.00"), Decimal("0.00")))
        self.assertTrue(draft.is_free_shipping)
        self.assertEqual(draft.total, Decimal("60.00"))

    def test_choosing_a_paid_method_above_the_threshold_still_pays(self):
        """O cliente pode preferir a Bpost: aí ele paga, e o resumo mostra."""
        draft = order_services.build_draft(self.linhas(2), country=self.be, method=self.standard)
        self.assertEqual(draft.shipping_total, Decimal("5.90"))
        self.assertFalse(draft.is_free_shipping)
        self.assertEqual(draft.total, Decimal("65.90"))

    def test_without_choosing_the_free_method_is_the_default(self):
        draft = order_services.build_draft(self.linhas(2), country=self.be)
        self.assertEqual(draft.shipping_option.method.code, "ponto")
        self.assertEqual(draft.shipping_total, Decimal("0.00"))

    def test_the_three_options_are_offered_with_their_prices(self):
        draft = order_services.build_draft(self.linhas(2), country=self.be, method=self.ponto)
        self.assertEqual(
            [(o.method.code, o.price) for o in draft.options],
            [("ponto", Decimal("0.00")), ("standard", Decimal("5.90")), ("express", Decimal("12.90"))],
        )

    def test_the_tax_follows_the_smaller_base(self):
        """A TVA está incluída no total: sem frete, a base cai e o imposto também."""
        pago = order_services.build_draft(self.linhas(1), country=self.be, method=self.ponto)
        gratis = order_services.build_draft(self.linhas(2), country=self.be, method=self.ponto)
        self.assertEqual(pago.tax.amount, taxes.included_tax(Decimal("34.90"), Decimal("21.00")))
        self.assertEqual(gratis.tax.amount, taxes.included_tax(Decimal("60.00"), Decimal("21.00")))
        self.assertEqual(gratis.tax.taxable, Decimal("60.00"))

    def test_the_tax_of_a_paid_method_keeps_the_shipping_in_the_base(self):
        draft = order_services.build_draft(self.linhas(2), country=self.be, method=self.standard)
        self.assertEqual(draft.tax.amount, taxes.included_tax(Decimal("65.90"), Decimal("21.00")))


class PedidoTests(CheckoutBase):
    def test_the_order_saves_zero_shipping_for_the_configured_method(self):
        pedido = self.pedido(self.linhas(2))
        self.assertEqual((pedido.subtotal, pedido.shipping_total, pedido.total),
                         (Decimal("60.00"), Decimal("0.00"), Decimal("60.00")))
        self.assertEqual(pedido.shipping_method_label, self.ponto.label)
        self.assertEqual(pedido.tax_total, taxes.included_tax(Decimal("60.00"), Decimal("21.00")))

    def test_the_order_of_a_paid_method_saves_its_price(self):
        pedido = self.pedido(self.linhas(2), metodo=self.standard)
        self.assertEqual((pedido.shipping_total, pedido.total), (Decimal("5.90"), Decimal("65.90")))
        self.assertEqual(pedido.shipping_method_label, self.standard.label)

    def test_below_the_threshold_the_order_charges_the_shipping(self):
        pedido = self.pedido(self.linhas(1))
        self.assertEqual((pedido.subtotal, pedido.shipping_total, pedido.total),
                         (Decimal("30.00"), Decimal("4.90"), Decimal("34.90")))

    def test_an_old_order_is_never_touched_when_the_rule_changes(self):
        pedido = self.pedido(self.linhas(2))
        self.assertEqual(pedido.shipping_total, Decimal("0.00"))

        DeliveryCountry.objects.filter(pk=self.be.pk).update(
            free_shipping_enabled=False, free_shipping_method=None, free_shipping_carrier=None
        )
        pedido.refresh_from_db()

        self.assertEqual((pedido.shipping_total, pedido.total), (Decimal("0.00"), Decimal("60.00")))
        self.assertEqual(pedido.tax_total, taxes.included_tax(Decimal("60.00"), Decimal("21.00")))

    def test_an_order_made_before_the_rule_keeps_its_shipping(self):
        DeliveryCountry.objects.filter(pk=self.be.pk).update(free_shipping_enabled=False)
        # O endereço carrega o país; relê para o pedido ver a regra desligada
        # (no checkout ele vem de uma consulta nova, em `clean_shipping_address`).
        antigo = self.pedido(self.linhas(2), endereco=type(self.endereco).objects.get(pk=self.endereco.pk))
        self.assertEqual(antigo.shipping_total, Decimal("4.90"))

        DeliveryCountry.objects.filter(pk=self.be.pk).update(free_shipping_enabled=True)
        antigo.refresh_from_db()

        self.assertEqual((antigo.shipping_total, antigo.total), (Decimal("4.90"), Decimal("64.90")))


class SubtotalComEscolhaTests(CheckoutBase):
    """O adicional da cor à escolha conta para alcançar o frete grátis."""

    def setUp(self):
        super().setUp()
        type(self.produto).objects.filter(pk=self.produto.pk).update(color_mode=ColorMode.CUSTOM)
        dourado = Color.objects.create(name="Dourado", hex_code="#D4AF37")
        self.linha_dourado = ProductColor.objects.create(
            product=self.produto, color=dourado, sort_order=0, price_delta=Decimal("2.00")
        )
        self.produto.refresh_from_db()
        ProductVariant.objects.filter(pk=self.variante.pk).update(sale_price=Decimal("24.00"))

    def test_the_choice_adjustment_counts_towards_the_threshold(self):
        """€ 48,00 de produto + € 4,00 das cores = € 52,00: alcança o limite."""
        draft = order_services.build_draft(
            self.linhas(2, raw={"color": self.linha_dourado.pk}), country=self.be, method=self.ponto
        )
        self.assertEqual((draft.subtotal, draft.shipping_total), (Decimal("52.00"), Decimal("0.00")))
        self.assertTrue(draft.is_free_shipping)

    def test_without_the_adjustment_the_same_order_pays_shipping(self):
        draft = order_services.build_draft(self.linhas(2), country=self.be, method=self.ponto)
        self.assertEqual((draft.subtotal, draft.shipping_total), (Decimal("48.00"), Decimal("4.90")))

    def test_the_order_agrees_with_the_draft(self):
        linhas = self.linhas(2, raw={"color": self.linha_dourado.pk})
        draft = order_services.build_draft(linhas, country=self.be, method=self.ponto)
        pedido = self.pedido(linhas)
        self.assertEqual((pedido.subtotal, pedido.shipping_total), (draft.subtotal, draft.shipping_total))
        self.assertEqual(pedido.shipping_total, Decimal("0.00"))


# ---------------------------------------------------------------------------
# 6. O pagamento e o Admin
# ---------------------------------------------------------------------------


class StripeTests(CheckoutBase):
    def provider(self):
        from apps.orders.payments.stripe_provider import StripeProvider

        return StripeProvider()

    def test_a_free_shipping_order_still_sends_the_delivery_line(self):
        """Zero é um preço válido: o cliente vê a modalidade e «€ 0,00» no pagamento."""
        opcoes = self.provider()._shipping_options(self.pedido(self.linhas(2)))
        self.assertEqual(len(opcoes), 1)
        dados = opcoes[0]["shipping_rate_data"]
        self.assertEqual(dados["fixed_amount"]["amount"], 0)
        self.assertEqual(dados["display_name"], self.ponto.label)

    def test_a_paid_order_sends_the_price_in_cents(self):
        opcoes = self.provider()._shipping_options(self.pedido(self.linhas(2), metodo=self.standard))
        self.assertEqual(opcoes[0]["shipping_rate_data"]["fixed_amount"]["amount"], 590)

    def test_an_order_without_shipping_at_all_sends_nothing(self):
        pedido = self.pedido(self.linhas(2))
        pedido.shipping_method_label = ""
        pedido.save(update_fields=["shipping_method_label"])
        self.assertEqual(self.provider()._shipping_options(pedido), [])


class AdminTests(FreteGratisBase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_superuser(
            "adm", "adm@jdprint.test", "senha-de-teste-77"
        )
        self.client.force_login(self.admin)

    def ficha(self, pais=None):
        return self.client.get(
            reverse("admin:core_deliverycountry_change", args=[(pais or self.be).pk])
        ).content.decode()

    def test_the_country_sheet_offers_the_four_fields(self):
        html = self.ficha()
        self.assertIn("FRETE GRÁTIS", html)
        for campo in (
            "free_shipping_enabled",
            "free_shipping_min_subtotal",
            "free_shipping_carrier",
            "free_shipping_method",
        ):
            with self.subTest(campo=campo):
                self.assertIn(f'name="{campo}"', html)
        self.assertIn("vale para <b>uma modalidade</b>", html)

    def test_each_method_option_carries_its_carrier(self):
        """É o que permite à tela filtrar a modalidade pela transportadora."""
        html = self.ficha()
        self.assertIn(f'data-carrier="{self.mondial.pk}"', html)
        self.assertIn(f'data-carrier="{self.bpost.pk}"', html)
        self.assertIn("admin/js/free_shipping_admin.js", html)

    def test_the_javascript_filters_the_method_by_the_carrier(self):
        from pathlib import Path

        from django.conf import settings

        script = (
            Path(settings.BASE_DIR) / "static" / "admin" / "js" / "free_shipping_admin.js"
        ).read_text(encoding="utf-8")
        self.assertIn("id_free_shipping_carrier", script)
        self.assertIn("id_free_shipping_method", script)
        self.assertIn("data-carrier", script)

    def test_the_list_shows_the_threshold_and_the_method(self):
        html = self.client.get(reverse("admin:core_deliverycountry_changelist")).content.decode()
        self.assertIn("a partir de € 50,00", html)
        self.assertIn("Mondial Relay — Ponto de coleta", html)
        self.assertIn("a partir de € 60,00", html)

    def test_the_list_warns_when_the_method_is_missing(self):
        DeliveryCountry.objects.filter(pk=self.be.pk).update(free_shipping_method=None)
        html = self.client.get(reverse("admin:core_deliverycountry_changelist")).content.decode()
        self.assertIn("sem modalidade", html)

    def test_saving_a_method_of_another_carrier_is_refused_by_the_server(self):
        """A tela filtra; o POST vem do navegador, e o servidor confere o par."""
        resposta = self.client.post(
            reverse("admin:core_deliverycountry_change", args=[self.be.pk]),
            {
                "iso_code": "BE", "is_active": "on", "sort_order": "0", "vat_rate": "21.00",
                "free_shipping_enabled": "on",
                "free_shipping_min_subtotal": "50.00",
                "free_shipping_carrier": str(self.mondial.pk),
                "free_shipping_method": str(self.standard.pk),  # da Bpost
                "translations-TOTAL_FORMS": "0", "translations-INITIAL_FORMS": "0",
                "translations-MIN_NUM_FORMS": "0", "translations-MAX_NUM_FORMS": "1000",
                "_save": "Salvar",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("modalidade de Bpost", resposta.content.decode())
        self.assertEqual(
            DeliveryCountry.objects.get(pk=self.be.pk).free_shipping_method_id, self.ponto.pk
        )
