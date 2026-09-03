"""Administração da Home.

A área "HOME" do painel reúne banners e seções. O formulário de seção mostra
apenas o que faz sentido para o tipo escolhido (ver
``static/admin/js/home_section_admin.js``).
"""

from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html

from apps.core.admin_mixins import (
    PartialSafeModelForm,
    RequiredDefaultLanguageInlineFormSet,
    UniqueLanguageInlineFormSet,
)
from apps.core.constants import DEFAULT_LANGUAGE
from apps.home.models import (
    HomeBanner,
    HomeBannerTranslation,
    HomeCallout,
    HomeCalloutTranslation,
    HomeCard,
    HomeCardTranslation,
    HomeSection,
    HomeSectionProduct,
    HomeSectionTranslation,
    HomeSectionType,
)

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


class HomeBannerTranslationInline(admin.StackedInline):
    """O conteúdo do banner, por idioma — e tudo opcional.

    Sem `min_num`: um banner que é só arte não tem título em idioma nenhum, e
    exigir uma linha de tradução vazia seria burocracia sem leitor. O
    `UniqueLanguageInlineFormSet` continua impedindo dois francês.
    """

    model = HomeBannerTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "title", "subtitle", "cta_label", "image_alt")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título, subtítulo e texto do botão por idioma (opcionais)"


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
                    "<b>Desktop: 1920 × 700 px</b> — proporção <b>2,74:1</b>. "
                    "Fora dessa medida a imagem é cortada pelo centro para caber "
                    "na faixa, e o que estiver nas bordas se perde.<br>"
                    "Mobile: proporção 4:5 (ex.: 900 × 1125 px).<br>"
                    "As duas são opcionais: sem imagem, a Home exibe um destaque "
                    "tipográfico com a identidade da marca — o layout não quebra."
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

    @admin.action(permissions=["change"], description="Ativar banners selecionados")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} banner(s) ativado(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Desativar banners selecionados")
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

    @admin.action(permissions=["change"], description="Ativar seções selecionadas")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} seção(ões) ativada(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Desativar seções selecionadas")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} seção(ões) desativada(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Duplicar seções selecionadas (como rascunho inativo)")
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


# ---------------------------------------------------------------------------
# Cards "como trabalhamos"
# ---------------------------------------------------------------------------


class HomeCardTranslationInline(admin.StackedInline):
    model = HomeCardTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "title", "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título e texto por idioma"


@admin.register(HomeCard)
class HomeCardAdmin(admin.ModelAdmin):
    inlines = [HomeCardTranslationInline]
    form = PartialSafeModelForm
    list_display = ("internal_name", "title_pt", "icon", "accent", "is_active", "sort_order")
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "accent")
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": ("internal_name", "is_active", "sort_order"),
                "description": (
                    "Os cards com ícone abaixo das faixas de produtos. "
                    "A quantidade não é fixa: a grade tem três colunas, e um quarto "
                    "card começa a segunda linha."
                ),
            },
        ),
        ("APARÊNCIA", {"fields": ("icon", "accent")}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, default="—")

    @admin.action(permissions=["change"], description="Ativar cards selecionados")
    def action_activate(self, request, queryset):
        total = queryset.update(is_active=True)
        self.message_user(request, f"{total} card(s) ativado(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Desativar cards selecionados")
    def action_deactivate(self, request, queryset):
        total = queryset.update(is_active=False)
        self.message_user(request, f"{total} card(s) desativado(s).", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Chamada final
# ---------------------------------------------------------------------------


class HomeCalloutTranslationInline(admin.StackedInline):
    model = HomeCalloutTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "eyebrow", "title", "text", "cta_label")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — sobretítulo, título, texto e botão por idioma"


@admin.register(HomeCallout)
class HomeCalloutAdmin(admin.ModelAdmin):
    """Uma linha só — o Admin leva direto a ela."""

    inlines = [HomeCalloutTranslationInline]
    save_on_top = True
    autocomplete_fields = ("cta_category", "cta_product")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "EXIBIÇÃO",
            {
                "fields": ("is_active",),
                "description": (
                    "A faixa escura no fim da Home. Sem título, sem texto e sem "
                    "botão ela não é desenhada — nunca vira uma faixa vazia."
                ),
            },
        ),
        ("BOTÃO (CTA)", {"fields": ("cta_target", "cta_category", "cta_product", "cta_url")}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    class Media:
        js = ("admin/js/home_cta_admin.js",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse

        callout = HomeCallout.load()
        return redirect(reverse("admin:home_homecallout_change", args=[callout.pk]))
