from django.test import SimpleTestCase

from apps.core.i18n import normalize_language


class NormalizeLanguageTests(SimpleTestCase):
    def test_exact_code(self):
        self.assertEqual(normalize_language("fr"), "fr")

    def test_regional_code_falls_back_to_base(self):
        self.assertEqual(normalize_language("pt-BR"), "pt")
        self.assertEqual(normalize_language("fr-be"), "fr")

    def test_unknown_code_falls_back_to_default(self):
        self.assertEqual(normalize_language("ja"), "pt")
        self.assertEqual(normalize_language(None), "pt")
