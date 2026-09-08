"""Testes do fluxo de cadastro pelo Django Admin.

Cobrem o que só acontece no admin: geração do slug a partir do nome em
português (gravado depois do produto, via inline), exigência da tradução
padrão ao ativar um produto e — desde a etapa 8 — a regra de que um produto
ativo precisa de pelo menos uma variante, porque é a variante que tem preço,
estoque, peso e prazo.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import PricingMode, Product, ProductStatus, ProductVariant
from apps.categories.models import Category, CategoryTranslation


def inline_payload(prefix: str, rows: list[dict]) -> dict:
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"{prefix}-{index}-{field}"] = value
    return data


#: Uma linha do inline de variantes. É aqui que mora o comercial.
def variant_row(**overrides):
    row = {
        "id": "",
        "product": "",
        "sku": "GATO-01-V1",
        "sort_order": "0",
        "is_active": "on",
        "color": "",
        "size": "",
        "material": "",
        "pricing_mode": PricingMode.PRICE,
        "sale_price": "10.00",
        "profit_margin": "",
        "filament_cost": "3.00",
        "energy_cost": "2.00",
        "stock_quantity": "4",
        "allow_backorder": "",
        "made_to_order": "",
        "production_lead_time_days": "",
        "weight_grams": "35",
        "print_time": "02:35:00",
        "width": "50",
        "height": "20",
        "depth": "5",
        "dimension_unit": "mm",
    }
    row.update(overrides)
    return row


class ProductAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@jdprint.test", password="senha-de-teste"
        )
        cls.category = Category.objects.create(slug="animais")
        CategoryTranslation.objects.create(master=cls.category, language="pt", name="Animais")

    def setUp(self):
        self.client.force_login(self.user)

    def base_payload(self, variants=None, **overrides):
        """O produto genérico + as variantes que vão junto no mesmo POST."""
        payload = {
            "sku": "GATO-01",
            "slug": "",
            "status": ProductStatus.DRAFT,
            "category": str(self.category.pk),
            "brand": "",
            "currency": "EUR",
            "is_featured": "",
            "featured_order": "0",
            "personalization_type": "none",
            "personalization_text_limit": "200",
        }
        payload.update(
            inline_payload(
                "translations",
                [
                    {
                        "language": "pt",
                        "name": "Gato Pompom",
                        "short_description": "Marcador de página.",
                        "description": "",
                        "extra_information": "",
                        "id": "",
                        "master": "",
                    }
                ],
            )
        )
        payload.update(inline_payload("media", []))
        payload.update(inline_payload("product_colors", []))
        payload.update(inline_payload("material_composition", []))
        payload.update(
            inline_payload("variants", [variant_row()] if variants is None else variants)
        )
        payload.update(overrides)
        return payload

    def variant_field(self, index, field, value):
        """Sobrescreve um campo da variante ``index`` do POST."""
        return {f"variants-{index}-{field}": value}

    # -- produto genérico ---------------------------------------------------

    def test_add_page_loads(self):
        response = self.client.get(reverse("admin:catalog_product_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INFORMAÇÕES BÁSICAS")
        self.assertContains(response, "VARIANTES")

    def test_add_page_no_longer_offers_a_price_on_the_product(self):
        """Preço não é do produto: o formulário do produto não pode oferecê-lo."""
        response = self.client.get(reverse("admin:catalog_product_add"))
        self.assertNotContains(response, 'name="sale_price"')
        self.assertNotContains(response, 'name="stock_quantity"')

    def test_creates_product_with_translation(self):
        response = self.client.post(
            reverse("admin:catalog_product_add"), self.base_payload(), follow=True
        )
        self.assertEqual(response.status_code, 200)

        product = Product.objects.get(sku="GATO-01")
        self.assertEqual(product.name_in("pt"), "Gato Pompom")
        self.assertEqual(product.created_by, self.user)

    def test_slug_is_generated_from_the_portuguese_name(self):
        self.client.post(reverse("admin:catalog_product_add"), self.base_payload(), follow=True)
        product = Product.objects.get(sku="GATO-01")
        self.assertEqual(product.slug, "gato-pompom")

    def test_active_product_without_portuguese_translation_is_rejected(self):
        payload = self.base_payload(status=ProductStatus.ACTIVE)
        payload["translations-0-language"] = "fr"
        payload["translations-0-name"] = "Chat Pompom"

        response = self.client.post(reverse("admin:catalog_product_add"), payload)

        self.assertEqual(response.status_code, 200)  # formulário devolvido com erro
        self.assertFalse(Product.objects.filter(sku="GATO-01").exists())

    # -- a variante criada junto --------------------------------------------

    def test_the_inline_variant_carries_the_commercial_data(self):
        self.client.post(reverse("admin:catalog_product_add"), self.base_payload(), follow=True)

        variant = ProductVariant.objects.get(sku="GATO-01-V1")
        self.assertEqual(variant.sale_price, Decimal("10.00"))
        self.assertEqual(variant.total_cost, Decimal("5.00"))
        self.assertEqual(variant.profit_margin, Decimal("50.00"))
        self.assertEqual(variant.stock_quantity, 4)
        self.assertEqual(variant.weight_grams, Decimal("35.00"))

    def test_variant_price_is_calculated_from_margin(self):
        payload = self.base_payload()
        payload.update(self.variant_field(0, "pricing_mode", PricingMode.MARGIN))
        payload.update(self.variant_field(0, "sale_price", ""))
        payload.update(self.variant_field(0, "profit_margin", "50"))

        self.client.post(reverse("admin:catalog_product_add"), payload, follow=True)

        self.assertEqual(
            ProductVariant.objects.get(sku="GATO-01-V1").sale_price, Decimal("10.00")
        )

    def test_made_to_order_without_lead_time_is_rejected(self):
        payload = self.base_payload()
        payload.update(self.variant_field(0, "made_to_order", "on"))
        payload.update(self.variant_field(0, "production_lead_time_days", ""))

        response = self.client.post(reverse("admin:catalog_product_add"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(sku="GATO-01").exists())

    def test_active_product_without_a_variant_is_rejected(self):
        """A regra da etapa 8, cobrada onde o administrador a encontra."""
        payload = self.base_payload(variants=[], status=ProductStatus.ACTIVE)

        response = self.client.post(reverse("admin:catalog_product_add"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pelo menos uma variante ativa")
        self.assertFalse(Product.objects.filter(sku="GATO-01").exists())

    def test_draft_product_without_a_variant_is_allowed(self):
        """Cadastro pela metade é legítimo enquanto o produto é rascunho."""
        payload = self.base_payload(variants=[])

        self.client.post(reverse("admin:catalog_product_add"), payload, follow=True)

        product = Product.objects.get(sku="GATO-01")
        self.assertFalse(product.has_variants)

    def test_two_variants_are_created_at_once(self):
        payload = self.base_payload(
            variants=[
                variant_row(sku="GATO-01-P", size="10 cm", weight_grams="150"),
                variant_row(sku="GATO-01-G", size="25 cm", weight_grams="400", sale_price="18.00"),
            ]
        )

        self.client.post(reverse("admin:catalog_product_add"), payload, follow=True)

        product = Product.objects.prefetch_related("variants").get(sku="GATO-01")
        self.assertEqual(len(product.active_variants()), 2)
        self.assertEqual(product.price_range, (Decimal("10.00"), Decimal("18.00")))

    # -- listagens ----------------------------------------------------------

    def test_changelist_loads(self):
        self.client.post(reverse("admin:catalog_product_add"), self.base_payload(), follow=True)
        response = self.client.get(reverse("admin:catalog_product_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GATO-01")

    def test_changelist_warns_about_a_product_without_variants(self):
        self.client.post(
            reverse("admin:catalog_product_add"), self.base_payload(variants=[]), follow=True
        )
        response = self.client.get(reverse("admin:catalog_product_changelist"))

        self.assertContains(response, "Sem configuração")
        self.assertContains(response, "nenhuma variante cadastrada")

    def test_variant_changelist_loads(self):
        self.client.post(reverse("admin:catalog_product_add"), self.base_payload(), follow=True)
        response = self.client.get(reverse("admin:catalog_productvariant_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GATO-01-V1")

    def test_category_admin_loads(self):
        response = self.client.get(reverse("admin:categories_category_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Animais")
