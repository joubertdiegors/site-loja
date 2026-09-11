"""Os avisos da loja num navegador de verdade.

Os testes de `test_store_notices.py` conferem o HTML; o que só um navegador
mostra fica aqui: onde cada posição cai na tela (no desktop e no celular, com
o sistema no claro e no escuro), se o texto se lê, se o aviso deixa clicar no
que importa, e o X — pelo teclado, lembrado entre páginas, sem esconder o
aviso vizinho e devolvendo o aviso reescrito. Mesma infraestrutura dos testes
da página de lançamento (Playwright, tag ``browser``).
"""

import os
import unittest
from datetime import date, time
from decimal import Decimal

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag

from apps.core.testing import LanguageResetMixin, make_category, make_product, make_variant
from apps.storefront.models import (
    NoticePosition,
    SpecialPage,
    SpecialPageKind,
    SpecialPageTranslation,
    StoreNotice,
    StoreNoticeTranslation,
)
from apps.storefront.tests.test_special_page_browser import _restore_env, launch

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depende da máquina
    sync_playwright = None

#: Cada aviso visível: caixa na tela, cores do texto e do fundo efetivo.
MEDIDAS = r"""() => {
  const fundo = (el) => {
    let n = el;
    while (n) {
      const bg = getComputedStyle(n).backgroundColor;
      const m = bg.match(/[\d.]+/g);
      if (m && (m.length < 4 || Number(m[3]) > 0.5)) return bg;
      n = n.parentElement;
    }
    return 'rgb(255, 255, 255)';
  };
  return [...document.querySelectorAll('[data-notice]')].map(a => {
    const r = a.getBoundingClientRect();
    const cs = getComputedStyle(a);
    const msg = a.querySelector('.notice-message');
    const link = a.querySelector('.notice-link');
    const x = a.querySelector('[data-notice-close]');
    const xr = x ? x.getBoundingClientRect() : null;
    return {
      id: a.getAttribute('data-notice'),
      texto: msg.textContent.trim(),
      visivel: r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden',
      caixa: [r.left, r.top, r.right, r.bottom],
      fixo: getComputedStyle(a.parentElement).position === 'fixed',
      cor: getComputedStyle(msg).color, fundo: fundo(msg),
      link_cor: link ? getComputedStyle(link).color : null, link_fundo: link ? fundo(link) : null,
      x: xr ? [xr.width, xr.height] : null,
    };
  });
}"""

SEM_TRANSBORDO = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _contraste(cor, fundo):
    def rgb(texto):
        return [float(n) for n in texto[texto.index("(") + 1:texto.index(")")].replace("/", ",").split(",")[:3]]

    def lum(canais):
        saida = []
        for valor in canais:
            v = valor / 255
            saida.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * saida[0] + 0.7152 * saida[1] + 0.0722 * saida[2]

    a, b = lum(rgb(cor)), lum(rgb(fundo))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def aviso(message, *, pages=("home",), position=NoticePosition.BELOW_BANNER, dismissible=True,
          sort_order=0, link_url="", link_label="", title=""):
    notice = StoreNotice.objects.create(
        pages=list(pages), position=position, dismissible=dismissible, sort_order=sort_order, link_url=link_url,
    )
    StoreNoticeTranslation.objects.create(
        master=notice, language="pt", message=message, title=title, link_label=link_label
    )
    return notice


