"""Telas da área do cliente criadas no redesenho: segurança, endereços, pedidos.

A etapa 6 é de frontend, mas duas telas passaram a existir de verdade
(``Segurança`` e as duas telas preparadas). O que importa testar aqui é que
elas não abriram nenhuma porta nova: continuam exigindo sessão, continuam
usando a autenticação do Django e a troca de e-mail continua caindo na regra
da etapa 5 — endereço novo volta a ficar por confirmar.
"""

from django.contrib.auth import get_user
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.testing import LanguageResetMixin, make_user

SENHA = "senha-de-teste-77"
NOVA_SENHA = "vaso-facetado-2026"


class AccountPagesRequireLoginTests(TestCase):
    """Nenhuma das telas novas responde a quem não entrou."""

    def test_anonymous_is_sent_to_login(self):
        for name in ("dashboard", "profile", "security", "addresses", "orders"):
            with self.subTest(page=name):
                url = reverse(f"accounts:{name}")
                response = self.client.get(url)

                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response["Location"])
                self.assertIn(url, response["Location"])


class AccountNavigationTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(username="diego3d", email="diego@example.com")
        self.client.force_login(self.user)

    def test_every_account_page_responds(self):
        for name in ("dashboard", "profile", "security", "addresses", "orders"):
            with self.subTest(page=name):
                response = self.client.get(reverse(f"accounts:{name}"))
                self.assertEqual(response.status_code, 200)

    def test_pages_share_the_account_navigation(self):
        response = self.client.get(reverse("accounts:security"))

        for name in ("dashboard", "profile", "security", "addresses", "orders"):
            self.assertContains(response, reverse(f"accounts:{name}"))

    def test_current_page_is_marked_in_the_navigation(self):
        response = self.client.get(reverse("accounts:addresses"))

        self.assertEqual(response.context["account_page"], "addresses")
        self.assertContains(response, "account-link-active")

    def test_orders_screen_is_prepared_without_inventing_data(self):
        response = self.client.get(reverse("accounts:orders"))

        self.assertContains(response, "empty-state")
        self.assertNotContains(response, "#00")  # nenhum número de pedido fictício

    def test_addresses_screen_is_prepared_without_inventing_data(self):
        response = self.client.get(reverse("accounts:addresses"))

        self.assertContains(response, "empty-state")


class PasswordChangeTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(username="diego3d", email="diego@example.com")
        self.client.force_login(self.user)
        self.url = reverse("accounts:security")

    def test_password_is_changed_with_the_django_hasher(self):
        response = self.client.post(
            self.url,
            {
                "form": "password",
                "old_password": SENHA,
                "new_password1": NOVA_SENHA,
                "new_password2": NOVA_SENHA,
            },
        )

        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NOVA_SENHA))
        # Guardada pelo hasher configurado (nos testes é o rápido), nunca em claro.
        self.assertIn("$", self.user.password)
        self.assertNotIn(NOVA_SENHA, self.user.password)

    def test_session_survives_the_password_change(self):
        self.client.post(
            self.url,
            {
                "form": "password",
                "old_password": SENHA,
                "new_password1": NOVA_SENHA,
                "new_password2": NOVA_SENHA,
            },
        )

        self.assertTrue(get_user(self.client).is_authenticated)

    def test_wrong_current_password_changes_nothing(self):
        response = self.client.post(
            self.url,
            {
                "form": "password",
                "old_password": "nao-e-a-senha",
                "new_password1": NOVA_SENHA,
                "new_password2": NOVA_SENHA,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(SENHA))

    def test_weak_password_is_refused_by_djangos_validators(self):
        response = self.client.post(
            self.url,
            {
                "form": "password",
                "old_password": SENHA,
                "new_password1": "12345678",
                "new_password2": "12345678",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(SENHA))

    def test_password_is_never_written_back_into_the_page(self):
        response = self.client.post(
            self.url,
            {
                "form": "password",
                "old_password": SENHA,
                "new_password1": NOVA_SENHA,
                "new_password2": "outra-coisa-99",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, NOVA_SENHA)


class EmailChangeTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(username="diego3d", email="diego@example.com")
        self.user.mark_email_verified()
        self.client.force_login(self.user)
        self.url = reverse("accounts:security")
        mail.outbox = []

    def post_email(self, email, password=SENHA):
        return self.client.post(
            self.url, {"form": "email", "email": email, "password": password}
        )

    def test_new_address_is_saved_and_needs_confirmation_again(self):
        response = self.post_email("novo@example.com")

        self.assertRedirects(response, self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "novo@example.com")
        self.assertFalse(self.user.email_verified)

    def test_confirmation_email_is_sent_to_the_new_address(self):
        self.post_email("novo@example.com")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["novo@example.com"])
        self.assertNotIn(SENHA, mail.outbox[0].body)

    def test_current_password_is_required(self):
        response = self.post_email("novo@example.com", password="nao-e-a-senha")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "diego@example.com")
        self.assertTrue(self.user.email_verified)
        self.assertEqual(len(mail.outbox), 0)

    def test_address_of_another_account_is_refused(self):
        make_user(username="outra", email="ocupado@example.com")

        response = self.post_email("ocupado@example.com")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "diego@example.com")

    def test_case_variation_of_a_taken_address_is_refused(self):
        make_user(username="outra", email="ocupado@example.com")

        response = self.post_email("Ocupado@Example.com")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "diego@example.com")

    def test_same_address_is_refused_and_keeps_the_confirmation(self):
        response = self.post_email("diego@example.com")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_address_is_normalized_before_saving(self):
        self.post_email("Novo@Example.COM")

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, User.objects.normalize_email("Novo@Example.COM"))


class UnverifiedEmailNoticeTests(LanguageResetMixin, TestCase):
    """O aviso do design só aparece para quem realmente não confirmou."""

    def setUp(self):
        super().setUp()
        self.user = make_user(username="diego3d", email="diego@example.com")
        self.client.force_login(self.user)

    def test_notice_and_resend_action_are_shown_when_unverified(self):
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertContains(response, "ainda não foi confirmado")
        self.assertContains(response, reverse("accounts:resend_verification"))

    def test_notice_disappears_after_confirmation(self):
        self.user.mark_email_verified()

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertNotContains(response, "ainda não foi confirmado")
