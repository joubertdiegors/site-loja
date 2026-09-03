"""Regressões da auditoria de segurança — etapa 21.

Cada classe aqui corresponde a uma falha que a auditoria encontrou, que foi
reproduzida antes de ser corrigida, e que não pode voltar sem alguém perceber.

Não são testes de "a proteção existe": são testes do **ataque**. Se a proteção
sair, o ataque volta a funcionar e o teste falha.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.i18n import translate_path
from apps.core.testing import LanguageResetMixin, make_category, make_product

SENHA = "senha-bem-comprida-99"


# ---------------------------------------------------------------------------
# 1. Open redirect (CRÍTICO — corrigido em apps/core/i18n.py)
# ---------------------------------------------------------------------------


class OpenRedirectTests(LanguageResetMixin, TestCase):
    """`GET /de//evil.com` devolvia `302 Location: ////evil.com`.

    O navegador resolve uma referência que começa com duas ou mais barras como
    protocol-relative: `////evil.com` vira `https://evil.com/`. Era um open
    redirect anônimo, de um GET só, usando o domínio da loja para phishing.

    O `de` funciona como isca porque é um idioma que o Django conhece mas que a
    loja não oferece — é justamente o ramo do middleware que devolve o
    visitante para o idioma padrão.
    """

    ISCAS = [
        "/de//evil.com",
        "/de//evil.com/x?a=1",
        "/es//evil.com",
        "/it//evil.com",
        "/tr//evil.com",
        "/ar//evil.com",
        "/de/%2F%2Fevil.com",
        "/de///evil.com",
    ]

    def test_no_redirect_ever_leaves_the_site(self):
        for caminho in self.ISCAS:
            with self.subTest(caminho=caminho):
                resposta = self.client.get(caminho)
                destino = resposta.headers.get("Location", "")
                self.assertFalse(
                    destino.startswith("//"),
                    f"{caminho} redirecionou para fora: {destino!r}",
                )

    def test_translate_path_only_returns_paths_of_this_site(self):
        """A correção mora na raiz: fecha o middleware e o `set_language` juntos."""
        for entrada in ("/de//evil.com", "//evil.com", "///evil.com", "/fr//evil.com"):
            with self.subTest(entrada=entrada):
                saida = translate_path(entrada, "pt-br")
                self.assertTrue(saida.startswith("/"))
                self.assertFalse(saida.startswith("//"), f"{entrada} -> {saida!r}")

    def test_set_language_cannot_send_the_visitor_away(self):
        resposta = self.client.post(
            reverse("set_language"), {"language": "fr", "next": "//evil.com"}
        )

        self.assertFalse(resposta.headers.get("Location", "").startswith("//"))

    def test_normal_redirects_still_work(self):
        """A correção não pode quebrar a troca de idioma de verdade."""
        self.assertEqual(translate_path("/fr/modelos/", "pt-br"), "/modelos/")
        self.assertEqual(translate_path("/", "fr"), "/fr/")
        self.assertEqual(translate_path("/modelos/", "fr"), "/fr/modelos/")


# ---------------------------------------------------------------------------
# 2. Escalação de privilégio no Admin (corrigido em apps/accounts/admin.py)
# ---------------------------------------------------------------------------


class AdminPrivilegeEscalationTests(LanguageResetMixin, TestCase):
    """Staff com `change_user` marcava a própria caixa "superusuário".

    O `fieldsets` do `UserAdmin` mostra `is_superuser` para qualquer pessoa com
    permissão de alterar usuário, e o Django não restringe isso sozinho. Quem
    corrigia o e-mail de um cliente passava a mandar em preço, estoque, pedidos
    e na configuração de e-mail da loja.
    """

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="funcionario", email="func@jdprint.test", password=SENHA, is_staff=True
        )
        tipo = ContentType.objects.get_for_model(User)
        self.staff.user_permissions.add(
            *Permission.objects.filter(content_type=tipo, codename__in=["view_user", "change_user"])
        )
        self.chefe = User.objects.create_superuser(
            username="chefe", email="chefe@jdprint.test", password=SENHA
        )

    def campos_travados(self, usuario):
        from apps.accounts.admin import UserAdmin

        pedido = type("R", (), {"user": usuario})()
        return set(UserAdmin(get_user_model(), None).get_readonly_fields(pedido, self.staff))

    def test_staff_cannot_edit_the_permission_fields(self):
        travados = self.campos_travados(self.staff)

        for campo in ("is_superuser", "is_staff", "groups", "user_permissions"):
            with self.subTest(campo=campo):
                self.assertIn(campo, travados)

    def test_a_superuser_still_can(self):
        """A trava é para quem está abaixo, não para quem administra."""
        travados = self.campos_travados(self.chefe)

        for campo in ("is_superuser", "is_staff", "groups", "user_permissions"):
            with self.subTest(campo=campo):
                self.assertNotIn(campo, travados)

    def test_posting_is_superuser_does_not_promote(self):
        """O ataque de verdade: um POST direto, sem passar pela tela."""
        self.client.force_login(self.staff)

        self.client.post(
            f"/admin/accounts/user/{self.staff.pk}/change/",
            {
                "username": self.staff.username,
                "email": self.staff.email,
                "is_active": "on",
                "is_staff": "on",
                "is_superuser": "on",
                "preferred_language": "pt-br",
                "customer-TOTAL_FORMS": "0",
                "customer-INITIAL_FORMS": "0",
                "_continue": "Salvar",
            },
        )

        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_superuser, "staff se promoveu a superusuário")


class AdminActionPermissionTests(TestCase):
    """Ação em lote sem `permissions=` roda para quem só tem permissão de VER.

    É o padrão do Django, e é uma armadilha: uma conta criada para consultar
    pedidos ativava e desativava produtos, marcava e-mails como confirmados e
    retirava idiomas da loja.
    """

    def test_every_registered_action_requires_a_permission(self):
        from django.contrib import admin as dj_admin

        sem_trava = []
        for model, site_admin in dj_admin.site._registry.items():
            for nome in site_admin.actions or []:
                funcao = getattr(site_admin, nome, None) if isinstance(nome, str) else nome
                if funcao is None:
                    continue
                if not getattr(funcao, "allowed_permissions", None):
                    sem_trava.append(f"{model._meta.label}.{getattr(funcao, '__name__', nome)}")

        self.assertEqual(sem_trava, [], f"ações sem permissions=: {sem_trava}")


# ---------------------------------------------------------------------------
# 3. Força bruta e torneira de e-mail (corrigido em apps/accounts/views.py)
# ---------------------------------------------------------------------------


@override_settings(LOGIN_FAILURE_LIMIT=5, LOGIN_FAILURE_WINDOW=900)
class LoginBruteForceTests(LanguageResetMixin, TestCase):
    """A tela aceitava senha errada indefinidamente — medi 25 seguidas."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="ana", email="ana@jdprint.test", password=SENHA
        )
        self.url = reverse("accounts:login")

    def errar(self, vezes, **extra):
        for i in range(vezes):
            resposta = self.client.post(
                self.url, {"username": "ana@jdprint.test", "password": f"errada{i}"}, **extra
            )
        return resposta

    def test_a_series_of_wrong_passwords_is_stopped(self):
        resposta = self.errar(6)

        self.assertContains(resposta, "Muitas tentativas")

    def test_the_right_password_stops_working_once_blocked(self):
        """Senão o bloqueio seria decorativo."""
        self.errar(6)

        resposta = self.client.post(self.url, {"username": "ana@jdprint.test", "password": SENHA})

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_whoever_types_the_right_password_is_never_blocked(self):
        """Acertar não pode aproximar ninguém do limite."""
        for _ in range(10):
            self.client.post(self.url, {"username": "ana@jdprint.test", "password": SENHA})
            self.client.post(reverse("accounts:logout"))

        resposta = self.client.post(self.url, {"username": "ana@jdprint.test", "password": SENHA})

        self.assertEqual(resposta.status_code, 302)

    def test_a_forged_forwarded_header_does_not_reset_the_counter(self):
        """Sem proxy declarado, `X-Forwarded-For` é só um texto do visitante."""
        for i in range(6):
            resposta = self.client.post(
                self.url,
                {"username": "ana@jdprint.test", "password": f"x{i}"},
                HTTP_X_FORWARDED_FOR=f"10.0.0.{i}",
            )

        self.assertContains(resposta, "Muitas tentativas")

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_behind_a_proxy_the_last_entry_is_what_counts(self):
        """O proxy acrescenta o IP real no fim; o que vem antes é do cliente."""
        for i in range(6):
            resposta = self.client.post(
                self.url,
                {"username": "ana@jdprint.test", "password": f"x{i}"},
                HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.9",
            )

        self.assertContains(resposta, "Muitas tentativas")

    def test_the_message_does_not_tell_the_attacker_he_hit_the_limit(self):
        """Bloqueio e senha errada devolvem 200 com o formulário — sem 429."""
        resposta = self.errar(6)

        self.assertEqual(resposta.status_code, 200)


