"""Administração da Home.

A área "HOME" do painel reúne banners e seções. O formulário de seção mostra
apenas o que faz sentido para o tipo escolhido (ver
``static/admin/js/home_section_admin.js``).
"""

from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.core.admin_mixins import (
    PartialSafeModelForm,
    RequiredDefaultLanguageInlineFormSet,
    UniqueLanguageInlineFormSet,
)
from apps.core.constants import DEFAULT_LANGUAGE
from apps.core.colors import swatch
from apps.home.models import (
    HomeAbout,
    HomeAboutBadge,
    HomeAboutBadgeTranslation,
    HomeAboutTranslation,
    HomeBanner,
    HomeBannerCarousel,
    HomeBannerTranslation,
    HomeCallout,
    HomeCalloutTranslation,
    HomeCard,
    HomeCardTranslation,
    HomeCalloutStep,
    HomeCalloutStepTranslation,
    HomeCategoryCard,
    HomeCategoryCardTranslation,
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
    fields = (
        "language",
        "eyebrow", "title", "title_highlight", "subtitle",
        "cta_label", "cta_secondary_label",
        "perk_1", "perk_2", "perk_3",
        "badge_yellow", "badge_mint", "badge_white", "badge_coral",
        "colors_note", "rating_value", "rating_note",
        "image_alt", "image_tile_left_alt", "image_tile_right_alt",
    )
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma (todos os campos opcionais)"


@admin.register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    inlines = [HomeBannerTranslationInline]
    list_display = ("internal_name", "layout", "title_pt", "is_active", "sort_order", "has_image", "updated_at")
    list_filter = ("is_active", "layout")
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "-created_at")
    autocomplete_fields = ("cta_category", "cta_product")
    readonly_fields = ("created_at", "updated_at", "preview")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {"fields": ("internal_name", "layout", "is_active", "sort_order")},
        ),
        (
            "IMAGENS",
            {
                "fields": ("preview", "image_desktop", "image_mobile"),
                "description": (
                    "<b>Hero editorial:</b> a foto entra no quadro da composição, "
                    "ao lado do texto. Proporção <b>quadrada a 4:3</b> "
                    "(ex.: 1000 × 800 px) — o quadro continua aparecendo em volta "
                    "dela. Só a imagem de desktop é usada.<br><br>"
                    "<b>Imagem completa:</b> a arte ocupa o banner inteiro. "
                    "Desktop <b>1920 × 700 px</b> (2,74:1); fora dessa medida a "
                    "imagem é cortada pelo centro e o que estiver nas bordas se "
                    "perde. A de celular é opcional, em 4:5 (ex.: 900 × 1125 px)."
                    "<br><br>"
                    "<b>Poster Pop:</b> esta é a foto do <b>quadro do meio</b> da "
                    "base; os quadros laterais ficam no bloco abaixo. Quadrada "
                    "(ex.: 600 × 600 px).<br><br>"
                    "<b>Bento Criativo:</b> a foto do cartão menta, à direita do "
                    "texto. Proporção próxima de <b>quadrada</b>.<br><br>"
                    "Tudo é opcional: sem imagem, o quadro aparece vazio e o "
                    "layout não quebra."
                ),
            },
        ),
        (
            "QUADROS LATERAIS (só no Poster Pop)",
            {
                "classes": ("jd-poster",),
                "fields": ("image_tile_left", "image_tile_right"),
                "description": (
                    "Os dois quadros que acompanham a foto principal na base do "
                    "poster. Quadrados (ex.: 600 × 600 px). Sem foto, o quadro "
                    "aparece tracejado — a faixa de três é parte do desenho."
                ),
            },
        ),
        (
            "COMPOSIÇÃO (só no hero editorial)",
            {
                "classes": ("jd-composicao",),
                "fields": ("plate_color", "frame_color", "surface_color"),
                "description": (
                    "As cores da cena à direita: a placa inclinada atrás, o "
                    "quadro listrado da foto e o fundo do bloco. As opções são "
                    "as da identidade JD Print — não há campo de cor livre, para "
                    "que o topo da loja não saia da marca."
                ),
            },
        ),
        (
            "BOTÕES",
            {
                "fields": (
                    "cta_target", "cta_category", "cta_product", "cta_url",
                    "cta_secondary_url",
                ),
                "description": (
                    "O texto de cada botão é cadastrado por idioma, no bloco de "
                    "conteúdo abaixo. O segundo botão existe no hero editorial, "
                    "no Poster Pop e no Bento Criativo, e só aparece se tiver "
                    "texto <b>e</b> endereço."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    class Media:
        js = ("admin/js/home_cta_admin.js", "admin/js/home_banner_admin.js")

    @admin.display(description="imagem atual")
    def preview(self, obj):
        """A imagem como ela está hoje, ou a frase de que não há nenhuma."""
        if obj is None or not obj.image_desktop:
            return format_html(
                '<span style="color:var(--body-quiet-color)">{}</span>',
                "Nenhuma imagem enviada — o quadro aparece vazio no site.",
            )
        return format_html(
            '<div style="display:inline-flex;align-items:center;gap:14px;'
            'padding:12px 16px;border:1px solid var(--border-color);'
            'border-radius:10px;background:#efe8fa">'
            '<img src="{}" alt="" style="height:120px;width:auto;display:block;'
            'border-radius:8px">'
            "</div>",
            obj.image_desktop.url,
        )

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


@admin.register(HomeBannerCarousel)
class HomeBannerCarouselAdmin(admin.ModelAdmin):
    """Uma linha só — o Admin leva direto a ela.

    É a configuração GLOBAL do carrossel, e não uma opção por banner: quantos
    banners rodam é decidido ligando e desligando cada um em "Banners da
    Home"; aqui se decide como eles rodam.
    """

    save_on_top = True
    readonly_fields = ("created_at", "updated_at", "banners_ativos")
    fieldsets = (
        (
            "ROTAÇÃO",
            {
                "fields": ("banners_ativos", "autoplay", "interval_seconds"),
                "description": (
                    "Com <b>um</b> banner ativo nada disto aparece: ele é mostrado "
                    "como sempre, sem setas nem indicadores. Com <b>dois ou mais</b>, "
                    "os banners ativos viram slides, na ordem do campo "
                    "<i>ordem</i> de cada um."
                ),
            },
        ),
        (
            "CONTROLES",
            {
                "fields": ("show_arrows", "show_dots", "pause_on_hover", "pause_on_interaction"),
                "description": (
                    "Setas e indicadores usam o desenho da marca (círculos brancos "
                    "com contorno navy e a bolinha cheia no slide atual). O teclado "
                    "(← →) e o deslize no celular funcionam sempre."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="banners ativos hoje")
    def banners_ativos(self, obj):
        total = HomeBanner.objects.filter(is_active=True).count()
        if total < 2:
            return format_html(
                "{} — <span style='color:var(--body-quiet-color)'>o carrossel só entra em cena com dois ou mais.</span>",
                total,
            )
        return str(total)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse

        carrossel = HomeBannerCarousel.load()
        return redirect(reverse("admin:home_homebannercarousel_change", args=[carrossel.pk]))


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
        (
            "CORES",
            {
                "fields": (
                    "surface_color", "eyebrow_color", "title_color", "text_color",
                    "cta_bg_color", "cta_text_color", "step_bg_color",
                ),
                "description": (
                    "Todo texto é conferido contra o fundo escolhido ao salvar: "
                    "uma combinação abaixo do mínimo legível é recusada, com o "
                    "número obtido na mensagem."
                ),
            },
        ),
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


# ---------------------------------------------------------------------------
# Blocos de categoria
# ---------------------------------------------------------------------------


class HomeCategoryCardTranslationInline(admin.StackedInline):
    model = HomeCategoryCardTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "eyebrow", "title", "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma (todos opcionais)"


@admin.register(HomeCategoryCard)
class HomeCategoryCardAdmin(admin.ModelAdmin):
    inlines = [HomeCategoryCardTranslationInline]
    form = PartialSafeModelForm
    list_display = (
        "internal_name", "title_pt", "category", "cores", "is_active", "sort_order",
    )
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "id")
    autocomplete_fields = ("category",)
    readonly_fields = ("created_at", "updated_at", "preview")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": ("internal_name", "category", "is_active", "sort_order"),
                "description": (
                    "Os blocos coloridos logo abaixo do banner. O destino é uma "
                    "categoria do catálogo — assim o link continua válido se o "
                    "endereço dela mudar. Sem categoria, o bloco aparece sem link."
                ),
            },
        ),
        (
            "IMAGEM OU ÍCONE",
            {
                "fields": ("preview", "icon", "image"),
                "description": (
                    "Opcionais, e só um dos dois aparece: a linha de cima (quando "
                    "cadastrada) tem prioridade, depois a imagem, depois o ícone."
                ),
            },
        ),
        (
            "CORES",
            {
                "fields": ("bg_color", "text_color", "accent_color"),
                "description": (
                    "Cada bloco tem a sua. O contraste entre texto e fundo é "
                    "conferido ao salvar: uma combinação ilegível é recusada."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category").prefetch_related(
            "translations"
        )

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"

    @admin.display(description="cores")
    def cores(self, obj):
        return format_html(
            "{} {}", mark_safe(swatch(obj.bg_color)), mark_safe(swatch(obj.text_color))
        )

    @admin.display(description="imagem atual")
    def preview(self, obj):
        if obj is None or not obj.image:
            return format_html(
                '<span style="color:var(--body-quiet-color)">{}</span>',
                "Nenhuma imagem enviada.",
            )
        return format_html(
            '<img src="{}" alt="" style="height:90px;width:auto;border-radius:10px;'
            'border:1px solid var(--border-color)">',
            obj.image.url,
        )


# ---------------------------------------------------------------------------
# Os passos da chamada final
# ---------------------------------------------------------------------------


class HomeCalloutStepTranslationInline(admin.StackedInline):
    model = HomeCalloutStepTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "title", "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título e descrição por idioma"


@admin.register(HomeCalloutStep)
class HomeCalloutStepAdmin(admin.ModelAdmin):
    inlines = [HomeCalloutStepTranslationInline]
    form = PartialSafeModelForm
    list_display = ("internal_name", "title_pt", "cor", "is_active", "sort_order")
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    ordering = ("sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": ("callout", "internal_name", "is_active", "sort_order"),
                "description": (
                    "Os blocos numerados ao lado da chamada final. O número é a "
                    "POSIÇÃO na ordem, não um campo: desativar o segundo passo "
                    "renumera os outros em vez de deixar um buraco."
                ),
            },
        ),
        ("APARÊNCIA", {"fields": ("icon", "accent_color")}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"

    @admin.display(description="cor do número")
    def cor(self, obj):
        return mark_safe(swatch(obj.accent_color))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Há uma chamada só — o campo já vem preenchido com ela."""
        if db_field.name == "callout":
            kwargs["initial"] = HomeCallout.load().pk
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------------------------------------------------------------------------
# Sobre a loja
# ---------------------------------------------------------------------------


class HomeAboutTranslationInline(admin.StackedInline):
    model = HomeAboutTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "eyebrow", "title", "text", "image_alt")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma"


class HomeAboutBadgeTranslationInline(admin.StackedInline):
    model = HomeAboutBadgeTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "text")
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO por idioma"


@admin.register(HomeAboutBadge)
class HomeAboutBadgeAdmin(admin.ModelAdmin):
    inlines = [HomeAboutBadgeTranslationInline]
    form = PartialSafeModelForm
    list_display = ("internal_name", "text_pt", "cores", "is_active", "sort_order")
    list_display_links = ("internal_name", "text_pt")
    list_editable = ("is_active", "sort_order")
    ordering = ("sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": ("about", "internal_name", "is_active", "sort_order"),
                "description": "As pílulas coloridas abaixo do texto institucional.",
            },
        ),
        (
            "CORES",
            {
                "fields": ("bg_color", "text_color"),
                "description": "O contraste é conferido ao salvar.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="texto (pt)")
    def text_pt(self, obj):
        return obj.tr("text", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"

    @admin.display(description="cores")
    def cores(self, obj):
        return format_html(
            "{} {}", mark_safe(swatch(obj.bg_color)), mark_safe(swatch(obj.text_color))
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "about":
            kwargs["initial"] = HomeAbout.load().pk
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(HomeAbout)
class HomeAboutAdmin(admin.ModelAdmin):
    """Uma linha só — o Admin leva direto a ela."""

    inlines = [HomeAboutTranslationInline]
    form = PartialSafeModelForm
    save_on_top = True
    readonly_fields = ("created_at", "updated_at", "preview")
    fieldsets = (
        (
            "EXIBIÇÃO",
            {
                "fields": ("is_active",),
                "description": (
                    "O bloco com o quadro de imagem e o texto sobre a loja. "
                    "Desmarcado, ele não aparece na Home — e não deixa espaço "
                    "vazio no lugar."
                ),
            },
        ),
        (
            "IMAGEM",
            {
                "fields": ("preview", "image"),
                "description": (
                    "Entra <b>dentro</b> do quadro, que continua visível em volta "
                    "dela. Proporção <b>4:3</b> (ex.: 1000 × 750 px). O texto "
                    "alternativo é cadastrado por idioma, abaixo."
                ),
            },
        ),
        (
            "CORES",
            {
                "fields": ("frame_color", "surface_color", "text_color"),
                "description": "O quadro listrado, o fundo do bloco e a tinta do texto.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse

        sobre = HomeAbout.load()
        return redirect(reverse("admin:home_homeabout_change", args=[sobre.pk]))

    @admin.display(description="imagem atual")
    def preview(self, obj):
        if obj is None or not obj.image:
            return format_html(
                '<span style="color:var(--body-quiet-color)">{}</span>',
                "Nenhuma imagem enviada — o quadro aparece vazio no site.",
            )
        return format_html(
            '<div style="display:inline-block;padding:12px;border-radius:12px;'
            'background:#fff3d6">'
            '<img src="{}" alt="" style="height:130px;width:auto;display:block;'
            'border-radius:8px"></div>',
            obj.image.url,
        )
