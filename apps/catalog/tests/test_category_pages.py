"""Páginas de categoria e filtro por material — etapa 15.

Filamentos e Acessórios tinham produtos cadastrados e mostravam um aviso de
"em construção": eram raízes sem vitrine, e só a árvore de Modelos tinha uma.
A vitrine nunca dependeu de qual árvore era — só da raiz que recebe. Agora ela
é de quem a pedir, e a página de categoria é a vitrine recortada nela.

Categorias **dentro** da árvore que já tem rota própria continuam
redirecionando para lá: duas URLs mostrando a mesma grade seriam duas páginas
para o buscador indexar e uma para o cliente entender.
"""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Material, ProductStatus
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    make_variant,
    translate_category,
    translate_product,
)


class CategoryPageBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.pla = Material.objects.create(name="PLA")
        self.petg = Material.objects.create(name="PETG")

        # A árvore com vitrine própria.
        self.modelos = make_category(slug="modelos", name="Modelos")
        self.animais = make_category(slug="animais", name="Animais", parent=self.modelos)

        # As raízes que caíam na página provisória.
        self.filamentos = make_category(slug="filamentos", name="Filamentos")
        self.acessorios = make_category(slug="acessorios", name="Acessórios")
        self.vazia = make_category(slug="vazia", name="Categoria Vazia")

        self.gato = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.animais,
            price=Decimal("8.90"), stock_quantity=5, material=self.pla,
        )
        self.rolo_pla = make_product(
            sku="ROLO-PLA", name="Rolo PLA 1 kg", category=self.filamentos,
            price=Decimal("22.00"), stock_quantity=9, material=self.pla,
        )
        self.rolo_petg = make_product(
            sku="ROLO-PETG", name="Rolo PETG 1 kg", category=self.filamentos,
            price=Decimal("27.00"), stock_quantity=4, material=self.petg,
        )
        self.espatula = make_product(
            sku="ESP-01", name="Espátula", category=self.acessorios,
            price=Decimal("6.50"), stock_quantity=12, material=self.pla,
        )

    def pagina(self, slug, idioma="", **params):
        return self.client.get(f"{idioma}/categorias/{slug}/", params)

    def skus(self, resposta):
        return sorted(product.sku for product in resposta.context["products"])


class CategoryPageTests(CategoryPageBase):
    """2.1 e 2.2 — a página real."""

    def test_the_placeholder_is_gone(self):
        resposta = self.pagina("filamentos")

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "Em construção")
        self.assertNotContains(resposta, "próxima etapa da loja")

    def test_filaments_shows_its_products(self):
        self.assertEqual(self.skus(self.pagina("filamentos")), ["ROLO-PETG", "ROLO-PLA"])

    def test_accessories_shows_its_products(self):
        self.assertEqual(self.skus(self.pagina("acessorios")), ["ESP-01"])

    def test_it_does_not_show_products_of_another_category(self):
        self.assertNotIn("GATO-01", self.skus(self.pagina("filamentos")))

    def test_it_reuses_the_shop_template_and_card(self):
        """Uma segunda grade seria um segundo lugar para corrigir."""
        resposta = self.pagina("filamentos")

        self.assertTemplateUsed(resposta, "catalog/shop.html")
        self.assertTemplateUsed(resposta, "catalog/_shop_results.html")
        self.assertTemplateUsed(resposta, "components/product_card.html")

    def test_the_page_is_titled_with_the_category(self):
        resposta = self.pagina("filamentos")

        self.assertEqual(resposta.context["page_title"], "Filamentos")
        self.assertContains(resposta, "Filamentos")

    def test_it_counts_the_results(self):
        self.assertEqual(self.pagina("filamentos").context["result_count"], 2)

    def test_a_subcategory_shows_up_in_the_sidebar(self):
        cabos = make_category(slug="cabos", name="Cabos", parent=self.acessorios)
        make_product(
            sku="CABO-01", name="Cabo USB", category=cabos,
            price=Decimal("3.00"), stock_quantity=2,
        )

        resposta = self.pagina("acessorios")

        slugs = [node["slug"] for node in resposta.context["sidebar_nodes"]]
        self.assertIn("cabos", slugs)
        self.assertEqual(self.skus(resposta), ["CABO-01", "ESP-01"])

    def test_the_breadcrumb_climbs_to_the_root(self):
        cabos = make_category(slug="cabos", name="Cabos", parent=self.acessorios)

        resposta = self.pagina("cabos")

        nomes = [category.name for category in resposta.context["breadcrumb"]]
        self.assertEqual(nomes, ["Acessórios"])


