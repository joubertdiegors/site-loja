"""Conta de acesso (``User``) e cliente da loja (``Customer``).

São duas coisas diferentes, de propósito:

* ``User``     — como a pessoa **entra** no sistema: username, e-mail, senha,
  confirmação de e-mail, idioma preferido, sessão, permissões.
* ``Customer`` — quem a pessoa **é comercialmente**: nome, telefone, empresa,
  NIF/VAT e, no futuro, endereços, faturas e histórico de pedidos.

Misturar os dois é o erro clássico: o cadastro de autenticação vira um formulário
gigante, o checkout precisa de dados que só existem no login, e uma futura conta
de empresa com vários acessos fica impossível. Aqui a relação é ``1 : 0..1``::

    User ──(OneToOne, opcional)──> Customer ──> CustomerAddress, Orders, ...

Sobre unicidade: ``username`` e ``email`` são únicos **sem diferenciar
maiúsculas**. Isso é garantido em três lugares — formulário (mensagem amigável),
``full_clean`` (admin, shell disciplinado) e uma ``UniqueConstraint`` funcional
sobre ``Lower(...)`` no banco, que é a barreira final: nenhuma importação,
``shell`` ou API futura consegue passar por cima dela.
"""

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.managers import UserManager
from apps.accounts.validators import (
    USERNAME_MAX_LENGTH,
    validate_phone,
    validate_username,
)
from apps.core.models import TimeStampedModel


def default_language() -> str:
    """Idioma padrão do usuário.

    Função (e não ``default=settings.LANGUAGE_CODE``) para o valor não ser
    congelado dentro da migration: trocar o idioma padrão da loja não pode
    gerar uma migration falsa.
    """
    return settings.LANGUAGE_CODE


