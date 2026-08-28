"""Admin dos pedidos.

A tela do pedido é feita para **operar**, não para editar: quem produz precisa
ver de relance o cliente, o que foi comprado, se pagou, o que personalizou e
para onde vai. Por isso os itens, o histórico e os endereços são somente
leitura, e as ações que mudam o pedido (marcar como enviado, decidir um
cancelamento) são ações explícitas — não campos soltos que alguém altera sem
querer e sem registro.

**Dado histórico não se edita.** Valores, snapshots dos itens e endereços
copiados ficam bloqueados: um pedido é documento contábil. O que se altera é o
estado da produção, o rastreio e as notas internas.
"""

from django.contrib import admin, messages
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _

from apps.orders import services
from apps.orders.models import (
    CancellationStatus,
    FulfillmentStatus,
    Order,
    OrderAddress,
    OrderItem,
    OrderNote,
    OrderNumberSequence,
    OrderStatusHistory,
    Payment,
    WebhookEvent,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class ReadOnlyInline(admin.TabularInline):
    """Inline de consulta: nada de acrescentar, alterar ou remover."""

    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class OrderItemInline(ReadOnlyInline):
    model = OrderItem
    fields = (
        "product_name",
        "sku",
        "variant_label",
        "quantity",
        "unit_price",
        "total",
        "fulfillment_type",
        "production_days",
        "personalization_summary",
    )
    readonly_fields = fields

    @admin.display(description="personalização")
    def personalization_summary(self, obj):
        if not obj.has_personalization:
            return "—"
        if obj.personalization_type == "photo" and obj.personalization_upload_id:
            upload = obj.personalization_upload
            return format_html(
                '<a href="{}" target="_blank" rel="noopener">{}</a>',
                upload.file.url,
                upload.original_name or upload.file.name,
            )
        return obj.personalization_text or obj.personalization_notes or obj.personalization_type


class OrderAddressInline(ReadOnlyInline):
    model = OrderAddress
    fields = ("kind", "full_name", "company_name", "street", "postal_code", "city", "country_name", "phone")
    readonly_fields = fields

    @admin.display(description="nome")
    def full_name(self, obj):
        return obj.full_name


class OrderHistoryInline(ReadOnlyInline):
    model = OrderStatusHistory
    fields = ("created_at", "event", "message", "created_by", "is_customer_visible")
    readonly_fields = fields
    ordering = ("created_at",)


class PaymentInline(ReadOnlyInline):
    model = Payment
    fields = (
        "created_at",
        "provider",
        "status",
        "amount",
        "currency",
        "method_label",
        "provider_session_id",
        "provider_payment_id",
        "paid_at",
    )
    readonly_fields = fields


class OrderNoteInline(admin.StackedInline):
    """A única coisa que se escreve na tela do pedido."""

    model = OrderNote
    extra = 0
    fields = ("body",)

    def save_model(self, request, obj, form, change):  # pragma: no cover - via formset
        obj.author = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):  # pragma: no cover
        instances = formset.save(commit=False)
        for instance in instances:
            instance.author = request.user
            instance.save()
        formset.save_m2m()