class CategoryScopeTests(CategoryPageBase):
    """2.2 — a mesma regra de publicação da loja, não uma nova."""

    def test_a_draft_product_does_not_show_up(self):
        self.rolo_petg.status = ProductStatus.DRAFT
        self.rolo_petg.save()

        self.assertEqual(self.skus(self.pagina("filamentos")), ["ROLO-PLA"])

    def test_an_inactive_product_does_not_show_up(self):
        self.rolo_petg.status = ProductStatus.INACTIVE
        self.rolo_petg.save()

        self.assertEqual(self.skus(self.pagina("filamentos")), ["ROLO-PLA"])

    def test_a_product_without_an_active_variant_does_not_show_up(self):
        self.rolo_petg.variants.update(is_active=False)

        self.assertEqual(self.skus(self.pagina("filamentos")), ["ROLO-PLA"])

    def test_it_matches_sellable(self):
        from apps.catalog.models import Product

        vendaveis = {
            p.sku for p in Product.objects.sellable().filter(category=self.filamentos)
        }
        self.assertEqual(set(self.skus(self.pagina("filamentos"))), vendaveis)


class EmptyCategoryTests(CategoryPageBase):
    """2.3 — categoria sem produto não é erro."""

    def test_an_empty_category_answers_200(self):
        resposta = self.pagina("vazia")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.skus(resposta), [])

    def test_it_says_so_kindly(self):
        self.assertContains(self.pagina("vazia"), "Nenhum produto disponível nesta categoria")

    def test_a_category_that_became_empty_still_answers(self):
        self.espatula.status = ProductStatus.DRAFT
        self.espatula.save()

        resposta = self.pagina("acessorios")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.skus(resposta), [])


class MissingCategoryTests(CategoryPageBase):
    """2.4 — categoria inexistente é 404, com a página 404 da loja."""

    def test_an_unknown_slug_is_404(self):
        self.assertEqual(self.pagina("nao-existe").status_code, 404)

    def test_an_inactive_category_is_404(self):
        self.filamentos.is_active = False
        self.filamentos.save()

        self.assertEqual(self.pagina("filamentos").status_code, 404)

    def test_it_uses_the_shared_404_page(self):
        """Sem página de erro própria para categoria — a da loja já existe."""
        resposta = self.pagina("nao-existe")

        self.assertTemplateUsed(resposta, "404.html")


class CategoryRedirectTests(CategoryPageBase):
    """4 — as URLs de antes continuam funcionando."""

    def test_a_models_subcategory_still_redirects_to_the_shop(self):
        resposta = self.pagina("animais")

        self.assertEqual(resposta.status_code, 301)
        self.assertEqual(resposta.url, "/modelos/?categoria=animais")

    def test_the_models_root_still_redirects_to_the_shop(self):
        resposta = self.pagina("modelos")

        self.assertEqual(resposta.status_code, 301)
        self.assertEqual(resposta.url, "/modelos/")

    def test_the_category_url_did_not_change(self):
        self.assertEqual(self.filamentos.get_absolute_url(), "/categorias/filamentos/")


class CategoryLanguageTests(CategoryPageBase):
    """2 e 5 — a categoria no idioma do cliente."""

    def test_the_name_is_translated(self):
        translate_category(self.filamentos, "fr", "Filaments")

        resposta = self.pagina("filamentos", "/fr")

        self.assertEqual(resposta.context["page_title"], "Filaments")
        self.assertContains(resposta, "Filaments")

    def test_the_products_are_translated(self):
        translate_product(self.rolo_pla, "fr", name="Bobine PLA 1 kg")

        self.assertContains(self.pagina("filamentos", "/fr"), "Bobine PLA 1 kg")

    def test_a_missing_translation_falls_back_to_portuguese(self):
        self.assertContains(self.pagina("filamentos", "/fr"), "Rolo PETG 1 kg")


