"""E-mails do pedido.

Três, e só três — um cliente não precisa de uma sequência de sete mensagens:

1. **confirmação** (cliente) — quando o pagamento é confirmado. Um e-mail só,
   completo: itens, personalização, endereços, prazo, valores, TVA;
2. **pedido novo** (administração) — a mesma compra escrita para quem vai
   produzir e despachar, com SKU, arquivos e observações, para não ser preciso
   abrir o Admin;
3. **enviado** (cliente) — transportadora, código e link de rastreio.

O idioma é o **do pedido** (``Order.language``, gravado na compra), não o da
requisição que dispara o envio: quem comprou em ``/fr/`` recebe em francês
mesmo que o disparo venha do admin em português, e continua recebendo em
francês três meses depois.

O envio nunca derruba a operação: se o provedor de e-mail estiver fora do ar, o
pagamento continua confirmado e a falha vai para o log.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.accounts.emails import absolute_url, site_url
from apps.core.languages import is_language_available

logger = logging.getLogger(__name__)


def order_language(order) -> str:
    """Idioma do e-mail: o do pedido; depois o do cliente; depois o padrão."""
    for candidate in (order.language, order.customer.user.preferred_language):
        candidate = (candidate or "").strip()
        if candidate and is_language_available(candidate):
            return candidate
    return settings.LANGUAGE_CODE


def order_context(order) -> dict:
    """Tudo que os três modelos de e-mail precisam, carregado de uma vez."""
    return {
        "order": order,
        "items": list(order.items.select_related("personalization_upload")),
        "shipping_address": order.shipping_address,
        "billing_address": order.billing_address,
        "customer": order.customer,
        "user": order.customer.user,
        "site_name": "JD PRINT",
        "site_url": site_url(),
        "order_url": absolute_url(order.get_absolute_url()),
    }


def _send(subject: str, template: str, context: dict, recipients: list[str]) -> bool:
    recipients = [address for address in recipients if address]
    if not recipients:
        return False

    text_body = render_to_string(f"emails/{template}.txt", context)
    html_body = render_to_string(f"emails/{template}.html", context)

    message = EmailMultiAlternatives(
        subject="".join(subject.splitlines()),  # cabeçalho não aceita quebra
        body=text_body,
        to=recipients,
    )
    message.attach_alternative(html_body, "text/html")

    try:
        message.send()
    except Exception:  # provedor fora do ar não pode desfazer um pagamento
        logger.exception("Falha ao enviar o e-mail %s do pedido %s", template, context["order"].number)
        return False
    return True


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


def send_order_confirmation_email(order) -> bool:
    """Confirmação para o cliente. Enviada **uma vez** por pedido."""
    if order.confirmation_email_sent_at is not None:
        return False

    with translation.override(order_language(order)):
        sent = _send(
            _("Pedido %(number)s confirmado — JD PRINT") % {"number": order.number},
            "order_confirmation",
            order_context(order),
            [order.customer.user.email],
        )

    if sent:
        order.confirmation_email_sent_at = timezone.now()
        order.save(update_fields=["confirmation_email_sent_at", "updated_at"])
    return sent


def send_order_shipped_email(order) -> bool:
    """Aviso de envio, com rastreio quando existe."""
    if order.shipped_email_sent_at is not None:
        return False

    with translation.override(order_language(order)):
        context = order_context(order)
        context["tracking_url"] = order.tracking_url
        sent = _send(
            _("Pedido %(number)s enviado — JD PRINT") % {"number": order.number},
            "order_shipped",
            context,
            [order.customer.user.email],
        )

    if sent:
        order.shipped_email_sent_at = timezone.now()
        order.save(update_fields=["shipped_email_sent_at", "updated_at"])
    return sent


# ---------------------------------------------------------------------------
# Administração
# ---------------------------------------------------------------------------


def admin_recipients() -> list[str]:
    return list(getattr(settings, "ORDER_ADMIN_EMAILS", []) or [])


def send_admin_order_email(order) -> bool:
    """Ordem de produção. Sempre em português — é a equipe que lê."""
    with translation.override(settings.LANGUAGE_CODE):
        context = order_context(order)
        context["admin_url"] = absolute_url(
            f"/admin/orders/order/{order.pk}/change/"
        )
        return _send(
            _("[JD PRINT] Novo pedido %(number)s — %(total)s %(currency)s")
            % {"number": order.number, "total": f"{order.total:.2f}", "currency": order.currency},
            "order_admin",
            context,
            admin_recipients(),
        )


def send_order_emails(order) -> None:
    """Os dois e-mails da confirmação, na ordem em que importam.

    O do cliente primeiro: se o segundo falhar, quem comprou já foi avisado.
    """
    send_order_confirmation_email(order)
    send_admin_order_email(order)