@override_settings(ACCOUNT_EMAIL_IP_LIMIT=3, EMAIL_VERIFICATION_IP_INTERVAL=60)
class RegisterFloodTests(LanguageResetMixin, TestCase):
    """Cadastro em laço dispara e-mail da loja para endereços escolhidos pelo atacante.

    O estrago não é o servidor: é a reputação do domínio. Depois de um envio em
    massa, o e-mail de pedido de um cliente de verdade cai na caixa de lixo.
    """

    def cadastrar(self, indice):
        return self.client.post(
            reverse("accounts:register"),
            {
                "username": f"conta{indice}",
                "email": f"alvo{indice}@exemplo.test",
                "password1": SENHA,
                "password2": SENHA,
            },
        )

    def test_a_series_of_signups_is_stopped(self):
        from django.core import mail

        mail.outbox = []
        for i in range(8):
            self.cadastrar(i)
            self.client.post(reverse("accounts:logout"))

        self.assertLessEqual(
            len(mail.outbox), 4, f"{len(mail.outbox)} e-mails saíram de um IP só"
        )

    def test_the_first_signups_still_work(self):
        """A trava é uma cota, não uma porta fechada: um escritório divide o IP."""
        resposta = self.cadastrar(0)

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(get_user_model().objects.filter(username="conta0").exists())


