"""Os dois desenhos novos do banner: Poster Pop e Bento Criativo.

A anatomia é a de `Banner - modelos.html`: o poster roxo com o texto centrado,
a palavra marcada em amarelo, dois selos e três quadros na base; e a grade de
cartões com o texto no branco, a foto no menta e os dois cartões pequenos
(cores e avaliação). Tudo vem do cadastro, por idioma.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_category
from apps.home.models import BannerLayout, CtaTarget, HomeBanner, HomeBannerTranslation

HOME = "/"

POSTER_PT = {
    "eyebrow": "Impressão 3D criativa",
    "title": "Do arquivo à sua mesa, camada por camada.",
    "title_highlight": "camada",
    "subtitle": "Modelos decorativos e filamentos.",
    "cta_label": "Ver produtos",
    "cta_secondary_label": "Pedir orçamento",
    "perk_1": "Qualidade", "perk_2": "Cores", "perk_3": "Envio rápido",
    "badge_mint": "+120 cores", "badge_white": "★ 4.9 · 300+ pedidos",
    "image_alt": "Peça em destaque",
    "image_tile_left_alt": "Modelo decorativo",
    "image_tile_right_alt": "Brinquedo",
}

BENTO_PT = {
    "eyebrow": "Impressão 3D criativa",
    "title": "Imprimimos o que você imagina.",
    "title_highlight": "imagina",
    "subtitle": "Com a cor e o acabamento que você escolher.",
    "cta_label": "Ver produtos",
    "cta_secondary_label": "Pedir orçamento",
    "perk_1": "Qualidade", "perk_2": "Cores", "perk_3": "Envio rápido",
    "badge_coral": "Feito na Bélgica", "badge_yellow": "PLA · 0.12 mm",
    "badge_mint": "+120 cores", "colors_note": "PLA · PETG · TPU",
    "rating_value": "4.9", "rating_note": "300+ pedidos entregues",
}


class BannerLayoutBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")

    def banner(self, layout, translations, **kwargs):
        banner = HomeBanner.objects.create(
            internal_name="Banner",
            layout=layout,
            cta_target=CtaTarget.CATEGORY,
            cta_category=self.category,
            cta_secondary_url="/contato/",
            **kwargs,
        )
        for language, fields in translations.items():
            HomeBannerTranslation.objects.create(master=banner, language=language, **fields)
        return banner

    def html(self, url=HOME):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class LayoutChoicesTests(TestCase):
    def test_the_four_layouts_exist(self):
        self.assertEqual(
            [choice[0] for choice in BannerLayout.choices],
            ["editorial", "full_image", "poster_pop", "bento_criativo"],
        )

    def test_the_admin_names_are_the_agreed_ones(self):
        rotulos = dict(BannerLayout.choices)

        self.assertIn("Poster Pop", rotulos["poster_pop"])
        self.assertIn("Bento Criativo", rotulos["bento_criativo"])
        self.assertIn("Hero editorial", rotulos["editorial"])
        self.assertIn("Imagem completa", rotulos["full_image"])


class PosterPopTests(BannerLayoutBase):
    def test_it_renders_the_poster_and_nothing_of_the_other_designs(self):
        self.banner(BannerLayout.POSTER_POP, {"pt": POSTER_PT})

        html = self.html()

        self.assertIn('class="hero-poster"', html)
        self.assertNotIn("hero-editorial", html)
        self.assertNotIn("hero-bento", html)
        self.assertNotIn("bg-gradient-to-t from-brand-950/90", html)
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('id="hero-titulo"', html)

    def test_the_highlighted_word_is_the_yellow_mark(self):
        self.banner(BannerLayout.POSTER_POP, {"pt": POSTER_PT})

        html = self.html()
        titulo = html.split('class="hero-poster-title"', 1)[1].split("</h1>", 1)[0]

        self.assertIn("Do arquivo à sua mesa, <em>camada</em> por camada.", titulo)

    def test_the_content_comes_from_the_record(self):
        self.banner(BannerLayout.POSTER_POP, {"pt": POSTER_PT})

        html = self.html()

        for texto in ("Impressão 3D criativa", "Modelos decorativos e filamentos.",
                      "Ver produtos", "Pedir orçamento", "/contato/",
                      "Qualidade", "Cores", "Envio rápido",
                      "+120 cores", "★ 4.9 · 300+ pedidos"):
            with self.subTest(texto=texto):
                self.assertIn(texto, html)
        self.assertIn(self.category.get_absolute_url(), html)
        self.assertIn("hero-btn-yellow", html)
        self.assertIn("hero-btn-ghost-light", html)
        self.assertIn("hero-poster-sticker-mint", html)
        self.assertIn("hero-poster-sticker-white", html)

    def test_the_base_always_has_three_tiles(self):
        """Sem foto o quadro fica tracejado: a faixa de três é a composição."""
        banner = self.banner(BannerLayout.POSTER_POP, {"pt": POSTER_PT})

        html = self.html()
        self.assertEqual(html.count('class="hero-poster-tile"'), 3)
        self.assertEqual(html.count("hero-poster-photo"), 0)
        self.assertEqual(len(banner.tiles), 3)

    def test_the_middle_tile_is_the_main_image_and_the_sides_have_their_own_alt(self):
        banner = self.banner(
            BannerLayout.POSTER_POP, {"pt": POSTER_PT},
            image_tile_left="banners/esq.jpg", image_desktop="banners/meio.jpg",
            image_tile_right="banners/dir.jpg",
        )

        html = self.html()
        quadros = [t["image"].name for t in banner.tiles]

        self.assertEqual(quadros, ["banners/esq.jpg", "banners/meio.jpg", "banners/dir.jpg"])
        self.assertEqual(html.count("hero-poster-photo"), 3)
        self.assertIn('alt="Modelo decorativo"', html)
        self.assertIn('alt="Peça em destaque"', html)
        self.assertIn('alt="Brinquedo"', html)

    def test_empty_stickers_and_perks_are_not_drawn(self):
        fields = {k: v for k, v in POSTER_PT.items() if not k.startswith(("badge_", "perk_"))}
        self.banner(BannerLayout.POSTER_POP, {"pt": fields})

        html = self.html()

        self.assertNotIn("hero-poster-sticker", html)
        self.assertNotIn("hero-poster-perks", html)

    def test_it_is_translated(self):
        self.banner(
            BannerLayout.POSTER_POP,
            {
                "pt": POSTER_PT,
                "fr": {**POSTER_PT, "title": "Du fichier à votre table, couche après couche.",
                       "title_highlight": "couche", "badge_mint": "+120 couleurs"},
            },
        )

        html = self.html("/fr/")

        self.assertIn("Du fichier à votre table, <em>couche</em> après couche.", html)
        self.assertIn("+120 couleurs", html)


class BentoTests(BannerLayoutBase):
    def test_it_renders_the_grid_of_cards(self):
        self.banner(BannerLayout.BENTO, {"pt": BENTO_PT})

        html = self.html()

        for classe in ("hero-bento-main", "hero-bento-photo", "hero-bento-split",
                       "hero-bento-colors", "hero-bento-rating"):
            with self.subTest(classe=classe):
                self.assertIn(classe, html)
        self.assertNotIn("hero-editorial", html)
        self.assertNotIn("hero-poster", html)
        self.assertEqual(html.count("<h1"), 1)

    def test_the_highlighted_word_and_the_stickers(self):
        self.banner(BannerLayout.BENTO, {"pt": BENTO_PT})

        html = self.html()
        titulo = html.split('class="hero-bento-title"', 1)[1].split("</h1>", 1)[0]

        self.assertIn("Imprimimos o que você <em>imagina</em>.", titulo)
        self.assertIn("Feito na Bélgica", html)
        self.assertIn("hero-bento-sticker-coral", html)
        self.assertIn("PLA · 0.12 mm", html)
        self.assertIn("hero-bento-sticker-yellow", html)

    def test_the_small_cards_carry_their_texts(self):
        self.banner(BannerLayout.BENTO, {"pt": BENTO_PT})

        html = self.html()
        cores = html.split('class="hero-bento-card hero-bento-colors"', 1)[1].split("</div>", 1)[0]
        avaliacao = html.split('class="hero-bento-card hero-bento-rating"', 1)[1].split("</div>", 1)[0]

        self.assertIn("<b>+120 cores</b>", cores)
        self.assertIn("PLA · PETG · TPU", cores)
        self.assertEqual(cores.count("<i "), 8)
        self.assertIn("<b>4.9</b>", avaliacao)
        self.assertIn("300+ pedidos entregues", avaliacao)
        self.assertIn("★★★★★", avaliacao)

    def test_a_small_card_without_its_text_disappears(self):
        fields = {k: v for k, v in BENTO_PT.items() if k not in ("rating_value", "rating_note")}
        self.banner(BannerLayout.BENTO, {"pt": fields})

        html = self.html()

        self.assertIn("hero-bento-colors", html)
        self.assertNotIn("hero-bento-rating", html)

    def test_without_both_small_cards_the_row_is_not_drawn(self):
        fields = {k: v for k, v in BENTO_PT.items()
                  if k not in ("rating_value", "rating_note", "badge_mint", "colors_note")}
        self.banner(BannerLayout.BENTO, {"pt": fields})

        self.assertNotIn("hero-bento-split", self.html())

    def test_the_photo_card_uses_the_main_image(self):
        self.banner(BannerLayout.BENTO, {"pt": {**BENTO_PT, "image_alt": "Dragão roxo"}},
                    image_desktop="banners/dragao.jpg")

        html = self.html()

        self.assertIn('class="hero-bento-img"', html)
        self.assertIn("banners/dragao.jpg", html)
        self.assertIn('alt="Dragão roxo"', html)

    def test_it_is_translated(self):
        self.banner(
            BannerLayout.BENTO,
            {"pt": BENTO_PT, "en": {**BENTO_PT, "title": "We print what you imagine.",
                                    "title_highlight": "imagine", "badge_coral": "Made in Belgium"}},
        )

        html = self.html("/en/")

        self.assertIn("We print what you <em>imagine</em>.", html)
        self.assertIn("Made in Belgium", html)


class ExistingDesignsUntouchedTests(BannerLayoutBase):
    def test_the_editorial_hero_still_renders_as_before(self):
        self.banner(BannerLayout.EDITORIAL, {"pt": POSTER_PT})

        html = self.html()

        self.assertIn('class="hero-editorial"', html)
        self.assertIn("hero-plate", html)
        self.assertNotIn("hero-poster", html)
        self.assertNotIn("hero-bento", html)

    def test_only_the_first_active_banner_shows(self):
        self.banner(BannerLayout.EDITORIAL, {"pt": {"title": "Editorial ativo"}}, sort_order=1)
        self.banner(BannerLayout.POSTER_POP, {"pt": {"title": "Poster inativo"}},
                    sort_order=2, is_active=False)

        html = self.html()

        self.assertIn("Editorial ativo", html)
        self.assertNotIn("Poster inativo", html)
        self.assertNotIn("hero-poster", html)


class BannerAdminLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin3", email="admin3@jdprint.test", password="senha-de-teste"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_add_page_offers_the_four_designs_and_the_new_fields(self):
        response = self.client.get(reverse("admin:home_homebanner_add"))

        for rotulo in ("Hero editorial", "Imagem completa", "Poster Pop", "Bento Criativo"):
            with self.subTest(rotulo=rotulo):
                self.assertContains(response, rotulo)
        for campo in ("image_tile_left", "image_tile_right", "badge_coral", "colors_note",
                      "rating_value", "rating_note", "image_tile_left_alt"):
            with self.subTest(campo=campo):
                self.assertContains(response, f'name="{campo}"' if "tile_" in campo and "alt" not in campo
                                    else campo)
        self.assertContains(response, "QUADROS LATERAIS")
        self.assertContains(response, "home_banner_admin.js")

    def test_creating_a_poster_pop_banner(self):
        from apps.home.tests.test_admin import inline_payload

        payload = {
            "internal_name": "Poster",
            "layout": "poster_pop",
            "is_active": "on",
            "sort_order": "1",
            "cta_target": CtaTarget.NONE,
            "cta_category": "", "cta_product": "", "cta_url": "", "cta_secondary_url": "",
            "plate_color": "purple", "frame_color": "lavender", "surface_color": "cream",
        }
        payload.update(inline_payload("translations", [{
            "language": "pt", "title": "Camada por camada", "title_highlight": "Camada",
            "subtitle": "", "cta_label": "", "image_alt": "", "badge_mint": "+120 cores",
            "id": "", "master": "",
        }]))

        self.client.post(reverse("admin:home_homebanner_add"), payload, follow=True)

        banner = HomeBanner.objects.get(internal_name="Poster")
        self.assertEqual(banner.layout, BannerLayout.POSTER_POP)
        self.assertTrue(banner.is_poster)
        self.assertEqual(banner.badge_mint, "+120 cores")
