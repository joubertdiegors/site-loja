"""Testes da árvore de categorias."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.categories.models import MAX_CATEGORY_DEPTH, Category, CategoryTranslation


def make_category(slug, name, parent=None, language="pt"):
    category = Category.objects.create(slug=slug, parent=parent)
    CategoryTranslation.objects.create(master=category, language=language, name=name)
    category.refresh_translations()
    return category


class CategoryHierarchyTests(TestCase):
    def setUp(self):
        self.models = make_category("modelos", "Modelos")
        self.animals = make_category("animais", "Animais", parent=self.models)
        self.cats = make_category("gatos", "Gatos", parent=self.animals)

    def test_unlimited_hierarchy(self):
        self.assertEqual(self.cats.depth, 2)
        self.assertEqual(self.cats.ancestors(), [self.models, self.animals])

    def test_full_path(self):
        self.assertEqual(self.cats.full_path(" > "), "Modelos > Animais > Gatos")

    def test_children_relation(self):
        self.assertEqual(list(self.models.children.all()), [self.animals])

    def test_roots(self):
        self.assertEqual(list(Category.objects.roots()), [self.models])

    def test_category_cannot_be_its_own_parent(self):
        self.models.parent = self.models
        with self.assertRaises(ValidationError):
            self.models.full_clean()

    def test_cycles_are_rejected(self):
        self.models.parent = self.cats
        with self.assertRaises(ValidationError):
            self.models.full_clean()

    def test_depth_limit(self):
        node = self.cats
        for index in range(MAX_CATEGORY_DEPTH):
            node = Category(slug=f"nivel-{index}", parent=node)
            try:
                node.full_clean()
            except ValidationError as error:
                self.assertIn("parent", error.message_dict)
                return
            node.save()
        self.fail("O limite de profundidade não foi aplicado.")

    def test_parent_is_protected_against_deletion(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.models.delete()


class CategorySlugTests(TestCase):
    def test_slug_must_be_unique(self):
        make_category("modelos", "Modelos")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(slug="modelos")


class CategoryTranslationTests(TestCase):
    def setUp(self):
        self.category = make_category("animais", "Animais")
        CategoryTranslation.objects.create(master=self.category, language="fr", name="Animaux")
        self.category.refresh_translations()

    def test_name_by_language(self):
        self.assertEqual(self.category.name_in("pt"), "Animais")
        self.assertEqual(self.category.name_in("fr"), "Animaux")

    def test_fallback_to_portuguese(self):
        self.assertEqual(self.category.name_in("tr"), "Animais")

    def test_duplicate_language_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CategoryTranslation.objects.create(
                master=self.category, language="pt", name="Duplicado"
            )
