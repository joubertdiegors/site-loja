"""Blocos da Home administráveis, e o nome público da vitrine — etapa 16.

Três coisas saíram do HTML: os cards com ícone, a chamada final e a
obrigatoriedade do título do banner. E o nome público de "Modelos" passou a vir
da categoria — que já era traduzível e já era do Admin.

O que estes testes guardam é o estado **intermediário**: um administrador
configura por partes, e cada meio-caminho tem de ser uma página apresentável.
"""

from django.test import TestCase

from apps.categories.models import CategoryTranslation
from apps.core.testing import LanguageResetMixin, make_category, make_product
from apps.home.models import (
    CtaTarget,
    HomeBanner,
    HomeBannerTranslation,
    HomeCallout,
    HomeCalloutTranslation,
    HomeCard,
    HomeCardTranslation,
)

HOME = "/"


class HomeBlocksBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.modelos = make_category(slug="modelos", name="Modelos")

    def html(self, url=HOME):
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()

    def card(self, nome="Card", titulo="Produção própria", texto="", **campos):
        traducoes = {
            idioma: campos.pop(idioma) for idioma in ("fr", "nl", "en") if idioma in campos
        }
        card = HomeCard.objects.create(internal_name=nome, **campos)
        HomeCardTranslation.objects.create(
            master=card, language="pt", title=titulo, text=texto
        )
        for idioma, valores in traducoes.items():
            HomeCardTranslation.objects.create(master=card, language=idioma, **valores)
        return card

    def callout(self, **campos):
        traducoes = {
            idioma: campos.pop(idioma)
            for idioma in ("pt", "fr", "nl", "en")
            if idioma in campos
        }
        chamada = HomeCallout.load()
        for campo, valor in campos.items():
            setattr(chamada, campo, valor)
        chamada.save()
        for idioma, valores in traducoes.items():
            HomeCalloutTranslation.objects.update_or_create(
                master=chamada, language=idioma, defaults=valores
            )
        chamada.refresh_translations()
        return chamada


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class HomeCardTests(HomeBlocksBase):
    def test_the_default_cards_show_without_configuration(self):
        """Uma instalação recém-migrada não abre com um buraco na Home."""
        html = self.html()

        self.assertIn("Produção própria", html)
        self.assertIn("Personalização real", html)

    def test_a_registered_card_replaces_the_defaults(self):
        self.card(titulo="Entrega rápida", texto="Sai em 48 h.")

        html = self.html()

        self.assertIn("Entrega rápida", html)
        self.assertIn("Sai em 48 h.", html)
        self.assertNotIn("Personalização real", html)

    def test_an_inactive_card_disappears(self):
        self.card(nome="Vivo", titulo="Aparece")
        self.card(nome="Morto", titulo="Some", is_active=False)

        html = self.html()

        self.assertIn("Aparece", html)
        self.assertNotIn("Some", html)

    def test_the_order_is_respected(self):
        self.card(nome="B", titulo="Segundo", sort_order=2)
        self.card(nome="A", titulo="Primeiro", sort_order=1)

        html = self.html()

        self.assertLess(html.index("Primeiro"), html.index("Segundo"))

    def test_the_quantity_is_not_fixed_at_three(self):
        """Era o HTML que tinha três, não uma regra."""
        for numero in range(5):
            self.card(nome=f"Card {numero}", titulo=f"Serviço {numero}", sort_order=numero)

        html = self.html()

        for numero in range(5):
            with self.subTest(numero=numero):
                self.assertIn(f"Serviço {numero}", html)

    def test_a_card_without_text_shows_only_the_title(self):
        self.card(titulo="Só o título")

        html = self.html()

        self.assertIn("Só o título", html)
        self.assertNotIn("None", html)

    def test_a_card_without_title_is_not_drawn(self):
        """Um card com ícone e nada escrito é uma caixa vazia."""
        HomeCard.objects.create(internal_name="Vazio")

        html = self.html()

        self.assertNotIn("None", html)
        self.assertNotIn("Produção própria", html)  # os padrões saíram de cena

    def test_deactivating_every_card_removes_the_section(self):
        """Sem isto, desativar o último card faria os padrões voltarem."""
        self.card(nome="Único", titulo="Só este", is_active=False)

        html = self.html()

        self.assertNotIn("Só este", html)
        self.assertNotIn("Produção própria", html)
        self.assertNotIn("titulo-como", html)

    def test_the_icon_and_accent_come_from_the_record(self):
        self.card(titulo="Envio", icon="truck", accent="cyan")

        html = self.html()

        self.assertIn("bg-cyan-100", html)
        self.assertIn("text-cyan-700", html)

    def test_the_accent_classes_survive_the_css_build(self):
        """Classe montada no template (`bg-{{ accent }}-100`) o Tailwind não vê."""
        with open("static/css/tailwind.css", encoding="utf-8") as arquivo:
            css = arquivo.read()

        for classe in ("bg-brand-100", "bg-cyan-100", "bg-magenta-100",
                       "text-brand-700", "text-cyan-700", "text-magenta-700"):
            with self.subTest(classe=classe):
                self.assertIn(f".{classe}", css)

    def test_cards_are_translated_with_fallback(self):
        self.card(
            titulo="Produção própria",
            fr={"title": "Production maison", "text": ""},
        )

        self.assertIn("Production maison", self.html("/fr/"))
        self.assertIn("Produção própria", self.html("/nl/"))


