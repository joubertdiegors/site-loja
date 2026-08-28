"""Admin de contas e clientes.

Dois cadastros separados de propósito, espelhando a arquitetura:

* **Usuários** — acesso: username, e-mail, confirmação, idioma, permissões;
* **Clientes** — dados comerciais: nome, telefone, empresa, NIF/VAT.

A senha nunca aparece: o Django mostra apenas o resumo do hash (algoritmo, sal
mascarado) e um link para trocá-la. O hash em si não é reversível e não é
editável por aqui.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import (
    AdminPasswordChangeForm,
    AdminUserCreationForm as DjangoAdminUserCreationForm,
    UserChangeForm,
)
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.emails import send_verification_email
from apps.accounts.models import Customer, CustomerAddress, Favorite, User


class AdminUserCreationForm(DjangoAdminUserCreationForm):
    """Criação pelo admin: mesma tela do Django, com o e-mail obrigatório."""

    class Meta(DjangoAdminUserCreationForm.Meta):
        model = User
        fields = ("username", "email")


class AdminUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mesma lista de idiomas oferecida na loja (core.SiteLanguage), sem
        # congelar nada dentro da migration.
        from apps.core.languages import active_languages

        self.fields["preferred_language"] = forms.ChoiceField(
            label="Idioma preferido",
            choices=[(language.code, language.native_name) for language in active_languages()],
            required=False,
            help_text="Idioma dos e-mails enviados para este cliente.",
        )


class CustomerInline(admin.StackedInline):
    """Dados comerciais na tela do usuário — separados dos de autenticação."""

    model = Customer
    can_delete = False
    extra = 0
    verbose_name = "dados comerciais"
    verbose_name_plural = "dados comerciais (cliente)"
    fields = (("first_name", "last_name"), "phone", ("company_name", "vat_number"))


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = AdminUserChangeForm
    add_form = AdminUserCreationForm
    change_password_form = AdminPasswordChangeForm
    inlines = (CustomerInline,)

    list_display = (
        "username",
        "email",
        "is_active",
        "email_verified",
        "preferred_language",
        "date_joined",
        "last_login",
    )
    list_filter = ("email_verified", "is_active", "is_staff", "is_superuser", "preferred_language")
    search_fields = ("username", "email")
    ordering = ("-date_joined",)
    date_hierarchy = "date_joined"
    readonly_fields = ("date_joined", "last_login", "email_verified_at", "verification_sent_at")
    actions = ("action_send_verification", "action_mark_verified")

    fieldsets = (
        ("ACESSO", {"fields": ("username", "password")}),
        (
            "E-MAIL",
            {
                "fields": ("email", "email_verified", "email_verified_at", "verification_sent_at"),
                "description": (
                    "A confirmação é feita pelo próprio cliente, pelo link enviado por e-mail. "
                    "Marcar manualmente só faz sentido em casos excepcionais."
                ),
            },
        ),
        ("PREFERÊNCIAS", {"fields": ("preferred_language",)}),
        (
            "PERMISSÕES",
            {
                "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
                "classes": ("collapse",),
            },
        ),
        ("DATAS", {"fields": ("date_joined", "last_login")}),
    )

    add_fieldsets = (
        (
            "NOVA CONTA",
            {
                "classes": ("wide",),
                "fields": ("username", "email", "usable_password", "password1", "password2"),
            },
        ),
    )

    @admin.action(description="Enviar e-mail de confirmação")
    def action_send_verification(self, request, queryset):
        sent = sum(1 for user in queryset if send_verification_email(user))
        self.message_user(request, f"{sent} e-mail(s) enviado(s).", messages.SUCCESS)

    @admin.action(description="Marcar e-mail como confirmado")
    def action_mark_verified(self, request, queryset):
        count = 0
        for user in queryset.filter(email_verified=False):
            user.mark_email_verified()
            count += 1
        self.message_user(request, f"{count} conta(s) confirmada(s).", messages.SUCCESS)


class CustomerAddressInline(admin.TabularInline):
    """Os endereços na própria ficha do cliente — é onde se procura por eles."""

    model = CustomerAddress
    extra = 0
    fields = (
        "label",
        "first_name",
        "last_name",
        "street",
        "postal_code",
        "city",
        "country",
        "is_default_shipping",
        "is_default_billing",
    )
    autocomplete_fields = ("country",)
    show_change_link = True


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """O cliente comercial. Nada aqui serve para autenticar.

    Login, senha e confirmação de e-mail ficam em **Contas › Usuários**: são a
    conta de acesso, não o cliente.
    """

    list_display = ("__str__", "account", "phone", "company_name", "vat_number", "created_at")
    inlines = (CustomerAddressInline,)
    list_filter = ("created_at",)
    search_fields = (
        "first_name",
        "last_name",
        "company_name",
        "vat_number",
        "phone",
        "user__username",
        "user__email",
    )
    autocomplete_fields = ("user",)
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at", "account")
    fieldsets = (
        (
            "CONTA DE ACESSO",
            {
                "fields": ("user", "account"),
                "description": (
                    "Username, senha e confirmação de e-mail ficam em Contas › Usuários. "
                    "Aqui ficam apenas os dados comerciais."
                ),
            },
        ),
        ("PESSOA", {"fields": (("first_name", "last_name"), "phone")}),
        (
            "EMPRESA",
            {
                "fields": ("company_name", "vat_number"),
                "description": "Preencher apenas para clientes com faturamento em nome de empresa.",
            },
        ),
        ("DATAS", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="usuário")
    def account(self, obj):
        if obj.user_id is None:
            return "—"
        url = reverse("admin:accounts_user_change", args=[obj.user_id])
        label = f"{obj.user.username} · {obj.user.email}"
        if not obj.user.email_verified:
            label += " (e-mail não confirmado)"
        return format_html('<a href="{}">{}</a>', url, label)



@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "customer",
        "country",
        "postal_code",
        "is_default_shipping",
        "is_default_billing",
    )
    list_filter = ("country", "is_default_shipping", "is_default_billing")
    search_fields = (
        "label",
        "first_name",
        "last_name",
        "street",
        "city",
        "postal_code",
        "customer__user__email",
        "customer__user__username",
    )
    autocomplete_fields = ("customer", "country")
    list_select_related = ("customer", "country")
    fieldsets = (
        (
            "QUEM RECEBE",
            {
                "fields": ("customer", "label", "first_name", "last_name", "phone"),
                "description": (
                    "Nome e sobrenome são de quem <strong>recebe</strong>, não "
                    "necessariamente do titular da conta — é o que permite enviar "
                    "como presente."
                ),
            },
        ),
        ("EMPRESA", {"fields": ("company_name", "vat_number")}),
        (
            "ENDEREÇO",
            {"fields": ("street", "street_extra", "postal_code", "city", "region", "country")},
        ),
        ("PADRÕES", {"fields": ("is_default_shipping", "is_default_billing")}),
    )


# ---------------------------------------------------------------------------
# Favoritos
# ---------------------------------------------------------------------------


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    """Consulta, não edição.

    Favorito é um gesto do cliente: quem decide o que está guardado é ele. A
    tela existe para responder "quais produtos as pessoas mais guardam?" — que
    é informação de vitrine, não algo para corrigir a mão.

    Por isso não há "adicionar" nem "alterar": criar um favorito por outra
    pessoa seria mexer na conta dela. Apagar continua possível, para o caso de
    uma conta pedir a remoção dos próprios dados.
    """

    list_display = ("product", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "user__username",
        "user__email",
        "product__sku",
        "product__translations__name",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_select_related = ("user", "product")
    readonly_fields = ("user", "product", "created_at", "updated_at")
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("product__translations")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
