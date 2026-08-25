"""E-mail: obrigatório, normalizado e único mesmo com outra caixa.

``diego@example.com`` e ``DIEGO@Example.com`` são a mesma pessoa. Duas contas
para o mesmo endereço significariam dois carrinhos, dois históricos e uma
recuperação de senha ambígua.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

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


class EmailFormatTests(TestCase):
    def test_email_is_required(self):
        form = form_with(email="")

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_invalid_email_is_rejected(self):
        for invalid in ("diego", "diego@", "@example.com", "diego example.com"):
            with self.subTest(email=invalid):
                self.assertFalse(form_with(email=invalid).is_valid())

    def test_email_is_stored_in_lowercase(self):
        form = form_with(email="Diego@Example.COM")
        self.assertTrue(form.is_valid())
        user = form.save()

        self.assertEqual(user.email, "diego@example.com")

    def test_manager_normalizes_on_create(self):
        user = User.objects.create_user(
            username="maria", email="  MARIA@Example.com ", password="senha-de-teste-77"
        )

        self.assertEqual(user.email, "maria@example.com")


class EmailUniquenessTests(TestCase):
    def setUp(self):
        self.existing = make_user(username="diego3d", email="diego@example.com")

    def test_duplicate_email_is_rejected(self):
        form = form_with(username="outro", email="diego@example.com")

        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_duplicate_email_with_different_case_is_rejected(self):
        for variation in ("DIEGO@example.com", "Diego@Example.com", "diego@EXAMPLE.COM"):
            with self.subTest(email=variation):
                form = form_with(username="outro", email=variation)
                self.assertFalse(form.is_valid())
                self.assertIn("email", form.errors)

    def test_model_validation_catches_the_case_variation(self):
        user = User(username="outro", email="DIEGO@example.com")

        with self.assertRaises(ValidationError) as context:
            user.full_clean()
        self.assertIn("email", context.exception.message_dict)

    def test_database_is_the_last_barrier(self):
        """``objects.create`` não passa por formulário nem por ``full_clean``."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(username="outro", email="DIEGO@EXAMPLE.COM")

    def test_bulk_create_is_also_refused(self):
        """Importação em massa é o caminho clássico para furar a validação."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.bulk_create([User(username="outro", email="Diego@Example.com")])

    def test_only_one_account_exists_after_all_attempts(self):
        self.assertEqual(User.objects.count(), 1)


class EmailChangeTests(TestCase):
    """Preparação para a tela futura de alterar e-mail."""

    def setUp(self):
        self.user = make_user(username="diego3d", email="diego@example.com")
        self.user.mark_email_verified()

    def test_changing_the_email_drops_the_confirmation(self):
        changed = self.user.set_email("novo@example.com")

        self.user.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(self.user.email, "novo@example.com")
        self.assertFalse(self.user.email_verified)
        self.assertIsNone(self.user.email_verified_at)

    def test_same_email_in_another_case_is_not_a_change(self):
        changed = self.user.set_email("DIEGO@Example.com")

        self.user.refresh_from_db()
        self.assertFalse(changed)
        self.assertTrue(self.user.email_verified)
