"""Testes da resolução do conteúdo da Home (apps/home/services.py)."""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import ProductStatus
from apps.home import services
from apps.home.models import HomeSectionType
from apps.core.testing import (
    add_products,
    make_category,
    make_product,
    make_section,
)


class ManualSectionTests(TestCase):
    def setUp(self):
        self.section = make_section(section_type=HomeSectionType.MANUAL_PRODUCTS, product_limit=4)
        self.a = make_product(sku="A", name="Alfa")
        self.b = make_product(sku="B", name="Bravo")
        self.c = make_product(sku="C", name="Charlie")

    def test_respects_the_administrator_order(self):
        add_products(self.section, [self.c, self.a, self.b])
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.c, self.a, self.b])

    def test_respects_the_limit(self):
        self.section.product_limit = 2
        self.section.save()
        add_products(self.section, [self.c, self.a, self.b])
        [resolved] = services.get_home_sections()
        self.assertEqual(len(resolved.products), 2)
        self.assertEqual(resolved.products, [self.c, self.a])

    def test_ignores_products_that_are_not_active(self):
        self.b.status = ProductStatus.DRAFT
        self.b.save()
        add_products(self.section, [self.a, self.b, self.c])
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.a, self.c])

    def test_section_without_products_is_dropped(self):
        self.assertEqual(services.get_home_sections(), [])


class CategorySectionTests(TestCase):
    def setUp(self):
        self.models = make_category(slug="modelos", name="Modelos")
        self.animals = make_category(slug="animais", name="Animais", parent=self.models)
        self.filaments = make_category(slug="filamentos", name="Filamentos")

        self.cat = make_product(sku="GATO", name="Gato", category=self.animals)
        self.vase = make_product(sku="VASO", name="Vaso", category=self.models)
        self.filament = make_product(sku="PLA", name="PLA", category=self.filaments)

    def test_includes_subcategories_by_default(self):
        make_section(section_type=HomeSectionType.CATEGORY_PRODUCTS, category=self.models)
        [resolved] = services.get_home_sections()
        self.assertCountEqual(resolved.products, [self.cat, self.vase])

    def test_can_exclude_subcategories(self):
        make_section(
            section_type=HomeSectionType.CATEGORY_PRODUCTS,
            category=self.models,
            include_subcategories=False,
        )
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.vase])

    def test_only_products_of_the_chosen_category(self):
        make_section(section_type=HomeSectionType.CATEGORY_PRODUCTS, category=self.filaments)
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.filament])

    def test_empty_category_section_is_dropped(self):
        empty = make_category(slug="impressoras", name="Impressoras")
        make_section(section_type=HomeSectionType.CATEGORY_PRODUCTS, category=empty)
        self.assertEqual(services.get_home_sections(), [])


class FeaturedAndNewestTests(TestCase):
    def setUp(self):
        self.first = make_product(sku="F1", name="Primeiro", is_featured=True, featured_order=2)
        self.second = make_product(sku="F2", name="Segundo", is_featured=True, featured_order=1)
        self.plain = make_product(sku="P1", name="Comum")

    def test_featured_uses_featured_flag_and_order(self):
        make_section(section_type=HomeSectionType.FEATURED_PRODUCTS)
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.second, self.first])

    def test_featured_ignores_inactive_products(self):
        self.second.status = ProductStatus.INACTIVE
        self.second.save()
        make_section(section_type=HomeSectionType.FEATURED_PRODUCTS)
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.first])

    def test_newest_is_ordered_by_creation(self):
        make_section(section_type=HomeSectionType.NEWEST_PRODUCTS, product_limit=2)
        [resolved] = services.get_home_sections()
        self.assertEqual(resolved.products, [self.plain, self.second])

    def test_featured_section_without_featured_products_is_dropped(self):
        self.first.is_featured = False
        self.first.save()
        self.second.is_featured = False
        self.second.save()
        make_section(section_type=HomeSectionType.FEATURED_PRODUCTS)
        self.assertEqual(services.get_home_sections(), [])


