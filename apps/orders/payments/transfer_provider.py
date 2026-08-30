"""Transferência bancária — provisório, e assumidamente provisório.

A loja precisa vender antes de o gateway existir. Este provedor implementa o
mesmo contrato dos outros (`PaymentProvider`), então o fluxo do pedido não sabe
que é diferente: `start()` é chamado no mesmo lugar, o `Payment` é gravado na
mesma tabela e o pedido nasce no mesmo estado pendente.

O que muda é para onde o cliente vai: em vez da página de um gateway, ele vai
direto para a confirmação, que explica que a loja mandará os dados bancários.

## Nada aqui confirma pagamento

Não existe webhook: quem recebe o dinheiro é uma conta bancária, e quem sabe
que ele chegou é uma pessoa. `parse_webhook` e `handle` recusam qualquer
chamada — se algum dia aparecer um POST nessa rota dizendo que um pedido por
transferência foi pago, ele é lixo.

Confirmar o pagamento continua sendo o que sempre foi: `services.confirm_payment`,
disparado pela equipe no Admin depois de ver o extrato.

## Trocar por um gateway depois

Basta `PAYMENT_PROVIDER=stripe` no `.env`. Nenhum pedido antigo muda: cada
`Payment` guarda o provedor que o criou.
"""

import logging

from django.conf import settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.orders.payments.base import (
    PaymentProvider,
    PaymentStart,
    WebhookError,
)

logger = logging.getLogger(__name__)

#: O nome do meio de pagamento, para tela e para o `method_label` do `Payment`.
TRANSFER_LABEL = _("Transferência bancária")


class TransferProvider(PaymentProvider):
    """Pagamento por transferência, conferido à mão pela equipe."""

    name = "transfer"

    @property
    def is_configured(self) -> bool:
        """Sempre. Não há credencial: a conta bancária não é um segredo do código."""
        return True

    def start(self, order, request=None) -> PaymentStart:
        """Registra a tentativa, avisa a loja e manda o cliente para a confirmação.

        O `Payment` existe para o pedido ter a mesma forma que teria com um
        gateway — é ele que diz, depois, por qual meio o cliente escolheu pagar.
        """
        from apps.orders.models import Payment, PaymentState

        # O rótulo guardado é o do idioma da loja: quem lê este campo é a
        # equipe, no Admin. A tela do cliente traduz na hora de exibir.
        with translation.override(settings.LANGUAGE_CODE):
            rotulo = str(TRANSFER_LABEL)

        payment = Payment.objects.create(
            order=order,
            provider=self.name,
            amount=order.total,
            currency=order.currency,
            status=PaymentState.CREATED,
            method_label=rotulo,
        )

        self._notify_store(order)

        return PaymentStart(
            redirect_url=reverse("orders:confirmation", kwargs={"number": order.number}),
            payment=payment,
        )

    def _notify_store(self, order) -> None:
        """Avisa a equipe que há um pedido esperando transferência.

        Falhar aqui não desfaz nada: o pedido já está gravado, e é dele que a
        equipe parte. O mesmo cuidado do formulário de contato — provedor de
        e-mail fora do ar não pode apagar a venda de ninguém.
        """
        from apps.orders.emails import send_transfer_pending_email

        try:
            send_transfer_pending_email(order)
        except Exception:  # noqa: BLE001 — nenhuma falha de e-mail derruba um pedido
            logger.exception(
                "Falha ao avisar a loja sobre o pedido %s (transferência).", order.number
            )

    # -- não existe notificação automática ---------------------------------

    def parse_webhook(self, payload: bytes, signature: str):
        raise WebhookError("Pagamento por transferência não recebe notificações.")

    def handle(self, message) -> bool:
        raise WebhookError("Pagamento por transferência não recebe notificações.")
