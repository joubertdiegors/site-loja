"""O carrossel de banners num navegador de verdade.

Os testes de ``test_banner_carousel.py`` olham o HTML que o servidor manda. Só
que "clicar em PRÓXIMO não muda o banner" aconteceu com esse HTML certo: o
navegador estava com um ``app.js`` antigo em cache. Um teste que lê o HTML não
enxerga isso. Estes abrem a Home num Chromium (o Edge ou o Chrome da máquina,
ou o Chromium do Playwright), clicam e olham o estado real da página: qual
slide está visível, o que ficou ``inert``, se a rotação anda sozinha.

Sem o pacote ``playwright`` (requirements/dev.txt) ou sem navegador, os testes
são pulados — não falham. ``manage.py test --exclude-tag browser`` os deixa de
fora de propósito.
"""

import os
import unittest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag

from apps.core.testing import LanguageResetMixin, make_category
from apps.home.models import (
    BannerLayout,
    CtaTarget,
    HomeBanner,
    HomeBannerCarousel,
    HomeBannerTranslation,
)

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:  # pragma: no cover - depende da máquina
    sync_playwright = None

#: Intervalo curto de propósito (o mínimo que o cadastro aceita).
INTERVAL_SECONDS = 2
INTERVAL_MS = INTERVAL_SECONDS * 1000
#: Larguras do roteiro de validação visual do projeto.
WIDTHS = (1500, 1100, 820, 640, 480)

STATE = """() => {
  const root = document.querySelector('[data-banner-carousel]');
  const slides = [...document.querySelectorAll('[data-banner-slide]')];
  const shown = s => getComputedStyle(s).visibility === 'visible' && getComputedStyle(s).opacity === '1';
  return {
    ready: !!root && root.hasAttribute('data-banner-ready'),
    n: slides.length,
    active: slides.findIndex(s => s.hasAttribute('data-active')),
    visible: slides.map(shown),
    inert: slides.map(s => s.hasAttribute('inert')),
    dot: [...document.querySelectorAll('[data-banner-dot]')].findIndex(d => d.getAttribute('aria-current') === 'true'),
    h1: document.querySelectorAll('h1').length,
    trackHeight: Math.round(document.querySelector('[data-banner-track]').getBoundingClientRect().height),
    slideHeights: slides.map(s => Math.round(s.firstElementChild.getBoundingClientRect().height)),
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    script: [...document.scripts].map(s => s.src).find(src => src.includes('/static/js/app.js')) || '',
  };
}"""

#: O slide ``index`` é o ativo e o fade (0,45s) terminou: só ele à vista.
ACTIVE_IS = """(index) => {
  const slides = [...document.querySelectorAll('[data-banner-slide]')];
  return slides.findIndex(s => s.hasAttribute('data-active')) === index
    && slides.every((s, i) => {
      const css = getComputedStyle(s);
      return i === index ? css.visibility === 'visible' && css.opacity === '1' : css.visibility === 'hidden';
    });
}"""

SWIPE_LEFT = """() => {
  const root = document.querySelector('[data-banner-carousel]');
  const at = (type, x) => new PointerEvent(type, {pointerType: 'touch', clientX: x, clientY: 300, bubbles: true});
  root.dispatchEvent(at('pointerdown', 300));
  root.dispatchEvent(at('pointerup', 120));
}"""


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def launch(playwright):
    """O Edge ou o Chrome instalados, senão o Chromium do Playwright."""
    for options in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return playwright.chromium.launch(headless=True, **options)
        except PlaywrightError:
            continue
    return None


