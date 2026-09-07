"""A composição da Home — etapa 20.

A Home é a lista das seções ativas, na ordem do Admin: faixas de produtos e
também os blocos (categorias em destaque, como trabalhamos, chamada final,
sobre a loja), que antes tinham posição fixa no template. O que estes testes
guardam:

1. a ordem é a do cadastro, e mover uma seção move o bloco;
2. um mesmo tipo pode aparecer mais de uma vez, cada instância com o seu
   conteúdo;
3. desativar uma seção a tira da página;
4. a faixa de fundo é consequência da POSIÇÃO entre as seções desenhadas —
   remover uma reajusta as outras;
5. as pílulas continuam do "Sobre a loja" e os passos da chamada final;
6. a migration de dados preserva o conteúdo e a ordem de antes;
7. cadastrar mais blocos ou mais seções de bloco não custa consulta nenhuma;
8. o Admin mostra a lista como a página (posição, tipo, nome da instância).
"""

import re

from django.contrib.auth.models import Permission
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_category, make_product, make_section, make_user
from apps.home.models import (
    HomeAbout,
    HomeAboutBadge,
    HomeAboutBadgeTranslation,
    HomeAboutTranslation,
    HomeCallout,
    HomeCalloutStep,
    HomeCalloutStepTranslation,
    HomeCalloutTranslation,
    HomeCard,
    HomeCardTranslation,
    HomeCategoryCard,
    HomeCategoryCardTranslation,
    HomeSection,
    HomeSectionType,
)

HOME = "/"


class ComposicaoBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.modelos = make_category(slug="modelos", name="Modelos")
        self.brinquedos = make_category(slug="brinquedos", name="Brinquedos")

    def html(self, url=HOME):
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()

    # -- fábricas ------------------------------------------------------------

    def secao(self, tipo, nome, ordem, **campos):
        return HomeSection.objects.create(
            internal_name=nome, section_type=tipo, sort_order=ordem, **campos
        )

    def categorias(self, nome, ordem, blocos, **campos):
        secao = self.secao(HomeSectionType.CATEGORY_CARDS, nome, ordem, **campos)
        for posicao, (titulo, categoria) in enumerate(blocos):
            bloco = HomeCategoryCard.objects.create(
                section=secao, internal_name=titulo, category=categoria, sort_order=posicao
            )
            HomeCategoryCardTranslation.objects.create(master=bloco, language="pt", title=titulo)
        return secao

    def como_trabalhamos(self, nome, ordem, titulos=("Produção própria aqui",), **campos):
        secao = self.secao(HomeSectionType.HOW_WE_WORK, nome, ordem, **campos)
        for posicao, titulo in enumerate(titulos):
            card = HomeCard.objects.create(section=secao, internal_name=titulo, sort_order=posicao)
            HomeCardTranslation.objects.create(master=card, language="pt", title=titulo)
        return secao

    def chamada(self, nome, ordem, titulo="Tem uma ideia?", passos=(), **campos):
        texto = HomeCallout.objects.create(internal_name=f"texto {nome}")
        HomeCalloutTranslation.objects.create(master=texto, language="pt", title=titulo)
        for posicao, passo in enumerate(passos):
            registro = HomeCalloutStep.objects.create(callout=texto, internal_name=passo, sort_order=posicao)
            HomeCalloutStepTranslation.objects.create(master=registro, language="pt", title=passo)
        return self.secao(HomeSectionType.CALLOUT, nome, ordem, callout=texto, **campos)

    def sobre(self, nome, ordem, titulo="Feito na Bélgica", pilulas=(), **campos):
        bloco = HomeAbout.objects.create(internal_name=f"bloco {nome}")
        HomeAboutTranslation.objects.create(master=bloco, language="pt", title=titulo)
        for posicao, pilula in enumerate(pilulas):
            registro = HomeAboutBadge.objects.create(about=bloco, internal_name=pilula, sort_order=posicao)
            HomeAboutBadgeTranslation.objects.create(master=registro, language="pt", text=pilula)
        return self.secao(HomeSectionType.ABOUT, nome, ordem, about=bloco, **campos)

    def produtos(self, nome, ordem, titulo):
        make_product(sku=f"SKU-{ordem}", name=f"Produto {ordem}", category=self.modelos, is_featured=True)
        return make_section(internal_name=nome, title=titulo, sort_order=ordem)

    # -- leitura da página -----------------------------------------------------

    def secoes_na_pagina(self, html):
        """Os ids das seções da composição, na ordem em que aparecem."""
        return re.findall(r'aria-labelledby="(secao-[\w-]+)"', html)

    def faixa(self, html, anchor):
        """'white' ou 'cream', pela classe da <section> desta âncora."""
        tag = re.search(rf'<section class="([^"]*)"\s+aria-labelledby="{anchor}"', html).group(1)
        return "white" if "bg-white" in tag else "cream"


