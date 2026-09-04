"""URLs de estáticos com versão (``{% static_versioned %}``).

Existe por causa de um problema real: o carrossel de banners chegou a um
navegador com o HTML novo e o ``app.js`` antigo, direto do cache — setas na
tela, clique sem efeito. Sem ``Cache-Control`` o navegador guarda um arquivo
"por conta própria", e a URL do ``{% static %}`` não muda quando o conteúdo
muda. Com o hash do conteúdo na URL, muda.
"""

import os
import re
import tempfile

from django.template import Context, Template
from django.test import TestCase, override_settings

from apps.core.templatetags.jdprint import static_version, static_versioned
from apps.core.testing import LanguageResetMixin

VERSIONED = re.compile(r"^/static/(?P<path>[\w./-]+)\?v=(?P<version>[0-9a-f]{10})$")


class StaticVersionedTagTests(TestCase):
    def render(self, path):
        return Template("{% load jdprint %}{% static_versioned path %}").render(Context({"path": path}))

    def test_site_assets_carry_a_content_hash(self):
        for path in ("js/app.js", "css/tailwind.css", "vendor/htmx.min.js"):
            with self.subTest(path=path):
                url = self.render(path)
                match = VERSIONED.match(url)
                self.assertIsNotNone(match, url)
                self.assertEqual(match["path"], path)

    def test_missing_file_falls_back_to_the_plain_url(self):
        self.assertEqual(self.render("js/nao-existe.js"), "/static/js/nao-existe.js")
        self.assertEqual(static_version("js/nao-existe.js"), "")

    def test_version_follows_the_file_content(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = os.path.join(pasta, "versao.js")
            with override_settings(STATICFILES_DIRS=[pasta]):
                with open(arquivo, "w", encoding="utf-8") as saida:
                    saida.write("console.log(1);")
                os.utime(arquivo, ns=(1_000_000_000, 1_000_000_000))
                primeira = static_version("versao.js")
                segunda = static_version("versao.js")
                self.assertEqual(primeira, segunda, "sem mudança no arquivo, a versão é a mesma")

                with open(arquivo, "w", encoding="utf-8") as saida:
                    saida.write("console.log(2);")
                os.utime(arquivo, ns=(2_000_000_000, 2_000_000_000))
                self.assertNotEqual(static_version("versao.js"), primeira)
                self.assertEqual(len(static_version("versao.js")), 10)
                self.assertEqual(static_versioned("versao.js"), f"/static/versao.js?v={static_version('versao.js')}")


class BaseTemplateUsesVersionedAssetsTests(LanguageResetMixin, TestCase):
    def test_home_links_css_and_js_with_a_version(self):
        html = self.client.get("/").content.decode()

        self.assertRegex(html, r'href="/static/css/tailwind\.css\?v=[0-9a-f]{10}"')
        self.assertRegex(html, r'src="/static/js/app\.js\?v=[0-9a-f]{10}"')
        self.assertRegex(html, r'src="/static/vendor/htmx\.min\.js\?v=[0-9a-f]{10}"')
        self.assertNotIn('src="/static/js/app.js"', html)
