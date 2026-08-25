"""Administração da Home.

A área "HOME" do painel reúne banners e seções. O formulário de seção mostra
apenas o que faz sentido para o tipo escolhido (ver
``static/admin/js/home_section_admin.js``).
"""

from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html

from apps.core.admin_mixins import PartialSafeModelForm, RequiredDefaultLanguageInlineFormSet
from apps.core.constants import DEFAULT_LANGUAGE
from apps.home.models import (
    HomeBanner,
    HomeBannerTranslation,
    HomeSection,
    HomeSectionProduct,
    HomeSectionTranslation,
    HomeSectionType,
)

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


class HomeBannerTranslationInline(admin.StackedInline):
    model = HomeBannerTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "title", "subtitle", "cta_label", "image_alt")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título e subtítulo por idioma"


@admin.register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    inlines = [HomeBannerTranslationInline]
    list_display = ("internal_name", "title_pt", "is_active", "sort_order", "has_image", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "-created_at")
    autocomplete_fields = ("cta_category", "cta_product")
    readonly_fields = ("created_at", "updated_at")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        ("IDENTIFICAÇÃO", {"fields": ("internal_name", "is_active", "sort_order")}),
        (
            "IMAGENS",
            {
                "fields": ("image_desktop", "image_mobile"),
                "description": (
                    "Opcionais. Sem imagem, a Home exibe um destaque tipográfico "
                    "com a identidade da marca — o layout não quebra."
                ),
            },
        ),
        (
            "BOTÃO (CTA)",
            {"fields": ("cta_target", "cta_category", "cta_product", "cta_url")},
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    class Media:
        js = ("admin/js/home_cta_admin.js",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, default="—")

    @admin.display(description="imagem", boolean=True)
    def has_image(self, obj):
        return bool(obj.image_desktop)

    @admin.action(description="Ativar banners selecionados")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} banner(s) ativado(s).", messages.SUCCESS)

    @admin.action(description="Desativar banners selecionados")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} banner(s) desativado(s).", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Seções
# ---------------------------------------------------------------------------


class HomeSectionTranslationInline(admin.StackedInline):
    model = HomeSectionTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "title", "subtitle", "cta_label")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título, subtítulo e texto do botão por idioma"


class HomeSectionProductInline(admin.TabularInline):
    model = HomeSectionProduct
    extra = 1
    fields = ("sort_order", "product")
    autocomplete_fields = ("product",)
    ordering = ("sort_order", "id")
    verbose_name = "produto escolhido"
    verbose_name_plural = "PRODUTOS — usados apenas no tipo 'Produtos escolhidos manualmente'"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product").prefetch_related(
            "product__translations"
        )


@admin.register(HomeSection)
class HomeSectionAdmin(admin.ModelAdmin):
    inlines = [HomeSectionTranslationInline, HomeSectionProductInline]
    save_on_top = True

    list_display = (
        "internal_name",
        "title_pt",
        "type_badge",
        "layout",
        "product_limit",
        "manual_products_count",
        "is_active",
        "sort_order",
    )
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "section_type", "layout", "category")
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "id")
    autocomplete_fields = ("category", "cta_category", "cta_product")
    readonly_fields = ("created_at", "updated_at")
    actions = ("action_activate", "action_deactivate", "action_duplicate")
    list_per_page = 40

    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": ("internal_name", "is_active", "sort_order"),
                "description": (
                    "O nome interno é só para você se organizar. O que o cliente vê "
                    "é o título, na seção <b>CONTEÚDO</b> no final da página."
                ),
            },
        ),
        (
            "CONTEÚDO DA SEÇÃO",
            {
                "fields": (
                    "section_type",
                    "layout",
                    "product_limit",
                    "category",
                    "include_subcategories",
                )
            },
        ),
        ("BOTÃO (CTA)", {"fields": ("cta_target", "cta_category", "cta_product", "cta_url")}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    class Media:
        js = ("admin/js/home_section_admin.js", "admin/js/home_cta_admin.js")
        css = {"all": ("admin/css/jdprint_admin.css",)}

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category")
            .prefetch_related("translations")
            .annotate(total_items=Count("items", distinct=True))
        )

    def get_changelist_form(self, request, **kwargs):
        # A edição em lote (ativar/ordenar) envia um formulário parcial.
        kwargs.setdefault("form", PartialSafeModelForm)
        return super().get_changelist_form(request, **kwargs)

    # -- colunas -----------------------------------------------------------

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, default="—")

    @admin.display(description="tipo", ordering="section_type")
    def type_badge(self, obj):
        label = obj.get_section_type_display()
        if not obj.has_data_source:
            return format_html(
                '<span title="Depende do módulo de pedidos" style="color:#b45309">{} ⚠</span>',
                label,
            )
        return label

    @admin.display(description="produtos manuais", ordering="total_items")
    def manual_products_count(self, obj):
        if not obj.uses_manual_products:
            return "—"
        return obj.total_items

    # -- ações -------------------------------------------------------------

    @admin.action(description="Ativar seções selecionadas")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} seção(ões) ativada(s).", messages.SUCCESS)

    @admin.action(description="Desativar seções selecionadas")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} seção(ões) desativada(s).", messages.SUCCESS)

    @admin.action(description="Duplicar seções selecionadas (como rascunho inativo)")
    def action_duplicate(self, request, queryset):
        created = 0
        for section in queryset.prefetch_related("translations", "items"):
            items = list(section.items.all())
            translations = list(section.translations.all())

            copy = section
            copy.pk = None
            copy._state.adding = True
            copy.internal_name = f"{section.internal_name} (cópia)"
            copy.is_active = False
            copy.save()

            for translation in translations:
                translation.pk = None
                translation._state.adding = True
                translation.master = copy
                translation.save()

            for item in items:
                item.pk = None
                item._state.adding = True
                item.section = copy
                item.save()

            created += 1

        self.message_user(
            request,
            f"{created} seção(ões) duplicada(s). As cópias começam desativadas.",
            messages.SUCCESS,
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.section_type == HomeSectionType.BEST_SELLERS:
            self.message_user(
                request,
                "A seção 'Mais vendidos' fica salva, mas só aparecerá na Home quando "
                "o módulo de pedidos existir — nenhum critério falso de vendas é usado.",
                messages.WARNING,
            )
