"""Regras do nome de usuário.

O username aparece em lugares onde texto livre dá problema (URL de perfil,
menção, futura API, arquivo de exportação). Por isso o conjunto de caracteres é
deliberadamente pequeno:

    A-Z  a-z  0-9  _  -

Nada de espaço, acento, ponto, barra ou arroba. Acento e ponto pareceriam
inofensivos, mas trariam de volta os dois problemas que este conjunto evita:
duas grafias visualmente iguais (``joão`` / ``joao``) e ambiguidade com
endereço de e-mail.
"""

import re

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 30

#: Letras, números, ``_`` e ``-``; nunca começando ou terminando com separador.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?$")

#: Parece um e-mail? Basta um arroba com algo dos dois lados.
LOOKS_LIKE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")

#: Nomes que o cadastro **público** não aceita: colidem com rotas da loja, com
#: o admin ou com identidades institucionais. Comparados sempre em minúsculas.
#: O administrador continua podendo criá-los (é ele quem tem esse direito).
RESERVED_USERNAMES = frozenset(
    {
        # infraestrutura
        "admin",
        "administrador",
        "administrator",
        "root",
        "sistema",
        "system",
        "static",
        "media",
        "api",
        "i18n",
        "www",
        "ftp",
        "mail",
        "email",
        "e-mail",
        "webmaster",
        "postmaster",
        "noreply",
        "no-reply",
        "nao-responda",
        "null",
        "none",
        "undefined",
        "true",
        "false",
        "robots",
        "sitemap",
        "favicon",
        # rotas da loja
        "conta",
        "contas",
        "account",
        "accounts",
        "login",
        "logout",
        "entrar",
        "sair",
        "cadastro",
        "register",
        "senha",
        "password",
        "carrinho",
        "cart",
        "checkout",
        "modelos",
        "produto",
        "produtos",
        "categoria",
        "categorias",
        "pedido",
        "pedidos",
        "orders",
        "busca",
        "search",
        # marca
        "jdprint",
        "jd-print",
        "jd_print",
        "suporte",
        "support",
        "ajuda",
        "help",
        "contato",
        "contact",
    }
)


@deconstructible
class UsernameValidator:
    """Valida o nome de usuário. ``@deconstructible`` para caber na migration."""

    def __call__(self, value):
        value = (value or "").strip()

        if LOOKS_LIKE_EMAIL.match(value):
            # Requisito explícito: username e e-mail não podem se confundir.
            # Como o login aceita os dois no mesmo campo, um username com
            # cara de e-mail criaria ambiguidade real, não só estética.
            raise ValidationError(
                _("O nome de usuário não pode ser um endereço de e-mail."),
                code="username_is_email",
            )

        if len(value) < USERNAME_MIN_LENGTH:
            raise ValidationError(
                _("O nome de usuário precisa de pelo menos %(min)s caracteres.")
                % {"min": USERNAME_MIN_LENGTH},
                code="username_too_short",
            )

        if len(value) > USERNAME_MAX_LENGTH:
            raise ValidationError(
                _("O nome de usuário pode ter no máximo %(max)s caracteres.")
                % {"max": USERNAME_MAX_LENGTH},
                code="username_too_long",
            )

        if not USERNAME_PATTERN.match(value):
            raise ValidationError(
                _(
                    "Use apenas letras sem acento, números, hífen e sublinhado, "
                    "começando e terminando com letra ou número."
                ),
                code="username_invalid",
            )

    def __eq__(self, other):
        return isinstance(other, UsernameValidator)

    def __hash__(self):
        return hash(self.__class__)


validate_username = UsernameValidator()


def validate_public_username(value):
    """Regras do formato **mais** a lista de nomes reservados.

    A lista de reservados é política de cadastro público, não integridade de
    dados — por isso não está no validador do modelo. Do contrário o próprio
    dono da loja não conseguiria criar o superusuário ``admin`` pelo terminal,
    que é justamente quem tem o direito de usar esse nome.
    """
    validate_username(value)
    if (value or "").strip().lower() in RESERVED_USERNAMES:
        raise ValidationError(
            _("Este nome de usuário não está disponível."),
            code="username_reserved",
        )


def validate_phone(value):
    """Telefone: permissivo de propósito (formatos belgas, franceses, BR)."""
    if not value:
        return
    if not re.match(r"^[0-9+()\s./-]{6,25}$", value.strip()):
        raise ValidationError(
            _("Telefone inválido. Use números, espaços, +, ( ) ou hífen."),
            code="phone_invalid",
        )
