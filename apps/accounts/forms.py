"""Formulários de conta.

O cadastro público é curto de propósito — username, e-mail e senha. Nome,
telefone, empresa e NIF pertencem ao ``Customer`` e são pedidos depois, em
"Meus dados": exigir tudo na primeira tela é a forma mais rápida de perder o
cliente que só queria comprar um vaso.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    BaseUserCreationForm,
    PasswordChangeForm,
)
from django.contrib.auth.tokens import default_token_generator
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.accounts.emails import send_password_reset_email
from apps.accounts.models import Customer, CustomerAddress
from apps.accounts.validators import validate_public_username
from apps.core.languages import active_languages

User = get_user_model()

TEXT_INPUT = {"class": "form-input"}


class RegistrationForm(BaseUserCreationForm):
    """Cadastro público.

    Herda de ``BaseUserCreationForm`` para não reescrever o que o Django já
    faz bem: as duas senhas, a conferência entre elas e os validadores de
    ``AUTH_PASSWORD_VALIDATORS``.
    """

    email = forms.EmailField(
        label=_("E-mail"),
        max_length=254,
        widget=forms.EmailInput(attrs={**TEXT_INPUT, "autocomplete": "email"}),
        help_text=_("Usado para confirmar a conta e para falar sobre seus pedidos."),
    )

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("username", "email")
        widgets = {
            "username": forms.TextInput(
                attrs={**TEXT_INPUT, "autocomplete": "username", "autocapitalize": "none"}
            )
        }

    def __init__(self, *args, language=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.language = language
        for field in ("password1", "password2"):
            self.fields[field].widget.attrs.update(
                {**TEXT_INPUT, "autocomplete": "new-password"}
            )

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        # Nomes reservados (admin, carrinho, suporte...) só são recusados no
        # cadastro público; o administrador pode criá-los pelo terminal.
        validate_public_username(username)
        # A unicidade sem diferenciar maiúsculas é conferida aqui para a
        # mensagem chegar no campo certo; o banco confere de novo, sempre.
        if username and User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(
                _("Este nome de usuário não está disponível."), code="unique"
            )
        return username

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data.get("email"))
        if email and User.objects.filter(email__iexact=email).exists():
            # Aqui a existência é revelada de propósito: sem isso, a pessoa
            # não teria como saber por que o cadastro não conclui. Nos fluxos
            # de recuperação e reenvio, a resposta é genérica.
            raise forms.ValidationError(
                _("Já existe uma conta com este e-mail. Tente entrar."), code="unique"
            )
        return email

    def save(self, commit=True):
        """Cria a conta e o cliente vazio, na mesma operação.

        ``Customer`` nasce junto porque a partir daqui todo pedido, endereço e
        fatura pendura nele. Nasce vazio porque o cadastro não pergunta nada
        comercial.
        """
        user = super().save(commit=False)
        user.email = User.objects.normalize_email(self.cleaned_data["email"])
        if self.language:
            user.preferred_language = self.language
        if commit:
            user.save()
            Customer.objects.create(user=user)
        return user


class LoginForm(AuthenticationForm):
    """Um campo só: "Usuário ou e-mail"."""

    username = forms.CharField(
        label=_("Usuário ou e-mail"),
        max_length=254,
        widget=forms.TextInput(
            attrs={**TEXT_INPUT, "autofocus": True, "autocomplete": "username", "autocapitalize": "none"}
        ),
    )
    password = forms.CharField(
        label=_("Senha"),
        strip=False,
        widget=forms.PasswordInput(attrs={**TEXT_INPUT, "autocomplete": "current-password"}),
    )

    # Mensagem única para senha errada, conta inexistente e conta desativada:
    # três respostas diferentes seriam três formas de descobrir quem tem conta.
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Usuário, e-mail ou senha incorretos."),
        "inactive": _("Usuário, e-mail ou senha incorretos."),
    }


class PasswordResetRequestForm(forms.Form):
    """"Esqueci minha senha".

    Responde sempre a mesma coisa, exista ou não a conta (ver a view).
    """

    email = forms.EmailField(
        label=_("E-mail"),
        max_length=254,
        widget=forms.EmailInput(attrs={**TEXT_INPUT, "autocomplete": "email"}),
    )

    def get_users(self, email):
        """Contas ativas com senha utilizável para este e-mail."""
        return [
            user
            for user in User.objects.filter(email__iexact=email, is_active=True)
            if user.has_usable_password()
        ]

    def save(self, token_generator=default_token_generator) -> int:
        """Envia o link. Devolve quantos e-mails saíram (a view ignora).

        O idioma vai daqui: este formulário é processado dentro da requisição,
        e ``get_language()`` devolve o idioma da tela em que a pessoa digitou o
        e-mail — o que ela escolheu, não o que a conta guardou há meses. É o
        único ponto do fluxo que sabe as duas coisas.
        """
        email = User.objects.normalize_email(self.cleaned_data["email"])
        idioma = translation.get_language()
        sent = 0
        for user in self.get_users(email):
            if send_password_reset_email(user, token_generator, language=idioma):
                sent += 1
        return sent


class ResendVerificationForm(forms.Form):
    """Reenvio do e-mail de confirmação para quem não está autenticado."""

    email = forms.EmailField(
        label=_("E-mail"),
        max_length=254,
        widget=forms.EmailInput(attrs={**TEXT_INPUT, "autocomplete": "email"}),
    )


class CustomerForm(forms.ModelForm):
    """"Meus dados" — só o cliente, nunca a autenticação.

    O idioma preferido aparece na mesma tela por conveniência, mas é gravado no
    ``User``: é ele quem manda e-mail.
    """

    preferred_language = forms.ChoiceField(label=_("Idioma preferido"), choices=())

    class Meta:
        model = Customer
        fields = ("first_name", "last_name", "phone", "company_name", "vat_number")
        widgets = {
            "first_name": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "family-name"}),
            "phone": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "tel"}),
            "company_name": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "organization"}),
            "vat_number": forms.TextInput(attrs=TEXT_INPUT),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # As opções vêm da tabela de idiomas da loja, nunca de uma lista
        # escrita aqui: desligar um idioma no admin some com ele daqui também.
        self.fields["preferred_language"].choices = [
            (language.code, language.native_name) for language in active_languages()
        ]
        self.fields["preferred_language"].widget.attrs.update({"class": "form-select"})
        self.fields["preferred_language"].initial = self.instance.user.preferred_language

    def save(self, commit=True):
        customer = super().save(commit=commit)
        if commit:
            user = customer.user
            user.preferred_language = self.cleaned_data["preferred_language"]
            user.save(update_fields=["preferred_language"])
        return customer


class StyledPasswordChangeForm(PasswordChangeForm):
    """Troca de senha em "Minha conta › Segurança".

    É o formulário do Django (exige a senha atual, aplica os validadores de
    ``AUTH_PASSWORD_VALIDATORS`` e regrava o hash). Só as classes visuais
    mudam — a etapa 6 é de frontend.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            autocomplete = "current-password" if name == "old_password" else "new-password"
            field.widget.attrs.update({"class": "form-input", "autocomplete": autocomplete})


