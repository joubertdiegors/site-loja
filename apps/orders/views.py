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
import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError, IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect
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
from apps.core.security import ip_is_throttled
from apps.core.uploads import private_file_response
from apps.orders import services
from apps.orders.emails import send_payment_proof_email
from apps.orders.forms import CancellationRequestForm, CheckoutForm, PaymentProofForm
from apps.orders.models import CancellationStatus, Order, OrderEvent, WebhookEvent
from apps.orders.payments import (
    TRANSFER,
    PaymentError,
    WebhookError,
    available_checkout_methods,
    checkout_methods,
    get_checkout_method,
    get_provider,
)

logger = logging.getLogger(__name__)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def _checkout_token(request) -> str:
    """A chave da finalização, lida direto do POST.

    Não passa pelo formulário de propósito: a chave precisa ser conhecida
    **antes** de qualquer validação — antes de olhar o carrinho, antes de
    montar o formulário. Um segundo clique chega com o carrinho já vazio, e é
    a chave que o distingue de alguém que abriu o checkout sem nada dentro.

    Só o formato é conferido. Valor estranho vira vazio: no pior caso não casa
    com pedido nenhum e a finalização segue como se fosse a primeira — que é
    exatamente o que ela é.
    """
    chave = (request.POST.get("checkout_token") or "").strip()
    return chave if re.fullmatch(r"[0-9a-f]{32}", chave) else ""


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

            # O id vem do cliente (GET ou POST), então pode não ser número:
            # `filter(pk="abc")` levanta `ValueError` no ORM antes de qualquer
            # consulta, e isso seria um 500 anônimo na página de finalizar
            # compra. Método inválido e método inexistente são a mesma coisa
            # aqui — os dois caem em `None`, e `build_draft` escolhe a opção
            # mais barata, que é o comportamento de "ainda não escolheu".
            try:
                method_id = int(submitted_method)
            except (TypeError, ValueError):
                method_id = None
            if method_id is not None:
                method = ShippingMethod.objects.filter(pk=method_id).first()

        draft = self.draft_for(address, method)

        context["addresses"] = addresses
        context["selected_address"] = address
        context["draft"] = draft
        context["problems"] = services.validate_lines(self.lines) if self.lines else []
        provider = get_provider()
        context["payment_available"] = provider.is_configured
        # O nome do provedor, para a tela não falar de um meio de pagamento
        # que não é o que está cobrando. Quem escolhe é o servidor.
        context["payment_provider"] = provider.name
        # As formas de pagamento, **todas** — as indisponíveis aparecem
        # desativadas, com "em breve". Esconder o que ainda não existe faria a
        # tela parecer uma loja que só aceita transferência por opção; mostrar
        # desativado diz que o resto está a caminho.
        context["payment_methods"] = checkout_methods()
        context["available_payment_methods"] = available_checkout_methods()
        context.setdefault("form", None)
        return context

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)

        if request.user.is_authenticated and self.lines:
            context["form"] = CheckoutForm(
                customer=self.customer,
                methods=context["draft"].options,
                # Uma chave por tela aberta. Recarregar a página gera outra —
                # e está certo: recarregar é uma intenção nova. O que a chave
                # impede é a **mesma** tela virar dois pedidos.
                initial={"checkout_token": uuid.uuid4().hex},
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
        # A chave vem antes de tudo — inclusive de "o carrinho está vazio".
        #
        # O primeiro clique esvazia o carrinho ao terminar; o segundo chega com
        # o carrinho já vazio e, sem esta ordem, cairia na tela de "seu carrinho
        # está vazio" logo abaixo. Quem acabou de comprar veria um estado vazio
        # em vez do pedido que acabou de fazer — e concluiria que a compra não
        # passou.
        chave = _checkout_token(request)
        if chave:
            ja_existe = Order.objects.filter(
                customer=self.customer, checkout_token=chave
            ).first()
            if ja_existe is not None:
                self.cart.clear()
                return redirect("orders:confirmation", number=ja_existe.number)

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
                payment_method=form.cleaned_data.get("payment_method", ""),
                is_gift=form.cleaned_data.get("is_gift", False),
                customer_note=form.cleaned_data.get("customer_note", ""),
                language=get_language() or "",
                checkout_token=chave,
            )
        except services.CheckoutError as error:
            messages.error(request, error.message)
            context["problems"] = error.problems or context["problems"]
            return self.render_to_response(context)
        except DatabaseError:
            # Dois cliques **ao mesmo tempo**: as duas requisições passaram pela
            # consulta acima antes de qualquer uma gravar, e a segunda esbarrou
            # no banco. É o caso que nenhum `if` em Python resolve.
            #
            # `DatabaseError` e não `IntegrityError` porque o banco recusa de
            # duas maneiras: no PostgreSQL a segunda gravação viola a
            # constraint (`IntegrityError`); no SQLite ela nem chega lá — o
            # arquivo inteiro fica travado enquanto o outro escreve, e sai um
            # `OperationalError: database is locked`. As duas dizem a mesma
            # coisa: alguém chegou primeiro.
            #
            # Isto **não** engole falha de banco. Só há recuperação se o pedido
            # do outro clique realmente existir; qualquer outra coisa sobe.
            vencedor = Order.objects.filter(
                customer=self.customer, checkout_token=chave
            ).first()
            if vencedor is None:
                raise
            self.cart.clear()
            return redirect("orders:confirmation", number=vencedor.number)

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
        # Como o cliente escolheu pagar. Vem do pedido, nunca de um parâmetro
        # da URL: esta página é pública para quem tem o link e não decide nada.
        # O `Payment` continua sendo a reserva para pedidos anteriores a
        # `Order.payment_method`, que nasceram sem o campo.
        ultimo = self.object.payments.first()
        context["payment_provider"] = self.object.payment_method or (
            ultimo.provider if ultimo else ""
        )
        # Os dados que o cliente recebeu por e-mail, para a tela dizer o que foi
        # enviado sem repetir o IBAN numa página que o navegador guarda.
        context["bank_details"] = self.object.bank_details
        context["proof_url"] = reverse(
            "orders:payment_proof", kwargs={"number": self.object.number}
        )
        return context


