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

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import ActionLocation
from django.db.models import Count
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _

from apps.orders import services
from apps.orders.emails import admin_recipients, send_transfer_details_email
from apps.orders.payments import checkout_methods
from apps.core.admin_mixins import UniqueLanguageInlineFormSet
from apps.orders.models import (
    BankAccount,
    CancellationSettings,
    CancellationSettingsTranslation,
    CancellationStatus,
    FulfillmentStatus,
    Order,
    OrderAddress,
    OrderEvent,
    OrderItem,
    OrderNote,
    OrderNumberSequence,
    OrderStatus,
    OrderStatusHistory,
    Payment,
    PaymentProof,
    PaymentStatus,
    RefundStatus,
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


class CancellationDecisionForm(forms.Form):
    """A decisão de cancelamento, com o que ela precisa saber antes de agir.

    A resposta ao cliente é opcional na aprovação e praticamente obrigatória na
    recusa — é ela que vai no e-mail. O estoque só aparece quando o plano exige
    uma decisão humana; nos outros casos a regra já sabe o que fazer, e
    perguntar seria fingir uma escolha que não existe.
    """

    resposta = forms.CharField(
        label="resposta ao cliente",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
        help_text=(
            "Vai no e-mail com estas palavras. Na recusa, é o que o cliente lê "
            "como motivo."
        ),
    )
    restore_stock = forms.ChoiceField(
        label="as peças voltam ao estoque?",
        required=False,
        choices=(
            ("", "—"),
            ("1", "Sim, devolver ao saldo"),
            ("0", "Não, as peças foram perdidas"),
        ),
    )

    @property
    def decisao_de_estoque(self):
        escolha = self.cleaned_data.get("restore_stock")
        if escolha == "":
            return None
        return escolha == "1"


class RefundForm(forms.Form):
    """Um reembolso que já saiu do banco, sendo registrado."""

    amount = forms.DecimalField(
        label="valor devolvido",
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Só o que saiu agora. Reembolsos parciais somam-se.",
    )
    reference = forms.CharField(
        label="referência da transferência",
        max_length=140,
        help_text=(
            "O identificador que o banco deu à devolução. É obrigatório: é por "
            "ele que o sistema sabe distinguir um segundo reembolso de um "
            "segundo clique no mesmo."
        ),
    )


#: Os nomes das sete seções, numa constante porque três lugares precisam
#: deles: os `fieldsets`, a ordem da página e o `get_fieldsets`, que esconde as
#: que não têm o que mostrar. Texto repetido em três lugares diverge no dia em
#: que alguém renomeia um.
SECAO_RESUMO = "1. RESUMO"
SECAO_PAGAMENTO = "2. PAGAMENTO"
SECAO_PRODUCAO = "3. PRODUÇÃO E ENTREGA"
SECAO_ITENS = "4. ITENS E VALORES"
SECAO_CANCELAMENTO = "5. CANCELAMENTO E REEMBOLSO"
SECAO_HISTORICO = "6. HISTÓRICO"
SECAO_COMUNICACOES = "7. COMUNICAÇÕES"


#: A ordem da tela do pedido, de cima para baixo.
#:
#: Texto = nome do fieldset; classe = model do inline. É a única lista que
#: manda: trocar duas linhas aqui muda a página, sem tocar em `fieldsets`, em
#: `inlines` nem no template.
#:
#: A ordem é a das perguntas que a operação faz, nesta sequência: de que pedido
#: se trata e em que pé ele está, o dinheiro entrou, a peça saiu, o que foi
#: comprado, há cancelamento a decidir, o que já aconteceu, e o que o cliente
#: recebeu.
ORDER_SECTION_ORDER = (
    SECAO_RESUMO,
    SECAO_PAGAMENTO,
    SECAO_PRODUCAO,
    SECAO_ITENS,
    SECAO_CANCELAMENTO,
    SECAO_HISTORICO,
    OrderNote,  # as notas internas, logo abaixo do histórico
    SECAO_COMUNICACOES,
)


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
    #: Só as notas — a única coisa que se **escreve** nesta tela.
    #:
    #: Itens, endereços, pagamentos, comprovantes e histórico eram cinco
    #: `inlines` somente-leitura, ou seja, cinco tabelas de formulário que
    #: ninguém preenchia, cada uma num bloco próprio. Viraram painéis dentro da
    #: seção de negócio a que pertencem.
    inlines = (OrderNoteInline,)
    actions = (
        "action_confirm_payment",
        "action_send_transfer_details",
        "action_resend_transfer_details",
        "action_mark_shipped",
        "action_resend_confirmation",
        "action_resend_admin_email",
        "action_resend_shipped_email",
        "action_approve_cancellation",
        "action_refuse_cancellation",
        "action_register_refund",
    )

    # Documento contábil: o que aconteceu não se reescreve.
    # O que **não** está aqui é a lista curta do que se pode editar num pedido:
    # a produção (que passa pelo serviço), o rastreio e os campos de presente.
    # Todo o resto é consequência de uma decisão registrada, e decisão se toma
    # por ação — não trocando um `select` e clicando em Salvar.
    #: Documento contábil: o que aconteceu não se reescreve.
    #:
    #: A tela inteira é somente-leitura com **três** exceções, e elas são a
    #: lista completa do que um operador pode digitar num pedido: a produção
    #: (que passa pelo serviço e recusa `HALTED`), o rastreio e os dois campos
    #: de presente. Todo o resto é consequência de uma decisão registrada, e
    #: decisão se toma por ação — não trocando um `select` e clicando em Salvar.
    #:
    #: Os painéis também entram aqui: para o Django, um método de exibição só
    #: pode aparecer num `fieldset` se estiver nesta lista.
    readonly_fields = (
        "cancelamento_aviso",
        "resumo_painel",
        "pagamento_painel",
        "producao_painel",
        "itens_e_valores",
        "cancelamento_painel",
        "historico_painel",
        "comunicacoes_painel",
    )

    #: Sete seções, uma por área de negócio. Eram dezesseis blocos — dez
    #: `fieldsets` e seis `inlines` —, com o pagamento em dois lugares, os
    #: valores em nove linhas separadas e o comprovante escondido no fim da
    #: página. Cada seção aqui é um painel; a ordem mora em
    #: `ORDER_SECTION_ORDER`.
    fieldsets = (
        (
            SECAO_RESUMO,
            {"fields": ("cancelamento_aviso", "resumo_painel")},
        ),
        (
            SECAO_PAGAMENTO,
            {
                "fields": ("pagamento_painel",),
                "description": (
                    "O dinheiro que entra, e o comprovante junto dele. "
                    "<b>Confirmar pagamento</b> é uma ação, não um campo: ela baixa "
                    "o estoque, avisa o cliente e manda a ordem de produção."
                ),
            },
        ),
        (
            SECAO_PRODUCAO,
            {
                "fields": ("producao_painel", "fulfillment_status", "tracking_number",
                           "is_gift", "gift_message"),
                "description": (
                    "<b>Produção</b> é o único estado que se escolhe nesta tela. "
                    "“Interrompido” não aparece na lista porque ele vem da aprovação "
                    "de um cancelamento — e um pedido interrompido não volta para a fila."
                ),
            },
        ),
        (
            SECAO_ITENS,
            {"fields": ("itens_e_valores",)},
        ),
        (
            SECAO_CANCELAMENTO,
            {
                "fields": ("cancelamento_painel",),
                "description": (
                    "Cancelamento e reembolso são coisas diferentes: aprovar um "
                    "cancelamento <b>abre</b> o reembolso, mas quem devolve o dinheiro "
                    "é uma pessoa, no banco — e o registro dela é a ação "
                    "<b>“Registrar reembolso já feito”</b>."
                ),
            },
        ),
        (
            SECAO_HISTORICO,
            {"fields": ("historico_painel",)},
        ),
        (
            SECAO_COMUNICACOES,
            {"fields": ("comunicacoes_painel",)},
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
        """(rótulo, data de envio, se era esperado) de cada um dos três.

        "Esperado" leva o cancelamento em conta: um pedido cancelado que
        recebeu o pagamento atrasado **não** devia ter mandado "compra
        confirmada" nem ordem de produção. Sem esta condição o painel os
        marcava em vermelho e pedia o reenvio — de e-mails que anunciariam ao
        cliente uma compra que ele cancelou.
        """
        vendido = obj.is_paid and not obj.is_cancelled
        return (
            ("Confirmação ao cliente", obj.confirmation_email_sent_at, vendido),
            ("Ordem de produção", obj.admin_email_sent_at, vendido),
            (
                "Aviso de envio",
                obj.shipped_email_sent_at,
                obj.fulfillment_status
                in {FulfillmentStatus.SHIPPED, FulfillmentStatus.DELIVERED},
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

    @admin.display(description="cancelamento")
    def cancelamento_aviso(self, obj):
        """Um pedido de cancelamento esperando decisão não pode passar batido.

        Fica no topo da tela, e não lá embaixo na seção CANCELAMENTO: quem abre
        o pedido depois do e-mail precisa ver a pendência antes de qualquer
        outra coisa. Quando não há solicitação em aberto, some — um aviso que
        aparece sempre deixa de ser aviso.
        """
        if obj.cancellation_status != CancellationStatus.REQUESTED:
            return ""
        return format_html(
            '<div style="padding:12px 14px;border-radius:10px;'
            'background:var(--jd-warn-bg,#fdf3d7);border:1px solid #e0c98a">'
            "<b>⚠ {}</b><br>{}<br><span>{}</span></div>",
            _("Cancelamento solicitado — aguardando decisão da equipe"),
            _("Motivo do cliente: %(motivo)s")
            % {"motivo": obj.cancellation_reason or _("(não informado)")},
            _("Use as ações “Aprovar” ou “Recusar cancelamento”. Aprovar avisa o cliente."),
        )



    @staticmethod
    def _chip(texto, tom=""):
        """Um selo de estado. `tom` é "", "ok", "warn", "bad" ou "info"."""
        return format_html('<span class="jd-chip {}">{}</span>', f"jd-{tom}" if tom else "", texto)

    @staticmethod
    def _linhas(pares):
        """Rótulo e valor em grade — duas colunas que o CSS reflui quando aperta.

        Pares com valor vazio somem: mostrar "Rastreio: —" em vinte pedidos que
        não têm rastreio é ruído que esconde os que têm.
        """
        visiveis = [
            (rotulo, valor)
            for rotulo, valor in pares
            if valor not in (None, "", "—", 0)
        ]
        if not visiveis:
            return ""
        return format_html(
            '<div class="jd-grid">{}</div>',
            format_html_join(
                "",
                '<div class="jd-cell"><span class="jd-label">{}</span><span class="jd-value">{}</span></div>',
                visiveis,
            ),
        )

    @staticmethod
    def _tabela(colunas, linhas, vazio=""):
        """Uma tabela compacta. `linhas` é uma lista de tuplas já formatadas."""
        if not linhas:
            return format_html('<p class="jd-empty">{}</p>', vazio) if vazio else ""
        return format_html(
            '<div class="jd-scroll"><table class="jd-table"><thead><tr>{}</tr></thead>'
            "<tbody>{}</tbody></table></div>",
            format_html_join("", "<th>{}</th>", ((c,) for c in colunas)),
            format_html_join(
                "",
                "<tr>{}</tr>",
                (
                    (format_html_join("", "<td>{}</td>", ((celula,) for celula in linha)),)
                    for linha in linhas
                ),
            ),
        )

    @staticmethod
    def _quando(momento):
        return localtime(momento).strftime("%d/%m/%Y %H:%M") if momento else ""

    def _acoes(self, acoes):
        """Os atalhos da seção — só os que fazem sentido para este pedido.

        Botão que existe mas não funciona ensina o operador a desconfiar da
        tela. Quando nenhuma ação cabe no estado atual, a faixa não aparece.
        """
        acoes = [a for a in acoes if a]
        if not acoes:
            return ""
        return format_html(
            '<p class="jd-actions"><span class="jd-label">{}</span>{}</p>',
            _("Ações desta seção, na lista de pedidos:"),
            format_html_join("", '<span class="jd-action">{}</span>', ((a,) for a in acoes)),
        )

    # -- 1. resumo ---------------------------------------------------------

    @admin.display(description="")
    def resumo_painel(self, obj):
        """O pedido inteiro numa olhada: quem, quando, quanto, e em que pé está.

        Os cinco estados ficam lado a lado de propósito. Eles respondem a
        perguntas diferentes — o pedido está de pé? o dinheiro entrou? a peça
        saiu? há um cancelamento? o dinheiro voltou? — e ver as cinco respostas
        juntas é o que permite entender a situação sem rolar a página.
        """
        selos = [self._chip(obj.get_status_display(), self._tom_do_pedido(obj))]
        selos.append(self._chip(obj.get_payment_status_display(), self._tom_do_pagamento(obj)))
        if obj.fulfillment_status != FulfillmentStatus.NOT_STARTED or not obj.is_cancelled:
            selos.append(
                self._chip(
                    obj.get_fulfillment_status_display(),
                    "bad" if obj.fulfillment_status == FulfillmentStatus.HALTED else "info",
                )
            )
        if obj.cancellation_status != CancellationStatus.NONE:
            selos.append(
                self._chip(
                    obj.get_cancellation_status_display(),
                    "warn" if obj.cancellation_status == CancellationStatus.REQUESTED else "",
                )
            )
        if obj.refund_status != RefundStatus.NONE:
            selos.append(
                self._chip(
                    obj.get_refund_status_display(),
                    "ok" if obj.refund_status == RefundStatus.DONE else "warn",
                )
            )

        cliente = format_html(
            '<a href="{}">{}</a> · <a href="mailto:{}">{}</a>{}',
            reverse("admin:accounts_customer_change", args=[obj.customer_id]),
            obj.customer.full_name or obj.customer.user.username,
            obj.customer.user.email,
            obj.customer.user.email,
            format_html(" · {}", obj.customer.phone) if obj.customer.phone else "",
        )

        return format_html(
            '<div class="jd-panel"><p class="jd-chips">{}</p>{}{}</div>',
            format_html_join(" ", "{}", ((selo,) for selo in selos)),
            self._linhas(
                [
                    (_("Número"), format_html("<b>{}</b>", obj.number)),
                    (_("Feito em"), self._quando(obj.created_at)),
                    (_("Cliente"), cliente),
                    (
                        _("Total"),
                        format_html("<b>{} {}</b>", obj.currency_symbol, f"{obj.total:.2f}"),
                    ),
                    (_("Idioma do pedido"), (obj.language or "").upper()),
                    (_("Itens"), obj.item_count),
                ]
            ),
            format_html(
                '<p class="jd-note"><span class="jd-label">{}</span>{}</p>',
                _("Observação do cliente"),
                obj.customer_note,
            )
            if obj.customer_note
            else "",
        )

    @staticmethod
    def _tom_do_pedido(obj):
        if obj.status == OrderStatus.CANCELLED:
            return "bad"
        if obj.status == OrderStatus.COMPLETED:
            return "ok"
        if obj.status == OrderStatus.PENDING:
            return "warn"
        return "info"

    @staticmethod
    def _tom_do_pagamento(obj):
        return {
            PaymentStatus.PAID: "ok",
            PaymentStatus.PENDING: "warn",
            PaymentStatus.FAILED: "bad",
        }.get(obj.payment_status, "")

    # -- 2. pagamento ------------------------------------------------------

    @staticmethod
    def _rotulo_do_metodo(obj):
        """O nome que o cliente viu no checkout, não o código do banco de dados.

        `payment_method` guarda o código (`transfer`, `card`) e não tem
        `choices` de propósito — a lista de formas é do registro de pagamentos,
        não do model. O rótulo vem de lá.
        """
        if not obj.payment_method:
            return _("não registrada")
        for metodo in checkout_methods():
            if metodo.code == obj.payment_method:
                return metodo.label
        return obj.payment_method

    @admin.display(description="")
    def pagamento_painel(self, obj):
        """Tudo o que envolve dinheiro que entra, num bloco só.

        Inclusive o **comprovante**: ele vivia numa seção própria, longe do
        estado do pagamento, e conferir um pagamento por transferência exigia
        olhar dois lugares da página ao mesmo tempo.
        """
        conta = obj.bank_details
        pago = obj.payment_status == PaymentStatus.PAID

        dados = [
            (_("Método"), self._rotulo_do_metodo(obj)),
            (_("Situação"), self._chip(obj.get_payment_status_display(), self._tom_do_pagamento(obj))),
            (_("Valor"), format_html("<b>{} {}</b>", obj.currency_symbol, f"{obj.total:.2f}")),
            (_("Pago em"), self._quando(obj.paid_at)),
            (_("Estoque baixado em"), self._quando(obj.stock_applied_at)),
        ]
        if conta is not None:
            dados.append((_("Conta usada"), format_html("{} · {}", conta.beneficiary, conta.masked_iban)))
            dados.append((_("Comunicação"), obj.number))
        if obj.transfer_details_sent_at:
            dados.append((_("Dados enviados em"), self._quando(obj.transfer_details_sent_at)))
        elif obj.payment_method == "transfer" and not pago:
            dados.append(
                (_("Dados bancários"), self._chip(_("ainda NÃO enviados ao cliente"), "warn"))
            )

        acoes = []
        if not pago:
            acoes.append(_("Confirmar pagamento (dinheiro na conta)"))
        if obj.payment_method == "transfer":
            acoes.append(
                _("Reenviar dados para transferência")
                if obj.transfer_details_sent_at
                else _("Enviar dados para transferência")
            )
        if obj.refund_status in {RefundStatus.PENDING, RefundStatus.PARTIAL}:
            acoes.append(_("Registrar reembolso já feito"))

        return format_html(
            '<div class="jd-panel">{}{}{}{}</div>',
            self._linhas(dados),
            self._bloco_comprovante(obj),
            self._bloco_tentativas(obj),
            self._acoes(acoes),
        )

    def _bloco_comprovante(self, obj):
        """O arquivo que o cliente mandou, com miniatura quando dá para ver."""
        proof = obj.payment_proofs.first()
        if proof is None:
            if obj.payment_method != "transfer":
                return ""
            return format_html(
                '<p class="jd-sub"><span class="jd-label">{}</span>{}</p>',
                _("Comprovante"),
                self._chip(_("nenhum recebido"), "warn" if not obj.is_paid else ""),
            )

        url = reverse("orders:payment_proof_file", args=[proof.pk])
        legenda = format_html(
            "{} · {} · {}",
            proof.original_name or proof.extension.upper(),
            proof.size_display,
            self._quando(proof.created_at),
        )
        if (proof.content_type or "").startswith("image/"):
            visual = format_html(
                '<a href="{}" target="_blank" rel="noopener" title="{}">'
                '<img src="{}" class="jd-thumb" alt="{}"></a>',
                url, _("Abrir em tamanho real"), url, _("Comprovante de pagamento"),
            )
        else:
            visual = format_html(
                '<a class="button" href="{}" target="_blank" rel="noopener">{}</a>',
                url, _("Abrir o comprovante"),
            )
        return format_html(
            '<div class="jd-sub"><span class="jd-label">{}</span>'
            '<div class="jd-proof">{}<span class="jd-value">{}</span></div></div>',
            _("Comprovante enviado pelo cliente"),
            visual,
            legenda,
        )

    def _bloco_tentativas(self, obj):
        """As tentativas de pagamento — o histórico da conta usada."""
        tentativas = list(obj.payments.all()[:10])
        if not tentativas:
            return ""
        linhas = [
            (
                self._quando(p.created_at),
                p.provider,
                p.get_status_display(),
                f"{p.amount:.2f} {p.currency}",
                p.method_label or "—",
                p.provider_payment_id or p.provider_session_id or "—",
            )
            for p in tentativas
        ]
        return format_html(
            '<div class="jd-sub"><span class="jd-label">{}</span>{}</div>',
            _("Tentativas de pagamento"),
            self._tabela(
                [_("Quando"), _("Provedor"), _("Situação"), _("Valor"), _("Meio"), _("Referência")],
                linhas,
            ),
        )

    # -- 3. produção e entrega ---------------------------------------------

    @admin.display(description="")
    def producao_painel(self, obj):
        """Onde a peça está e para onde ela vai — as duas coisas juntas."""
        metodo = obj.shipping_method_label or (
            obj.shipping_method.name if obj.shipping_method_id else ""
        )
        transportadora = (
            obj.shipping_method.carrier.name
            if obj.shipping_method_id and obj.shipping_method.carrier_id
            else ""
        )
        rastreio = ""
        if obj.tracking_number:
            url = obj.tracking_url
            rastreio = (
                format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, obj.tracking_number)
                if url
                else obj.tracking_number
            )

        dados = [
            (_("Produção"), self._chip(
                obj.get_fulfillment_status_display(),
                "bad" if obj.fulfillment_status == FulfillmentStatus.HALTED else "info",
            )),
            (_("Confirmado em"), self._quando(obj.confirmed_at)),
            (_("Enviado em"), self._quando(obj.shipped_at)),
            (_("Entregue em"), self._quando(obj.delivered_at)),
            (_("Transportadora"), transportadora),
            (_("Método"), metodo),
            (_("Rastreio"), rastreio),
            (_("Prazo"), obj.delivery_days_display and _("%(dias)s dias úteis") % {"dias": obj.delivery_days_display}),
            (_("Produção estimada"), obj.production_days and _("%(dias)s dias úteis") % {"dias": obj.production_days}),
            (_("Peso"), obj.total_weight_grams and f"{obj.total_weight_grams} g"),
        ]

        acoes = []
        if obj.fulfillment_status in {FulfillmentStatus.READY, FulfillmentStatus.IN_PRODUCTION}:
            acoes.append(_("Marcar como enviado (envia o e-mail de rastreio)"))

        return format_html(
            '<div class="jd-panel">{}{}{}{}</div>',
            self._linhas(dados),
            self._bloco_enderecos(obj),
            self._bloco_presente(obj),
            self._acoes(acoes),
        )

    def _bloco_enderecos(self, obj):
        """Entrega e faturação lado a lado, como no envelope."""
        blocos = []
        for rotulo, endereco in (
            (_("Entrega"), obj.shipping_address),
            (_("Faturação"), obj.billing_address),
        ):
            if endereco is None:
                continue
            blocos.append(
                (
                    rotulo,
                    format_html_join(
                        mark_safe("<br>"), "{}", ((linha,) for linha in endereco.lines())
                    ),
                )
            )
        if not blocos:
            return ""
        return format_html(
            '<div class="jd-grid jd-addr">{}</div>',
            format_html_join(
                "",
                '<div class="jd-cell"><span class="jd-label">{}</span><span class="jd-value">{}</span></div>',
                blocos,
            ),
        )

    def _bloco_presente(self, obj):
        if not obj.is_gift:
            return ""
        return format_html(
            '<p class="jd-sub"><span class="jd-label">{}</span>{}</p>',
            _("Presente"),
            obj.gift_message or _("sem mensagem"),
        )

    # -- 4. itens e valores ------------------------------------------------

    @admin.display(description="")
    def itens_e_valores(self, obj):
        """O que foi comprado e quanto custou — uma tabela e um rodapé.

        Os valores eram nove campos, um por linha. Aqui eles são o rodapé da
        própria tabela de itens, que é onde se confere um total: ao lado das
        parcelas que o formam.
        """
        linhas = []
        for item in obj.items.select_related("personalization_upload"):
            detalhe = []
            if item.personalization_text:
                detalhe.append(item.personalization_text)
            if item.personalization_notes:
                detalhe.append(item.personalization_notes)
            if item.personalization_upload_id:
                detalhe.append(
                    format_html(
                        '<a href="{}" target="_blank" rel="noopener">{}</a>',
                        reverse("cart:customization_file", args=[item.personalization_upload_id]),
                        _("arquivo enviado"),
                    )
                )
            linhas.append(
                (
                    item.sku or "—",
                    format_html(
                        "<b>{}</b>{}",
                        item.description,
                        format_html('<br><span class="jd-muted">{}</span>',
                                    format_html_join(" · ", "{}", ((d,) for d in detalhe)))
                        if detalhe
                        else "",
                    ),
                    item.get_fulfillment_type_display(),
                    item.quantity,
                    f"{item.unit_price:.2f}",
                    f"{item.total:.2f}",
                )
            )

        totais = [(_("Subtotal"), obj.subtotal)]
        if obj.discount_total:
            totais.append((_("Desconto"), -obj.discount_total))
        totais.append((_("Frete"), obj.shipping_total))
        totais.append((_("TVA %(taxa)s%%") % {"taxa": f"{obj.tax_rate:g}"}, obj.tax_total))

        return format_html(
            '<div class="jd-panel">{}<div class="jd-totais">{}<span class="jd-total">{} {} {}</span></div></div>',
            self._tabela(
                [_("SKU"), _("Produto"), _("Tipo"), _("Qtd."), _("Unitário"), _("Total")],
                linhas,
                vazio=_("Este pedido não tem itens."),
            ),
            format_html_join(
                "",
                '<span class="jd-parcela"><span class="jd-label">{}</span>{} {}</span>',
                ((rotulo, obj.currency_symbol, f"{valor:.2f}") for rotulo, valor in totais),
            ),
            _("Total"),
            obj.currency_symbol,
            f"{obj.total:.2f}",
        )

    # -- 5. cancelamento e reembolso ---------------------------------------

    @admin.display(description="")
    def cancelamento_painel(self, obj):
        """Duas coisas distintas, no mesmo lugar porque andam juntas.

        Distintas de propósito: um cancelamento aprovado **abre** um reembolso,
        mas não o executa, e um reembolso concluído não desfaz o cancelamento.
        Cada um tem o seu estado e o seu bloco.
        """
        blocos = []

        if obj.cancellation_status != CancellationStatus.NONE:
            quem = obj.history.filter(event=OrderEvent.CANCELLATION_REQUESTED).first()
            dados = [
                (_("Situação"), self._chip(
                    obj.get_cancellation_status_display(),
                    "warn" if obj.cancellation_status == CancellationStatus.REQUESTED else "",
                )),
                (_("Solicitado em"), self._quando(obj.cancellation_requested_at)),
                (_("Solicitado por"), (quem.created_by if quem and quem.created_by_id else _("o cliente"))),
                (_("Decidido em"), self._quando(obj.cancellation_decided_at)),
                (
                    _("Decidido por"),
                    obj.cancellation_decided_by
                    if obj.cancellation_decided_by_id
                    else (_("a regra, sem análise humana") if obj.cancellation_auto else ""),
                ),
                (_("Estoque"), obj.get_stock_return_decision_display() if obj.stock_return_decision else ""),
                (_("Estoque devolvido em"), self._quando(obj.stock_returned_at)),
            ]
            blocos.append(
                format_html(
                    '<div class="jd-sub"><span class="jd-label">{}</span>{}{}{}</div>',
                    _("Cancelamento"),
                    self._linhas(dados),
                    format_html(
                        '<p class="jd-note"><span class="jd-label">{}</span>{}</p>',
                        _("Motivo do cliente"), obj.cancellation_reason,
                    ) if obj.cancellation_reason else "",
                    format_html(
                        '<p class="jd-note"><span class="jd-label">{}</span>{}</p>',
                        _("Resposta enviada ao cliente"), obj.cancellation_decision_note,
                    ) if obj.cancellation_decision_note else "",
                )
            )

        if obj.refund_status != RefundStatus.NONE:
            dados = [
                (_("Situação"), self._chip(
                    obj.get_refund_status_display(),
                    "ok" if obj.refund_status == RefundStatus.DONE else "warn",
                )),
                (_("Valor do pedido"), f"{obj.currency_symbol} {obj.total:.2f}"),
                (_("Já devolvido"), f"{obj.currency_symbol} {obj.refunded_amount:.2f}"),
                (_("Falta devolver"), f"{obj.currency_symbol} {obj.refund_due:.2f}"),
                (_("Concluído em"), self._quando(obj.refunded_at)),
                (_("Referência"), obj.refund_reference),
                (_("Registrado por"), obj.refunded_by if obj.refunded_by_id else ""),
            ]
            blocos.append(
                format_html(
                    '<div class="jd-sub"><span class="jd-label">{}</span>{}</div>',
                    _("Reembolso"),
                    self._linhas(dados),
                )
            )

        acoes = []
        if obj.cancellation_status == CancellationStatus.REQUESTED:
            acoes.append(_("Aprovar cancelamento solicitado"))
            acoes.append(_("Recusar cancelamento solicitado"))
        if obj.refund_status in {RefundStatus.PENDING, RefundStatus.PARTIAL}:
            acoes.append(_("Registrar reembolso já feito"))

        return format_html(
            '<div class="jd-panel">{}{}</div>',
            format_html_join("", "{}", ((b,) for b in blocos)),
            self._acoes(acoes),
        )

    # -- 6. histórico ------------------------------------------------------

    @admin.display(description="")
    def historico_painel(self, obj):
        """Tudo o que aconteceu com o pedido, em ordem, numa tabela só.

        Era um inline com cinco colunas de formulário por linha. Aqui é texto:
        o histórico não se edita, e desenhá-lo como formulário sugeria que sim.
        """
        linhas = [
            (
                self._quando(entrada.created_at),
                entrada.get_event_display(),
                entrada.created_by if entrada.created_by_id else _("sistema"),
                entrada.message or "—",
                self._chip(_("cliente vê"), "info") if entrada.is_customer_visible else "",
            )
            for entrada in obj.history.select_related("created_by")
        ]
        return format_html(
            '<div class="jd-panel">{}<p class="jd-foot">{}</p></div>',
            self._tabela(
                [_("Data/hora"), _("Evento"), _("Responsável"), _("Observação"), ""],
                linhas,
                vazio=_("Nada registrado ainda."),
            ),
            _("Última alteração do pedido: %(quando)s") % {"quando": self._quando(obj.updated_at)},
        )

    # -- 7. comunicações ---------------------------------------------------

    #: O que a loja envia, e como se sabe que saiu.
    #:
    #: `marca` é o campo do pedido que guarda a data do envio; `evento` é o
    #: evento do histórico que o serviço grava junto do disparo. Quatro e-mails
    #: têm marca; os outros são deduzidos do evento — ver a nota no rodapé da
    #: seção.
    COMUNICACOES = (
        ("confirmation_email_sent_at", None, "Confirmação da compra", "cliente"),
        ("admin_email_sent_at", None, "Ordem de produção", "equipe"),
        ("shipped_email_sent_at", None, "Aviso de envio", "cliente"),
        ("transfer_details_sent_at", None, "Dados para transferência", "cliente"),
        (None, OrderEvent.PAYMENT_PROOF_RECEIVED, "Comprovante recebido", "equipe"),
        (None, OrderEvent.CANCELLATION_APPROVED, "Cancelamento aprovado", "cliente"),
        (None, OrderEvent.CANCELLATION_REFUSED, "Cancelamento recusado", "cliente"),
        (None, OrderEvent.REFUND_PENDING, "Reembolso iniciado", "cliente"),
        (None, OrderEvent.REFUND_PARTIAL, "Reembolso parcial registrado", "cliente"),
        (None, OrderEvent.REFUNDED, "Reembolso concluído", "cliente"),
        (None, OrderEvent.EMAIL_RESENT, "Reenvio pedido pela equipe", "cliente"),
    )

    @admin.display(description="")
    def comunicacoes_painel(self, obj):
        """As mensagens que saíram — separadas do histórico de propósito.

        Histórico é o que aconteceu com o pedido; comunicações é o que o
        cliente e a equipe receberam. Misturar os dois faz procurar "o cliente
        foi avisado?" dentro de uma lista de mudanças de estado.
        """
        cliente = obj.customer.user.email
        equipe = ", ".join(admin_recipients()) or _("(sem destinatário configurado)")
        linhas = []

        for marca, evento, rotulo, para in self.COMUNICACOES:
            destinatario = cliente if para == "cliente" else equipe
            if marca:
                quando = getattr(obj, marca)
                if quando:
                    linhas.append((quando, rotulo, destinatario, _("registrado no pedido")))
                continue
            for entrada in obj.history.filter(event=evento):
                linhas.append(
                    (entrada.created_at, rotulo, destinatario, _("conforme o histórico"))
                )

        # A solicitação de cancelamento manda dois e-mails — mas só quando ela
        # espera decisão. Aprovada pela regra, ninguém é avisado de que há algo
        # a decidir, porque não há.
        for entrada in obj.history.filter(event=OrderEvent.CANCELLATION_REQUESTED):
            if obj.cancellation_auto:
                continue
            linhas.append((entrada.created_at, _("Solicitação recebida"), cliente, _("conforme o histórico")))
            linhas.append((entrada.created_at, _("AÇÃO NECESSÁRIA: cancelamento"), equipe, _("conforme o histórico")))

        linhas.sort(key=lambda linha: linha[0])
        formatadas = [
            (self._quando(quando), rotulo, destinatario, resultado)
            for quando, rotulo, destinatario, resultado in linhas
        ]

        return format_html(
            '<div class="jd-panel">{}{}<p class="jd-foot">{}</p></div>',
            self._tabela(
                [_("Data/hora"), _("Tipo"), _("Destinatário"), _("Resultado")],
                formatadas,
                vazio=_("Nenhuma mensagem enviada ainda."),
            ),
            self._pendencias(obj),
            _(
                "A loja guarda a data de envio de quatro e-mails; os demais são "
                "deduzidos dos eventos do pedido. Uma falha do provedor num e-mail "
                "deduzido não apareceria aqui — ela vai para o log do servidor."
            ),
        )

    def _pendencias(self, obj):
        """O e-mail que já devia ter saído e não saiu.

        É o único sinal na página de que um cliente ficou sem a confirmação da
        compra dele. Uma tabela do que foi enviado não conta isso — e a falha
        do provedor de e-mail é justamente o caso em que a linha não existe.

        Silêncio quando está tudo em ordem: aviso que aparece sempre deixa de
        ser aviso.
        """
        faltando = [
            rotulo
            for rotulo, quando, esperado in self.email_rows(obj)
            if quando is None and esperado
        ]
        if not faltando:
            return ""
        return format_html(
            '<p class="jd-sub"><span class="jd-label">{}</span>{}<span class="jd-value">{}</span></p>',
            _("Pendências"),
            format_html_join(
                " ",
                '<span class="jd-chip jd-bad">{}</span>',
                ((f"{rotulo} — {_('NÃO enviado')}",) for rotulo in faltando),
            ),
            _("Reenvie pela lista de pedidos: reenviar não altera pagamento, estoque nem valores."),
        )

    # -- ações -------------------------------------------------------------

    @admin.action(
        permissions=["change"],
        description="Confirmar pagamento (dinheiro na conta)",
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def action_confirm_payment(self, request, queryset):
        """Marca o pagamento como recebido — e faz tudo o que isso implica.

        **Não é o campo `pagamento` do formulário.** Trocar aquele select grava
        um estado e mais nada: o estoque não baixa, o cliente não é avisado e a
        oficina não recebe a ordem de produção. O pedido fica "pago" e parado.

        Aqui quem executa é `services.confirm_payment`, a mesma rotina que o
        webhook da Stripe usa — e é de propósito que seja a mesma. Ela é
        idempotente por etapa: pagamento, estoque e e-mails se protegem cada um
        por sua marca no pedido, então confirmar duas vezes não baixa estoque
        duas vezes nem manda a ordem de produção de novo.

        Há uma tela de confirmação no meio porque isto é dinheiro: o clique
        avisa o cliente de que o pagamento entrou e manda a peça para a fila de
        impressão. Um engano aqui não se desfaz com um segundo clique.
        """
        elegiveis, recusados = self._split_confirmable(queryset)

        if not elegiveis:
            self.message_user(
                request,
                _(
                    "Nenhum dos pedidos escolhidos pode ser confirmado: "
                    "eles já estão pagos ou foram cancelados."
                ),
                messages.WARNING,
            )
            return None

        if request.POST.get("confirmar") == "1":
            return self._confirm_payments(request, elegiveis)

        return render(
            request,
            "admin/orders/confirm_payment.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Confirmar pagamento"),
                "opts": self.opts,
                "orders": elegiveis,
                "refused": recusados,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "action_name": "action_confirm_payment",
                "action_field": (
                    "CHANGE_FORM-action"
                    if "CHANGE_FORM-action" in request.POST
                    else "action"
                ),
            },
        )

    @staticmethod
    def _split_confirmable(queryset):
        """(o que dá para confirmar, o que não dá).

        Elegível é o pedido cujo dinheiro ainda não entrou e que continua vivo.
        Não é restrito a transferência: um pedido de cartão que ficou preso
        porque o webhook não chegou tem exatamente o mesmo remédio, e negá-lo
        obrigaria alguém a mexer no banco à mão.
        """
        elegiveis, recusados = [], []
        for order in queryset:
            # Cancelado **não** é motivo para recusar: com transferência, o
            # dinheiro do cliente cai na conta dias depois, e o cancelamento
            # não o faz voltar sozinho. Registrá-lo é o que abre o reembolso —
            # e sem esta ação não havia caminho nenhum para isso, porque
            # transferência não tem webhook.
            #
            # Quem cuida do resto é o `confirm_payment`: em pedido cancelado
            # ele não baixa estoque, não manda "compra confirmada" e abre o
            # reembolso.
            elegiveis.append(order) if order.payment_status != PaymentStatus.PAID else recusados.append(order)
        return elegiveis, recusados

    def _confirm_payments(self, request, orders):
        """Executa a rotina de negócio, uma vez por pedido."""
        confirmados, repetidos = 0, 0
        for order in orders:
            # O rótulo do meio de pagamento vem do pedido: é o que o cliente
            # escolheu no checkout, e é o que vai aparecer no recibo.
            if services.confirm_payment(order, user=request.user):
                confirmados += 1
            else:
                # Nada novo aconteceu — o pedido já tinha passado por tudo.
                repetidos += 1

        if confirmados:
            self.message_user(
                request,
                _(
                    "%(count)s pagamento(s) confirmado(s): estoque baixado, "
                    "cliente avisado e ordem de produção enviada."
                )
                % {"count": confirmados},
                messages.SUCCESS,
            )
        if repetidos:
            self.message_user(
                request,
                _("%(count)s já estavam confirmados; nada foi refeito.")
                % {"count": repetidos},
                messages.INFO,
            )
        return None

    @admin.action(
        permissions=["change"],
        description="Enviar dados bancários ao cliente",
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def action_send_transfer_details(self, request, queryset):
        """Manda ao cliente a conta escolhida — **uma vez** por pedido.

        A ação não envia nada de imediato: ela abre uma tela intermediária com
        a lista de contas, o mesmo desenho que o Django usa para confirmar uma
        exclusão. É esse passo que torna o envio uma decisão, e não um efeito
        colateral de ter clicado no lugar errado da lista.

        Pedido que **já recebeu** os dados fica de fora, e a mensagem diz
        quantos ficaram. Desde que o checkout passou a mandar os dados sozinho,
        todo pedido por transferência chega aqui já enviado — e clicar nesta
        ação por hábito mandava um segundo e-mail idêntico ao cliente e escrevia
        um segundo evento igual no histórico.

        Reenviar continua possível: é a ação **"Reenviar dados bancários"**,
        separada de propósito. O acidente que se quer evitar é o clique
        distraído, não o reenvio deliberado.

        Só o que dá para enviar entra na lista de contas: conta ativa e com
        titular e IBAN preenchidos (`BankAccount.objects.usable()`).
        """
        pendentes = [o for o in queryset if o.transfer_details_sent_at is None]
        ja_enviados = len(queryset) - len(pendentes)

        if ja_enviados:
            self.message_user(
                request,
                _(
                    "%(count)s pedido(s) já haviam recebido os dados e ficaram de fora. "
                    "Para mandar de novo, use “Reenviar dados bancários ao cliente”."
                )
                % {"count": ja_enviados},
                messages.WARNING,
            )
        if not pendentes:
            return None
        queryset = pendentes

        return self._transfer_details_flow(request, queryset)

    def _transfer_details_flow(self, request, queryset):
        """A tela de escolha da conta e o envio. Compartilhada pelas duas ações."""
        contas = list(BankAccount.objects.usable())
        if not contas:
            self.message_user(
                request,
                _(
                    "Nenhuma conta bancária cadastrada e ativa. "
                    "Cadastre uma em Pedidos › contas bancárias."
                ),
                messages.ERROR,
            )
            return None

        escolhida = request.POST.get("conta")
        if escolhida:
            conta = BankAccount.objects.usable().filter(pk=escolhida).first()
            if conta is None:
                # O id veio do navegador; a conta é procurada de novo, entre as
                # que podem ser usadas. Uma conta desativada no meio do caminho
                # não passa por estar num `<option>` antigo.
                self.message_user(
                    request, _("Escolha uma conta bancária válida."), messages.ERROR
                )
                return None
            return self._send_transfer_details(request, queryset, conta)

        return render(
            request,
            "admin/orders/send_transfer_details.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Enviar dados bancários ao cliente"),
                "opts": self.opts,
                "orders": queryset,
                "accounts": contas,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                # A tela devolve o POST para a **mesma** ação que a abriu:
                # voltar pela outra faria o reenvio cair na ação que pula quem
                # já recebeu, e o clique não faria nada.
                "action_name": request.POST.get("action")
                or request.POST.get("CHANGE_FORM-action")
                or "action_send_transfer_details",
                # O nome do campo muda com o lugar de onde a ação foi
                # disparada: na lista o Django lê "action"; na tela do
                # pedido, o formulário vem com o prefixo do
                # ActionLocation ("CHANGE_FORM-action"). O formulário
                # abaixo posta para a URL de onde veio, então precisa
                # devolver a chave que **aquela** view procura — senão o
                # POST vira uma tentativa de salvar o pedido.
                "action_field": (
                    "CHANGE_FORM-action"
                    if "CHANGE_FORM-action" in request.POST
                    else "action"
                ),
            },
        )

    @admin.action(
        permissions=["change"],
        description="Reenviar dados bancários ao cliente (mesmo já enviados)",
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def action_resend_transfer_details(self, request, queryset):
        """O reenvio deliberado — o cliente perdeu o e-mail, caiu no spam.

        É a mesma tela e o mesmo envio da ação acima; o que muda é não pular
        quem já recebeu. Existir separada é o ponto: o reenvio passa a ser uma
        coisa que alguém **escolhe**, e não o que acontece quando se clica duas
        vezes no lugar errado.
        """
        return self._transfer_details_flow(request, queryset)

    def _send_transfer_details(self, request, queryset, conta):
        """Envia, marca o pedido e registra qual conta foi usada."""
        enviados = 0
        falhas = 0
        for order in queryset:
            # Quem registra no histórico é o próprio envio — ele acontece por
            # dois caminhos e os dois têm que deixar o mesmo rastro. Daqui vai
            # só o que o envio não sabe: **quem** clicou.
            if send_transfer_details_email(order, conta, user=request.user):
                enviados += 1
            else:
                falhas += 1

        if enviados:
            self.message_user(
                request,
                _("Dados bancários enviados para %(count)s pedido(s).") % {"count": enviados},
                messages.SUCCESS,
            )
        if falhas:
            self.message_user(
                request,
                _(
                    "%(count)s não saíram. O erro foi para o log do servidor — "
                    "confira a configuração de e-mail."
                )
                % {"count": falhas},
                messages.ERROR,
            )
        return None

    @admin.action(permissions=["change"], description="Marcar como enviado (envia o e-mail de rastreio)")
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

    @admin.action(permissions=["change"], description="Reenviar confirmação ao cliente")
    def action_resend_confirmation(self, request, queryset):
        self._resend(request, queryset, "confirmation", "confirmação(ões)")

    @admin.action(permissions=["change"], description="Reenviar ordem de produção (equipe)")
    def action_resend_admin_email(self, request, queryset):
        self._resend(request, queryset, "admin", "ordem(ns) de produção")

    @admin.action(permissions=["change"], description="Reenviar aviso de envio ao cliente")
    def action_resend_shipped_email(self, request, queryset):
        self._resend(request, queryset, "shipped", "aviso(s) de envio")

    @admin.action(
        permissions=["change"],
        description="Aprovar cancelamento solicitado",
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def action_approve_cancellation(self, request, queryset):
        """Aprovar é **uma** operação, e ela tem consequências.

        Para a produção, decide o estoque, fecha a cobrança ou abre o reembolso,
        recalcula o pedido e avisa o cliente — tudo numa transação. Por isso há
        uma tela no meio: quando a peça já entrou em produção, ninguém além de
        quem está olhando para ela sabe se ela volta ao saldo ou virou sucata.
        """
        return self._decidir_cancelamento(request, queryset, aprovar=True)

    @admin.action(
        permissions=["change"],
        description="Recusar cancelamento solicitado",
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def action_refuse_cancellation(self, request, queryset):
        """Recusar também é uma decisão comunicada: o texto vai no e-mail."""
        return self._decidir_cancelamento(request, queryset, aprovar=False)

    def _decidir_cancelamento(self, request, queryset, *, aprovar: bool):
        pendentes = [
            order for order in queryset
            if order.cancellation_status == CancellationStatus.REQUESTED
        ]
        if not pendentes:
            self.message_user(
                request,
                _("Nenhum dos pedidos escolhidos tem cancelamento aguardando decisão."),
                messages.WARNING,
            )
            return None

        planos = {order.pk: services.cancellation_plan(order) for order in pendentes}
        precisa_estoque = aprovar and any(p.needs_stock_decision for p in planos.values())

        if request.POST.get("confirmar") == "1":
            form = CancellationDecisionForm(request.POST)
            if form.is_valid():
                escolha = form.decisao_de_estoque
                if precisa_estoque and escolha is None:
                    form.add_error(
                        "restore_stock",
                        _("Estes pedidos já entraram em produção: diga o que fazer com as peças."),
                    )
                else:
                    return self._aplicar_decisao(
                        request, pendentes, aprovar, form.cleaned_data["resposta"], escolha
                    )
        else:
            form = CancellationDecisionForm()

        return render(
            request,
            "admin/orders/cancellation_decision.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Aprovar cancelamento") if aprovar else _("Recusar cancelamento"),
                "opts": self.opts,
                "orders": pendentes,
                "planos": planos,
                "aprovar": aprovar,
                "precisa_estoque": precisa_estoque,
                "form": form,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "action_name": (
                    "action_approve_cancellation" if aprovar else "action_refuse_cancellation"
                ),
                "action_field": (
                    "CHANGE_FORM-action" if "CHANGE_FORM-action" in request.POST else "action"
                ),
            },
        )

    def _aplicar_decisao(self, request, orders, aprovar, resposta, restore_stock):
        feitos = 0
        for order in orders:
            try:
                if aprovar:
                    # A resposta humana vale **só** para os pedidos que a
                    # pediram. Repassá-la ao lote inteiro fazia um pedido pronto
                    # devolver a peça antes do reembolso, e um pedido pago e
                    # ainda parado perder a devolução automática — bastava um
                    # pedido em produção no meio da seleção.
                    plano = services.cancellation_plan(order)
                    escolha = restore_stock if plano.needs_stock_decision else None
                    ok = services.approve_cancellation(
                        order, note=resposta, user=request.user, restore_stock=escolha
                    )
                else:
                    ok = services.refuse_cancellation(order, note=resposta, user=request.user)
            except services.StockDecisionRequired as erro:
                self.message_user(request, f"{order.number}: {erro}", messages.ERROR)
                continue
            feitos += int(bool(ok))

        if feitos:
            self.message_user(
                request,
                (
                    _("%(count)s cancelamento(s) aprovado(s). O cliente foi avisado; "
                      "onde havia cobrança, o reembolso ficou aberto para registro.")
                    if aprovar
                    else _("%(count)s cancelamento(s) recusado(s). O cliente foi avisado.")
                )
                % {"count": feitos},
                messages.SUCCESS,
            )
        return None

    @admin.action(
        permissions=["change"],
        description="Registrar reembolso já feito",
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def action_register_refund(self, request, queryset):
        """Guarda o que uma pessoa já devolveu no banco. Não move dinheiro.

        Um pedido de cada vez, de propósito: valor e referência são de **uma**
        transferência, e aplicar o mesmo valor a vários pedidos de uma vez é a
        forma mais fácil de registrar um reembolso que nunca aconteceu.
        """
        abertos = [
            order for order in queryset
            if order.refund_status in {RefundStatus.PENDING, RefundStatus.PARTIAL}
        ]
        if len(abertos) != 1:
            self.message_user(
                request,
                _("Escolha exatamente um pedido com reembolso em aberto.")
                if abertos
                else _("Nenhum dos pedidos escolhidos tem reembolso em aberto."),
                messages.WARNING,
            )
            return None

        order = abertos[0]
        if request.POST.get("confirmar") == "1":
            form = RefundForm(request.POST)
            if form.is_valid():
                gravou = services.register_refund(
                    order,
                    form.cleaned_data["amount"],
                    reference=form.cleaned_data["reference"],
                    user=request.user,
                )
                order.refresh_from_db()
                if gravou:
                    self.message_user(
                        request,
                        _("Reembolso registrado. Situação: %(estado)s.")
                        % {"estado": order.get_refund_status_display()},
                        messages.SUCCESS,
                    )
                else:
                    # O caso comum é o clique repetido: a mesma referência já
                    # está no histórico. Anunciar sucesso ali fazia quem
                    # registrou acreditar que somou duas transferências.
                    self.message_user(
                        request,
                        _(
                            "Nada foi registrado: esta referência já consta no "
                            "histórico deste pedido, ou o reembolso já está concluído."
                        ),
                        messages.WARNING,
                    )
                return None
        else:
            form = RefundForm(initial={"amount": order.refund_due})

        return render(
            request,
            "admin/orders/register_refund.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Registrar reembolso"),
                "opts": self.opts,
                "order": order,
                "form": form,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "action_name": "action_register_refund",
                "action_field": (
                    "CHANGE_FORM-action" if "CHANGE_FORM-action" in request.POST else "action"
                ),
            },
        )

    # -- a ordem da página ---------------------------------------------

    def get_fieldsets(self, request, obj=None):
        """A tela mostra só o que este pedido tem.

        Duas coisas somem quando não há o que dizer:

        * o **aviso de cancelamento pendente**, porque um `readonly` que devolve
          `""` não desaparece — ele continua rendendo a linha e o rótulo, e sem
          texto isso virava um ":" solto no alto da tela;
        * a **seção 5 inteira**, num pedido que nunca teve solicitação de
          cancelamento nem reembolso. É a maioria dos pedidos, e uma seção vazia
          em todos eles é espaço tirado das que importam.
        """
        fieldsets = super().get_fieldsets(request, obj)
        pendente = obj is not None and obj.cancellation_status == CancellationStatus.REQUESTED
        tem_cancelamento = obj is not None and (
            obj.cancellation_status != CancellationStatus.NONE
            or obj.refund_status != RefundStatus.NONE
        )

        limpos = []
        for nome, opcoes in fieldsets:
            if nome == SECAO_CANCELAMENTO and not tem_cancelamento:
                continue
            campos = opcoes.get("fields", ())
            if not pendente:
                campos = tuple(c for c in campos if c != "cancelamento_aviso")
            limpos.append((nome, {**opcoes, "fields": campos}))
        return limpos

    def _page_layout(self, context):
        """As seções na ordem de `ORDER_SECTION_ORDER`, numa lista só.

        Mesma mecânica do `ProductAdmin`: cada item é
        ``{"kind": "fieldset"|"formset", ...}``, e o `index` do fieldset é a
        posição real dele no formulário — é dela que sai o `id` de cada bloco,
        e dois blocos com o mesmo `id` quebrariam o `aria-labelledby`.

        Seção que não estiver na lista não é engolida: vai para o fim, na ordem
        em que o Django a entregou. Melhor no lugar errado do que invisível.
        """
        adminform = context.get("adminform")
        if adminform is None:
            return None

        por_nome = {
            fieldset.name: {"kind": "fieldset", "fieldset": fieldset, "index": indice}
            for indice, fieldset in enumerate(adminform)
        }
        por_modelo = {
            formset.opts.model: {"kind": "formset", "formset": formset}
            for formset in (context.get("inline_admin_formsets") or ())
        }

        layout = []
        for chave in ORDER_SECTION_ORDER:
            item = por_nome.pop(chave, None) if isinstance(chave, str) else por_modelo.pop(chave, None)
            if item is not None:
                layout.append(item)

        layout.extend(por_nome.values())
        layout.extend(por_modelo.values())
        return layout

    def render_change_form(self, request, context, add=False, change=False, **kwargs):
        context["order_layout"] = self._page_layout(context)
        return super().render_change_form(request, context, add=add, change=change, **kwargs)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, OrderNote) and instance.author_id is None:
                instance.author = request.user
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """“Interrompido” não entra na lista da produção.

        Ele existe como estado, mas não como escolha: quem o escreve é a
        aprovação do cancelamento, que junto dele decide estoque, reembolso e
        e-mail. Deixá-lo no `select` seria recriar, com outro nome, o caminho
        que esta etapa veio fechar.
        """
        if db_field.name == "fulfillment_status":
            # `HALTED` sai da lista para não poder ser **escolhido** — mas
            # continua na lista do pedido que já está interrompido. Sem essa
            # exceção o `select` não encontrava o valor atual, caía na primeira
            # opção e mostrava "Não iniciado" para uma produção que a loja
            # parou.
            atual = getattr(getattr(request, "_jd_order", None), "fulfillment_status", None)
            kwargs["choices"] = [
                (valor, rotulo)
                for valor, rotulo in db_field.get_choices(include_blank=False)
                if valor != FulfillmentStatus.HALTED or atual == FulfillmentStatus.HALTED
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        """Guarda o pedido em edição para o `formfield_for_choice_field` ver.

        O Django não passa o objeto para aquele gancho; sem esta ponte não há
        como saber se o pedido está interrompido na hora de montar a lista.
        """
        if request is not None:
            # `None` chega de código que só quer inspecionar o formulário — um
            # teste, um comando. Ali não há tela para desenhar, e a ponte não
            # tem onde se apoiar.
            request._jd_order = obj
        return super().get_form(request, obj, **kwargs)

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


@admin.register(PaymentProof)
class PaymentProofAdmin(admin.ModelAdmin):
    """Os comprovantes, numa lista só — para achar sem abrir pedido por pedido.

    Somente leitura: é documento que o cliente enviou. O arquivo continua saindo
    pela rota protegida (`orders:payment_proof_file`), nunca como mídia pública
    — `payment-proofs/` não está entre as pastas servidas.
    """

    list_display = ("created_at", "pedido", "original_name", "tamanho", "content_type", "arquivo")
    list_filter = ("content_type", "created_at")
    search_fields = ("order__number", "original_name", "order__customer__user__email")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("order", "order__customer", "order__customer__user")
    readonly_fields = tuple(field.name for field in PaymentProof._meta.fields) + ("arquivo",)

    @admin.display(description="pedido", ordering="order__number")
    def pedido(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:orders_order_change", args=[obj.order_id]),
            obj.order.number,
        )

    @admin.display(description="tamanho")
    def tamanho(self, obj):
        return obj.size_display

    @admin.display(description="arquivo")
    def arquivo(self, obj):
        if not obj.file:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            reverse("orders:payment_proof_file", args=[obj.pk]),
            _("Abrir"),
        )

    def has_add_permission(self, request):
        # Quem envia comprovante é o cliente, pela conta dele.
        return False

    def has_change_permission(self, request, obj=None):
        return False


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


# ---------------------------------------------------------------------------
# Contas bancárias
# ---------------------------------------------------------------------------


class CancellationSettingsTranslationInline(admin.StackedInline):
    """A observação extra por idioma."""

    model = CancellationSettingsTranslation
    formset = UniqueLanguageInlineFormSet
    # Uma linha só: o campo é opcional, e nove `textarea` vazios empurram o
    # botão Salvar para fora da tela. "Adicionar outro" continua ali.
    extra = 1
    fields = ("language", "extra_note")
    verbose_name = "observação por idioma"
    verbose_name_plural = "OBSERVAÇÃO EXTRA — opcional, por idioma"


@admin.register(CancellationSettings)
class CancellationSettingsAdmin(admin.ModelAdmin):
    """O que a loja diz ao cliente quando aprova um cancelamento.

    **Uma linha só.** O corpo da mensagem vive no código, traduzido como o
    resto da interface; o que se edita aqui é o **prazo do reembolso** — que
    depende do banco e do contrato da loja, não do software — e uma observação
    opcional por idioma.

    Sem prazo cadastrado a mensagem não promete prazo nenhum. É de propósito:
    inventar um número em nome de quem atende é pior do que uma frase
    incompleta e verdadeira.
    """

    inlines = [CancellationSettingsTranslationInline]
    readonly_fields = ("previa", "created_at", "updated_at")
    fieldsets = (
        (
            "PRAZO PARA CANCELAR OU DEVOLVER",
            {
                "fields": ("withdrawal_days",),
                "description": (
                    "Contados a partir da <b>entrega</b> — é dessa data que a lei "
                    "manda contar, não da compra nem do envio. <b>Catorze dias é o "
                    "mínimo legal na Bélgica</b> (diretiva 2011/83/UE): a loja pode "
                    "oferecer mais, e o formulário recusa qualquer valor abaixo "
                    "disso.<br>"
                    "Enquanto o prazo corre, o cliente vê o botão de cancelar na "
                    "conta dele. Pedido personalizado continua aparecendo, mas vai "
                    "para análise da equipe: a exceção do artigo 16.º c tem "
                    "condições que só uma pessoa confere."
                ),
            },
        ),
        (
            "PRAZO DO REEMBOLSO",
            {
                "fields": ("refund_days_min", "refund_days_max"),
                "description": (
                    "Em dias úteis. <b>Deixe os dois em branco enquanto a loja não "
                    "tiver um prazo definido</b>: sem eles, a mensagem diz que o "
                    "reembolso será feito, sem prometer quando.<br>"
                    "Preencher só o mínimo produz “em até X dias úteis”; preencher "
                    "os dois produz “em X a Y dias úteis”."
                ),
            },
        ),
        (
            "COMO O CLIENTE VÊ",
            {
                "fields": ("previa",),
                "description": (
                    "O texto exato que vai no e-mail e no acompanhamento do pedido, "
                    "em português. Nos outros idiomas o corpo é traduzido "
                    "automaticamente; só a observação extra é escrita por idioma."
                ),
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="mensagem enviada ao cliente")
    def previa(self, obj):
        return format_html("<i>{}</i>", CancellationSettings.approved_message())

    def has_add_permission(self, request):
        """Uma linha só — e só para quem já teria permissão de criá-la.

        O `super()` primeiro, e não só o `exists()`: o Admin usa este método
        também para decidir se o model aparece no menu, e sem ele a política
        surgia na tela de qualquer conta da equipe — inclusive uma criada só
        para consultar pedidos.
        """
        return super().has_add_permission(request) and not CancellationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Apagar deixaria o cliente sem resposta no meio de um cancelamento.
        return False


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    """As contas para onde o cliente transfere.

    Era uma linha só enquanto os dados apareciam apenas num aviso interno, para
    a equipe copiar à mão. Agora quem atende **escolhe** a conta antes de
    mandar (ver `OrderAdmin.action_send_transfer_details`), e escolher entre
    uma coisa não é escolher.

    Nada aqui aparece sozinho para o cliente: só sai por ação de uma pessoa,
    num pedido específico.
    """

    list_display = (
        "__str__", "padrao", "beneficiary", "masked_iban", "bic", "situacao",
        "is_active", "sort_order",
    )
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_default", "is_active")
    search_fields = ("label", "beneficiary", "iban")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "CONTA QUE RECEBE",
            {
                "fields": ("label", "beneficiary", "iban", "bic"),
                "description": (
                    "O <b>nome da conta</b> é o que aparece na hora de escolher, "
                    "no pedido. Titular e IBAN são obrigatórios para a conta poder "
                    "ser usada — sem os dois ela não entra na lista."
                ),
            },
        ),
        (
            "USO AUTOMÁTICO",
            {
                "fields": ("is_default",),
                "description": (
                    "A <b>conta padrão</b> é a que todo pedido por transferência usa "
                    "sozinho — o cliente recebe estes dados por e-mail no momento em "
                    "que confirma a compra. Marcar outra conta como padrão desmarca "
                    "esta automaticamente.<br>"
                    "Uma conta padrão <b>desativada não é usada</b>: sem uma conta "
                    "padrão ativa e completa, o checkout por transferência para de "
                    "aceitar pedidos e avisa o cliente."
                ),
            },
        ),
        (
            "INSTRUÇÕES",
            {
                "fields": ("instructions",),
                "description": (
                    "Vai no e-mail do cliente, abaixo dos dados. O número do pedido "
                    "já é enviado como comunicação — não precisa repetir aqui."
                ),
            },
        ),
        ("EXIBIÇÃO", {"fields": ("is_active", "sort_order")}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="IBAN", ordering="iban")
    def masked_iban(self, obj):
        return obj.masked_iban or "—"

    @admin.display(description="padrão", ordering="is_default")
    def padrao(self, obj):
        """Padrão, e se dá para usá-la.

        Uma conta marcada como padrão mas desativada é o caso que trava o
        checkout inteiro, e é exatamente o que passa despercebido numa coluna
        de "sim/não". Aqui ela se anuncia.
        """
        if not obj.is_default:
            return "—"
        if obj.is_usable:
            return format_html('<b style="color:var(--jd-ok,#1a7f37)">✔ {}</b>', _("padrão"))
        return format_html(
            '<b>⚠ {}</b>', _("padrão, mas NÃO utilizável — o checkout está travado")
        )

    @admin.display(description="pronta para usar", boolean=True)
    def situacao(self, obj):
        return obj.is_complete

    def has_delete_permission(self, request, obj=None):
        # Apagar uma conta apagaria o meio de conferir um pedido antigo que
        # recebeu esse IBAN por e-mail. Para parar de usar, desmarque "ativa".
        return False