class BestSellersTests(TestCase):
    def test_best_sellers_returns_nothing_until_orders_exist(self):
        make_product(sku="P1", name="Produto", is_featured=True)
        make_section(section_type=HomeSectionType.BEST_SELLERS)

        # Sem módulo de pedidos não há dado de vendas: a seção some da Home
        # em vez de usar um critério inventado.
        self.assertEqual(services.get_home_sections(), [])


class SectionSelectionTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="P1", name="Produto", is_featured=True)

    def test_inactive_sections_are_ignored(self):
        make_section(internal_name="Inativa", title="Inativa", is_active=False)
        self.assertEqual(services.get_home_sections(), [])

    def test_sections_come_in_ascending_order(self):
        make_section(internal_name="C", title="C", sort_order=3)
        make_section(internal_name="A", title="A", sort_order=1)
        make_section(internal_name="B", title="B", sort_order=2)
        titles = [resolved.title for resolved in services.get_home_sections()]
        self.assertEqual(titles, ["A", "B", "C"])


class CategoryCardsTests(TestCase):
    def setUp(self):
        self.models = make_category(slug="modelos", name="Modelos", sort_order=1)
        self.animals = make_category(slug="animais", name="Animais", parent=self.models)
        self.filaments = make_category(slug="filamentos", name="Filamentos", sort_order=2)
        make_category(slug="impressoras", name="Impressoras", sort_order=3)

        make_product(sku="A1", name="Gato", category=self.animals)
        make_product(sku="A2", name="Cão", category=self.animals)
        make_product(sku="M1", name="Vaso", category=self.models)
        make_product(sku="F1", name="PLA", category=self.filaments)

    def test_only_root_categories_with_products(self):
        cards = services.get_category_cards()
        names = [card.name for card in cards]
        self.assertEqual(names, ["Modelos", "Filamentos"])

    def test_counts_include_the_whole_subtree(self):
        cards = {card.name: card.product_count for card in services.get_category_cards()}
        self.assertEqual(cards["Modelos"], 3)
        self.assertEqual(cards["Filamentos"], 1)

    def test_inactive_products_are_not_counted(self):
        product = make_product(sku="A3", name="Rascunho", category=self.animals, status=ProductStatus.DRAFT)
        cards = {card.name: card.product_count for card in services.get_category_cards()}
        self.assertEqual(cards["Modelos"], 3)
        self.assertIsNotNone(product.pk)


class BannerServiceTests(TestCase):
    def test_no_banner_returns_none(self):
        self.assertIsNone(services.get_active_banner())

    def test_returns_the_first_active_banner_in_order(self):
        from apps.core.testing import make_banner

        make_banner(internal_name="Segundo", title="Segundo", sort_order=2)
        first = make_banner(internal_name="Primeiro", title="Primeiro", sort_order=1)
        make_banner(internal_name="Desativado", title="Desativado", sort_order=0, is_active=False)

        self.assertEqual(services.get_active_banner(), first)


class QueryBudgetTests(TestCase):
    """A Home não pode crescer em consultas junto com o catálogo."""

    def build(self, product_count):
        category = make_category(slug="modelos", name="Modelos")
        products = [
            make_product(
                sku=f"P{index}",
                name=f"Produto {index}",
                category=category,
                price=Decimal("9.90"),
                is_featured=True,
            )
            for index in range(product_count)
        ]
        section = make_section(section_type=HomeSectionType.MANUAL_PRODUCTS, product_limit=12)
        add_products(section, products)
        make_section(
            internal_name="Destaques", title="Destaques",
            section_type=HomeSectionType.FEATURED_PRODUCTS, sort_order=2, product_limit=12,
        )
        return products

    def test_query_count_does_not_grow_with_the_catalogue(self):
        self.build(3)
        baseline = self.count_queries()

        self.build_more(6)
        with self.assertNumQueries(baseline):
            services.get_home_context()

    def count_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            services.get_home_context()
        return len(captured)

    def build_more(self, product_count):
        category = make_category(slug="extra", name="Extra")
        for index in range(product_count):
            make_product(
                sku=f"X{index}", name=f"Extra {index}", category=category, is_featured=True
            )