class EmailChangeForm(forms.Form):
    """Troca do endereço de e-mail.

    Pede a senha atual de propósito: o e-mail é o que recupera a conta, então
    trocá-lo com a sessão aberta de outra pessoa não pode ser fácil. A regra em
    si já existe desde a etapa 5 (``User.set_email`` derruba a confirmação); o
    formulário só a aciona.
    """

    email = forms.EmailField(
        label=_("Novo e-mail"),
        max_length=254,
        widget=forms.EmailInput(attrs={**TEXT_INPUT, "autocomplete": "email"}),
    )
    password = forms.CharField(
        label=_("Sua senha atual"),
        strip=False,
        widget=forms.PasswordInput(attrs={**TEXT_INPUT, "autocomplete": "current-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data.get("password", "")
        if not self.user.check_password(password):
            raise forms.ValidationError(_("Senha incorreta."), code="invalid_password")
        return password

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data.get("email"))
        if email == self.user.email:
            raise forms.ValidationError(
                _("Este já é o seu e-mail atual."), code="unchanged"
            )
        if User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError(
                _("Já existe uma conta com este e-mail."), code="unique"
            )
        return email

    def save(self) -> bool:
        """Grava o novo endereço. Devolve ``True`` se ele realmente mudou."""
        return self.user.set_email(self.cleaned_data["email"])


class AddressForm(forms.ModelForm):
    """Cadastro e edição de um endereço do cliente.

    O país é uma escolha entre os países **ativos** — a lista nunca é escrita
    no template, e desligar a Alemanha no admin a tira daqui na mesma hora. Um
    endereço já salvo num país que a loja parou de atender continua visível na
    conta (o cliente não perde o cadastro), mas o checkout o recusa.
    """

    class Meta:
        model = CustomerAddress
        fields = (
            "label",
            "first_name",
            "last_name",
            "company_name",
            "vat_number",
            "phone",
            "street",
            "street_extra",
            "postal_code",
            "city",
            "region",
            "country",
        )
        widgets = {
            "label": forms.TextInput(
                attrs={**TEXT_INPUT, "placeholder": _("Casa, Trabalho, Presente da Marie...")}
            ),
            "first_name": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "family-name"}),
            "company_name": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "organization"}),
            "vat_number": forms.TextInput(attrs={**TEXT_INPUT, "placeholder": "BE0123456789"}),
            "phone": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "tel"}),
            "street": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "address-line1"}),
            "street_extra": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "address-line2"}),
            "postal_code": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "postal-code"}),
            "city": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "address-level2"}),
            "region": forms.TextInput(attrs={**TEXT_INPUT, "autocomplete": "address-level1"}),
            "country": forms.Select(attrs={"class": "form-input"}),
        }

    def __init__(self, *args, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer

        from apps.core.models import DeliveryCountry

        countries = DeliveryCountry.objects.for_checkout()
        self.fields["country"].queryset = countries
        self.fields["country"].empty_label = None
        if not self.instance.pk and countries:
            self.fields["country"].initial = countries.first()

        # Nome e sobrenome já foram preenchidos em "Meus dados": repetir a
        # digitação é atrito, não segurança.
        if not self.instance.pk and customer is not None:
            self.fields["first_name"].initial = customer.first_name
            self.fields["last_name"].initial = customer.last_name
            self.fields["phone"].initial = customer.phone

    def save(self, commit: bool = True):
        address = super().save(commit=False)
        if self.customer is not None:
            address.customer = self.customer
        if commit:
            address.save()
        return address