class ClientIpTests(TestCase):
    """De onde sai o IP que as travas usam."""

    def pedido(self, **meta):
        from django.test import RequestFactory

        return RequestFactory().get("/", **meta)

    def test_without_a_declared_proxy_the_header_is_ignored(self):
        from apps.core.security import client_ip

        pedido = self.pedido(HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="127.0.0.1")

        self.assertEqual(client_ip(pedido), "127.0.0.1")

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_behind_a_proxy_the_last_entry_wins(self):
        from apps.core.security import client_ip

        pedido = self.pedido(
            HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9", REMOTE_ADDR="10.0.0.1"
        )

        self.assertEqual(client_ip(pedido), "203.0.113.9")


# ---------------------------------------------------------------------------
# 4. 500 anônimo no checkout (corrigido em apps/orders/views.py)
# ---------------------------------------------------------------------------


class CheckoutGarbageInputTests(LanguageResetMixin, TestCase):
    """`?shipping_method=abc` derrubava a página de finalizar compra.

    `filter(pk="abc")` levanta `ValueError` no ORM antes de qualquer consulta —
    com `DEBUG=False` isso é HTTP 500. Anônimo, um GET, na URL mais importante
    da loja: qualquer varredura de bot produzia erro no dia do lançamento.
    """

    LIXO = ["abc", "1.5", "9e9", "1 OR 1=1", "-1", "../../etc/passwd", "", "  ", "99999999"]

    def setUp(self):
        super().setUp()
        make_category(slug="modelos", name="Modelos")
        make_product(sku="SEG-01", name="Vaso", price="19.90", stock_quantity=5)

    def test_no_garbage_shipping_method_breaks_the_page(self):
        for valor in self.LIXO:
            with self.subTest(valor=valor):
                resposta = self.client.get("/carrinho/finalizar/", {"shipping_method": valor})
                self.assertEqual(resposta.status_code, 200)

    def test_the_htmx_partial_survives_it_too(self):
        """É o mesmo caminho que a tela usa ao trocar de endereço."""
        for valor in self.LIXO:
            with self.subTest(valor=valor):
                resposta = self.client.get(
                    "/carrinho/finalizar/",
                    {"partial": "delivery", "shipping_method": valor},
                    HTTP_HX_REQUEST="true",
                )
                self.assertEqual(resposta.status_code, 200)

    def test_a_garbage_address_does_not_break_it_either(self):
        for valor in self.LIXO:
            with self.subTest(valor=valor):
                resposta = self.client.get("/carrinho/finalizar/", {"shipping_address": valor})
                self.assertEqual(resposta.status_code, 200)


# ---------------------------------------------------------------------------
# 5. Dois laços de abuso (corrigidos em apps/orders/views.py e apps/cart/views.py)
# ---------------------------------------------------------------------------


@override_settings(ACCOUNT_EMAIL_IP_LIMIT=3, EMAIL_VERIFICATION_IP_INTERVAL=60)
class AbuseLoopTests(LanguageResetMixin, TestCase):
    """As travas existem e contam por IP, com a mesma cota do resto do projeto."""

    def test_the_retry_bucket_is_capped(self):
        """20 POST em "pagar" davam 20 e-mails para a loja — a cota da hospedagem."""
        from django.test import RequestFactory

        from apps.core.security import ip_is_throttled

        pedido = RequestFactory().post("/", REMOTE_ADDR="203.0.113.7")
        resultados = [ip_is_throttled(pedido, "order-retry") for _ in range(6)]

        self.assertFalse(resultados[0], "a primeira tentativa nunca pode ser barrada")
        self.assertTrue(resultados[-1], "o laço não foi barrado")

    def test_the_upload_bucket_is_capped(self):
        """Upload é o único ponto em que quem não tem conta grava no disco."""
        from django.test import RequestFactory

        from apps.core.security import ip_is_throttled

        pedido = RequestFactory().post("/", REMOTE_ADDR="203.0.113.8")
        resultados = [ip_is_throttled(pedido, "upload") for _ in range(6)]

        self.assertFalse(resultados[0])
        self.assertTrue(resultados[-1])

    def test_the_buckets_do_not_share_a_counter(self):
        """Quem envia foto não pode ficar sem conseguir tentar pagar."""
        from django.test import RequestFactory

        from apps.core.security import ip_is_throttled

        pedido = RequestFactory().post("/", REMOTE_ADDR="203.0.113.9")
        for _ in range(6):
            ip_is_throttled(pedido, "upload")

        self.assertFalse(ip_is_throttled(pedido, "order-retry"))


