"""Testes da quantidade de produtos por página no Shop."""

from decimal import Decimal

from django.test import TestCase, override_settings

from apps.core.testing import LanguageResetMixin, make_category, make_product

SHOP = "/modelos/"


class PageSizeTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.models = make_category(slug="modelos", name="Modelos", sort_order=1)
        self.animals = make_category(slug="animais", name="Animais", parent=self.models)
        self.decor = make_category(slug="decoracao", name="Decoração", parent=self.models)

        for index in range(20):
            category = self.animals if index < 12 else self.decor
            make_product(
                sku=f"P-{index:02d}",
                name=f"Produto {index:02d}",
                category=category,
                price=Decimal("10.00"),
            )

    def shown(self, response):
        return len(response.context["products"])

    # -- opções válidas ----------------------------------------------------

    def test_default_is_twelve(self):
        response = self.client.get(SHOP)

        self.assertEqual(response.context["page_size"], 12)
        self.assertEqual(self.shown(response), 12)

    def test_four_per_page(self):
        response = self.client.get(SHOP, {"per_page": 4})

        self.assertEqual(self.shown(response), 4)
        self.assertEqual(response.context["paginator"].num_pages, 5)

    def test_eight_per_page(self):
        response = self.client.get(SHOP, {"per_page": 8})

        self.assertEqual(self.shown(response), 8)
        self.assertEqual(response.context["paginator"].num_pages, 3)

    def test_twelve_per_page(self):
        response = self.client.get(SHOP, {"per_page": 12})

        self.assertEqual(self.shown(response), 12)
        self.assertEqual(response.context["paginator"].num_pages, 2)

    def test_sixteen_per_page(self):
        response = self.client.get(SHOP, {"per_page": 16})

        self.assertEqual(self.shown(response), 16)
        self.assertEqual(response.context["paginator"].num_pages, 2)

    def test_twenty_per_page_fits_everything(self):
        response = self.client.get(SHOP, {"per_page": 20})

        self.assertEqual(self.shown(response), 20)
        self.assertEqual(response.context["paginator"].num_pages, 1)

    def test_every_option_is_a_multiple_of_four(self):
        for option in self.client.get(SHOP).context["page_size_options"]:
            with self.subTest(option=option):
                self.assertEqual(option % 4, 0)

    # -- valores inválidos -------------------------------------------------

    def test_value_outside_the_list_is_ignored(self):
        response = self.client.get(SHOP, {"per_page": 7})

        self.assertEqual(response.context["page_size"], 12)
        self.assertEqual(self.shown(response), 12)

    def test_huge_value_is_ignored(self):
        """Ninguém pede a base inteira em uma requisição."""
        response = self.client.get(SHOP, {"per_page": 100000})

        self.assertEqual(response.context["page_size"], 12)

    def test_text_value_is_ignored(self):
        response = self.client.get(SHOP, {"per_page": "todos"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_size"], 12)

    def test_negative_value_is_ignored(self):
        response = self.client.get(SHOP, {"per_page": -5})
        self.assertEqual(response.context["page_size"], 12)

    # -- controle na interface ---------------------------------------------

    def test_selector_is_rendered_with_the_options(self):
        response = self.client.get(SHOP)

        self.assertContains(response, 'name="per_page"')
        self.assertContains(response, "Mostrar")
        for option in (4, 8, 12, 16, 20, 24):
            self.assertContains(response, f'value="{option}"')

    def test_current_choice_is_selected(self):
        response = self.client.get(SHOP, {"per_page": 8})
        self.assertContains(response, '<option value="8" selected>')

    # -- persistência ------------------------------------------------------

    def test_kept_when_changing_category(self):
        response = self.client.get(SHOP, {"per_page": 4, "categoria": "decoracao"})

        self.assertEqual(response.context["page_size"], 4)
        self.assertEqual(response.context["selected_category"], self.decor)
        self.assertEqual(self.shown(response), 4)

    def test_kept_when_changing_sort(self):
        response = self.client.get(SHOP, {"per_page": 8, "ordenar": "nome-az"})

        self.assertEqual(response.context["page_size"], 8)
        self.assertEqual(response.context["sort_key"], "nome-az")

    def test_kept_in_the_pagination_links(self):
        response = self.client.get(SHOP, {"per_page": 4})

        self.assertContains(response, "per_page=4")

    def test_kept_when_paginating(self):
        response = self.client.get(SHOP, {"per_page": 4, "page": 3})

        self.assertEqual(response.context["page_size"], 4)
        self.assertEqual(response.context["page_obj"].number, 3)

    def test_kept_in_the_category_links(self):
        response = self.client.get(SHOP, {"per_page": 16})
        html = response.content.decode()

        self.assertIn("categoria=animais", html)
        self.assertIn("per_page=16", html)

    def test_kept_when_changing_language(self):
        response = self.client.post(
            "/i18n/setlang/", {"language": "fr", "next": f"{SHOP}?per_page=16&ordenar=nome-az"}
        )
        self.assertEqual(response["Location"], "/fr/modelos/?per_page=16&ordenar=nome-az")

    @override_settings(SHOP_PAGE_SIZE_OPTIONS=(6, 12), SHOP_PAGE_SIZE=6)
    def test_options_are_configurable(self):
        response = self.client.get(SHOP, {"per_page": 6})

        self.assertEqual(self.shown(response), 6)
        self.assertEqual(list(response.context["page_size_options"]), [6, 12])
