"""Testes da página inicial: renderização, idiomas, imagens e consultas."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import MediaType, ProductMedia
from apps.home.models import CtaTarget, HomeSectionLayout, HomeSectionType
from apps.core.testing import (
    add_products,
    make_banner,
    make_category,
    make_product,
    make_section,
    translate_category,
    translate_product,
    translate_section,
)

HOME_PT = "/"
HOME_FR = "/fr/"
HOME_EN = "/en/"


class HomeRenderingTests(TestCase):
    def test_home_responds_200(self):
        response = self.client.get(HOME_PT)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "home/index.html")

    def test_home_url_is_the_site_root(self):
        self.assertEqual(reverse("home:index"), HOME_PT)

    def test_empty_home_still_renders(self):
        response = self.client.get(HOME_PT)
        self.assertContains(response, "A vitrine está sendo montada")

    def test_seo_basics_are_present(self):
        response = self.client.get(HOME_PT)
        self.assertContains(response, "<title>")
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'lang="pt-br"')
        self.assertContains(response, "<h1")
        self.assertContains(response, "<main")


class SectionVisibilityTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="P1", name="Gato Pompom", is_featured=True)

    def test_active_section_appears(self):
        make_section(internal_name="Destaques", title="Destaques de Modelos")
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Destaques de Modelos")
        self.assertContains(response, "Gato Pompom")

    def test_inactive_section_does_not_appear(self):
        make_section(internal_name="Natal", title="Promoção de Natal", is_active=False)
        response = self.client.get(HOME_PT)
        self.assertNotContains(response, "Promoção de Natal")

    def test_empty_section_does_not_appear(self):
        make_section(
            internal_name="Manual vazia",
            title="Seção sem produtos",
            section_type=HomeSectionType.MANUAL_PRODUCTS,
        )
        response = self.client.get(HOME_PT)
        self.assertNotContains(response, "Seção sem produtos")

    def test_best_sellers_section_does_not_appear(self):
        make_section(
            internal_name="Mais vendidos",
            title="Os mais vendidos",
            section_type=HomeSectionType.BEST_SELLERS,
        )
        response = self.client.get(HOME_PT)
        self.assertNotContains(response, "Os mais vendidos")

    def test_sections_appear_in_the_configured_order(self):
        make_section(internal_name="B", title="Segunda faixa", sort_order=2)
        make_section(internal_name="A", title="Primeira faixa", sort_order=1)

        content = self.client.get(HOME_PT).content.decode()
        self.assertLess(content.index("Primeira faixa"), content.index("Segunda faixa"))

    def test_reordering_changes_the_home(self):
        first = make_section(internal_name="A", title="Primeira faixa", sort_order=1)
        make_section(internal_name="B", title="Segunda faixa", sort_order=2)

        first.sort_order = 9
        first.save()

        content = self.client.get(HOME_PT).content.decode()
        self.assertLess(content.index("Segunda faixa"), content.index("Primeira faixa"))

    def test_product_limit_is_respected_in_the_page(self):
        for index in range(6):
            make_product(sku=f"X{index}", name=f"Produto {index}", is_featured=True)
        make_section(internal_name="Destaques", title="Destaques", product_limit=3)

        content = self.client.get(HOME_PT).content.decode()
        shown = [f"Produto {index}" for index in range(6) if f"Produto {index}" in content]
        self.assertEqual(len(shown), 3)

    def test_carousel_layout_renders_the_track(self):
        make_section(internal_name="Novidades", title="Novidades", layout=HomeSectionLayout.CAROUSEL)
        response = self.client.get(HOME_PT)
        self.assertContains(response, "carousel-track")

    def test_grid_layout_renders_the_grid(self):
        make_section(internal_name="Destaques", title="Destaques", layout=HomeSectionLayout.GRID)
        response = self.client.get(HOME_PT)
        self.assertContains(response, "product-grid")


class ProductCardTests(TestCase):
    def setUp(self):
        self.category = make_category(slug="animais", name="Animais")
        self.product = make_product(
            sku="GATO-01",
            name="Gato Pompom",
            category=self.category,
            price=Decimal("8.90"),
            is_featured=True,
            stock_quantity=3,
        )
        self.variant = self.product.default_variant
        make_section(internal_name="Destaques", title="Destaques")

    def test_card_shows_name_price_and_link(self):
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, "8,90")
        self.assertContains(response, self.product.get_absolute_url())

    def test_card_shows_the_category(self):
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Animais")

    def test_made_to_order_badge(self):
        self.variant.made_to_order = True
        self.variant.production_lead_time_days = 5
        self.variant.save()
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Sob encomenda")

    def test_out_of_stock_badge(self):
        self.product.is_featured = True
        self.product.save()
        self.variant.stock_quantity = 0
        self.variant.save()
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Esgotado")

    def test_missing_image_does_not_break_the_home(self):
        response = self.client.get(HOME_PT)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Foto em breve")
        self.assertNotContains(response, "<img src=\"\"")


@override_settings(MEDIA_ROOT="/tmp/jdprint-home-tests")
class ProductImageTests(TestCase):
    def setUp(self):
        self.product = make_product(sku="GATO-01", name="Gato Pompom", is_featured=True)
        make_section(internal_name="Destaques", title="Destaques")

    def upload(self, filename):
        return SimpleUploadedFile(filename, b"conteudo")

    def test_primary_image_is_used(self):
        ProductMedia.objects.create(product=self.product, file=self.upload("primeira.jpg"))
        principal = ProductMedia.objects.create(
            product=self.product, file=self.upload("principal.jpg"), is_primary=True
        )

        response = self.client.get(HOME_PT)
        self.assertContains(response, principal.file.url)

    def test_falls_back_to_the_first_image_without_a_primary(self):
        media = ProductMedia.objects.create(product=self.product, file=self.upload("unica.jpg"))
        ProductMedia.objects.filter(pk=media.pk).update(is_primary=False)

        response = self.client.get(HOME_PT)
        self.assertContains(response, media.file.url)

    def test_video_is_not_used_as_a_card_cover(self):
        video = ProductMedia.objects.create(
            product=self.product, file=self.upload("filme.mp4"), media_type=MediaType.VIDEO
        )
        response = self.client.get(HOME_PT)
        self.assertNotContains(response, video.file.url)
        self.assertContains(response, "Foto em breve")

    def test_alt_text_is_used(self):
        ProductMedia.objects.create(
            product=self.product, file=self.upload("foto.jpg"), alt_text="Gato branco de pompom"
        )
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Gato branco de pompom")


class LanguageTests(TestCase):
    def setUp(self):
        self.category = make_category(slug="modelos", name="Modelos")
        translate_category(self.category, "fr", "Modèles")

        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category, is_featured=True
        )
        translate_product(self.product, "fr", "Chat Pompon")
        translate_product(self.product, "en", "Pompom Cat")

        self.section = make_section(internal_name="Destaques", title="Destaques de Modelos")
        translate_section(self.section, "fr", "Modèles à la une")
        translate_section(self.section, "en", "Featured Models")

    def test_portuguese_is_served_at_the_root(self):
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Destaques de Modelos")
        self.assertContains(response, "Gato Pompom")
        self.assertContains(response, 'lang="pt-br"')

    def test_french_content(self):
        response = self.client.get(HOME_FR)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modèles à la une")
        self.assertContains(response, "Chat Pompon")
        self.assertContains(response, 'lang="fr"')

    def test_english_content(self):
        response = self.client.get(HOME_EN)
        self.assertContains(response, "Featured Models")
        self.assertContains(response, "Pompom Cat")
        self.assertContains(response, 'lang="en"')

    def test_untranslated_language_falls_back_to_portuguese(self):
        # Holandês está disponível na loja, mas sem tradução deste conteúdo.
        response = self.client.get("/nl/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Destaques de Modelos")
        self.assertContains(response, "Gato Pompom")

    def test_interface_strings_are_translated(self):
        """A âncora era "Categorias", que saiu da Home junto com a faixa.

        Trocada por outra cadeia de interface da própria página — o teste
        continua guardando a mesma coisa: a Home sai traduzida.
        """
        self.assertContains(self.client.get(HOME_FR), "Rechercher des produits")
        self.assertContains(self.client.get(HOME_EN), "Search products")

    def test_language_selector_lists_the_available_languages(self):
        response = self.client.get(HOME_PT)
        for code in ("pt-br", "fr", "nl", "en"):
            self.assertContains(response, f'value="{code}"')

    def test_switching_language_redirects_and_sticks(self):
        response = self.client.post(
            reverse("set_language"), {"language": "fr", "next": HOME_PT}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modèles à la une")

    def test_arabic_is_not_offered_yet(self):
        # O árabe segue suportado pelo sistema, mas desligado na loja.
        response = self.client.get("/ar/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")


class BannerRenderingTests(TestCase):
    def test_placeholder_when_no_banner_exists(self):
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Espaço reservado para o banner")

    def test_banner_content_is_rendered(self):
        make_banner(internal_name="Boas-vindas", title="Peças sob medida")
        response = self.client.get(HOME_PT)
        self.assertContains(response, "Peças sob medida")
        self.assertNotContains(response, "Espaço reservado para o banner")

    def test_banner_cta(self):
        category = make_category(slug="modelos", name="Modelos")
        make_banner(
            internal_name="Boas-vindas",
            title="Peças sob medida",
            cta_target=CtaTarget.CATEGORY,
            cta_category=category,
        )
        response = self.client.get(HOME_PT)
        self.assertContains(response, category.get_absolute_url())


class CategorySectionTests(TestCase):
    def test_categories_come_from_the_database(self):
        models = make_category(slug="modelos", name="Modelos")
        make_product(sku="P1", name="Vaso", category=models)

        response = self.client.get(HOME_PT)
        self.assertContains(response, "Modelos")
        self.assertContains(response, models.get_absolute_url())

    def test_categories_without_products_are_not_in_the_category_block(self):
        # A categoria continua no menu do header (o administrador controla isso
        # pelo campo "ativa"), mas não vira card na faixa de categorias.
        make_category(slug="impressoras", name="Impressoras")
        response = self.client.get(HOME_PT)
        names = [card.name for card in response.context["category_cards"]]
        self.assertNotIn("Impressoras", names)


class NavigationTests(TestCase):
    def test_header_lists_root_categories(self):
        make_category(slug="modelos", name="Modelos")
        make_category(slug="filamentos", name="Filamentos")

        response = self.client.get(HOME_PT)
        self.assertContains(response, "Modelos")
        self.assertContains(response, "Filamentos")

    def test_inactive_categories_are_not_in_the_menu(self):
        make_category(slug="secreta", name="Categoria Secreta", is_active=False)
        response = self.client.get(HOME_PT)
        self.assertNotContains(response, "Categoria Secreta")


class PlaceholderPageTests(TestCase):
    def setUp(self):
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="GATO-01", name="Gato Pompom", category=self.category)

    def test_product_page_responds(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gato Pompom")

    def test_category_page_responds(self):
        response = self.client.get(self.category.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_unknown_product_returns_404(self):
        response = self.client.get("/produtos/nao-existe/")
        self.assertEqual(response.status_code, 404)

    def test_draft_product_is_not_public(self):
        from apps.catalog.models import ProductStatus

        self.product.status = ProductStatus.DRAFT
        self.product.save()
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 404)


class HomeQueryTests(TestCase):
    def test_home_uses_a_bounded_number_of_queries(self):
        category = make_category(slug="modelos", name="Modelos")
        products = [
            make_product(sku=f"P{index}", name=f"Produto {index}", category=category, is_featured=True)
            for index in range(8)
        ]
        manual = make_section(
            internal_name="Manual", title="Escolhidos", section_type=HomeSectionType.MANUAL_PRODUCTS
        )
        add_products(manual, products[:4])
        make_section(internal_name="Destaques", title="Destaques", sort_order=2)
        make_section(
            internal_name="Novidades",
            title="Novidades",
            section_type=HomeSectionType.NEWEST_PRODUCTS,
            sort_order=3,
        )

        # Orçamento fixo. Nenhuma consulta por produto: o número depende só da
        # quantidade de seções (cada uma traz produtos, traduções, mídia e
        # variantes com cor e material) mais os idiomas da loja. Se alguém
        # introduzir um N+1, este teste quebra.
        #
        # Caiu de 27 para 24 na etapa 8: as cores do card saem das variantes,
        # que já vinham no prefetch — as três consultas de ``Product.colors``
        # deixaram de existir.
        #
        # Subiu de 24 para 29 na etapa 16, e as cinco são o preço de o conteúdo
        # ser cadastrado em vez de escrito no HTML: faixa do topo, rodapé,
        # colunas do rodapé, cards e chamada final. Cinco consultas indexadas,
        # e **fixas** — `test_query_count_does_not_grow_with_more_content`
        # prova que cadastrar vinte itens não acrescenta nenhuma.
        #
        # E de 29 para 33 na etapa 18. O rodapé deixou de mostrar três textos
        # sem link e passou a mostrar uma coluna cadastrada que aponta para as
        # páginas da loja — o que custa os links, os rótulos deles e os títulos
        # das páginas. A página vem no mesmo SELECT dos links
        # (`select_related`), então são cinco consultas para o rodapé inteiro.
        #
        # E de 33 para 34 com as logos administráveis: uma consulta para ler a
        # linha de `BrandAssets`. **Uma** por requisição, e não uma por card —
        # a tag guarda a linha no `request`, e é isso que este orçamento prova:
        # sem esse cuidado, a Home com oito produtos custaria oito consultas a
        # mais só para descobrir a imagem padrão.
        #
        # E de 34 para 36 com os blocos da direção visual: uma consulta para a
        # lista de blocos de categoria e uma para o registro único de "Sobre a
        # loja". As duas são indexadas e **fixas** — cadastrar vinte blocos não
        # acrescenta nenhuma, e é o teste abaixo que prova isso.
        #
        # Fixas: `test_query_count_does_not_grow_with_more_content` continua
        # provando que cadastrar mais conteúdo não acrescenta nenhuma.
        #
        # E de 36 para 37 com o carrossel de banners: uma consulta para a linha
        # única de configuração (rotação, setas, indicadores). Os banners em si
        # já vinham numa consulta só — agora todos os ativos, não só o primeiro.
        with self.assertNumQueries(38):
            self.client.get(HOME_PT)

    def test_query_count_does_not_grow_with_more_content(self):
        """O conteúdo administrável não pode custar uma consulta por item.

        É o que separa "cinco consultas a mais" de um N+1 que só aparece
        quando a loja estiver cheia.
        """
        from apps.home.models import (
            HomeAbout,
            HomeAboutBadge,
            HomeAboutBadgeTranslation,
            HomeCard,
            HomeCardTranslation,
            HomeCategoryCard,
            HomeCategoryCardTranslation,
        )
        from apps.storefront.models import (
            FooterColumn,
            FooterColumnTranslation,
            FooterLink,
            FooterLinkTranslation,
            TopBarItem,
            TopBarItemTranslation,
        )

        category = make_category(slug="modelos", name="Modelos")
        for index in range(4):
            make_product(sku=f"P{index}", name=f"Produto {index}", category=category, is_featured=True)
        make_section(internal_name="Destaques", title="Destaques", product_limit=4)

        sobre = HomeAbout.load()

        def povoar(quantidade):
            for index in range(quantidade):
                item = TopBarItem.objects.create(internal_name=f"Topo {index}", sort_order=index)
                TopBarItemTranslation.objects.create(master=item, language="pt", text=f"T{index}")

                card = HomeCard.objects.create(internal_name=f"Card {index}", sort_order=index)
                HomeCardTranslation.objects.create(master=card, language="pt", title=f"C{index}")

                coluna = FooterColumn.objects.create(internal_name=f"Col {index}", sort_order=index)
                FooterColumnTranslation.objects.create(master=coluna, language="pt", title=f"Col {index}")
                link = FooterLink.objects.create(column=coluna, sort_order=index)
                FooterLinkTranslation.objects.create(master=link, language="pt", label=f"L{index}")

                # Os blocos da direção visual entram na mesma prova: eles
                # trazem categoria (`select_related`) e tradução (`prefetch`),
                # e é justamente esse par que um N+1 quebraria.
                bloco = HomeCategoryCard.objects.create(
                    internal_name=f"Bloco {index}", category=category, sort_order=index
                )
                HomeCategoryCardTranslation.objects.create(
                    master=bloco, language="pt", title=f"B{index}"
                )

                pilula = HomeAboutBadge.objects.create(
                    about=sobre, internal_name=f"Pill {index}", sort_order=index
                )
                HomeAboutBadgeTranslation.objects.create(
                    master=pilula, language="pt", text=f"P{index}"
                )

        povoar(2)
        base = self.count_queries()

        povoar(18)

        with self.assertNumQueries(base):
            self.client.get(HOME_PT)

    def test_query_count_is_the_same_with_many_more_products(self):
        category = make_category(slug="modelos", name="Modelos")
        for index in range(8):
            make_product(sku=f"P{index}", name=f"Produto {index}", category=category, is_featured=True)
        make_section(internal_name="Destaques", title="Destaques", product_limit=4)

        baseline = self.count_queries()

        for index in range(20):
            make_product(sku=f"X{index}", name=f"Extra {index}", category=category, is_featured=True)

        with self.assertNumQueries(baseline):
            self.client.get(HOME_PT)

    def count_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.client.get(HOME_PT)
        return len(captured)
