"""Admin: dois cadastros separados, e a senha nunca em texto claro."""

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.testing import make_user


class UserAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser(
            username="chefe", email="chefe@jdprint.test", password="senha-de-teste-77"
        )
        cls.customer = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_changelist_shows_the_expected_columns(self):
        response = self.client.get(reverse("admin:accounts_user_changelist"))

        content = response.content.decode().lower()
        self.assertEqual(response.status_code, 200)
        for label in ("nome de usuário", "e-mail confirmado", "idioma preferido", "último login"):
            self.assertIn(label, content)

    def test_filters_are_available(self):
        response = self.client.get(
            reverse("admin:accounts_user_changelist"), {"email_verified__exact": "0"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "diego3d")

    def test_search_by_email(self):
        response = self.client.get(
            reverse("admin:accounts_user_changelist"), {"q": "diego@example.com"}
        )

        self.assertContains(response, "diego3d")

    def test_change_page_never_shows_the_password(self):
        response = self.client.get(
            reverse("admin:accounts_user_change", args=[self.customer.pk])
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("vaso-facetado-77", content)
        self.assertNotIn(self.customer.password, content)

    def test_customer_data_is_edited_inline(self):
        response = self.client.get(
            reverse("admin:accounts_user_change", args=[self.customer.pk])
        )

        self.assertIn("dados comerciais", response.content.decode().lower())

    def test_action_marks_the_email_as_verified(self):
        self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "action_mark_verified", "_selected_action": [str(self.customer.pk)]},
            follow=True,
        )

        self.customer.refresh_from_db()
        self.assertTrue(self.customer.email_verified)

    def test_action_sends_the_verification_email(self):
        from django.core import mail

        self.client.post(
            reverse("admin:accounts_user_changelist"),
            {"action": "action_send_verification", "_selected_action": [str(self.customer.pk)]},
            follow=True,
        )

        self.assertEqual(len(mail.outbox), 1)

    def test_creating_a_user_from_the_admin_rejects_a_duplicate_by_case(self):
        response = self.client.post(
            reverse("admin:accounts_user_add"),
            {
                "username": "DIEGO3D",
                "email": "outro@example.com",
                "password1": "senha-forte-2026",
                "password2": "senha-forte-2026",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username__iexact="diego3d").count(), 1)


class CustomerAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser(
            username="chefe", email="chefe@jdprint.test", password="senha-de-teste-77"
        )
        cls.customer = make_user(username="diego3d", email="diego@example.com")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_changelist_loads(self):
        response = self.client.get(reverse("admin:accounts_customer_changelist"))

        self.assertEqual(response.status_code, 200)

    def test_change_page_links_to_the_account(self):
        response = self.client.get(
            reverse("admin:accounts_customer_change", args=[self.customer.customer.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:accounts_user_change", args=[self.customer.pk]))
        self.assertIn("e-mail não confirmado", response.content.decode().lower())

    def test_authentication_fields_are_not_here(self):
        response = self.client.get(
            reverse("admin:accounts_customer_change", args=[self.customer.customer.pk])
        )
        content = response.content.decode()

        self.assertNotIn('name="password"', content)
        self.assertNotIn('name="email_verified"', content)


class AdminPermissionTests(TestCase):
    def test_regular_customer_cannot_reach_the_user_admin(self):
        user = make_user(username="diego3d", password="vaso-facetado-77")
        self.client.force_login(user)

        response = self.client.get(reverse("admin:accounts_user_changelist"))

        self.assertEqual(response.status_code, 302)
