"""E-mails transacionais da conta (confirmação e recuperação de senha).

Três cuidados que valem o arquivo separado:

**1. O domínio do link não vem da requisição.** ``request.get_host()`` é
controlado por quem manda o cabeçalho ``Host``; um atacante que dispare o
formulário de recuperação com ``Host: site-falso`` faria a JD PRINT enviar, do
próprio domínio, um e-mail com link para o site dele. Aqui o endereço sai
sempre de ``settings.SITE_URL``.

**2. O idioma é o do cliente.** Quem se cadastrou em ``/fr/`` recebe o e-mail em
francês, mesmo que a mensagem seja disparada por um administrador em português.
``translation.override`` cobre tanto os textos quanto o ``reverse()`` — a URL do
link já sai com o prefixo do idioma certo.

**3. Nada sensível no corpo.** Nem senha, nem dados pessoais além do necessário:
o link carrega apenas ``uid`` + token assinado.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.accounts.tokens import email_verification_token, encode_uid
from apps.core.languages import is_language_available

logger = logging.getLogger(__name__)


def site_url() -> str:
    return str(getattr(settings, "SITE_URL", "http://127.0.0.1:8000")).rstrip("/")


def absolute_url(path: str) -> str:
    """URL completa a partir de um caminho, sempre no domínio oficial."""
    return f"{site_url()}{path}"


def email_language(user) -> str:
    """Idioma do e-mail: o do cliente, se ainda estiver disponível na loja."""
    language = (user.preferred_language or "").strip()
    if language and is_language_available(language):
        return language
    return settings.LANGUAGE_CODE


def _send(user, subject: str, template: str, context: dict) -> bool:
    """Monta e envia o e-mail em texto + HTML. Nunca derruba a requisição."""
    context = {
        "user": user,
        "site_name": "JD PRINT",
        "site_url": site_url(),
        **context,
    }
    text_body = render_to_string(f"emails/{template}.txt", context)
    html_body = render_to_string(f"emails/{template}.html", context)

    message = EmailMultiAlternatives(
        subject="".join(subject.splitlines()),  # cabeçalho não aceita quebra
        body=text_body,
        to=[user.email],
    )
    message.attach_alternative(html_body, "text/html")

    try:
        message.send()
    except Exception:  # provedor fora do ar não pode quebrar o cadastro
        logger.exception("Falha ao enviar e-mail (%s) para o usuário %s", template, user.pk)
        return False
    return True


def send_verification_email(user) -> bool:
    """Envia (ou reenvia) a confirmação de e-mail e marca a data do envio."""
    if user.email_verified:
        return False

    with translation.override(email_language(user)):
        path = reverse(
            "accounts:verify_email",
            kwargs={"uidb64": encode_uid(user), "token": email_verification_token.make_token(user)},
        )
        hours = int(getattr(settings, "EMAIL_VERIFICATION_TIMEOUT", 86400) // 3600)
        sent = _send(
            user,
            _("Confirme seu endereço de e-mail — JD PRINT"),
            "verify_email",
            {"verification_url": absolute_url(path), "validity_hours": hours},
        )

    if sent:
        user.verification_sent_at = timezone.now()
        user.save(update_fields=["verification_sent_at"])
    return sent


def send_password_reset_email(user, token_generator) -> bool:
    """Recuperação de senha, com o gerador de token padrão do Django."""
    with translation.override(email_language(user)):
        path = reverse(
            "accounts:password_reset_confirm",
            kwargs={"uidb64": encode_uid(user), "token": token_generator.make_token(user)},
        )
        hours = int(getattr(settings, "PASSWORD_RESET_TIMEOUT", 86400) // 3600)
        return _send(
            user,
            _("Redefinição de senha — JD PRINT"),
            "password_reset",
            {"reset_url": absolute_url(path), "validity_hours": hours},
        )