# ---------------------------------------------------------------------------
# Ordem, repetição, ativação
# ---------------------------------------------------------------------------


class OrdemDaComposicaoTests(ComposicaoBase):
    def test_sections_come_in_the_admin_order_whatever_their_type(self):
        s_sobre = self.sobre("Sobre", 10)
        s_cat = self.categorias("Categorias", 20, [("Modelos 3D", self.modelos)])
        s_prod = self.produtos("Destaques", 30, "Destaques")
        s_como = self.como_trabalhamos("Como", 40)
        s_chamada = self.chamada("Chamada", 50)

        self.assertEqual(
            self.secoes_na_pagina(self.html()),
            [f"secao-{s.pk}" for s in (s_sobre, s_cat, s_prod, s_como, s_chamada)],
        )

    def test_moving_a_section_moves_the_block(self):
        s_cat = self.categorias("Categorias", 10, [("Modelos 3D", self.modelos)])
        s_como = self.como_trabalhamos("Como", 20)
        self.assertEqual(self.secoes_na_pagina(self.html()), [f"secao-{s_cat.pk}", f"secao-{s_como.pk}"])

        s_cat.sort_order = 30
        s_cat.save()

        self.assertEqual(self.secoes_na_pagina(self.html()), [f"secao-{s_como.pk}", f"secao-{s_cat.pk}"])

    def test_the_same_type_can_appear_more_than_once_each_with_its_own_content(self):
        primeira = self.categorias("Categorias — Nossos produtos", 10, [("Modelos 3D", self.modelos)])
        self.como_trabalhamos("Como", 20)
        segunda = self.categorias("Categorias — Coleções", 30, [("Brinquedos", self.brinquedos)])

        html = self.html()

        ids = self.secoes_na_pagina(html)
        self.assertEqual(ids.count(f"secao-{primeira.pk}"), 1)
        self.assertEqual(ids.count(f"secao-{segunda.pk}"), 1)
        # Cada instância com os seus blocos, na sua posição.
        self.assertLess(html.index(f"secao-{primeira.pk}"), html.index(f"secao-{segunda.pk}"))
        bloco_primeira = html.split(f'aria-labelledby="secao-{primeira.pk}"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Modelos 3D", bloco_primeira)
        self.assertNotIn("Brinquedos", bloco_primeira)
        bloco_segunda = html.split(f'aria-labelledby="secao-{segunda.pk}"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Brinquedos", bloco_segunda)
        self.assertNotIn("Modelos 3D", bloco_segunda)

    def test_two_callout_sections_can_share_one_text(self):
        texto = HomeCallout.objects.create(internal_name="Texto único")
        HomeCalloutTranslation.objects.create(master=texto, language="pt", title="Peça o seu projeto")
        self.secao(HomeSectionType.CALLOUT, "Chamada 1", 10, callout=texto)
        self.como_trabalhamos("Como", 20)
        self.secao(HomeSectionType.CALLOUT, "Chamada 2", 30, callout=texto)

        self.assertEqual(self.html().count("Peça o seu projeto"), 2)

    def test_deactivating_a_section_removes_it_without_touching_its_content(self):
        s_cat = self.categorias("Categorias", 10, [("Modelos 3D", self.modelos)])
        s_como = self.como_trabalhamos("Como", 20)

        s_cat.is_active = False
        s_cat.save()

        html = self.html()
        self.assertEqual(self.secoes_na_pagina(html), [f"secao-{s_como.pk}"])
        self.assertNotIn("Modelos 3D", html)
        self.assertEqual(HomeCategoryCard.objects.filter(section=s_cat).count(), 1)

    def test_a_category_section_without_blocks_is_not_drawn(self):
        vazia = self.secao(HomeSectionType.CATEGORY_CARDS, "Vazia", 10)
        s_como = self.como_trabalhamos("Como", 20)

        self.assertEqual(self.secoes_na_pagina(self.html()), [f"secao-{s_como.pk}"])
        self.assertTrue(HomeSection.objects.filter(pk=vazia.pk).exists())

    def test_an_about_section_without_a_chosen_block_is_not_drawn(self):
        self.secao(HomeSectionType.ABOUT, "Sobre sem bloco", 10)
        s_como = self.como_trabalhamos("Como", 20)

        self.assertEqual(self.secoes_na_pagina(self.html()), [f"secao-{s_como.pk}"])

    def test_pills_belong_to_the_about_block_and_steps_to_the_callout(self):
        self.sobre("Sobre", 10, pilulas=("Feito na Bélgica", "Envio rápido"))
        self.chamada("Chamada", 20, passos=("Escolha", "Personalize", "Receba"))

        html = self.html()

        bloco_sobre = html.split('class="about"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Feito na Bélgica", bloco_sobre)
        self.assertIn("Envio rápido", bloco_sobre)
        bloco_chamada = html.split('class="callout"', 1)[1].split("</section>", 1)[0]
        for passo in ("Escolha", "Personalize", "Receba"):
            self.assertIn(passo, bloco_chamada)

    def test_the_banner_and_the_carousel_still_open_the_page(self):
        from apps.home.models import CtaTarget, HomeBanner, HomeBannerTranslation

        for nome in ("A", "B"):
            banner = HomeBanner.objects.create(
                internal_name=nome, cta_target=CtaTarget.CATEGORY, cta_category=self.modelos
            )
            HomeBannerTranslation.objects.create(master=banner, language="pt", title=f"Banner {nome}")
        self.categorias("Categorias", 10, [("Modelos 3D", self.modelos)])

        html = self.html()

        self.assertIn("data-banner-carousel", html)
        self.assertIn("Banner A", html)
        self.assertLess(html.index("data-banner-carousel"), html.index("Modelos 3D"))


# ---------------------------------------------------------------------------
# A faixa de fundo pela posição
# ---------------------------------------------------------------------------


class FaixaDeFundoTests(ComposicaoBase):
    def test_bands_alternate_by_position_starting_white_after_the_banner(self):
        a = self.categorias("Cat A", 10, [("Modelos 3D", self.modelos)])
        b = self.produtos("Destaques", 20, "Destaques")
        c = self.categorias("Cat C", 30, [("Brinquedos", self.brinquedos)])

        html = self.html()

        self.assertEqual(self.faixa(html, f"secao-{a.pk}"), "white")
        self.assertEqual(self.faixa(html, f"secao-{b.pk}"), "cream")
        self.assertEqual(self.faixa(html, f"secao-{c.pk}"), "white")

    def test_removing_a_section_shifts_the_bands(self):
        a = self.categorias("Cat A", 10, [("Modelos 3D", self.modelos)])
        b = self.produtos("Destaques", 20, "Destaques")
        c = self.categorias("Cat C", 30, [("Brinquedos", self.brinquedos)])
        self.assertEqual(self.faixa(self.html(), f"secao-{c.pk}"), "white")

        b.is_active = False
        b.save()

        html = self.html()
        self.assertEqual(self.faixa(html, f"secao-{a.pk}"), "white")
        self.assertEqual(self.faixa(html, f"secao-{c.pk}"), "cream")

    def test_moving_a_section_recomputes_its_band(self):
        a = self.categorias("Cat A", 10, [("Modelos 3D", self.modelos)])
        c = self.categorias("Cat C", 30, [("Brinquedos", self.brinquedos)])
        self.assertEqual(self.faixa(self.html(), f"secao-{c.pk}"), "cream")

        c.sort_order = 5
        c.save()

        html = self.html()
        self.assertEqual(self.faixa(html, f"secao-{c.pk}"), "white")
        self.assertEqual(self.faixa(html, f"secao-{a.pk}"), "cream")

    def test_blocks_with_their_own_surface_still_count_in_the_alternation(self):
        """A chamada escura ocupa uma posição: a seção seguinte recebe a faixa dela."""
        a = self.categorias("Cat A", 10, [("Modelos 3D", self.modelos)])
        self.chamada("Chamada", 20)
        c = self.categorias("Cat C", 30, [("Brinquedos", self.brinquedos)])

        html = self.html()

        self.assertEqual(self.faixa(html, f"secao-{a.pk}"), "white")
        self.assertEqual(self.faixa(html, f"secao-{c.pk}"), "white")
        self.assertIn('class="callout"', html)

    def test_no_band_is_stored_on_the_section(self):
        self.assertFalse(any(f.name in ("band", "surface") for f in HomeSection._meta.fields))


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


class ComposicaoQueryTests(ComposicaoBase):
    def count_queries(self):
        with CaptureQueriesContext(connection) as captured:
            self.client.get(HOME)
        return len(captured)

    def test_more_blocks_and_more_block_sections_cost_no_extra_query(self):
        self.categorias("Cat A", 10, [("Modelos 3D", self.modelos)])
        self.como_trabalhamos("Como", 20)
        self.chamada("Chamada", 30, passos=("Um",))
        self.sobre("Sobre", 40, pilulas=("Uma",))
        base = self.count_queries()

        for numero in range(3):
            self.categorias(f"Cat extra {numero}", 50 + numero, [("Brinquedos", self.brinquedos)] * 4)
            self.como_trabalhamos(f"Como extra {numero}", 60 + numero, titulos=("a", "b", "c", "d"))
            self.chamada(f"Chamada extra {numero}", 70 + numero, passos=("Um", "Dois", "Três"))
            self.sobre(f"Sobre extra {numero}", 80 + numero, pilulas=("Uma", "Duas", "Três"))

        self.assertEqual(self.count_queries(), base)


# ---------------------------------------------------------------------------
# Admin: a lista é a página
# ---------------------------------------------------------------------------


class ComposicaoAdminTests(ComposicaoBase):
    def setUp(self):
        super().setUp()
        self.chefe = make_user("chefe", is_staff=True, is_superuser=True)
        self.client.force_login(self.chefe)

    def test_the_changelist_numbers_the_sections_and_names_type_and_instance(self):
        self.categorias("Categorias — Nossos produtos", 10, [("Modelos 3D", self.modelos)])
        self.como_trabalhamos("Como trabalhamos", 20)
        self.categorias("Categorias — Coleções", 30, [("Brinquedos", self.brinquedos)])

        html = self.client.get(reverse("admin:home_homesection_changelist")).content.decode()

        for nome in ("Categorias — Nossos produtos", "Como trabalhamos", "Categorias — Coleções"):
            self.assertIn(nome, html)
        self.assertLess(html.index("Categorias — Nossos produtos"), html.index("Categorias — Coleções"))
        self.assertIn("<strong>01</strong>", html)
        self.assertIn("<strong>03</strong>", html)
        self.assertIn("Categorias em destaque", html)  # o tipo, ao lado do nome da instância
        self.assertIn("1 bloco(s) de categoria", html)

    def test_the_add_form_offers_every_kind_and_the_content_pickers(self):
        html = self.client.get(reverse("admin:home_homesection_add")).content.decode()

        for rotulo in ("Categorias em destaque", "Como trabalhamos", "Chamada final", "Sobre a loja"):
            self.assertIn(rotulo, html)
        self.assertIn('name="callout"', html)
        self.assertIn('name="about"', html)
        self.assertIn("home_section_admin.js", html)

    def test_a_block_section_saves_without_a_translated_title(self):
        from apps.home.tests.test_admin import inline_payload

        data = {
            "internal_name": "Categorias — Coleções",
            "is_active": "on",
            "sort_order": "5",
            "section_type": HomeSectionType.CATEGORY_CARDS,
            "layout": "grid",
            "product_limit": "4",
            "category": "",
            "include_subcategories": "on",
            "callout": "",
            "about": "",
            "cta_target": "none",
            "cta_category": "",
            "cta_product": "",
            "cta_url": "",
        }
        data.update(inline_payload("translations", []))
        data.update(inline_payload("items", []))

        resposta = self.client.post(reverse("admin:home_homesection_add"), data, follow=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(HomeSection.objects.filter(internal_name="Categorias — Coleções").exists())

    def test_a_product_section_still_requires_the_portuguese_title(self):
        from apps.home.tests.test_admin import inline_payload

        data = {
            "internal_name": "Destaques",
            "is_active": "on",
            "sort_order": "5",
            "section_type": HomeSectionType.FEATURED_PRODUCTS,
            "layout": "grid",
            "product_limit": "4",
            "category": "",
            "include_subcategories": "on",
            "callout": "",
            "about": "",
            "cta_target": "none",
            "cta_category": "",
            "cta_product": "",
            "cta_url": "",
        }
        data.update(inline_payload("translations", []))
        data.update(inline_payload("items", []))

        resposta = self.client.post(reverse("admin:home_homesection_add"), data)

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("título em português", resposta.content.decode())
        self.assertFalse(HomeSection.objects.filter(internal_name="Destaques").exists())

    def test_the_content_admins_are_no_longer_single_row_and_link_to_their_sections(self):
        s_chamada = self.chamada("Chamada — Natal", 10)
        self.sobre("Sobre", 20)

        chamadas = self.client.get(reverse("admin:home_homecallout_changelist"))
        self.assertEqual(chamadas.status_code, 200)
        self.assertContains(chamadas, "Chamada — Natal")
        self.assertEqual(self.client.get(reverse("admin:home_homecallout_add")).status_code, 200)

        sobres = self.client.get(reverse("admin:home_homeabout_changelist"))
        self.assertEqual(sobres.status_code, 200)
        self.assertContains(sobres, "Sobre")
        self.assertEqual(self.client.get(reverse("admin:home_homeabout_add")).status_code, 200)

        # A seção que usa o texto aparece na lista das chamadas.
        self.assertContains(chamadas, s_chamada.internal_name)

    def test_permissions_still_gate_the_composition(self):
        leitor = make_user("leitor", is_staff=True)
        leitor.user_permissions.add(Permission.objects.get(codename="view_homesection"))
        self.client.force_login(leitor)

        self.assertEqual(self.client.get(reverse("admin:home_homesection_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:home_homesection_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:home_homecategorycard_changelist")).status_code, 403)


# ---------------------------------------------------------------------------
# A migration de dados preserva a Home de antes
# ---------------------------------------------------------------------------


class MigracaoDaComposicaoTests(TransactionTestCase):
    """Volta a `home` para antes da composição, cadastra a Home como ela era
    (blocos globais, chamada e sobre únicos, seções de produtos) e aplica a
    migration: nada some, nada é recriado, e a ordem é a de sempre."""

    antes = ("home", "0008_banner_carousel_settings")
    schema = ("home", "0009_home_composition")
    depois = ("home", "0010_home_composition_data")

    def migrar(self, alvo):
        executor = MigrationExecutor(connection)
        executor.migrate([alvo])
        executor.loader.build_graph()
        return executor.loader.project_state([alvo]).apps

    def tearDown(self):
        # Deixa o banco de teste no estado final, para os outros testes.
        MigrationExecutor(connection).migrate([self.depois])
        super().tearDown()

    def test_existing_content_is_kept_and_ordered_as_before(self):
        apps_antes = self.migrar(self.antes)
        Category = apps_antes.get_model("categories", "Category")
        HomeSectionA = apps_antes.get_model("home", "HomeSection")
        HomeSectionTranslationA = apps_antes.get_model("home", "HomeSectionTranslation")
        HomeCardA = apps_antes.get_model("home", "HomeCard")
        HomeCardTranslationA = apps_antes.get_model("home", "HomeCardTranslation")
        HomeCategoryCardA = apps_antes.get_model("home", "HomeCategoryCard")
        HomeCategoryCardTranslationA = apps_antes.get_model("home", "HomeCategoryCardTranslation")
        HomeCalloutA = apps_antes.get_model("home", "HomeCallout")
        HomeCalloutTranslationA = apps_antes.get_model("home", "HomeCalloutTranslation")
        HomeCalloutStepA = apps_antes.get_model("home", "HomeCalloutStep")
        HomeAboutA = apps_antes.get_model("home", "HomeAbout")
        HomeAboutTranslationA = apps_antes.get_model("home", "HomeAboutTranslation")
        HomeAboutBadgeA = apps_antes.get_model("home", "HomeAboutBadge")

        categoria = Category.objects.create(slug="modelos")
        destaques = HomeSectionA.objects.create(internal_name="Destaques", section_type="featured", sort_order=2)
        HomeSectionTranslationA.objects.create(master=destaques, language="pt", title="Destaques")
        HomeSectionTranslationA.objects.create(master=destaques, language="fr", title="À la une")
        novidades = HomeSectionA.objects.create(internal_name="Novidades", section_type="newest", sort_order=1)
        HomeSectionTranslationA.objects.create(master=novidades, language="pt", title="Novidades")
        for numero in range(3):
            card = HomeCardA.objects.create(internal_name=f"Card {numero}", sort_order=numero)
            HomeCardTranslationA.objects.create(master=card, language="pt", title=f"Card {numero}")
            HomeCardTranslationA.objects.create(master=card, language="nl", title=f"Kaart {numero}")
        for numero in range(2):
            bloco = HomeCategoryCardA.objects.create(
                internal_name=f"Bloco {numero}", category=categoria, sort_order=numero
            )
            HomeCategoryCardTranslationA.objects.create(master=bloco, language="pt", title=f"Bloco {numero}")
        chamada = HomeCalloutA.objects.create(pk=1)
        HomeCalloutTranslationA.objects.create(master=chamada, language="pt", title="Tem uma ideia?")
        HomeCalloutStepA.objects.create(callout=chamada, internal_name="Passo 1", sort_order=0)
        sobre = HomeAboutA.objects.create(pk=1)
        HomeAboutTranslationA.objects.create(master=sobre, language="pt", title="Feito na Bélgica")
        HomeAboutTranslationA.objects.create(master=sobre, language="en", title="Made in Belgium")
        HomeAboutBadgeA.objects.create(about=sobre, internal_name="Pílula", sort_order=0)

        # A 0009 é só schema: nenhuma seção nasce, os vínculos ficam vazios.
        apps_schema = self.migrar(self.schema)
        HomeSectionS = apps_schema.get_model("home", "HomeSection")
        self.assertEqual(HomeSectionS.objects.count(), 2)
        self.assertEqual(set(apps_schema.get_model("home", "HomeCard").objects.values_list("section_id", flat=True)), {None})
        self.assertEqual(
            set(apps_schema.get_model("home", "HomeCategoryCard").objects.values_list("section_id", flat=True)), {None}
        )

        apps_depois = self.migrar(self.depois)
        HomeSectionD = apps_depois.get_model("home", "HomeSection")
        HomeCardD = apps_depois.get_model("home", "HomeCard")
        HomeCategoryCardD = apps_depois.get_model("home", "HomeCategoryCard")
        HomeCalloutD = apps_depois.get_model("home", "HomeCallout")
        HomeAboutD = apps_depois.get_model("home", "HomeAbout")

        ordem = list(HomeSectionD.objects.order_by("sort_order", "id").values_list("section_type", "internal_name"))
        self.assertEqual(
            ordem,
            [
                ("category_cards", "Categorias em destaque"),
                ("newest", "Novidades"),
                ("featured", "Destaques"),
                ("how_we_work", "Como trabalhamos"),
                ("callout", "Chamada final"),
                ("about", "Sobre a loja"),
            ],
        )
        # Nada foi apagado nem recriado: mesmos registros, mesmas traduções.
        self.assertEqual(HomeCardD.objects.count(), 3)
        self.assertEqual(HomeCategoryCardD.objects.count(), 2)
        self.assertEqual(HomeCalloutD.objects.count(), 1)
        self.assertEqual(HomeAboutD.objects.count(), 1)
        self.assertEqual(set(HomeCardD.objects.values_list("section__section_type", flat=True)), {"how_we_work"})
        self.assertEqual(set(HomeCategoryCardD.objects.values_list("section__section_type", flat=True)), {"category_cards"})
        secao_chamada = HomeSectionD.objects.get(section_type="callout")
        self.assertEqual(secao_chamada.callout_id, 1)
        self.assertEqual(secao_chamada.callout.steps.count(), 1)
        secao_sobre = HomeSectionD.objects.get(section_type="about")
        self.assertEqual(secao_sobre.about_id, 1)
        self.assertEqual(secao_sobre.about.badges.count(), 1)
        self.assertEqual(
            set(HomeSectionD.objects.get(internal_name="Destaques").translations.values_list("language", flat=True)),
            {"pt", "fr"},
        )
        self.assertEqual(
            set(HomeAboutD.objects.get(pk=1).translations.values_list("language", flat=True)), {"pt", "en"}
        )
        self.assertEqual(HomeCalloutD.objects.get(pk=1).internal_name, "Chamada final")
        self.assertEqual(
            list(HomeSectionD.objects.order_by("sort_order", "id").values_list("sort_order", flat=True)),
            [0, 10, 20, 30, 40, 50],
        )

        # Reverter só a 0010: os vínculos são soltos ANTES de apagar as seções
        # de bloco (FKs em cascata), e nada de conteúdo some.
        apps_volta = self.migrar(self.schema)
        HomeSectionV = apps_volta.get_model("home", "HomeSection")
        self.assertEqual(
            list(HomeSectionV.objects.order_by("sort_order", "id").values_list("section_type", "internal_name")),
            [("newest", "Novidades"), ("featured", "Destaques")],
        )
        self.assertEqual(apps_volta.get_model("home", "HomeCard").objects.count(), 3)
        self.assertEqual(set(apps_volta.get_model("home", "HomeCard").objects.values_list("section_id", flat=True)), {None})
        self.assertEqual(apps_volta.get_model("home", "HomeCategoryCard").objects.count(), 2)
        self.assertEqual(apps_volta.get_model("home", "HomeCallout").objects.get(pk=1).steps.count(), 1)
        self.assertEqual(apps_volta.get_model("home", "HomeAbout").objects.get(pk=1).badges.count(), 1)
        self.assertEqual(apps_volta.get_model("home", "HomeCardTranslation").objects.count(), 6)

        # E a 0010 de novo produz a mesma composição.
        apps_de_novo = self.migrar(self.depois)
        self.assertEqual(
            list(apps_de_novo.get_model("home", "HomeSection").objects.order_by("sort_order", "id").values_list("section_type", flat=True)),
            ["category_cards", "newest", "featured", "how_we_work", "callout", "about"],
        )

    def test_an_empty_installation_gets_no_sections(self):
        self.migrar(self.antes)

        apps_depois = self.migrar(self.depois)

        self.assertEqual(apps_depois.get_model("home", "HomeSection").objects.count(), 0)