class PaymentCancelledView(OrderAccessMixin, DetailView):
    """O cliente desistiu na página da Stripe. O pedido continua pendente."""

    template_name = "orders/payment_cancelled.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Pagamento não concluído — JD PRINT")
        return context


class OrderRetryPaymentView(OrderAccessMixin, DetailView):
    """"Pagar agora": uma tentativa nova, com as perguntas feitas de novo.

    Era um POST cego que repetia o `start()` do provedor. O problema é que um
    pedido esperando pagamento pode ficar dias parado, e nesse intervalo três
    coisas mudam sozinhas:

    * **o estoque.** A peça pode ter esgotado depois que o pedido nasceu.
      Mandar o cliente transferir por algo que a loja não tem mais é o pior
      desfecho possível — o dinheiro entra e a venda não existe;
    * **as formas de pagamento.** A loja pode ter ligado o cartão, ou
      desligado a transferência. A escolha de semanas atrás não vale como
      resposta de hoje;
    * **a conta bancária padrão.** Se ela mudou, é para a nova que o cliente
      tem de transferir.

    Por isso a tela existe: ela mostra o que está disponível **agora** e pede a
    escolha de novo. Com uma forma só, não há o que escolher e o botão
    simplesmente confirma; com duas, o cliente decide.

    O que ela **não** faz é criar pedido: é o mesmo `Order`, com uma tentativa
    de pagamento a mais.
    """

    template_name = "orders/retry_payment.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = self.object
        context["meta_title"] = _("Pagar o pedido %(number)s — JD PRINT") % {
            "number": order.number
        }
        context["account_page"] = "orders"
        # A disponibilidade é conferida na hora de abrir a tela **e** de novo
        # no envio: entre uma coisa e outra alguém pode ter comprado a última.
        context["problems"] = services.validate_order_items(order)
        context["payment_methods"] = checkout_methods()
        context["available_payment_methods"] = available_checkout_methods()
        context["can_pay"] = not (
            order.is_paid or order.is_cancelled or context["problems"]
        )
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.is_paid or self.object.is_cancelled:
            return redirect("orders:detail", number=self.object.number)
        return self.render_to_response(self.get_context_data())

    def post(self, request, *args, **kwargs):
        order = self.object = self.get_object()

        if order.is_paid or order.is_cancelled:
            return redirect("orders:detail", number=order.number)

        # Cada tentativa grava um `Payment` e manda e-mail. Sem freio, um laço
        # de POST queima a cota diária de envio da hospedagem — e o e-mail é o
        # canal pelo qual a equipe fica sabendo que há pedido esperando.
        if ip_is_throttled(request, "order-retry"):
            messages.error(
                request, _("Aguarde alguns instantes antes de tentar pagar de novo.")
            )
            return redirect("orders:detail", number=order.number)

        problemas = services.validate_order_items(order)
        if problemas:
            # Segunda checagem, agora dentro do envio: a tela pode ter sido
            # aberta antes de a última unidade sair.
            for problema in problemas:
                messages.error(request, problema)
            return self.render_to_response(self.get_context_data())

        escolhido = (request.POST.get("payment_method") or "").strip()
        disponiveis = available_checkout_methods()
        if not escolhido and len(disponiveis) == 1:
            # Uma opção só: o rádio já vem marcado e o silêncio quer dizer
            # "essa mesma" — a mesma regra do checkout.
            escolhido = disponiveis[0].code

        metodo = get_checkout_method(escolhido)
        if metodo is None:
            messages.error(request, _("Escolha uma forma de pagamento disponível."))
            return self.render_to_response(self.get_context_data())

        if metodo.code == TRANSFER:
            conta = services.refresh_transfer_account(order)
            if conta is None:
                messages.error(
                    request,
                    _(
                        "O pagamento por transferência está indisponível neste momento. "
                        "Entre em contato com a gente e concluímos o seu pedido."
                    ),
                )
                return self.render_to_response(self.get_context_data())

        # A forma escolhida agora passa a ser a do pedido: é por ela que a
        # equipe vai cobrar, e é ela que a tela de confirmação lê.
        if order.payment_method != metodo.code:
            order.payment_method = metodo.code
            order.save(update_fields=["payment_method", "updated_at"])

        try:
            start = get_provider(metodo.provider).start(order, request)
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
        # O histórico interno fica no Admin; aqui só o que é do cliente — e só
        # o que **tem uma frase para ele**. Um evento marcado como visível mas
        # sem mensagem de cliente (``customer_message`` vazio) desenharia uma
        # linha em branco na tela de quem comprou.
        context["history"] = [
            entrada
            for entrada in self.object.history.filter(is_customer_visible=True)
            if entrada.customer_message
        ]
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
        # A tela promete o que vai mesmo acontecer com **este** pedido: uns são
        # cancelados no clique, outros vão para análise. Uma frase só para os
        # dois casos estaria errada metade das vezes.
        context["plano"] = services.cancellation_plan(self.object)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if not self.object.can_request_cancellation:
            # Dizer só "não pode" deixa o cliente sem saber se é um erro da
            # tela ou uma regra — e ele escreve para perguntar. O prazo legal é
            # a resposta, e ela cabe na própria mensagem.
            messages.error(
                request,
                _(
                    "O prazo para cancelar ou devolver esta encomenda já passou. "
                    "Se precisar de ajuda, é só responder ao e-mail do pedido."
                ),
            )
            return redirect("orders:detail", number=self.object.number)

        form = CancellationRequestForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        try:
            services.request_cancellation(
                self.object, form.cleaned_data["reason"], user=request.user
            )
        except services.CancellationRefused as erro:
            messages.error(request, str(erro))
            return redirect("orders:detail", number=self.object.number)

        # A regra pode ter resolvido na hora — nada cobrado, ou pago sem a
        # produção ter começado. Dizer "vamos analisar" nesse caso seria pedir
        # ao cliente que esperasse por uma decisão que já foi tomada.
        self.object.refresh_from_db()
        if self.object.cancellation_status == CancellationStatus.APPROVED:
            messages.success(request, _("Pedido cancelado. Enviámos os detalhes por e-mail."))
        else:
            messages.success(
                request,
                _("Pedido de cancelamento enviado. Respondemos assim que analisarmos."),
            )
        return redirect("orders:detail", number=self.object.number)


