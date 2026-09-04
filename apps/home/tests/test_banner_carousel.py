"""O carrossel de banners da Home.

Com um banner ativo, nada muda: o hero de sempre, sem setas nem indicadores.
Com dois ou mais, os banners ativos viram slides, na ordem do cadastro, e a
configuração global (`HomeBannerCarousel`, uma linha só) diz como se roda.
Cada slide é o `hero.html` do seu desenho — a anatomia dos quatro não muda.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_category
from apps.home.models import (
    BannerLayout,
    CtaTarget,
    HomeBanner,
    HomeBannerCarousel,
    HomeBannerTranslation,
)

HOME = "/"


class CarouselBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")

    def banner(self, name, layout=BannerLayout.EDITORIAL, sort_order=0, is_active=True, **fields):
        banner = HomeBanner.objects.create(
            internal_name=name, layout=layout, sort_order=sort_order, is_active=is_active,
            cta_target=CtaTarget.CATEGORY, cta_category=self.category,
        )
        HomeBannerTranslation.objects.create(
            master=banner, language="pt", title=fields.pop("title", name), **fields
        )
        return banner

    def html(self, url=HOME):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class NoOrOneBannerTests(CarouselBase):
    def test_no_banner_no_carousel(self):
        html = self.html()

        self.assertNotIn("data-banner-carousel", html)
        self.assertIn("Espaço reservado para o banner", html)

    def test_one_banner_renders_the_plain_hero(self):
        self.banner("Único")

        html = self.html()

        self.assertNotIn("data-banner-carousel", html)
        self.assertNotIn("hero-carousel", html)
        self.assertNotIn("data-banner-dot", html)
        self.assertIn('class="hero-editorial"', html)
        self.assertEqual(html.count("<h1"), 1)

    def test_one_active_among_inactive_is_still_plain(self):
        self.banner("Ativo", sort_order=1)
        self.banner("Desligado", sort_order=2, is_active=False)
        self.banner("Desligado 2", sort_order=3, is_active=False)

        html = self.html()

        self.assertNotIn("data-banner-carousel", html)
        self.assertIn("Ativo", html)
        self.assertNotIn("Desligado", html)


class TwoOrMoreBannersTests(CarouselBase):
    def test_two_banners_make_a_carousel_with_both_slides(self):
        self.banner("Primeiro", sort_order=1)
        self.banner("Segundo", sort_order=2)

        html = self.html()

        self.assertIn("data-banner-carousel", html)
        self.assertEqual(html.count("data-banner-slide"), 2)
        self.assertIn("Primeiro", html)
        self.assertIn("Segundo", html)
        self.assertEqual(html.count('class="hero-carousel-dot focus-ring"'), 2)
        self.assertIn("data-banner-prev", html)
        self.assertIn("data-banner-next", html)

    def test_only_the_first_slide_is_active_and_the_others_are_inert(self):
        self.banner("Primeiro", sort_order=1)
        self.banner("Segundo", sort_order=2)
        self.banner("Terceiro", sort_order=3)

        html = self.html()
        slides = html.split("data-banner-slide")[1:]

        self.assertEqual(len(slides), 3)
        self.assertIn("data-active", slides[0].split(">", 1)[0] + slides[0][:400])
        self.assertNotIn(" inert", slides[0][:400])
        for slide in slides[1:]:
            with self.subTest(slide=slide[:60]):
                self.assertIn("inert", slide[:400])
                self.assertNotIn("data-active", slide[:400])

    def test_the_order_is_the_sort_order(self):
        self.banner("B", sort_order=2)
        self.banner("C", sort_order=3)
        self.banner("A", sort_order=1)

        html = self.html()

        self.assertLess(html.index("Banner 1 de 3"), html.index("Banner 2 de 3"))
        primeiro = html.split("data-banner-slide", 2)[1]
        self.assertIn(">A<", primeiro.replace("\n", "").replace(" ", ""))

    def test_inactive_banners_are_not_slides(self):
        self.banner("Ativo 1", sort_order=1)
        self.banner("Escondido", sort_order=2, is_active=False)
        self.banner("Ativo 2", sort_order=3)

        html = self.html()

        self.assertEqual(html.count("data-banner-slide"), 2)
        self.assertNotIn("Escondido", html)

    def test_only_the_first_slide_has_the_h1_and_the_hero_id(self):
        self.banner("Primeiro", sort_order=1)
        self.banner("Segundo", sort_order=2, layout=BannerLayout.POSTER_POP)
        self.banner("Terceiro", sort_order=3, layout=BannerLayout.BENTO)

        html = self.html()

        self.assertEqual(html.count("<h1"), 1)
        self.assertEqual(html.count('id="hero-titulo"'), 1)
        self.assertIn('id="hero-titulo-2"', html)
        self.assertIn('id="hero-titulo-3"', html)
        self.assertEqual(html.count("<h2 id=\"hero-titulo-"), 2)

    def test_the_four_layouts_keep_their_anatomy_as_slides(self):
        self.banner("Editorial", sort_order=1, layout=BannerLayout.EDITORIAL)
        self.banner("Poster", sort_order=2, layout=BannerLayout.POSTER_POP, badge_mint="+120 cores")
        self.banner("Bento", sort_order=3, layout=BannerLayout.BENTO, rating_value="4.9")
        HomeBanner.objects.create(
            internal_name="Cheia", layout=BannerLayout.FULL_IMAGE, sort_order=4,
            image_desktop="banners/arte.jpg",
        )

        html = self.html()

        self.assertEqual(html.count("data-banner-slide"), 4)
        self.assertIn('class="hero-editorial"', html)
        self.assertIn("hero-plate", html)
        self.assertIn('class="hero-poster"', html)
        self.assertIn("hero-poster-tile", html)
        self.assertIn("hero-bento-main", html)
        self.assertIn("hero-bento-rating", html)
        self.assertIn("banners/arte.jpg", html)

    def test_hidden_slides_load_their_photos_lazily(self):
        HomeBanner.objects.create(internal_name="A", layout=BannerLayout.EDITORIAL, sort_order=1,
                                  image_desktop="banners/a.jpg")
        HomeBanner.objects.create(internal_name="B", layout=BannerLayout.BENTO, sort_order=2,
                                  image_desktop="banners/b.jpg")

        html = self.html()
        primeiro, segundo = html.split("data-banner-slide")[1:3]

        self.assertIn('fetchpriority="high"', primeiro)
        self.assertNotIn('fetchpriority="high"', segundo)
        self.assertIn('loading="lazy"', segundo)

    def test_the_slides_are_labelled_for_screen_readers(self):
        self.banner("Primeiro", sort_order=1)
        self.banner("Segundo", sort_order=2)

        html = self.html()

        self.assertIn('aria-roledescription="carrossel"', html)
        self.assertIn('aria-label="Destaques"', html)
        self.assertIn('aria-label="Banner 1 de 2"', html)
        self.assertIn('aria-label="Banner anterior"', html)
        self.assertIn('aria-label="Próximo banner"', html)
        self.assertIn('aria-label="Ir para o banner 2"', html)
        primeiro_ponto = html.split('class="hero-carousel-dot focus-ring"', 2)[1]
        self.assertIn('aria-current="true"', primeiro_ponto)
        segundo_ponto = html.split('class="hero-carousel-dot focus-ring"', 2)[2].split(">", 1)[0]
        self.assertNotIn("aria-current", segundo_ponto)

    def test_it_is_translated(self):
        for name in ("Primeiro", "Segundo"):
            banner = self.banner(name, sort_order=1 if name == "Primeiro" else 2)
            HomeBannerTranslation.objects.create(master=banner, language="fr", title=f"{name} FR")

        for prefixo, esperados in (
            ("/fr", ("Bannière précédente", "Bannière suivante", "Bannière 1 sur 2", "Primeiro FR")),
            ("/nl", ("Vorige banner", "Volgende banner", "Banner 1 van 2")),
            ("/en", ("Previous banner", "Next banner", "Banner 1 of 2", "Go to banner 2")),
        ):
            with self.subTest(idioma=prefixo):
                html = self.html(f"{prefixo}/")
                for texto in esperados:
                    self.assertIn(texto, html)


class CarouselSettingsTests(CarouselBase):
    def setUp(self):
        super().setUp()
        self.banner("Primeiro", sort_order=1)
        self.banner("Segundo", sort_order=2)

    def test_defaults_without_a_row(self):
        self.assertEqual(HomeBannerCarousel.objects.count(), 0)

        html = self.html()

        self.assertIn('data-autoplay="false"', html)
        self.assertIn('data-interval="5000"', html)
        self.assertIn('data-pause-hover="true"', html)
        self.assertIn('data-pause-interaction="true"', html)
        self.assertIn("data-banner-prev", html)
        self.assertIn("data-banner-dots", html)
        # Ler a Home não cria a linha.
        self.assertEqual(HomeBannerCarousel.objects.count(), 0)

    def test_autoplay_and_interval_reach_the_javascript(self):
        HomeBannerCarousel.objects.create(autoplay=True, interval_seconds=8)

        html = self.html()

        self.assertIn('data-autoplay="true"', html)
        self.assertIn('data-interval="8000"', html)

    def test_arrows_and_dots_can_be_hidden(self):
        HomeBannerCarousel.objects.create(show_arrows=False, show_dots=False)

        html = self.html()

        self.assertIn("data-banner-carousel", html)
        self.assertNotIn("data-banner-prev", html)
        self.assertNotIn("data-banner-dots", html)

    def test_pause_options_reach_the_javascript(self):
        HomeBannerCarousel.objects.create(pause_on_hover=False, pause_on_interaction=False)

        html = self.html()

        self.assertIn('data-pause-hover="false"', html)
        self.assertIn('data-pause-interaction="false"', html)

    def test_it_is_a_singleton(self):
        primeiro = HomeBannerCarousel.load()
        primeiro.autoplay = True
        primeiro.interval_seconds = 7
        primeiro.save()
        # Gravar por outro caminho continua caindo na mesma linha.
        outro = HomeBannerCarousel.objects.get(pk=1)
        outro.pk = 99
        outro.show_arrows = False
        outro.save()

        self.assertEqual(HomeBannerCarousel.objects.count(), 1)
        self.assertEqual(primeiro.pk, 1)
        atual = HomeBannerCarousel.load()
        self.assertTrue(atual.autoplay)
        self.assertEqual(atual.interval_seconds, 7)
        self.assertFalse(atual.show_arrows)
        self.assertEqual(HomeBannerCarousel.current().pk, 1)

    def test_the_interval_is_bounded(self):
        for valor in (1, 31, 0):
            with self.subTest(valor=valor):
                with self.assertRaises(ValidationError):
                    HomeBannerCarousel(interval_seconds=valor).full_clean()
        for valor in (2, 5, 30):
            with self.subTest(valor=valor):
                HomeBannerCarousel(interval_seconds=valor).full_clean()

    def test_the_settings_do_not_render_with_a_single_banner(self):
        HomeBanner.objects.filter(internal_name="Segundo").update(is_active=False)
        HomeBannerCarousel.objects.create(autoplay=True)

        html = self.html()

        self.assertNotIn("data-banner-carousel", html)
        self.assertNotIn("data-autoplay", html)


class CarouselAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin4", email="admin4@jdprint.test", password="senha-de-teste"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_changelist_goes_straight_to_the_single_row(self):
        response = self.client.get(reverse("admin:home_homebannercarousel_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(HomeBannerCarousel.objects.count(), 1)
        self.assertIn("/1/change/", response["Location"])

    def test_the_form_has_the_options_and_help(self):
        response = self.client.get(reverse("admin:home_homebannercarousel_change", args=[HomeBannerCarousel.load().pk]))

        self.assertEqual(response.status_code, 200)
        for campo in ("autoplay", "interval_seconds", "show_arrows", "show_dots",
                      "pause_on_hover", "pause_on_interaction"):
            with self.subTest(campo=campo):
                self.assertContains(response, f'name="{campo}"')
        self.assertContains(response, "ROTAÇÃO")
        self.assertContains(response, "CONTROLES")
        self.assertContains(response, "prefers-reduced-motion")

    def test_an_absurd_interval_is_refused(self):
        row = HomeBannerCarousel.load()
        response = self.client.post(
            reverse("admin:home_homebannercarousel_change", args=[row.pk]),
            {"autoplay": "on", "interval_seconds": "90", "show_arrows": "on", "show_dots": "on",
             "pause_on_hover": "on", "pause_on_interaction": "on"},
        )

        self.assertEqual(response.status_code, 200)  # o formulário volta com o erro
        self.assertContains(response, "30")
        row.refresh_from_db()
        self.assertEqual(row.interval_seconds, 5)

    def test_cannot_add_or_delete(self):
        self.assertEqual(self.client.get(reverse("admin:home_homebannercarousel_add")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin:home_homebannercarousel_delete", args=[HomeBannerCarousel.load().pk])).status_code,
            403,
        )