class MaterialFilterTests(CategoryPageBase):
    """3 — filtro por material."""

    def test_filtering_by_material(self):
        self.assertEqual(
            self.skus(self.pagina("filamentos", material="pla")), ["ROLO-PLA"]
        )

    def test_category_and_material_together(self):
        """Categoria: Filamentos + Material: PETG."""
        resposta = self.pagina("filamentos", material="petg")

        self.assertEqual(self.skus(resposta), ["ROLO-PETG"])
        self.assertEqual(resposta.context["selected_material"], self.petg)

    def test_a_material_from_another_category_gives_nothing(self):
        self.assertEqual(self.skus(self.pagina("acessorios", material="petg")), [])

    def test_an_unknown_material_is_ignored_not_a_404(self):
        """URL antiga com um material apagado mostra a vitrine, não um erro."""
        resposta = self.pagina("filamentos", material="inexistente")

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.context["selected_material"])
        self.assertEqual(self.skus(resposta), ["ROLO-PETG", "ROLO-PLA"])

    def test_an_inactive_variant_of_that_material_does_not_qualify(self):
        """A variante tem que ser a MESMA: ativa **e** daquele material."""
        morta = make_variant(
            self.espatula, sku="ESP-01-PETG", price=Decimal("7.00"), material=self.petg
        )
        morta.is_active = False
        morta.save()

        self.assertEqual(self.skus(self.pagina("acessorios", material="petg")), [])

    def test_the_filter_lists_only_materials_that_have_products(self):
        """Um filtro que leva a zero resultado é um beco sem saída."""
        madeira = Material.objects.create(name="Madeira")

        nodes = self.pagina("filamentos").context["material_nodes"]
        slugs = [node["slug"] for node in nodes]

        self.assertIn(self.pla.slug, slugs)
        self.assertIn(self.petg.slug, slugs)
        self.assertNotIn(madeira.slug, slugs)

    def test_the_list_keeps_the_other_materials_after_choosing_one(self):
        """Senão, escolher PLA deixaria a lista com uma linha e sem volta."""
        nodes = self.pagina("filamentos", material="pla").context["material_nodes"]
        slugs = [node["slug"] for node in nodes]

        self.assertIn(self.petg.slug, slugs)
        self.assertTrue(any(node["is_selected"] for node in nodes))

    def test_the_counts_are_per_material(self):
        nodes = {node["slug"]: node["count"] for node in self.pagina("filamentos").context["material_nodes"]}

        self.assertEqual(nodes[self.pla.slug], 1)
        self.assertEqual(nodes[self.petg.slug], 1)

    def test_the_material_name_is_translated(self):
        from apps.catalog.models import MaterialTranslation

        MaterialTranslation.objects.create(master=self.petg, language="fr", name="PETG (FR)")

        nodes = self.pagina("filamentos", "/fr").context["material_nodes"]
        nomes = [node["name"] for node in nodes]

        self.assertIn("PETG (FR)", nomes)

    def test_the_filter_also_works_on_the_shop(self):
        """Um filtro só, nas três telas — não três implementações."""
        resposta = self.client.get("/modelos/", {"material": "pla"})

        self.assertEqual(sorted(p.sku for p in resposta.context["products"]), ["GATO-01"])

    def test_the_filter_also_works_on_the_search(self):
        resposta = self.client.get("/buscar/", {"q": "rolo", "material": "petg"})

        self.assertEqual(sorted(p.sku for p in resposta.context["products"]), ["ROLO-PETG"])

    def test_a_single_material_is_not_offered_as_a_filter(self):
        """Achado no navegador: a lateral desenhava um cartao branco vazio.

        A view dizia "ha 1 material" e o template dizia "so mostro com 2 ou
        mais" -- duas condicoes decidindo a mesma coisa, e a que sobrava era o
        cartao sem conteudo. Agora quem decide e `available_materials()`.
        """
        resposta = self.pagina("acessorios")

        self.assertEqual(resposta.context["material_nodes"], [])
        self.assertNotContains(resposta, "titulo-materiais-shop")

    def test_two_materials_are_offered(self):
        resposta = self.pagina("filamentos")

        self.assertEqual(len(resposta.context["material_nodes"]), 2)
        self.assertContains(resposta, "titulo-materiais-shop")

    def test_the_search_page_has_no_empty_material_card(self):
        resposta = self.client.get("/buscar/", {"q": "espatula"})

        self.assertEqual(resposta.context["material_nodes"], [])
        self.assertNotContains(resposta, "titulo-materiais-shop")

    def test_choosing_a_material_keeps_the_category(self):
        html = self.pagina("filamentos", material="pla").content.decode()

        self.assertIn('<input type="hidden" name="material" value="pla">', html)
