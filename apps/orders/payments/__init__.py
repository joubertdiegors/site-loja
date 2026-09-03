"""Escolha do provedor de pagamento, e o que o checkout oferece.

Duas coisas, e elas respondem a perguntas diferentes.

``get_provider()`` responde **quem cobra**: um ponto só de entrada, para quem
precisa cobrar não saber (nem dever saber) qual gateway está configurado.

``checkout_methods()`` responde **o que o cliente vê**: a lista de formas de
pagamento da tela final, cada uma sabendo se pode ser escolhida agora. É onde
um método futuro entra — uma linha em ``CHECKOUT_METHODS`` e a tela passa a
mostrá-lo, desativado, sem template novo e sem `if` espalhado.

A disponibilidade não é um interruptor solto: um método só pode ser escolhido
quando **está implementado** e quando **o provedor dele é o que está
configurado** e responde por si (`is_configured`). É por isso que hoje o cartão
aparece como "em breve" — não porque alguém o desligou, mas porque a loja está
rodando em transferência. Trocar ``PAYMENT_PROVIDER`` para ``stripe`` volta a
oferecê-lo sem tocar em nada aqui.
"""

from dataclasses import dataclass

from django.conf import settings
from django.utils.translation import gettext_lazy as _

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
    "CheckoutMethod",
    "CHECKOUT_METHODS",
    "checkout_methods",
    "available_checkout_methods",
    "get_checkout_method",
    "method_for_provider",
    "TRANSFER",
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


# ---------------------------------------------------------------------------
# O que o checkout oferece
# ---------------------------------------------------------------------------

#: O código gravado em `Order.payment_method` para transferência bancária.
#: Existe como constante porque três arquivos precisam concordar sobre ele.
TRANSFER = "transfer"


@dataclass(frozen=True)
class CheckoutMethod:
    """Uma forma de pagamento, como o cliente a vê.

    ``code`` é o que vai para ``Order.payment_method`` e o que o formulário
    valida — nunca o rótulo, que é traduzido e muda.

    ``implemented`` diz se o código para cobrar por este meio existe. É o que
    separa "ainda não construímos" de "não está ligado agora": o Bancontact
    passa pela Stripe, mas ninguém escreveu esse caminho, então ele não fica
    disponível nem quando a Stripe está configurada.
    """

    code: str
    label: str
    note: str
    provider: str
    implemented: bool

    @property
    def is_available(self) -> bool:
        """Dá para escolher este meio agora?

        A pergunta é feita ao provedor, e não a uma lista de constantes: se a
        Stripe estiver sem credenciais, o cartão não é oferecido mesmo com
        ``PAYMENT_PROVIDER=stripe``. É a mesma checagem que o botão de finalizar
        já fazia — agora por método.
        """
        if not self.implemented:
            return False
        configurado = str(getattr(settings, "PAYMENT_PROVIDER", "stripe")).lower()
        if self.provider != configurado:
            return False
        try:
            return bool(get_provider(self.provider).is_configured)
        except ImproperlyConfiguredProvider:
            return False


#: A ordem aqui é a ordem na tela. O que está disponível vem primeiro.
CHECKOUT_METHODS = (
    CheckoutMethod(
        code=TRANSFER,
        label=_("Transferência bancária"),
        note=_("Você recebe os dados por e-mail assim que confirmar o pedido."),
        provider="transfer",
        implemented=True,
    ),
    CheckoutMethod(
        code="card",
        label=_("Cartão"),
        note=_("Visa, Mastercard e Maestro."),
        provider="stripe",
        implemented=True,
    ),
    CheckoutMethod(
        code="bancontact",
        label=_("Bancontact"),
        note=_("Pagamento pelo aplicativo do seu banco."),
        provider="stripe",
        implemented=False,
    ),
)


def checkout_methods() -> tuple[CheckoutMethod, ...]:
    """Todas as formas, disponíveis ou não — a tela mostra as duas coisas."""
    return CHECKOUT_METHODS


def available_checkout_methods() -> tuple[CheckoutMethod, ...]:
    """Só as que podem ser escolhidas agora. É contra esta lista que se valida."""
    return tuple(method for method in CHECKOUT_METHODS if method.is_available)


def method_for_provider(name: str = "") -> CheckoutMethod | None:
    """A forma de pagamento do provedor configurado — sem perguntar a ninguém.

    Serve o caminho em que **não houve escolha**: `create_order` chamado por
    código, e não pelo checkout. Diferente de `get_checkout_method`, não exige
    `is_configured`: quem chamou já decidiu que vai cobrar por este provedor, e
    recusar aqui só transformaria uma configuração faltando num erro num lugar
    que não sabe explicá-lo.

    Devolve o primeiro método **implementado** daquele provedor, na ordem de
    `CHECKOUT_METHODS`.
    """
    name = (name or getattr(settings, "PAYMENT_PROVIDER", "stripe")).lower()
    for method in CHECKOUT_METHODS:
        if method.implemented and method.provider == name:
            return method
    return None


def get_checkout_method(code: str) -> CheckoutMethod | None:
    """A forma de pagamento com este código, **se ela puder ser escolhida**.

    Um código desconhecido e um código de método desativado devolvem a mesma
    coisa: ``None``. Quem chama não precisa da diferença — as duas respostas ao
    cliente são "escolha uma forma de pagamento válida", e distinguir as duas na
    mensagem só contaria ao curioso o que existe do outro lado.
    """
    code = (code or "").strip().lower()
    for method in available_checkout_methods():
        if method.code == code:
            return method
    return None
