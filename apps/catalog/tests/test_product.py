"""Testes do modelo de produto — a **definição genérica**.

Desde a etapa 8 o Product não tem preço, estoque, peso, prazo nem dimensões:
tudo isso é da ``ProductVariant`` e está em ``test_variants.py``. O que sobra
aqui é a identidade do produto (SKU, slug, tradução, categoria, marca), as
regras de ativação e as propriedades que ele **deriva** das variantes.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.catalog.models import (
    Brand,
    Color,
    Material,
    Product,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.categories.models import Category, CategoryTranslation


def make_category(slug="modelos", name="Modelos", parent=None):
    category = Category.objects.create(slug=slug, parent=parent)
    CategoryTranslation.objects.create(master=category, language="pt", name=name)
    return category


def make_product(sku="GATO-01", name="Gato Pompom", **kwargs):
    """Produto puro, **sem** variante — para testar o que é do produto."""
    product = Product.objects.create(sku=sku, **kwargs)
    if name:
        ProductTranslation.objects.create(master=product, language="pt", name=name)
        product.refresh_translations()
    return product


def make_variant(product, sku="VAR-1", price=Decimal("10.00"), **kwargs):
    kwargs.setdefault("sale_price", price)
    return ProductVariant.objects.create(product=product, sku=sku, **kwargs)


class ProductCreationTests(TestCase):
    def test_created_as_draft_by_default(self):
        product = make_product()
        self.assertEqual(product.status, ProductStatus.DRAFT)

    def test_sku_is_normalized_to_uppercase(self):
        product = make_product(sku=" gato-01 ")
        self.assertEqual(product.sku, "GATO-01")

    def test_slug_is_generated_from_sku_when_blank(self):
        product = make_product(sku="GATO-01", name=None)
        self.assertEqual(product.slug, "gato-01")

    def test_explicit_slug_is_kept(self):
        product = make_product(slug="gato-pompom")
        self.assertEqual(product.slug, "gato-pompom")

    def test_defaults(self):
        product = make_product()
        self.assertFalse(product.is_featured)
        self.assertEqual(product.currency, "EUR")
        self.assertEqual(product.personalization_text_limit, 200)


class GenericProductHasNoCommercialFieldsTests(TestCase):
    """A trave da etapa 8.

    Se alguém devolver preço, estoque, peso ou prazo para o Product, estes
    testes caem — e é para caírem: seriam duas fontes da verdade para o mesmo
    número, e a de baixo (a variante) é a que o cliente paga e recebe.
    """

    #: Campos comerciais que **não** podem voltar a existir em Product.
    PROIBIDOS = (
        "sale_price",
        "stock_quantity",
        "weight_grams",
        "width",
        "height",
        "depth",
        "dimension_unit",
        "print_time",
        "allow_backorder",
        "filament_cost",
        "energy_cost",
        "total_cost",
        "pricing_mode",
        "profit_margin",
    )

    def field_names(self):
        return {field.name for field in Product._meta.get_fields()}

    def test_product_has_no_commercial_columns(self):
        presentes = sorted(self.field_names() & set(self.PROIBIDOS))
        self.assertEqual(presentes, [], f"campos comerciais em Product: {presentes}")

    def test_price_cannot_be_written_on_the_product(self):
        with self.assertRaises(TypeError):
            Product.objects.create(sku="X-PRICE", sale_price=Decimal("10.00"))

    def test_stock_cannot_be_written_on_the_product(self):
        with self.assertRaises(TypeError):
            Product.objects.create(sku="X-STOCK", stock_quantity=5)

    def test_weight_cannot_be_written_on_the_product(self):
        with self.assertRaises(TypeError):
            Product.objects.create(sku="X-WEIGHT", weight_grams=Decimal("100"))

    def test_lead_time_is_read_only_on_the_product(self):
        """Ele ainda se lê — derivado da variante — mas não se escreve."""
        product = make_product(sku="X-LEAD")
        with self.assertRaises(AttributeError):
            product.production_lead_time_days = 5

    def test_made_to_order_is_read_only_on_the_product(self):
        product = make_product(sku="X-MTO")
        with self.assertRaises(AttributeError):
            product.made_to_order = True

    def test_product_has_no_colors_or_materials_of_its_own(self):
        """Cor e material são eixos da variante, não etiquetas do produto."""
        nomes = self.field_names()
        self.assertNotIn("colors", nomes)
        self.assertNotIn("materials", nomes)


class UniquenessTests(TestCase):
    def test_duplicate_sku_is_rejected(self):
        make_product(sku="GATO-01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="GATO-01")

    def test_duplicate_sku_is_rejected_case_insensitively(self):
        make_product(sku="GATO-01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="gato-01")

    def test_duplicate_slug_is_rejected(self):
        make_product(sku="GATO-01", slug="gato-pompom")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="GATO-02", slug="gato-pompom")

    def test_generated_slug_never_collides(self):
        first = make_product(sku="GATO-01", name="Gato Pompom", slug="gato-pompom")
        second = Product.objects.create(sku="GATO-02", slug="")
        ProductTranslation.objects.create(master=second, language="pt", name="Gato Pompom")
        second.refresh_translations()
        second.slug = ""
        second.save()
        self.assertNotEqual(second.slug, first.slug)


class DerivedFromVariantsTests(TestCase):
    """Tudo o que o produto sabe de comercial, ele pergunta às variantes."""

    def setUp(self):
        self.product = make_product(sku="CANECA", name="Caneca")
        self.black = Color.objects.create(name="Preto", hex_code="#000000")
        self.white = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")

    def test_product_without_variants_is_not_sellable(self):
        self.product.status = ProductStatus.ACTIVE
        self.product.save()

        self.assertFalse(self.product.has_variants)
        self.assertFalse(self.product.is_sellable)
        self.assertIsNone(self.product.default_variant)

    def test_product_without_variants_has_no_price(self):
        self.assertEqual(self.product.price_range, (None, None))
        self.assertIsNone(self.product.display_price)
        self.assertFalse(self.product.has_price_range)

    def test_product_without_variants_has_no_stock(self):
        self.assertEqual(self.product.available_stock, 0)
        self.assertFalse(self.product.is_available)
        self.assertEqual(self.product.stock_state, "out")

    def test_one_variant_makes_it_sellable(self):
        self.product.status = ProductStatus.ACTIVE
        self.product.save()
        make_variant(self.product, sku="V-1", price=Decimal("15.00"), stock_quantity=3)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertTrue(product.is_sellable)
        self.assertFalse(product.has_multiple_variants)

    def test_display_price_is_the_default_variant_price(self):
        make_variant(self.product, sku="V-1", price=Decimal("15.00"), stock_quantity=3)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.display_price, Decimal("15.00"))

    def test_price_range_spans_the_variants(self):
        make_variant(self.product, sku="V-1", size="P", price=Decimal("15.00"), stock_quantity=1)
        make_variant(self.product, sku="V-2", size="G", price=Decimal("22.00"), stock_quantity=1)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.price_range, (Decimal("15.00"), Decimal("22.00")))
        self.assertTrue(product.has_price_range)
        self.assertTrue(product.has_multiple_variants)

    def test_stock_is_the_sum_of_the_variants(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=10)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=5)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.available_stock, 15)

    def test_available_when_any_variant_is(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=0)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=3)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertTrue(product.is_available)

    def test_unavailable_when_no_variant_is(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=0)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=0)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertFalse(product.is_available)
        self.assertEqual(product.stock_state, "out")

    def test_inactive_variant_does_not_count(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=5, is_active=False)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertFalse(product.has_variants)
        self.assertEqual(product.available_stock, 0)

    def test_default_variant_is_the_first_available_one(self):
        make_variant(self.product, sku="V-1", size="P", stock_quantity=0, sort_order=1)
        disponivel = make_variant(self.product, sku="V-2", size="G", stock_quantity=4, sort_order=2)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.default_variant, disponivel)

    def test_default_variant_falls_back_to_the_first_when_all_are_out(self):
        primeira = make_variant(self.product, sku="V-1", size="P", stock_quantity=0, sort_order=1)
        make_variant(self.product, sku="V-2", size="G", stock_quantity=0, sort_order=2)

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertEqual(product.default_variant, primeira)

    def test_lead_time_comes_from_the_displayed_variant(self):
        make_variant(
            self.product, sku="V-1", made_to_order=True, production_lead_time_days=7
        )

        product = Product.objects.prefetch_related("variants").get(pk=self.product.pk)
        self.assertTrue(product.made_to_order)
        self.assertEqual(product.production_lead_time_days, 7)
        self.assertEqual(product.stock_state, "made_to_order")

    def test_colors_are_derived_from_the_variants(self):
        make_variant(self.product, sku="V-1", color=self.black, stock_quantity=1)
        make_variant(self.product, sku="V-2", color=self.white, stock_quantity=1)
        make_variant(self.product, sku="V-3", color=self.black, size="G", stock_quantity=1)

        product = Product.objects.prefetch_related("variants__color").get(pk=self.product.pk)
        self.assertEqual(product.available_colors, [self.black, self.white])

    def test_materials_are_derived_from_the_variants(self):
        make_variant(self.product, sku="V-1", material=self.pla, stock_quantity=1)

        product = Product.objects.prefetch_related("variants__material").get(pk=self.product.pk)
        self.assertEqual(product.available_materials, [self.pla])


class ValidationTests(TestCase):
    def test_active_product_requires_category_and_name(self):
        product = make_product(sku="X-7", name=None)
        product.status = ProductStatus.ACTIVE
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        errors = context.exception.message_dict
        self.assertIn("category", errors)
        self.assertIn("status", errors)

    def test_active_product_no_longer_validates_a_price_of_its_own(self):
        """Preço é da variante — o produto não tem o que validar aqui."""
        product = make_product(sku="X-7B", name=None)
        product.status = ProductStatus.ACTIVE
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertNotIn("sale_price", context.exception.message_dict)

    def test_active_product_with_minimum_information_is_valid(self):
        category = make_category()
        product = make_product(sku="X-8", name="Gato Pompom", category=category)
        product.status = ProductStatus.ACTIVE
        product.full_clean()
        product.save()
        self.assertEqual(product.status, ProductStatus.ACTIVE)

    def test_sku_is_required(self):
        product = Product(sku="")
        with self.assertRaises(ValidationError) as context:
            product.full_clean()
        self.assertIn("sku", context.exception.message_dict)


class SellableQuerysetTests(TestCase):
    """``sellable()`` é o filtro que a loja usa: ativo e com o que vender."""

    def setUp(self):
        self.category = make_category()

    def make_active(self, sku, com_variante=True, **kwargs):
        product = make_product(
            sku=sku, name=f"Produto {sku}", category=self.category,
            status=ProductStatus.ACTIVE, **kwargs
        )
        if com_variante:
            make_variant(product, sku=f"{sku}-V", stock_quantity=2)
        return product

    def test_product_without_variant_is_not_sellable(self):
        self.make_active("S-1")
        self.make_active("S-2", com_variante=False)

        self.assertEqual([p.sku for p in Product.objects.sellable()], ["S-1"])

    def test_product_with_only_inactive_variants_is_not_sellable(self):
        product = self.make_active("S-3", com_variante=False)
        make_variant(product, sku="S-3-V", stock_quantity=2, is_active=False)

        self.assertFalse(Product.objects.sellable().filter(pk=product.pk).exists())

    def test_draft_product_is_not_sellable(self):
        product = make_product(sku="S-4", name="Rascunho", category=self.category)
        make_variant(product, sku="S-4-V", stock_quantity=2)

        self.assertFalse(Product.objects.sellable().filter(pk=product.pk).exists())

    def test_sellable_does_not_duplicate_a_product_with_many_variants(self):
        product = self.make_active("S-5")
        make_variant(product, sku="S-5-B", size="G", stock_quantity=1)
        make_variant(product, sku="S-5-C", size="GG", stock_quantity=1)

        self.assertEqual(Product.objects.sellable().filter(pk=product.pk).count(), 1)

    def test_featured_only_lists_sellable_products(self):
        com = self.make_active("F-1", is_featured=True, featured_order=1)
        self.make_active("F-2", com_variante=False, is_featured=True, featured_order=2)

        self.assertEqual(list(Product.objects.featured()), [com])


class RelationTests(TestCase):
    def test_product_belongs_to_a_category(self):
        root = make_category(slug="modelos", name="Modelos")
        animals = make_category(slug="animais", name="Animais", parent=root)
        product = make_product(category=animals)

        self.assertEqual(product.category, animals)
        self.assertEqual(product.category.parent, root)
        self.assertIn(product, root.children.first().products.all())

    def test_category_with_products_cannot_be_deleted(self):
        category = make_category()
        make_product(category=category)
        with self.assertRaises(ProtectedError):
            category.delete()

    def test_brand_is_optional(self):
        product = make_product()
        self.assertIsNone(product.brand)

        brand = Brand.objects.create(name="Bambu Lab")
        product.brand = brand
        product.save()
        self.assertEqual(Product.objects.get(pk=product.pk).brand, brand)

    def test_variants_die_with_the_product(self):
        product = make_product()
        make_variant(product, sku="V-1")
        product.delete()
        self.assertEqual(ProductVariant.objects.count(), 0)


class FeaturedTests(TestCase):
    def test_featured_queryset_respects_order_and_status(self):
        category = make_category()

        def destaque(sku, order, status=ProductStatus.ACTIVE, is_featured=True):
            product = make_product(
                sku=sku, name=sku, category=category, status=status,
                is_featured=is_featured, featured_order=order,
            )
            make_variant(product, sku=f"{sku}-V", stock_quantity=1)
            return product

        second = destaque("F-2", 2)
        first = destaque("F-1", 1)
        destaque("F-3", 0, is_featured=False)
        destaque("F-4", 0, status=ProductStatus.DRAFT)

        self.assertEqual(list(Product.objects.featured()), [first, second])
