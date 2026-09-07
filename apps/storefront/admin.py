"""Administração do conteúdo da loja: faixa do topo e rodapé.

O padrão é o que a Home já usava: um inline de tradução (uma linha por idioma)
sob o registro principal, seções recolhíveis e edição em lote de "ativo" e
"ordem" direto na listagem — reordenar cinco itens não deveria custar cinco
telas.

Nada aqui inventa um segundo mecanismo de tradução: é o mesmo
``TranslationBase`` de ``ProductTranslation`` e companhia.
"""

import csv

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils import translation
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import check_for_language

from apps.core.colors import swatch
from apps.core.admin_preview import LivePreviewMixin, Wrapped, ordered, replace_or_append
from apps.core.admin_mixins import (
    PartialSafeModelForm,
    RequiredDefaultLanguageInlineFormSet,
    UniqueLanguageInlineFormSet,
)
from apps.core.constants import DEFAULT_LANGUAGE
from apps.storefront.models import (
    ContactMessage,
    FooterColumn,
    FooterColumnTranslation,
    FooterLink,
    FooterLinkTranslation,
    FooterSettings,
    FooterSettingsTranslation,
    InstitutionalPage,
    InstitutionalPageTranslation,
    LaunchSubscriber,
    SpecialPage,
    SpecialPageBenefit,
    SpecialPageBenefitTranslation,
    SpecialPageTranslation,
    TIMEZONE_CHOICES,
    TopBarItem,
    TopBarItemTranslation,
    page_cta_url,
)
from apps.storefront.context_processors import _footer_columns


