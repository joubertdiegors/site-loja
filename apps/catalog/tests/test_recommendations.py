"""«Você também pode gostar» — §3 da etapa 13.

A regra é simples e determinística de propósito: mesma categoria, depois
destaques, depois os mais recentes; sem repetir, sem o produto da página, e só
o que está vendável. Recomendação de verdade — quem viu isto viu aquilo —
depende de dados de navegação que a loja ainda não coleta.

O card é o do Shop, incluído como está. Um card próprio aqui seria um segundo
lugar para corrigir quando o card mudar.
"""

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import ProductStatus
from apps.catalog.views import ProductDetailView
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    translate_product,
)


class RecommendationBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.animais = make_category(slug="animais", name="Animais")
        self.utilidades = make_category(slug="utilidades", name="Utilidades")
        self.product = make_product(
            sku="GATO-01",
            name="Gato Pompom",
            category=self.animais,
            price=Decimal("8.90"),
            stock_quantity=8,
        )

    def outro(self, sku, name, category=None, **kwargs):
        return make_product(
            sku=sku,
            name=name,
            category=category or self.animais,
            price=Decimal("12.00"),
            stock_quantity=5,
            **kwargs,
        )

    def get(self, product=None):
        return self.client.get((product or self.product).get_absolute_url())

    def suggested(self, response):
        return list(response.context["recommended"])


class RecommendationRuleTests(RecommendationBase):
    def test_valid_products_show_up(self):
        cao = self.outro("CAO-01", "Cão Bola")

        self.assertIn(cao, self.suggested(self.get()))

    def test_the_current_product_is_never_suggested(self):
        """Sugerir a página em que se está é ruído puro."""
        self.outro("CAO-01", "Cão Bola")

        self.assertNotIn(self.product, self.suggested(self.get()))

    def test_a_draft_product_is_not_suggested(self):
        rascunho = self.outro("CAO-02", "Cão Rascunho", status=ProductStatus.DRAFT)

        self.assertNotIn(rascunho, self.suggested(self.get()))

    def test_an_inactive_product_is_not_suggested(self):
        inativo = self.outro("CAO-03", "Cão Fora", status=ProductStatus.INACTIVE)

        self.assertNotIn(inativo, self.suggested(self.get()))

    def test_a_product_without_an_active_variant_is_not_suggested(self):
        """Sem variante não há preço, peso nem estoque — o card sairia vazio."""
        sem_variante = make_product(
            sku="CAO-04", name="Cão Sem Variante", category=self.animais,
            with_variant=False,
        )

        self.assertNotIn(sem_variante, self.suggested(self.get()))

    def test_the_maximum_is_respected(self):
        for numero in range(9):
            self.outro(f"CAO-1{numero}", f"Cão {numero}")

        self.assertLessEqual(
            len(self.suggested(self.get())), ProductDetailView.RECOMMENDED_LIMIT
        )

    def test_the_same_category_comes_first(self):
        """É a relação que o catálogo já tem, e a que o cliente entende."""
        for numero in range(ProductDetailView.RECOMMENDED_LIMIT):
            self.outro(f"CAO-2{numero}", f"Cão {numero}")
        de_fora = self.outro("COPO-01", "Copo", category=self.utilidades)

        sugeridos = self.suggested(self.get())

        self.assertNotIn(de_fora, sugeridos)
        for sugerido in sugeridos:
            with self.subTest(sku=sugerido.sku):
                self.assertEqual(sugerido.category_id, self.animais.pk)

    def test_featured_products_fill_the_gap_before_the_rest(self):
        destaque = self.outro(
            "COPO-02", "Copo Destaque", category=self.utilidades,
            is_featured=True, featured_order=1,
        )
        comum = self.outro("COPO-03", "Copo Comum", category=self.utilidades)

        sugeridos = self.suggested(self.get())

        self.assertLess(sugeridos.index(destaque), sugeridos.index(comum))

    def test_nobody_is_suggested_twice(self):
        """Um destaque da mesma categoria entra nas três passadas."""
        destaque = self.outro("CAO-30", "Cão Destaque", is_featured=True)

        sugeridos = self.suggested(self.get())

        self.assertEqual(sugeridos.count(destaque), 1)

    def test_the_section_is_empty_when_the_catalog_has_nothing_else(self):
        """Sem candidato, a seção não aparece — melhor que uma grade vazia."""
        resposta = self.get()

        self.assertEqual(self.suggested(resposta), [])
        self.assertNotContains(resposta, "recomendados-titulo")

    def test_a_product_without_a_category_still_gets_suggestions(self):
        """A primeira passada é pulada; as outras duas seguram a seção."""
        sem_categoria = make_product(
            sku="SOLTO-01", name="Solto", category=None, price=Decimal("5.00"),
        )
        cao = self.outro("CAO-40", "Cão Bola")

        self.assertIn(cao, self.suggested(self.get(sem_categoria)))


class RecommendationRenderTests(RecommendationBase):
    def test_the_section_reuses_the_shop_card(self):
        """Sem reuso, o card teria dois lugares para corrigir."""
        self.outro("CAO-01", "Cão Bola")

        resposta = self.get()

        self.assertTemplateUsed(resposta, "components/product_card.html")
        self.assertContains(resposta, "recomendados-titulo")
        self.assertContains(resposta, "Cão Bola")

    def test_the_heading_is_translated(self):
        self.outro("CAO-01", "Cão Bola")
        cao = self.outro("CAO-02", "Cão Dois")
        translate_product(cao, "fr", name="Chien Deux")

        resposta = self.client.get(f"/fr{self.product.get_absolute_url()}")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Chien Deux")
        self.assertNotContains(resposta, "Você também pode gostar")

    def test_the_card_shows_a_price(self):
        """O card sem preço não convida ninguém a clicar."""
        self.outro("CAO-01", "Cão Bola")

        self.assertContains(self.get(), "12,00")
