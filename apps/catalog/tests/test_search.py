"""Busca da loja — etapa 15.

A busca é a vitrine recortada por texto: mesma grade, mesmo card, mesma
paginação, mesma regra de "o que o cliente pode ver". O que estes testes
guardam é o recorte — o que entra, o que fica de fora e o que não pode
aparecer duas vezes.

Uma coisa merece ser dita em voz alta: **a busca olha o idioma do cliente e o
português**. Não é preguiça. Um produto sem tradução francesa aparece no card
com o nome em português; se a busca não olhasse o português, o cliente não
acharia exatamente aquilo que está lendo na tela.
"""

import io
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Material, ProductStatus
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    make_variant,
    translate_product,
)

BUSCA = "/buscar/"


class SearchBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.modelos = make_category(slug="modelos", name="Modelos")
        self.animais = make_category(slug="animais", name="Animais", parent=self.modelos)
        self.decoracao = make_category(slug="decoracao", name="Decoração", parent=self.modelos)

        self.pla = Material.objects.create(name="PLA")
        self.resina = Material.objects.create(name="Resina")

        self.gato = make_product(
            sku="GATO-01",
            name="Gato Pompom",
            category=self.animais,
            price=Decimal("8.90"),
            stock_quantity=5,
            material=self.pla,
        )
        self.gato.translations.filter(language="pt").update(
            short_description="Estatueta de gato para a estante.",
            description="Um gato peludo impresso em camadas finas.",
        )
        self.vaso = make_product(
            sku="VASO-77",
            name="Vaso Facetado",
            category=self.decoracao,
            price=Decimal("19.90"),
            stock_quantity=3,
            material=self.resina,
        )

    def buscar(self, termo, idioma=""):
        return self.client.get(f"{idioma}{BUSCA}", {"q": termo})

    def encontrados(self, resposta):
        return [product.sku for product in resposta.context["products"]]


class SearchFindsTests(SearchBase):
    """1.2 — o que a busca deve encontrar."""

    def test_by_name(self):
        self.assertEqual(self.encontrados(self.buscar("Pompom")), ["GATO-01"])

    def test_by_name_is_case_insensitive(self):
        self.assertEqual(self.encontrados(self.buscar("pompom")), ["GATO-01"])

    def test_by_short_description(self):
        self.assertEqual(self.encontrados(self.buscar("estante")), ["GATO-01"])

    def test_by_description(self):
        self.assertEqual(self.encontrados(self.buscar("camadas finas")), ["GATO-01"])

    def test_by_product_sku(self):
        self.assertEqual(self.encontrados(self.buscar("VASO-77")), ["VASO-77"])

    def test_by_variant_sku(self):
        make_variant(self.gato, sku="GATO-01-XL", price=Decimal("12.00"), stock=2)

        self.assertEqual(self.encontrados(self.buscar("GATO-01-XL")), ["GATO-01"])

    def test_by_category(self):
        self.assertEqual(self.encontrados(self.buscar("Decoração")), ["VASO-77"])

    def test_by_material(self):
        self.assertEqual(self.encontrados(self.buscar("Resina")), ["VASO-77"])

    def test_several_words_are_an_and(self):
        """"gato pompom" pede as duas palavras, não uma delas."""
        self.assertEqual(self.encontrados(self.buscar("gato pompom")), ["GATO-01"])
        self.assertEqual(self.encontrados(self.buscar("gato vaso")), [])

    def test_the_words_may_land_in_different_fields(self):
        """"gato PLA": uma no nome, outra no material da variante."""
        self.assertEqual(self.encontrados(self.buscar("gato PLA")), ["GATO-01"])

    def test_extra_spaces_do_not_break_anything(self):
        self.assertEqual(self.encontrados(self.buscar("   gato    pompom   ")), ["GATO-01"])

    def test_a_product_appears_only_once(self):
        """Cada JOIN pode repetir a linha; o cliente veria o mesmo card 3 vezes."""
        make_variant(self.gato, sku="GATO-01-A", price=Decimal("9.00"), material=self.pla)
        make_variant(self.gato, sku="GATO-01-B", price=Decimal("9.50"), material=self.pla)

        achados = self.encontrados(self.buscar("gato"))

        self.assertEqual(achados, ["GATO-01"])
        self.assertEqual(len(achados), len(set(achados)))


