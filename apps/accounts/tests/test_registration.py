"""Cadastro público: conta, cliente, login automático e e-mail de confirmação."""

from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Customer, User
from apps.core.testing import LanguageResetMixin, make_category, make_product

VALID = {
    "username": "diego3d",
    "email": "diego@example.com",
    "password1": "vaso-facetado-77",
    "password2": "vaso-facetado-77",
}


class RegistrationTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("accounts:register")

    def register(self, **changes):
        return self.client.post(self.url, {**VALID, **changes})

    def test_page_loads(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Criar conta")

    def test_signup_asks_only_for_access_data(self):
        """Nome, telefone e empresa pertencem ao cliente, não ao cadastro."""
        response = self.client.get(self.url)
        fields = set(response.context["form"].fields)

        self.assertEqual(fields, {"username", "email", "password1", "password2"})

    def test_user_is_created(self):
        self.register()

        user = User.objects.get(username="diego3d")
        self.assertEqual(user.email, "diego@example.com")

    def test_customer_is_created_empty(self):
        self.register()

        customer = Customer.objects.get(user__username="diego3d")
        self.assertEqual(customer.first_name, "")
        self.assertFalse(customer.is_complete)

    def test_user_is_logged_in_right_away(self):
        response = self.register()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.session["_auth_user_id"], str(User.objects.get().pk)
        )

    def test_email_starts_unverified(self):
        self.register()

        self.assertFalse(User.objects.get().email_verified)

    def test_confirmation_email_is_sent(self):
        self.register()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["diego@example.com"])
        self.assertIn("Confirme", mail.outbox[0].subject)

    @override_settings(
        PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    )
    def test_password_is_hashed(self):
        """Com o hasher real de produção — a suíte usa um rápido por padrão."""
        self.register()

        user = User.objects.get()
        self.assertNotEqual(user.password, VALID["password1"])
        self.assertTrue(user.password.startswith("pbkdf2_"))
        self.assertTrue(user.check_password(VALID["password1"]))

    def test_password_never_appears_in_the_email(self):
        self.register()

        body = mail.outbox[0].body + str(mail.outbox[0].alternatives[0].content)
        self.assertNotIn(VALID["password1"], body)

    def test_mismatched_passwords_are_rejected(self):
        response = self.register(password2="outra-senha-77")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_weak_password_is_rejected(self):
        response = self.register(password1="12345678", password2="12345678")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_password_similar_to_the_username_is_rejected(self):
        response = self.register(password1="diego3d2026", password2="diego3d2026")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_authenticated_visitor_is_redirected(self):
        self.register()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("accounts:dashboard"))

    def test_next_is_respected_when_it_belongs_to_the_site(self):
        response = self.client.post(f"{self.url}?next=/carrinho/", VALID)

        self.assertEqual(response["Location"], "/carrinho/")

    def test_next_pointing_outside_is_ignored(self):
        response = self.client.post(f"{self.url}?next=https://site-falso.example/", VALID)

        self.assertEqual(response["Location"], reverse("accounts:dashboard"))


class RegistrationLanguageTests(LanguageResetMixin, TestCase):
    """O idioma da loja no momento do cadastro vira o idioma do cliente."""

    def test_language_of_the_page_becomes_the_preference(self):
        self.client.post("/fr/conta/criar/", VALID)

        self.assertEqual(User.objects.get().preferred_language, "fr")

    def test_confirmation_email_follows_that_language(self):
        self.client.post("/fr/conta/criar/", VALID)

        self.assertIn("Confirmez", mail.outbox[0].subject)
        self.assertIn("/fr/conta/confirmar/", mail.outbox[0].body)

    def test_portuguese_is_the_default(self):
        self.client.post(reverse("accounts:register"), VALID)

        self.assertEqual(User.objects.get().preferred_language, "pt-br")
        self.assertIn("Confirme", mail.outbox[0].subject)


class RegistrationKeepsTheCartTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(
            sku="VASO-01",
            name="Vaso Facetado",
            category=category,
            price=Decimal("19.90"),
            stock_quantity=5,
        )

    def test_cart_survives_the_signup(self):
        self.client.post(
            reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2}
        )

        self.client.post(reverse("accounts:register"), VALID)

        user = User.objects.get()
        self.assertEqual(user.cart.items.count(), 1)
        self.assertEqual(user.cart.items.get().quantity, 2)
        # A sessão foi esvaziada: agora o banco é a fonte da verdade.
        self.assertFalse(self.client.session.get("cart"))
