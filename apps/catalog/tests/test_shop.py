"""Testes da vitrine de Modelos (/modelos/)."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import ProductMedia, ProductStatus
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    translate_category,
    translate_product,
)

SHOP = "/modelos/"


class ShopBase(LanguageResetMixin, TestCase):
    """Árvore mínima: Modelos > (Animais > Gatos, Decoração) e Filamentos."""

    def setUp(self):
        super().setUp()
        self.models = make_category(slug="modelos", name="Modelos", sort_order=1)
        self.animals = make_category(slug="animais", name="Animais", parent=self.models, sort_order=1)
        self.cats = make_category(slug="gatos", name="Gatos", parent=self.animals, sort_order=1)
        self.decor = make_category(slug="decoracao", name="Decoração", parent=self.models, sort_order=2)
        self.filaments = make_category(slug="filamentos", name="Filamentos", sort_order=2)


class ShopPageTests(ShopBase):
    def setUp(self):
        super().setUp()
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.cats,
            price=Decimal("8.90"), stock_quantity=5,
        )

    def test_page_responds(self):
        response = self.client.get(SHOP)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/shop.html")

    def test_url_name(self):
        self.assertEqual(reverse("catalog:models_shop"), SHOP)

    def test_seo_basics(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "<h1")
        self.assertContains(response, "Modelos | JD PRINT")
        self.assertContains(response, 'name="description"')

    def test_shows_products(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "8,90")

    def test_product_links_to_its_page(self):
        response = self.client.get(SHOP)
        self.assertContains(response, self.product.get_absolute_url())

    def test_sidebar_lists_the_category_tree(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "Animais")
        self.assertContains(response, "Gatos")
        self.assertContains(response, "Decoração")
        self.assertContains(response, "Todos")

    def test_card_has_an_add_to_cart_button(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "Adicionar ao carrinho")
        self.assertContains(response, reverse("cart:add"))

    def test_sold_out_product_cannot_be_added(self):
        make_product(sku="ESG-01", name="Esgotado", category=self.cats, stock_quantity=0)
        response = self.client.get(SHOP)

        self.assertContains(response, "Esgotado")


class ShopScopeTests(ShopBase):
    """Só produtos da árvore de Modelos, e só os ativos."""

    def setUp(self):
        super().setUp()
        self.cat = make_product(sku="GATO-01", name="Gato", category=self.cats)
        self.vase = make_product(sku="VASO-01", name="Vaso", category=self.decor)
        self.filament = make_product(sku="PLA-01", name="Filamento PLA", category=self.filaments)
        self.draft = make_product(
            sku="RAS-01", name="Rascunho", category=self.cats, status=ProductStatus.DRAFT
        )
        self.inactive = make_product(
            sku="INA-01", name="Inativo", category=self.cats, status=ProductStatus.INACTIVE
        )

    def test_only_products_from_the_models_tree(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "Gato")
        self.assertContains(response, "Vaso")
        self.assertNotContains(response, "Filamento PLA")

    def test_only_active_products(self):
        response = self.client.get(SHOP)

        self.assertNotContains(response, "Rascunho")
        self.assertNotContains(response, "Inativo")

    def test_count_respects_the_scope(self):
        response = self.client.get(SHOP)
        self.assertEqual(response.context["result_count"], 2)

    def test_product_without_category_is_not_shown(self):
        make_product(sku="SEM-01", name="Sem categoria", category=None)
        response = self.client.get(SHOP)

        self.assertNotContains(response, "Sem categoria")

    @override_settings(SHOP_MODELS_CATEGORY_SLUG="inexistente")
    def test_missing_root_category_shows_a_notice(self):
        response = self.client.get(SHOP)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vitrine ainda não configurada")


class ShopFilterTests(ShopBase):
    def setUp(self):
        super().setUp()
        self.cat = make_product(sku="GATO-01", name="Gato Pompom", category=self.cats)
        self.dog = make_product(sku="CAO-01", name="Cachorro", category=self.animals)
        self.vase = make_product(sku="VASO-01", name="Vaso Espiral", category=self.decor)

    def test_filter_by_category(self):
        response = self.client.get(SHOP, {"categoria": "decoracao"})

        self.assertContains(response, "Vaso Espiral")
        self.assertNotContains(response, "Gato Pompom")
        self.assertEqual(response.context["result_count"], 1)

    def test_filter_includes_subcategories(self):
        response = self.client.get(SHOP, {"categoria": "animais"})

        self.assertContains(response, "Gato Pompom")  # está em Gatos, filha de Animais
        self.assertContains(response, "Cachorro")
        self.assertEqual(response.context["result_count"], 2)

    def test_selected_category_is_highlighted(self):
        response = self.client.get(SHOP, {"categoria": "decoracao"})

        self.assertContains(response, "shop-filter-active")
        self.assertEqual(response.context["selected_category"], self.decor)

    def test_todos_removes_the_filter(self):
        response = self.client.get(SHOP)

        self.assertIsNone(response.context["selected_category"])
        self.assertEqual(response.context["result_count"], 3)

    def test_category_outside_the_tree_is_ignored(self):
        make_product(sku="PLA-01", name="Filamento PLA", category=self.filaments)
        response = self.client.get(SHOP, {"categoria": "filamentos"})

        self.assertIsNone(response.context["selected_category"])
        self.assertNotContains(response, "Filamento PLA")

    def test_unknown_category_is_ignored(self):
        response = self.client.get(SHOP, {"categoria": "nao-existe"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_category"])

    def test_sidebar_counts(self):
        response = self.client.get(SHOP)
        counts = {node["slug"]: node["count"] for node in response.context["sidebar_nodes"]}

        self.assertEqual(counts["animais"], 2)
        self.assertEqual(counts["gatos"], 1)
        self.assertEqual(counts["decoracao"], 1)
        self.assertEqual(response.context["total_count"], 3)


class ShopSortingTests(ShopBase):
    def setUp(self):
        super().setUp()
        self.cheap = make_product(sku="C-1", name="Zebra", category=self.cats, price=Decimal("5.00"))
        self.mid = make_product(sku="C-2", name="Abelha", category=self.cats, price=Decimal("15.00"))
        self.expensive = make_product(sku="C-3", name="Macaco", category=self.cats, price=Decimal("30.00"))

    def names(self, response):
        return [product.display_name for product in response.context["products"]]

    def test_default_is_newest_first(self):
        response = self.client.get(SHOP)
        self.assertEqual(self.names(response), ["Macaco", "Abelha", "Zebra"])

    def test_price_ascending(self):
        response = self.client.get(SHOP, {"ordenar": "preco-asc"})
        self.assertEqual(self.names(response), ["Zebra", "Abelha", "Macaco"])

    def test_price_descending(self):
        response = self.client.get(SHOP, {"ordenar": "preco-desc"})
        self.assertEqual(self.names(response), ["Macaco", "Abelha", "Zebra"])

    def test_name_a_to_z(self):
        response = self.client.get(SHOP, {"ordenar": "nome-az"})
        self.assertEqual(self.names(response), ["Abelha", "Macaco", "Zebra"])

    def test_name_z_to_a(self):
        response = self.client.get(SHOP, {"ordenar": "nome-za"})
        self.assertEqual(self.names(response), ["Zebra", "Macaco", "Abelha"])

    def test_name_sorting_uses_the_active_language(self):
        # Em português a ordem A-Z é Abelha, Macaco, Zebra. Traduzindo "Zebra"
        # para "Alpha", em francês ela passa a ser a segunda — prova de que a
        # ordenação usa o nome traduzido, e não o português.
        translate_product(self.cheap, "fr", "Alpha")

        self.assertEqual(
            self.names(self.client.get(SHOP, {"ordenar": "nome-az"})),
            ["Abelha", "Macaco", "Zebra"],
        )
        self.assertEqual(
            self.names(self.client.get("/fr/modelos/", {"ordenar": "nome-az"})),
            ["Abelha", "Alpha", "Macaco"],
        )

    def test_invalid_sort_falls_back_to_the_default(self):
        response = self.client.get(SHOP, {"ordenar": "qualquer-coisa"})
        self.assertEqual(response.context["sort_key"], "recentes")

    def test_sorting_is_kept_with_the_category_filter(self):
        response = self.client.get(SHOP, {"categoria": "gatos", "ordenar": "preco-asc"})

        self.assertEqual(response.context["sort_key"], "preco-asc")
        self.assertEqual(self.names(response), ["Zebra", "Abelha", "Macaco"])


@override_settings(SHOP_PAGE_SIZE=4)
class ShopPaginationTests(ShopBase):
    def setUp(self):
        super().setUp()
        for index in range(9):
            make_product(
                sku=f"P-{index}", name=f"Produto {index:02d}", category=self.cats,
                price=Decimal("10.00"),
            )

    def test_first_page_is_limited(self):
        response = self.client.get(SHOP)

        self.assertEqual(len(response.context["products"]), 4)
        self.assertEqual(response.context["paginator"].num_pages, 3)

    def test_count_is_the_total_not_the_page(self):
        response = self.client.get(SHOP)
        self.assertEqual(response.context["result_count"], 9)

    def test_second_page(self):
        response = self.client.get(SHOP, {"page": 2})

        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(len(response.context["products"]), 4)

    def test_last_page_has_the_remainder(self):
        response = self.client.get(SHOP, {"page": 3})
        self.assertEqual(len(response.context["products"]), 1)

    def test_pagination_keeps_the_filters(self):
        response = self.client.get(SHOP, {"categoria": "gatos", "ordenar": "nome-az", "page": 2})

        self.assertEqual(response.context["selected_category"], self.cats)
        self.assertEqual(response.context["sort_key"], "nome-az")
        self.assertContains(response, "categoria=gatos")
        self.assertContains(response, "ordenar=nome-az")

    def test_pagination_links_are_rendered(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "Próximo")
        self.assertContains(response, "page-link")

    def test_out_of_range_page_returns_404(self):
        response = self.client.get(SHOP, {"page": 99})
        self.assertEqual(response.status_code, 404)


class ShopLanguageTests(ShopBase):
    def setUp(self):
        super().setUp()
        translate_category(self.models, "fr", "Modèles")
        translate_category(self.cats, "fr", "Chats")
        self.product = make_product(sku="GATO-01", name="Gato Pompom", category=self.cats)
        translate_product(self.product, "fr", "Chat Pompon")
        translate_product(self.product, "en", "Pompom Cat")

    def test_portuguese(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "Gatos")

    def test_french(self):
        response = self.client.get("/fr/modelos/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chat Pompon")
        self.assertContains(response, "Chats")
        self.assertContains(response, 'lang="fr"')

    def test_english(self):
        response = self.client.get("/en/modelos/")
        self.assertContains(response, "Pompom Cat")

    def test_untranslated_language_falls_back_to_portuguese(self):
        # Holandês está na loja; este produto não tem tradução para ele.
        response = self.client.get("/nl/modelos/")

        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "Gatos")

    def test_filter_keeps_the_language(self):
        response = self.client.get("/fr/modelos/", {"categoria": "gatos"})

        self.assertContains(response, "Chat Pompon")
        self.assertContains(response, 'lang="fr"')


class ShopMediaTests(ShopBase):
    def setUp(self):
        super().setUp()
        self.product = make_product(sku="GATO-01", name="Gato Pompom", category=self.cats)

    @override_settings(MEDIA_ROOT="/tmp/jdprint-shop-tests")
    def test_primary_image_is_used(self):
        ProductMedia.objects.create(
            product=self.product, file=SimpleUploadedFile("foto.jpg", b"x"), is_primary=True
        )
        response = self.client.get(SHOP)

        self.assertContains(response, "foto")

    def test_product_without_image_does_not_break(self):
        response = self.client.get(SHOP)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foto em breve")


class ShopHtmxTests(ShopBase):
    def setUp(self):
        super().setUp()
        make_product(sku="GATO-01", name="Gato Pompom", category=self.cats)

    def test_htmx_request_returns_only_the_results(self):
        response = self.client.get(SHOP, **{"HTTP_HX_REQUEST": "true"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/_shop_results.html")
        self.assertNotContains(response, "<html")
        self.assertContains(response, "Gato Pompom")

    def test_normal_request_returns_the_full_page(self):
        response = self.client.get(SHOP)

        self.assertContains(response, "<html")
        self.assertTemplateUsed(response, "catalog/shop.html")

    def test_partial_response_brings_the_category_sidebar_along(self):
        """A barra fica fora do alvo da troca: sem OOB, o filtro ativo congela."""
        response = self.client.get(
            SHOP, {"categoria": "decoracao"}, **{"HTTP_HX_REQUEST": "true"}
        )

        self.assertContains(response, 'id="shop-categories"')
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, "shop-filter-active")

    def test_the_sidebar_is_not_duplicated_on_a_normal_request(self):
        response = self.client.get(SHOP)

        self.assertEqual(response.content.decode().count('id="shop-categories"'), 1)
        self.assertNotContains(response, 'hx-swap-oob="true"')


class ShopLayoutTests(ShopBase):
    """A anatomia do catálogo — a da direção visual (`Catalogo.html`).

    Trilha, banner, lateral com categorias e materiais, título com a contagem,
    filtros ativos em pílulas. E, no celular, a lateral vira gaveta: um
    `popover` que o botão "Filtros" abre — sem JavaScript.
    """

    def setUp(self):
        super().setUp()
        make_product(
            sku="GATO-01", name="Gato Pompom", category=self.cats,
            price=Decimal("8.90"), stock_quantity=5,
        )

    def test_the_filters_button_opens_the_sidebar_as_a_popover(self):
        html = self.client.get(SHOP).content.decode()

        self.assertIn('<aside id="shop-categories" class="catalog-aside" popover', html)
        self.assertIn('popovertarget="shop-categories"', html)
        # Fechar: o ✕ do cabeçalho da gaveta e o "Ver N produtos".
        self.assertIn('popovertargetaction="hide"', html)
        self.assertIn("Ver 1 produto", html)

    def test_the_title_is_the_category_in_focus_with_the_count(self):
        html = self.client.get(SHOP, {"categoria": "gatos"}).content.decode()
        titulo = html.split('<h1 class="catalog-title">', 1)[1].split("</h1>", 1)[0]

        self.assertIn("Gatos", titulo)
        self.assertIn("1 item", titulo)
        self.assertNotIn("Modelos", titulo)

    def test_without_a_filter_the_title_is_the_shop_front(self):
        html = self.client.get(SHOP).content.decode()
        titulo = html.split('<h1 class="catalog-title">', 1)[1].split("</h1>", 1)[0]

        self.assertIn("Modelos", titulo)

    def test_the_banner_invites_to_another_shop_front(self):
        """"Ver filamentos →": a primeira raiz que não é esta, nunca esta."""
        response = self.client.get(SHOP)

        self.assertEqual(response.context["banner_link"], self.filaments)
        self.assertContains(response, "catalog-banner")
        self.assertContains(response, self.filaments.get_absolute_url())
        self.assertContains(response, "Ver Filamentos")

    def test_the_banner_of_a_nested_category_still_points_elsewhere(self):
        response = self.client.get(SHOP, {"categoria": "gatos"})

        self.assertEqual(response.context["banner_link"], self.filaments)

    def test_the_banner_text_is_the_category_description(self):
        """Nada de promoção escrita à mão: o texto é o da categoria, do Admin."""
        from apps.categories.models import CategoryTranslation

        CategoryTranslation.objects.filter(master=self.models, language="pt").update(
            description="Peças impressas com carinho."
        )

        self.assertContains(self.client.get(SHOP), "Peças impressas com carinho.")

    def test_the_search_has_no_banner(self):
        self.assertNotContains(self.client.get("/buscar/", {"q": "gato"}), "catalog-banner")

    def test_active_filters_become_pills_and_a_clear_button(self):
        response = self.client.get(SHOP, {"categoria": "gatos"})

        self.assertContains(response, 'class="catalog-pill"')
        self.assertContains(response, "Limpar filtros")

    def test_without_filters_there_is_nothing_to_clear(self):
        response = self.client.get(SHOP)

        self.assertNotContains(response, "catalog-pill")
        self.assertNotContains(response, "Limpar filtros")

    def test_the_sidebar_indents_by_depth_without_inline_measures(self):
        """A indentação é `--depth`, que o CSS transforma em medida."""
        html = self.client.get(SHOP).content.decode()

        self.assertIn('style="--depth: 1"', html)  # Gatos, dentro de Animais
        self.assertNotIn("padding-inline-start:", html)

    def test_the_drawer_is_translated(self):
        for prefixo, filtros, fechar in (
            ("/fr", "Filtres", "Fermer les filtres"),
            ("/nl", "Filters", "Filters sluiten"),
            ("/en", "Filters", "Close filters"),
        ):
            with self.subTest(idioma=prefixo):
                response = self.client.get(f"{prefixo}/modelos/")

                self.assertContains(response, filtros)
                self.assertContains(response, fechar)


class ShopQueryTests(ShopBase):
    def test_query_count_does_not_grow_with_the_catalogue(self):
        for index in range(4):
            make_product(sku=f"P-{index}", name=f"Produto {index}", category=self.cats)

        baseline = self.count_queries()

        for index in range(4, 12):
            make_product(sku=f"P-{index}", name=f"Produto {index}", category=self.cats)

        with self.assertNumQueries(baseline):
            self.client.get(SHOP)

    def count_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.client.get(SHOP)
        return len(captured)


class CategoryRoutingTests(ShopBase):
    """Categorias de Modelos levam à vitrine; as outras, à página delas."""

    def test_models_category_url_points_to_the_shop(self):
        self.assertEqual(self.models.get_absolute_url(), SHOP)

    def test_subcategory_url_points_to_the_filtered_shop(self):
        self.assertEqual(self.cats.get_absolute_url(), f"{SHOP}?categoria=gatos")

    def test_other_root_has_its_own_page(self):
        """A URL não mudou; o que mudou é que agora ela mostra produtos."""
        self.assertEqual(self.filaments.get_absolute_url(), "/categorias/filamentos/")

    def test_old_category_url_redirects_to_the_shop(self):
        response = self.client.get("/categorias/gatos/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, f"{SHOP}?categoria=gatos")

    def test_other_trees_now_get_a_real_shop(self):
        """Era uma página "em construção" com produtos cadastrados atrás dela."""
        response = self.client.get("/categorias/filamentos/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/shop.html")
        self.assertNotContains(response, "Em construção")
        self.assertNotContains(response, "próxima etapa da loja")

    def test_old_product_list_url_redirects_to_the_shop(self):
        response = self.client.get("/produtos/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, SHOP)