class SearchScopeTests(SearchBase):
    """1.6 — o que a busca NÃO pode mostrar."""

    def test_a_draft_product_is_not_found(self):
        self.vaso.status = ProductStatus.DRAFT
        self.vaso.save()

        self.assertEqual(self.encontrados(self.buscar("Vaso")), [])

    def test_an_inactive_product_is_not_found(self):
        self.vaso.status = ProductStatus.INACTIVE
        self.vaso.save()

        self.assertEqual(self.encontrados(self.buscar("Vaso")), [])

    def test_a_product_without_an_active_variant_is_not_found(self):
        """Sem variante não há preço nem estoque: o card sairia vazio."""
        self.vaso.variants.update(is_active=False)

        self.assertEqual(self.encontrados(self.buscar("Vaso")), [])

    def test_the_sku_of_an_inactive_variant_does_not_bring_it_back(self):
        morta = make_variant(self.vaso, sku="VASO-77-MORTA", price=Decimal("5.00"))
        morta.is_active = False
        morta.save()

        self.assertEqual(self.encontrados(self.buscar("VASO-77-MORTA")), [])

    def test_it_uses_the_same_rule_as_the_shop(self):
        """A regra de publicação é uma só — não uma cópia para a busca.

        Com um rascunho no meio: sem ele, "achados ⊆ vendáveis" passaria mesmo
        se a busca ignorasse a regra, porque tudo seria vendável.
        """
        from apps.catalog.models import Product

        rascunho = make_product(
            sku="GATO-99",
            name="Gato Rascunho",
            category=self.animais,
            price=Decimal("7.00"),
            status=ProductStatus.DRAFT,
        )

        vendaveis = {p.sku for p in Product.objects.sellable()}
        achados = set(
            self.encontrados(self.buscar("gato")) + self.encontrados(self.buscar("vaso"))
        )

        self.assertNotIn(rascunho.sku, vendaveis)
        self.assertTrue(achados)
        self.assertTrue(achados.issubset(vendaveis))
        self.assertNotIn(rascunho.sku, achados)

    def test_no_admin_field_leaks_into_the_page(self):
        html = self.buscar("gato").content.decode()

        for proibido in ("filament_cost", "profit_margin", "total_cost", "is_featured"):
            with self.subTest(campo=proibido):
                self.assertNotIn(proibido, html)


class SearchEmptyTests(SearchBase):
    """1.5 — nenhum resultado, e nenhuma busca."""

    def test_no_result_shows_the_term_back(self):
        resposta = self.buscar("xyzabc")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.encontrados(resposta), [])
        self.assertContains(resposta, "xyzabc")
        self.assertContains(resposta, "Não encontramos nada para")

    def test_no_result_still_offers_a_new_search(self):
        resposta = self.buscar("xyzabc")

        self.assertContains(resposta, 'name="q"')

    def test_an_empty_query_does_not_break(self):
        resposta = self.client.get(BUSCA, {"q": ""})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.encontrados(resposta), [])

    def test_no_query_at_all_does_not_break(self):
        resposta = self.client.get(BUSCA)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.encontrados(resposta), [])

    def test_only_spaces_is_the_same_as_empty(self):
        resposta = self.client.get(BUSCA, {"q": "     "})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.encontrados(resposta), [])

    def test_a_single_letter_does_not_scan_the_catalog(self):
        """Uma letra devolveria meio catálogo sem ajudar ninguém."""
        self.assertEqual(self.encontrados(self.buscar("a")), [])

    def test_a_very_long_query_is_capped(self):
        """Duzentas palavras seriam duzentos JOINs — e um jeito barato de doer."""
        from apps.catalog.views import MAX_SEARCH_TERMS

        resposta = self.buscar(" ".join(["gato"] * 50))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context["view"].terms), MAX_SEARCH_TERMS)