# ---------------------------------------------------------------------------
# Chamada final
# ---------------------------------------------------------------------------


class HomeCalloutTests(HomeBlocksBase):
    def test_the_default_shows_without_configuration(self):
        self.assertIn("Tem uma ideia? A gente imprime.", self.html())

    def test_a_registered_callout_replaces_the_default(self):
        self.callout(pt={"title": "Peça o seu projeto", "text": "Conte o que precisa."})

        html = self.html()

        self.assertIn("Peça o seu projeto", html)
        self.assertIn("Conte o que precisa.", html)
        self.assertNotIn("Tem uma ideia? A gente imprime.", html)

    def test_deactivating_hides_the_block(self):
        """Desativar é uma decisão — não "volte ao padrão".

        Sem essa distinção o administrador não teria como **remover** a faixa:
        desligá-la faria o texto de fábrica voltar.
        """
        self.callout(pt={"title": "Do Admin"})
        self.assertIn("Do Admin", self.html())

        chamada = HomeCallout.load()
        chamada.is_active = False
        chamada.save()

        html = self.html()
        self.assertNotIn("Do Admin", html)
        self.assertNotIn("Tem uma ideia? A gente imprime.", html)

    def test_an_empty_callout_falls_back_instead_of_drawing_a_dark_stripe(self):
        """Faixa escura vazia no fim da página é pior que faixa nenhuma."""
        HomeCallout.load()  # existe, ativa, sem tradução nenhuma

        html = self.html()

        self.assertIn("Tem uma ideia? A gente imprime.", html)
        self.assertNotIn("None", html)

    def test_merely_opening_the_admin_screen_does_not_change_the_home(self):
        """O Admin cria a linha só para redirecionar para ela.

        Se "a linha existe" contasse como "alguém configurou", a chamada final
        sumiria da Home porque alguém *olhou* a tela.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()
        User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(User.objects.get(username="ana"))
        self.client.get("/admin/home/homecallout/")
        self.client.logout()

        self.assertEqual(HomeCallout.objects.count(), 1)
        self.assertIn("Tem uma ideia? A gente imprime.", self.html())

    def test_only_a_title_is_enough(self):
        self.callout(pt={"title": "Só um título"})

        html = self.html()

        self.assertIn("Só um título", html)
        self.assertNotIn("None", html)

    def test_without_a_cta_no_button_is_drawn(self):
        self.callout(pt={"title": "Sem botão"})

        bloco = self.html().rsplit("Sem botão", 1)[1].split("</section>", 1)[0]

        self.assertNotIn("btn-primary", bloco)

    def test_a_cta_without_label_is_not_drawn(self):
        """Botão sem texto é um retângulo que ninguém sabe para que serve."""
        self.callout(
            cta_target=CtaTarget.URL,
            cta_url="/modelos/",
            pt={"title": "Com destino, sem rótulo"},
        )

        bloco = self.html().rsplit("Com destino, sem rótulo", 1)[1].split("</section>", 1)[0]

        self.assertNotIn("btn-primary", bloco)

    def test_a_complete_cta_is_drawn(self):
        self.callout(
            cta_target=CtaTarget.URL,
            cta_url="/modelos/",
            pt={"title": "Com botão", "cta_label": "Ver o catálogo"},
        )

        html = self.html()

        self.assertIn("Ver o catálogo", html)
        self.assertIn('href="/modelos/"', html)

    def test_it_is_translated_with_fallback(self):
        self.callout(
            pt={"title": "Tem uma ideia?"},
            fr={"title": "Vous avez une idée ?"},
        )

        self.assertIn("Vous avez une idée ?", self.html("/fr/"))
        self.assertIn("Tem uma ideia?", self.html("/nl/"))

    def test_there_is_only_one_row(self):
        HomeCallout.load()
        HomeCallout.load()

        self.assertEqual(HomeCallout.objects.count(), 1)


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


class BannerWithoutTitleTests(HomeBlocksBase):
    """§2 — um banner pode ser só arte."""

    def banner(self, com_imagem=True, **traducoes):
        banner = HomeBanner.objects.create(
            internal_name="Campanha",
            image_desktop="banners/arte.jpg" if com_imagem else "",
        )
        for idioma, valores in traducoes.items():
            HomeBannerTranslation.objects.create(master=banner, language=idioma, **valores)
        return banner

    def test_a_banner_can_exist_without_any_translation(self):
        """A constraint do banco exigia título; um banner de arte não tem."""
        banner = self.banner()

        self.assertEqual(banner.translations.count(), 0)
        self.assertEqual(banner.title, "")
        self.assertFalse(banner.has_text)

    def test_a_translation_row_with_an_empty_title_is_allowed(self):
        banner = self.banner(pt={"title": "", "image_alt": "Peça azul sobre a mesa"})

        self.assertEqual(banner.title, "")
        self.assertEqual(banner.image_alt, "Peça azul sobre a mesa")

    def test_an_image_only_banner_draws_no_overlay(self):
        """Sem isto, a tarja escura cobria justamente o pé da arte."""
        self.banner(pt={"title": "", "image_alt": "Arte"})

        html = self.html()

        self.assertIn("banners/arte.jpg", html)
        self.assertNotIn("bg-gradient-to-t from-brand-950/90", html)

    def test_an_image_only_banner_draws_no_empty_heading(self):
        self.banner(pt={"title": "", "image_alt": "Arte"})

        html = self.html()

        self.assertNotIn('class="font-display text-3xl font-bold tracking-[-0.025em] text-white', html)
        self.assertNotIn("None", html)

    def test_the_page_still_has_exactly_one_h1(self):
        """Página sem `<h1>` é página sem título para o buscador e o leitor."""
        self.banner(pt={"title": "", "image_alt": "Arte"})

        html = self.html()

        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('id="hero-titulo"', html)

    def test_a_banner_with_a_title_still_draws_the_overlay(self):
        self.banner(pt={"title": "Coleção de inverno"})

        html = self.html()

        self.assertIn("Coleção de inverno", html)
        self.assertIn("bg-gradient-to-t from-brand-950/90", html)

    def test_a_subtitle_without_a_title_still_shows(self):
        self.banner(pt={"title": "", "subtitle": "Novidades da semana"})

        html = self.html()

        self.assertIn("Novidades da semana", html)
        self.assertEqual(html.count("<h1"), 1)

    def test_a_cta_without_label_is_not_drawn(self):
        banner = self.banner(pt={"title": "Campanha", "cta_label": ""})
        banner.cta_target = CtaTarget.URL
        banner.cta_url = "/modelos/"
        banner.save()

        hero = self.html().split("<section", 2)[1]

        self.assertNotIn("btn-primary", hero)

    def test_the_alt_text_falls_back_to_the_title(self):
        self.banner(pt={"title": "Coleção de inverno"})

        self.assertIn('alt="Coleção de inverno"', self.html())


# ---------------------------------------------------------------------------
# Nome público da vitrine
# ---------------------------------------------------------------------------


class PublicShopNameTests(HomeBlocksBase):
    """§6 — o nome vem da categoria, e a URL não muda."""

    def test_the_default_name_comes_from_the_category(self):
        resposta = self.client.get("/modelos/")

        self.assertEqual(resposta.context["page_title"], "Modelos")

    def test_renaming_the_category_renames_the_shop(self):
        traducao = self.modelos.translations.get(language="pt")
        traducao.name = "Criações"
        traducao.save()
        self.modelos.refresh_translations()

        resposta = self.client.get("/modelos/")

        self.assertEqual(resposta.context["page_title"], "Criações")
        self.assertContains(resposta, "Criações")

    def test_the_url_does_not_change(self):
        """O slug manda na URL, e o nome público não é o slug."""
        traducao = self.modelos.translations.get(language="pt")
        traducao.name = "Criações"
        traducao.save()

        self.modelos.refresh_from_db()
        self.assertEqual(self.modelos.slug, "modelos")
        self.assertEqual(self.client.get("/modelos/").status_code, 200)

    def test_it_is_translated(self):
        CategoryTranslation.objects.create(master=self.modelos, language="fr", name="Modèles")
        CategoryTranslation.objects.create(master=self.modelos, language="nl", name="Modellen")
        self.modelos.refresh_translations()

        self.assertEqual(self.client.get("/fr/modelos/").context["page_title"], "Modèles")
        self.assertEqual(self.client.get("/nl/modelos/").context["page_title"], "Modellen")

    def test_it_falls_back_to_portuguese(self):
        self.assertEqual(self.client.get("/en/modelos/").context["page_title"], "Modelos")

    def test_the_subtitle_comes_from_the_category_description(self):
        traducao = self.modelos.translations.get(language="pt")
        traducao.description = "Peças decorativas impressas na Bélgica."
        traducao.save()
        self.modelos.refresh_translations()

        resposta = self.client.get("/modelos/")

        self.assertEqual(resposta.context["page_subtitle"], "Peças decorativas impressas na Bélgica.")

    def test_without_a_description_the_default_subtitle_is_used(self):
        resposta = self.client.get("/modelos/")

        self.assertEqual(
            resposta.context["page_subtitle"], "Descubra nossos modelos impressos em 3D"
        )

    def test_the_menu_already_used_the_category_name(self):
        """O cabeçalho nunca teve "Modelos" escrito — só a vitrine tinha."""
        traducao = self.modelos.translations.get(language="pt")
        traducao.name = "Criações"
        traducao.save()

        html = self.html()

        self.assertIn("Criações", html)

    def test_no_new_model_was_created_for_this(self):
        """Um segundo lugar para o mesmo texto seria dois para corrigir."""
        from django.apps import apps

        nomes = {model.__name__ for model in apps.get_app_config("storefront").get_models()}

        self.assertNotIn("ShopName", nomes)
        self.assertNotIn("StoreSettings", nomes)

    def test_a_product_page_still_works(self):
        """A troca de `page_title` para propriedade não pode quebrar o resto."""
        produto = make_product(sku="P1", name="Peça", category=self.modelos)

        self.assertEqual(self.client.get(produto.get_absolute_url()).status_code, 200)