@tag("browser")
@unittest.skipIf(sync_playwright is None, "playwright não instalado (requirements/dev.txt)")
class BannerCarouselBrowserTests(LanguageResetMixin, StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # O Playwright síncrono roda sobre um loop asyncio nesta thread, e o
        # Django recusa consultas ao banco "de dentro de um loop". É a variável
        # que a documentação do Playwright indica para testes Django; vale só
        # enquanto esta classe roda.
        anterior = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        cls.addClassCleanup(_restore_env, "DJANGO_ALLOW_ASYNC_UNSAFE", anterior)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = launch(cls.playwright)
        if cls.browser is None:
            raise unittest.SkipTest("nenhum navegador Chromium disponível para o Playwright")
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        for order, (name, layout) in enumerate(
            [
                ("Editorial", BannerLayout.EDITORIAL),
                ("Poster", BannerLayout.POSTER_POP),
                ("Bento", BannerLayout.BENTO),
            ]
        ):
            banner = HomeBanner.objects.create(
                internal_name=name, layout=layout, sort_order=order,
                cta_target=CtaTarget.CATEGORY, cta_category=self.category,
            )
            HomeBannerTranslation.objects.create(master=banner, language="pt", title=name)
        # Como no admin recém-instalado: rotação desligada, controles ligados.
        HomeBannerCarousel.objects.create(autoplay=False, interval_seconds=INTERVAL_SECONDS)
        self.errors = []

    # -- apoio -------------------------------------------------------------

    def autoplay_on(self):
        HomeBannerCarousel.objects.filter(pk=1).update(autoplay=True, interval_seconds=INTERVAL_SECONDS)

    def open_home(self, width=1500, touch=False, reduced_motion="no-preference"):
        context = self.browser.new_context(
            viewport={"width": width, "height": 900},
            has_touch=touch,
            locale="pt-BR",
            reduced_motion=reduced_motion,
        )
        self.addCleanup(context.close)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(f"pageerror: {error}"))
        page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        page.goto(self.live_server_url + "/")
        page.wait_for_selector("[data-banner-carousel][data-banner-ready]")
        # O cursor longe do carrossel: a pausa ao passar o mouse não entra por acidente.
        page.mouse.move(5, 5)
        return page

    def state(self, page):
        return page.evaluate(STATE)

    def wait_active(self, page, index, timeout=1500):
        """Espera o slide ``index`` ficar ativo e visível — ou falha com o estado real."""
        try:
            page.wait_for_function(ACTIVE_IS, arg=index, timeout=timeout)
        except PlaywrightError:
            self.fail(f"o slide {index + 1} não ficou ativo em {timeout}ms: {self.state(page)}")

    def assert_only(self, page, index):
        estado = self.state(page)
        esperado = [i == index for i in range(3)]
        self.assertEqual(estado["active"], index)
        self.assertEqual(estado["visible"], esperado, estado)
        self.assertEqual(estado["inert"], [not v for v in esperado], estado)
        self.assertEqual(estado["dot"], index, estado)

    def assert_still(self, page, index, wait_ms):
        page.wait_for_timeout(wait_ms)
        self.assertEqual(self.state(page)["active"], index, self.state(page))

    def assert_no_errors(self):
        self.assertEqual(self.errors, [])

    # -- o que o navegador executa -----------------------------------------

    def test_the_page_runs_the_current_script(self):
        page = self.open_home()

        estado = self.state(page)
        self.assertTrue(estado["ready"], "setupBannerCarousels() não rodou")
        self.assertEqual(estado["n"], 3)
        self.assertEqual(estado["h1"], 1)
        self.assertRegex(estado["script"], r"/static/js/app\.js\?v=[0-9a-f]{10}$")
        self.assert_no_errors()

    # -- clique -------------------------------------------------------------

    def test_next_arrow_changes_the_slide(self):
        page = self.open_home()
        self.assert_only(page, 0)

        page.click("[data-banner-next]")
        self.wait_active(page, 1)
        self.assert_only(page, 1)

        page.click("[data-banner-next]")
        self.wait_active(page, 2)
        self.assert_only(page, 2)

        page.click("[data-banner-next]")
        self.wait_active(page, 0)
        self.assert_only(page, 0)

        page.click("[data-banner-prev]")
        self.wait_active(page, 2)
        self.assert_only(page, 2)
        self.assert_no_errors()

    def test_next_arrow_changes_the_slide_on_touch(self):
        page = self.open_home(width=480, touch=True)
        # `tap()` é um toque de verdade (pointer touch + click) e confere que
        # a seta está mesmo sob o dedo — nada por cima dela.
        seta = page.locator("[data-banner-next]")

        seta.tap()
        self.wait_active(page, 1)
        self.assert_only(page, 1)

        seta.tap()
        self.wait_active(page, 2)
        self.assert_only(page, 2)
        self.assert_no_errors()

    def test_dots_keyboard_and_swipe(self):
        page = self.open_home(width=820, touch=True)

        page.locator("[data-banner-dot]").nth(2).click()
        self.wait_active(page, 2)
        self.assert_only(page, 2)

        page.locator("[data-banner-dot]").nth(2).focus()
        page.keyboard.press("ArrowRight")
        self.wait_active(page, 0)
        page.keyboard.press("ArrowLeft")
        self.wait_active(page, 2)

        page.evaluate(SWIPE_LEFT)
        self.wait_active(page, 0)
        self.assert_only(page, 0)
        self.assert_no_errors()

    def test_click_works_and_one_slide_shows_at_every_width(self):
        for width in WIDTHS:
            with self.subTest(width=width):
                page = self.open_home(width=width, touch=width <= 820)
                estado = self.state(page)
                self.assertEqual(estado["visible"], [True, False, False], estado)
                self.assertFalse(estado["overflow"], f"overflow horizontal em {width}px")
                self.assertGreaterEqual(
                    estado["trackHeight"], max(estado["slideHeights"]) - 1,
                    f"a área não mede o slide mais alto em {width}px: {estado}",
                )

                page.click("[data-banner-next]")
                self.wait_active(page, 1)
                self.assert_only(page, 1)
                self.assertFalse(self.state(page)["overflow"])
        self.assert_no_errors()

    # -- rotação automática ---------------------------------------------------

    def test_autoplay_is_off_by_default(self):
        page = self.open_home()

        self.assert_still(page, 0, INTERVAL_MS + 800)

    def test_autoplay_rotates_through_every_slide_and_loops(self):
        self.autoplay_on()
        page = self.open_home()

        for esperado in (1, 2, 0):
            self.wait_active(page, esperado, timeout=INTERVAL_MS + 1500)
        self.assert_no_errors()

    def test_autoplay_pauses_on_hover_and_after_interaction(self):
        self.autoplay_on()
        page = self.open_home()

        page.hover("[data-banner-track]")
        self.assert_still(page, 0, INTERVAL_MS + 800)

        page.mouse.move(5, 5)
        self.wait_active(page, 1, timeout=INTERVAL_MS + 1500)

        page.click("[data-banner-next]")
        self.wait_active(page, 2)
        page.mouse.move(5, 5)
        # Dois intervalos de pausa depois do clique...
        self.assert_still(page, 2, INTERVAL_MS + 800)
        # ...e volta a rodar.
        self.wait_active(page, 0, timeout=INTERVAL_MS * 2)

    def test_reduced_motion_disables_autoplay_but_not_the_arrows(self):
        self.autoplay_on()
        page = self.open_home(reduced_motion="reduce")

        self.assert_still(page, 0, INTERVAL_MS + 800)

        page.click("[data-banner-next]")
        self.wait_active(page, 1)
        self.assert_only(page, 1)