class SearchSafetyTests(SearchBase):
    """1.6 — caracteres estranhos não podem virar consulta nem HTML."""

    def test_sql_looking_input_is_just_text(self):
        resposta = self.buscar("'; DROP TABLE catalog_product; --")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.encontrados(resposta), [])
        from apps.catalog.models import Product

        self.assertEqual(Product.objects.count(), 2)

    def test_html_in_the_query_is_escaped(self):
        resposta = self.buscar("<script>alert(1)</script>")

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "<script>alert(1)</script>")

    def test_percent_and_underscore_are_not_wildcards(self):
        """`%` casaria com tudo se virasse LIKE cru."""
        self.assertEqual(self.encontrados(self.buscar("%%")), [])

    def test_accents_work(self):
        self.assertEqual(self.encontrados(self.buscar("Decoração")), ["VASO-77"])


class SearchLanguageTests(SearchBase):
    """Procurar em todos os idiomas; mostrar no idioma do cliente.

    ## O que mudou, e por quê

    Até aqui a busca olhava só o idioma da tela mais o português. O efeito era
    que o **mesmo produto existia ou não conforme a bandeirinha escolhida**:
    quem estava em neerlandês não achava "Dinosaure", quem estava em francês
    não achava "Dinosaurus". Numa loja belga isso é errado — o cliente lê o
    rótulo em francês, ouve falar do produto em neerlandês e vê o nome em
    inglês numa rede social.

    O teste que guardava o comportamento antigo
    (`test_a_dutch_only_word_is_not_offered_to_a_french_reader`) foi trocado
    pela matriz abaixo: ele afirmava que uma palavra holandesa **não** podia
    achar nada em francês, que é exatamente o que se pediu para inverter.

    Achar e mostrar continuam sendo coisas separadas: o card escreve o nome no
    idioma do cliente, com o fallback de sempre, sem saber por qual tradução o
    produto foi encontrado.
    """

    #: idioma do conteúdo -> (prefixo da URL, termo só daquela tradução)
    IDIOMAS = {
        "pt": ("", "Dinossauro"),
        "fr": ("/fr", "Dinosaure"),
        "nl": ("/nl", "Dinosaurus"),
        "en": ("/en", "Dinosaur"),
    }

    def setUp(self):
        super().setUp()
        self.rex = make_product(
            sku="REX-01",
            name="Dinossauro T-Rex",
            category=self.animais,
            price=Decimal("21.90"),
            stock_quantity=4,
        )
        translate_product(self.rex, "fr", name="Dinosaure T-Rex")
        translate_product(self.rex, "nl", name="T-Rex Dinosaurus")
        translate_product(self.rex, "en", name="T-Rex Dinosaur")

    def test_every_language_finds_every_translation(self):
        """As 16 combinações: 4 idiomas de loja × 4 idiomas de termo."""
        for loja, (prefixo, _termo) in self.IDIOMAS.items():
            for termo_de, (_p, termo) in self.IDIOMAS.items():
                with self.subTest(loja=loja, termo=termo_de, buscando=termo):
                    self.assertIn(
                        "REX-01",
                        self.encontrados(self.buscar(termo, prefixo)),
                        f"loja em {loja} não achou o termo em {termo_de}",
                    )

    def test_the_result_is_shown_in_the_shop_language(self):
        """Achar por uma tradução estrangeira não troca o idioma da tela."""
        resposta = self.buscar("Dinosaurus", "/fr")

        self.assertIn("REX-01", self.encontrados(resposta))
        self.assertContains(resposta, "Dinosaure T-Rex")
        self.assertNotContains(resposta, "T-Rex Dinosaurus")

    def test_a_product_without_that_translation_falls_back(self):
        """O card mostra "Vaso Facetado" em francês: achá-lo por ele tem que dar."""
        resposta = self.buscar("Facetado", "/fr")

        self.assertEqual(self.encontrados(resposta), ["VASO-77"])
        self.assertContains(resposta, "Vaso Facetado")

    def test_a_description_in_another_language_also_finds(self):
        translate_product(
            self.gato, "fr", name="Figurine de chat",
            short_description="Petite figurine pour l'étagère.",
        )

        self.assertEqual(self.encontrados(self.buscar("étagère")), ["GATO-01"])
        self.assertEqual(self.encontrados(self.buscar("étagère", "/nl")), ["GATO-01"])

    def test_a_product_matching_in_four_languages_appears_once(self):
        """"T-Rex" está nas quatro traduções: um JOIN por tradução que casa."""
        achados = self.encontrados(self.buscar("T-Rex", "/nl"))

        self.assertEqual(achados, ["REX-01"])
        self.assertEqual(len(achados), len(set(achados)))

    def test_it_does_not_run_one_query_per_language(self):
        """Quatro consultas por palavra seriam dezesseis numa busca de quatro."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as capturadas:
            self.buscar("Dinosaurus")

        de_produto = [
            q for q in capturadas.captured_queries
            if "catalog_producttranslation" in q["sql"] and "COUNT" not in q["sql"].upper()
        ]
        self.assertLessEqual(len(de_produto), 4)

    def test_two_words_may_come_from_two_different_languages(self):
        """"Dinosaure Dinosaurus": uma casa em FR, a outra em NL."""
        self.assertEqual(self.encontrados(self.buscar("Dinosaure Dinosaurus")), ["REX-01"])

    def test_the_page_answers_in_the_chosen_language(self):
        translate_product(self.gato, "fr", name="Figurine de chat")

        resposta = self.buscar("figurine", "/fr")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Figurine de chat")


class SearchUntranslatedFieldsTests(SearchBase):
    """Os campos que não têm idioma continuam como estavam."""

    def test_product_sku(self):
        self.assertEqual(self.encontrados(self.buscar("VASO-77", "/nl")), ["VASO-77"])

    def test_variant_sku(self):
        make_variant(self.gato, sku="GATO-01-XL", price=Decimal("12.00"), stock=2)

        self.assertEqual(self.encontrados(self.buscar("GATO-01-XL", "/en")), ["GATO-01"])

    def test_category(self):
        self.assertEqual(self.encontrados(self.buscar("Decoração", "/fr")), ["VASO-77"])

    def test_category_in_another_language(self):
        from apps.categories.models import CategoryTranslation

        CategoryTranslation.objects.create(
            master=self.decoracao, language="nl", name="Decoratie"
        )
        self.decoracao.refresh_translations()

        self.assertEqual(self.encontrados(self.buscar("Decoratie", "/fr")), ["VASO-77"])

    def test_material(self):
        self.assertEqual(self.encontrados(self.buscar("Resina", "/en")), ["VASO-77"])

    def test_material_in_another_language(self):
        from apps.catalog.models import MaterialTranslation

        MaterialTranslation.objects.create(
            master=self.resina, language="nl", name="Hars"
        )

        self.assertEqual(self.encontrados(self.buscar("Hars", "/fr")), ["VASO-77"])


class SearchLayoutTests(SearchBase):
    """A busca é o catálogo por outra porta — inclusive no tamanho do card.

    Dois defeitos já passaram por aqui, e os dois produziam card errado:

    1. (etapa 15b) a lateral vazia levava `hidden` — `display: none` tira o
       item da grade, sobrava um só, e os resultados caíam na coluna de 254 px:
       cards de 49 px;
    2. (esta etapa) a correção do primeiro foi manter a lateral vazia **dentro**
       da grade. Ela passou a reservar 254 px + o gap e a empurrar a barra e os
       cards 282 px para a direita, enquanto o título e o campo ficavam na
       borda esquerda. O bloco de resultados virava uma ilha no meio da página.

    A regra que ficou: a grade de duas colunas existe quando a lateral tem
    conteúdo. Sem conteúdo não há coluna nenhuma — os resultados ocupam a
    largura inteira do container, que é o mesmo do catálogo.

    A medição em pixels é feita no navegador; o que se guarda aqui é a
    estrutura que a produz.
    """

    def setUp(self):
        super().setUp()
        # Uma raiz fora de Modelos: `/categorias/decoracao/` responderia 301
        # para a vitrine, e a pagina de categoria de verdade nao seria medida.
        self.filamentos = make_category(slug="filamentos", name="Filamentos")
        make_product(
            sku="ROLO-PLA", name="Rolo PLA", category=self.filamentos,
            price=Decimal("22.00"), stock_quantity=9, material=self.pla,
        )

    def html(self, url, **params):
        # Chaves `HTTP_...` vão como cabeçalho da requisição, não como
        # parâmetro da URL — é assim que se simula o HTMX.
        cabecalhos = {k: params.pop(k) for k in list(params) if k.startswith("HTTP_")}
        resposta = self.client.get(url, params, **cabecalhos)
        self.assertEqual(resposta.status_code, 200, f"{url} respondeu {resposta.status_code}")
        return resposta.content.decode()

    def sem_material(self):
        """Um produto que nenhum material alcança.

        É o único cenário em que a busca não desenha a lateral: com material,
        mesmo um só, o filtro aparece (`SearchView.MIN_MATERIAL_OPTIONS = 1`).
        """
        return make_product(
            sku="SEM-MAT-01", name="Peça Anônima", category=self.filamentos,
            price=Decimal("5.00"), stock_quantity=2,
        )

    def test_the_catalogue_pages_use_the_shared_layout_utility(self):
        """Duas cópias das classes da grade foi por onde a busca divergiu.

        O catálogo sempre tem lateral (as categorias), então sempre usa a
        utilidade. A busca usa quando tem material para filtrar — ver
        `test_the_search_uses_the_same_utility_when_it_has_a_side_column`.
        """
        for url, params in (("/modelos/", {}), ("/categorias/filamentos/", {})):
            with self.subTest(url=url):
                self.assertIn('class="shop-layout"', self.html(url, **params))

    def test_the_search_uses_the_same_utility_when_it_has_a_side_column(self):
        """Com material para filtrar, a busca é o catálogo — mesma utilidade."""
        html = self.html("/buscar/", q="pla")

        if 'id="shop-materials"' in html:
            self.assertIn('class="shop-layout"', html)

    def test_no_template_hardcodes_the_grid_columns(self):
        for arquivo in ("shop.html", "search.html"):
            with self.subTest(arquivo=arquivo):
                with open(f"templates/catalog/{arquivo}", encoding="utf-8") as origem:
                    self.assertNotIn("lg:grid-cols-[254px_1fr]", origem.read())

    def test_without_a_filter_no_empty_side_column_is_drawn(self):
        """Coluna vazia é vão morto: 254 px empurrando tudo para a direita."""
        self.sem_material()

        html = self.html("/buscar/", q="Anônima")

        self.assertNotIn('id="shop-materials"', html)
        self.assertNotIn('class="shop-layout"', html)

    def test_without_a_filter_the_results_take_the_whole_width(self):
        """O ponto: a barra e a grade começam onde o título começa."""
        self.sem_material()

        html = self.html("/buscar/", q="Anônima")
        depois = html.split('id="shop-results"')[1][:120]

        self.assertIn('class="mt-7"', depois)
        # Nada de `max-w-*` aqui: essa é a largura do estado vazio, não a de
        # uma página com cards.
        self.assertNotIn("max-w-", depois)

    def test_all_three_pages_use_the_same_grid_and_card(self):
        for url, params in (
            ("/modelos/", {}),
            ("/categorias/filamentos/", {}),
            ("/buscar/", {"q": "gato"}),
        ):
            with self.subTest(url=url):
                html = self.html(url, **params)
                self.assertIn('class="product-grid', html)

        resposta = self.client.get("/buscar/", {"q": "gato"})
        self.assertTemplateUsed(resposta, "components/product_card.html")

    def test_one_material_is_enough_for_the_search_to_show_the_filter(self):
        """A causa do defeito: o mínimo da vitrine (dois) apagava o filtro.

        De oito termos medidos no banco de trabalho, sete alcançavam exatamente
        um material — e em todos os sete o filtro sumia.
        """
        html = self.html("/buscar/", q="gato")  # só PLA

        self.assertIn('id="shop-materials"', html)
        self.assertIn("PLA", html)

    def test_the_filter_always_offers_todos_beside_the_material(self):
        """Sem "Todos" não há como desfazer a escolha."""
        html = self.html("/buscar/", q="gato")

        self.assertIn("Todos", html)

    def test_selecting_the_material_still_filters(self):
        """A seleção continua funcionando — nenhuma lógica de busca mudou."""
        todos = self.client.get("/buscar/", {"q": "gato"})
        filtrado = self.client.get("/buscar/", {"q": "gato", "material": self.pla.slug})

        self.assertEqual(self.encontrados(filtrado), self.encontrados(todos))
        self.assertEqual(filtrado.context["selected_material"], self.pla)

    def test_a_material_that_matches_nothing_returns_nothing(self):
        """Filtrar por Resina numa busca só de PLA não pode devolver o PLA."""
        resposta = self.client.get("/buscar/", {"q": "gato", "material": self.resina.slug})

        self.assertEqual(self.encontrados(resposta), [])

    def test_results_without_any_material_draw_no_filter(self):
        """Um bloco com só "Todos" seria uma escolha que não escolhe nada."""
        self.sem_material()

        html = self.html("/buscar/", q="Anônima")

        self.assertNotIn('id="shop-materials"', html)

    def test_the_htmx_swap_returns_the_same_block_as_the_full_page(self):
        """A troca *out of band* é `outerHTML`: o que os dois lados não
        compartilham, o primeiro clique perde.

        O defeito real: a página cheia embrulhava o filtro num
        `card p-3 lg:sticky lg:top-28` e a resposta parcial não. Clicar num
        material trocava `#shop-materials` inteiro pela versão sem cartão — a
        lateral perdia fundo, borda, respiro e `sticky`.

        Comparar os dois blocos é o que impede a divergência de voltar.
        """
        import re

        def bloco(**extra):
            html = self.html("/buscar/", q="gato", material=self.pla.slug, **extra)
            self.assertIn('id="shop-materials"', html)
            corpo = html.split('id="shop-materials"', 1)[1]
            corpo = corpo.split("</div>")[0]
            return re.sub(r"\s+", " ", corpo.replace(' hx-swap-oob="true"', "")).strip()

        self.assertEqual(bloco(), bloco(HTTP_HX_REQUEST="true"))

    def test_the_side_column_survives_the_htmx_swap(self):
        """A lateral tem de estar nos dois lados, não só na página cheia.

        É a `<aside>` do catálogo — a mesma classe, o mesmo `popover` que a
        transforma em gaveta no celular. Uma resposta parcial sem eles
        devolveria uma lista de materiais solta no lugar da lateral.
        """
        for extra in ({}, {"HTTP_HX_REQUEST": "true"}):
            with self.subTest(htmx=bool(extra)):
                html = self.html("/buscar/", q="gato", material=self.pla.slug, **extra)
                depois = html.split('id="shop-materials"', 1)[1][:160]

                self.assertIn("catalog-aside", depois)
                self.assertIn("popover", depois)

    def test_the_catalogue_filter_lives_inside_the_category_sidebar(self):
        """No catálogo o filtro fica DENTRO da lateral das categorias.

        Ele não pode ganhar uma lateral própria: seriam duas gavetas, e o
        botão "Filtros" só abre uma.
        """
        html = self.html("/categorias/filamentos/")

        self.assertNotIn('id="shop-materials"', html)
        self.assertEqual(html.count('class="catalog-aside"'), 1)

    def test_the_shop_still_needs_two_options(self):
        """A vitrine não muda: lá "Todos + PLA" seria a lista inteira duas vezes."""
        from apps.catalog.views import SearchView, ShopView

        self.assertEqual(ShopView.MIN_MATERIAL_OPTIONS, 2)
        self.assertEqual(SearchView.MIN_MATERIAL_OPTIONS, 1)

    def test_the_controls_bar_and_the_grid_share_the_same_box(self):
        """A barra de ordenação mede o mesmo que a grade — as duas em `#shop-results`.

        Era o sintoma visível: a barra parecia mais larga que os produtos
        porque as duas viviam numa coluna deslocada, e não porque medissem
        diferente.
        """
        html = self.html("/buscar/", q="gato")
        bloco = html.split('id="shop-results"')[1]

        self.assertIn('class="product-grid', bloco)
        self.assertIn("Ordenar", bloco)

    def test_the_empty_state_stays_in_the_reading_column(self):
        """Sem resultado, uma faixa de 1216 px em volta de quatro linhas seria pior."""
        depois = self.html("/buscar/", q="zzzznaoexiste").split('id="shop-results"')[1][:120]

        self.assertIn("max-w-2xl", depois)

    def test_the_search_field_is_centred_and_not_full_width(self):
        """O campo é uma ação, não um bloco de leitura: fica no meio e estreito."""
        with open("templates/catalog/search.html", encoding="utf-8") as origem:
            fonte = origem.read()

        self.assertIn('class="mx-auto mt-6 flex max-w-xl gap-2"', fonte)

    def test_without_results_there_is_no_dead_side_column(self):
        """O defeito: 254 px de vão morto empurravam tudo para a direita.

        Medido a 1440 px, a barra e o estado vazio começavam em x=394 enquanto
        o título ficava em x=112. A coluna existe para alinhar cards com o
        catálogo — sem card, não há nada a alinhar e ela só desloca a página.
        """
        html = self.html("/buscar/", q="zzzznaoexiste")

        self.assertNotIn('class="shop-layout"', html)
        self.assertNotIn('id="shop-materials"', html)
        self.assertIn('id="shop-results"', html)

    def test_without_a_query_there_is_no_dead_side_column(self):
        html = self.html("/buscar/")

        self.assertNotIn('class="shop-layout"', html)

    def test_the_empty_block_shares_the_header_measure(self):
        """Sem a medida, o estado vazio seria uma faixa tracejada de 1216 px."""
        html = self.html("/buscar/", q="zzzznaoexiste")
        bloco = html.split('id="shop-results"', 1)[1][:80]

        self.assertIn("max-w-2xl", bloco)
        # A mesma medida do cabecalho da pagina -- e nao um numero inventado.
        cabecalho = html.split("<header", 2)[2].split(">", 1)[0]
        self.assertIn("max-w-2xl", cabecalho)

    def test_without_results_but_with_a_material_filter_the_grid_stays(self):
        """O filtro precisa continuar alcançável para o cliente alargar a busca.

        Dois produtos casam a palavra, um de cada material; o filtro pede um
        terceiro material, que nenhum deles tem. Zero resultados, mas a lista
        de materiais continua lá — é por ela que se volta atrás.
        """
        make_product(
            sku="SUP-PLA", name="Suporte Simples", category=self.animais,
            price=Decimal("4.00"), stock_quantity=2, material=self.pla,
        )
        make_product(
            sku="SUP-RES", name="Suporte Duplo", category=self.animais,
            price=Decimal("6.00"), stock_quantity=2, material=self.resina,
        )
        petg = Material.objects.create(name="PETG")

        resposta = self.client.get("/buscar/", {"q": "Suporte", "material": petg.slug})

        self.assertEqual(len(resposta.context["products"]), 0)
        self.assertEqual(len(resposta.context["material_nodes"]), 2)
        self.assertContains(resposta, 'class="shop-layout"')

    def test_no_artificial_height_anywhere(self):
        """Empurrar o rodapé com altura fixa esconderia o sintoma.

        No HTML entregue, e não no texto-fonte: o comentário do template
        explica justamente por que não há `min-height`, e citar a palavra não
        pode reprovar o teste.
        """
        html = self.html("/buscar/", q="zzzznaoexiste")
        corpo = html.split("<main", 1)[1].split("</main>", 1)[0]

        for hack in ("min-h-", "h-screen", "min-height", "height:"):
            with self.subTest(hack=hack):
                self.assertNotIn(hack, corpo)

    def test_the_search_has_no_card_css_of_its_own(self):
        """Um segundo sistema de cards seria um segundo lugar para corrigir."""
        with open("templates/catalog/search.html", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        self.assertNotIn("product-grid", fonte)
        self.assertNotIn("product_card.html", fonte)
        self.assertIn('{% include "catalog/_shop_results.html" %}', fonte)


class SearchPageTests(SearchBase):
    """1.4 — a página de resultados."""

    def test_it_reuses_the_product_card(self):
        resposta = self.buscar("gato")

        self.assertTemplateUsed(resposta, "components/product_card.html")
        self.assertTemplateUsed(resposta, "catalog/_shop_results.html")

    def test_it_shows_how_many_were_found(self):
        resposta = self.buscar("gato")

        self.assertEqual(resposta.context["result_count"], 1)
        self.assertContains(resposta, "1 produto encontrado")

    def test_the_card_carries_name_and_price(self):
        resposta = self.buscar("gato")

        self.assertContains(resposta, "Gato Pompom")
        self.assertContains(resposta, "8,90")

    def test_the_field_comes_back_filled(self):
        """Corrigir uma palavra não pode exigir digitar tudo de novo."""
        resposta = self.buscar("gato")

        self.assertContains(resposta, 'value="gato"')

    def test_the_header_field_is_a_real_form(self):
        resposta = self.client.get("/")

        self.assertContains(resposta, 'action="/buscar/"')
        self.assertNotContains(resposta, 'id="busca" type="search" class="field-input w-44 lg:w-60" disabled')

    def test_it_paginates_like_the_shop(self):
        for numero in range(15):
            make_product(
                sku=f"GATO-1{numero:02}",
                name=f"Gato de teste {numero}",
                category=self.animais,
                price=Decimal("5.00"),
                stock_quantity=1,
            )

        resposta = self.buscar("gato")

        self.assertTrue(resposta.context["page_obj"].has_other_pages())
        self.assertEqual(len(resposta.context["products"]), resposta.context["page_size"])

    def test_the_sort_order_still_works(self):
        resposta = self.client.get(BUSCA, {"q": "o", "ordenar": "preco-desc"})

        self.assertEqual(resposta.status_code, 200)

    def test_sorting_keeps_the_query(self):
        """Trocar a ordenação não pode apagar o que foi procurado."""
        resposta = self.buscar("gato")

        self.assertContains(resposta, '<input type="hidden" name="q" value="gato">')


class SearchEmptyStateTests(LanguageResetMixin, TestCase):
    """Busca sem resultado é a única tela em que o cliente já disse o que quer
    e a loja não tem. Ela precisa oferecer um próximo passo.

    A JD PRINT imprime sob encomenda: "não está no catálogo" não é o mesmo que
    "não dá para fazer", e é por isso que o convite ao contato existe aqui — e
    **só** aqui. Numa categoria vazia o caminho certo é continuar navegando.
    """

    IDIOMAS = (
        ("", "Não encontramos nada para", "Entrar em contato"),
        ("/fr", "Nous n’avons rien trouvé pour", "Nous contacter"),
        ("/nl", "We hebben niets gevonden voor", "Contact opnemen"),
        ("/en", "We couldn’t find anything for", "Get in touch"),
    )

    def buscar(self, prefixo=""):
        return self.client.get(f"{prefixo}/buscar/", {"q": "zzznaoexiste"})

    def test_the_message_is_humanised_in_every_language(self):
        for prefixo, mensagem, _botao in self.IDIOMAS:
            with self.subTest(idioma=prefixo or "pt"):
                self.assertContains(self.buscar(prefixo), mensagem)

    def test_the_contact_button_is_offered_in_every_language(self):
        for prefixo, _mensagem, botao in self.IDIOMAS:
            with self.subTest(idioma=prefixo or "pt"):
                self.assertContains(self.buscar(prefixo), botao)

    def test_the_button_points_at_the_institutional_contact_page(self):
        """A página existe e é administrável — o link não é inventado aqui."""
        resposta = self.buscar()

        self.assertContains(resposta, reverse("storefront:page_contact"))

    def test_the_invitation_to_write_is_there(self):
        self.assertContains(self.buscar(), "fale com a gente")

    def test_the_block_is_centred(self):
        """Encostado à esquerda, sobravam 600 px de vazio numa tela larga."""
        with io.open("templates/catalog/search.html", encoding="utf-8") as origem:
            fonte = origem.read()

        self.assertIn('id="shop-results" class="mx-auto mt-7 max-w-2xl"', fonte)

    def test_a_search_with_results_keeps_no_contact_button(self):
        """O convite é da tela vazia; com resultados ele seria ruído."""
        make_product(sku="ACHA-01", name="Vaso Achável", price=Decimal("10.00"))

        resposta = self.client.get("/buscar/", {"q": "Achável"})

        self.assertNotContains(resposta, "Entrar em contato")

    def test_an_empty_category_does_not_invite_to_contact(self):
        """Categoria sem produto tem outro caminho: continuar navegando."""
        categoria = make_category(slug="modelos", name="Modelos")
        resposta = self.client.get(reverse("catalog:models_shop"), {"categoria": categoria.slug})

        self.assertNotContains(resposta, "Entrar em contato")

    def test_the_search_page_without_a_term_does_not_invite_either(self):
        """Sem termo, ninguém procurou nada — não há o que não ter sido achado."""
        resposta = self.client.get("/buscar/")

        self.assertNotContains(resposta, "Entrar em contato")
