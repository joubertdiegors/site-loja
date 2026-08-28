"""Configuração de e-mail: prioridade, segredo e teste de envio.

Três coisas para provar:

1. **prioridade** — o Admin, quando ativo e completo; o ``.env`` no resto do
   tempo. Nunca as duas ao mesmo tempo, nunca metade de cada;
2. **segredo** — a senha do SMTP não aparece na tela, no HTML, no log nem na
   mensagem de erro;
3. **teste de envio** — usa a mesma conexão dos e-mails de pedido, e informa o
   resultado sem vazar credencial.
"""

import logging
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core import mailer, secrets
from apps.core.models import EmailSettings

SENHA = "s3nh4-do-smtp-que-nao-pode-vazar"


def configurar(**overrides):
    obj = EmailSettings.load()
    dados = {
        "is_active": True,
        "host": "smtp.exemplo.test",
        "port": 587,
        "username": "loja@exemplo.test",
        "use_tls": True,
        "use_ssl": False,
        "from_email": "loja@exemplo.test",
        "from_name": "JD PRINT",
    }
    dados.update(overrides)
    for campo, valor in dados.items():
        setattr(obj, campo, valor)
    obj.password = overrides.get("password", SENHA)
    obj.save()
    return obj


class SecretStorageTests(TestCase):
    """A senha vai cifrada para o banco."""

    def test_the_plaintext_is_not_in_the_column(self):
        obj = configurar()

        self.assertNotIn(SENHA, obj.password_encrypted)
        self.assertTrue(obj.password_encrypted)

    def test_it_comes_back_intact(self):
        configurar()
        obj = EmailSettings.objects.get(pk=1)

        self.assertEqual(obj.password, SENHA)

    def test_the_raw_column_read_from_the_database_is_not_the_password(self):
        """Um dump do banco não pode carregar a credencial."""
        configurar()
        cru = EmailSettings.objects.values_list("password_encrypted", flat=True).get(pk=1)

        self.assertNotIn(SENHA, cru)

    def test_an_empty_password_stays_empty(self):
        obj = configurar(password="")

        self.assertEqual(obj.password_encrypted, "")
        self.assertEqual(obj.password, "")
        self.assertFalse(obj.has_password)

    def test_a_token_written_with_another_key_comes_back_empty(self):
        """Trocar a SECRET_KEY invalida o que foi cifrado — e isso é o certo."""
        cifrado = secrets.encrypt(SENHA)

        with override_settings(SECRET_KEY="outra-chave-completamente-diferente"):
            with self.assertLogs("apps.core.secrets", level=logging.WARNING):
                self.assertEqual(secrets.decrypt(cifrado), "")

    def test_a_corrupt_token_does_not_explode(self):
        with self.assertLogs("apps.core.secrets", level=logging.WARNING):
            self.assertEqual(secrets.decrypt("isto-não-é-um-token"), "")

    def test_masking_never_shows_a_short_secret(self):
        self.assertEqual(secrets.mask("1234"), "••••••••")
        self.assertNotIn("1234", secrets.mask("1234"))

    def test_masking_shows_only_the_edges_of_a_long_secret(self):
        mascarado = secrets.mask(SENHA)

        self.assertNotIn(SENHA, mascarado)
        self.assertTrue(mascarado.startswith("s3"))


class PriorityTests(TestCase):
    """Admin ativo manda; sem ele, o ``.env``."""

    @override_settings(EMAIL_HOST="smtp.do-env.test", EMAIL_HOST_USER="env@exemplo.test")
    def test_without_admin_config_the_env_wins(self):
        config = mailer.resolve()

        self.assertEqual(config.source, "env")
        self.assertEqual(config.host, "smtp.do-env.test")

    @override_settings(EMAIL_HOST="smtp.do-env.test")
    def test_an_inactive_admin_config_does_not_win(self):
        configurar(is_active=False)

        config = mailer.resolve()
        self.assertEqual(config.source, "env")
        self.assertEqual(config.host, "smtp.do-env.test")

    @override_settings(EMAIL_HOST="smtp.do-env.test")
    def test_an_active_admin_config_wins(self):
        configurar()

        config = mailer.resolve()
        self.assertEqual(config.source, "admin")
        self.assertEqual(config.host, "smtp.exemplo.test")
        self.assertEqual(config.password, SENHA)

    def test_an_active_config_without_a_host_is_not_usable(self):
        """Meia configuração não pode derrubar o envio: cai no ``.env``."""
        obj = EmailSettings.load()
        obj.is_active = True
        obj.host = ""
        obj.save()

        self.assertIsNone(EmailSettings.active())
        self.assertEqual(mailer.resolve().source, "env")

    def test_the_sender_comes_from_the_admin_config(self):
        configurar(from_name="Loja JD", from_email="contato@jd.test")

        self.assertEqual(mailer.resolve().from_email, "Loja JD <contato@jd.test>")

    def test_the_admin_recipients_come_from_the_admin_config(self):
        configurar(admin_recipients="producao@jd.test\nchefe@jd.test")

        self.assertEqual(
            mailer.resolve().admin_recipients, ["producao@jd.test", "chefe@jd.test"]
        )

    def test_recipients_accept_commas_too(self):
        obj = configurar(admin_recipients="a@jd.test, b@jd.test")

        self.assertEqual(obj.recipient_list(), ["a@jd.test", "b@jd.test"])

    @override_settings(ORDER_ADMIN_EMAILS=["padrao@jd.test"])
    def test_empty_recipients_fall_back_to_settings(self):
        configurar(admin_recipients="")

        self.assertEqual(mailer.resolve().admin_recipients, ["padrao@jd.test"])


