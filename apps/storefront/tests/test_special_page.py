"""A página de manutenção/lançamento — a que fecha a loja.

O que estes testes guardam:

1. **só uma ativa**, e no banco: ativar uma desliga a outra, e um `UPDATE`
   direto que tente ligar a segunda bate no índice único parcial;
2. **com uma ativa, o site público inteiro responde com ela** — Home,
   catálogo, produto, carrinho, conta, em qualquer idioma e método —, com
   `noindex`, sem cache e com o status certo (503 na manutenção, 200 no
   lançamento);
3. **o Admin nunca fica bloqueado**, a equipe logada vê a loja normal e o
   cliente comum logado não;
4. **o formulário de aviso funciona de verdade**: grava, não duplica, valida,
   tem honeypot, trava por IP e CSRF;
5. **nada do que o Admin digita vira HTML**.
"""

from datetime import date, time
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_category, make_product, make_user
from apps.storefront.models import (
    LaunchSubscriber,
    SpecialPage,
    SpecialPageBenefit,
    SpecialPageBenefitTranslation,
    SpecialPageKind,
    SpecialPageTranslation,
    Tone,
)

SENHA = "senha-bem-comprida"
AVISO = "/lancamento/aviso/"


def make_page(kind=SpecialPageKind.MAINTENANCE, name="Página", is_active=False, **fields):
    translations = fields.pop("translations", None)
    page = SpecialPage.objects.create(internal_name=name, kind=kind, is_active=is_active, **fields)
    base = {"title": f"{name} título", "status_text": f"{name} status"}
    base.update(translations or {})
    SpecialPageTranslation.objects.create(master=page, language="pt", **base)
    return page


def add_benefit(page, text, tone=Tone.MINT, sort_order=0, **fields):
    benefit = SpecialPageBenefit.objects.create(page=page, tone=tone, sort_order=sort_order, **fields)
    SpecialPageBenefitTranslation.objects.create(master=benefit, language="pt", text=text)
    return benefit


class SpecialPageBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="SP-01", name="Vaso", category=self.category)

    def public_urls(self):
        return [
            "/",
            "/modelos/",
            f"/produtos/{self.product.slug}/",
            "/carrinho/",
            "/conta/entrar/",
            "/contato/",
            "/fr/",
            "/nl/carrinho/",
            "/en/modelos/",
        ]


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


