"""Identidade visual do painel administrativo e configuração da loja."""

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.core.admin_mixins import RequiredDefaultLanguageInlineFormSet
from apps.core.models import (
    DeliveryCountry,
    DeliveryCountryTranslation,
    EmailSettings,
    SiteLanguage,
)

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

    @admin.action(permissions=["change"], description="Disponibilizar na loja")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} idioma(s) disponível(is).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Retirar da loja")
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

    list_display = (
        "iso_code",
        "translated_name",
        "is_active",
        "vat_rate",
        "rate_count",
        "shipping_warning",
        "sort_order",
    )
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
        return (
            super()
            .get_queryset(request)
            .prefetch_related("translations", "shipping_rates__method__carrier")
        )

    def changelist_view(self, request, extra_context=None):
        """Avisa, no topo da lista, sobre país ativo que não fecha checkout.

        Ativar um país é um clique; cadastrar a tabela de preços dele é outra
        tarefa, feita noutra tela. Entre as duas cabe um país que aparece no
        cadastro de endereço, aceita o cliente até o checkout e lá diz que não
        há entrega — e a loja não fica sabendo (AUD-05).

        O aviso não bloqueia nada: cadastrar o país antes da tarifa é uma ordem
        de trabalho legítima. O que não pode é isso passar despercebido.
        """
        problemas = []
        for country in self.get_queryset(request).filter(is_active=True):
            aviso = self.shipping_problem(country)
            if aviso:
                problemas.append(f"{country.name} ({country.iso_code}): {aviso}")

        if problemas:
            self.message_user(
                request,
                "Países ativos sem entrega possível — "
                + "; ".join(problemas)
                + ". Cadastre a tarifa em Frete › Tarifas, ou desative o país.",
                messages.WARNING,
            )
        return super().changelist_view(request, extra_context)

    @staticmethod
    def active_rates(country):
        """As tarifas que valem: da transportadora ativa, do método ativo."""
        return [
            rate
            for rate in country.shipping_rates.all()
            if rate.is_active and rate.method.is_active and rate.method.carrier.is_active
        ]

    def shipping_problem(self, country) -> str:
        """O que impede este país de fechar um checkout — ou string vazia."""
        rates = self.active_rates(country)
        if not rates:
            return "nenhuma tarifa de entrega cadastrada"

        # Uma faixa sem teto cobre qualquer peso: nada mais a checar.
        if any(rate.max_weight_grams is None for rate in rates):
            return ""

        teto = max(rate.max_weight_grams for rate in rates)
        return (
            f"a tabela vai até {teto} g — um pedido mais pesado fica sem opção "
            "de entrega"
        )

    @admin.display(description="nome")
    def translated_name(self, obj):
        return obj.name

    @admin.display(description="tarifas de frete")
    def rate_count(self, obj):
        return len(self.active_rates(obj))

    @admin.display(description="entrega")
    def shipping_warning(self, obj):
        """Coluna que responde "dá para comprar para este país hoje?"."""
        if not obj.is_active:
            return format_html('<span style="color:#6b7280">{}</span>', "país inativo")

        problema = self.shipping_problem(obj)
        if not problema:
            return format_html('<span style="color:#1a7f37">{}</span>', "✓ ok")
        return format_html(
            '<span style="color:#b42318" title="{}">{}</span>', problema, "⚠ sem tarifa"
        )

    @admin.action(permissions=["change"], description="Passar a entregar nestes países")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} país(es) disponível(is).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Parar de entregar nestes países")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} país(es) retirado(s) do checkout.", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Configuração de e-mail
# ---------------------------------------------------------------------------