class PaymentProofView(OrderAccessMixin, DetailView):
    """A página segura do comprovante — para onde o botão do e-mail leva.

    "Segura" aqui não é um token na URL. É `OrderAccessMixin`, o mesmo portão
    das outras telas de pedido: precisa estar autenticado, e o pedido é
    procurado dentro de `Order.objects.for_user(request.user)`. O pedido de
    outra pessoa não está nesse queryset, então trocar o número na barra de
    endereços dá **404** — não 403, que confirmaria que aquele número existe.

    Um token assinado no link seria mais frágil, não mais forte: e-mail se
    reencaminha, e quem recebesse o encaminhamento abriria o pedido de outra
    pessoa. Assim, quem chega pelo e-mail sem estar logado passa pela tela de
    entrar e volta para cá — o `next` do `LoginRequiredMixin` cuida disso.

    Enviar comprovante **não** confirma pagamento. A tela diz isso, e o
    `PaymentProofForm.save()` também não mexe em `payment_status`.

    ## Um comprovante, e só

    Depois do primeiro envio a tela deixa de oferecer o formulário e passa a
    dizer que o comprovante chegou — inclusive para quem voltar pelo botão do
    e-mail ou digitar a URL de novo. Um POST que insista é recusado aqui, antes
    de tocar em disco, e a constraint do banco é a rede embaixo.

    Substituir sozinho abriria a porta para trocar o documento depois de a
    equipe já ter olhado. Se o cliente mandou o arquivo errado, quem resolve é
    a equipe — e o arquivo original não é apagado.
    """

    template_name = "orders/payment_proof.html"
    context_object_name = "order"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Enviar comprovante — JD PRINT")
        context["account_page"] = "orders"
        context["proof"] = self.object.payment_proofs.first()
        context["already_sent"] = context["proof"] is not None
        if not context["already_sent"]:
            context.setdefault("form", PaymentProofForm())
        context["max_mb"] = (
            getattr(settings, "PAYMENT_PROOF_MAX_UPLOAD_SIZE", 10 * 1024 * 1024) // (1024 * 1024)
        )
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if self.object.payment_proofs.exists():
            messages.info(
                request,
                _("O comprovante deste pedido já foi recebido. Não é preciso enviar de novo."),
            )
            return redirect("orders:payment_proof", number=self.object.number)

        if ip_is_throttled(request, "payment-proof"):
            messages.error(
                request, _("Muitos envios seguidos. Tente novamente em alguns minutos.")
            )
            return redirect("orders:payment_proof", number=self.object.number)

        form = PaymentProofForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        try:
            proof = form.save(self.object)
        except IntegrityError:
            # Dois POST ao mesmo tempo: o segundo esbarra na constraint. Para
            # quem enviou, o resultado é o mesmo — o comprovante está lá.
            messages.info(request, _("O comprovante deste pedido já foi recebido."))
            return redirect("orders:payment_proof", number=self.object.number)
        self.object.log(
            OrderEvent.PAYMENT_PROOF_RECEIVED,
            _("Comprovante enviado pelo cliente: %(name)s") % {"name": proof.original_name},
            user=request.user,
        )
        try:
            send_payment_proof_email(self.object, proof)
        except Exception:  # avisar a equipe não pode desfazer o envio
            logger.exception("Falha ao avisar a equipe do comprovante do pedido %s", self.object.pk)

        messages.success(
            request,
            _("Comprovante recebido. Vamos conferir o pagamento e avisar você."),
        )
        return redirect("orders:payment_proof", number=self.object.number)