class ValidationTests(TestCase):
    def test_tls_and_ssl_together_are_refused(self):
        from django.core.exceptions import ValidationError

        obj = EmailSettings(host="smtp.test", use_tls=True, use_ssl=True)
        with self.assertRaises(ValidationError) as contexto:
            obj.full_clean()

        self.assertIn("use_tls", contexto.exception.message_dict)

    def test_activating_without_a_host_is_refused(self):
        from django.core.exceptions import ValidationError

        obj = EmailSettings(is_active=True, host="", from_email="a@b.test")
        with self.assertRaises(ValidationError) as contexto:
            obj.full_clean()

        self.assertIn("host", contexto.exception.message_dict)

    def test_activating_without_a_sender_is_refused(self):
        from django.core.exceptions import ValidationError

        obj = EmailSettings(is_active=True, host="smtp.test", from_email="")
        with self.assertRaises(ValidationError) as contexto:
            obj.full_clean()

        self.assertIn("from_email", contexto.exception.message_dict)

    def test_there_is_only_ever_one_row(self):
        """A garantia fica na tabela, não numa convenção."""
        from django.db import IntegrityError, transaction

        EmailSettings.objects.create(host="a.test")

        with self.assertRaises(IntegrityError), transaction.atomic():
            EmailSettings.objects.create(host="b.test")

        self.assertEqual(EmailSettings.objects.count(), 1)

    def test_load_edits_the_single_row(self):
        EmailSettings.objects.create(host="a.test")

        obj = EmailSettings.load()
        obj.host = "b.test"
        obj.save()

        self.assertEqual(EmailSettings.objects.count(), 1)
        self.assertEqual(EmailSettings.objects.get().host, "b.test")


class SanitizeErrorTests(TestCase):
    """O erro que aparece na tela não pode carregar credencial."""

    def test_a_useful_error_survives(self):
        texto = mailer.sanitize_error(ConnectionRefusedError("Connection refused"))

        self.assertIn("Connection refused", texto)

    def test_an_auth_line_is_hidden(self):
        erro = Exception("SMTPAuthenticationError AUTH PLAIN AGxvamFAZXhlbXBsby50ZXN0AHNlbmhh")
        texto = mailer.sanitize_error(erro)

        self.assertNotIn("AGxvamFAZXhlbXBsby50ZXN0AHNlbmhh", texto)
        self.assertIn("[oculto]", texto)

    def test_a_password_in_the_message_is_hidden(self):
        texto = mailer.sanitize_error(Exception(f"login failed password={SENHA}"))

        self.assertNotIn(SENHA, texto)

    def test_the_message_is_truncated(self):
        texto = mailer.sanitize_error(Exception("x" * 1000))

        self.assertLessEqual(len(texto), 280)


class SendTestEmailTests(TestCase):
    def test_a_successful_test_reports_the_source(self):
        configurar(host="", is_active=False)

        ok, mensagem = mailer.send_test_email("eu@exemplo.test")

        self.assertTrue(ok)
        self.assertIn(".env", mensagem)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["eu@exemplo.test"])

    def test_a_failure_is_reported_without_the_password(self):
        configurar()

        with mock.patch.object(
            mailer, "build_connection", side_effect=Exception(f"auth failed {SENHA}")
        ):
            ok, mensagem = mailer.send_test_email("eu@exemplo.test")

        self.assertFalse(ok)
        self.assertNotIn(SENHA, mensagem)

    def test_a_connection_that_delivers_nothing_is_a_failure(self):
        conexao = mock.Mock()
        conexao.send_messages.return_value = 0

        with mock.patch.object(mailer, "build_connection", return_value=conexao):
            ok, mensagem = mailer.send_test_email("eu@exemplo.test")

        self.assertFalse(ok)
        self.assertIn("não entregou", mensagem)


