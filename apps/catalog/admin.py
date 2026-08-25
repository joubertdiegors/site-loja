"""Configuração do Django Admin do catálogo.

O formulário de produto é organizado em seções (``fieldsets``) e usa dois
inlines: traduções e mídias.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from apps.catalog.models import (
    Brand,
    Color,
    Material,
    MediaType,
    Product,
    ProductMedia,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.core.admin_mixins import AuditUserAdminMixin, TranslatedSlugAdminMixin
from apps.core.constants import DEFAULT_LANGUAGE

# ---------------------------------------------------------------------------
# Atributos
# ---------------------------------------------------------------------------


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "website", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "description", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ("name", "swatch", "hex_code", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "hex_code")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="amostra")
    def swatch(self, obj):
        if not obj.hex_code:
            return "—"
        return format_html(
            '<span style="display:inline-block;width:22px;height:22px;border-radius:4px;'
            'border:1px solid #bbb;background:{}"></span>',
            obj.hex_code,
        )


# ---------------------------------------------------------------------------
# Inlines do produto
# ---------------------------------------------------------------------------


class ProductTranslationInlineFormSet(forms.BaseInlineFormSet):
    """Garante o conteúdo mínimo por idioma."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        languages = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            languages.append(form.cleaned_data.get("language"))

        if len(set(languages)) != len(languages):
            raise ValidationError("Há mais de uma tradução para o mesmo idioma.")

        product_is_active = self.instance.status == ProductStatus.ACTIVE
        if product_is_active and DEFAULT_LANGUAGE.value not in languages:
            raise ValidationError(
                "Um produto ativo precisa da tradução em português (nome do produto)."
            )


class ProductTranslationInline(admin.StackedInline):
    model = ProductTranslation
    formset = ProductTranslationInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "name", "short_description", "description", "extra_information")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — nome e descrições por idioma"


class ProductVariantInline(admin.TabularInline):
    """Variantes do produto: cor, tamanho e material com preço e estoque.

    Campo vazio herda do produto — só se preenche o que difere. O estoque é
    sempre da variante: é exatamente o número que não pode ser compartilhado.
    """

    model = ProductVariant
    extra = 0
    fields = (
        "sort_order",
        "sku",
        "color",
        "size",
        "material",
        "sale_price",
        "stock_quantity",
        "weight_grams",
        "is_active",
    )
    autocomplete_fields = ("color", "material")
    ordering = ("sort_order", "id")
    verbose_name = "variante"
    verbose_name_plural = "VARIANTES — deixe vazio o que for igual ao produto"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("color", "material")


class ProductMediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1
    fields = ("preview", "file", "media_type", "alt_text", "sort_order", "is_primary")
    readonly_fields = ("preview",)
    verbose_name = "mídia"
    verbose_name_plural = "MÍDIA — fotos, vídeos e GIFs"

    @admin.display(description="prévia")
    def preview(self, obj):
        if not obj.pk or not obj.file:
            return "—"
        if obj.media_type in {MediaType.IMAGE, MediaType.GIF}:
            return format_html(
                '<img src="{}" style="max-height:70px;border-radius:4px" />', obj.file.url
            )
        return format_html('<a href="{}" target="_blank">abrir vídeo</a>', obj.file.url)


# ---------------------------------------------------------------------------
# Produto
# ---------------------------------------------------------------------------


