"""Formulário do checkout.

Ele só faz **escolhas**, nunca cálculos: endereço de entrega, endereço de
faturamento, método e presente. Preço, frete e imposto são recalculados no
servidor a partir dessas escolhas (ver ``services.create_order``) — nada que
venha do navegador entra numa soma.

As listas de opções são montadas a partir do que pertence ao cliente e do que
serve para o destino. Um id de endereço de outra pessoa, ou de um método que
não atende aquele país, simplesmente não está no ``queryset``: é o próprio
formulário que recusa, sem depender de um ``if`` na view.
"""

import re

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import CustomerAddress
from apps.core.uploads import sniff_receipt
from apps.orders.payments import checkout_methods, get_checkout_method
from apps.shipping.models import ShippingMethod


class CheckoutForm(forms.Form):
    #: A escolha do cliente, conferida contra o servidor. O `<input>` diz o que
    #: quiser; quem decide o que existe é `available_checkout_methods()`.
    #: A chave desta tela de checkout — uma por página aberta, não uma por
    #: clique. Volta no POST e é o que faz o segundo clique reconhecer o pedido
    #: que o primeiro criou. Ver `Order.checkout_token`.
    checkout_token = forms.CharField(required=False, widget=forms.HiddenInput)

    #: `CharField`, e nao `ChoiceField`, de proposito: com `choices` o Django
    #: recusa antes de `clean_payment_method` e devolve a mensagem dele, que
    #: repete na tela o valor que o navegador mandou. A decisao — e a mensagem —
    #: ficam num lugar so.
    payment_method = forms.CharField(required=False)

    shipping_address = forms.ModelChoiceField(
        label=_("Endereço de entrega"),
        queryset=CustomerAddress.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"required": _("Escolha para onde enviar.")},
    )
    billing_same_as_shipping = forms.BooleanField(
        label=_("A fatura vai para o mesmo endereço"),
        required=False,
        initial=True,
    )
    billing_address = forms.ModelChoiceField(
        label=_("Endereço de faturamento"),
        queryset=CustomerAddress.objects.none(),
        required=False,
        empty_label=None,
        widget=forms.RadioSelect,
    )
    shipping_method = forms.ModelChoiceField(
        label=_("Forma de entrega"),
        queryset=ShippingMethod.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"required": _("Escolha uma forma de entrega.")},
    )
    is_gift = forms.BooleanField(
        label=_("Enviar como presente"),
        required=False,
        help_text=_("A entrega vai para outra pessoa; a fatura continua no seu nome."),
    )
    customer_note = forms.CharField(
        label=_("Observação para a produção"),
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "form-input",
                "rows": 2,
                "placeholder": _("Algo que devemos saber antes de imprimir?"),
            }
        ),
    )

    def __init__(self, *args, customer=None, methods=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer

        # As formas de pagamento vêm do servidor a cada requisição, e não de
        # uma lista fixa na classe: o que está disponível depende do provedor
        # configurado, e um formulário montado ontem não decide o de hoje.
        self.payment_methods = checkout_methods()
        self.available_payment_methods = [m for m in self.payment_methods if m.is_available]
        if len(self.available_payment_methods) == 1:
            self.fields["payment_method"].initial = self.available_payment_methods[0].code

        addresses = CustomerAddress.objects.none()
        if customer is not None:
            addresses = (
                customer.addresses.select_related("country")
                .prefetch_related("country__translations")
                .all()
            )
        self.fields["shipping_address"].queryset = addresses
        self.fields["billing_address"].queryset = addresses

        # Só os métodos que servem este pedido (país + peso) entram na lista.
        ids = [option.method.pk for option in (methods or [])]
        self.fields["shipping_method"].queryset = ShippingMethod.objects.filter(pk__in=ids)

        default_shipping = addresses.filter(is_default_shipping=True).first()
        default_billing = addresses.filter(is_default_billing=True).first()
        if default_shipping is not None:
            self.fields["shipping_address"].initial = default_shipping.pk
        if default_billing is not None:
            self.fields["billing_address"].initial = default_billing.pk

    def clean_checkout_token(self):
        """Só o formato. Quem decide o que a chave significa é a view.

        Um valor estranho não é motivo para recusar uma compra: no pior caso
        ele não casa com pedido nenhum e a finalização segue como se fosse a
        primeira — que é exatamente o que ela é.
        """
        chave = (self.cleaned_data.get("checkout_token") or "").strip()
        return chave if re.fullmatch(r"[0-9a-f]{32}", chave) else ""

    def clean_payment_method(self):
        """A escolha do cliente, conferida contra o que a loja realmente aceita.

        O ``<input>`` é do navegador: um radio desabilitado na tela chega aqui
        como qualquer outro valor, e um código que nunca esteve na tela chega
        igual. Os dois são recusados pela mesma linha — quem decide é
        ``available_checkout_methods()``.

        Vazio tem dois significados, e eles são diferentes: com **uma** forma
        disponível não há escolha a fazer (é a que o radio já vem marcado), e o
        silêncio quer dizer "essa mesma"; com mais de uma, silêncio é um campo
        não preenchido, e o cliente precisa dizer.
        """
        escolhido = (self.cleaned_data.get("payment_method") or "").strip()

        if not escolhido:
            if len(self.available_payment_methods) == 1:
                return self.available_payment_methods[0].code
            raise forms.ValidationError(_("Escolha a forma de pagamento."))

        if get_checkout_method(escolhido) is None:
            raise forms.ValidationError(_("Escolha uma forma de pagamento disponível."))
        return escolhido

    def clean_shipping_address(self):
        address = self.cleaned_data["shipping_address"]
        if not address.country.is_active:
            raise forms.ValidationError(
                _("Ainda não entregamos em %(country)s. Escolha outro endereço.")
                % {"country": address.country.name}
            )
        return address

    def clean(self):
        cleaned = super().clean()

        # "Mesmo endereço" é o padrão: quem não marca nada não fica sem fatura.
        if cleaned.get("billing_same_as_shipping") or not cleaned.get("billing_address"):
            cleaned["billing_address"] = cleaned.get("shipping_address")

        if cleaned.get("billing_address") is None and "billing_address" not in self.errors:
            self.add_error("billing_address", _("Escolha o endereço de faturamento."))

        return cleaned


class CancellationRequestForm(forms.Form):
    """O cliente **pede** o cancelamento; quem decide é a loja."""

    reason = forms.CharField(
        label=_("Por que quer cancelar?"),
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "form-input",
                "rows": 4,
                "placeholder": _("Conte o que aconteceu — ajuda a resolver mais rápido."),
            }
        ),
        error_messages={"required": _("Escreva o motivo do cancelamento.")},
    )


