"""A página de lançamento num navegador de verdade: a contagem anda e o
formulário grava.

Os testes de `test_special_page.py` conferem o HTML e os cabeçalhos; o que
acontece depois — o JavaScript contando a partir de `data-launch-at`, o estado
final ao chegar a zero, o formulário indo e voltando com a pílula de sucesso —
só um navegador mostra. Mesma infraestrutura dos testes do carrossel
(Playwright, tag ``browser``, pulados sem o pacote ou sem Chromium).
"""

import os
import unittest
from datetime import date, time, timedelta

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from django.utils import timezone

from apps.core.testing import LanguageResetMixin
from apps.storefront.models import LaunchSubscriber, SpecialPage, SpecialPageKind, SpecialPageTranslation

try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
except ImportError:  # pragma: no cover - depende da máquina
    sync_playwright = None

COUNT = """() => [...document.querySelectorAll('[data-count-d],[data-count-h],[data-count-m],[data-count-s]')].map(e => e.textContent)"""


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def launch(playwright):
    for options in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return playwright.chromium.launch(headless=True, **options)
        except PlaywrightError:
            continue
    return None


@tag("browser")
@unittest.skipIf(sync_playwright is None, "playwright não instalado (requirements/dev.txt)")
class LaunchPageBrowserTests(LanguageResetMixin, StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        anterior = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        cls.addClassCleanup(_restore_env, "DJANGO_ALLOW_ASYNC_UNSAFE", anterior)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = launch(cls.playwright)
        if cls.browser is None:
            raise unittest.SkipTest("nenhum navegador Chromium disponível para o Playwright")
        cls.addClassCleanup(cls.browser.close)

    def make_launch(self, launch_at):
        page = SpecialPage.objects.create(
            internal_name="Lançamento", kind=SpecialPageKind.LAUNCH, is_active=True,
            launch_date=launch_at.date(), launch_time=launch_at.time().replace(microsecond=0),
            launch_timezone="UTC",
        )
        SpecialPageTranslation.objects.create(
            master=page, language="pt", title="Quase pronta.", title_highlight="pronta",
            countdown_done_text="Já lançamos!", form_success_text="Pronto! Avisamos você.",
            form_button_label="Quero ser avisado",
        )
        return page

    def open(self, width=1500):
        context = self.browser.new_context(viewport={"width": width, "height": 900}, locale="pt-BR")
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.live_server_url + "/")
        page.wait_for_load_state("networkidle")
        return page

    def test_countdown_counts_down_from_the_configured_moment(self):
        self.make_launch(timezone.now().replace(tzinfo=None) + timedelta(days=2, hours=3, minutes=4, seconds=30))
        page = self.open()

        first = page.evaluate(COUNT)
        page.wait_for_timeout(1500)
        second = page.evaluate(COUNT)

        self.assertEqual(first[0], "02")
        self.assertEqual(first[1], "03")
        self.assertNotEqual(first, second, "a contagem não andou")
        self.assertTrue(page.locator("[data-countdown-done]").is_hidden())
        self.assertEqual(self.errors, [])

    def test_countdown_reaches_zero_and_shows_the_final_state(self):
        self.make_launch(timezone.now().replace(tzinfo=None) + timedelta(seconds=2))
        page = self.open()

        page.wait_for_selector("[data-countdown][data-done]", timeout=5000)

        self.assertEqual(page.evaluate(COUNT), ["00", "00", "00", "00"])
        self.assertTrue(page.locator("[data-countdown-done]").is_visible())
        self.assertIn("Já lançamos!", page.locator("[data-countdown-done]").inner_text())

    def test_form_submits_and_shows_success_at_every_width(self):
        self.make_launch(timezone.now().replace(tzinfo=None) + timedelta(days=1))
        for width in (1500, 1100, 820, 640, 480):
            with self.subTest(width=width):
                page = self.open(width)
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > window.innerWidth"))

                page.fill("input[name=email]", f"pessoa{width}@exemplo.test")
                page.click(".sp-form button")
                page.wait_for_load_state("networkidle")

                self.assertIn("aviso=ok", page.url)
                self.assertTrue(page.locator(".sp-ok#aviso").is_visible())
                self.assertEqual(page.locator(".sp-form").count(), 0)
        self.assertEqual(LaunchSubscriber.objects.count(), 5)