@admin.register(Product)
class ProductAdmin(TranslatedSlugAdminMixin, AuditUserAdminMixin):
    inlines = [ProductTranslationInline, ProductVariantInline, ProductMediaInline]
    save_on_top = True

    list_display = (
        "sku",
        "display_name",
        "category",
        "status_badge",
        "price_display",
        "total_cost_display",
        "margin_display",
        "stock_display",
        "variant_count",
        "personalization_badge",
        "is_featured",
        "updated_at",
    )
    list_filter = (
        "status",
        "personalization_type",
        "is_featured",
        "made_to_order",
        "category",
        "brand",
        "materials",
        "currency",
    )
    search_fields = ("sku", "slug", "translations__name", "translations__short_description")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 30
    autocomplete_fields = ("category", "brand")
    filter_horizontal = ("materials", "colors")
    actions = ("action_activate", "action_deactivate", "action_feature", "action_unfeature")

    readonly_fields = (
        "total_cost_display",
        "effective_margin_display",
        "profit_display",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )

    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": ("sku", "status", "slug"),
                "description": (
                    "O conteúdo traduzível (nome e descrições) fica na seção "
                    "<b>CONTEÚDO</b>, no final da página."
                ),
            },
        ),
        ("CLASSIFICAÇÃO", {"fields": ("category", "brand")}),
        (
            "CARACTERÍSTICAS",
            {
                "fields": (
                    ("width", "height", "depth"),
                    "dimension_unit",
                    "weight_grams",
                    "materials",
                    "colors",
                )
            },
        ),
        (
            "PRODUÇÃO",
            {
                "fields": (
                    "print_time",
                    "currency",
                    "filament_cost",
                    "energy_cost",
                    "total_cost_display",
                )
            },
        ),
        (
            "PREÇO",
            {
                "fields": (
                    "pricing_mode",
                    "sale_price",
                    "profit_margin",
                    "effective_margin_display",
                    "profit_display",
                ),
                "description": (
                    "Escolha em <b>definir preço por</b> qual valor você digita. "
                    "O outro é calculado automaticamente ao salvar."
                ),
            },
        ),
        (
            "ESTOQUE",
            {
                "fields": (
                    "stock_quantity",
                    "allow_backorder",
                    "made_to_order",
                    "production_lead_time_days",
                )
            },
        ),
        (
            "PERSONALIZAÇÃO",
            {
                "fields": ("personalization_type", "personalization_text_limit"),
                "description": (
                    "Define se o cliente deverá fornecer uma foto, um texto ou escolher "
                    "entre os dois antes de adicionar o produto ao carrinho. "
                    "É característica do produto — não crie categoria para isso."
                ),
            },
        ),
        ("CONFIGURAÇÕES", {"fields": ("is_featured", "featured_order")}),
        (
            "AUDITORIA",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "created_by", "updated_at", "updated_by"),
            },
        ),
    )

    class Media:
        css = {"all": ("admin/css/jdprint_admin.css",)}

    def get_queryset(self, request):
        return super().get_queryset(request).for_listing().prefetch_related("variants")

    # -- colunas calculadas ------------------------------------------------

    @admin.display(description="nome")
    def display_name(self, obj):
        return obj.name_in(DEFAULT_LANGUAGE.value)

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        colors = {
            ProductStatus.DRAFT: "#8a8a8a",
            ProductStatus.ACTIVE: "#1a7f37",
            ProductStatus.INACTIVE: "#b42318",
        }
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>',
            colors.get(obj.status, "#000"),
            obj.get_status_display(),
        )

    @admin.display(description="preço", ordering="sale_price")
    def price_display(self, obj):
        if obj.sale_price is None:
            return "—"
        return f"{obj.currency_symbol} {obj.sale_price}"

    @admin.display(description="custo", ordering="total_cost")
    def total_cost_display(self, obj):
        return f"{obj.currency_symbol} {obj.total_cost}"

    @admin.display(description="margem", ordering="profit_margin")
    def margin_display(self, obj):
        if obj.profit_margin is None:
            return "—"
        return f"{obj.profit_margin}%"

    @admin.display(description="margem real do preço gravado")
    def effective_margin_display(self, obj):
        margin = obj.effective_margin
        return "—" if margin is None else f"{margin}%"

    @admin.display(description="lucro por unidade")
    def profit_display(self, obj):
        value = obj.profit
        return "—" if value is None else f"{obj.currency_symbol} {value}"

    @admin.display(description="variantes")
    def variant_count(self, obj):
        total = obj.variants.count()
        return total or "—"

    @admin.display(description="personalização", ordering="personalization_type")
    def personalization_badge(self, obj):
        if not obj.needs_personalization:
            return "—"
        return obj.get_personalization_type_display()

    @admin.display(description="estoque", ordering="stock_quantity")
    def stock_display(self, obj):
        if obj.made_to_order:
            days = obj.production_lead_time_days
            return f"sob encomenda ({days} dias)" if days else "sob encomenda"
        return obj.stock_quantity

    # -- ações -------------------------------------------------------------

    @admin.action(description="Ativar produtos selecionados")
    def action_activate(self, request, queryset):
        activated = 0
        for product in queryset:
            product.status = ProductStatus.ACTIVE
            try:
                product.full_clean()
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{product.sku}: {'; '.join(m for msgs in error.message_dict.values() for m in msgs)}",
                    level=messages.ERROR,
                )
                continue
            product.save()
            activated += 1
        if activated:
            self.message_user(request, f"{activated} produto(s) ativado(s).", messages.SUCCESS)

    @admin.action(description="Desativar produtos selecionados")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(status=ProductStatus.INACTIVE)
        self.message_user(request, f"{updated} produto(s) desativado(s).", messages.SUCCESS)

    @admin.action(description="Marcar como destaque")
    def action_feature(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"{updated} produto(s) em destaque.", messages.SUCCESS)

    @admin.action(description="Remover do destaque")
    def action_unfeature(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"{updated} produto(s) fora do destaque.", messages.SUCCESS)
