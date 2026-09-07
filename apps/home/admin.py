"""Administração da Home.

A área "HOME" do painel reúne banners e seções. O formulário de seção mostra
apenas o que faz sentido para o tipo escolhido (ver
``static/admin/js/home_section_admin.js``).
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, F, Window, prefetch_related_objects
from django.db.models.functions import RowNumber
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.core.admin_preview import LivePreviewMixin, Wrapped, ordered, replace_or_append
from apps.core.admin_mixins import (
    PartialSafeModelForm,
    RequiredDefaultLanguageInlineFormSet,
    UniqueLanguageInlineFormSet,
)
from apps.core.constants import DEFAULT_LANGUAGE
from apps.core.colors import swatch
from apps.home import services
from apps.home.services import ResolvedSection
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
    fieldsets = (
        (None, {"fields": (("language", "eyebrow", "title_highlight"), "title", "subtitle")}),
        ("Botões", {"fields": (("cta_label", "cta_secondary_label"),)}),
        ("Promessas e selos", {"fields": (("perk_1", "perk_2", "perk_3"), ("badge_yellow", "badge_mint", "badge_white", "badge_coral"))}),
        ("Bento Criativo", {"fields": (("colors_note", "rating_value", "rating_note"),)}),
        ("Textos alternativos das imagens", {"fields": (("image_alt", "image_tile_left_alt", "image_tile_right_alt"),)}),
    )
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma (todos os campos opcionais)"


@admin.register(HomeBanner)
class HomeBannerAdmin(LivePreviewMixin, admin.ModelAdmin):
    preview_component = "components/banner_carousel.html"
    preview_note = "O banner como abre a Home, no desenho escolhido. Uma imagem recém-escolhida aparece depois de salvar."
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
            {"fields": (("internal_name", "layout"), ("is_active", "sort_order"))},
        ),
        (
            "IMAGENS",
            {
                "fields": ("preview", ("image_desktop", "image_mobile")),
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
                "fields": (("image_tile_left", "image_tile_right"),),
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
                "fields": (("plate_color", "frame_color", "surface_color"),),
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
                    ("cta_target", "cta_category", "cta_product", "cta_url"),
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
        js = ("admin/js/jd_fields.js", "admin/js/home_cta_admin.js", "admin/js/home_banner_admin.js")

    def get_preview_context(self, request, instance):
        """O banner sozinho no quadro da Home — o componente de sempre."""
        return {
            "banner": instance,
            "banners": [instance],
            "banner_carousel": HomeBannerCarousel.current(),
        }

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
class HomeBannerCarouselAdmin(LivePreviewMixin, admin.ModelAdmin):
    """Uma linha só — o Admin leva direto a ela.

    É a configuração GLOBAL do carrossel, e não uma opção por banner: quantos
    banners rodam é decidido ligando e desligando cada um em "Banners da
    Home"; aqui se decide como eles rodam.
    """

    preview_component = "components/banner_carousel.html"
    preview_scripts = True
    preview_note = "Os banners ativos hoje, girando como na Home. Com um só, não há carrossel."
    save_on_top = True
    readonly_fields = ("created_at", "updated_at", "banners_ativos")
    fieldsets = (
        (
            "ROTAÇÃO",
            {
                "fields": (("autoplay", "interval_seconds"), "banners_ativos"),
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
                "fields": (("show_arrows", "show_dots"), ("pause_on_hover", "pause_on_interaction")),
                "description": (
                    "Setas e indicadores usam o desenho da marca (círculos brancos "
                    "com contorno navy e a bolinha cheia no slide atual). O teclado "
                    "(← →) e o deslize no celular funcionam sempre."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_preview_context(self, request, instance):
        banners = services.get_active_banners()
        return {"banner": banners[0] if banners else None, "banners": banners, "banner_carousel": instance}

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


class SectionTranslationFormSet(UniqueLanguageInlineFormSet):
    """O título em português é obrigatório nas faixas de produtos.

    Os blocos (categorias, como trabalhamos, chamada final, sobre a loja) têm
    o conteúdo no cadastro próprio; o título da seção só serve de rótulo
    acessível, e exigi-lo seria burocracia sem leitor.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if not self.instance.is_products_section:
            return
        languages = [
            form.cleaned_data.get("language")
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if DEFAULT_LANGUAGE.value not in languages:
            raise ValidationError("Uma faixa de produtos precisa do título em português.")


class HomeSectionTranslationInline(admin.StackedInline):
    model = HomeSectionTranslation
    formset = SectionTranslationFormSet
    extra = 0
    fields = (("language", "title"), ("subtitle", "cta_label"))
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título, subtítulo e texto do botão por idioma (faixas de produtos)"


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
class HomeSectionAdmin(LivePreviewMixin, admin.ModelAdmin):
    """A ordem da Home. Cada linha é uma seção; a lista é a página, de cima para baixo."""

    preview_component = "components/home_composition.html"
    preview_note = (
        "A seção como aparece na Home, com o conteúdo de hoje. A faixa de fundo "
        "(branca ou creme) depende da posição entre as outras seções."
    )
    inlines = [HomeSectionTranslationInline, HomeSectionProductInline]
    save_on_top = True

    list_display = (
        "posicao",
        "internal_name",
        "type_badge",
        "conteudo",
        "is_active",
        "sort_order",
    )
    list_display_links = ("internal_name",)
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "section_type")
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "id")
    autocomplete_fields = ("category", "cta_category", "cta_product")
    readonly_fields = ("created_at", "updated_at", "blocos_desta_secao")
    actions = ("action_activate", "action_deactivate", "action_duplicate")
    list_per_page = 60

    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("internal_name", "section_type"), ("is_active", "sort_order")),
                "description": (
                    "<b>Tipo</b> é o modelo do bloco; <b>nome interno</b> é esta instância "
                    "(ex.: <i>Categorias — Coleções</i>) — é o que distingue duas seções do "
                    "mesmo tipo na lista. A <b>ordem</b> é a posição na Home: menor valor "
                    "aparece primeiro. Uma mesma seção pode existir quantas vezes quiser."
                ),
            },
        ),
        (
            "CONTEÚDO DA SEÇÃO",
            {
                "classes": ("jd-produtos",),
                "fields": (("layout", "product_limit"), ("category", "include_subcategories")),
                "description": "Só para as faixas de produtos. O título do cliente fica em CONTEÚDO, no fim da página.",
            },
        ),
        (
            "CHAMADA FINAL",
            {
                "classes": ("jd-callout",),
                "fields": ("callout",),
                "description": (
                    "Qual texto esta seção mostra (o cadastro fica em «Chamada final»; os "
                    "passos são dele). Sem escolher, a seção mostra o texto padrão. "
                    "Duas seções podem usar o mesmo texto."
                ),
            },
        ),
        (
            "SOBRE A LOJA",
            {
                "classes": ("jd-about",),
                "fields": ("about",),
                "description": (
                    "Qual bloco esta seção mostra (o cadastro fica em «Sobre a loja»; as "
                    "pílulas são dele). Sem escolher, a seção não é desenhada."
                ),
            },
        ),
        (
            "BLOCOS DESTA SEÇÃO",
            {
                "classes": ("jd-blocos",),
                "fields": ("blocos_desta_secao",),
                "description": (
                    "Os blocos de categorias e os cards de «Como trabalhamos» pertencem a "
                    "uma seção: cada instância tem os seus. Uma seção nova de «Como "
                    "trabalhamos» sem cards mostra os três de fábrica até você cadastrar."
                ),
            },
        ),
        (
            "BOTÃO (CTA)",
            {"classes": ("jd-cta",), "fields": (("cta_target", "cta_category", "cta_product", "cta_url"),)},
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    class Media:
        js = ("admin/js/jd_fields.js", "admin/js/home_section_admin.js", "admin/js/home_cta_admin.js")
        css = {"all": ("admin/css/jdprint_admin.css",)}

    def get_preview_context(self, request, instance):
        """A seção resolvida pela mesma rotina da Home, com os mesmos prefetches."""
        prefetch_related_objects([instance], *services.section_prefetches())
        entry = services.resolve_section(instance)
        if entry is None or not entry.is_renderable:
            motivos = {
                HomeSectionType.CATEGORY_CARDS: "Esta seção ainda não tem blocos de categoria ativos — cadastre-os em «Blocos de categorias» depois de salvar.",
                HomeSectionType.ABOUT: "Escolha um bloco «Sobre a loja» com conteúdo: sem ele a seção não é desenhada.",
                HomeSectionType.CALLOUT: "O texto escolhido está desativado ou vazio: a seção não é desenhada.",
            }
            motivo = motivos.get(
                instance.section_type,
                "Esta seção não tem produtos para mostrar com a configuração atual — ela não seria desenhada na Home.",
            )
            return {"sections": [], "preview_empty": motivo}
        return {"sections": services._with_bands([entry])}

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category", "callout", "about")
            .prefetch_related("translations")
            .annotate(
                total_items=Count("items", distinct=True),
                total_blocks=Count("category_cards", distinct=True),
                total_cards=Count("cards", distinct=True),
                # A posição na Home: a numeração da lista inteira, na ordem em
                # que ela aparece — inclusive quando a tela está filtrada.
                posicao_na_home=Window(RowNumber(), order_by=[F("sort_order").asc(), F("id").asc()]),
            )
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in ("callout", "about"):
            kwargs["queryset"] = db_field.remote_field.model.objects.order_by("id")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # -- colunas -----------------------------------------------------------

    @admin.display(description="nº", ordering="sort_order")
    def posicao(self, obj):
        numero = getattr(obj, "posicao_na_home", None)
        texto = f"{numero:02d}" if numero else "—"
        if not obj.is_active:
            return format_html('<span style="color:var(--body-quiet-color)" title="inativa: não aparece">{}</span>', texto)
        return format_html("<strong>{}</strong>", texto)

    @admin.display(description="mostra")
    def conteudo(self, obj):
        """O que a instância mostra, numa frase: é o que separa duas seções iguais."""
        if obj.section_type == HomeSectionType.CATEGORY_CARDS:
            url = reverse("admin:home_homecategorycard_changelist") + f"?section__id__exact={obj.pk}"
            return format_html('<a href="{}">{} bloco(s) de categoria</a>', url, obj.total_blocks)
        if obj.section_type == HomeSectionType.HOW_WE_WORK:
            url = reverse("admin:home_homecard_changelist") + f"?section__id__exact={obj.pk}"
            if obj.total_cards:
                return format_html('<a href="{}">{} card(s)</a>', url, obj.total_cards)
            return format_html('<a href="{}">os 3 cards padrão</a>', url)
        if obj.section_type == HomeSectionType.CALLOUT:
            if obj.callout_id:
                url = reverse("admin:home_homecallout_change", args=[obj.callout_id])
                return format_html('<a href="{}">{}</a>', url, obj.callout)
            return "texto padrão"
        if obj.section_type == HomeSectionType.ABOUT:
            if obj.about_id:
                url = reverse("admin:home_homeabout_change", args=[obj.about_id])
                return format_html('<a href="{}">{}</a>', url, obj.about)
            return format_html('<span style="color:#b45309">sem conteúdo escolhido ⚠</span>')
        titulo = obj.tr("title", language=DEFAULT_LANGUAGE.value, default="")
        detalhe = f"{obj.get_layout_display().lower()}, até {obj.product_limit}"
        if obj.uses_manual_products:
            detalhe += f", {obj.total_items} escolhido(s)"
        if obj.uses_category and obj.category_id:
            detalhe += f", {obj.category}"
        return f"«{titulo}» — {detalhe}" if titulo else detalhe

    @admin.display(description="blocos")
    def blocos_desta_secao(self, obj):
        if not obj.pk:
            return "Salve a seção para cadastrar os blocos dela."
        if obj.section_type == HomeSectionType.CATEGORY_CARDS:
            lista = reverse("admin:home_homecategorycard_changelist") + f"?section__id__exact={obj.pk}"
            novo = reverse("admin:home_homecategorycard_add") + f"?section={obj.pk}"
            total = obj.category_cards.count()
            return format_html('{} bloco(s) — <a href="{}">ver</a> · <a href="{}">acrescentar bloco</a>', total, lista, novo)
        if obj.section_type == HomeSectionType.HOW_WE_WORK:
            lista = reverse("admin:home_homecard_changelist") + f"?section__id__exact={obj.pk}"
            novo = reverse("admin:home_homecard_add") + f"?section={obj.pk}"
            total = obj.cards.count()
            return format_html('{} card(s) — <a href="{}">ver</a> · <a href="{}">acrescentar card</a>', total, lista, novo)
        return "Este tipo não tem blocos próprios."

    def get_changelist_form(self, request, **kwargs):
        # A edição em lote (ativar/ordenar) envia um formulário parcial.
        kwargs.setdefault("form", PartialSafeModelForm)
        return super().get_changelist_form(request, **kwargs)

    @admin.display(description="tipo", ordering="section_type")
    def type_badge(self, obj):
        label = obj.get_section_type_display().split(" (")[0]
        if not obj.has_data_source:
            return format_html(
                '<span title="Depende do módulo de pedidos" style="color:#b45309">{} ⚠</span>',
                label,
            )
        return label

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
            f"{created} seção(ões) duplicada(s). As cópias começam desativadas; blocos de "
            "categoria e cards continuam na seção original — cadastre os da cópia.",
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
    fields = (("language", "title"), "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título e texto por idioma"


@admin.register(HomeCard)
class HomeCardAdmin(LivePreviewMixin, admin.ModelAdmin):
    preview_component = "components/home_composition.html"
    preview_note = "A seção «Como trabalhamos» inteira, com este card no lugar dele."
    inlines = [HomeCardTranslationInline]
    form = PartialSafeModelForm
    list_display = ("internal_name", "title_pt", "section", "icon", "accent", "is_active", "sort_order")
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("section", "is_active", "accent")
    search_fields = ("internal_name", "translations__title")
    ordering = ("section", "sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("section", "internal_name"), ("is_active", "sort_order")),
                "description": (
                    "Os cards com ícone de uma seção «Como trabalhamos». Cada seção tem "
                    "os seus. A quantidade não é fixa: a grade tem três colunas, e um "
                    "quarto card começa a segunda linha."
                ),
            },
        ),
        ("APARÊNCIA", {"fields": (("icon", "accent"),)}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_preview_context(self, request, instance):
        secao = instance.section
        if secao is None:
            return {"sections": [], "preview_empty": "Escolha a seção «Como trabalhamos» em que este card aparece."}
        cards = ordered(replace_or_append(list(secao.cards.prefetch_related("translations")), instance))
        entry = ResolvedSection(
            section=secao,
            kind=ResolvedSection.KIND_HOW_WE_WORK,
            cards=[c for c in cards if c.is_active or c is instance],
        )
        return {"sections": services._with_bands([entry])}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("section").prefetch_related("translations")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "section":
            kwargs["queryset"] = HomeSection.objects.filter(
                section_type=HomeSectionType.HOW_WE_WORK
            ).order_by("sort_order", "id")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

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
    fields = (("language", "eyebrow", "cta_label"), "title", "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — sobretítulo, título, texto e botão por idioma"


@admin.register(HomeCallout)
class HomeCalloutAdmin(LivePreviewMixin, admin.ModelAdmin):
    """O conteúdo das chamadas finais. A posição na Home é uma seção."""

    preview_component = "components/home_composition.html"
    preview_note = "A faixa como aparece na Home, com os passos cadastrados para este texto."
    inlines = [HomeCalloutTranslationInline]
    save_on_top = True
    autocomplete_fields = ("cta_category", "cta_product")
    readonly_fields = ("created_at", "updated_at", "usada_em")
    list_display = ("__str__", "title_pt", "usada_em", "is_active")
    fieldsets = (
        (
            "EXIBIÇÃO",
            {
                "fields": (("internal_name", "is_active"), "usada_em"),
                "description": (
                    "A faixa escura com botão e passos. Sem título, sem texto e sem "
                    "botão ela não é desenhada — nunca vira uma faixa vazia. Onde ela "
                    "aparece na Home é uma seção do tipo «Chamada final», em Seções da Home."
                ),
            },
        ),
        ("BOTÃO (CTA)", {"fields": (("cta_target", "cta_category", "cta_product", "cta_url"),)}),
        (
            "CORES",
            {
                "fields": (
                    ("surface_color", "eyebrow_color", "title_color", "text_color"),
                    ("cta_bg_color", "cta_text_color", "step_bg_color"),
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
        js = ("admin/js/jd_fields.js", "admin/js/home_cta_admin.js")

    def get_preview_context(self, request, instance):
        if not instance.has_content:
            return {"sections": [], "preview_empty": "Sem título, texto, botão ou passos, a chamada não é desenhada na Home."}
        entry = ResolvedSection(section=None, kind=ResolvedSection.KIND_CALLOUT, callout=instance)
        return {"sections": services._with_bands([entry])}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations", "sections")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"

    @admin.display(description="usada nas seções")
    def usada_em(self, obj):
        if not obj.pk:
            return "—"
        nomes = [s.internal_name for s in obj.sections.all()]
        return ", ".join(nomes) if nomes else "nenhuma seção usa este texto ainda"


# ---------------------------------------------------------------------------
# Blocos de categoria
# ---------------------------------------------------------------------------


class HomeCategoryCardTranslationInline(admin.StackedInline):
    model = HomeCategoryCardTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = (("language", "eyebrow"), ("title", "text"))
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma (todos opcionais)"


@admin.register(HomeCategoryCard)
class HomeCategoryCardAdmin(LivePreviewMixin, admin.ModelAdmin):
    preview_component = "components/home_composition.html"
    preview_note = "A seção de categorias inteira, com este bloco no lugar dele. Uma imagem nova aparece depois de salvar."
    inlines = [HomeCategoryCardTranslationInline]
    form = PartialSafeModelForm
    list_display = (
        "internal_name", "title_pt", "section", "category", "cores", "is_active", "sort_order",
    )
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("section", "is_active")
    search_fields = ("internal_name", "translations__title")
    ordering = ("section", "sort_order", "id")
    autocomplete_fields = ("category",)
    readonly_fields = ("created_at", "updated_at", "preview")
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("section", "category"), ("internal_name", "is_active", "sort_order")),
                "description": (
                    "Os blocos coloridos de uma seção «Categorias em destaque»; cada "
                    "seção tem os seus. O destino é uma categoria do catálogo — assim o "
                    "link continua válido se o endereço dela mudar. Sem categoria, o "
                    "bloco aparece sem link."
                ),
            },
        ),
        (
            "IMAGEM OU ÍCONE",
            {
                "fields": ("preview", ("icon", "image")),
                "description": (
                    "Opcionais, e só um dos dois aparece: a linha de cima (quando "
                    "cadastrada) tem prioridade, depois a imagem, depois o ícone."
                ),
            },
        ),
        (
            "CORES",
            {
                "fields": (("bg_color", "text_color", "accent_color"),),
                "description": (
                    "Cada bloco tem a sua. O contraste entre texto e fundo é "
                    "conferido ao salvar: uma combinação ilegível é recusada."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category", "section").prefetch_related(
            "translations"
        )

    def get_preview_context(self, request, instance):
        secao = instance.section
        if secao is None:
            return {"sections": [], "preview_empty": "Escolha a seção «Categorias em destaque» em que este bloco aparece."}
        blocos = list(
            secao.category_cards.select_related("category").prefetch_related("translations", "category__translations")
        )
        blocos = ordered(replace_or_append(blocos, instance))
        entry = ResolvedSection(
            section=secao,
            kind=ResolvedSection.KIND_CATEGORY_CARDS,
            blocks=[b for b in blocos if b.is_active or b is instance],
        )
        return {"sections": services._with_bands([entry])}

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "section":
            kwargs["queryset"] = HomeSection.objects.filter(
                section_type=HomeSectionType.CATEGORY_CARDS
            ).order_by("sort_order", "id")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

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
    fields = (("language", "title"), "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título e descrição por idioma"


@admin.register(HomeCalloutStep)
class HomeCalloutStepAdmin(LivePreviewMixin, admin.ModelAdmin):
    preview_component = "components/home_composition.html"
    preview_note = "A chamada final inteira, com este passo no lugar dele."
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
                "fields": (("callout", "internal_name"), ("is_active", "sort_order")),
                "description": (
                    "Os blocos numerados ao lado da chamada final. O número é a "
                    "POSIÇÃO na ordem, não um campo: desativar o segundo passo "
                    "renumera os outros em vez de deixar um buraco."
                ),
            },
        ),
        ("APARÊNCIA", {"fields": (("icon", "accent_color"),)}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_preview_context(self, request, instance):
        chamada = instance.callout
        if chamada is None:
            return {"sections": [], "preview_empty": "Escolha a chamada final a que este passo pertence."}
        passos = ordered(replace_or_append(list(chamada.steps.prefetch_related("translations")), instance))
        visiveis = [p for p in passos if (p.is_active and p.title) or p is instance]
        entry = ResolvedSection(
            section=None, kind=ResolvedSection.KIND_CALLOUT, callout=Wrapped(chamada, visible_steps=visiveis)
        )
        return {"sections": services._with_bands([entry])}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"

    @admin.display(description="cor do número")
    def cor(self, obj):
        return mark_safe(swatch(obj.accent_color))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Com uma chamada só cadastrada, o campo já vem preenchido com ela."""
        if db_field.name == "callout":
            unica = HomeCallout.objects.order_by("id").first()
            if unica is not None and HomeCallout.objects.count() == 1:
                kwargs["initial"] = unica.pk
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------------------------------------------------------------------------
# Sobre a loja
# ---------------------------------------------------------------------------


class HomeAboutTranslationInline(admin.StackedInline):
    model = HomeAboutTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = (("language", "eyebrow", "image_alt"), "title", "text")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma"


class HomeAboutBadgeTranslationInline(admin.StackedInline):
    model = HomeAboutBadgeTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = (("language", "text"),)
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO por idioma"


@admin.register(HomeAboutBadge)
class HomeAboutBadgeAdmin(LivePreviewMixin, admin.ModelAdmin):
    preview_component = "components/home_composition.html"
    preview_note = "O bloco «Sobre a loja» inteiro, com esta pílula no lugar dela."
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
                "fields": (("about", "internal_name"), ("is_active", "sort_order")),
                "description": "As pílulas coloridas abaixo do texto institucional.",
            },
        ),
        (
            "CORES",
            {
                "fields": (("bg_color", "text_color"),),
                "description": "O contraste é conferido ao salvar.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_preview_context(self, request, instance):
        sobre = instance.about
        if sobre is None:
            return {"sections": [], "preview_empty": "Escolha o bloco «Sobre a loja» a que esta pílula pertence."}
        pilulas = ordered(replace_or_append(list(sobre.badges.prefetch_related("translations")), instance))
        visiveis = [p for p in pilulas if (p.is_active and p.text) or p is instance]
        entry = ResolvedSection(
            section=None, kind=ResolvedSection.KIND_ABOUT, about=Wrapped(sobre, visible_badges=visiveis)
        )
        return {"sections": services._with_bands([entry])}

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
            unico = HomeAbout.objects.order_by("id").first()
            if unico is not None and HomeAbout.objects.count() == 1:
                kwargs["initial"] = unico.pk
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(HomeAbout)
class HomeAboutAdmin(LivePreviewMixin, admin.ModelAdmin):
    """O conteúdo dos blocos «Sobre a loja». A posição na Home é uma seção."""

    preview_component = "components/home_composition.html"
    preview_note = "O bloco como aparece na Home, com as pílulas cadastradas. Uma imagem nova aparece depois de salvar."
    inlines = [HomeAboutTranslationInline]
    form = PartialSafeModelForm
    save_on_top = True
    readonly_fields = ("created_at", "updated_at", "preview", "usado_em")
    list_display = ("__str__", "title_pt", "usado_em", "is_active")
    fieldsets = (
        (
            "EXIBIÇÃO",
            {
                "fields": (("internal_name", "is_active"), "usado_em"),
                "description": (
                    "O bloco com o quadro de imagem e o texto sobre a loja; as pílulas "
                    "coloridas são dele. Desmarcado, ele não aparece na Home — e não "
                    "deixa espaço vazio no lugar. Onde ele aparece é uma seção do tipo "
                    "«Sobre a loja», em Seções da Home."
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
                "fields": (("frame_color", "surface_color", "text_color"),),
                "description": "O quadro listrado, o fundo do bloco e a tinta do texto.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_preview_context(self, request, instance):
        if not instance.has_content:
            return {"sections": [], "preview_empty": "Sem título, texto, imagem ou pílulas, o bloco não é desenhado na Home."}
        entry = ResolvedSection(section=None, kind=ResolvedSection.KIND_ABOUT, about=instance)
        return {"sections": services._with_bands([entry])}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations", "sections")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"

    @admin.display(description="usado nas seções")
    def usado_em(self, obj):
        if not obj.pk:
            return "—"
        nomes = [s.internal_name for s in obj.sections.all()]
        return ", ".join(nomes) if nomes else "nenhuma seção usa este bloco ainda"

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
