"""Identidade visual do painel administrativo e configuração da loja."""

from django import forms
from django.conf import settings
from django.contrib import admin, messages

from apps.core.admin_mixins import RequiredDefaultLanguageInlineFormSet
from apps.core.models import DeliveryCountry, DeliveryCountryTranslation, SiteLanguage

admin.site.site_header = "JD PRINT — Administração"
admin.site.site_title = "JD PRINT"
admin.site.index_title = "Catálogo"


class SiteLanguageForm(forms.ModelForm):
    """As opções vêm de ``settings.LANGUAGES`` na hora de exibir o formulário.

    O model não guarda ``choices`` de propósito: elas seriam congeladas dentro
    da migration e mexer na lista de idiomas suportados passaria a gerar
    migrations falsas.
    """

    class Meta:
        model = SiteLanguage
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"] = forms.ChoiceField(
            label="idioma", choices=settings.LANGUAGES, help_text="Idioma suportado pelo sistema."
        )


@admin.register(SiteLanguage)
class SiteLanguageAdmin(admin.ModelAdmin):
    """Quais idiomas a loja oferece hoje.

    Ativar um idioma não exige ter todos os textos traduzidos: o que faltar cai
    no português.
    """

    form = SiteLanguageForm
    list_display = ("native_name", "code", "name", "is_active", "default_badge", "sort_order")
    list_display_links = ("native_name", "code")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    ordering = ("sort_order", "code")
    actions = ("action_activate", "action_deactivate")

    @admin.display(description="nome nativo")
    def native_name(self, obj):
        return obj.native_name

    @admin.display(description="nome")
    def name(self, obj):
        return obj.name

    @admin.display(description="padrão", boolean=True)
    def default_badge(self, obj):
        return obj.is_default

    def has_delete_permission(self, request, obj=None):
        # O idioma padrão nunca sai: a loja ficaria sem fallback.
        if obj is not None and obj.is_default:
            return False
        return super().has_delete_permission(request, obj)

    @admin.action(description="Disponibilizar na loja")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} idioma(s) disponível(is).", messages.SUCCESS)

    @admin.action(description="Retirar da loja")
    def action_deactivate(self, request, queryset):
        protected = queryset.filter(code=settings.LANGUAGE_CODE)
        updated = queryset.exclude(code=settings.LANGUAGE_CODE).update(is_active=False)

        if protected.exists():
            self.message_user(
                request,
                "O idioma padrão da loja não pode ser retirado — ele é o fallback de tudo.",
                messages.WARNING,
            )
        self.message_user(request, f"{updated} idioma(s) retirado(s) da loja.", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Países de entrega
# ---------------------------------------------------------------------------


class DeliveryCountryTranslationInline(admin.TabularInline):
    """O nome do país em cada idioma da loja.

    Mesma mecânica das traduções do catálogo: uma linha por idioma, nenhuma
    coluna ``nome_fr`` no schema. Sem tradução nenhuma, a loja mostra o código
    ISO — feio, mas honesto, e o administrador vê na hora o que falta.
    """

    model = DeliveryCountryTranslation
    formset = RequiredDefaultLanguageInlineFormSet
    extra = 1
    fields = ("language", "name")


@admin.register(DeliveryCountry)
class DeliveryCountryAdmin(admin.ModelAdmin):
    """Para onde a loja envia — e com qual alíquota.

    Desligar um país o tira do checkout e do cadastro de endereços na mesma
    hora. Endereços já salvos naquele país continuam existindo (o cliente não
    perde o cadastro), mas o checkout os recusa com uma mensagem clara.
    """

    list_display = ("iso_code", "translated_name", "is_active", "vat_rate", "rate_count", "sort_order")
    list_editable = ("is_active", "vat_rate", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("iso_code", "translations__name")
    inlines = (DeliveryCountryTranslationInline,)
    actions = ("action_activate", "action_deactivate")
    fieldsets = (
        (None, {"fields": ("iso_code", "is_active", "sort_order")}),
        (
            "IMPOSTO",
            {
                "fields": ("vat_rate",),
                "description": (
                    "Alíquota aplicada quando a entrega é neste país (regime de venda "
                    "a distância B2C da UE). O pedido guarda uma cópia: mudar aqui não "
                    "reescreve faturas antigas."
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="nome")
    def translated_name(self, obj):
        return obj.name

    @admin.display(description="tarifas de frete")
    def rate_count(self, obj):
        return obj.shipping_rates.count()

    @admin.action(description="Passar a entregar nestes países")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} país(es) disponível(is).", messages.SUCCESS)

    @admin.action(description="Parar de entregar nestes países")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} país(es) retirado(s) do checkout.", messages.SUCCESS)