def payment_proof_file(request, pk: int):
    """Entrega o comprovante — só a quem tem o que ver com ele.

    Mesmo desenho de `cart.views.customization_file`, pelas mesmas razões. Duas
    portas, e nenhuma delas é "ter o link":

    * **a equipe** — staff com permissão de ver o comprovante ou de ver
      pedidos. É quem confere o pagamento;
    * **o dono** — o cliente do pedido a que o comprovante pertence.

    Quem não passa recebe **404**, não 403: um 403 confirmaria que o arquivo
    existe, e a lista de ids é curta de percorrer.

    O arquivo também não é servido como mídia pública: `payment-proofs/` não
    está entre as pastas que `config/urls.py` publica, e em produção o proxy
    aponta só para `products/` e `banners/`.
    """
    from apps.orders.models import PaymentProof

    proof = PaymentProof.objects.filter(pk=pk).select_related("order__customer").first()
    if proof is None or not proof.file:
        raise Http404("Arquivo não encontrado.")

    if not _may_read_proof(request, proof):
        raise Http404("Arquivo não encontrado.")

    return private_file_response(
        proof.file,
        content_type=proof.content_type,
        filename=proof.original_name or proof.file.name,
    )


def _may_read_proof(request, proof) -> bool:
    user = request.user
    if not user.is_authenticated:
        return False

    if user.is_staff and (
        user.has_perm("orders.view_paymentproof") or user.has_perm("orders.view_order")
    ):
        return True

    return proof.order.customer.user_id == user.pk


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
