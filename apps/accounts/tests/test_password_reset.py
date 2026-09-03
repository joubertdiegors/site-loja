"""Recuperação de senha: mecanismo nativo do Django, telas e e-mail nossos."""

import re

from django.contrib.auth import authenticate
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.testing import LanguageResetMixin, make_user


def link_from_email(message) -> str:
    """Caminho do link contido no e-mail (sem o domínio)."""
    found = re.search(r"https?://[^\s]+/conta/senha/nova/[^\s]+", message.body)
    return found.group(0).split("/conta/")[1] if found else ""


class PasswordResetRequestTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )
        self.url = reverse("accounts:password_reset")

    def test_page_loads(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_existing_account_receives_the_email(self):
        response = self.client.post(self.url, {"email": "diego@example.com"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["diego@example.com"])

    def test_email_is_found_regardless_of_case(self):
        self.client.post(self.url, {"email": "DIEGO@Example.com"})

        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_account_gets_the_same_answer_and_no_email(self):
        known = self.client.post(self.url, {"email": "diego@example.com"}, follow=True)
        cache.clear()
        unknown = self.client.post(self.url, {"email": "ninguem@example.com"}, follow=True)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(known.redirect_chain, unknown.redirect_chain)
        self.assertContains(unknown, "Se existir uma conta")

    def test_inactive_account_gets_no_email(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertEqual(len(mail.outbox), 0)

    @override_settings(SITE_URL="https://jd-print.com")
    def test_link_uses_the_configured_site_url(self):
        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertIn("https://jd-print.com/conta/senha/nova/", mail.outbox[0].body)

    def test_email_is_sent_in_the_language_of_the_screen(self):
        """O idioma é o da tela em que a pessoa pediu — não o do servidor.

        Quem está lendo a loja em francês e clica em "esqueci minha senha"
        recebe o e-mail em francês, com o link já prefixado. Antes o idioma
        vinha só da preferência guardada na conta, e quem tinha conta em
        português navegando em `/fr/` recebia tudo em português.
        """
        self.client.post("/fr/conta/senha/", {"email": "diego@example.com"})

        self.assertIn("/fr/conta/senha/nova/", mail.outbox[0].body)

    def test_the_screen_wins_over_the_language_saved_in_the_account(self):
        """A escolha de agora vale mais que a de quando a conta foi criada.

        É a diferença entre "que idioma esta pessoa fala" e "que idioma esta
        pessoa está lendo neste momento". Quem trocou o idioma no cabeçalho
        trocou por um motivo, e a resposta tem que chegar no idioma da tela em
        que ela digitou o e-mail.
        """
        self.user.preferred_language = "fr"
        self.user.save(update_fields=["preferred_language"])

        self.client.post("/nl/conta/senha/", {"email": "diego@example.com"})

        self.assertIn("/nl/conta/senha/nova/", mail.outbox[0].body)

    def test_the_account_language_is_the_fallback(self):
        """Sem tela, vale a conta — é o caso de um envio disparado pela equipe.

        `email_language` é a mesma função dos dois lados. Aqui ela é exercida
        direto, sem requisição: é assim que o reenvio de confirmação pelo
        Admin a chama, e nesse caminho o idioma do administrador não pode
        vazar para o e-mail do cliente.
        """
        from apps.accounts.emails import email_language

        self.user.preferred_language = "fr"

        self.assertEqual(email_language(self.user), "fr")
        self.assertEqual(email_language(self.user, requested="nl"), "nl")
        # Idioma desligado na loja não passa por estar na requisição.
        self.assertEqual(email_language(self.user, requested="zz"), "fr")

    def test_password_is_never_in_the_email(self):
        self.client.post(self.url, {"email": "diego@example.com"})

        self.assertNotIn("vaso-facetado-77", mail.outbox[0].body)


class PasswordResetConfirmTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(
            username="diego3d", email="diego@example.com", password="vaso-facetado-77"
        )
        self.client.post(reverse("accounts:password_reset"), {"email": "diego@example.com"})
        self.path = "/conta/" + link_from_email(mail.outbox[0])

    def set_password(self, path, password="nova-senha-forte-88"):
        # O Django redireciona o link para uma URL com "set-password" e guarda
        # o token na sessão; é nessa URL que o formulário é enviado.
        response = self.client.get(path, follow=True)
        return self.client.post(
            response.redirect_chain[-1][0] if response.redirect_chain else path,
            {"new_password1": password, "new_password2": password},
        )

    def test_link_opens_the_form(self):
        response = self.client.get(self.path, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["validlink"])

    def test_new_password_works_and_the_old_one_stops_working(self):
        self.set_password(self.path)

        self.assertIsNotNone(authenticate(username="diego3d", password="nova-senha-forte-88"))
        self.assertIsNone(authenticate(username="diego3d", password="vaso-facetado-77"))

    def test_link_cannot_be_used_twice(self):
        self.set_password(self.path)

        response = self.client.get(self.path, follow=True)

        self.assertFalse(response.context["validlink"])

    def test_tampered_token_is_refused(self):
        broken = self.path[:-3] + "aa/"

        response = self.client.get(broken, follow=True)

        self.assertFalse(response.context["validlink"])

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_link_is_refused(self):
        response = self.client.get(self.path, follow=True)

        self.assertFalse(response.context["validlink"])
        self.assertContains(response, "não é mais válido")

    def test_weak_password_is_rejected(self):
        self.set_password(self.path, password="12345678")

        self.assertIsNone(authenticate(username="diego3d", password="12345678"))


class PasswordResetLanguageMatrixTests(LanguageResetMixin, TestCase):
    """Os quatro idiomas, de ponta a ponta: assunto, corpo, link e a tela.

    O que estava errado: o idioma vinha da preferência guardada na conta, e
    não da tela. Quem tinha conta em português e navegava em `/fr/` pedia a
    redefinição numa tela em francês e recebia tudo em português — inclusive
    um link sem o prefixo, que devolvia a pessoa para a loja em português.
    """

    #: (prefixo da URL, código de INTERFACE, trecho do assunto, caminho do link)
    #:
    #: O português da interface é `pt-br`; `pt` é o código de **conteúdo**, o
    #: das tabelas de tradução. Os dois convivem de propósito — ver
    #: `apps.core.i18n.normalize_language` — e aqui vale o da interface, que é
    #: o que decide qual catálogo de mensagens o e-mail usa.
    IDIOMAS = (
        ("", "pt-br", "Redefinição de senha", "/conta/senha/nova/"),
        ("/fr", "fr", "Réinitialisation", "/fr/conta/senha/nova/"),
        ("/nl", "nl", "Wachtwoord", "/nl/conta/senha/nova/"),
        ("/en", "en", "password", "/en/conta/senha/nova/"),
    )

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user(
            username="multilingue", email="multi@example.test", password="vaso-facetado-77"
        )

    def pedir(self, prefixo):
        mail.outbox = []
        cache.clear()
        return self.client.post(f"{prefixo}/conta/senha/", {"email": "multi@example.test"})

    def test_the_link_carries_the_language_prefix(self):
        for prefixo, idioma, _assunto, caminho in self.IDIOMAS:
            with self.subTest(idioma=idioma):
                self.pedir(prefixo)

                self.assertEqual(len(mail.outbox), 1)
                self.assertIn(caminho, mail.outbox[0].body)

    def test_the_subject_is_translated(self):
        for prefixo, idioma, assunto, _caminho in self.IDIOMAS:
            with self.subTest(idioma=idioma):
                self.pedir(prefixo)

                self.assertIn(assunto.lower(), mail.outbox[0].subject.lower())

    def test_the_html_body_declares_the_language(self):
        for prefixo, idioma, _assunto, _caminho in self.IDIOMAS:
            with self.subTest(idioma=idioma):
                self.pedir(prefixo)

                html = mail.outbox[0].alternatives[0][0]
                self.assertIn(f'lang="{idioma}"', html)

    def test_the_screen_that_asks_is_in_the_same_language_as_the_email(self):
        """Assunto, corpo e tela: o mesmo idioma, do começo ao fim do fluxo.

        A verificação é sobre o idioma **ativo** da requisição, e não sobre o
        atributo `lang` do HTML: o português da loja é `pt-br` na marcação e
        `pt` no código de idioma, e comparar as duas coisas testaria a grafia
        em vez do comportamento.
        """
        for prefixo, idioma, _assunto, _caminho in self.IDIOMAS:
            with self.subTest(idioma=idioma):
                tela = self.client.get(f"{prefixo}/conta/senha/")

                self.assertEqual(tela.status_code, 200)
                self.assertEqual(tela.context["LANGUAGE_CODE"], idioma)

    def test_the_link_still_opens_the_form(self):
        """Trocar o idioma não pode quebrar o token: a segurança não mudou."""
        for prefixo, idioma, _assunto, _caminho in self.IDIOMAS:
            with self.subTest(idioma=idioma):
                self.pedir(prefixo)
                caminho = "/conta/" + link_from_email(mail.outbox[0])

                resposta = self.client.get(f"{prefixo}{caminho}", follow=True)

                self.assertEqual(resposta.status_code, 200)

    def test_the_response_is_the_same_whether_the_account_exists(self):
        """A correção de idioma não pode ter aberto enumeração de contas."""
        conhecida = self.pedir("/fr")
        desconhecida = self.client.post(
            "/fr/conta/senha/", {"email": "ninguem@example.test"}
        )

        self.assertEqual(conhecida.status_code, desconhecida.status_code)
        self.assertEqual(conhecida["Location"], desconhecida["Location"])