class EmailSettingsForm(forms.ModelForm):
    """A senha entra, mas não sai.

    O campo do formulário **não** é o campo do model: ``password_encrypted``
    nunca é renderizado. Este campo é sempre desenhado vazio e, deixado vazio,
    conserva a senha que já estava gravada. É o que permite editar a porta sem
    ter de digitar a senha de novo — e o que garante que ela não viaja no HTML
    de volta para o navegador, onde ficaria no cache, no histórico e em
    qualquer captura de tela.
    """

    password = forms.CharField(
        label="senha",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=(
            "Deixe em branco para manter a senha atual. "
            "Ela é cifrada antes de ir para o banco e nunca é exibida."
        ),
    )
    clear_password = forms.BooleanField(
        label="apagar a senha gravada",
        required=False,
        help_text="Marque para o servidor passar a conectar sem autenticação.",
    )

    class Meta:
        model = EmailSettings
        fields = (
            "is_active",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "timeout",
            "from_email",
            "from_name",
            "reply_to",
            "admin_recipients",
            "contact_recipients",
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("clear_password") and cleaned.get("password"):
            self.add_error(
                "password",
                "Escolha uma coisa só: digitar uma senha nova ou apagar a atual.",
            )
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("clear_password"):
            obj.password = ""
        elif self.cleaned_data.get("password"):
            obj.password = self.cleaned_data["password"]
        # Sem nenhum dos dois: `password_encrypted` fica como estava.
        if commit:
            obj.save()
        return obj


class SendTestEmailForm(forms.Form):
    recipient = forms.EmailField(
        label="enviar teste para",
        help_text="Um endereço que você consiga abrir agora.",
    )


@admin.register(EmailSettings)
class EmailSettingsAdmin(admin.ModelAdmin):
    """De onde a loja envia e-mail.

    **Uma linha só** — é *a* configuração, não uma lista. E só superusuário
    entra: esta tela guarda uma credencial de servidor.

    Enquanto ela não estiver ativa, a loja continua usando as variáveis do
    ``.env``, exatamente como antes. A regra de prioridade está em
    ``apps/core/mailer.py`` e em ``docs/OPERACAO.md``.
    """

    form = EmailSettingsForm
    list_display = ("__str__", "source_display", "sender", "test_result")
    readonly_fields = (
        "effective_source",
        "password_state",
        "test_result",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "SITUAÇÃO",
            {
                "fields": ("effective_source", "test_result"),
                "description": (
                    "Enquanto esta configuração estiver inativa, a loja usa as "
                    "variáveis do arquivo <code>.env</code> do servidor."
                ),
            },
        ),
        (
            "SERVIDOR",
            {"fields": ("is_active", "host", "port", ("use_tls", "use_ssl"), "timeout")},
        ),
        (
            "CREDENCIAL",
            {
                "fields": ("username", "password_state", "password", "clear_password"),
                "description": (
                    "A senha é cifrada antes de ir para o banco e <strong>nunca</strong> "
                    "é exibida de volta. Deixe o campo em branco para manter a atual."
                ),
            },
        ),
        (
            "REMETENTE",
            {"fields": ("from_email", "from_name", "reply_to")},
        ),
        (
            "DESTINATÁRIOS INTERNOS",
            {"fields": ("admin_recipients", "contact_recipients")},
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    # -- permissões ---------------------------------------------------------

    def has_add_permission(self, request):
        # Uma linha só, e só para quem manda no servidor.
        return request.user.is_superuser and not EmailSettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        # Apagar deixaria a loja sem configuração no meio da operação. Para
        # voltar ao `.env`, basta desmarcar "usar esta configuração".
        return False

    # -- colunas e leitura --------------------------------------------------

    @admin.display(description="senha gravada")
    def password_state(self, obj):
        """Se existe senha — nunca qual é."""
        if obj is None or not obj.pk:
            return "—"
        if not obj.has_password:
            return format_html('<span style="color:#b42318">{}</span>', "nenhuma")
        from apps.core.secrets import mask

        return format_html(
            '<span style="color:#1a7f37">{}</span> <code>{}</code>',
            "gravada",
            mask(obj.password),
        )

    @admin.display(description="fonte em uso")
    def effective_source(self, obj=None):
        from apps.core.mailer import resolve

        config = resolve()
        if config.source == "admin":
            return format_html(
                '<b style="color:#1a7f37">{}</b> — {}',
                "configuração do Admin",
                config.host,
            )
        return format_html(
            '<b>{}</b> — {}',
            "variáveis do .env",
            config.host or "(backend local, sem servidor)",
        )

    @admin.display(description="fonte")
    def source_display(self, obj):
        return "Admin" if obj.is_usable else ".env"

    @admin.display(description="último teste")
    def test_result(self, obj=None):
        if obj is None or not obj.pk or obj.last_test_at is None:
            return "nunca testado"
        cor = "#1a7f37" if obj.last_test_ok else "#b42318"
        marca = "✓" if obj.last_test_ok else "⚠"
        # A data e formatada ANTES de entrar no `format_html`: ele escapa cada
        # argumento primeiro, e o `str.format` recebe uma SafeString, para a
        # qual `{:%d/%m/%Y}` nao e um spec valido. O resultado seria um 500
        # nesta tela assim que houvesse um teste registrado.
        quando = timezone.localtime(obj.last_test_at).strftime("%d/%m/%Y %H:%M")
        return format_html(
            '<span style="color:{}">{} {} — {}</span>',
            cor,
            marca,
            quando,
            obj.last_test_message or "",
        )

    # -- teste de envio -----------------------------------------------------

    def get_urls(self):
        extra = [
            path(
                "enviar-teste/",
                self.admin_site.admin_view(self.send_test_view),
                name="core_emailsettings_send_test",
            ),
        ]
        return extra + super().get_urls()

    def send_test_view(self, request):
        """Manda um e-mail de teste com a configuração que vale agora.

        Usa exatamente o mesmo caminho de um e-mail de pedido — se este teste
        chega, o pedido chega. Um teste que usasse outra conexão não provaria
        nada.
        """
        from apps.core.mailer import resolve, send_test_email

        if not request.user.is_superuser:
            self.message_user(request, "Sem permissão.", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:index"))

        config = resolve()
        form = SendTestEmailForm(request.POST or None)

        if request.method == "POST" and form.is_valid():
            destinatario = form.cleaned_data["recipient"]
            ok, mensagem = send_test_email(destinatario)

            obj = EmailSettings.load()
            obj.last_test_at = timezone.now()
            obj.last_test_ok = ok
            obj.last_test_message = mensagem[:300]
            obj.save(
                update_fields=[
                    "last_test_at", "last_test_ok", "last_test_message", "updated_at"
                ]
            )

            self.message_user(
                request,
                mensagem,
                messages.SUCCESS if ok else messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:core_emailsettings_change", args=[obj.pk])
            )

        contexto = {
            **self.admin_site.each_context(request),
            "title": "Enviar e-mail de teste",
            "form": form,
            "source": config.source,
            "host": config.host,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/core/send_test_email.html", contexto)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["send_test_url"] = reverse("admin:core_emailsettings_send_test")
        return super().change_view(request, object_id, form_url, extra_context)
