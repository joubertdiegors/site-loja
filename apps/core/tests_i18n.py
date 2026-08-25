"""Testes da troca de idioma.

A falha corrigida nesta etapa: partindo de uma URL com prefixo (``/en/``), a
view ``set_language`` do Django devolvia a mesma URL prefixada — o cookie
mudava, mas o prefixo continuava mandando no idioma da página. Estes testes
cobrem a matriz completa de combinações para que a regressão não volte.
"""

from django.test import TestCase
from django.urls import reverse

from apps.core.i18n import normalize_language, translate_path
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    make_section,
    translate_category,
    translate_product,
    translate_section,
)

SET_LANGUAGE = "/i18n/setlang/"

#: Idiomas oferecidos na loja (core.SiteLanguage). Alemão e companhia
#: continuam suportados pelo sistema, mas desligados — ver InactiveLanguageTests.
HOME = {"pt-br": "/", "fr": "/fr/", "en": "/en/", "nl": "/nl/"}


class TranslatePathTests(TestCase):
    """A função que resolve a URL no idioma de destino."""

    def test_removes_the_prefix_for_the_default_language(self):
        self.assertEqual(translate_path("/fr/", "pt-br"), "/")
        self.assertEqual(translate_path("/en/modelos/", "pt-br"), "/modelos/")

    def test_swaps_between_two_prefixed_languages(self):
        self.assertEqual(translate_path("/en/", "fr"), "/fr/")
        self.assertEqual(translate_path("/nl/modelos/", "en"), "/en/modelos/")

    def test_adds_the_prefix_leaving_the_default_language(self):
        self.assertEqual(translate_path("/", "fr"), "/fr/")
        self.assertEqual(translate_path("/modelos/", "nl"), "/nl/modelos/")

    def test_keeps_the_query_string_and_fragment(self):
        self.assertEqual(
            translate_path("/modelos/?categoria=gatos&ordenar=nome-az", "fr"),
            "/fr/modelos/?categoria=gatos&ordenar=nome-az",
        )
        self.assertEqual(translate_path("/?a=1#topo", "en"), "/en/?a=1#topo")

    def test_unresolvable_path_still_changes_language(self):
        self.assertEqual(translate_path("/fr/pagina-inexistente/", "pt-br"), "/pagina-inexistente/")
        self.assertEqual(translate_path("/pagina-inexistente/", "fr"), "/fr/pagina-inexistente/")

    def test_normalize_language_maps_to_content_language(self):
        self.assertEqual(normalize_language("pt-br"), "pt")
        self.assertEqual(normalize_language("fr-be"), "fr")
        self.assertEqual(normalize_language("ja"), "pt")


class LanguageSwitchMatrixTests(LanguageResetMixin, TestCase):
    """Todas as combinações pedidas: PT↔FR↔EN↔NL."""

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        translate_category(self.category, "fr", "Modèles")
        translate_category(self.category, "en", "Models")

        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category, is_featured=True
        )
        translate_product(self.product, "fr", "Chat Pompon")
        translate_product(self.product, "en", "Pompom Cat")

        self.section = make_section(internal_name="Destaques", title="Destaques de Modelos")
        translate_section(self.section, "fr", "Modèles à la une")
        translate_section(self.section, "en", "Featured Models")

    def switch(self, from_url, to_language):
        """Faz a troca como o seletor do header faz e segue o redirecionamento."""
        self.client.get(from_url)
        response = self.client.post(SET_LANGUAGE, {"language": to_language, "next": from_url})
        self.assertEqual(response.status_code, 302)
        return response["Location"], self.client.get(response["Location"])

    def assertLanguage(self, response, code):
        self.assertContains(response, f'lang="{code}"')

    # -- a partir do português (sem prefixo) -------------------------------

    def test_pt_to_fr(self):
        url, response = self.switch(HOME["pt-br"], "fr")

        self.assertEqual(url, "/fr/")
        self.assertLanguage(response, "fr")
        self.assertContains(response, "Chat Pompon")
        self.assertContains(response, "Modèles à la une")

    def test_pt_to_en(self):
        url, response = self.switch(HOME["pt-br"], "en")

        self.assertEqual(url, "/en/")
        self.assertContains(response, "Pompom Cat")

    def test_pt_to_nl(self):
        url, response = self.switch(HOME["pt-br"], "nl")

        self.assertEqual(url, "/nl/")
        self.assertLanguage(response, "nl")

    # -- a partir de uma URL com prefixo (o caso que estava quebrado) ------

    def test_fr_to_en(self):
        url, response = self.switch(HOME["fr"], "en")

        self.assertEqual(url, "/en/")
        self.assertLanguage(response, "en")
        self.assertContains(response, "Pompom Cat")
        self.assertNotContains(response, "Chat Pompon")

    def test_en_to_pt(self):
        url, response = self.switch(HOME["en"], "pt-br")

        self.assertEqual(url, "/")
        self.assertLanguage(response, "pt-br")
        self.assertContains(response, "Gato Pompom")
        self.assertNotContains(response, "Pompom Cat")

    def test_en_to_fr(self):
        url, response = self.switch(HOME["en"], "fr")

        self.assertEqual(url, "/fr/")
        self.assertContains(response, "Chat Pompon")

    def test_fr_to_pt(self):
        url, response = self.switch(HOME["fr"], "pt-br")

        self.assertEqual(url, "/")
        self.assertContains(response, "Gato Pompom")

    def test_nl_to_pt(self):
        url, response = self.switch(HOME["nl"], "pt-br")

        self.assertEqual(url, "/")
        self.assertLanguage(response, "pt-br")

    def test_nl_to_en(self):
        url, response = self.switch(HOME["nl"], "en")

        self.assertEqual(url, "/en/")
        self.assertContains(response, "Pompom Cat")

    def test_every_pair_works(self):
        """Matriz completa, para nenhuma combinação escapar."""
        for source_code, source_url in HOME.items():
            for target_code, target_url in HOME.items():
                if source_code == target_code:
                    continue
                with self.subTest(de=source_code, para=target_code):
                    self.client.cookies.clear()
                    url, response = self.switch(source_url, target_code)
                    self.assertEqual(url, target_url)
                    self.assertContains(response, f'lang="{target_code}"')


class LanguagePersistenceTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category, stock_quantity=5
        )
        translate_product(self.product, "fr", "Chat Pompon")

    def test_cookie_is_written(self):
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})
        self.assertEqual(self.client.cookies["django_language"].value, "fr")

    def test_language_survives_navigation(self):
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})

        for path in ("/fr/", "/fr/modelos/", "/fr/carrinho/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'lang="fr"')

    def test_unprefixed_url_redirects_to_the_chosen_language(self):
        """Voltar pela URL sem prefixo não pode desfazer a escolha."""
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})

        response = self.client.get("/modelos/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/fr/modelos/")

        self.assertContains(self.client.get("/modelos/", follow=True), 'lang="fr"')

    def test_home_without_prefix_also_follows_the_choice(self):
        self.client.post(SET_LANGUAGE, {"language": "en", "next": "/"})
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/en/")

    def test_query_string_survives_the_redirect(self):
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})
        response = self.client.get("/modelos/?ordenar=nome-az")

        self.assertEqual(response["Location"], "/fr/modelos/?ordenar=nome-az")

    def test_portuguese_cookie_does_not_redirect(self):
        self.client.post(SET_LANGUAGE, {"language": "pt-br", "next": "/"})
        response = self.client.get("/modelos/")

        self.assertEqual(response.status_code, 200)

    def test_visitor_without_cookie_gets_the_default_language(self):
        response = self.client.get("/modelos/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="pt-br"')

    def test_admin_is_never_redirected(self):
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})
        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_post_is_never_redirected(self):
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})
        response = self.client.post(
            "/carrinho/adicionar/", {"product_id": self.product.pk, "next": "/"}
        )

        self.assertNotEqual(response.status_code, 301)
        self.assertIn(response.status_code, (302, 404))

    def test_language_survives_a_cart_action(self):
        self.client.post(SET_LANGUAGE, {"language": "fr", "next": "/"})
        self.client.post(
            "/fr/carrinho/adicionar/", {"product_id": self.product.pk, "next": "/fr/modelos/"}
        )
        response = self.client.get("/fr/carrinho/")

        self.assertContains(response, 'lang="fr"')
        self.assertContains(response, "Chat Pompon")


class LanguageFallbackTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="GATO-01", name="Gato Pompom", category=self.category)
        translate_product(self.product, "fr", "Chat Pompon")
        make_section(internal_name="Destaques", title="Destaques de Modelos")
        self.product.is_featured = True
        self.product.save()

    def test_language_without_translation_falls_back_to_portuguese(self):
        # Holandês está disponível na loja, mas o produto só tem PT e FR.
        response = self.client.get("/nl/")

        self.assertContains(response, "Gato Pompom")
        self.assertNotContains(response, "Chat Pompon")

    def test_never_shows_an_empty_name(self):
        response = self.client.get("/nl/modelos/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gato Pompom")


class SetLanguageSecurityTests(LanguageResetMixin, TestCase):
    def test_get_is_not_allowed(self):
        response = self.client.get(SET_LANGUAGE)
        self.assertEqual(response.status_code, 405)

    def test_external_next_is_refused(self):
        response = self.client.post(
            SET_LANGUAGE, {"language": "fr", "next": "https://exemplo-malicioso.test/"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/fr/")

    def test_unknown_language_is_ignored(self):
        response = self.client.post(SET_LANGUAGE, {"language": "xx", "next": "/"})

        self.assertEqual(response["Location"], "/")
        self.assertNotIn("django_language", response.cookies)

    def test_missing_language_is_ignored(self):
        response = self.client.post(SET_LANGUAGE, {"next": "/"})
        self.assertEqual(response["Location"], "/")

    def test_url_name_is_preserved(self):
        self.assertEqual(reverse("set_language"), SET_LANGUAGE)


class LanguageSelectorTests(LanguageResetMixin, TestCase):
    def test_selector_lists_only_the_available_languages(self):
        response = self.client.get("/")

        for code in ("pt-br", "fr", "nl", "en"):
            with self.subTest(code=code):
                self.assertContains(response, f'value="{code}"')

    def test_selector_hides_languages_that_are_off(self):
        response = self.client.get("/")

        for code in ("de", "es", "it", "tr", "ar"):
            with self.subTest(code=code):
                self.assertNotContains(response, f'value="{code}"')

    def test_selector_marks_the_current_language(self):
        response = self.client.get("/fr/")
        self.assertContains(response, 'aria-current="true"')

    def test_selector_posts_the_current_path(self):
        response = self.client.get("/fr/modelos/")
        self.assertContains(response, 'name="next" value="/fr/modelos/"')

    def test_selector_keeps_the_query_string(self):
        make_category(slug="modelos", name="Modelos")
        response = self.client.get("/modelos/?categoria=nao-existe&ordenar=nome-az")

        self.assertContains(response, "ordenar=nome-az")