class User(AbstractBaseUser, PermissionsMixin):
    """Conta de acesso. Nenhum dado comercial mora aqui."""

    username = models.CharField(
        "nome de usuário",
        max_length=USERNAME_MAX_LENGTH,
        unique=True,
        validators=[validate_username],
        help_text="Letras, números, hífen e sublinhado. Não pode ser um e-mail.",
        error_messages={"unique": _("Este nome de usuário não está disponível.")},
    )
    email = models.EmailField(
        "e-mail",
        max_length=254,
        unique=True,
        error_messages={"unique": _("Já existe uma conta com este e-mail.")},
    )

    is_active = models.BooleanField(
        "ativo",
        default=True,
        help_text="Desmarcado, o usuário não consegue entrar. Preferir isto a apagar a conta.",
    )
    is_staff = models.BooleanField(
        "acessa o admin", default=False, help_text="Permite entrar no painel administrativo."
    )

    email_verified = models.BooleanField(
        "e-mail confirmado",
        default=False,
        help_text="Fica marcado quando o cliente clica no link enviado por e-mail.",
    )
    email_verified_at = models.DateTimeField("confirmado em", null=True, blank=True)
    verification_sent_at = models.DateTimeField(
        "último envio de confirmação",
        null=True,
        blank=True,
        help_text="Usado para limitar reenvios.",
    )

    preferred_language = models.CharField(
        "idioma preferido",
        max_length=10,
        default=default_language,
        help_text="Idioma dos e-mails enviados para este cliente.",
    )

    date_joined = models.DateTimeField("cadastrado em", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["email"]  # pedidos pelo createsuperuser

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
        ordering = ("-date_joined",)
        constraints = [
            # A barreira final. ``unique=True`` acima já impede o duplicado
            # exato; estas duas impedem o duplicado por caixa, inclusive vindo
            # de shell, importação ou de uma API futura.
            models.UniqueConstraint(
                Lower("username"),
                name="accounts_user_username_ci_unique",
                violation_error_message="Já existe um usuário com este nome (maiúsculas não contam).",
            ),
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
                violation_error_message="Já existe uma conta com este e-mail (maiúsculas não contam).",
            ),
        ]

    def __str__(self) -> str:
        return self.username

    # -- normalização e validação -----------------------------------------

    def clean(self):
        super().clean()
        self.username = User.objects.normalize_username(self.username)
        self.email = User.objects.normalize_email(self.email)

    def validate_unique(self, exclude=None):
        """Duplicidade por caixa vira erro de campo, não erro de constraint.

        Sem isto, ``DIEGO@example.com`` passaria pela validação de campo e só
        seria barrado pela constraint — com uma mensagem genérica e longe do
        campo que causou o problema.
        """
        errors = {}
        try:
            super().validate_unique(exclude=exclude)
        except ValidationError as error:
            errors = error.update_error_dict(errors)

        exclude = exclude or set()
        others = User.objects.exclude(pk=self.pk) if self.pk else User.objects.all()

        if "username" not in exclude and self.username:
            if others.filter(username__iexact=self.username.strip()).exists():
                errors.setdefault("username", []).append(
                    ValidationError(
                        self._meta.get_field("username").error_messages["unique"],
                        code="unique",
                    )
                )
        if "email" not in exclude and self.email:
            if others.filter(email__iexact=self.email.strip()).exists():
                errors.setdefault("email", []).append(
                    ValidationError(
                        self._meta.get_field("email").error_messages["unique"],
                        code="unique",
                    )
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Normaliza sempre, inclusive quando o objeto não passou por um
        # formulário (shell, comando, importação).
        self.username = User.objects.normalize_username(self.username)
        self.email = User.objects.normalize_email(self.email)
        super().save(*args, **kwargs)

    # -- exibição ----------------------------------------------------------

    def get_full_name(self) -> str:
        """Nome comercial, se já foi informado; senão o username.

        O nome de verdade vive em ``Customer`` — este método existe porque o
        admin e alguns templates do Django o chamam.
        """
        customer = getattr(self, "customer", None)
        if customer is not None and customer.full_name:
            return customer.full_name
        return self.username

    def get_short_name(self) -> str:
        customer = getattr(self, "customer", None)
        if customer is not None and customer.first_name:
            return customer.first_name
        return self.username

    @property
    def display_name(self) -> str:
        return self.get_short_name()

    # -- confirmação de e-mail --------------------------------------------

    def mark_email_verified(self, commit: bool = True) -> None:
        self.email_verified = True
        self.email_verified_at = timezone.now()
        if commit:
            self.save(update_fields=["email_verified", "email_verified_at"])

    def set_email(self, email: str, commit: bool = True) -> bool:
        """Troca o e-mail e derruba a confirmação quando o endereço muda.

        Preparado para a tela futura de "alterar e-mail": manter
        ``email_verified=True`` depois de trocar o endereço seria afirmar algo
        que ninguém verificou. Devolve ``True`` quando o endereço mudou.
        """
        new_email = User.objects.normalize_email(email)
        if new_email == self.email:
            return False

        self.email = new_email
        self.email_verified = False
        self.email_verified_at = None
        self.verification_sent_at = None
        if commit:
            self.save(
                update_fields=[
                    "email",
                    "email_verified",
                    "email_verified_at",
                    "verification_sent_at",
                ]
            )
        return True

    def seconds_until_resend(self) -> int:
        """Quanto falta para poder pedir outro e-mail de confirmação."""
        if self.verification_sent_at is None:
            return 0
        interval = getattr(settings, "EMAIL_VERIFICATION_RESEND_INTERVAL", 120)
        elapsed = (timezone.now() - self.verification_sent_at).total_seconds()
        return max(0, int(interval - elapsed))

    def can_request_verification_email(self) -> bool:
        return not self.email_verified and self.seconds_until_resend() == 0


class Customer(TimeStampedModel):
    """O cliente da JD PRINT: dados comerciais, nunca dados de acesso.

    Campos de propósito poucos. Endereço, dados fiscais completos, preferências
    de comunicação e histórico entram quando o checkout existir — cada um com a
    sua tabela (``CustomerAddress``, ``Order``, ``Invoice``). Criar dezenas de
    colunas agora por antecipação só geraria campos vazios e migrations extras.

    Nasce junto com a conta, vazio: o cadastro público pede só username, e-mail
    e senha. É "Minha conta › Meus dados" que o preenche.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="conta de acesso",
        related_name="customer",
        on_delete=models.CASCADE,
    )

    first_name = models.CharField("nome", max_length=80, blank=True)
    last_name = models.CharField("sobrenome", max_length=80, blank=True)
    phone = models.CharField("telefone", max_length=25, blank=True, validators=[validate_phone])
    company_name = models.CharField("empresa", max_length=120, blank=True)
    vat_number = models.CharField(
        "NIF / VAT",
        max_length=20,
        blank=True,
        help_text="Número de IVA da empresa (ex.: BE0123456789).",
    )

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.full_name or self.user.username

    def clean(self):
        super().clean()
        self.vat_number = (self.vat_number or "").replace(" ", "").upper()

    def save(self, *args, **kwargs):
        self.vat_number = (self.vat_number or "").replace(" ", "").upper()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    @property
    def is_complete(self) -> bool:
        """Tem o mínimo para um pedido? (o checkout vai exigir bem mais)."""
        return bool(self.first_name and self.last_name)


# ---------------------------------------------------------------------------
# Endereços
# ---------------------------------------------------------------------------


class AddressQuerySet(models.QuerySet):
    def deliverable(self):
        """Endereços cujo país a loja ainda atende."""
        return self.filter(country__is_active=True)


class CustomerAddress(TimeStampedModel):
    """Um endereço do cliente.

    Tabela separada, e não colunas dentro de ``Customer``, por três motivos
    concretos: o cliente tem mais de um endereço (casa, trabalho, o presente
    da irmã), entrega e faturamento podem ser endereços diferentes no mesmo
    pedido, e um endereço precisa poder ser corrigido sem reescrever nada do
    cadastro comercial.

    Nome e sobrenome ficam aqui e **não** são lidos do ``Customer``: quem
    recebe pode não ser quem compra. É o que torna "enviar como presente"
    possível sem um segundo sistema de endereços.

    O pedido nunca aponta para esta linha — ele **copia** os dados
    (ver ``orders.OrderAddress``). Corrigir o endereço aqui não pode reescrever
    o histórico de uma entrega já feita.
    """

    customer = models.ForeignKey(
        Customer, verbose_name="cliente", related_name="addresses", on_delete=models.CASCADE
    )

    label = models.CharField(
        "identificação",
        max_length=40,
        blank=True,
        help_text='Como o cliente reconhece este endereço: "Casa", "Trabalho"...',
    )

    # Quem recebe. Separado do Customer de propósito (presente, empresa).
    first_name = models.CharField("nome", max_length=80)
    last_name = models.CharField("sobrenome", max_length=80)
    company_name = models.CharField("empresa", max_length=120, blank=True)
    vat_number = models.CharField(
        "NIF / VAT",
        max_length=20,
        blank=True,
        help_text="Só quando a fatura sai em nome de uma empresa.",
    )
    phone = models.CharField(
        "telefone",
        max_length=25,
        blank=True,
        validators=[validate_phone],
        help_text="A transportadora usa em caso de problema na entrega.",
    )

    # Onde.
    street = models.CharField("endereço", max_length=160)
    street_extra = models.CharField(
        "complemento", max_length=120, blank=True, help_text="Andar, caixa postal, ponto de referência."
    )
    postal_code = models.CharField("código postal", max_length=16)
    city = models.CharField("cidade", max_length=80)
    region = models.CharField("região / província", max_length=80, blank=True)
    country = models.ForeignKey(
        "core.DeliveryCountry",
        verbose_name="país",
        related_name="addresses",
        on_delete=models.PROTECT,
    )

    is_default_shipping = models.BooleanField("padrão para entrega", default=False)
    is_default_billing = models.BooleanField("padrão para faturamento", default=False)

    objects = AddressQuerySet.as_manager()

    class Meta:
        verbose_name = "endereço"
        verbose_name_plural = "endereços"
        ordering = ("-is_default_shipping", "-is_default_billing", "label", "pk")
        constraints = [
            # Um padrão por tipo e por cliente — garantido pelo banco, não pela
            # boa vontade de quem escrever a próxima view.
            models.UniqueConstraint(
                fields=("customer",),
                condition=models.Q(is_default_shipping=True),
                name="one_default_shipping_per_customer",
            ),
            models.UniqueConstraint(
                fields=("customer",),
                condition=models.Q(is_default_billing=True),
                name="one_default_billing_per_customer",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label or self.full_name} — {self.city}"

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    @property
    def display_label(self) -> str:
        return self.label or self.full_name or self.city

    def lines(self) -> list[str]:
        """O endereço como o correio o lê, sem linhas vazias."""
        parts = [
            self.full_name,
            self.company_name,
            self.street,
            self.street_extra,
            " ".join(part for part in (self.postal_code, self.city) if part).strip(),
            self.region,
            self.country.name if self.country_id else "",
        ]
        return [part for part in parts if part]

    def clean(self):
        super().clean()
        self.vat_number = (self.vat_number or "").replace(" ", "").upper()
        self.postal_code = (self.postal_code or "").strip().upper()

    def save(self, *args, **kwargs):
        self.vat_number = (self.vat_number or "").replace(" ", "").upper()
        self.postal_code = (self.postal_code or "").strip().upper()

        first = not self.customer.addresses.exclude(pk=self.pk).exists()
        if first:
            # O primeiro endereço é o padrão dos dois tipos: ninguém cadastra
            # um endereço para ele não ser usado.
            self.is_default_shipping = True
            self.is_default_billing = True

        super().save(*args, **kwargs)

        if self.is_default_shipping:
            self._clear_other_defaults("is_default_shipping")
        if self.is_default_billing:
            self._clear_other_defaults("is_default_billing")

    def _clear_other_defaults(self, field: str) -> None:
        (
            CustomerAddress.objects.filter(customer_id=self.customer_id, **{field: True})
            .exclude(pk=self.pk)
            .update(**{field: False})
        )

    def make_default(self, *, shipping: bool = False, billing: bool = False) -> None:
        fields = []
        if shipping:
            self.is_default_shipping = True
            fields.append("is_default_shipping")
        if billing:
            self.is_default_billing = True
            fields.append("is_default_billing")
        if not fields:
            return
        # Limpa antes de gravar: a constraint parcial do banco não aceita dois
        # padrões nem por um instante dentro da mesma transação.
        for field in fields:
            self._clear_other_defaults(field)
        self.save(update_fields=[*fields, "updated_at"])


class FavoriteQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(user=user)

    def visible(self):
        """Só o que o cliente poderia comprar hoje.

        O favorito **não** é apagado quando o produto sai do ar: se ele voltar,
        o cliente reencontra o que tinha guardado. O que muda é só o que a
        página mostra.
        """
        from apps.catalog.models import ProductStatus

        return self.filter(
            product__status=ProductStatus.ACTIVE, product__variants__is_active=True
        ).distinct()


class Favorite(TimeStampedModel):
    """Um produto que o cliente guardou.

    ## Do produto, nunca da variante

    O cliente favorita "o vaso", não "o vaso preto de 25 cm em PLA". Guardar a
    variante criaria três favoritos do mesmo produto e uma lista que se repete
    — e obrigaria a decidir o que fazer quando aquela variante saísse de linha.

    ## Do usuário, nunca da sessão

    Sem conta não há favorito: um "favorito anônimo" viveria numa sessão que
    expira, e o cliente perderia a lista sem entender por quê. O coração do
    visitante leva ao login, que é o mesmo login de sempre.

    ## Nunca apagado por indisponibilidade

    Produto desativado sai da **listagem** (`visible()`), não da tabela. Se ele
    voltar, o favorito continua lá. Apagar seria decidir pelo cliente que ele
    perdeu o interesse.

    `CASCADE` nos dois lados: sem a conta ou sem o produto, a linha não
    significa mais nada — e um favorito órfão apontando para um produto que não
    existe seria um erro esperando a próxima listagem.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="cliente",
        related_name="favorites",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        "catalog.Product",
        verbose_name="produto",
        related_name="favorited_by",
        on_delete=models.CASCADE,
    )

    objects = FavoriteQuerySet.as_manager()

    class Meta:
        verbose_name = "favorito"
        verbose_name_plural = "favoritos"
        # Mais recente primeiro: é a ordem em que o cliente pensa na própria
        # lista. Ordem alfabética esconderia o que ele acabou de guardar.
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], name="favorite_unique_user_product"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="favorite_user_recent_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} ♥ {self.product}"
