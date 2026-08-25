"""
Configurações do projeto JD PRINT.

Toda a configuração sensível/variável vem do arquivo `.env` (ver `.env.example`).
O banco oficial do projeto é o PostgreSQL.
"""

from pathlib import Path

from dotenv import load_dotenv

import os
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-troque-esta-chave-em-producao")

DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.categories",
    "apps.catalog",
    "apps.cart",
    "apps.shipping",
    "apps.orders",
    "apps.home",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    # Depois do LocaleMiddleware: manda o visitante para a URL do idioma que
    # ele escolheu quando a URL não traz prefixo (ver o docstring).
    "apps.core.middleware.PreferredLanguageRedirectMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
                "apps.cart.context_processors.cart",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Banco de dados
#
# O banco oficial do projeto e o PostgreSQL. O backend sqlite so deve ser usado
# como saida de emergencia em maquinas de desenvolvimento onde o PostgreSQL
# ainda nao foi instalado (DJANGO_DB_ENGINE=sqlite no .env).
# ---------------------------------------------------------------------------

if env("DJANGO_DB_ENGINE", "postgres") == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "jdprint"),
            "USER": env("POSTGRES_USER", "jdprint"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Autenticação
#
# `User` e `Customer` sao coisas diferentes: o primeiro e a conta de acesso
# (apps/accounts/models.py), o segundo e o cliente comercial. Ver docs.
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

# Backend proprio para o login aceitar username OU e-mail no mesmo campo. Ele
# herda de ModelBackend: hashing, permissoes e bloqueio de conta inativa
# continuam sendo os do Django.
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.UsernameOrEmailBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Hashing de senha em teste: o PBKDF2 e caro de proposito (e assim deve ficar
# em producao), mas com centenas de testes que criam contas ele domina o tempo
# da suite. Nos testes usamos um hasher rapido; o teste que verifica o hashing
# real reativa o PBKDF2 com override_settings.
if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "home:index"

# ---------------------------------------------------------------------------
# Sessao e cookies
#
# Em HTTPS os cookies precisam ser `Secure`; em desenvolvimento (HTTP puro) um
# cookie `Secure` simplesmente nao e gravado e nada funciona. Por isso o padrao
# acompanha o DEBUG, e o .env manda em producao.
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True  # JavaScript nao le o cookie de sessao
SESSION_COOKIE_SAMESITE = "Lax"  # o cookie nao viaja em POST de outro site
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE", str(60 * 60 * 24 * 14)))
SESSION_SAVE_EVERY_REQUEST = False

CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # o HTMX precisa ler o token no navegador

LANGUAGE_COOKIE_SAMESITE = "Lax"

if not DEBUG:
    # Hospedagens como o PythonAnywhere terminam o TLS num proxy, ANTES do
    # Django: a requisicao chega no app em HTTP puro e `request.is_secure()`
    # devolveria False mesmo o visitante estando em HTTPS. O resultado sem esta
    # linha e um loop -- o SECURE_SSL_REDIRECT abaixo manda para HTTPS uma
    # requisicao que ja veio de HTTPS, e o navegador fica repetindo.
    #
    # ATENCAO: so vale confiar neste cabecalho porque o proxy da hospedagem o
    # reescreve sempre, ignorando o que o cliente mandou. Numa hospedagem SEM
    # proxy na frente, qualquer visitante poderia forjar `X-Forwarded-Proto` e
    # se declarar seguro: nesse caso, ponha SECURE_PROXY_SSL_HEADER=False.
    if env_bool("SECURE_PROXY_SSL_HEADER", True):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 30)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
    # No PythonAnywhere da para deixar False e ligar o "Force HTTPS" na aba Web
    # (o proxy redireciona antes de a requisicao chegar no Python). Manter True
    # tambem funciona, desde que o SECURE_PROXY_SSL_HEADER acima esteja ativo.
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"

# ---------------------------------------------------------------------------
# Internacionalização
#
# Duas coisas diferentes convivem aqui:
#   * LANGUAGE_CODE / LANGUAGES -> idioma da INTERFACE (admin, mensagens).
#   * CONTENT_LANGUAGES         -> idiomas do CONTEUDO cadastrado (traducoes
#                                  de produto, categoria, etc).
# Ver apps/core/constants.py.
# ---------------------------------------------------------------------------

LANGUAGE_CODE = env("DJANGO_LANGUAGE_CODE", "pt-br")

LANGUAGES = [
    ("pt-br", "Português"),
    ("fr", "Français"),
    ("nl", "Nederlands"),
    ("en", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("tr", "Türkçe"),
    ("ar", "العربية"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = env("DJANGO_TIME_ZONE", "Europe/Brussels")

USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Arquivos estáticos e de mídia
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Loja
# ---------------------------------------------------------------------------

# Categoria raiz que alimenta a página /modelos/. Quando existirem outras
# vitrines (filamentos, impressoras), cada uma aponta para a sua raiz.
SHOP_MODELS_CATEGORY_SLUG = env("SHOP_MODELS_CATEGORY_SLUG", "modelos")

# Produtos por página no Shop. As opções são múltiplos de 4 para fechar tanto
# a grade de 4 colunas do desktop quanto a de 2 colunas do celular.
SHOP_PAGE_SIZE_OPTIONS = tuple(
    int(value) for value in env_list("SHOP_PAGE_SIZE_OPTIONS", "4,8,12,16,20,24")
)
SHOP_PAGE_SIZE = int(env("SHOP_PAGE_SIZE", "12"))
if SHOP_PAGE_SIZE not in SHOP_PAGE_SIZE_OPTIONS:
    SHOP_PAGE_SIZE = SHOP_PAGE_SIZE_OPTIONS[0]

# Personalização enviada pelo cliente (foto).
CUSTOMIZATION_MAX_UPLOAD_SIZE = int(env("CUSTOMIZATION_MAX_UPLOAD_SIZE", str(10 * 1024 * 1024)))
CUSTOMIZATION_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
CUSTOMIZATION_NOTES_MAX_LENGTH = int(env("CUSTOMIZATION_NOTES_MAX_LENGTH", "500"))

# Teto de unidades por linha do carrinho (proteção contra valores absurdos).
CART_MAX_QUANTITY_PER_LINE = int(env("CART_MAX_QUANTITY_PER_LINE", "99"))

# Tamanho máximo aceito para upload de mídia de produto (bytes).
PRODUCT_MEDIA_MAX_UPLOAD_SIZE = int(env("PRODUCT_MEDIA_MAX_UPLOAD_SIZE", str(50 * 1024 * 1024)))

# ---------------------------------------------------------------------------
# E-mail e confirmacao de conta
# ---------------------------------------------------------------------------

MAILERS = {
    "default": {
        "BACKEND": env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"),
    },
}

DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "JD PRINT <nao-responda@jd-print.com>")
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# Endereco oficial da loja. Os links enviados por e-mail SAO MONTADOS A PARTIR
# DAQUI, nunca a partir do cabecalho Host da requisicao: senao bastaria mandar
# um Host falso para a loja enviar, do proprio dominio, um link para o site de
# quem atacou.
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

# Validade do link de confirmacao de e-mail (segundos). Um so lugar: nada de
# 86400 espalhado pelo codigo.
EMAIL_VERIFICATION_TIMEOUT = int(env("EMAIL_VERIFICATION_TIMEOUT", str(24 * 60 * 60)))

# Intervalo minimo entre dois pedidos de reenvio, por usuario e por IP.
EMAIL_VERIFICATION_RESEND_INTERVAL = int(env("EMAIL_VERIFICATION_RESEND_INTERVAL", "120"))
EMAIL_VERIFICATION_IP_INTERVAL = int(env("EMAIL_VERIFICATION_IP_INTERVAL", "60"))
# Quantos e-mails de conta um mesmo IP pode disparar dentro dessa janela. Cota
# (e nao "um so"), porque um escritorio inteiro divide o mesmo IP.
ACCOUNT_EMAIL_IP_LIMIT = int(env("ACCOUNT_EMAIL_IP_LIMIT", "5"))

# Validade do link de redefinicao de senha (segundos). O padrao do Django e de
# tres dias; 24 horas e mais adequado para uma loja.
PASSWORD_RESET_TIMEOUT = int(env("PASSWORD_RESET_TIMEOUT", str(24 * 60 * 60)))

# ---------------------------------------------------------------------------
# Pedidos, frete e impostos
# ---------------------------------------------------------------------------

# Prefixo do numero publico do pedido: JD-2026-000001. O numero NAO e o PK.
ORDER_NUMBER_PREFIX = env("ORDER_NUMBER_PREFIX", "JD")

# Pais da sede. E o padrao quando o pedido ainda nao tem endereco de entrega
# (calculo preliminar) e o fallback da aliquota de TVA.
STORE_COUNTRY = env("STORE_COUNTRY", "BE").upper()

# Moeda dos precos praticados hoje. O pedido guarda a sua propria copia: o
# valor historico nunca pode depender desta configuracao.
STORE_CURRENCY = env("STORE_CURRENCY", "EUR").upper()

# Para onde vai o e-mail administrativo de cada pedido novo. Lista, porque
# producao e atendimento podem ser pessoas diferentes.
ORDER_ADMIN_EMAILS = env_list("ORDER_ADMIN_EMAILS", DEFAULT_FROM_EMAIL)

# ---------------------------------------------------------------------------
# Pagamento (Stripe)
# ---------------------------------------------------------------------------
#
# As chaves NUNCA ficam no codigo. Elas vem do .env:
#
#   STRIPE_SECRET_KEY       sk_test_... / sk_live_...   (servidor, secreta)
#   STRIPE_PUBLISHABLE_KEY  pk_test_... / pk_live_...   (publica)
#   STRIPE_WEBHOOK_SECRET   whsec_...                   (assinatura do webhook)
#
# Sem STRIPE_SECRET_KEY a loja continua funcionando inteira; so o passo de
# pagamento avisa que o meio de pagamento esta indisponivel. E o que evita
# tanto uma chave de teste esquecida em producao quanto um checkout que finge
# funcionar sem gateway.

PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", "stripe")

STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", "")

# Quanto tempo a sessao de pagamento da Stripe fica valida (segundos).
# O minimo aceito pela Stripe e 30 minutos.
STRIPE_SESSION_EXPIRES_IN = int(env("STRIPE_SESSION_EXPIRES_IN", str(30 * 60)))

