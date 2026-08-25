"""Testes do fluxo de cadastro pelo Django Admin.

Cobrem o que só acontece no admin: geração do slug a partir do nome em
português (gravado depois do produto, via inline) e exigência da tradução
padrão ao ativar um produto.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import PricingMode, Product, ProductStatus
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

    def base_payload(self, **overrides):
        payload = {
            "sku": "GATO-01",
            "slug": "",
            "status": ProductStatus.DRAFT,
            "category": str(self.category.pk),
            "brand": "",
            "width": "50",
            "height": "20",
            "depth": "5",
            "dimension_unit": "mm",
            "weight_grams": "35",
            "materials": [],
            "colors": [],
            "print_time": "02:35:00",
            "currency": "EUR",
            "filament_cost": "3.00",
            "energy_cost": "2.00",
            "pricing_mode": PricingMode.PRICE,
            "sale_price": "10.00",
            "profit_margin": "",
            "stock_quantity": "4",
            "allow_backorder": "",
            "made_to_order": "",
            "production_lead_time_days": "",
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
        payload.update(inline_payload("variants", []))
        payload.update(overrides)
        return payload

    def test_add_page_loads(self):
        response = self.client.get(reverse("admin:catalog_product_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IDENTIFICAÇÃO")
        self.assertContains(response, "PREÇO")

    def test_creates_product_with_translation_and_calculated_price(self):
        response = self.client.post(
            reverse("admin:catalog_product_add"), self.base_payload(), follow=True
        )
        self.assertEqual(response.status_code, 200)

        product = Product.objects.get(sku="GATO-01")
        self.assertEqual(product.total_cost, Decimal("5.00"))
        self.assertEqual(product.profit_margin, Decimal("50.00"))
        self.assertEqual(product.name_in("pt"), "Gato Pompom")
        self.assertEqual(product.created_by, self.user)

    def test_slug_is_generated_from_the_portuguese_name(self):
        self.client.post(reverse("admin:catalog_product_add"), self.base_payload(), follow=True)
        product = Product.objects.get(sku="GATO-01")
        self.assertEqual(product.slug, "gato-pompom")

    def test_price_is_calculated_from_margin(self):
        payload = self.base_payload(
            pricing_mode=PricingMode.MARGIN, sale_price="", profit_margin="50"
        )
        self.client.post(reverse("admin:catalog_product_add"), payload, follow=True)

        product = Product.objects.get(sku="GATO-01")
        self.assertEqual(product.sale_price, Decimal("10.00"))

    def test_active_product_without_portuguese_translation_is_rejected(self):
        payload = self.base_payload(status=ProductStatus.ACTIVE)
        payload["translations-0-language"] = "fr"
        payload["translations-0-name"] = "Chat Pompom"

        response = self.client.post(reverse("admin:catalog_product_add"), payload)

        self.assertEqual(response.status_code, 200)  # formulário devolvido com erro
        self.assertFalse(Product.objects.filter(sku="GATO-01").exists())

    def test_made_to_order_without_lead_time_is_rejected(self):
        payload = self.base_payload(made_to_order="on", production_lead_time_days="")
        response = self.client.post(reverse("admin:catalog_product_add"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(sku="GATO-01").exists())

    def test_changelist_loads(self):
        self.client.post(reverse("admin:catalog_product_add"), self.base_payload(), follow=True)
        response = self.client.get(reverse("admin:catalog_product_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GATO-01")

    def test_category_admin_loads(self):
        response = self.client.get(reverse("admin:categories_category_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Animais")
