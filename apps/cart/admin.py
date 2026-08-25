"""Arquivos de personalização enviados pelos clientes.

Somente leitura: o admin não deve criar nem trocar o arquivo de um cliente.
Serve para conferir o que foi enviado e para limpar órfãos.
"""

from django.contrib import admin
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils.html import format_html

from apps.cart.models import Cart, CartItem, CustomizationUpload


@admin.register(CustomizationUpload)
class CustomizationUploadAdmin(admin.ModelAdmin):
    list_display = ("original_name", "preview", "content_type", "size_display", "created_at")
    list_filter = ("content_type", "created_at")
    search_fields = ("original_name", "session_key")
    date_hierarchy = "created_at"
    readonly_fields = (
        "file",
        "original_name",
        "content_type",
        "extension",
        "size_bytes",
        "session_key",
        "created_at",
        "updated_at",
        "preview",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="prévia")
    def preview(self, obj):
        if not obj.file:
            return "—"
        return format_html(
            '<img src="{}" style="max-height:60px;border-radius:4px" />', obj.file.url
        )

    @admin.display(description="tamanho")
    def size_display(self, obj):
        return obj.size_display


# ---------------------------------------------------------------------------
# Carrinhos persistentes
# ---------------------------------------------------------------------------


class CartItemInline(admin.TabularInline):
    """Somente leitura: o carrinho é do cliente, não do administrador."""

    model = CartItem
    extra = 0
    can_delete = False
    fields = ("product", "variant", "quantity", "customization_type", "customization_text")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product", "variant")


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    """Carrinhos de clientes autenticados.

    É também a base do lembrete de carrinho abandonado: quem é o dono, o que
    tem dentro, quando nasceu e quando foi mexido pela última vez. O envio
    automático em si é assunto de outra etapa.
    """

    list_display = ("user", "line_count", "unit_count", "created_at", "updated_at")
    list_filter = ("updated_at", "created_at")
    search_fields = ("user__username", "user__email")
    date_hierarchy = "updated_at"
    ordering = ("-updated_at",)
    inlines = (CartItemInline,)
    readonly_fields = ("user", "customer_link", "created_at", "updated_at")
    fields = ("user", "customer_link", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .annotate(_lines=Count("items", distinct=True), _units=Sum("items__quantity"))
        )

    @admin.display(description="linhas", ordering="_lines")
    def line_count(self, obj):
        return obj._lines

    @admin.display(description="unidades", ordering="_units")
    def unit_count(self, obj):
        return obj._units or 0

    @admin.display(description="cliente")
    def customer_link(self, obj):
        customer = getattr(obj.user, "customer", None)
        if customer is None:
            return "—"
        url = reverse("admin:accounts_customer_change", args=[customer.pk])
        return format_html('<a href="{}">{}</a>', url, customer)
