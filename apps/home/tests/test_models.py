"""Testes dos models da Home: validação, tradução, ordem e CTA."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.home.models import (
    CtaTarget,
    HomeSection,
    HomeSectionLayout,
    HomeSectionProduct,
    HomeSectionTranslation,
    HomeSectionType,
    MAX_PRODUCT_LIMIT,
)
from apps.core.testing import (
    add_products,
    make_banner,
    make_category,
    make_product,
    make_section,
    translate_section,
)


class SectionDefaultsTests(TestCase):
    def test_defaults(self):
        section = make_section()
        self.assertTrue(section.is_active)
        self.assertEqual(section.layout, HomeSectionLayout.GRID)
        self.assertEqual(section.product_limit, 4)
        self.assertEqual(section.sort_order, 0)
        self.assertTrue(section.include_subcategories)

    def test_str_uses_internal_name(self):
        section = make_section(internal_name="Destaques de Modelos", title="Nossos favoritos")
        self.assertEqual(str(section), "Destaques de Modelos")

    def test_title_falls_back_to_internal_name(self):
        section = HomeSection.objects.create(internal_name="Sem tradução ainda")
        self.assertEqual(section.title, "Sem tradução ainda")

    def test_ordering_is_by_sort_order(self):
        third = make_section(internal_name="C", title="C", sort_order=3)
        first = make_section(internal_name="A", title="A", sort_order=1)
        second = make_section(internal_name="B", title="B", sort_order=2)
        self.assertEqual(list(HomeSection.objects.ordered()), [first, second, third])


class SectionValidationTests(TestCase):
    def test_category_type_requires_category(self):
        section = HomeSection(
            internal_name="Filamentos", section_type=HomeSectionType.CATEGORY_PRODUCTS
        )
        with self.assertRaises(ValidationError) as context:
            section.full_clean()
        self.assertIn("category", context.exception.message_dict)

    def test_category_type_is_enforced_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSection.objects.create(
                internal_name="Filamentos", section_type=HomeSectionType.CATEGORY_PRODUCTS
            )

    def test_product_limit_must_be_at_least_one(self):
        section = HomeSection(internal_name="X", product_limit=0)
        with self.assertRaises(ValidationError) as context:
            section.full_clean()
        self.assertIn("product_limit", context.exception.message_dict)

    def test_product_limit_has_an_upper_bound(self):
        section = HomeSection(internal_name="X", product_limit=MAX_PRODUCT_LIMIT + 1)
        with self.assertRaises(ValidationError) as context:
            section.full_clean()
        self.assertIn("product_limit", context.exception.message_dict)

    def test_product_limit_bound_is_enforced_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSection.objects.create(internal_name="X", product_limit=0)

    def test_best_sellers_is_flagged_as_without_data_source(self):
        section = make_section(section_type=HomeSectionType.BEST_SELLERS)
        self.assertFalse(section.has_data_source)

    def test_other_types_have_a_data_source(self):
        for section_type in (
            HomeSectionType.MANUAL_PRODUCTS,
            HomeSectionType.FEATURED_PRODUCTS,
            HomeSectionType.NEWEST_PRODUCTS,
        ):
            with self.subTest(section_type=section_type):
                section = HomeSection(internal_name="X", section_type=section_type)
                self.assertTrue(section.has_data_source)


class SectionCtaTests(TestCase):
    def setUp(self):
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="P-1", name="Gato", category=self.category)

    def test_no_cta_by_default(self):
        section = make_section()
        self.assertFalse(section.has_cta)
        self.assertEqual(section.cta_link, "")

    def test_category_cta_points_to_the_category_page(self):
        section = make_section(cta_target=CtaTarget.CATEGORY, cta_category=self.category)
        self.assertTrue(section.has_cta)
        self.assertEqual(section.cta_link, self.category.get_absolute_url())

    def test_product_cta_points_to_the_product_page(self):
        section = make_section(cta_target=CtaTarget.PRODUCT, cta_product=self.product)
        self.assertEqual(section.cta_link, self.product.get_absolute_url())

    def test_free_url_cta(self):
        section = make_section(cta_target=CtaTarget.URL, cta_url="/promocoes/")
        self.assertEqual(section.cta_link, "/promocoes/")

    def test_category_cta_requires_a_category(self):
        section = HomeSection(internal_name="X", cta_target=CtaTarget.CATEGORY)
        with self.assertRaises(ValidationError) as context:
            section.full_clean()
        self.assertIn("cta_category", context.exception.message_dict)

    def test_url_cta_requires_a_url(self):
        section = HomeSection(internal_name="X", cta_target=CtaTarget.URL)
        with self.assertRaises(ValidationError) as context:
            section.full_clean()
        self.assertIn("cta_url", context.exception.message_dict)

    def test_dangerous_url_scheme_is_rejected(self):
        section = HomeSection(
            internal_name="X", cta_target=CtaTarget.URL, cta_url="javascript:alert(1)"
        )
        with self.assertRaises(ValidationError) as context:
            section.full_clean()
        self.assertIn("cta_url", context.exception.message_dict)


class SectionTranslationTests(TestCase):
    def setUp(self):
        self.section = make_section(
            internal_name="Destaques", title="Destaques de Modelos", subtitle="Nossos favoritos."
        )
        translate_section(self.section, "fr", "Modèles à la une", "Nos préférés.", "Tout voir")

    def test_reads_the_requested_language(self):
        self.assertEqual(self.section.tr("title", language="pt"), "Destaques de Modelos")
        self.assertEqual(self.section.tr("title", language="fr"), "Modèles à la une")

    def test_falls_back_to_portuguese(self):
        self.assertEqual(self.section.tr("title", language="de"), "Destaques de Modelos")

    def test_field_level_fallback(self):
        # A tradução francesa tem cta_label; a portuguesa não.
        self.assertEqual(self.section.tr("cta_label", language="fr"), "Tout voir")
        self.assertEqual(self.section.tr("cta_label", language="pt"), "Tout voir")

    def test_duplicate_language_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSectionTranslation.objects.create(
                master=self.section, language="pt", title="Outro título"
            )

    def test_empty_title_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSectionTranslation.objects.create(master=self.section, language="nl", title="")

    def test_translations_die_with_the_section(self):
        self.section.delete()
        self.assertEqual(HomeSectionTranslation.objects.count(), 0)


class ManualProductsTests(TestCase):
    def setUp(self):
        self.section = make_section(section_type=HomeSectionType.MANUAL_PRODUCTS)
        self.first = make_product(sku="P-1", name="Primeiro")
        self.second = make_product(sku="P-2", name="Segundo")

    def test_products_keep_the_configured_order(self):
        add_products(self.section, [self.second, self.first])
        ordered = [item.product for item in self.section.items.all()]
        self.assertEqual(ordered, [self.second, self.first])

    def test_reordering_changes_the_result(self):
        add_products(self.section, [self.second, self.first])
        item = self.section.items.get(product=self.first)
        item.sort_order = 0
        item.save()
        ordered = [item.product for item in self.section.items.all()]
        self.assertEqual(ordered, [self.first, self.second])

    def test_a_product_cannot_be_added_twice(self):
        add_products(self.section, [self.first])
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSectionProduct.objects.create(section=self.section, product=self.first)

    def test_removing_a_product_removes_it_from_the_section(self):
        add_products(self.section, [self.first, self.second])
        self.first.delete()
        self.assertEqual(self.section.items.count(), 1)


class BannerTests(TestCase):
    def test_banner_defaults(self):
        banner = make_banner(title="Boas-vindas")
        self.assertTrue(banner.is_active)
        self.assertEqual(banner.title, "Boas-vindas")
        self.assertFalse(banner.has_cta)

    def test_image_alt_falls_back_to_the_title(self):
        banner = make_banner(title="Boas-vindas")
        self.assertEqual(banner.image_alt, "Boas-vindas")

    def test_banner_cta_validation(self):
        banner = make_banner(title="Boas-vindas")
        banner.cta_target = CtaTarget.URL
        banner.cta_url = ""
        with self.assertRaises(ValidationError) as context:
            banner.full_clean()
        self.assertIn("cta_url", context.exception.message_dict)
