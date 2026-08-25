"""Testes de sanidade dos templates.

Existem por causa de um erro real: um comentário ``{# ... #}`` escrito em duas
linhas não é comentário no Django — a sintaxe é de uma linha só — e o texto
aparece na página para o cliente. Aconteceu duas vezes nesta etapa.
"""

import glob
import io
import os
import re

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    make_section,
    make_user,
)


class TemplateSourceTests(TestCase):
    def test_no_multiline_short_comments(self):
        """``{#`` precisa fechar na mesma linha, senão vira texto visível."""
        offenders = []
        for path in glob.glob(os.path.join("templates", "**", "*.html"), recursive=True):
            for number, line in enumerate(io.open(path, encoding="utf-8"), start=1):
                if "{#" in line and "#}" not in line:
                    offenders.append(f"{path}:{number}")

        self.assertEqual(
            offenders,
            [],
            "Comentário {# #} aberto em uma linha e fechado em outra: use "
            "{% comment %}...{% endcomment %}.",
        )


class RenderedPagesTests(LanguageResetMixin, TestCase):
    """Nenhuma página pode entregar sintaxe de template crua ao cliente."""

    def setUp(self):
        super().setUp()
        category = make_category(slug="modelos", name="Modelos")
        product = make_product(
            sku="GATO-01", name="Gato Pompom", category=category,
            price=Decimal("8.90"), stock_quantity=5, is_featured=True,
        )
        make_section(internal_name="Destaques", title="Destaques de Modelos")
        self.client.post("/carrinho/adicionar/", {"product_id": product.pk})
        self.product = product

    def pages(self):
        return [
            "/",
            "/modelos/",
            "/modelos/?categoria=modelos",
            "/carrinho/",
            "/carrinho/finalizar/",
            self.product.get_absolute_url(),
            "/fr/",
            "/fr/modelos/",
        ]

    def test_no_raw_template_syntax_is_rendered(self):
        for page in self.pages():
            with self.subTest(page=page):
                content = self.client.get(page).content.decode()
                for token in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(token, content, f"'{token}' apareceu em {page}")

    def test_every_page_responds(self):
        for page in self.pages():
            with self.subTest(page=page):
                self.assertEqual(self.client.get(page).status_code, 200)


class DesignTokenTests(TestCase):
    """Cor escrita à mão no template é cor que ninguém consegue trocar depois.

    O Design System vive em ``static/src/input.css`` (``@theme``): as telas
    usam ``bg-brand-600``, ``text-ink-soft``, ``border-surface-line``. Duas
    exceções continuam válidas e estão listadas abaixo — os e-mails, onde não
    existe CSS externo, e os poucos lugares em que a cor é dado, não estilo.
    """

    HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

    #: (arquivo, motivo) — tudo que não estiver aqui é erro.
    ALLOWED = {
        "templates/base.html",  # <meta name="theme-color">, exige valor literal
        "templates/catalog/product_detail.html",  # cor do filamento vem do banco
        "templates/components/product_card.html",  # idem
    }

    def test_interface_templates_use_tokens_instead_of_raw_colors(self):
        offenders = []
        for path in glob.glob(os.path.join("templates", "**", "*.html"), recursive=True):
            relative = path.replace("\\", "/")
            if relative in self.ALLOWED or relative.startswith("templates/emails/"):
                continue
            for number, line in enumerate(io.open(path, encoding="utf-8"), start=1):
                if self.HEX.search(line):
                    offenders.append(f"{relative}:{number}")

        self.assertEqual(
            offenders,
            [],
            "Cor literal no template: use um token do Design System "
            "(bg-brand-600, text-ink-soft, border-surface-line...).",
        )

    def test_emails_use_the_same_brand_palette_as_the_store(self):
        """E-mail não tem CSS externo, mas a cor tem que ser a mesma da loja."""
        antigos = {"#7c3aed", "#18181b", "#f5f5f7"}  # paleta anterior ao redesenho
        for path in glob.glob(os.path.join("templates", "emails", "*.html")):
            with self.subTest(path=path):
                content = io.open(path, encoding="utf-8").read().lower()
                for cor in antigos:
                    self.assertNotIn(cor, content)


class AccountPagesRenderTests(LanguageResetMixin, TestCase):
    """As telas da conta entram na mesma varredura das páginas públicas."""

    def setUp(self):
        super().setUp()
        self.user = make_user(username="cliente3d", email="cliente@example.com")
        self.client.force_login(self.user)

    def pages(self):
        return [reverse(f"accounts:{name}") for name in
                ("dashboard", "profile", "security", "addresses", "orders")]

    def test_no_raw_template_syntax_is_rendered(self):
        for page in self.pages():
            with self.subTest(page=page):
                content = self.client.get(page).content.decode()
                for token in ("{#", "#}", "{%", "%}", "{{", "}}"):
                    self.assertNotIn(token, content, f"'{token}' apareceu em {page}")

    def test_interface_is_translated_on_the_account_pages(self):
        for language, expected in (("fr", "Sécurité"), ("en", "Security"), ("nl", "Beveiliging")):
            with self.subTest(language=language):
                response = self.client.get(f"/{language}/conta/seguranca/")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected)