# ---------------------------------------------------------------------------
# Pedido
# ---------------------------------------------------------------------------


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "number",
        "created_at",
        "customer_link",
        "total_display",
        "status",
        "payment_status",
        "fulfillment_status",
        "cancellation_flag",
        "emails_flag",
        "gift_flag",
    )
    list_filter = (
        "status",
        "payment_status",
        "fulfillment_status",
        "cancellation_status",
        "is_gift",
        "created_at",
    )
    search_fields = (
        "number",
        "customer__first_name",
        "customer__last_name",
        "customer__user__email",
        "customer__user__username",
        "tracking_number",
    )
    list_select_related = ("customer", "customer__user")
    inlines = (OrderItemInline, OrderAddressInline, PaymentInline, OrderHistoryInline, OrderNoteInline)
    actions = (
        "action_mark_shipped",
        "action_resend_confirmation",
        "action_resend_admin_email",
        "action_resend_shipped_email",
        "action_approve_cancellation",
        "action_refuse_cancellation",
    )

    # Documento contábil: o que aconteceu não se reescreve.
    readonly_fields = (
        "number",
        "customer",
        "created_at",
        "updated_at",
        "currency",
        "subtotal",
        "discount_total",
        "shipping_total",
        "tax_total",
        "total",
        "tax_country",
        "tax_rate",
        "prices_include_tax",
        "shipping_method",
        "shipping_method_label",
        "shipping_min_days",
        "shipping_max_days",
        "production_days",
        "total_weight_grams",
        "language",
        "customer_note",
        "paid_at",
        "confirmed_at",
        "shipped_at",
        "cancelled_at",
        "stock_applied_at",
        "confirmation_email_sent_at",
        "admin_email_sent_at",
        "shipped_email_sent_at",
        "email_status",
        "cancellation_requested_at",
        "cancellation_reason",
        "cancellation_decided_at",
        "summary",
    )

    fieldsets = (
        (
            "PEDIDO",
            {"fields": ("number", "customer", "created_at", "summary")},
        ),
        (
            "AVISOS ENVIADOS",
            {
                "fields": ("email_status",),
                "description": (
                    "Quem já foi avisado desta compra. Se algum e-mail falhou, "
                    "reenvie pela lista de pedidos (selecione o pedido e escolha "
                    "a ação de reenvio) — reenviar <strong>não</strong> altera "
                    "pagamento, estoque, situação nem valores."
                ),
            },
        ),
        (
            "SITUAÇÃO",
            {
                "fields": ("status", "payment_status", "fulfillment_status", "paid_at", "confirmed_at"),
                "description": (
                    "Três estados independentes: o pedido, o dinheiro e a produção. "
                    "O pagamento é confirmado pelo webhook da Stripe — alterá-lo aqui "
                    "não cobra nem devolve nada."
                ),
            },
        ),
        (
            "ENTREGA",
            {
                "fields": (
                    "shipping_method_label",
                    "shipping_min_days",
                    "shipping_max_days",
                    "production_days",
                    "total_weight_grams",
                    "tracking_number",
                    "shipped_at",
                    "is_gift",
                    "gift_message",
                )
            },
        ),
        (
            "VALORES",
            {
                "fields": (
                    "currency",
                    "subtotal",
                    "discount_total",
                    "shipping_total",
                    "tax_country",
                    "tax_rate",
                    "tax_total",
                    "prices_include_tax",
                    "total",
                ),
                "description": "Os preços já incluem TVA; o imposto mostrado é a parcela contida no total.",
            },
        ),
        (
            "CANCELAMENTO",
            {
                "fields": (
                    "cancellation_status",
                    "cancellation_reason",
                    "cancellation_requested_at",
                    "cancellation_decision_note",
                    "cancellation_decided_at",
                ),
                "description": (
                    "O cliente solicita; a decisão é sua. Aprovar cancela o pedido — "
                    "e <strong>não</strong> emite reembolso: isso é uma ação separada, "
                    "feita no painel da Stripe."
                ),
            },
        ),
        (
            "OUTROS",
            {
                "fields": (
                    "language",
                    "customer_note",
                    "stock_applied_at",
                    "confirmation_email_sent_at",
                    "admin_email_sent_at",
                    "shipped_email_sent_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        # Pedido nasce de uma compra, não de um formulário do admin: criado
        # aqui, ele não teria pagamento, snapshot nem estoque reservado.
        return False

    def has_delete_permission(self, request, obj=None):
        # Apagar pedido é apagar contabilidade. Cancelar é outra coisa.
        return False

    # -- colunas -----------------------------------------------------------

    @admin.display(description="cliente", ordering="customer__last_name")
    def customer_link(self, obj):
        url = reverse("admin:accounts_customer_change", args=[obj.customer_id])
        return format_html('<a href="{}">{}</a>', url, obj.customer)

    @admin.display(description="total", ordering="total")
    def total_display(self, obj):
        return f"{obj.currency_symbol} {obj.total:.2f}"

    @admin.display(description="cancelamento")
    def cancellation_flag(self, obj):
        if obj.cancellation_status == CancellationStatus.REQUESTED:
            # `format_html` exige argumento: sem nenhum ele levanta TypeError, e
            # a listagem inteira caia justamente quando havia um cancelamento
            # para decidir -- a hora em que ela mais precisa abrir.
            return format_html('<strong style="color:#b45309">{}</strong>', "solicitado")
        if obj.cancellation_status == CancellationStatus.APPROVED:
            return "aprovado"
        if obj.cancellation_status == CancellationStatus.REFUSED:
            return "recusado"
        return "—"

    @admin.display(description="presente", boolean=True)
    def gift_flag(self, obj):
        return obj.is_gift

    # -- situação dos e-mails ----------------------------------------------

    def email_rows(self, obj):
        """(rótulo, data de envio, se era esperado) de cada um dos três."""
        return (
            ("Confirmação ao cliente", obj.confirmation_email_sent_at, obj.is_paid),
            ("Ordem de produção", obj.admin_email_sent_at, obj.is_paid),
            (
                "Aviso de envio",
                obj.shipped_email_sent_at,
                obj.fulfillment_status
                in {FulfillmentStatus.SHIPPED, FulfillmentStatus.DELIVERED},
            ),
        )

    @admin.display(description="avisos")
    def email_status(self, obj):
        """Os três e-mails do pedido, com data — sem precisar abrir log nenhum.

        "Ainda não enviado" e "não enviado" são coisas diferentes: o aviso de
        envio de um pedido que não saiu ainda está certo em não ter ido. O que
        precisa de atenção é o e-mail que **já deveria** ter saído e não saiu —
        e é só esse que aparece em vermelho.
        """
        linhas = []
        for rotulo, quando, esperado in self.email_rows(obj):
            if quando is not None:
                linhas.append(
                    ("#1a7f37", "✓", rotulo, f"enviado em {localtime(quando):%d/%m/%Y %H:%M}")
                )
            elif esperado:
                linhas.append(("#b42318", "⚠", rotulo, "NÃO enviado — reenvie"))
            else:
                linhas.append(("#6b7280", "—", rotulo, "ainda não enviado"))

        return format_html(
            '<table style="border:0">{}</table>',
            format_html_join(
                "",
                '<tr><td style="padding:2px 8px 2px 0;color:{}">{}</td>'
                '<td style="padding:2px 12px 2px 0"><b>{}</b></td>'
                '<td style="padding:2px 0;color:{}">{}</td></tr>',
                (
                    (cor, marca, rotulo, cor, texto)
                    for cor, marca, rotulo, texto in linhas
                ),
            ),
        )

    @admin.display(description="avisos")
    def emails_flag(self, obj):
        """Na listagem, só o que está pendente e deveria ter saído."""
        faltando = [
            rotulo for rotulo, quando, esperado in self.email_rows(obj)
            if quando is None and esperado
        ]
        if not faltando:
            return format_html('<span style="color:#1a7f37">{}</span>', "✓")
        return format_html(
            '<span style="color:#b42318" title="{}">⚠ {}</span>',
            ", ".join(faltando),
            len(faltando),
        )

    @admin.display(description="resumo")
    def summary(self, obj):
        """O pedido em três linhas, no topo da tela.

        Montado com ``format_html_join`` e não com concatenação de strings: o
        que entra aqui é nome de produto e texto de personalização digitados
        pelo cliente, e eles têm que ser escapados.
        """
        items = list(obj.items.all())
        if not items:
            return "—"

        return format_html(
            '<ul style="margin:0;padding-left:1.1rem">{}</ul>',
            format_html_join(
                "",
                "<li>{} × {}{}</li>",
                (
                    (
                        item.quantity,
                        item.description,
                        f" — {item.personalization_text}" if item.personalization_text else "",
                    )
                    for item in items
                ),
            ),
        )

    # -- ações -------------------------------------------------------------

    @admin.action(description="Marcar como enviado (envia o e-mail de rastreio)")
    def action_mark_shipped(self, request, queryset):
        done = sum(
            1 for order in queryset if services.mark_shipped(order, order.tracking_number, request.user)
        )
        self.message_user(
            request, _("%(count)s pedido(s) marcado(s) como enviado(s).") % {"count": done},
            messages.SUCCESS if done else messages.WARNING,
        )

    def _resend(self, request, queryset, kind, rotulo):
        """Reenvia um e-mail para cada pedido selecionado, e conta o resultado."""
        enviados = 0
        falhas = 0
        for order in queryset:
            if services.resend_email(order, kind, user=request.user):
                enviados += 1
            else:
                falhas += 1

        if enviados:
            self.message_user(
                request,
                _("%(count)s %(rotulo)s reenviado(s).")
                % {"count": enviados, "rotulo": rotulo},
                messages.SUCCESS,
            )
        if falhas:
            self.message_user(
                request,
                _(
                    "%(count)s falharam. O erro foi para o log do servidor — "
                    "confira a configuração de e-mail."
                )
                % {"count": falhas},
                messages.ERROR,
            )

    @admin.action(description="Reenviar confirmação ao cliente")
    def action_resend_confirmation(self, request, queryset):
        self._resend(request, queryset, "confirmation", "confirmação(ões)")

    @admin.action(description="Reenviar ordem de produção (equipe)")
    def action_resend_admin_email(self, request, queryset):
        self._resend(request, queryset, "admin", "ordem(ns) de produção")

    @admin.action(description="Reenviar aviso de envio ao cliente")
    def action_resend_shipped_email(self, request, queryset):
        self._resend(request, queryset, "shipped", "aviso(s) de envio")

    @admin.action(description="Aprovar cancelamento solicitado")
    def action_approve_cancellation(self, request, queryset):
        done = sum(1 for order in queryset if services.approve_cancellation(order, user=request.user))
        self.message_user(
            request,
            _("%(count)s cancelamento(s) aprovado(s). O reembolso, se houver, é feito na Stripe.")
            % {"count": done},
            messages.SUCCESS if done else messages.WARNING,
        )

    @admin.action(description="Recusar cancelamento solicitado")
    def action_refuse_cancellation(self, request, queryset):
        done = sum(1 for order in queryset if services.refuse_cancellation(order, user=request.user))
        self.message_user(
            request, _("%(count)s cancelamento(s) recusado(s).") % {"count": done},
            messages.SUCCESS if done else messages.WARNING,
        )

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, OrderNote) and instance.author_id is None:
                instance.author = request.user
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

    def save_model(self, request, obj, form, change):
        """Mudar a produção pela tela passa pelo mesmo controle da ação.

        Antes, mexer no campo aqui gravava e pronto: o histórico não registrava
        quem tinha mexido nem de onde para onde, e nada impedia "Entregue"
        virar "Não iniciado" por um clique errado (AUD-04).
        """
        anterior = (
            Order.objects.filter(pk=obj.pk).values("fulfillment_status").first()
            if obj.pk
            else None
        )
        antes = anterior["fulfillment_status"] if anterior else None
        depois = obj.fulfillment_status

        if antes is not None and antes != depois:
            # O serviço precisa do objeto ainda no estado anterior: é de lá que
            # ele tira o "de onde" do registro.
            obj.fulfillment_status = antes
            try:
                services.change_fulfillment_status(obj, depois, user=request.user)
            except services.StatusChangeRefused as erro:
                self.message_user(request, str(erro), messages.ERROR)
                depois = antes
            obj.fulfillment_status = depois

        super().save_model(request, obj, form, change)

        became_shipped = antes != FulfillmentStatus.SHIPPED and depois == FulfillmentStatus.SHIPPED
        if antes is not None and became_shipped:
            services.mark_shipped(obj, obj.tracking_number, request.user)


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "order", "provider", "status", "amount", "currency", "paid_at")
    list_filter = ("provider", "status", "created_at")
    search_fields = ("order__number", "provider_session_id", "provider_payment_id")
    list_select_related = ("order",)
    readonly_fields = tuple(field.name for field in Payment._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OrderNote)
class OrderNoteAdmin(admin.ModelAdmin):
    list_display = ("created_at", "order", "author", "preview")
    list_filter = ("created_at",)
    search_fields = ("order__number", "body")
    autocomplete_fields = ("order",)

    @admin.display(description="nota")
    def preview(self, obj):
        return obj.body[:80]

    def save_model(self, request, obj, form, change):
        if obj.author_id is None:
            obj.author = request.user
        super().save_model(request, obj, form, change)


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "order", "event", "message", "created_by", "is_customer_visible")
    list_filter = ("event", "is_customer_visible", "created_at")
    search_fields = ("order__number", "message")
    list_select_related = ("order", "created_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Só consulta: é a prova de que um evento já foi processado."""

    list_display = ("received_at", "provider", "event_type", "event_id", "order", "processed_at")
    list_filter = ("provider", "event_type", "received_at")
    search_fields = ("event_id", "order__number")
    list_select_related = ("order",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OrderNumberSequence)
class OrderNumberSequenceAdmin(admin.ModelAdmin):
    list_display = ("year", "last_number")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(total=Count("pk"))
