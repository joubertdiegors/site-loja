"""Confirmação de e-mail: token, prazo, uso único e reenvio.

O token é HMAC do Django com sal próprio. O que ele assina inclui o e-mail e o
estado ``email_verified`` — é isso que faz o link parar de funcionar depois de
usado, sem precisar de tabela de tokens gastos.
"""

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.emails import send_verification_email
from apps.accounts.tokens import email_verification_token, encode_uid
from apps.core.testing import LanguageResetMixin, make_user


def verify_url(user, token=None):
    return reverse(
        "accounts:verify_email",
        kwargs={
            "uidb64": encode_uid(user),
            "token": token or email_verification_token.make_token(user),
        },
    )


class VerificationLinkTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(username="diego3d", email="diego@example.com")

    def test_valid_token_confirms_the_account(self):
        response = self.client.get(verify_url(self.user))

        self.user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status"], "ok")
        self.assertTrue(self.user.email_verified)
        self.assertIsNotNone(self.user.email_verified_at)

    def test_token_can_be_used_only_once(self):
        url = verify_url(self.user)
        self.client.get(url)

        response = self.client.get(url)

        self.assertEqual(response.context["status"], "already")
        self.assertContains(response, "já foi utilizado")

    def test_tampered_token_is_refused(self):
        token = email_verification_token.make_token(self.user)
        broken = token[:-1] + ("a" if token[-1] != "a" else "b")

        response = self.client.get(verify_url(self.user, broken))

        self.user.refresh_from_db()
        self.assertEqual(response.context["status"], "invalid")
        self.assertFalse(self.user.email_verified)

    def test_token_of_one_user_does_not_confirm_another(self):
        other = make_user(username="maria", email="maria@example.com")
        token = email_verification_token.make_token(other)

        response = self.client.get(verify_url(self.user, token))

        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(response.context["status"], "invalid")
        self.assertFalse(self.user.email_verified)
        self.assertFalse(other.email_verified)

    def test_tampered_uid_does_not_confirm_anyone(self):
        other = make_user(username="maria", email="maria@example.com")
        url = reverse(
            "accounts:verify_email",
            kwargs={
                "uidb64": encode_uid(other),  # troca só o uid
                "token": email_verification_token.make_token(self.user),
            },
        )

        response = self.client.get(url)

        other.refresh_from_db()
        self.assertEqual(response.context["status"], "invalid")
        self.assertFalse(other.email_verified)

    def test_unknown_uid_is_refused(self):
        url = reverse(
            "accounts:verify_email", kwargs={"uidb64": "abcdef", "token": "1-aaaaaa"}
        )

        response = self.client.get(url)

        self.assertEqual(response.context["status"], "invalid")

    @override_settings(EMAIL_VERIFICATION_TIMEOUT=-1)
    def test_expired_token_offers_a_new_email(self):
        response = self.client.get(verify_url(self.user))

        self.user.refresh_from_db()
        self.assertEqual(response.context["status"], "expired")
        self.assertContains(response, "expirou")
        self.assertContains(response, "Enviar novo e-mail")
        self.assertFalse(self.user.email_verified)

    def test_changing_the_email_invalidates_the_pending_link(self):
        url = verify_url(self.user)
        self.user.set_email("outro@example.com")

        response = self.client.get(url)

        self.user.refresh_from_db()
        self.assertEqual(response.context["status"], "invalid")
        self.assertFalse(self.user.email_verified)

    def test_logging_in_again_does_not_break_the_pending_link(self):
        """O token do Django inclui ``last_login``; o nosso não, de propósito.

        Quem se cadastra é autenticado na hora — se ``last_login`` entrasse no
        token, qualquer login posterior mataria o link de confirmação.
        """
        url = verify_url(self.user)
        self.user.last_login = timezone.now()
        self.user.save(update_fields=["last_login"])

        response = self.client.get(url)

        self.assertEqual(response.context["status"], "ok")


class VerificationEmailContentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(username="diego3d", email="diego@example.com")

    @override_settings(SITE_URL="https://jd-print.com")
    def test_link_uses_the_configured_site_url(self):
        """Nunca o host da requisição: senão um Host falso vira link falso."""
        send_verification_email(self.user)

        self.assertIn("https://jd-print.com/conta/confirmar/", mail.outbox[0].body)

    def test_email_has_text_and_html(self):
        send_verification_email(self.user)

        message = mail.outbox[0]
        self.assertTrue(message.body.strip())
        self.assertEqual(len(message.alternatives), 1)
        self.assertEqual(message.alternatives[0].mimetype, "text/html")

    def test_email_never_asks_for_sensitive_data(self):
        send_verification_email(self.user)

        body = (mail.outbox[0].body + str(mail.outbox[0].alternatives[0].content)).lower()
        for forbidden in ("cartão", "cartao", "banco", "sua senha é", "código sms"):
            self.assertNotIn(forbidden, body)

    def test_sending_records_the_timestamp(self):
        send_verification_email(self.user)

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.verification_sent_at)

    def test_verified_user_gets_nothing(self):
        self.user.mark_email_verified()

        self.assertFalse(send_verification_email(self.user))
        self.assertEqual(len(mail.outbox), 0)


class ResendVerificationTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )
        self.url = reverse("accounts:resend_verification")

    def test_authenticated_user_can_ask_for_a_new_email(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_second_request_is_throttled(self):
        self.client.force_login(self.user)
        self.client.post(self.url)

        self.client.post(self.url)

        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_VERIFICATION_RESEND_INTERVAL=0)
    def test_throttle_window_is_configurable(self):
        self.client.force_login(self.user)
        self.client.post(self.url)

        self.client.post(self.url)

        self.assertEqual(len(mail.outbox), 2)

    def test_get_is_refused(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_anonymous_request_for_an_existing_account_sends_the_email(self):
        response = self.client.post(self.url, {"email": "DIEGO@example.com"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_anonymous_request_for_an_unknown_account_says_the_same_thing(self):
        known = self.client.post(self.url, {"email": "diego@example.com"}, follow=True)
        cache.clear()
        unknown = self.client.post(self.url, {"email": "ninguem@example.com"}, follow=True)

        self.assertEqual(len(mail.outbox), 1)  # só a conta que existe recebeu
        self.assertContains(known, "Se existir uma conta")
        self.assertContains(unknown, "Se existir uma conta")

    def test_already_verified_account_gets_no_email(self):
        self.user.mark_email_verified()

        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ACCOUNT_EMAIL_IP_LIMIT=2)
    def test_ip_quota_stops_a_flood(self):
        for index in range(6):
            make_user(username=f"cliente{index}", email=f"cliente{index}@example.com")
            self.client.post(self.url, {"email": f"cliente{index}@example.com"})

        self.assertLessEqual(len(mail.outbox), 2)
