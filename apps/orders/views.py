"""Checkout, retorno do pagamento, webhook e área de pedidos do cliente.

Duas regras atravessam o arquivo.

**A primeira: quem paga tem conta.** Navegar, montar carrinho e mudar
quantidade continuam abertos a qualquer visitante — é a etapa 3 e ela não muda.
Fechar a compra, não: um pedido precisa de dono, de endereço e de um lugar para
ser acompanhado depois. Por isso ``/carrinho/finalizar/`` responde 200 para
todo mundo: o visitante vê o resumo e o convite para entrar, em vez de um
redirecionamento seco que perde a compra.

**A segunda: pagamento se confirma pelo webhook.** ``ConfirmationView`` é uma
página que qualquer pessoa pode abrir digitando a URL — ela consulta o estado,
nunca o altera. Quem move o pedido para "pago" é ``StripeWebhookView``, depois
de conferir a assinatura.
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, TemplateView

from apps.accounts.models import Customer
from apps.cart.cart import Cart
from apps.orders import services
from apps.orders.forms import CancellationRequestForm, CheckoutForm
from apps.orders.models import Order, OrderEvent, WebhookEvent
from apps.orders.payments import PaymentError, WebhookError, get_provider

logger = logging.getLogger(__name__)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


class CheckoutView(TemplateView):
    """``/carrinho/finalizar/`` — a única porta para virar pedido.

    Três situações, todas com resposta 200:

    * **visitante** — resumo do pedido + convite para entrar ou criar conta;
    * **carrinho vazio** — estado vazio com link para a loja;
    * **cliente com itens** — o formulário completo.
    """

    template_name = "orders/checkout.html"

    def dispatch(self, request, *args, **kwargs):
        self.cart = Cart(request)
        self.lines = self.cart.lines()
        return super().dispatch(request, *args, **kwargs)

    # -- dados compartilhados ---------------------------------------------

    @property
    def customer(self):
        if not self.request.user.is_authenticated:
            return None
        customer, _created = Customer.objects.get_or_create(user=self.request.user)
        return customer

    def addresses(self):
        customer = self.customer
        if customer is None:
            return []
        return list(
            customer.addresses.select_related("country").prefetch_related("country__translations")
        )

    def selected_address(self, addresses, submitted=None):
        """Endereço de entrega em uso agora: o escolhido, ou o padrão."""
        if submitted:
            for address in addresses:
                if str(address.pk) == str(submitted):
                    return address
        for address in addresses:
            if address.is_default_shipping:
                return address
        return addresses[0] if addresses else None

    def draft_for(self, address, method=None):
        country = address.country if address is not None else None
        return services.build_draft(self.lines, country=country, method=method)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Finalizar compra — JD PRINT")
        context["cart"] = self.cart
        context["lines"] = self.lines
        context["is_authenticated"] = self.request.user.is_authenticated

        addresses = self.addresses()
        submitted = self.request.POST.get("shipping_address") or self.request.GET.get(
            "shipping_address"
        )
        address = self.selected_address(addresses, submitted)

        submitted_method = self.request.POST.get("shipping_method") or self.request.GET.get(
            "shipping_method"
        )
        method = None
        if submitted_method:
            from apps.shipping.models import ShippingMethod

            method = ShippingMethod.objects.filter(pk=submitted_method).first()

        draft = self.draft_for(address, method)

        context["addresses"] = addresses
        context["selected_address"] = address
        context["draft"] = draft
        context["problems"] = services.validate_lines(self.lines) if self.lines else []
        context["payment_available"] = get_provider().is_configured
        context.setdefault("form", None)
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)

        if request.user.is_authenticated and self.lines:
            context["form"] = CheckoutForm(
                customer=self.customer, methods=context["draft"].options
            )

        # Recalcular frete/resumo quando o cliente troca de endereço, sem
        # recarregar a página inteira (mesmo mecanismo do Shop e da gaveta).
        if _is_htmx(request) and request.GET.get("partial") == "delivery":
            context["partial"] = True
            return render(request, "orders/_delivery.html", context)

        return self.render_to_response(context)

    # -- criação do pedido -------------------------------------------------

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Não é um erro: é o passo que falta. Volta para cá depois de entrar.
            return redirect(f"{reverse('accounts:login')}?next={reverse('cart:checkout')}")

        context = self.get_context_data(**kwargs)
        if not self.lines:
            return self.render_to_response(context)

        form = CheckoutForm(
            request.POST, customer=self.customer, methods=context["draft"].options
        )
        context["form"] = form

        if not form.is_valid():
            return self.render_to_response(context)

        try:
            order = services.create_order(
                customer=self.customer,
                lines=self.lines,
                shipping_address=form.cleaned_data["shipping_address"],
                billing_address=form.cleaned_data["billing_address"],
                shipping_method=form.cleaned_data["shipping_method"],
                is_gift=form.cleaned_data.get("is_gift", False),
                customer_note=form.cleaned_data.get("customer_note", ""),
                language=get_language() or "",
            )
        except services.CheckoutError as error:
            messages.error(request, error.message)
            context["problems"] = error.problems or context["problems"]
            return self.render_to_response(context)

        try:
            start = get_provider().start(order, request)
        except PaymentError as error:
            # O pedido fica gravado como pendente: o cliente pode tentar pagar
            # de novo pela conta, sem remontar o carrinho.
            order.log(OrderEvent.PAYMENT_FAILED, str(error), visible=False)
            messages.error(request, str(error))
            return redirect("orders:detail", number=order.number)

        order.log(OrderEvent.PAYMENT_STARTED, _("Pagamento iniciado."), visible=False)

        # O carrinho só é esvaziado aqui, com o pedido já gravado: se algo
        # falhar antes, o cliente não perde nada.
        self.cart.clear()

        return HttpResponseRedirect(start.redirect_url)


# ---------------------------------------------------------------------------
# Retorno do pagamento
# ---------------------------------------------------------------------------


class OrderAccessMixin(LoginRequiredMixin):
    """Um cliente só enxerga os pedidos dele.

    O filtro é por dono, não por permissão genérica: o número do pedido está na
    URL e, mesmo sendo difícil de adivinhar, número em URL não é credencial.
    """

    def get_queryset(self):
        return Order.objects.for_user(self.request.user).with_details()

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        return get_object_or_404(queryset, number=self.kwargs["number"])


class ConfirmationView(OrderAccessMixin, DetailView):
    """Página de retorno da Stripe.

    **Não confirma nada.** Ela lê o estado que o webhook já gravou (ou ainda
    não) e diz ao cliente o que está acontecendo. Abrir esta URL na mão não
    torna nenhum pedido pago.
    """

    template_name = "orders/confirmation.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Pedido confirmado — JD PRINT")
        context["awaiting_payment"] = not self.object.is_paid
        return context


class PaymentCancelledView(OrderAccessMixin, DetailView):
    """O cliente desistiu na página da Stripe. O pedido continua pendente."""

    template_name = "orders/payment_cancelled.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Pagamento não concluído — JD PRINT")
        return context


class OrderRetryPaymentView(OrderAccessMixin, View):
    """Nova tentativa de pagamento de um pedido pendente. POST com CSRF."""

    def post(self, request, *args, **kwargs):
        order = self.get_object()

        if order.is_paid or order.is_cancelled:
            return redirect("orders:detail", number=order.number)

        try:
            start = get_provider().start(order, request)
        except PaymentError as error:
            messages.error(request, str(error))
            return redirect("orders:detail", number=order.number)

        order.log(OrderEvent.PAYMENT_STARTED, _("Nova tentativa de pagamento."), visible=False)
        return HttpResponseRedirect(start.redirect_url)


# ---------------------------------------------------------------------------
# Área do cliente
# ---------------------------------------------------------------------------


class OrderDetailView(OrderAccessMixin, DetailView):
    template_name = "accounts/order_detail.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Pedido %(number)s — JD PRINT") % {"number": self.object.number}
        context["account_page"] = "orders"
        context["items"] = self.object.items.all()
        # O histórico interno fica no admin; aqui só o que é do cliente.
        context["history"] = self.object.history.filter(is_customer_visible=True)
        return context


class OrderCancelView(OrderAccessMixin, DetailView):
    """Solicitação de cancelamento — o cliente pede, a loja decide."""

    template_name = "accounts/order_cancel.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Solicitar cancelamento — JD PRINT")
        context["account_page"] = "orders"
        context.setdefault("form", CancellationRequestForm())
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if not self.object.can_request_cancellation:
            messages.error(request, _("Este pedido não pode mais ser cancelado por aqui."))
            return redirect("orders:detail", number=self.object.number)

        form = CancellationRequestForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        services.request_cancellation(
            self.object, form.cleaned_data["reason"], user=request.user
        )
        messages.success(
            request,
            _("Pedido de cancelamento enviado. Respondemos assim que analisarmos."),
        )
        return redirect("orders:detail", number=self.object.number)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(View):
    """Notificações da Stripe — a autoridade sobre o que foi pago.

    **Por que ``csrf_exempt`` aqui e em nenhum outro lugar.** O CSRF protege
    contra o *navegador do cliente* ser induzido a enviar um POST. Quem chama
    esta URL não é um navegador: é o servidor da Stripe, que não tem cookie de
    sessão nem token nosso. A autenticação certa neste caso é a assinatura
    ``Stripe-Signature`` — HMAC sobre o corpo bruto, com um segredo que só nós
    e a Stripe conhecemos. Sem ela, nada é processado.

    **Idempotência.** A Stripe reenvia eventos até receber 200. O ``event_id``
    é gravado com unicidade **antes** de o evento ser aplicado: a segunda
    entrega esbarra na constraint do banco e sai por 200 sem fazer nada. Isso
    vale mesmo para duas entregas simultâneas, porque quem decide é o banco e
    não um ``if``.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        provider = get_provider()
        signature = request.headers.get("Stripe-Signature", "")

        try:
            message = provider.parse_webhook(request.body, signature)
        except WebhookError as error:
            logger.warning("Webhook recusado: %s", error)
            return HttpResponse(status=400)
        except Exception:  # corpo ilegível, provedor mal configurado
            logger.exception("Webhook: falha inesperada ao interpretar o evento")
            return HttpResponse(status=400)

        if not message.event_id:
            return HttpResponse(status=400)

        try:
            with transaction.atomic():
                record = WebhookEvent.objects.create(
                    provider=provider.name,
                    event_id=message.event_id,
                    event_type=message.event_type,
                )
        except IntegrityError:
            # Já processado. 200 para a Stripe parar de reenviar.
            return HttpResponse(status=200)

        try:
            provider.handle(message)
        except Exception:
            # Devolver 500 faz a Stripe reenviar — e o registro acima impediria
            # o reprocessamento. Apagamos para a nova entrega poder tentar.
            record.delete()
            logger.exception("Webhook %s: falha ao aplicar o evento", message.event_id)
            return HttpResponse(status=500)

        from django.utils import timezone

        record.processed_at = timezone.now()
        if message.order_number:
            record.order = Order.objects.filter(number=message.order_number).first()
        record.save(update_fields=["processed_at", "order"])

        return HttpResponse(status=200)
