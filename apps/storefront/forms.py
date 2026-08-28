"""O formulário de contato.

Um só, e simples: nome, e-mail, assunto e mensagem. A revenda não tem
formulário próprio — ela termina numa chamada para esta mesma página, porque
uma segunda caixa de entrada com campos próprios já seria um sistema de
revendedores.

O visual é o mesmo das telas de conta (`form-input`, `_field.html`): nenhum
estilo novo, nenhum campo que se comporte diferente do resto da loja.

Toda validação é do servidor. Os atributos HTML (`required`, `type="email"`)
ajudam quem tem JavaScript, mas quem chega com um POST montado à mão passa
exatamente pelas mesmas regras.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

#: As mesmas classes das telas de conta.
TEXT_INPUT = {"class": "form-input"}
TEXTAREA = {"class": "form-input", "rows": 6}

#: Um limite generoso para uma mensagem escrita à mão. Existe para o campo não
#: virar porta de entrada de um megabyte de texto por requisição — o
#: `TextField` do banco aceitaria.
MAX_MESSAGE = 4000


class HoneypotMixin(forms.Form):
    """Um campo que ninguém vê e que só um robô preenche.

    Não é segurança — é higiene. Formulário público sem nenhuma barreira enche
    a caixa de entrada de lixo em uma semana, e um captcha seria atrito para o
    cliente de verdade resolver um problema que ainda não temos.

    O campo é escondido por CSS e ignorado por leitor de tela; quem o preencher
    recebe um erro genérico, sem explicar por quê.
    """

    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "class": "jd-hp",
                "tabindex": "-1",
                "autocomplete": "off",
                "aria-hidden": "true",
            }
        ),
    )

    def clean_website(self):
        if (self.cleaned_data.get("website") or "").strip():
            raise forms.ValidationError(_("Não foi possível enviar. Tente novamente."))
        return ""


class ContactForm(HoneypotMixin, forms.Form):
    """Nome, e-mail, assunto e mensagem — o que uma dúvida precisa."""

    name = forms.CharField(
        label=_("Nome"),
        max_length=120,
        widget=forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "name"}),
        error_messages={"required": _("Informe o seu nome.")},
    )
    email = forms.EmailField(
        label=_("E-mail"),
        widget=forms.EmailInput(attrs={**TEXT_INPUT, "autocomplete": "email"}),
        error_messages={
            "required": _("Informe o seu e-mail."),
            "invalid": _("Informe um e-mail válido."),
        },
    )
    subject = forms.CharField(
        label=_("Assunto"),
        max_length=200,
        widget=forms.TextInput(attrs=TEXT_INPUT),
        error_messages={"required": _("Informe o assunto.")},
    )
    message = forms.CharField(
        label=_("Mensagem"),
        max_length=MAX_MESSAGE,
        widget=forms.Textarea(attrs=TEXTAREA),
        error_messages={"required": _("Escreva a sua mensagem.")},
    )

    def clean_message(self):
        texto = (self.cleaned_data.get("message") or "").strip()
        if len(texto) < 10:
            raise forms.ValidationError(
                _("Escreva um pouco mais para podermos ajudar.")
            )
        return texto
