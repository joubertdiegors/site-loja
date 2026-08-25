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

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import CustomerAddress
from apps.shipping.models import ShippingMethod


class CheckoutForm(forms.Form):
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