class PaymentProofForm(forms.Form):
    """O comprovante que o cliente envia depois de transferir.

    A validação é a mesma da foto de personalização, e de propósito: limite de
    tamanho, arquivo vazio recusado, e o tipo decidido pelos **primeiros bytes**
    — nunca pela extensão nem pelo ``Content-Type``, que quem envia o arquivo
    também escreve. Um ``comprovante.jpg`` que por dentro é um SVG com
    ``<script>`` não casa com assinatura nenhuma e para aqui.

    A única diferença é o PDF, que entra porque é o que o banco entrega a quem
    paga pelo internet banking. Ver ``apps.core.uploads.sniff_receipt``.
    """

    file = forms.FileField(
        label=_("Comprovante"),
        help_text=_("Foto ou PDF do comprovante da transferência."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sniffed = None

    @property
    def max_bytes(self) -> int:
        return getattr(settings, "PAYMENT_PROOF_MAX_UPLOAD_SIZE", 10 * 1024 * 1024)

    def clean_file(self):
        enviado = self.cleaned_data["file"]

        if enviado.size > self.max_bytes:
            raise forms.ValidationError(
                _("O arquivo pode ter no máximo %(limit)s MB.")
                % {"limit": self.max_bytes // (1024 * 1024)}
            )
        if enviado.size == 0:
            raise forms.ValidationError(_("O arquivo enviado está vazio."))

        cabecalho = enviado.read(32)
        enviado.seek(0)
        reconhecido = sniff_receipt(cabecalho)
        if reconhecido is None:
            raise forms.ValidationError(
                _("Envie uma imagem (JPG, PNG ou WEBP) ou um PDF.")
            )

        self._sniffed = reconhecido
        return enviado

    def save(self, order) -> "PaymentProof":
        """Grava o comprovante do pedido. Não toca no pagamento.

        Nada aqui muda ``payment_status``: receber um arquivo não é ver o
        dinheiro na conta. Quem confirma é a equipe, no Admin, depois de olhar
        o extrato.
        """
        from apps.orders.models import PaymentProof

        enviado = self.cleaned_data["file"]
        extensao, tipo = self._sniffed
        proof = PaymentProof(
            order=order,
            original_name=(enviado.name or "")[:200],
            content_type=tipo,
            extension=extensao,
            size_bytes=enviado.size,
        )
        # O nome em disco vem de `payment_proof_upload_to`, que lê
        # `proof.extension` — por isso ele é preenchido antes do `save` do
        # arquivo, e o nome que o cliente escolheu nunca vira caminho.
        proof.file.save(f"comprovante.{extensao}", enviado, save=False)
        proof.save()
        return proof
