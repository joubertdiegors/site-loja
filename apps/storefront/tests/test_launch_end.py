"""O fim do lançamento: na hora marcada, a loja abre sozinha.

O que estes testes guardam:

1. **na hora marcada a loja abre sozinha**, no servidor: qualquer endereço
   público volta a entregar o site — num refresh, num link aberto depois da
   hora e sem JavaScript nenhum (o cliente de teste não roda script). A hora é
   lida no fuso do cadastro (Europe/Brussels), no verão e no inverno;
2. **nada é reescrito no banco**: a página continua ativa no Admin, que só
   passa a dizer «lançado — site aberto». Sem cron, sem tarefa agendada;
3. **o script recebe o relógio do servidor e a Home do idioma**, e a
   pré-visualização do Admin continua mostrando o estado final, sem sair.
"""

from datetime import date, datetime, time
from datetime import timezone as dt_timezone
from unittest import mock

from django.urls import reverse

from apps.core.testing import make_user
from apps.storefront.models import (
    LaunchSubscriber,
    SpecialPage,
    SpecialPageKind,
)
from apps.storefront.tests.test_special_page import AVISO, SpecialPageBase, make_page

UTC = dt_timezone.utc


def agora(ano, mes, dia, hora, minuto=0, segundo=0):
    """Congela o relógio do servidor num instante UTC."""
    instante = datetime(ano, mes, dia, hora, minuto, segundo, tzinfo=UTC)
    return mock.patch("django.utils.timezone.now", return_value=instante)


class LaunchBase(SpecialPageBase):
    """Lançamento em 1º de outubro de 2026, 10:00 em Bruxelas (08:00 UTC, horário de verão)."""

    def setUp(self):
        super().setUp()
        self.page = make_page(
            kind=SpecialPageKind.LAUNCH,
            name="Lançamento",
            is_active=True,
            launch_date=date(2026, 10, 1),
            launch_time=time(10, 0),
            launch_timezone="Europe/Brussels",
            translations={
                "title": "Nossa loja está quase pronta.",
                "countdown_done_text": "Já lançamos!",
                "form_button_label": "Quero ser avisado",
            },
        )

    def antes(self):
        return agora(2026, 10, 1, 7, 59, 59)

    def depois(self):
        return agora(2026, 10, 1, 8, 0, 0)


# ---------------------------------------------------------------------------
# O fim do lançamento
# ---------------------------------------------------------------------------


class LaunchEndTests(LaunchBase):
    def test_before_the_moment_the_launch_page_answers(self):
        with self.antes():
            html = self.client.get("/").content.decode()

        self.assertIn("sp-poster", html)
        self.assertIn("data-countdown", html)

    def test_at_the_moment_in_brussels_the_store_opens(self):
        """10:00 em Bruxelas no verão (+02:00) é 08:00 UTC: um segundo antes, a página; na hora, a loja."""
        with self.depois():
            html = self.client.get("/").content.decode()

        self.assertNotIn("sp-poster", html)
        self.assertIn('id="conteudo"', html)  # a Home, com o `<main>` do base.html

    def test_winter_time_in_brussels_is_one_hour_from_utc(self):
        """1º de dezembro, 10:00 em Bruxelas (+01:00) é 09:00 UTC — e não 08:00."""
        self.page.launch_date = date(2026, 12, 1)
        self.page.save()

        with agora(2026, 12, 1, 8, 59, 59):
            self.assertIn("sp-poster", self.client.get("/").content.decode())
        with agora(2026, 12, 1, 9, 0, 0):
            self.assertNotIn("sp-poster", self.client.get("/").content.decode())

    def test_after_the_launch_every_public_url_serves_the_site_even_without_javascript(self):
        """O cliente de teste não roda script: é a regra do servidor sozinha."""
        with self.depois():
            for url in self.public_urls() + ["/"]:  # "/" duas vezes: o refresh
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertNotEqual(response.status_code, 503)
                    self.assertNotIn("sp-poster", response.content.decode())
                    self.assertNotIn("X-Robots-Tag", response)

    def test_nothing_is_rewritten_in_the_database(self):
        antes = SpecialPage.objects.get(pk=self.page.pk).updated_at
        with self.depois():
            self.client.get("/")
            self.client.get("/modelos/")

        self.page.refresh_from_db()
        self.assertTrue(self.page.is_active)
        self.assertEqual(self.page.updated_at, antes)
        with self.depois():
            self.assertTrue(self.page.launch_is_over)
            self.assertIsNone(SpecialPage.objects.current())

    def test_after_the_launch_each_request_costs_a_single_query(self):
        """A mesma consulta de quando não há página nenhuma: nada de traduções lidas à toa."""
        with self.depois(), self.assertNumQueries(1):
            self.assertIsNone(SpecialPage.objects.current())

    def test_maintenance_never_ends_by_the_date(self):
        manutencao = make_page(name="Manutenção", launch_date=date(2020, 1, 1), launch_time=time(0, 0))
        manutencao.activate()

        with self.depois():
            response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(manutencao.launch_is_over)

    def test_launch_without_a_date_stays_until_switched_off(self):
        self.page.show_countdown = False
        self.page.launch_date = None
        self.page.launch_time = None
        self.page.save()

        with self.depois():
            self.assertIn("sp-poster", self.client.get("/").content.decode())

    def test_countdown_carries_the_server_clock_and_the_home_in_the_page_language(self):
        with agora(2026, 10, 1, 7, 0, 0):
            html = self.client.get("/modelos/").content.decode()
            html_fr = self.client.get("/fr/").content.decode()

        self.assertIn('data-server-now="2026-10-01T07:00:00+00:00"', html)
        self.assertIn('data-home-url="/"', html)
        self.assertIn('data-home-url="/fr/"', html_fr)

    def test_notify_after_the_launch_goes_home_without_saving(self):
        with self.depois():
            response = self.client.post(AVISO, {"email": "tarde@exemplo.test", "website": ""})
            response_fr = self.client.post("/fr" + AVISO, {"email": "tard@exemple.test", "website": ""})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")
        self.assertEqual(response_fr["Location"], "/fr/")
        self.assertEqual(LaunchSubscriber.objects.count(), 0)

    def test_notify_without_any_launch_is_still_404(self):
        self.page.deactivate()

        response = self.client.post(AVISO, {"email": "ana@exemplo.test", "website": ""})

        self.assertEqual(response.status_code, 404)

    def test_admin_preview_keeps_the_final_state_and_never_leaves(self):
        chefe = make_user("chefe", is_staff=True, is_superuser=True)
        preview = reverse("admin:storefront_specialpage_preview", args=[self.page.pk])

        with self.depois():
            self.client.force_login(chefe)
            html = self.client.get(preview).content.decode()

        self.assertIn("data-done", html)
        self.assertIn("Já lançamos!", html)
        self.assertNotIn("data-home-url", html)

    def test_admin_list_says_the_store_is_already_open(self):
        chefe = make_user("chefe", is_staff=True, is_superuser=True)
        lista = reverse("admin:storefront_specialpage_changelist")

        with self.antes():
            self.client.force_login(chefe)
            self.assertIn("● no ar", self.client.get(lista).content.decode())
        with self.depois():
            self.client.force_login(chefe)
            self.assertIn("lançado — site aberto", self.client.get(lista).content.decode())