class CookieSecureTests(TestCase):
    """A marca `Secure` do cookie de sessão e do CSRF, em produção.

    Antes, as duas eram ``env_bool(..., not DEBUG)``: o padrão acertava, mas o
    `.env` mandava — e o `.env.example`, que é o arquivo que quem faz o deploy
    copia, trazia as duas como ``False``. Uma produção configurada a partir dele
    nascia servindo o cookie de sessão de cliente logado e o token CSRF sem a
    marca, e o padrão seguro nunca era consultado, porque a variável existia.

    Estes testes leem o `config/settings.py` como fonte, e não as `settings`
    carregadas: o valor em memória depende do `.env` da máquina que roda a
    suíte, e o que precisa ficar travado é a **regra**.
    """

    @staticmethod
    def fonte() -> str:
        import io

        return io.open("config/settings.py", encoding="utf-8").read()

    def test_production_does_not_negotiate_the_secure_flag(self):
        self.assertIn(
            'COOKIE_SECURE = True if not DEBUG else env_bool("COOKIE_SECURE_IN_DEBUG", False)',
            self.fonte(),
        )

    def test_neither_cookie_reads_an_env_var_of_its_own(self):
        """Uma variável por cookie é uma chance a mais de esquecer uma linha."""
        fonte = self.fonte()

        self.assertIn("SESSION_COOKIE_SECURE = COOKIE_SECURE", fonte)
        self.assertIn("CSRF_COOKIE_SECURE = COOKIE_SECURE", fonte)
        self.assertNotIn('env_bool("SESSION_COOKIE_SECURE"', fonte)
        self.assertNotIn('env_bool("CSRF_COOKIE_SECURE"', fonte)

    def test_the_example_env_does_not_document_an_insecure_production(self):
        import io

        exemplo = io.open(".env.example", encoding="utf-8").read()

        self.assertNotIn("SESSION_COOKIE_SECURE=False", exemplo)
        self.assertNotIn("CSRF_COOKIE_SECURE=False", exemplo)

    @staticmethod
    def regra(debug: bool, variavel: str = "False") -> bool:
        """A expressão do `settings.py`, avaliada com um DEBUG à escolha.

        Não dá para ler `settings.SESSION_COOKIE_SECURE` e concluir nada: ele é
        calculado no import, com o `.env` da máquina, e o runner de teste força
        `settings.DEBUG = False` **depois** disso. O que se testa aqui é a
        regra, com os dois valores de DEBUG que importam.
        """
        import os
        from unittest import mock

        from config.settings import env_bool

        with mock.patch.dict(os.environ, {"COOKIE_SECURE_IN_DEBUG": variavel}):
            return True if not debug else env_bool("COOKIE_SECURE_IN_DEBUG", False)

    def test_production_forces_the_flag_on(self):
        self.assertTrue(self.regra(debug=False))

    def test_production_ignores_a_variable_asking_to_turn_it_off(self):
        """O caso que o `.env.example` antigo produzia."""
        self.assertTrue(self.regra(debug=False, variavel="False"))

    def test_development_keeps_the_flag_off_so_login_works_over_http(self):
        """Em HTTP puro o navegador não grava um cookie `Secure`."""
        self.assertFalse(self.regra(debug=True))

    def test_development_can_turn_it_on_for_local_https(self):
        """mkcert, túnel — quem testa HTTPS local precisa da marca."""
        self.assertTrue(self.regra(debug=True, variavel="True"))

    def test_the_other_cookie_protections_are_untouched(self):
        from django.conf import settings

        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")
        # O HTMX precisa ler o token no navegador.
        self.assertFalse(settings.CSRF_COOKIE_HTTPONLY)

    def test_hsts_preload_stays_off(self):
        """Entrar na lista de preload é praticamente irreversível."""
        self.assertIn('SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)', self.fonte())