@tag("browser")
@unittest.skipIf(sync_playwright is None, "playwright não instalado (requirements/dev.txt)")
class StoreNoticeBrowserTests(LanguageResetMixin, StaticLiveServerTestCase):
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

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="AV-01", name="Vaso", category=self.category)
        make_variant(self.product, price=Decimal("19.90"), stock=5)

    def open(self, path="/", width=1500, scheme="light", context=None):
        if context is None:
            context = self.browser.new_context(
                viewport={"width": width, "height": 900}, locale="pt-BR", color_scheme=scheme
            )
            self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.live_server_url + path)
        page.wait_for_load_state("networkidle")
        return page

    # -- posições -------------------------------------------------------------------

    def test_the_four_positions_on_desktop_and_mobile_in_both_themes(self):
        aviso("Faixa fina no topo da página.", position=NoticePosition.TOP, link_url="/modelos/", link_label="Ver")
        aviso("Faixa de destaque abaixo do banner.", link_url="/modelos/", link_label="Ver produtos",
              title="Novidade")
        aviso("Aviso discreto depois do conteúdo.", position=NoticePosition.AFTER_CONTENT, link_url="/contato/")
        aviso("Card no canto, como uma mensagem.", position=NoticePosition.CORNER, link_url="/contato/")

        for scheme in ("light", "dark"):
            for width in (1500, 820, 390):
                with self.subTest(scheme=scheme, width=width):
                    page = self.open(width=width, scheme=scheme)
                    medidas = {m["texto"]: m for m in page.evaluate(MEDIDAS)}
                    self.assertEqual(len(medidas), 4)
                    for texto, m in medidas.items():
                        self.assertTrue(m["visivel"], texto)
                        self.assertGreaterEqual(_contraste(m["cor"], m["fundo"]), 4.5, texto)
                        if m["link_cor"]:
                            self.assertGreaterEqual(_contraste(m["link_cor"], m["link_fundo"]), 4.5, texto)
                        # O X é um alvo de verdade: pelo menos 32px.
                        self.assertGreaterEqual(min(m["x"]), 32, texto)
                        # Nenhum aviso sai da tela para os lados.
                        self.assertGreaterEqual(m["caixa"][0], 0, texto)
                        self.assertLessEqual(m["caixa"][2], width, texto)

                    header = page.locator("header").bounding_box()
                    topo = medidas["Faixa fina no topo da página."]
                    self.assertLessEqual(topo["caixa"][3], header["y"] + 1)  # acima do cabeçalho
                    self.assertFalse(topo["fixo"])

                    hero = page.locator('[aria-labelledby="hero-titulo"]').bounding_box()
                    faixa = medidas["Faixa de destaque abaixo do banner."]
                    self.assertGreaterEqual(faixa["caixa"][1], hero["y"] + hero["height"] - 1)  # depois do banner
                    self.assertFalse(faixa["fixo"])

                    canto = medidas["Card no canto, como uma mensagem."]
                    self.assertTrue(canto["fixo"])
                    self.assertLessEqual(canto["caixa"][3], 900)
                    self.assertGreater(canto["caixa"][0], width / 2 - 1 if width > 640 else 0)

                    self.assertTrue(page.evaluate(SEM_TRANSBORDO))
                    self.assertEqual(self.errors, [])

    def test_the_notices_never_block_the_important_clicks(self):
        """O carrinho do cabeçalho, o botão de comprar e o fim do rodapé continuam clicáveis.

        A rolagem é instantânea de propósito: a loja usa `scroll-smooth`, e uma
        rolagem animada ainda não terminou quando a posição é medida.
        """
        aviso("No canto", pages=["home", "product"], position=NoticePosition.CORNER)
        aviso("No topo", pages=["home", "product"], position=NoticePosition.TOP)

        for width in (1500, 390):
            with self.subTest(width=width):
                page = self.open(f"/produtos/{self.product.slug}/", width=width)
                alvos = {
                    "carrinho": "#cart-link",
                    "comprar": "[data-add-button]",
                }
                for nome, seletor in alvos.items():
                    livre = page.evaluate(
                        """(sel) => { const el = document.querySelector(sel);
                             el.scrollIntoView({block: 'center', behavior: 'instant'});
                             const r = el.getBoundingClientRect();
                             const achado = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                             return el === achado || el.contains(achado); }""",
                        seletor,
                    )
                    self.assertTrue(livre, f"{nome} coberto em {width}px")

                # No fim da página, o último link do rodapé fica acima do card.
                ultimo = page.evaluate(
                    """() => { window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'});
                         const links = [...document.querySelectorAll('footer a')];
                         const el = links[links.length - 1];
                         const r = el.getBoundingClientRect();
                         const achado = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                         return el === achado || el.contains(achado); }"""
                )
                self.assertTrue(ultimo, f"o fim do rodapé fica embaixo do card em {width}px")

    # -- fechar -----------------------------------------------------------------------

    def test_close_by_keyboard_is_remembered_and_spares_the_neighbour(self):
        fechar = aviso("Este eu fecho.", pages=["home", "catalog"])
        aviso("Este continua.", pages=["home", "catalog"], sort_order=1)

        context = self.browser.new_context(viewport={"width": 1500, "height": 900}, locale="pt-BR")
        self.addCleanup(context.close)
        page = self.open(context=context)

        botao = page.locator(f'[data-notice="{fechar.pk}"] [data-notice-close]')
        self.assertEqual(botao.get_attribute("aria-label"), "Fechar aviso")
        self.assertEqual(botao.evaluate("b => b.tagName"), "BUTTON")
        botao.focus()
        page.keyboard.press("Enter")

        self.assertEqual(page.locator(f'[data-notice="{fechar.pk}"]').count(), 0)
        self.assertEqual(page.locator("text=Este continua.").count(), 1)
        # O foco não caiu no vazio: foi para o X do aviso que ficou.
        self.assertEqual(page.evaluate("() => document.activeElement.hasAttribute('data-notice-close')"), True)

        # Outra página, e a mesma na volta: o fechado não reaparece; o vizinho sim.
        for path in ("/modelos/", "/"):
            with self.subTest(path=path):
                outra = self.open(path, context=context)
                self.assertEqual(outra.locator("text=Este eu fecho.").count(), 0)
                self.assertEqual(outra.locator("text=Este continua.").count(), 1)

        # O Admin reescreve o aviso: a versão nova aparece para quem fechou a antiga.
        StoreNoticeTranslation.objects.filter(master=fechar).update(message="Este eu fecho — versão nova.")
        depois = self.open(context=context)
        self.assertEqual(depois.locator("text=Este eu fecho — versão nova.").count(), 1)
        self.assertEqual(self.errors, [])

    def test_a_notice_that_cannot_be_closed_has_no_close_button(self):
        aviso("Fixo.", dismissible=False)

        page = self.open()

        self.assertEqual(page.locator("text=Fixo.").count(), 1)
        self.assertEqual(page.locator("[data-notice-close]").count(), 0)

    def test_the_corner_shows_one_card_at_a_time(self):
        aviso("Primeiro card.", position=NoticePosition.CORNER, sort_order=1)
        aviso("Segundo card.", position=NoticePosition.CORNER, sort_order=2)

        for width in (1500, 390):
            with self.subTest(width=width):
                page = self.open(width=width)
                self.assertTrue(page.locator("text=Primeiro card.").is_visible())
                self.assertTrue(page.locator("text=Segundo card.").is_hidden())

                page.locator("[data-notice-stack] [data-notice-close]").first.click()

                self.assertTrue(page.locator("text=Segundo card.").is_visible())
                self.assertEqual(page.evaluate("() => document.activeElement.hasAttribute('data-notice-close')"), True)
                page.context.clear_cookies()

    # -- sem vínculo com o lançamento ----------------------------------------------

    def test_on_the_launch_page_when_chosen(self):
        pagina = SpecialPage.objects.create(
            internal_name="Lançamento", kind=SpecialPageKind.LAUNCH, is_active=True,
            launch_date=date(2099, 1, 1), launch_time=time(10, 0), launch_timezone="Europe/Brussels",
        )
        SpecialPageTranslation.objects.create(master=pagina, language="pt", title="Quase pronta.")
        aviso("Durante o lançamento.", pages=["special"], position=NoticePosition.CORNER)

        for width in (1500, 390):
            with self.subTest(width=width):
                page = self.open(width=width)
                self.assertEqual(page.locator(".sp-poster").count(), 1)
                self.assertTrue(page.locator("text=Durante o lançamento.").is_visible())
                page.locator("[data-notice-close]").click()
                self.assertEqual(page.locator("text=Durante o lançamento.").count(), 0)
                self.assertTrue(page.evaluate(SEM_TRANSBORDO))
                self.assertEqual(self.errors, [])
                page.context.clear_cookies()
