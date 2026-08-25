"""Testes dos idiomas disponíveis na loja (core.SiteLanguage)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.core.languages import active_language_codes, active_languages, is_language_available
from apps.core.models import SiteLanguage
from apps.core.testing import LanguageResetMixin, make_product, translate_product


class SeededLanguagesTests(TestCase):
    """A migration de dados deixa a loja com os quatro idiomas pedidos."""

    def test_all_supported_languages_are_registered(self):
        self.assertEqual(SiteLanguage.objects.count(), len(settings.LANGUAGES))

    def test_only_four_are_available(self):
        self.assertEqual(active_language_codes(), ["pt-br", "fr", "nl", "en"])

    def test_the_others_stay_registered_but_off(self):
        off = set(SiteLanguage.objects.filter(is_active=False).values_list("code", flat=True))
        self.assertEqual(off, {"de", "es", "it", "tr", "ar"})

    def test_names_come_from_django(self):
        french = SiteLanguage.objects.get(code="fr")

        self.assertEqual(french.name, "French")
        self.assertEqual(french.native_name, "Français")

    def test_order_is_respected(self):
        codes = [language.code for language in active_languages()]
        self.assertEqual(codes[0], "pt-br")


class DefaultLanguageProtectionTests(TestCase):
    def test_default_language_cannot_be_deactivated(self):
        portuguese = SiteLanguage.objects.get(code=settings.LANGUAGE_CODE)
        portuguese.is_active = False

        with self.assertRaises(ValidationError):
            portuguese.full_clean()

    def test_saving_forces_the_default_language_on(self):
        portuguese = SiteLanguage.objects.get(code=settings.LANGUAGE_CODE)
        portuguese.is_active = False
        portuguese.save()

        portuguese.refresh_from_db()
        self.assertTrue(portuguese.is_active)

    def test_default_language_cannot_be_deleted(self):
        portuguese = SiteLanguage.objects.get(code=settings.LANGUAGE_CODE)

        with self.assertRaises(ValidationError):
            portuguese.delete()

    def test_default_is_always_available(self):
        SiteLanguage.objects.all().delete()
        self.assertTrue(is_language_available(settings.LANGUAGE_CODE))

    def test_empty_table_still_gives_a_selector(self):
        SiteLanguage.objects.filter(is_active=True).exclude(
            code=settings.LANGUAGE_CODE
        ).delete()
        SiteLanguage.objects.filter(code=settings.LANGUAGE_CODE).delete()

        codes = active_language_codes()
        self.assertEqual(codes, [settings.LANGUAGE_CODE])

    def test_unsupported_code_is_rejected(self):
        language = SiteLanguage(code="xx", is_active=True)

        with self.assertRaises(ValidationError) as context:
            language.full_clean()
        self.assertIn("code", context.exception.message_dict)


class LanguageAvailabilityTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(sku="GATO-01", name="Gato Pompom", stock_quantity=5)
        translate_product(self.product, "fr", "Chat Pompon")

    def test_available_language_works(self):
        response = self.client.get("/fr/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="fr"')

    def test_language_that_is_off_redirects_to_the_default(self):
        for code in ("de", "es", "it", "tr", "ar"):
            with self.subTest(code=code):
                response = self.client.get(f"/{code}/")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], "/")

    def test_switching_to_a_language_that_is_off_is_refused(self):
        response = self.client.post("/i18n/setlang/", {"language": "de", "next": "/"})

        self.assertEqual(response["Location"], "/")
        self.assertNotIn("django_language", response.cookies)

    def test_activating_a_language_makes_it_work(self):
        german = SiteLanguage.objects.get(code="de")
        german.is_active = True
        german.save()

        response = self.client.get("/de/")
        self.assertEqual(response.status_code, 200)

        switch = self.client.post("/i18n/setlang/", {"language": "de", "next": "/"})
        self.assertEqual(switch["Location"], "/de/")

    def test_deactivating_a_language_takes_it_off_the_selector(self):
        self.assertContains(self.client.get("/"), 'value="fr"')

        french = SiteLanguage.objects.get(code="fr")
        french.is_active = False
        french.save()

        self.assertNotContains(self.client.get("/"), 'value="fr"')

    def test_visitor_inside_a_language_that_is_turned_off_is_sent_home(self):
        """Sem reiniciar nada: a mudança vale na requisição seguinte."""
        self.assertEqual(self.client.get("/fr/").status_code, 200)

        french = SiteLanguage.objects.get(code="fr")
        french.is_active = False
        french.save()

        response = self.client.get("/fr/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_content_still_falls_back_to_portuguese(self):
        """Ativar idioma não exige ter tudo traduzido."""
        response = self.client.get(f"/nl{self.product.get_absolute_url()}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gato Pompom")


class SiteLanguageAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@jdprint.test", password="senha-de-teste"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_changelist_loads(self):
        response = self.client.get(reverse("admin:core_sitelanguage_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Français")
        self.assertContains(response, "Deutsch")

    def test_activate_action(self):
        german = SiteLanguage.objects.get(code="de")
        self.client.post(
            reverse("admin:core_sitelanguage_changelist"),
            {"action": "action_activate", "_selected_action": [str(german.pk)]},
            follow=True,
        )

        german.refresh_from_db()
        self.assertTrue(german.is_active)

    def test_deactivate_action_protects_the_default(self):
        portuguese = SiteLanguage.objects.get(code=settings.LANGUAGE_CODE)
        response = self.client.post(
            reverse("admin:core_sitelanguage_changelist"),
            {"action": "action_deactivate", "_selected_action": [str(portuguese.pk)]},
            follow=True,
        )

        portuguese.refresh_from_db()
        self.assertTrue(portuguese.is_active)
        self.assertContains(response, "não pode ser retirado")

    def test_deactivate_action_works_for_the_others(self):
        french = SiteLanguage.objects.get(code="fr")
        self.client.post(
            reverse("admin:core_sitelanguage_changelist"),
            {"action": "action_deactivate", "_selected_action": [str(french.pk)]},
            follow=True,
        )

        french.refresh_from_db()
        self.assertFalse(french.is_active)

    def test_default_language_cannot_be_deleted_from_the_admin(self):
        portuguese = SiteLanguage.objects.get(code=settings.LANGUAGE_CODE)
        response = self.client.get(
            reverse("admin:core_sitelanguage_change", args=[portuguese.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "deletelink")
