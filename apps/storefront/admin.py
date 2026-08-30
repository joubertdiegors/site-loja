"""Administração do conteúdo da loja: faixa do topo e rodapé.

O padrão é o que a Home já usava: um inline de tradução (uma linha por idioma)
sob o registro principal, seções recolhíveis e edição em lote de "ativo" e
"ordem" direto na listagem — reordenar cinco itens não deveria custar cinco
telas.

Nada aqui inventa um segundo mecanismo de tradução: é o mesmo
``TranslationBase`` de ``ProductTranslation`` e companhia.
"""

from django.contrib import admin, messages

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
    TopBarItem,
    TopBarItemTranslation,
)


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
    fields = ("language", "text")
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO — uma linha por idioma"


@admin.register(TopBarItem)
class TopBarItemAdmin(ActivateActionsMixin):
    inlines = [TopBarItemTranslationInline]
    form = PartialSafeModelForm
    list_display = ("internal_name", "text_pt", "icon", "is_active", "sort_order", "updated_at")
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
                "fields": ("internal_name", "icon", "is_active", "sort_order"),
                "description": (
                    "Aparece na faixa escura acima do cabeçalho <b>e</b> na lista do "
                    "hero quando não há banner com imagem. O ícone é usado só no "
                    "hero — a faixa é sempre só texto.<br>"
                    "Do segundo item em diante a faixa vai escondendo nas telas "
                    "estreitas: em 390 px não cabem três frases lado a lado."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

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
class FooterSettingsAdmin(admin.ModelAdmin):
    """Uma linha só. O Admin leva direto a ela em vez de mostrar uma lista de um."""

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
                "fields": ("contact_email", "contact_phone"),
                "description": (
                    "Opcionais. Em branco, o bloco de contato não aparece no rodapé."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

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
    fields = ("language", "label")
    verbose_name = "texto por idioma"
    verbose_name_plural = "TEXTO — uma linha por idioma"


@admin.register(FooterLink)
class FooterLinkAdmin(ActivateActionsMixin):
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
                "fields": ("column", "page", "url", "is_active", "sort_order"),
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


class FooterColumnTranslationInline(admin.StackedInline):
    model = FooterColumnTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "title")
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
    verbose_name = "link"
    verbose_name_plural = "LINKS — o texto de cada um é cadastrado na tela do link"


@admin.register(FooterColumn)
class FooterColumnAdmin(ActivateActionsMixin):
    inlines = [FooterColumnTranslationInline, FooterLinkInline]
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
                "fields": ("internal_name", "is_active", "sort_order"),
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
class InstitutionalPageAdmin(ActivateActionsMixin):
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
                "fields": ("slug", "is_active", "show_in_footer", "sort_order"),
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
