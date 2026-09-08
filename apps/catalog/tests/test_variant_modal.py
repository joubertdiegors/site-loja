"""O cadastro de variantes: tabela compacta + modal.

O que mudou de comportamento e precisa de prova:

* os campos de edição **não** ficam mais abaixo da tabela — eles vivem num
  modal `position: fixed`;
* na tela de edição, o "Salvar" do modal grava a variante de verdade, num
  endpoint próprio, e devolve os erros por campo para o modal continuar aberto
  com o que já foi digitado;
* na tela de cadastro o produto ainda não tem PK, então não há endpoint: os
  campos continuam sendo os do formset e a gravação acontece junto com o
  produto. A tela diz isso.

O resto — matriz da vitrine, carrinho, frete, tradução — não podia ser tocado,
e há teste aqui cobrando isso.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import (
    Color,
    ColorTranslation,
    Material,
    MaterialTranslation,
    PricingMode,
    Product,
    ProductStatus,
    ProductVariant,
)
from apps.core.testing import make_category, make_product


def traduzir(obj, modelo, **nomes):
    for idioma, nome in nomes.items():
        modelo.objects.create(master=obj, language=idioma, name=nome)
    obj.refresh_translations()
    return obj


class ModalBase(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jd.test", password="senha-de-teste"
        )
        self.client.force_login(self.staff)

        self.category = make_category(slug="modelos", name="Modelos")
        self.preto = traduzir(
            Color.objects.create(name="Preto", hex_code="#000000"),
            ColorTranslation, pt="Preto", fr="Noir",
        )
        self.branco = traduzir(
            Color.objects.create(name="Branco", hex_code="#FFFFFF"),
            ColorTranslation, pt="Branco", fr="Blanc",
        )
        self.resina = traduzir(
            Material.objects.create(name="Resina"),
            MaterialTranslation, pt="Resina", fr="Résine",
        )

        self.product = make_product(
            sku="DINO",
            name="Dinossauro",
            category=self.category,
            status=ProductStatus.ACTIVE,
            with_variant=False,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DINO-PRETO-25",
            color=self.preto,
            material=self.resina,
            size="25 cm",
            sale_price=Decimal("20.00"),
            filament_cost=Decimal("4.00"),
            weight_grams=Decimal("300"),
            production_lead_time_days=2,
            stock_quantity=10,
        )
        self.product.refresh_from_db()

    def change_url(self):
        return reverse("admin:catalog_product_change", args=[self.product.pk])

    def save_url(self):
        return reverse("admin:catalog_product_variant_save", args=[self.product.pk])

    def delete_url(self, variant):
        return reverse(
            "admin:catalog_product_variant_delete", args=[self.product.pk, variant.pk]
        )

    def payload(self, **overrides):
        """Os campos que o modal envia — só os da variante, sem prefixo."""
        dados = {
            "sku": self.variant.sku,
            "sort_order": "0",
            "is_active": "on",
            "color": str(self.preto.pk),
            "size": self.variant.size,
            "material": str(self.resina.pk),
            "filament_cost": "4.00",
            "energy_cost": "0.00",
            "pricing_mode": PricingMode.PRICE,
            "sale_price": "20.00",
            "profit_margin": "",
            "stock_quantity": "10",
            "allow_backorder": "",
            "made_to_order": "",
            "production_lead_time_days": "2",
            "weight_grams": "300",
            "print_time": "",
            "width": "",
            "height": "",
            "depth": "",
            "dimension_unit": "mm",
            "variant_id": str(self.variant.pk),
        }
        dados.update(overrides)
        return {chave: valor for chave, valor in dados.items() if valor != ""}


class VariantTableTests(ModalBase):
    """1 e 3 — a tabela aparece; os campos não ficam abaixo dela."""

    def test_the_page_has_the_variant_table(self):
        resposta = self.client.get(self.change_url())

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "jd-variant-table")
        self.assertContains(resposta, "data-variant-rows")

    def test_the_table_is_compact(self):
        """Só o que identifica e compara — nada de todos os campos na grade."""
        html = self.client.get(self.change_url()).content.decode()
        # A tabela DA VARIANTE, nao a primeira `<thead>` da pagina -- a secao
        # MIDIA vem antes desde a etapa 12.
        tabela = html.split('class="jd-variant-table"')[1]
        cabecalho = tabela.split("<thead>")[1].split("</thead>")[0]

        for coluna in ("SKU", "Cor", "Tamanho", "Material", "Peso", "Estoque", "Preço"):
            with self.subTest(coluna=coluna):
                self.assertIn(coluna, cabecalho)

        for fora in ("Custo de filamento", "Definir preço por", "Profundidade"):
            with self.subTest(fora=fora):
                self.assertNotIn(fora, cabecalho)

    def test_the_fields_live_inside_a_modal(self):
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn("data-variant-modal", html)
        self.assertIn('class="jd-modal"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)

    def test_no_panel_is_rendered_below_the_table(self):
        """A classe do painel antigo não pode voltar a existir."""
        html = self.client.get(self.change_url()).content.decode()

        self.assertNotIn("jd-variant-panel", html)

    def test_every_variant_field_is_in_the_modal(self):
        html = self.client.get(self.change_url()).content.decode()
        modais = html.split('class="jd-variant-modals"')[1]

        for campo in (
            "sku", "color", "size", "material", "filament_cost", "energy_cost",
            "pricing_mode", "sale_price", "profit_margin", "stock_quantity",
            "allow_backorder", "made_to_order", "production_lead_time_days",
            "weight_grams", "print_time", "width", "height", "depth",
            "dimension_unit",
        ):
            with self.subTest(campo=campo):
                self.assertIn(f'-{campo}"', modais)

    def test_the_modal_has_a_title_and_the_three_buttons(self):
        resposta = self.client.get(self.change_url())

        self.assertContains(resposta, "data-variant-modal-title")
        self.assertContains(resposta, "data-variant-cancel")
        self.assertContains(resposta, "data-variant-save")
        self.assertContains(resposta, "Cancelar")
        self.assertContains(resposta, "Salvar")

    def test_the_add_button_is_there(self):
        self.assertContains(self.client.get(self.change_url()), "Adicionar variante")

    def test_the_page_carries_the_save_url(self):
        """É a presença dela que faz o botão "Salvar" gravar de verdade."""
        self.assertContains(self.client.get(self.change_url()), "data-variant-save-url")


class VariantSaveEndpointTests(ModalBase):
    """4 — editar pelo modal funciona."""

    def test_an_existing_variant_is_updated(self):
        resposta = self.client.post(
            self.save_url(), self.payload(sale_price="27.90", stock_quantity="7")
        )

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertTrue(dados["ok"])
        self.assertFalse(dados["created"])

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.sale_price, Decimal("27.90"))
        self.assertEqual(self.variant.stock_quantity, 7)

    def test_the_response_carries_what_the_server_saved(self):
        """O modal reescreve os campos com o valor gravado, não o digitado."""
        resposta = self.client.post(self.save_url(), self.payload(sale_price="27.90"))
        campos = resposta.json()["fields"]

        self.assertEqual(campos["sale_price"], "27.90")
        self.assertEqual(campos["sku"], "DINO-PRETO-25")
        self.assertEqual(campos["id"] if "id" in campos else resposta.json()["id"], self.variant.pk)

    def test_the_server_recalculates_the_margin(self):
        """Custo 4,00 e preço 20,00 dão 80% — e é o servidor que decide."""
        resposta = self.client.post(self.save_url(), self.payload(profit_margin="1"))

        self.assertEqual(resposta.json()["fields"]["profit_margin"], "80.00")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.profit_margin, Decimal("80.00"))

    def test_a_new_variant_is_created(self):
        resposta = self.client.post(
            self.save_url(),
            self.payload(
                variant_id="",
                sku="DINO-BRANCO-30",
                color=str(self.branco.pk),
                size="30 cm",
                sale_price="32.90",
            ),
        )

        dados = resposta.json()
        self.assertTrue(dados["ok"])
        self.assertTrue(dados["created"])
        nova = ProductVariant.objects.get(sku="DINO-BRANCO-30")
        self.assertEqual(nova.product, self.product)
        self.assertEqual(dados["id"], nova.pk)

    def test_the_new_variant_belongs_to_the_product_of_the_url(self):
        """Nem o POST decide o produto: ele vem da URL."""
        outro = make_product(sku="OUTRO", name="Outro", with_variant=False)

        self.client.post(
            self.save_url(),
            self.payload(variant_id="", sku="NOVA-1", size="10 cm", product=str(outro.pk)),
        )

        self.assertEqual(ProductVariant.objects.get(sku="NOVA-1").product, self.product)

    def test_a_variant_of_another_product_is_refused(self):
        outro = make_product(sku="OUTRO", name="Outro", price=Decimal("10.00"))

        resposta = self.client.post(
            self.save_url(), self.payload(variant_id=str(outro.default_variant.pk))
        )

        self.assertEqual(resposta.status_code, 404)

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(self.save_url()).status_code, 405)

    def test_an_anonymous_visitor_cannot_save(self):
        self.client.logout()

        resposta = self.client.post(self.save_url(), self.payload())

        self.assertIn(resposta.status_code, (302, 403))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.sale_price, Decimal("20.00"))

    def test_a_staff_user_without_permission_cannot_save(self):
        usuario = get_user_model().objects.create_user(
            username="atendente", email="a@jd.test", password="senha-de-teste"
        )
        usuario.is_staff = True
        usuario.save()
        self.client.force_login(usuario)

        resposta = self.client.post(self.save_url(), self.payload(sale_price="1.00"))

        self.assertIn(resposta.status_code, (302, 403))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.sale_price, Decimal("20.00"))


class ValidationKeepsTheModalOpenTests(ModalBase):
    """6 — erro de validação volta por campo, com o modal aberto."""

    def test_an_invalid_variant_is_refused(self):
        resposta = self.client.post(self.save_url(), self.payload(sale_price="-5.00"))

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(resposta.json()["ok"])

    def test_the_error_comes_back_on_the_field(self):
        resposta = self.client.post(self.save_url(), self.payload(sale_price="-5.00"))

        self.assertIn("sale_price", resposta.json()["errors"])

    def test_nothing_is_saved_when_it_is_invalid(self):
        self.client.post(self.save_url(), self.payload(sale_price="-5.00", size="99 cm"))

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.sale_price, Decimal("20.00"))
        self.assertEqual(self.variant.size, "25 cm")

    def test_a_duplicate_sku_is_refused_by_field(self):
        ProductVariant.objects.create(
            product=self.product, sku="DINO-OUTRA", size="30 cm",
            sale_price=Decimal("10.00"), stock_quantity=1,
        )

        resposta = self.client.post(self.save_url(), self.payload(sku="DINO-OUTRA"))

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("sku", resposta.json()["errors"])

    def test_an_active_variant_without_a_price_is_refused(self):
        resposta = self.client.post(
            self.save_url(), self.payload(sale_price="", profit_margin="")
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("sale_price", resposta.json()["errors"])

    def test_made_to_order_without_a_lead_time_is_refused(self):
        resposta = self.client.post(
            self.save_url(),
            self.payload(made_to_order="on", production_lead_time_days=""),
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("production_lead_time_days", resposta.json()["errors"])

    def test_a_duplicate_combination_is_refused(self):
        ProductVariant.objects.create(
            product=self.product, sku="DINO-IGUAL", color=self.branco,
            material=self.resina, size="30 cm",
            sale_price=Decimal("10.00"), stock_quantity=1,
        )

        resposta = self.client.post(
            self.save_url(),
            self.payload(color=str(self.branco.pk), size="30 cm"),
        )

        self.assertEqual(resposta.status_code, 400)


class VariantDeleteEndpointTests(ModalBase):
    """9 — excluir continua funcionando, agora com confirmação."""

    def setUp(self):
        super().setUp()
        self.segunda = ProductVariant.objects.create(
            product=self.product, sku="DINO-BRANCO-30", color=self.branco,
            material=self.resina, size="30 cm",
            sale_price=Decimal("32.90"), stock_quantity=5,
        )

    def test_a_variant_is_deleted(self):
        resposta = self.client.post(self.delete_url(self.segunda))

        self.assertTrue(resposta.json()["ok"])
        self.assertFalse(ProductVariant.objects.filter(pk=self.segunda.pk).exists())

    def test_the_other_variants_survive(self):
        self.client.post(self.delete_url(self.segunda))

        self.assertTrue(ProductVariant.objects.filter(pk=self.variant.pk).exists())

    def test_the_last_active_variant_of_an_active_product_is_protected(self):
        """A regra da etapa 8: produto ativo sem variante não vende nada."""
        self.segunda.delete()

        resposta = self.client.post(self.delete_url(self.variant))

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("última variante ativa", resposta.json()["detail"])
        self.assertTrue(ProductVariant.objects.filter(pk=self.variant.pk).exists())

    def test_a_draft_product_can_lose_its_last_variant(self):
        self.segunda.delete()
        self.product.status = ProductStatus.DRAFT
        self.product.save()

        resposta = self.client.post(self.delete_url(self.variant))

        self.assertTrue(resposta.json()["ok"])
        self.assertFalse(ProductVariant.objects.filter(pk=self.variant.pk).exists())

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(self.delete_url(self.segunda)).status_code, 405)

    def test_an_anonymous_visitor_cannot_delete(self):
        self.client.logout()

        resposta = self.client.post(self.delete_url(self.segunda))

        self.assertIn(resposta.status_code, (302, 403))
        self.assertTrue(ProductVariant.objects.filter(pk=self.segunda.pk).exists())

    def test_a_variant_of_another_product_is_refused(self):
        outro = make_product(sku="OUTRO", name="Outro", price=Decimal("10.00"))
        url = reverse(
            "admin:catalog_product_variant_delete",
            args=[self.product.pk, outro.default_variant.pk],
        )

        self.assertEqual(self.client.post(url).status_code, 404)

    def test_the_formset_checkbox_still_exists(self):
        """Sem JavaScript, a caixa do formset continua sendo o jeito de excluir."""
        self.assertContains(self.client.get(self.change_url()), "-DELETE")


class AddPageTests(ModalBase):
    """7 e 8 — adicionar abre o mesmo modal; no cadastro, sem endpoint."""

    def add_url(self):
        return reverse("admin:catalog_product_add")

    def test_the_add_page_has_the_modal_template(self):
        """O molde `__prefix__` é o que "Adicionar variante" clona."""
        resposta = self.client.get(self.add_url())

        self.assertContains(resposta, "data-variant-template")
        self.assertContains(resposta, "__prefix__")

    def test_the_add_page_has_no_save_url(self):
        """O produto ainda não tem PK: não há a que prender uma variante."""
        resposta = self.client.get(self.add_url())

        self.assertNotContains(resposta, "data-variant-save-url")

    def test_the_add_page_says_when_the_variant_is_saved(self):
        self.assertContains(
            self.client.get(self.add_url()), "gravadas junto com ele"
        )

    def test_the_change_page_has_no_such_warning(self):
        self.assertNotContains(
            self.client.get(self.change_url()), "gravadas junto com ele"
        )

    def test_creating_a_product_with_variants_still_works(self):
        """O caminho do formset não podia ser quebrado (etapa 8)."""
        payload = {
            "sku": "NOVO-01",
            "slug": "",
            "status": ProductStatus.DRAFT,
            "category": str(self.category.pk),
            "brand": "",
            "currency": "EUR",
            "is_featured": "",
            "featured_order": "0",
            "personalization_type": "none",
            "personalization_text_limit": "200",
            "translations-TOTAL_FORMS": "1",
            "translations-INITIAL_FORMS": "0",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "translations-0-language": "pt",
            "translations-0-name": "Produto novo",
            "translations-0-short_description": "",
            "translations-0-description": "",
            "translations-0-extra_information": "",
            "translations-0-id": "",
            "translations-0-master": "",
            "media-TOTAL_FORMS": "0",
            "media-INITIAL_FORMS": "0",
            "media-MIN_NUM_FORMS": "0",
            "media-MAX_NUM_FORMS": "1000",
            "product_colors-TOTAL_FORMS": "0",
            "product_colors-INITIAL_FORMS": "0",
            "product_colors-MIN_NUM_FORMS": "0",
            "product_colors-MAX_NUM_FORMS": "1000",
            "material_composition-TOTAL_FORMS": "0",
            "material_composition-INITIAL_FORMS": "0",
            "material_composition-MIN_NUM_FORMS": "0",
            "material_composition-MAX_NUM_FORMS": "1000",
            "variants-TOTAL_FORMS": "1",
            "variants-INITIAL_FORMS": "0",
            "variants-MIN_NUM_FORMS": "0",
            "variants-MAX_NUM_FORMS": "1000",
            "variants-0-id": "",
            "variants-0-product": "",
            "variants-0-sku": "NOVO-01-A",
            "variants-0-sort_order": "0",
            "variants-0-is_active": "on",
            "variants-0-color": "",
            "variants-0-size": "20 cm",
            "variants-0-material": "",
            "variants-0-pricing_mode": PricingMode.PRICE,
            "variants-0-sale_price": "15.00",
            "variants-0-profit_margin": "",
            "variants-0-filament_cost": "2.00",
            "variants-0-energy_cost": "0.00",
            "variants-0-stock_quantity": "3",
            "variants-0-allow_backorder": "",
            "variants-0-made_to_order": "",
            "variants-0-production_lead_time_days": "",
            "variants-0-weight_grams": "100",
            "variants-0-print_time": "",
            "variants-0-width": "",
            "variants-0-height": "",
            "variants-0-depth": "",
            "variants-0-dimension_unit": "mm",
        }
        self.client.post(self.add_url(), payload, follow=True)

        produto = Product.objects.get(sku="NOVO-01")
        self.assertEqual(produto.variants.count(), 1)
        self.assertEqual(produto.variants.get().sale_price, Decimal("15.00"))


class PricingIsUnchangedTests(ModalBase):
    """10 — custo → margem → preço continua igual, e o servidor decide."""

    def test_cost_and_margin_give_the_price(self):
        resposta = self.client.post(
            self.save_url(),
            self.payload(
                pricing_mode=PricingMode.MARGIN,
                profit_margin="50",
                sale_price="",
                filament_cost="5.00",
                energy_cost="0.00",
            ),
        )

        self.assertEqual(resposta.json()["fields"]["sale_price"], "10.00")

    def test_margin_is_not_markup(self):
        resposta = self.client.post(
            self.save_url(),
            self.payload(
                pricing_mode=PricingMode.MARGIN,
                profit_margin="50",
                sale_price="",
                filament_cost="5.00",
                energy_cost="0.00",
            ),
        )

        self.assertNotEqual(resposta.json()["fields"]["sale_price"], "7.50")

    def test_cost_and_price_give_the_margin(self):
        resposta = self.client.post(
            self.save_url(),
            self.payload(
                pricing_mode=PricingMode.PRICE,
                sale_price="10.00",
                filament_cost="5.00",
                energy_cost="0.00",
            ),
        )

        self.assertEqual(resposta.json()["fields"]["profit_margin"], "50.00")

    def test_the_browser_number_is_not_trusted(self):
        """O preço enviado no modo margem é descartado: quem manda é o custo."""
        resposta = self.client.post(
            self.save_url(),
            self.payload(
                pricing_mode=PricingMode.MARGIN,
                profit_margin="50",
                sale_price="999.00",
                filament_cost="5.00",
                energy_cost="0.00",
            ),
        )

        self.assertEqual(resposta.json()["fields"]["sale_price"], "10.00")

    def test_a_margin_of_one_hundred_percent_is_refused(self):
        resposta = self.client.post(
            self.save_url(),
            self.payload(pricing_mode=PricingMode.MARGIN, profit_margin="100", sale_price=""),
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("profit_margin", resposta.json()["errors"])

    def test_the_javascript_still_carries_the_calculation(self):
        from pathlib import Path

        from django.conf import settings

        fonte = (
            Path(settings.BASE_DIR) / "static" / "admin" / "js" / "variant_admin.js"
        ).read_text(encoding="utf-8")

        self.assertIn("priceFromMargin", fonte)
        self.assertIn("marginFromPrice", fonte)
        self.assertIn("pricing_mode", fonte)


class TranslationsStillWorkTests(ModalBase):
    """11 — cor e material traduzidos continuam funcionando."""

    def test_the_admin_shows_the_internal_names_in_the_selects(self):
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn("Preto", html)
        self.assertIn("Resina", html)

    def test_the_storefront_still_translates(self):
        resposta = self.client.get("/fr" + self.product.get_absolute_url())

        self.assertContains(resposta, "Noir")
        self.assertContains(resposta, "Résine")

    def test_saving_through_the_modal_keeps_the_translations(self):
        self.client.post(self.save_url(), self.payload(sale_price="21.00"))

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.color, self.preto)
        self.assertEqual(self.variant.material, self.resina)
        self.assertEqual(self.variant.color.tr("name", language="fr"), "Noir")


class TheStoreIsUntouchedTests(ModalBase):
    """12, 13 e 14 — vitrine, carrinho e frete não podiam ser afetados."""

    def setUp(self):
        super().setUp()
        self.segunda = ProductVariant.objects.create(
            product=self.product, sku="DINO-BRANCO-30", color=self.branco,
            material=self.resina, size="30 cm",
            sale_price=Decimal("32.90"), weight_grams=Decimal("500"),
            production_lead_time_days=3, stock_quantity=5,
        )
        self.product.refresh_from_db()

    def test_the_product_page_still_offers_the_matrix(self):
        resposta = self.client.get(self.product.get_absolute_url())
        chaves = [grupo["key"] for grupo in resposta.context["variant_options"]]

        self.assertIn("color", chaves)
        self.assertIn("size", chaves)

    def test_an_impossible_combination_is_still_refused(self):
        from apps.cart.forms import AddToCartForm

        form = AddToCartForm(
            {
                "option_color": str(self.branco.pk),
                "option_size": "25 cm",  # Branco só existe em 30 cm
                "quantity": "1",
            },
            product=self.product,
        )

        self.assertFalse(form.is_valid())

    def test_the_cart_keeps_the_chosen_variant(self):
        """Lido pela página do carrinho: o usuário está logado e, para quem tem
        conta, o carrinho mora no banco — não na sessão."""
        self.client.post(
            reverse("cart:add"),
            {
                "product_id": self.product.pk,
                "variant_id": self.segunda.pk,
                "quantity": "1",
            },
        )

        linhas = self.client.get(reverse("cart:detail")).context["cart"].lines()
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0].variant, self.segunda)
        self.assertEqual(linhas[0].unit_price, Decimal("32.90"))

    def test_the_freight_uses_the_variant_weight(self):
        from apps.cart.cart import CartLine
        from apps.shipping import services as frete

        linhas = [
            CartLine(key="a", product=self.product, variant=self.variant, quantity=2),
            CartLine(key="b", product=self.product, variant=self.segunda, quantity=1),
        ]

        self.assertEqual(frete.cart_weight_grams(linhas), 1100)  # 2×300 + 500
        self.assertEqual(frete.production_days(linhas), 3)

    def test_saving_through_the_modal_changes_the_freight(self):
        """Editar o peso pelo modal precisa chegar ao cálculo do frete."""
        from apps.cart.cart import CartLine
        from apps.shipping import services as frete

        self.client.post(self.save_url(), self.payload(weight_grams="450"))
        self.variant.refresh_from_db()

        linha = CartLine(key="a", product=self.product, variant=self.variant, quantity=1)
        self.assertEqual(frete.cart_weight_grams([linha]), 450)


class ModalAccessibilityTests(ModalBase):
    """O modal precisa ser navegável por teclado e por leitor de tela."""

    def test_the_dialog_is_announced_as_a_modal(self):
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)

    def test_the_dialog_has_a_label(self):
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn("aria-labelledby=", html)
        self.assertIn("data-variant-modal-title", html)

    def test_the_close_button_has_a_name(self):
        self.assertContains(self.client.get(self.change_url()), "Fechar sem salvar")

    def test_the_backdrop_is_hidden_from_screen_readers(self):
        """Pela classe, e não pelo `data-variant-backdrop` que já não existe.

        O gancho saiu quando o fundo escuro deixou de fechar o modal: quem
        cuida dele agora é `jd_modal.js`, pela classe. O que este teste guarda
        continua sendo o mesmo — o fundo é decoração e o leitor de tela pula.
        """
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn('class="jd-modal-backdrop" aria-hidden="true"', html)

    def test_the_javascript_handles_escape_and_focus(self):
        """ESC, foco e trava de rolagem vivem na casca compartilhada.

        Desde a etapa 12 os dois modais do Admin — VARIANTES e CONTEÚDO —
        usam `jd_modal.js`. Ter duas implementações de ESC na mesma página
        daria duas respostas para a mesma tecla.
        """
        from pathlib import Path

        from django.conf import settings

        casca = (
            Path(settings.BASE_DIR) / "static" / "admin" / "js" / "jd_modal.js"
        ).read_text(encoding="utf-8")
        variante = (
            Path(settings.BASE_DIR) / "static" / "admin" / "js" / "variant_admin.js"
        ).read_text(encoding="utf-8")

        self.assertIn('evento.key === "Escape"', casca)
        self.assertIn("opener.focus()", casca)
        self.assertIn("jd-modal-lock", casca)
        # E o modal de variantes usa a casca, em vez de repeti-la.
        self.assertIn("new window.JDModal(", variante)
        self.assertNotIn('evento.key === "Escape"', variante)


class ModalAndProductSaveTogetherTests(ModalBase):
    """A interação que o desenho do modal tinha de resolver.

    O modal grava a variante por fora do formset, mas o formulário do produto
    continua na página com aqueles mesmos campos. Salvar o produto em seguida
    reenvia tudo — e isso não pode duplicar variante, apagar o que o modal
    gravou nem estourar.

    É por causa deste risco que criar e excluir pelo modal recarregam a página:
    uma variante criada por fora entraria no formset como formulário "novo" e o
    próximo Salvar tentaria inseri-la de novo.
    """

    def product_payload(self, **variant_overrides):
        """O POST do produto, com o inline refletindo o estado atual."""
        variantes = list(self.product.variants.order_by("sort_order", "id"))
        dados = {
            "sku": self.product.sku,
            "slug": self.product.slug,
            "status": self.product.status,
            "category": str(self.category.pk),
            "brand": "",
            "currency": "EUR",
            "is_featured": "",
            "featured_order": "0",
            "personalization_type": "none",
            "personalization_text_limit": "200",
            "translations-TOTAL_FORMS": "1",
            "translations-INITIAL_FORMS": "1",
            "translations-MIN_NUM_FORMS": "0",
            "translations-MAX_NUM_FORMS": "1000",
            "media-TOTAL_FORMS": "0",
            "media-INITIAL_FORMS": "0",
            "media-MIN_NUM_FORMS": "0",
            "media-MAX_NUM_FORMS": "1000",
            "product_colors-TOTAL_FORMS": "0",
            "product_colors-INITIAL_FORMS": "0",
            "product_colors-MIN_NUM_FORMS": "0",
            "product_colors-MAX_NUM_FORMS": "1000",
            "material_composition-TOTAL_FORMS": "0",
            "material_composition-INITIAL_FORMS": "0",
            "material_composition-MIN_NUM_FORMS": "0",
            "material_composition-MAX_NUM_FORMS": "1000",
            "variants-TOTAL_FORMS": str(len(variantes)),
            "variants-INITIAL_FORMS": str(len(variantes)),
            "variants-MIN_NUM_FORMS": "0",
            "variants-MAX_NUM_FORMS": "1000",
        }

        traducao = self.product.translations.get(language="pt")
        dados.update({
            "translations-0-id": str(traducao.pk),
            "translations-0-master": str(self.product.pk),
            "translations-0-language": "pt",
            "translations-0-name": traducao.name,
            "translations-0-short_description": "",
            "translations-0-description": "",
            "translations-0-extra_information": "",
        })

        for indice, variante in enumerate(variantes):
            dados.update({
                f"variants-{indice}-id": str(variante.pk),
                f"variants-{indice}-product": str(self.product.pk),
                f"variants-{indice}-sku": variante.sku,
                f"variants-{indice}-sort_order": str(variante.sort_order),
                f"variants-{indice}-is_active": "on" if variante.is_active else "",
                f"variants-{indice}-color": str(variante.color_id or ""),
                f"variants-{indice}-size": variante.size,
                f"variants-{indice}-material": str(variante.material_id or ""),
                f"variants-{indice}-pricing_mode": variante.pricing_mode,
                f"variants-{indice}-sale_price": str(variante.sale_price or ""),
                f"variants-{indice}-profit_margin": str(variante.profit_margin or ""),
                f"variants-{indice}-filament_cost": str(variante.filament_cost),
                f"variants-{indice}-energy_cost": str(variante.energy_cost),
                f"variants-{indice}-stock_quantity": str(variante.stock_quantity),
                f"variants-{indice}-allow_backorder": "on" if variante.allow_backorder else "",
                f"variants-{indice}-made_to_order": "on" if variante.made_to_order else "",
                f"variants-{indice}-production_lead_time_days": str(
                    variante.production_lead_time_days or ""
                ),
                f"variants-{indice}-weight_grams": str(variante.weight_grams or ""),
                f"variants-{indice}-print_time": "",
                f"variants-{indice}-width": str(variante.width or ""),
                f"variants-{indice}-height": str(variante.height or ""),
                f"variants-{indice}-depth": str(variante.depth or ""),
                f"variants-{indice}-dimension_unit": variante.dimension_unit,
            })

        dados.update(variant_overrides)
        return dados

    def test_saving_the_product_after_the_modal_keeps_what_the_modal_saved(self):
        self.client.post(self.save_url(), self.payload(sale_price="27.90", stock_quantity="7"))

        self.product.refresh_from_db()
        resposta = self.client.post(self.change_url(), self.product_payload(), follow=True)

        self.assertEqual(resposta.status_code, 200)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.sale_price, Decimal("27.90"))
        self.assertEqual(self.variant.stock_quantity, 7)

    def test_saving_the_product_does_not_duplicate_the_variant(self):
        self.client.post(self.save_url(), self.payload(sale_price="27.90"))

        self.product.refresh_from_db()
        self.client.post(self.change_url(), self.product_payload(), follow=True)

        self.assertEqual(ProductVariant.objects.filter(product=self.product).count(), 1)

    def test_a_variant_created_by_the_modal_survives_the_product_save(self):
        """O caso que motivou o recarregamento: criar por fora e salvar depois."""
        self.client.post(
            self.save_url(),
            self.payload(variant_id="", sku="DINO-NOVA", size="30 cm", sale_price="30.00"),
        )
        self.assertEqual(ProductVariant.objects.filter(product=self.product).count(), 2)

        self.product.refresh_from_db()
        self.client.post(self.change_url(), self.product_payload(), follow=True)

        # Continuam duas: nenhuma inserida de novo, nenhuma perdida.
        self.assertEqual(ProductVariant.objects.filter(product=self.product).count(), 2)
        self.assertTrue(ProductVariant.objects.filter(sku="DINO-NOVA").exists())

    def test_the_formset_still_refuses_an_active_product_without_variants(self):
        """A regra da etapa 8 continua cobrada no Salvar do produto."""
        payload = self.product_payload()
        payload["variants-TOTAL_FORMS"] = "0"
        payload["variants-INITIAL_FORMS"] = "0"

        resposta = self.client.post(self.change_url(), payload)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "pelo menos uma variante ativa")

    def test_the_delete_checkbox_of_the_formset_still_deletes(self):
        """Sem JavaScript, é por aqui que se exclui — e continua funcionando."""
        segunda = ProductVariant.objects.create(
            product=self.product, sku="DINO-SEGUNDA", size="30 cm",
            sale_price=Decimal("10.00"), stock_quantity=1,
        )
        self.product.refresh_from_db()

        payload = self.product_payload()
        indice = [v.pk for v in self.product.variants.order_by("sort_order", "id")].index(
            segunda.pk
        )
        payload[f"variants-{indice}-DELETE"] = "on"

        self.client.post(self.change_url(), payload, follow=True)

        self.assertFalse(ProductVariant.objects.filter(pk=segunda.pk).exists())
        self.assertTrue(ProductVariant.objects.filter(pk=self.variant.pk).exists())
