"""Login por username ou e-mail, no mesmo campo.

A tela tem um campo só ("Usuário ou e-mail"), então o backend precisa aceitar
os dois — sem diferenciar maiúsculas em nenhum deles — e responder sempre a
mesma coisa quando dá errado.
"""

from django.contrib.auth import authenticate
from django.test import TestCase
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_user


class LoginBackendTests(TestCase):
    def setUp(self):
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )

    def test_login_with_username(self):
        self.assertEqual(
            authenticate(username="diego3d", password="vaso-facetado-77"), self.user
        )

    def test_login_with_email(self):
        self.assertEqual(
            authenticate(username="diego@example.com", password="vaso-facetado-77"), self.user
        )

    def test_username_is_case_insensitive(self):
        for variation in ("Diego3D", "DIEGO3D"):
            with self.subTest(username=variation):
                self.assertEqual(
                    authenticate(username=variation, password="vaso-facetado-77"), self.user
                )

    def test_email_is_case_insensitive(self):
        self.assertEqual(
            authenticate(username="DIEGO@Example.com", password="vaso-facetado-77"), self.user
        )

    def test_surrounding_spaces_are_ignored(self):
        self.assertEqual(
            authenticate(username="  diego3d  ", password="vaso-facetado-77"), self.user
        )

    def test_wrong_password_fails(self):
        self.assertIsNone(authenticate(username="diego3d", password="senha-errada-77"))

    def test_unknown_user_fails(self):
        self.assertIsNone(authenticate(username="ninguem", password="vaso-facetado-77"))

    def test_inactive_user_cannot_authenticate(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertIsNone(authenticate(username="diego3d", password="vaso-facetado-77"))


class LoginViewTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )
        self.url = reverse("accounts:login")

    def post(self, identifier, password="vaso-facetado-77", **extra):
        return self.client.post(self.url, {"username": identifier, "password": password}, **extra)

    def test_page_shows_a_single_identifier_field(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuário ou e-mail")

    def test_login_with_username_redirects_to_the_account(self):
        response = self.post("diego3d")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("accounts:dashboard"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_login_with_email_redirects_to_the_account(self):
        response = self.post("DIEGO@example.com")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_the_three_failures_give_the_same_message(self):
        """Senha errada, conta inexistente e conta desativada respondem igual.

        Mensagens diferentes transformariam a tela de login num verificador de
        quem tem conta na loja.
        """
        wrong_password = self.post("diego3d", password="senha-errada-77")
        unknown = self.post("ninguem", password="senha-errada-77")

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.post("diego3d")

        messages = {
            " ".join(response.context["form"].non_field_errors())
            for response in (wrong_password, unknown, inactive)
        }
        self.assertEqual(len(messages), 1)
        self.assertIn("incorretos", messages.pop())

    def test_session_key_changes_on_login(self):
        """``login()`` rotaciona a sessão — proteção contra fixação de sessão."""
        self.client.get(self.url)
        before = self.client.session.session_key

        self.post("diego3d")

        self.assertNotEqual(self.client.session.session_key, before)

    def test_authenticated_visitor_is_sent_to_the_account(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = make_user(username="diego3d")
        self.client.force_login(self.user)

    def test_logout_by_get_is_refused(self):
        """Sair muda estado: não pode acontecer por GET (um <img> bastaria)."""
        response = self.client.get(reverse("accounts:logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_by_post_works(self):
        response = self.client.post(reverse("accounts:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_account_pages_require_login(self):
        self.client.post(reverse("accounts:logout"))

        for name in ("accounts:dashboard", "accounts:profile"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response["Location"])