class SpecialPageModelTests(SpecialPageBase):
    def test_creation_and_translation_fallback(self):
        page = make_page(name="Manutenção", translations={"title": "Uma pausa rápida", "eyebrow": "Em manutenção"})
        SpecialPageTranslation.objects.create(master=page, language="fr", title="Une pause rapide")

        self.assertEqual(page.title, "Uma pausa rápida")
        self.assertEqual(page.tr("title", language="fr"), "Une pause rapide")
        # Campo sem tradução em francês cai no português.
        self.assertEqual(page.tr("eyebrow", language="fr"), "Em manutenção")
        self.assertTrue(page.is_maintenance)
        self.assertFalse(page.is_active)

    def test_activating_one_deactivates_the_other(self):
        first = make_page(name="A", is_active=True)
        second = make_page(name="B")

        second.activate()

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(SpecialPage.objects.filter(is_active=True).count(), 1)
        self.assertEqual(SpecialPage.objects.current(), second)

    def test_saving_as_active_through_the_admin_path_also_deactivates(self):
        first = make_page(name="A", is_active=True)
        second = make_page(name="B")

        second.is_active = True
        second.save()  # o caminho do formulário do Admin

        first.refresh_from_db()
        self.assertFalse(first.is_active)

    def test_database_refuses_two_active_pages(self):
        """A garantia final, para duas gravações no mesmo instante."""
        make_page(name="A", is_active=True)
        second = make_page(name="B")

        with self.assertRaises(IntegrityError), transaction.atomic():
            # Um UPDATE cru, sem passar pelo save(): é o que uma corrida faria.
            SpecialPage.objects.filter(pk=second.pk).update(is_active=True)

    def test_deactivate(self):
        page = make_page(name="A", is_active=True)

        page.deactivate()

        self.assertIsNone(SpecialPage.objects.current())

    def test_title_and_description_escape_admin_input(self):
        page = make_page(
            translations={
                "title": 'Pausa <script>alert(1)</script> para calibrar',
                "title_highlight": "calibrar",
                "description": 'Volta **às <b>10h</b>** amanhã',
            }
        )

        self.assertEqual(
            str(page.title_html),
            "Pausa &lt;script&gt;alert(1)&lt;/script&gt; para <em>calibrar</em>",
        )
        self.assertEqual(str(page.description_html), "Volta <b>às &lt;b&gt;10h&lt;/b&gt;</b> amanhã")

    def test_title_highlight_missing_from_title_keeps_title_intact(self):
        page = make_page(translations={"title": "Uma pausa", "title_highlight": "calibrar"})

        self.assertEqual(str(page.title_html), "Uma pausa")

    def test_launch_at_uses_the_configured_timezone(self):
        page = make_page(
            kind=SpecialPageKind.LAUNCH,
            launch_date=date(2026, 10, 1), launch_time=time(10, 0), launch_timezone="Europe/Brussels",
        )

        instante = page.launch_at
        self.assertEqual(instante.isoformat(), "2026-10-01T10:00:00+02:00")
        self.assertEqual(instante.astimezone(ZoneInfo("UTC")).hour, 8)
        self.assertFalse(page.is_launched)

        page.launch_timezone = "America/Sao_Paulo"
        self.assertEqual(page.launch_at.isoformat(), "2026-10-01T10:00:00-03:00")

    def test_launch_in_the_past_is_launched(self):
        page = make_page(kind=SpecialPageKind.LAUNCH, launch_date=date(2020, 1, 1), launch_time=time(0, 0))

        self.assertTrue(page.is_launched)

    def test_clean_rejects_unknown_timezone_and_launch_without_date(self):
        page = make_page(kind=SpecialPageKind.LAUNCH, launch_timezone="Marte/Olympus")

        with self.assertRaises(ValidationError) as ctx:
            page.full_clean()
        self.assertIn("launch_timezone", ctx.exception.error_dict)
        self.assertIn("launch_date", ctx.exception.error_dict)

    def test_clean_rejects_unreadable_colors(self):
        page = make_page(brand_color="yellow", brand_text_color="white")

        with self.assertRaises(ValidationError) as ctx:
            page.full_clean()
        self.assertIn("brand_text_color", ctx.exception.error_dict)

    def test_style_resolves_presets_and_hex(self):
        page = make_page(brand_color="#123456", accent_color="mint")

        style = page.style
        self.assertIn("--sp-brand:#123456", style)
        self.assertIn("--sp-accent:#7edcd8", style)
        self.assertIn("--sp-accent-dk:#3fa8a3", style)
        self.assertNotIn("<", style)

    def test_stickers_and_benefits_skip_empty_items(self):
        page = make_page(translations={"title": "T", "sticker_1": "Voltamos já", "sticker_3": "Obrigado"})
        add_benefit(page, "Pedidos seguem normais", tone=Tone.MINT, sort_order=1)
        add_benefit(page, "Inativo", is_active=False)
        add_benefit(page, "Previsão: poucas horas", tone=Tone.YELLOW, sort_order=0)

        self.assertEqual([s["slot"] for s in page.stickers], [1, 3])
        self.assertEqual([b.text for b in page.visible_benefits], ["Previsão: poucas horas", "Pedidos seguem normais"])


# ---------------------------------------------------------------------------
# O site público
# ---------------------------------------------------------------------------


class NoActivePageTests(SpecialPageBase):
    def test_site_is_normal_without_an_active_page(self):
        make_page(name="Inativa")

        for url in self.public_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("sp-body", response.content.decode())
                self.assertNotIn("X-Robots-Tag", response)


