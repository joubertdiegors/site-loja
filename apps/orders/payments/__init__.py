"""Escolha do provedor de pagamento.

Um ponto só de entrada: ``get_provider()``. Quem precisa cobrar não sabe (nem
deve saber) qual gateway está configurado.
"""

from django.conf import settings

from apps.orders.payments.base import (
    PaymentError,
    PaymentProvider,
    PaymentStart,
    WebhookError,
    WebhookMessage,
)
from apps.orders.payments.stripe_provider import StripeProvider
from apps.orders.payments.transfer_provider import TransferProvider

__all__ = [
    "PaymentError",
    "PaymentProvider",
    "PaymentStart",
    "WebhookError",
    "WebhookMessage",
    "StripeProvider",
    "TransferProvider",
    "get_provider",
    "ImproperlyConfiguredProvider",
    "PROVIDERS",
]


class ImproperlyConfiguredProvider(Exception):
    """``PAYMENT_PROVIDER`` aponta para um provedor que não existe."""


PROVIDERS = {
    "stripe": StripeProvider,
    # Provisório: a loja precisa vender antes de o gateway existir. Sai
    # trocando `PAYMENT_PROVIDER` no `.env`, sem tocar em pedido nenhum.
    "transfer": TransferProvider,
}


def get_provider(name: str = "") -> PaymentProvider:
    """O provedor configurado. Nome desconhecido é erro de configuração, não silêncio."""
    name = (name or getattr(settings, "PAYMENT_PROVIDER", "stripe")).lower()
    try:
        return PROVIDERS[name]()
    except KeyError as error:
        raise ImproperlyConfiguredProvider(
            f"PAYMENT_PROVIDER={name!r} não existe. Opções: {', '.join(sorted(PROVIDERS))}."
        ) from error
