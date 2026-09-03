"""As páginas de erro com ``DEBUG=False``.

Com ``DEBUG=True`` o Django mostra a sua própria tela — a amarela do 404 e o
traceback do 500 —, e nenhuma delas aparece para o cliente. O que o cliente vê
é o que estes testes checam: os templates ``404.html`` e ``500.html`` do
projeto, sem nada interno dentro.

O 500 é testado com um urlconf próprio, que aponta para uma view que estoura.
Não dá para provar que a página de erro funciona sem provocar um erro.
"""

import re

from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import path

import config.urls


def view_que_estoura(request):
    raise RuntimeError("falha proposital para exercitar o handler500")


def view_ok(request):
    return HttpResponse("ok")


urlpatterns = config.urls.urlpatterns + [
    path("estoura/", view_que_estoura),
    path("ok/", view_ok),
]

#: Sinais de que uma página vazou informação interna.
VAZAMENTOS = re.compile(
    r"(Traceback|RuntimeError|falha proposital|django\.core|"
    r"settings\.py|urls\.py|/site-packages/|DJANGO_SECRET_KEY|"
    r"Request Method:|Exception Value:)",
    re.IGNORECASE,
)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver", "*"])
class NotFoundPageTests(TestCase):
    def test_an_unknown_address_gets_the_projects_page(self):
        resposta = self.client.get("/isto-nao-existe/")

        self.assertEqual(resposta.status_code, 404)
        self.assertTemplateUsed(resposta, "404.html")

    def test_the_page_offers_a_way_back(self):
        resposta = self.client.get("/isto-nao-existe/")

        self.assertContains(resposta, "Ir para a página inicial", status_code=404)
        self.assertContains(resposta, 'href="/"', status_code=404)

    def test_the_page_carries_the_shop_navigation(self):
        """Herda do base.html: logo, menu e rodapé continuam ali."""
        resposta = self.client.get("/isto-nao-existe/")

        self.assertContains(resposta, "JD PRINT", status_code=404)

    def test_nothing_internal_leaks(self):
        resposta = self.client.get("/isto-nao-existe/")

        self.assertIsNone(VAZAMENTOS.search(resposta.content.decode()))

    def test_the_language_prefix_is_kept(self):
        """Quem estava em /fr/ continua vendo a loja em francês."""
        resposta = self.client.get("/fr/isto-nao-existe/")

        self.assertEqual(resposta.status_code, 404)
        self.assertContains(resposta, 'lang="fr"', status_code=404)

    def test_the_admin_does_not_get_the_shop_page(self):
        """O Admin resolve as URLs dele antes: um endereço estranho lá dentro
        vira redirecionamento para o login, não a página 404 da loja."""
        resposta = self.client.get("/admin/nao-existe/")

        self.assertNotEqual(resposta.status_code, 200)
        if resposta.status_code == 404:
            self.assertTemplateNotUsed(resposta, "404.html")


@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=["testserver", "*"],
    ROOT_URLCONF="apps.core.tests_error_pages",
)
class ServerErrorPageTests(TestCase):
    """`raise_request_exception` e argumento do Client, nao do `get()`:
    passado ao `get()` ele cai no `**extra` e vira um header inutil."""

    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_a_crash_gets_the_projects_page(self):
        resposta = self.client.get("/estoura/")

        self.assertEqual(resposta.status_code, 500)
        self.assertTemplateUsed(resposta, "500.html")

    def test_the_page_says_what_happened_without_saying_how(self):
        resposta = self.client.get("/estoura/")
        corpo = resposta.content.decode()

        self.assertIn("Alguma coisa falhou do nosso lado", corpo)
        self.assertIsNone(VAZAMENTOS.search(corpo))

    def test_the_page_reassures_about_the_payment(self):
        """Quem estava pagando precisa saber que nada foi cobrado sem confirmação."""
        resposta = self.client.get("/estoura/")

        self.assertContains(resposta, "nada foi cobrado", status_code=500)

    def test_the_page_does_not_touch_the_database(self):
        """O 500 pode ser justamente o banco fora do ar.

        Se ``500.html`` herdasse de ``base.html`` — que consulta categorias,
        idiomas e carrinho —, a página de erro estouraria dentro do tratamento
        do erro, e o visitante veria a tela crua do Django.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as consultas:
            self.client.get("/estoura/")

        # A view estoura antes de consultar; a página de erro não pode
        # acrescentar consulta nenhuma.
        de_template = [
            consulta
            for consulta in consultas
            if "categories_category" in consulta["sql"]
            or "core_sitelanguage" in consulta["sql"]
        ]
        self.assertEqual(de_template, [])

    def test_the_page_has_a_way_back(self):
        resposta = self.client.get("/estoura/")

        self.assertContains(resposta, 'href="/"', status_code=500)

    def test_the_page_asks_not_to_be_indexed(self):
        resposta = self.client.get("/estoura/")

        self.assertContains(resposta, "noindex", status_code=500)

    def test_a_working_view_is_untouched(self):
        """A rede de segurança do próprio teste."""
        self.assertEqual(self.client.get("/ok/").status_code, 200)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver", "*"])
class ProductionBehaviourTests(TestCase):
    """O que muda com ``DEBUG=False`` e precisa continuar funcionando."""

    def test_the_favicon_does_not_404(self):
        """Sem esta rota é um 404 por visitante no log.

        O redirecionamento é temporário (302) desde que o ícone passou a vir do
        Admin: um 301 fica no cache do navegador e do proxy, e quem trocasse o
        favicon continuaria vendo o antigo por meses.
        """
        resposta = self.client.get("/favicon.ico")

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("favicon", resposta["Location"])

    def test_a_private_page_redirects_to_the_login(self):
        resposta = self.client.get("/conta/")

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta["Location"])

    def test_the_security_headers_are_there(self):
        resposta = self.client.get("/")

        self.assertEqual(resposta["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resposta["Referrer-Policy"], "same-origin")
        self.assertEqual(resposta["X-Frame-Options"], "DENY")

    def test_a_post_without_the_csrf_token_is_refused(self):
        from django.test import Client

        cliente = Client(enforce_csrf_checks=True)
        resposta = cliente.post("/carrinho/adicionar/", {"product_id": "1"})

        self.assertEqual(resposta.status_code, 403)

    def test_the_csrf_failure_page_says_nothing_internal(self):
        from django.test import Client

        cliente = Client(enforce_csrf_checks=True)
        resposta = cliente.post("/carrinho/adicionar/", {"product_id": "1"})

        self.assertIsNone(VAZAMENTOS.search(resposta.content.decode()))
