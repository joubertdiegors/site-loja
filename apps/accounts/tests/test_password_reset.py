"""Recuperação de senha: mecanismo nativo do Django, telas e e-mail nossos."""

import re

from django.contrib.auth import authenticate
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_user


def link_from_email(message) -> str:
    """Caminho do link contido no e-mail (sem o domínio)."""
    found = re.search(r"https?://[^\s]+/conta/senha/nova/[^\s]+", message.body)
    return found.group(0).split("/conta/")[1] if found else ""


class PasswordResetRequestTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )
        self.url = reverse("accounts:password_reset")

    def test_page_loads(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_existing_account_receives_the_email(self):
        response = self.client.post(self.url, {"email": "diego@example.com"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["diego@example.com"])

    def test_email_is_found_regardless_of_case(self):
        self.client.post(self.url, {"email": "DIEGO@Example.com"})

        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_account_gets_the_same_answer_and_no_email(self):
        known = self.client.post(self.url, {"email": "diego@example.com"}, follow=True)
        cache.clear()
        unknown = self.client.post(self.url, {"email": "ninguem@example.com"}, follow=True)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(known.redirect_chain, unknown.redirect_chain)
        self.assertContains(unknown, "Se existir uma conta")

    def test_inactive_account_gets_no_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertEqual(len(mail.outbox), 0)

    @override_settings(SITE_URL="https://jd-print.com")
    def test_link_uses_the_configured_site_url(self):
        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertIn("https://jd-print.com/conta/senha/nova/", mail.outbox[0].body)

    def test_email_is_sent_in_the_language_of_the_customer(self):
        self.user.preferred_language = "fr"
        self.user.save(update_fields=["preferred_language"])

        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertIn("/fr/conta/senha/nova/", mail.outbox[0].body)

    def test_password_is_never_in_the_email(self):
        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertNotIn("vaso-facetado-77", mail.outbox[0].body)


class PasswordResetConfirmTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )
        self.client.post(reverse("accounts:password_reset"), {"email": "diego@example.com"})
        self.path = "/conta/" + link_from_email(mail.outbox[0])

    def set_password(self, path, password="nova-senha-forte-88"):
        # O Django redireciona o link para uma URL com "set-password" e guarda
        # o token na sessão; é nessa URL que o formulário é enviado.
        response = self.client.get(path, follow=True)
        return self.client.post(
            response.redirect_chain[-1][0] if response.redirect_chain else path,
            {"new_password1": password, "new_password2": password},
        )

    def test_link_opens_the_form(self):
        response = self.client.get(self.path, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["validlink"])

    def test_new_password_works_and_the_old_one_stops_working(self):
        self.set_password(self.path)

        self.assertIsNotNone(authenticate(username="diego3d", password="nova-senha-forte-88"))
        self.assertIsNone(authenticate(username="diego3d", password="vaso-facetado-77"))

    def test_link_cannot_be_used_twice(self):
        self.set_password(self.path)

        response = self.client.get(self.path, follow=True)

        self.assertFalse(response.context["validlink"])

    def test_tampered_token_is_refused(self):
        broken = self.path[:-3] + "aa/"

        response = self.client.get(broken, follow=True)

        self.assertFalse(response.context["validlink"])

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_link_is_refused(self):
        response = self.client.get(self.path, follow=True)

        self.assertFalse(response.context["validlink"])
        self.assertContains(response, "não é mais válido")

    def test_weak_password_is_rejected(self):
        self.set_password(self.path, password="12345678")

        self.assertIsNone(authenticate(username="diego3d", password="12345678"))
