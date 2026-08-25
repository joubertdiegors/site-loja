"""Nome de usuário: formato, nomes reservados e unicidade sem caixa.

A unicidade é testada nas três camadas em que ela existe — formulário, modelo e
banco. As três precisam existir: o formulário dá a mensagem, o modelo protege o
admin, e o banco é a última barreira, a que vale para importação, shell e
qualquer interface futura.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.forms import RegistrationForm
from apps.accounts.models import User
from apps.core.testing import make_user

VALID = {
    "username": "diego3d",
    "email": "diego@example.com",
    "password1": "vaso-facetado-77",
    "password2": "vaso-facetado-77",
}


def form_with(**changes):
    return RegistrationForm(data={**VALID, **changes})


class UsernameFormatTests(TestCase):
    def test_username_is_required(self):
        form = form_with(username="")

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_valid_username_is_accepted(self):
        self.assertTrue(form_with().is_valid())

    def test_username_with_space_is_rejected(self):
        form = form_with(username="diego 3d")

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_username_with_forbidden_characters_is_rejected(self):
        for invalid in ("diego/3d", "diego.3d", "diego!", "diego#3d", "diegão", "diego%20"):
            with self.subTest(username=invalid):
                self.assertFalse(form_with(username=invalid).is_valid())

    def test_username_cannot_be_an_email_address(self):
        form = form_with(username="diego@example.com")

        self.assertFalse(form.is_valid())
        self.assertIn(
            "não pode ser um endereço de e-mail", " ".join(form.errors["username"])
        )

    def test_username_too_short_is_rejected(self):
        self.assertFalse(form_with(username="jd").is_valid())

    def test_username_too_long_is_rejected(self):
        self.assertFalse(form_with(username="d" * 31).is_valid())

    def test_username_cannot_start_or_end_with_separator(self):
        for invalid in ("-diego", "diego-", "_diego", "diego_"):
            with self.subTest(username=invalid):
                self.assertFalse(form_with(username=invalid).is_valid())

    def test_hyphen_and_underscore_inside_are_accepted(self):
        for valid in ("diego-3d", "diego_3d", "jd-print-2"):
            with self.subTest(username=valid):
                self.assertTrue(form_with(username=valid).is_valid())

    def test_reserved_username_is_rejected_in_public_signup(self):
        for reserved in ("admin", "Carrinho", "SUPORTE", "checkout"):
            with self.subTest(username=reserved):
                self.assertFalse(form_with(username=reserved).is_valid())

    def test_reserved_username_is_still_available_to_the_owner(self):
        """A lista de reservados é política do cadastro público.

        Quem instala a loja precisa conseguir criar o superusuário ``admin``.
        """
        user = User.objects.create_superuser(
            username="admin", email="admin@jdprint.test", password="senha-de-teste-77"
        )

        self.assertEqual(user.username, "admin")


class UsernameUniquenessTests(TestCase):
    def setUp(self):
        self.existing = make_user(username="diego3d", email="diego@example.com")

    def test_duplicate_username_is_rejected(self):
        form = form_with(username="diego3d", email="outro@example.com")

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_duplicate_username_ignoring_case_is_rejected(self):
        for variation in ("Diego3D", "DIEGO3D", "dIeGo3D"):
            with self.subTest(username=variation):
                form = form_with(username=variation, email="outro@example.com")
                self.assertFalse(form.is_valid())
                self.assertIn("username", form.errors)

    def test_model_validation_catches_the_case_variation(self):
        user = User(username="DIEGO3D", email="outro@example.com")

        with self.assertRaises(ValidationError) as context:
            user.full_clean()
        self.assertIn("username", context.exception.message_dict)

    def test_database_is_the_last_barrier(self):
        """Sem formulário e sem ``full_clean``: o banco ainda recusa."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(username="Diego3D", email="outro@example.com")

    def test_three_case_variations_are_the_same_account(self):
        for variation in ("Diego3D", "diego3d", "DIEGO3D"):
            with self.subTest(username=variation):
                self.assertEqual(
                    User.objects.get(username__iexact=variation).pk, self.existing.pk
                )

    def test_signup_page_shows_the_message(self):
        response = self.client.post(
            reverse("accounts:register"), {**VALID, "username": "DIEGO3D", "email": "n@e.com"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "não está disponível")
        self.assertEqual(User.objects.count(), 1)