class ConfiguredBackendTests(TestCase):
    """Todo envio do projeto passa pela mesma regra."""

    def test_the_sender_of_the_admin_config_is_applied(self):
        configurar(from_name="Loja JD", from_email="contato@jd.test", host="")
        configurar(from_name="Loja JD", from_email="contato@jd.test", is_active=False)

        obj = EmailSettings.load()
        obj.is_active = True
        obj.host = "smtp.exemplo.test"
        obj.save()

        mensagem = mail.EmailMultiAlternatives("assunto", "corpo", to=["a@b.test"])
        backend = mailer.ConfiguredEmailBackend()
        backend._apply_sender(mensagem, mailer.resolve())

        self.assertEqual(mensagem.from_email, "Loja JD <contato@jd.test>")

    def test_a_message_with_its_own_sender_is_left_alone(self):
        configurar(from_email="contato@jd.test")

        mensagem = mail.EmailMultiAlternatives(
            "assunto", "corpo", from_email="outro@jd.test", to=["a@b.test"]
        )
        mailer.ConfiguredEmailBackend._apply_sender(mensagem, mailer.resolve())

        self.assertEqual(mensagem.from_email, "outro@jd.test")

    def test_the_reply_to_is_applied(self):
        configurar(reply_to="resposta@jd.test")

        mensagem = mail.EmailMultiAlternatives("assunto", "corpo", to=["a@b.test"])
        mailer.ConfiguredEmailBackend._apply_sender(mensagem, mailer.resolve())

        self.assertEqual(mensagem.reply_to, ["resposta@jd.test"])