class MaintenanceActiveTests(SpecialPageBase):
    def setUp(self):
        super().setUp()
        self.page = make_page(
            name="Manutenção",
            is_active=True,
            translations={"title": "Uma pausa rápida para calibrar a loja.", "title_highlight": "calibrar"},
        )
        SpecialPageTranslation.objects.create(master=self.page, language="fr", title="Une pause rapide")

    def test_every_public_url_shows_the_page_with_503(self):
        for url in self.public_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 503)
                html = response.content.decode()
                self.assertIn("sp-body", html)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
                self.assertNotIn('id="conteudo"', html)  # nada do site normal
                self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
                self.assertEqual(response["Retry-After"], "3600")
                self.assertIn("no-store", response["Cache-Control"])

    def test_post_requests_are_blocked_too(self):
        response = self.client.post("/carrinho/adicionar/", {"variant": 1, "quantity": 1})

        self.assertEqual(response.status_code, 503)
        self.assertIn("sp-body", response.content.decode())

    def test_language_follows_the_url(self):
        self.assertIn("Une pause rapide", self.client.get("/fr/").content.decode())
        self.assertIn("calibrar", self.client.get("/").content.decode())

    def test_title_highlight_is_marked_up(self):
        html = self.client.get("/").content.decode()

        self.assertIn("para <em>calibrar</em> a loja.", html)

    def test_htmx_requests_get_a_refresh_header(self):
        response = self.client.get("/carrinho/painel/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["HX-Refresh"], "true")

    def test_admin_stays_reachable(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sp-body", response.content.decode())

        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_infrastructure_routes_stay_open(self):
        # Trocar o idioma e o webhook da Stripe não podem parar.
        response = self.client.post("/i18n/setlang/", {"language": "fr", "next": "/"})
        self.assertNotEqual(response.status_code, 503)

        response = self.client.post("/pagamento/stripe/webhook/", data="{}", content_type="application/json")
        self.assertNotEqual(response.status_code, 503)

    def test_staff_sees_the_normal_site(self):
        user = make_user("equipe", is_staff=True)
        self.client.force_login(user)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sp-body", response.content.decode())

    def test_regular_customer_cannot_bypass(self):
        user = make_user("cliente")
        self.client.force_login(user)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("sp-body", response.content.decode())

    def test_no_magic_query_string_bypass(self):
        for url in ("/?preview=1", "/?bypass=1", "/?staff=1", "/?maintenance=off"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 503)

    def test_launch_form_route_is_404_during_maintenance(self):
        response = self.client.post(AVISO, {"email": "a@b.test", "website": ""})

        self.assertEqual(response.status_code, 404)

    def test_deactivating_reopens_the_site_immediately(self):
        self.page.deactivate()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sp-body", response.content.decode())
        self.assertNotIn("X-Robots-Tag", response)


class LaunchActiveTests(SpecialPageBase):
    def setUp(self):
        super().setUp()
        self.page = make_page(
            kind=SpecialPageKind.LAUNCH,
            name="Lançamento",
            is_active=True,
            launch_date=date(2030, 10, 1),
            launch_time=time(10, 0),
            launch_timezone="Europe/Brussels",
            translations={
                "title": "Nossa loja está quase pronta.",
                "title_highlight": "pronta",
                "description": "Lançamos no dia **1 de outubro** às **10:00**.",
                "form_success_text": "Pronto! Avisamos você.",
                "countdown_done_text": "Já lançamos!",
                "form_button_label": "Quero ser avisado",
            },
        )

    def test_launch_page_returns_200_with_noindex_and_no_cache(self):
        response = self.client.get("/modelos/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Retry-After", response)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", response["Cache-Control"])
        html = response.content.decode()
        self.assertIn("sp-poster", html)
        self.assertIn("quase <em>pronta</em>.", html)
        self.assertIn("<b>1 de outubro</b>", html)

    def test_countdown_comes_from_the_configured_moment_not_from_the_script(self):
        html = self.client.get("/").content.decode()

        self.assertIn('data-launch-at="2030-10-01T10:00:00+02:00"', html)
        self.assertIn("data-count-d", html)
        self.assertNotIn("data-done", html)
        self.assertIn("js/special_page.js?v=", html)
        with open("static/js/special_page.js", encoding="utf-8") as script:
            self.assertNotIn("new Date('", script.read())

    def test_countdown_in_the_past_renders_the_done_state(self):
        self.page.launch_date = date(2020, 1, 1)
        self.page.save()

        html = self.client.get("/").content.decode()

        self.assertIn("data-done", html)
        self.assertIn("Já lançamos!", html)
        self.assertNotIn("data-countdown-done hidden", html)

    def test_countdown_hidden_when_switched_off(self):
        self.page.show_countdown = False
        self.page.save()

        self.assertNotIn("data-countdown", self.client.get("/").content.decode())

    def test_activating_a_launch_deactivates_the_maintenance(self):
        maintenance = make_page(name="Manutenção")
        maintenance.activate()
        self.assertEqual(self.client.get("/").status_code, 503)

        self.page.activate()

        maintenance.refresh_from_db()
        self.assertFalse(maintenance.is_active)
        self.assertEqual(SpecialPage.objects.filter(is_active=True).count(), 1)
        self.assertEqual(self.client.get("/").status_code, 200)

    # -- o formulário -----------------------------------------------------------------

    def test_form_is_rendered_with_csrf_and_honeypot(self):
        html = self.client.get("/").content.decode()

        self.assertIn('action="/lancamento/aviso/"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn('name="website"', html)
        self.assertIn("Quero ser avisado", html)

    def test_subscribe_saves_and_shows_success(self):
        response = self.client.post(AVISO, {"email": "Ana@Exemplo.test", "website": ""})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/?aviso=ok#aviso")
        inscrito = LaunchSubscriber.objects.get()
        self.assertEqual(inscrito.email, "ana@exemplo.test")
        self.assertEqual(inscrito.page, self.page)
        self.assertEqual(inscrito.language, "pt-br")

        html = self.client.get("/?aviso=ok").content.decode()
        self.assertIn("Pronto! Avisamos você.", html)
        self.assertNotIn('class="sp-form"', html)

    def test_subscribe_in_french_records_the_language_and_redirects_to_fr(self):
        response = self.client.post("/fr" + AVISO, {"email": "luc@exemple.test", "website": ""})

        self.assertEqual(response["Location"], "/fr/?aviso=ok#aviso")
        self.assertEqual(LaunchSubscriber.objects.get().language, "fr")

    def test_duplicates_do_not_create_rows_and_look_the_same(self):
        first = self.client.post(AVISO, {"email": "ana@exemplo.test", "website": ""})
        second = self.client.post(AVISO, {"email": "ANA@exemplo.test", "website": ""})

        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first["Location"], second["Location"])
        self.assertEqual(LaunchSubscriber.objects.count(), 1)

    def test_invalid_email_is_refused(self):
        response = self.client.post(AVISO, {"email": "nao-e-email", "website": ""})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Informe um e-mail válido.", response.content.decode())
        self.assertIn("sp-error", response.content.decode())
        self.assertEqual(LaunchSubscriber.objects.count(), 0)

    def test_honeypot_blocks_robots(self):
        response = self.client.post(AVISO, {"email": "robo@exemplo.test", "website": "http://spam"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LaunchSubscriber.objects.count(), 0)

    @override_settings(ACCOUNT_EMAIL_IP_LIMIT=2, EMAIL_VERIFICATION_IP_INTERVAL=60)
    def test_ip_throttle_stops_abuse(self):
        for i in range(2):
            self.client.post(AVISO, {"email": f"p{i}@exemplo.test", "website": ""})

        response = self.client.post(AVISO, {"email": "p9@exemplo.test", "website": ""})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Muitas tentativas", response.content.decode())
        self.assertEqual(LaunchSubscriber.objects.count(), 2)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(AVISO, {"email": "ana@exemplo.test", "website": ""})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(LaunchSubscriber.objects.count(), 0)

    def test_get_on_the_form_route_is_not_allowed(self):
        self.assertEqual(self.client.get(AVISO).status_code, 405)

    def test_form_route_is_404_when_the_form_is_switched_off(self):
        self.page.show_form = False
        self.page.save()

        response = self.client.post(AVISO, {"email": "ana@exemplo.test", "website": ""})
        self.assertEqual(response.status_code, 404)

    def test_no_launch_active_means_no_subscription_endpoint(self):
        self.page.deactivate()

        response = self.client.post(AVISO, {"email": "ana@exemplo.test", "website": ""})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(LaunchSubscriber.objects.count(), 0)


# ---------------------------------------------------------------------------
# Admin: permissões, ativação com confirmação, pré-visualização
# ---------------------------------------------------------------------------


class SpecialPageAdminTests(SpecialPageBase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.page = make_page(name="Manutenção do servidor")
        self.launch = make_page(kind=SpecialPageKind.LAUNCH, name="Lançamento outubro", launch_date=date(2030, 1, 1), launch_time=time(9, 0))
        self.changelist = reverse("admin:storefront_specialpage_changelist")
        self.preview = reverse("admin:storefront_specialpage_preview", args=[self.page.pk])

    def test_changelist_lists_pages_with_type_and_state(self):
        self.client.force_login(self.admin)

        html = self.client.get(self.changelist).content.decode()

        self.assertIn("Manutenção do servidor", html)
        self.assertIn("Lançamento outubro", html)
        self.assertIn("pré-visualizar", html)
        self.assertIn("MANUTENÇÃO E LANÇAMENTO", html.upper())

    def test_change_form_warns_before_activating(self):
        self.client.force_login(self.admin)

        html = self.client.get(reverse("admin:storefront_specialpage_change", args=[self.page.pk])).content.decode()

        self.assertIn("ATIVAR ESTA PÁGINA BLOQUEARÁ O SITE PÚBLICO", html)
        self.assertIn("special_page_admin.js", html)

    def test_activate_action_asks_for_confirmation_then_activates(self):
        self.client.force_login(self.admin)
        data = {"action": "action_activate_page", "_selected_action": [self.page.pk]}

        response = self.client.post(self.changelist, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("ATIVAR ESTA PÁGINA BLOQUEARÁ O SITE PÚBLICO", response.content.decode())
        self.page.refresh_from_db()
        self.assertFalse(self.page.is_active)

        response = self.client.post(self.changelist, {**data, "confirmar": "1"})
        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        self.assertTrue(self.page.is_active)

    def test_activate_action_refuses_more_than_one(self):
        self.client.force_login(self.admin)

        self.client.post(self.changelist, {"action": "action_activate_page", "_selected_action": [self.page.pk, self.launch.pk], "confirmar": "1"})

        self.assertEqual(SpecialPage.objects.filter(is_active=True).count(), 0)

    def test_deactivate_action(self):
        self.page.activate()
        self.client.force_login(self.admin)

        self.client.post(self.changelist, {"action": "action_deactivate_page", "_selected_action": [self.page.pk]})

        self.assertIsNone(SpecialPage.objects.current())

    def test_preview_requires_staff(self):
        response = self.client.get(self.preview)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

        cliente = make_user("cliente")
        self.client.force_login(cliente)
        response = self.client.get(self.preview)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_preview_requires_view_permission(self):
        staff = make_user("staff", is_staff=True)
        self.client.force_login(staff)

        self.assertEqual(self.client.get(self.preview).status_code, 403)

    def test_preview_shows_an_inactive_page_with_200_and_noindex(self):
        self.client.force_login(self.admin)

        response = self.client.get(self.preview)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("sp-body", html)
        self.assertIn("Manutenção do servidor título", html)
        self.assertIn("Pré-visualização", html)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        # A página continua inativa: pré-visualizar não é ativar.
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_preview_in_another_language(self):
        SpecialPageTranslation.objects.create(master=self.page, language="fr", title="Titre français")
        self.client.force_login(self.admin)

        html = self.client.get(self.preview + "?lang=fr").content.decode()

        self.assertIn("Titre français", html)

    def test_admin_input_never_becomes_markup(self):
        page = make_page(
            name="XSS",
            is_active=True,
            logo_text='JD<img src=x onerror=alert(1)>',
            translations={
                "title": "<b>Título</b>",
                "status_text": "<script>1</script>",
                "eyebrow": '"><script>2</script>',
                "footer_text": "<i>3</i>",
                "sticker_1": "<u>4</u>",
                "description": "<script>5</script>",
            },
        )
        add_benefit(page, "<script>6</script>")

        html = self.client.get("/").content.decode()

        for perigoso in ("<script>", "<b>Título", "<i>3", "<u>4", "<img src=x"):
            self.assertNotIn(perigoso, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_subscribers_admin_is_read_only(self):
        LaunchSubscriber.objects.create(email="ana@exemplo.test", language="pt")
        self.client.force_login(self.admin)

        lista = reverse("admin:storefront_launchsubscriber_changelist")
        self.assertEqual(self.client.get(lista).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:storefront_launchsubscriber_add")).status_code, 403)

        response = self.client.post(lista, {"action": "action_export_csv", "_selected_action": [LaunchSubscriber.objects.get().pk]})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("ana@exemplo.test", response.content.decode("utf-8-sig"))
