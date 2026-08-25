"""O cliente comercial é uma entidade separada da conta de acesso.

A separação não é estética: o checkout vai pedir nome, telefone, endereço e
NIF, e nada disso pode virar requisito para simplesmente entrar na loja.
"""

from django.contrib.auth import authenticate
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Customer, User
from apps.core.testing import LanguageResetMixin, make_user

VALID = {
    "username": "diego3d",
    "email": "diego@example.com",
    "password1": "vaso-facetado-77",
    "password2": "vaso-facetado-77",
}


class CustomerModelTests(TestCase):
    def setUp(self):
        self.user = make_user(username="diego3d", email="diego@example.com")

    def test_customer_is_linked_to_the_right_user(self):
        self.assertEqual(self.user.customer.user_id, self.user.pk)

    def test_customer_holds_no_authentication_data(self):
        """Nem senha, nem e-mail de login, nem estado de confirmação."""
        fields = {field.name for field in Customer._meta.get_fields()}

        self.assertTrue(fields.isdisjoint({"password", "email", "email_verified", "last_login"}))

    def test_commercial_data_cannot_be_used_to_authenticate(self):
        customer = self.user.customer
        customer.first_name = "Diego"
        customer.company_name = "JD PRINT SRL"
        customer.save()

        self.assertIsNone(authenticate(username="Diego", password="senha-de-teste-77"))
        self.assertIsNone(authenticate(username="JD PRINT SRL", password="senha-de-teste-77"))

    def test_full_name_and_completeness(self):
        customer = self.user.customer
        self.assertFalse(customer.is_complete)

        customer.first_name = "Diego"
        customer.last_name = "Joubert"
        customer.save()

        self.assertEqual(customer.full_name, "Diego Joubert")
        self.assertTrue(customer.is_complete)

    def test_vat_is_normalized(self):
        customer = self.user.customer
        customer.vat_number = "be 0123 456 789"
        customer.save()

        customer.refresh_from_db()
        self.assertEqual(customer.vat_number, "BE0123456789")

    def test_deleting_the_account_deletes_the_customer(self):
        self.user.delete()

        self.assertEqual(Customer.objects.count(), 0)

    def test_one_customer_per_account(self):
        with self.assertRaises(Exception):
            Customer.objects.create(user=self.user)


class CustomerCreationTests(LanguageResetMixin, TestCase):
    def test_signup_creates_both(self):
        self.client.post(reverse("accounts:register"), VALID)

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Customer.objects.get().user, User.objects.get())


class ProfileViewTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(username="diego3d", password="vaso-facetado-77")
        self.url = reverse("accounts:profile")

    def test_login_is_required(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_page_shows_only_commercial_fields(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)
        fields = set(response.context["form"].fields)

        self.assertEqual(
            fields,
            {"first_name", "last_name", "phone", "company_name", "vat_number", "preferred_language"},
        )

    def test_saving_updates_the_customer(self):
        self.client.force_login(self.user)

        self.client.post(
            self.url,
            {
                "first_name": "Diego",
                "last_name": "Joubert",
                "phone": "+32 470 00 00 00",
                "company_name": "",
                "vat_number": "",
                "preferred_language": "fr",
            },
        )

        self.user.refresh_from_db()
        self.user.customer.refresh_from_db()
        self.assertEqual(self.user.customer.full_name, "Diego Joubert")
        # O idioma preferido é do usuário, não do cliente.
        self.assertEqual(self.user.preferred_language, "fr")

    def test_invalid_phone_is_rejected(self):
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {
                "first_name": "Diego",
                "last_name": "Joubert",
                "phone": "telefone?",
                "company_name": "",
                "vat_number": "",
                "preferred_language": "pt-br",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("phone", response.context["form"].errors)

    def test_language_choices_come_from_the_store_languages(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)
        codes = [code for code, _label in response.context["form"].fields["preferred_language"].choices]

        self.assertEqual(codes, ["pt-br", "fr", "nl", "en"])
