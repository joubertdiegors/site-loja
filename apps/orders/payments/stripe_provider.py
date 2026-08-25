"""Pagamento com Stripe Checkout (página hospedada pela Stripe).

**Por que Checkout hospedado e não Payment Element.** Com o Checkout, o cliente
digita o cartão no domínio da Stripe: nenhum dado de cartão passa pelo nosso
servidor nem pelo nosso JavaScript, o que mantém a loja no escopo PCI mais
simples (SAQ-A em vez de SAQ-A-EP). A Stripe também resolve sozinha o 3-D
Secure exigido pelo PSD2 na Europa, aceita Bancontact/iDEAL/Apple Pay conforme
o país e traduz a própria página para PT/FR/NL/EN. O Payment Element daria mais
controle visual em troca de um fluxo de pagamento inteiro em JavaScript — o
oposto do que este projeto é.

**O que confirma um pagamento.** O webhook, sempre. A volta do cliente para
``/pedido/.../confirmacao/`` é só uma navegação: qualquer pessoa pode abrir
aquela URL, e o cliente pode fechar o navegador antes de voltar. Por isso
``start()`` não muda estado nenhum e a página de retorno apenas *consulta*.

**Idempotência.** Em dois pontos: ao criar a sessão (chave de idempotência da
própria Stripe, para um duplo clique não gerar duas cobranças) e ao processar o
evento (``WebhookEvent`` com unicidade no banco, porque a Stripe reenvia).

Nenhuma credencial aqui: tudo vem de ``settings`` e, portanto, do ``.env``.
"""

import json
import logging
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.orders.models import Payment, PaymentState
from apps.orders.payments.base import (
    PaymentError,
    PaymentProvider,
    PaymentStart,
    WebhookError,
    WebhookMessage,
)

logger = logging.getLogger(__name__)

#: Eventos que interessam. O resto a Stripe pode mandar à vontade: devolvemos
#: 200 e ignoramos, que é o comportamento recomendado.
PAID_EVENTS = {"checkout.session.completed", "checkout.session.async_payment_succeeded"}
FAILED_EVENTS = {"checkout.session.async_payment_failed", "checkout.session.expired"}

#: Idioma da loja -> locale aceito pela Stripe.
LOCALES = {"pt-br": "pt-BR", "pt": "pt-BR", "fr": "fr", "nl": "nl", "en": "en"}


def _stripe():
    """Importa a biblioteca só quando ela é realmente usada.

    Assim uma instalação sem ``stripe`` ainda sobe a loja inteira — só o passo
    de pagamento avisa que está indisponível.
    """
    try:
        import stripe
    except ImportError as error:  # pragma: no cover - ambiente sem a dependência
        raise PaymentError(
            _("O meio de pagamento não está instalado neste servidor.")
        ) from error

    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def _absolute(path: str) -> str:
    return f"{str(settings.SITE_URL).rstrip('/')}{path}"


def _cents(value: Decimal) -> int:
    """Euros para centavos, sem passar por float em momento nenhum."""
    return int((Decimal(value or 0) * 100).quantize(Decimal("1")))