class ActivateActionsMixin(admin.ModelAdmin):
    """Ativar/desativar em lote — o que mais se faz nestas telas."""

    actions = ("action_activate", "action_deactivate")

    @admin.action(permissions=["change"], description="Ativar selecionados")
    def action_activate(self, request, queryset):
        total = queryset.update(is_active=True)
        self.message_user(request, f"{total} item(ns) ativado(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Desativar selecionados")
    def action_deactivate(self, request, queryset):
        total = queryset.update(is_active=False)
        self.message_user(request, f"{total} item(ns) desativado(s).", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Faixa do topo
# ---------------------------------------------------------------------------


class TopBarItemTranslationInline(admin.StackedInline):
    model = TopBarItemTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = (("language", "text"),)
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO — uma linha por idioma"


@admin.register(TopBarItem)
class TopBarItemAdmin(LivePreviewMixin, ActivateActionsMixin):
    preview_component = "components/top_bar.html"
    preview_note = "A faixa inteira, com as frases ativas de hoje e esta no lugar dela."
    inlines = [TopBarItemTranslationInline]
    form = PartialSafeModelForm
    list_display = ("internal_name", "text_pt", "icon", "cor", "is_active", "sort_order", "updated_at")
    list_display_links = ("internal_name", "text_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("internal_name", "translations__text")
    ordering = ("sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("internal_name", "is_active", "sort_order"),),
                "description": (
                    "Aparece na faixa escura acima do cabeçalho <b>e</b> na lista "
                    "do hero quando não há banner com imagem.<br>"
                    "Nas telas estreitas a faixa quebra em duas linhas — nenhuma "
                    "promessa cadastrada deixa de ser lida."
                ),
            },
        ),
        (
            "APARÊNCIA",
            {
                "fields": (("icon", "color"), "link_url"),
                "description": (
                    "O desenho da marca alterna as cores dos itens — amarelo, "
                    "menta e coral. A cor é conferida contra o fundo navy da "
                    "faixa ao salvar: uma tinta que não se leria ali é recusada.<br>"
                    "O ícone e o endereço são opcionais."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="cor")
    def cor(self, obj):
        return mark_safe(swatch(obj.color))

    def get_preview_context(self, request, instance):
        itens = ordered(replace_or_append(list(TopBarItem.objects.for_display()), instance))
        return {"top_bar_items": [i for i in itens if i.is_active or i is instance]}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="texto (pt)")
    def text_pt(self, obj):
        return obj.tr("text", language=DEFAULT_LANGUAGE.value, default="—")


# ---------------------------------------------------------------------------
# Rodapé
# ---------------------------------------------------------------------------


class FooterSettingsTranslationInline(admin.StackedInline):
    model = FooterSettingsTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = (
        "language",
        "about_text",
        "categories_title",
        "contact_title",
        "copyright_text",
        "badge_text",
    )
    verbose_name = "textos por idioma"
    verbose_name_plural = "TEXTOS — uma linha por idioma"


@admin.register(FooterSettings)
class FooterSettingsAdmin(LivePreviewMixin, admin.ModelAdmin):
    """Uma linha só. O Admin leva direto a ela em vez de mostrar uma lista de um."""

    preview_component = "components/footer.html"
    preview_note = "O rodapé inteiro, com as colunas e os links cadastrados hoje."
    inlines = [FooterSettingsTranslationInline]
    save_on_top = True
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "EXIBIÇÃO",
            {
                "fields": ("is_active",),
                "description": (
                    "Desmarcado, o rodapé volta aos textos padrão do template — "
                    "a loja nunca fica com um rodapé vazio."
                ),
            },
        ),
        (
            "CONTATO",
            {
                "fields": (("contact_email", "contact_phone"),),
                "description": (
                    "Opcionais. Em branco, o bloco de contato não aparece no rodapé."
                ),
            },
        ),
        (
            "CORES",
            {
                "fields": (("surface_color", "text_color"), ("heading_color", "accent_color")),
                "description": (
                    "Só a aparência: as colunas, os links e as páginas "
                    "institucionais continuam vindo do cadastro abaixo e não são "
                    "afetados.<br>"
                    "Cada tinta é conferida contra o fundo ao salvar — não dá "
                    "para configurar um rodapé ilegível."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_preview_context(self, request, instance):
        # Desligado, o rodapé volta aos textos padrão — é o que o site faz.
        return {"footer_settings": instance if instance.is_active else None}

    def has_add_permission(self, request):
        """Nunca "adicionar": a linha já existe e é sempre a mesma."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Apagar deixaria a loja sem a linha; para desligar, use "usar este rodapé"."""
        return False

    def changelist_view(self, request, extra_context=None):
        """Uma lista de um item é uma tela a mais para chegar ao mesmo lugar."""
        from django.shortcuts import redirect
        from django.urls import reverse

        settings_obj = FooterSettings.load()
        return redirect(
            reverse("admin:storefront_footersettings_change", args=[settings_obj.pk])
        )


class FooterLinkTranslationInline(admin.StackedInline):
    model = FooterLinkTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = (("language", "label"),)
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO — uma linha por idioma"


@admin.register(FooterLink)
class FooterLinkAdmin(LivePreviewMixin, ActivateActionsMixin):
    preview_component = "components/footer.html"
    preview_note = "O rodapé inteiro, com este link na coluna dele."
    inlines = [FooterLinkTranslationInline]
    form = PartialSafeModelForm
    list_display = ("label_pt", "column", "destino", "is_active", "sort_order")
    list_display_links = ("label_pt",)
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "column")
    search_fields = ("url", "translations__label")
    ordering = ("column", "sort_order", "id")
    list_select_related = ("column", "page")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("column", "page", "url"), ("is_active", "sort_order")),
                "description": (
                    "Para uma página da loja, escolha-a no campo acima — o link "
                    "acompanha o idioma do visitante e o texto vira o título da "
                    "página. O endereço é para o que é de fora. Sem os dois, o "
                    "item aparece como texto, sem link."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="texto (pt)")
    def label_pt(self, obj):
        return obj.tr("label", language=DEFAULT_LANGUAGE.value, default="—")

    @admin.display(description="destino")
    def destino(self, obj):
        if obj.page_id:
            return f"página: {obj.page.get_slug_display()}"
        return obj.url or "— (texto sem link)"

    def get_preview_context(self, request, instance):
        coluna = instance.column
        colunas = []
        for col in _footer_columns():
            if coluna is not None and col.pk == coluna.pk:
                links = ordered(replace_or_append(list(col.links.all()), instance))
                visiveis = [l for l in links if (l.is_active and l.is_visible) or l is instance]
                colunas.append(Wrapped(col, visible_links=visiveis))
            else:
                colunas.append(col)
        return {"footer_columns": colunas}


class FooterColumnTranslationInline(admin.StackedInline):
    model = FooterColumnTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = (("language", "title"),)
    verbose_name = "título por idioma"
    verbose_name_plural = "TÍTULO — uma linha por idioma"


class FooterLinkInline(admin.TabularInline):
    """Os links da coluna, na própria tela da coluna.

    A tradução de cada link fica na tela do link: um inline aninhado não existe
    no Django Admin, e forçar um seria uma tela pior que duas. Apontando para
    uma página da loja, nem é preciso: o texto vem do título dela.
    """

    model = FooterLink
    extra = 1
    fields = ("sort_order", "page", "url", "is_active")
    ordering = ("sort_order", "id")
    classes = ("jd-cards",)  # nas telas estreitas vira um card por link (jdprint_forms.css)
    verbose_name = "link"
    verbose_name_plural = "LINKS — o texto de cada um é cadastrado na tela do link"


@admin.register(FooterColumn)
class FooterColumnAdmin(LivePreviewMixin, ActivateActionsMixin):
    preview_component = "components/footer.html"
    preview_note = "O rodapé inteiro, com o título desta coluna. Links acrescentados aqui aparecem depois de salvar."
    inlines = [FooterColumnTranslationInline, FooterLinkInline]

    class Media:
        js = ("admin/js/jd_tabular_cards.js",)
    form = PartialSafeModelForm
    list_display = ("internal_name", "title_pt", "link_count", "is_active", "sort_order")
    list_display_links = ("internal_name", "title_pt")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("internal_name", "translations__title")
    ordering = ("sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("internal_name", "is_active", "sort_order"),),
                "description": (
                    "A coluna de <b>categorias</b> não é cadastrada aqui: ela vem "
                    "das categorias do catálogo e se atualiza sozinha. Só o título "
                    "dela é editável, em RODAPÉ — textos e contato."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations", "links")

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, default="—")

    @admin.display(description="links")
    def link_count(self, obj):
        return len(obj.links.all())

    def get_preview_context(self, request, instance):
        colunas = []
        for col in _footer_columns():
            if col.pk == instance.pk:
                if instance.is_active:
                    colunas.append(Wrapped(col, title=instance.title, sort_order=instance.sort_order))
            else:
                colunas.append(col)
        return {"footer_columns": ordered(colunas)}


# ---------------------------------------------------------------------------
# Páginas institucionais
# ---------------------------------------------------------------------------


class InstitutionalPageTranslationInline(admin.StackedInline):
    """O texto por idioma.

    Inline empilhado, e não o modal de CONTEÚDO do produto: aquele modal fala
    com `admin:catalog_product_content_save`, uma rota que só existe para
    `ProductTranslation`. Reaproveitá-lo aqui exigiria generalizar aquelas
    views — mais peça móvel do que quatro textos por página justificam. O
    padrão continua sendo o mesmo dos banners e da faixa do topo.
    """

    model = InstitutionalPageTranslation
    formset = UniqueLanguageInlineFormSet
    extra = 0
    fields = ("language", "title", "intro", "body", "meta_description")
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — título e texto por idioma"


@admin.register(InstitutionalPage)
class InstitutionalPageAdmin(LivePreviewMixin, ActivateActionsMixin):
    preview_component = "admin/preview/page.html"
    preview_note = "O texto como o cliente lê na página: títulos com «# », listas com «- »."
    inlines = [InstitutionalPageTranslationInline]
    form = PartialSafeModelForm
    save_on_top = True
    list_display = ("page_name", "title_pt", "has_form", "is_active", "show_in_footer", "sort_order")
    list_display_links = ("page_name", "title_pt")
    list_editable = ("is_active", "show_in_footer", "sort_order")
    list_filter = ("is_active", "show_in_footer")
    search_fields = ("slug", "translations__title", "translations__body")
    ordering = ("sort_order", "slug")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("slug", "is_active"), ("show_in_footer", "sort_order")),
                "description": (
                    "Cada página tem uma URL própria na loja. <b>Contato</b> e "
                    "<b>Seja um revendedor</b> mostram o formulário abaixo do texto — "
                    "elas funcionam mesmo sem nenhum texto cadastrado."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="página", ordering="slug")
    def page_name(self, obj):
        return obj.get_slug_display()

    @admin.display(description="título (pt)")
    def title_pt(self, obj):
        return obj.tr("title", language=DEFAULT_LANGUAGE.value, default="—")

    @admin.display(description="formulário", boolean=True)
    def has_form(self, obj):
        return bool(obj.form_kind)

    def get_preview_context(self, request, instance):
        return {
            "page": instance,
            "page_title": instance.title or instance.get_slug_display(),
            "page_cta_url": page_cta_url(instance.slug) if instance.body else "",
            "form": None,
        }


# ---------------------------------------------------------------------------
# O que chega pelos formulários
# ---------------------------------------------------------------------------


class ReceivedMessageAdmin(admin.ModelAdmin):
    """Caixa de entrada, não CRM.

    Só leitura do conteúdo: a mensagem é o que a pessoa escreveu, e editá-la
    seria reescrever o que ela disse. O que a equipe muda é "já respondida" —
    o mínimo para não reler duas vezes a mesma coisa.

    Apagar continua possível: um pedido de remoção de dados tem de ter uma
    porta.
    """

    list_filter = ("is_handled", "created_at")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50
    actions = ("action_mark_handled", "action_mark_pending")

    def has_add_permission(self, request):
        return False

    @admin.action(permissions=["change"], description="Marcar como respondida")
    def action_mark_handled(self, request, queryset):
        total = queryset.update(is_handled=True)
        self.message_user(request, f"{total} marcada(s) como respondida(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Marcar como pendente")
    def action_mark_pending(self, request, queryset):
        total = queryset.update(is_handled=False)
        self.message_user(request, f"{total} marcada(s) como pendente(s).", messages.SUCCESS)


@admin.register(ContactMessage)
class ContactMessageAdmin(ReceivedMessageAdmin):
    list_display = ("subject", "name", "email", "language", "is_handled", "created_at")
    list_editable = ("is_handled",)
    search_fields = ("name", "email", "subject", "message")
    readonly_fields = ("name", "email", "subject", "message", "language", "created_at", "updated_at")
    fieldsets = (
        ("QUEM ESCREVEU", {"fields": ("name", "email", "language")}),
        ("MENSAGEM", {"fields": ("subject", "message")}),
        ("ATENDIMENTO", {"fields": ("is_handled",)}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )


# ---------------------------------------------------------------------------
# Manutenção e lançamento
# ---------------------------------------------------------------------------


class SpecialPageForm(PartialSafeModelForm):
    """O fuso vem de uma lista curta; um valor gravado fora dela continua aparecendo."""

    launch_timezone = forms.ChoiceField(label="fuso horário", choices=TIMEZONE_CHOICES)

    class Meta:
        model = SpecialPage
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        atual = getattr(self.instance, "launch_timezone", "")
        opcoes = list(TIMEZONE_CHOICES)
        if atual and atual not in dict(opcoes):
            opcoes.append((atual, atual))
        self.fields["launch_timezone"].choices = opcoes
        self.fields["launch_timezone"].help_text = SpecialPage._meta.get_field("launch_timezone").help_text


class SpecialPageTranslationInline(admin.StackedInline):
    model = SpecialPageTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — um bloco por idioma (o português é obrigatório)"
    fieldsets = (
        (None, {"fields": (("language", "status_text", "eyebrow"), ("title", "title_highlight"), "description")}),
        ("Botões", {"fields": (("primary_label", "secondary_label"),)}),
        ("Selos e rodapé", {"fields": (("sticker_1", "sticker_2", "sticker_3"), "footer_text")}),
        ("Manutenção", {"fields": ("progress_label",)}),
        (
            "Lançamento",
            {"fields": (("countdown_done_text", "form_placeholder"), ("form_button_label", "form_note"), "form_success_text")},
        ),
    )


@admin.register(SpecialPage)
class SpecialPageAdmin(LivePreviewMixin, admin.ModelAdmin):
    """A página que fecha a loja — com a ativação pedindo confirmação."""

    preview_note = "A página inteira, no modelo escolhido (manutenção ou lançamento). Benefícios e logo aparecem depois de salvar."
    form = SpecialPageForm
    inlines = [SpecialPageTranslationInline]
    save_on_top = True
    list_display = ("internal_name", "kind", "ativa", "updated_at", "links")
    list_display_links = ("internal_name",)
    list_filter = ("kind", "is_active")
    search_fields = ("internal_name",)
    ordering = ("-is_active", "-updated_at")
    actions = ("action_activate_page", "action_deactivate_page")
    readonly_fields = ("created_at", "updated_at", "links", "beneficios")
    fieldsets = (
        (
            "IDENTIFICAÇÃO",
            {
                "fields": (("internal_name", "kind"), "is_active", ("links", "beneficios")),
                "description": (
                    "<strong>⚠️ ATIVAR ESTA PÁGINA BLOQUEARÁ O SITE PÚBLICO.</strong> "
                    "Só uma página fica ativa por vez; ativar esta desliga a outra. "
                    "Confira antes na pré-visualização."
                ),
            },
        ),
        (
            "MARCA",
            {
                "fields": (("logo", "logo_mark", "logo_text"), ("logo_url", "status_color", "status_pulse")),
                "description": "A pílula de status ao lado da logo leva o texto do bloco de idioma abaixo.",
            },
        ),
        (
            "BOTÕES",
            {
                "fields": (("primary_enabled", "primary_url"), ("secondary_enabled", "secondary_url")),
                "description": "Os textos ficam no bloco de idioma. Um botão sem texto ou sem link não aparece.",
            },
        ),
        (
            "MANUTENÇÃO — a impressora",
            {"classes": ("jd-sp-maintenance",), "fields": (("show_progress", "progress_percent"),)},
        ),
        (
            "LANÇAMENTO — contagem e formulário",
            {
                "classes": ("jd-sp-launch",),
                "fields": (("launch_date", "launch_time", "launch_timezone"), ("show_countdown", "show_form")),
                "description": (
                    "A contagem é calculada no navegador do visitante a partir desta data. "
                    "Os e-mails deixados no formulário ficam em «Inscritos»."
                ),
            },
        ),
        (
            "SELOS",
            {
                "fields": (("sticker_1_tone", "sticker_2_tone", "sticker_3_tone"),),
                "description": "Os textos ficam no bloco de idioma; um selo sem texto não aparece.",
            },
        ),
        ("RODAPÉ E CONTATO", {"fields": (("instagram_url", "whatsapp_url", "contact_email"),)}),
        (
            "CORES",
            {
                "classes": ("collapse",),
                "fields": (
                    "surface_color",
                    ("brand_color", "brand_text_color"),
                    ("accent_color", "accent_text_color"),
                ),
                "description": "O contraste é conferido ao salvar: uma dupla ilegível é recusada.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    class Media:
        js = ("admin/js/jd_fields.js", "admin/js/special_page_admin.js")

    def render_live_preview(self, request, instance):
        """A página especial inteira, como `render_special_page` a entrega ao visitante."""
        from apps.storefront.views import render_special_page

        return render_special_page(request, instance, preview=True)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="ativa", ordering="is_active")
    def ativa(self, obj):
        if obj.is_active:
            return mark_safe('<span style="color:#2e7d32;font-weight:700">● no ar</span>')
        return mark_safe('<span style="color:#8a8399">○</span>')

    @admin.display(description="ver")
    def links(self, obj):
        if not obj.pk:
            return "—"
        url = reverse("admin:storefront_specialpage_preview", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">pré-visualizar</a>'
            ' · <a href="{}?lang=fr" target="_blank" rel="noopener">fr</a>'
            ' · <a href="{}?lang=nl" target="_blank" rel="noopener">nl</a>'
            ' · <a href="{}?lang=en" target="_blank" rel="noopener">en</a>',
            url, url, url, url,
        )

    @admin.display(description="benefícios")
    def beneficios(self, obj):
        if not obj.pk:
            return "Salve a página para cadastrar os benefícios."
        total = obj.benefits.count()
        url = reverse("admin:storefront_specialpagebenefit_changelist") + f"?page__id__exact={obj.pk}"
        novo = reverse("admin:storefront_specialpagebenefit_add") + f"?page={obj.pk}"
        return format_html(
            '{} cadastrado(s) — <a href="{}">ver</a> · <a href="{}">acrescentar</a>', total, url, novo
        )

    # -- pré-visualização --------------------------------------------------------

    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                "<int:pk>/preview/",
                self.admin_site.admin_view(self.preview_view),
                name="storefront_specialpage_preview",
            ),
        ]
        return extra + urls

    def preview_view(self, request, pk):
        """A página como o visitante veria — só para quem pode ver o cadastro.

        Passa por `admin_view` (exige login de equipe) e pela permissão de
        visualização do model. `?lang=fr` mostra a versão de outro idioma.
        """
        page = get_object_or_404(
            SpecialPage.objects.prefetch_related("translations", "benefits__translations"), pk=pk
        )
        if not self.has_view_permission(request, page):
            raise PermissionDenied
        from apps.storefront.views import render_special_page

        idioma = request.GET.get("lang", "")
        if idioma and check_for_language(idioma):
            with translation.override(idioma):
                return render_special_page(request, page, preview=True)
        return render_special_page(request, page, preview=True)

    # -- ativação --------------------------------------------------------------------

    @admin.action(permissions=["change"], description="Ativar a página selecionada (bloqueia o site público)")
    def action_activate_page(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Escolha uma página só: apenas uma pode ficar ativa.", messages.ERROR)
            return None
        page = queryset.first()
        if request.POST.get("confirmar"):
            try:
                page.activate()
            except IntegrityError:
                self.message_user(
                    request,
                    "Outra página foi ativada neste mesmo instante. Recarregue e tente de novo.",
                    messages.ERROR,
                )
                return None
            self.message_user(
                request,
                f"«{page.internal_name}» está no ar: o site público mostra só esta página.",
                messages.WARNING,
            )
            return None
        return render(
            request,
            "admin/storefront/specialpage/activate.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": "Ativar página especial",
                "page": page,
                "current": SpecialPage.objects.filter(is_active=True).exclude(pk=page.pk).first(),
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
                "action_field": "action",
                "action_name": "action_activate_page",
            },
        )

    @admin.action(permissions=["change"], description="Desativar (reabre o site público)")
    def action_deactivate_page(self, request, queryset):
        total = 0
        for page in queryset.filter(is_active=True):
            page.deactivate()
            total += 1
        if total:
            self.message_user(request, "Página desativada: o site público voltou ao normal.", messages.SUCCESS)
        else:
            self.message_user(request, "Nenhuma das páginas escolhidas estava ativa.", messages.INFO)


class SpecialPageBenefitTranslationInline(admin.StackedInline):
    model = SpecialPageBenefitTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    fields = (("language", "text"),)
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO por idioma (o português é obrigatório)"


@admin.register(SpecialPageBenefit)
class SpecialPageBenefitAdmin(admin.ModelAdmin):
    inlines = [SpecialPageBenefitTranslationInline]
    form = PartialSafeModelForm
    list_display = ("text_pt", "page", "tone", "is_active", "sort_order")
    list_display_links = ("text_pt",)
    list_editable = ("is_active", "sort_order")
    list_filter = ("page",)
    ordering = ("page", "sort_order", "id")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "BENEFÍCIO",
            {
                "fields": (("page", "tone"), ("is_active", "sort_order")),
                "description": "Uma promessa curta sob os botões: o quadradinho colorido e o texto.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("page").prefetch_related("translations")

    @admin.display(description="texto (pt)")
    def text_pt(self, obj):
        return obj.tr("text", language=DEFAULT_LANGUAGE.value, fallback=False) or "—"


@admin.register(LaunchSubscriber)
class LaunchSubscriberAdmin(admin.ModelAdmin):
    """Quem pediu o aviso do lançamento. Só leitura, com exportação em CSV."""

    list_display = ("email", "language", "page", "created_at")
    list_filter = ("page", "language")
    search_fields = ("email",)
    date_hierarchy = "created_at"
    readonly_fields = ("email", "language", "page", "created_at", "updated_at")
    actions = ("action_export_csv",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(permissions=["view"], description="Exportar selecionados em CSV")
    def action_export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="inscritos-lancamento.csv"'
        response.write("﻿")  # BOM: o Excel abre em UTF-8 sem perguntar
        escritor = csv.writer(response, delimiter=";")
        escritor.writerow(["email", "idioma", "pagina", "inscrito_em"])
        for inscrito in queryset.select_related("page").order_by("created_at"):
            escritor.writerow([
                inscrito.email,
                inscrito.language,
                inscrito.page.internal_name if inscrito.page else "",
                inscrito.created_at.isoformat(timespec="seconds"),
            ])
        return response