class EmailSettingsAdminTests(TestCase):
    def setUp(self):
        self.super = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jd.test", password="senha-de-teste"
        )
        self.staff = get_user_model().objects.create_user(
            username="atendente", email="atendente@jd.test", password="senha-de-teste"
        )
        self.staff.is_staff = True
        self.staff.save()
        configurar()

    def change_url(self):
        return reverse("admin:core_emailsettings_change", args=[1])

    def test_the_password_is_not_in_the_page(self):
        """O teste que sustenta esta tela."""
        self.client.force_login(self.super)
        resposta = self.client.get(self.change_url())

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, SENHA)

    def test_the_encrypted_token_is_not_in_the_page_either(self):
        self.client.force_login(self.super)
        cifrado = EmailSettings.objects.get(pk=1).password_encrypted
        resposta = self.client.get(self.change_url())

        self.assertNotContains(resposta, cifrado)

    def test_the_page_says_a_password_is_stored(self):
        self.client.force_login(self.super)
        resposta = self.client.get(self.change_url())

        self.assertContains(resposta, "gravada")

    def test_only_superusers_get_in(self):
        self.client.force_login(self.staff)
        resposta = self.client.get(self.change_url())

        self.assertIn(resposta.status_code, (302, 403))

    def test_saving_without_typing_the_password_keeps_it(self):
        self.client.force_login(self.super)
        antes = EmailSettings.objects.get(pk=1).password_encrypted

        self.client.post(self.change_url(), self.payload(port="2525"), follow=True)

        obj = EmailSettings.objects.get(pk=1)
        self.assertEqual(obj.port, 2525)
        self.assertEqual(obj.password_encrypted, antes)
        self.assertEqual(obj.password, SENHA)

    def test_typing_a_new_password_replaces_it(self):
        self.client.force_login(self.super)

        self.client.post(
            self.change_url(), self.payload(password="senha-nova-do-smtp"), follow=True
        )

        self.assertEqual(EmailSettings.objects.get(pk=1).password, "senha-nova-do-smtp")

    def test_clearing_the_password_empties_it(self):
        self.client.force_login(self.super)

        self.client.post(self.change_url(), self.payload(clear_password="on"), follow=True)

        obj = EmailSettings.objects.get(pk=1)
        self.assertFalse(obj.has_password)

    def test_typing_and_clearing_at_once_is_refused(self):
        self.client.force_login(self.super)

        resposta = self.client.post(
            self.change_url(), self.payload(password="x", clear_password="on")
        )

        self.assertContains(resposta, "Escolha uma coisa só")
        self.assertEqual(EmailSettings.objects.get(pk=1).password, SENHA)

    def test_the_test_page_opens(self):
        self.client.force_login(self.super)
        resposta = self.client.get(reverse("admin:core_emailsettings_send_test"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Enviar teste")
        self.assertNotContains(resposta, SENHA)

    def test_sending_a_test_records_the_result(self):
        self.client.force_login(self.super)

        self.client.post(
            reverse("admin:core_emailsettings_send_test"),
            {"recipient": "eu@exemplo.test"},
            follow=True,
        )

        obj = EmailSettings.objects.get(pk=1)
        self.assertIsNotNone(obj.last_test_at)
        self.assertNotIn(SENHA, obj.last_test_message)

    def test_a_failed_test_is_recorded_without_the_password(self):
        self.client.force_login(self.super)

        with mock.patch.object(
            mailer, "build_connection", side_effect=Exception(f"auth failed {SENHA}")
        ):
            resposta = self.client.post(
                reverse("admin:core_emailsettings_send_test"),
                {"recipient": "eu@exemplo.test"},
                follow=True,
            )

        obj = EmailSettings.objects.get(pk=1)
        self.assertFalse(obj.last_test_ok)
        self.assertNotIn(SENHA, obj.last_test_message)
        self.assertNotContains(resposta, SENHA)

    def test_the_config_cannot_be_deleted(self):
        self.client.force_login(self.super)
        resposta = self.client.post(
            reverse("admin:core_emailsettings_delete", args=[1]), {"post": "yes"}
        )

        self.assertIn(resposta.status_code, (302, 403))
        self.assertTrue(EmailSettings.objects.filter(pk=1).exists())

    def payload(self, **overrides):
        obj = EmailSettings.objects.get(pk=1)
        dados = {
            "is_active": "on" if obj.is_active else "",
            "host": obj.host,
            "port": str(obj.port),
            "username": obj.username,
            "use_tls": "on" if obj.use_tls else "",
            "use_ssl": "on" if obj.use_ssl else "",
            "timeout": str(obj.timeout),
            "from_email": obj.from_email,
            "from_name": obj.from_name,
            "reply_to": obj.reply_to,
            "admin_recipients": obj.admin_recipients,
            "password": "",
            "clear_password": "",
        }
        dados.update(overrides)
        return dados


class TestResultColumnTests(TestCase):
    """A coluna do último teste já derrubou esta tela.

    `format_html` escapa cada argumento **antes** de chamar `str.format`, então
    o `str.format` recebe uma `SafeString` — e `{:%d/%m/%Y}` não é um
    especificador válido para ela. O resultado era um 500 na listagem e na
    tela de configuração assim que existisse um teste registrado. O navegador
    pegou; a suíte não pegava, porque nenhum teste tinha `last_test_at`.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="chefe", email="chefe@jd.test", password="senha-de-teste"
        )
        self.client.force_login(self.user)

    def registrar_teste(self, ok=True):
        from django.utils import timezone

        obj = configurar()
        obj.last_test_at = timezone.now()
        obj.last_test_ok = ok
        obj.last_test_message = "E-mail enviado para eu@exemplo.test"
        obj.save()
        return obj

    def test_the_changelist_opens_after_a_successful_test(self):
        self.registrar_teste(ok=True)

        resposta = self.client.get(reverse("admin:core_emailsettings_changelist"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "E-mail enviado")

    def test_the_changelist_opens_after_a_failed_test(self):
        self.registrar_teste(ok=False)

        self.assertEqual(
            self.client.get(reverse("admin:core_emailsettings_changelist")).status_code,
            200,
        )

    def test_the_change_page_opens_after_a_test(self):
        self.registrar_teste()

        resposta = self.client.get(reverse("admin:core_emailsettings_change", args=[1]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "E-mail enviado")

    def test_the_date_is_shown(self):
        obj = self.registrar_teste()
        from django.utils import timezone

        esperado = timezone.localtime(obj.last_test_at).strftime("%d/%m/%Y")
        resposta = self.client.get(reverse("admin:core_emailsettings_change", args=[1]))

        self.assertContains(resposta, esperado)

    def test_without_a_test_it_says_so(self):
        configurar()

        self.assertContains(
            self.client.get(reverse("admin:core_emailsettings_change", args=[1])),
            "nunca testado",
        )