class StripeProvider(PaymentProvider):
    name = "stripe"

    @property
    def is_configured(self) -> bool:
        return bool(getattr(settings, "STRIPE_SECRET_KEY", ""))

    # -- início ------------------------------------------------------------

    def _line_items(self, order) -> list[dict]:
        """Os itens como a Stripe os mostra na página de pagamento.

        Os valores enviados já incluem TVA (é o preço do catálogo). Não usamos
        o Stripe Tax: o imposto é calculado e guardado por nós, a partir do
        país de entrega (ver ``apps/orders/taxes.py``).
        """
        items = []
        for item in order.items.all():
            name = item.description[:250] or _("Produto")
            description = ""
            if item.has_personalization and item.personalization_text:
                description = _("Personalização: %(text)s") % {
                    "text": item.personalization_text[:80]
                }
            product_data = {"name": name}
            if description:
                product_data["description"] = description[:250]

            items.append(
                {
                    "quantity": item.quantity,
                    "price_data": {
                        "currency": order.currency.lower(),
                        "unit_amount": _cents(item.unit_price),
                        "product_data": product_data,
                    },
                }
            )
        return items

    def _shipping_options(self, order) -> list[dict]:
        if order.shipping_total <= 0 and not order.shipping_method_label:
            return []
        return [
            {
                "shipping_rate_data": {
                    "type": "fixed_amount",
                    "display_name": (order.shipping_method_label or _("Entrega"))[:100],
                    "fixed_amount": {
                        "amount": _cents(order.shipping_total),
                        "currency": order.currency.lower(),
                    },
                    "delivery_estimate": {
                        "minimum": {"unit": "business_day", "value": max(1, order.shipping_min_days)},
                        "maximum": {"unit": "business_day", "value": max(1, order.shipping_max_days)},
                    },
                }
            }
        ]

    def start(self, order, request=None) -> PaymentStart:
        """Cria a sessão de pagamento e a tentativa correspondente."""
        if not self.is_configured:
            raise PaymentError(
                _("O pagamento on-line está temporariamente indisponível. Tente novamente em instantes.")
            )

        stripe = _stripe()

        payment = Payment.objects.create(
            order=order,
            provider=self.name,
            amount=order.total,
            currency=order.currency,
            status=PaymentState.CREATED,
        )

        success_path = reverse("orders:confirmation", kwargs={"number": order.number})
        cancel_path = reverse("orders:payment_cancelled", kwargs={"number": order.number})
        expires_in = int(getattr(settings, "STRIPE_SESSION_EXPIRES_IN", 1800))

        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=self._line_items(order),
                shipping_options=self._shipping_options(order),
                client_reference_id=order.number,
                customer_email=order.customer.user.email or None,
                locale=LOCALES.get((order.language or "").lower(), "auto"),
                success_url=_absolute(success_path),
                cancel_url=_absolute(cancel_path),
                expires_at=int(timezone.now().timestamp()) + expires_in,
                metadata={"order_number": order.number, "payment_id": str(payment.pk)},
                payment_intent_data={
                    "metadata": {"order_number": order.number},
                    "description": f"JD PRINT {order.number}",
                },
                # Duplo clique no botão não pode virar duas cobranças.
                idempotency_key=f"jdprint-order-{order.number}-{payment.pk}",
            )
        except Exception as error:  # a Stripe tem dezenas de exceções próprias
            payment.status = PaymentState.FAILED
            payment.failure_message = str(error)[:300]
            payment.save(update_fields=["status", "failure_message", "updated_at"])
            logger.exception("Stripe: falha ao criar a sessão do pedido %s", order.number)
            raise PaymentError(
                _("Não foi possível iniciar o pagamento. Tente novamente em instantes.")
            ) from error

        payment.provider_session_id = session.get("id", "")
        payment.provider_payment_id = session.get("payment_intent") or ""
        payment.status = PaymentState.PROCESSING
        payment.save(
            update_fields=["provider_session_id", "provider_payment_id", "status", "updated_at"]
        )

        return PaymentStart(redirect_url=session["url"], payment=payment)

    # -- webhook -----------------------------------------------------------

    def parse_webhook(self, payload: bytes, signature: str) -> WebhookMessage:
        """Valida a assinatura e traduz o evento.

        A assinatura é o que autentica o remetente: sem ela, qualquer um que
        descobrisse a URL poderia declarar pedidos como pagos. Por isso a
        ausência de ``STRIPE_WEBHOOK_SECRET`` é erro, não um "deixa passar".
        """
        secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            raise WebhookError("STRIPE_WEBHOOK_SECRET não configurado.")

        stripe = _stripe()
        try:
            stripe.Webhook.construct_event(payload, signature, secret)
        except Exception as error:
            raise WebhookError(f"Assinatura inválida: {error}") from error

        # A partir daqui o corpo está autenticado. Trabalhamos sobre o JSON
        # cru, e não sobre o objeto da biblioteca: ``Event`` não é um dict e
        # o acesso encadeado a campos ausentes levantaria exceção em vez de
        # devolver vazio — logo abaixo há uma dúzia desses acessos.
        return self._to_message(json.loads(payload))

    def _to_message(self, event) -> WebhookMessage:
        event_type = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        metadata = obj.get("metadata") or {}

        is_paid = event_type in PAID_EVENTS and (
            obj.get("payment_status") in {"paid", "no_payment_required"}
        )

        return WebhookMessage(
            event_id=event.get("id", ""),
            event_type=event_type,
            order_number=metadata.get("order_number") or obj.get("client_reference_id") or "",
            session_id=obj.get("id", "") if str(obj.get("object", "")) == "checkout.session" else "",
            payment_id=obj.get("payment_intent") or "",
            is_paid=is_paid,
            is_failed=event_type in FAILED_EVENTS,
            method_label=self._method_label(obj),
            failure_message=(obj.get("last_payment_error") or {}).get("message", "") or "",
            raw=dict(event),
        )

    def _method_label(self, obj) -> str:
        types = obj.get("payment_method_types") or []
        if not types:
            return ""
        return {
            "card": _("Cartão"),
            "bancontact": "Bancontact",
            "ideal": "iDEAL",
            "sepa_debit": "SEPA",
            "link": "Link",
            "paypal": "PayPal",
        }.get(types[0], str(types[0]))

    # -- aplicação do evento ----------------------------------------------

    def handle(self, message: WebhookMessage) -> bool:
        """Aplica o evento ao pedido. Devolve ``True`` se algo mudou.

        Chamada apenas depois de ``parse_webhook``, e apenas uma vez por evento
        (a view registra o ``event_id`` antes de chamar).
        """
        from apps.orders import services

        payment = self._find_payment(message)
        order = payment.order if payment is not None else self._find_order(message)
        if order is None:
            logger.warning("Stripe: evento %s sem pedido correspondente", message.event_id)
            return False

        if message.is_paid:
            if payment is not None and message.payment_id and not payment.provider_payment_id:
                payment.provider_payment_id = message.payment_id
                payment.save(update_fields=["provider_payment_id", "updated_at"])
            return services.confirm_payment(order, payment, method_label=message.method_label)

        if message.is_failed:
            services.register_payment_failure(
                order, payment, message.failure_message or message.event_type
            )
            return True

        return False

    def _find_payment(self, message: WebhookMessage):
        query = Payment.objects.select_related("order", "order__customer")
        if message.session_id:
            payment = query.filter(provider_session_id=message.session_id).first()
            if payment is not None:
                return payment
        if message.payment_id:
            return query.filter(provider_payment_id=message.payment_id).first()
        return None

    def _find_order(self, message: WebhookMessage):
        from apps.orders.models import Order

        if not message.order_number:
            return None
        return Order.objects.filter(number=message.order_number).first()
