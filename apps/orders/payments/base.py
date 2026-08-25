"""Contrato de um meio de pagamento.

O resto do sistema fala com esta interface, nunca com a Stripe. É o que
permite trocar (ou acrescentar) provedor sem procurar ``stripe.`` espalhado por
views, models e e-mails — e é o que torna possível testar o fluxo do pedido sem
rede.

Três momentos, e só três:

* ``start``          — o cliente clica em pagar; devolvemos para onde mandá-lo;
* ``parse_webhook``  — chega uma notificação; ou ela é autêntica, ou é lixo;
* ``handle``         — a notificação autêntica muda o estado do pedido.

O que **não** existe aqui de propósito: nenhum método "marcar como pago". Quem
confirma pagamento é o provedor, pelo webhook. A página de retorno do cliente
não é prova de nada — ela é apenas uma URL que qualquer um pode abrir.
"""

from dataclasses import dataclass, field


class PaymentError(Exception):
    """Falha ao iniciar o pagamento. A mensagem é para o cliente."""


class WebhookError(Exception):
    """Notificação inválida: assinatura errada, corpo adulterado ou repetido."""


@dataclass(frozen=True)
class PaymentStart:
    """Para onde mandar o cliente e qual tentativa isso registrou."""

    redirect_url: str
    payment: object


@dataclass(frozen=True)
class WebhookMessage:
    """Uma notificação já autenticada, traduzida para o vocabulário da loja."""

    event_id: str
    event_type: str
    order_number: str = ""
    session_id: str = ""
    payment_id: str = ""
    is_paid: bool = False
    is_failed: bool = False
    method_label: str = ""
    failure_message: str = ""
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    """Interface implementada por cada provedor."""

    name = ""

    @property
    def is_configured(self) -> bool:
        """Há credenciais? Sem elas o checkout avisa em vez de fingir."""
        raise NotImplementedError

    def start(self, order, request=None) -> PaymentStart:
        raise NotImplementedError

    def parse_webhook(self, payload: bytes, signature: str) -> WebhookMessage:
        raise NotImplementedError

    def handle(self, message: WebhookMessage) -> bool:
        raise NotImplementedError
