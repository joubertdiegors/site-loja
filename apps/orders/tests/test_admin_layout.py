"""A tela do pedido no Admin: sete seções, e o que cada uma promete.

A tela anterior tinha dezesseis blocos. O pagamento aparecia em dois lugares, os
valores ocupavam nove linhas separadas, o comprovante vivia no fim da página
atrás de itens, endereços e histórico, e "OUTROS" guardava idioma, observação do
cliente e marcas de e-mail — três coisas sem relação entre si.

Estes testes protegem a reorganização de duas formas. A primeira é óbvia: as
seções existem, na ordem certa, e a informação está dentro da seção de negócio a
que pertence. A segunda importa mais: **reorganizar a tela não pode ter aberto
uma porta**. Um campo que vira editável no meio de uma mudança visual é o tipo
de coisa que passa despercebida — e contorna serviço, histórico, estoque e
e-mail de uma vez.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cart.cart import CartLine
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)
from apps.orders import services
from apps.orders.admin import (
    SECAO_CANCELAMENTO,
    SECAO_COMUNICACOES,
    SECAO_HISTORICO,
    SECAO_ITENS,
    SECAO_PAGAMENTO,
    SECAO_PRODUCAO,
    SECAO_RESUMO,
    OrderAdmin,
)
from apps.orders.models import FulfillmentStatus, Order

SECOES = (
    SECAO_RESUMO,
    SECAO_PAGAMENTO,
    SECAO_PRODUCAO,
    SECAO_ITENS,
    SECAO_HISTORICO,
    SECAO_COMUNICACOES,
)


@override_settings(ORDER_ADMIN_EMAILS=["equipe@jdprint.test"])
class TelaBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.product = make_product(
            sku="LAY-1", name="Vaso Espiral", price=Decimal("19.90"), stock_quantity=10
        )
        self.user = make_user(username="ana", email="ana@exemplo.test")
        self.customer = self.user.customer
        self.customer.first_name, self.customer.last_name = "Ana", "Ribeiro"
        self.customer.save()
        self.address = make_address(self.customer, self.country)
        self.order = self.novo_pedido()

        self.staff = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", "senha-de-teste-77"
        )
        self.client.force_login(self.staff)
        mail.outbox = []

    def novo_pedido(self, **extra):
        return services.create_order(
            customer=self.customer,
            lines=[
                CartLine(
                    key="k",
                    product=self.product,
                    variant=self.product.default_variant,
                    quantity=1,
                )
            ],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
            **extra,
        )

    def corpo(self, order=None):
        order = order or self.order
        return self.client.get(
            reverse("admin:orders_order_change", args=[order.pk])
        ).content.decode()

    def secao(self, nome, corpo=None, ate=None):
        """O pedaço da página entre um título de seção e o seguinte."""
        corpo = corpo if corpo is not None else self.corpo()
        inicio = corpo.index(nome)
        fim = corpo.index(ate) if ate else len(corpo)
        return corpo[inicio:fim]


# ---------------------------------------------------------------------------
# A estrutura
# ---------------------------------------------------------------------------


class SetesSecoesTests(TelaBase):
    def test_every_section_is_on_the_page(self):
        corpo = self.corpo()

        for nome in SECOES:
            with self.subTest(secao=nome):
                self.assertIn(nome, corpo)

    def test_they_come_in_the_order_the_operation_reads_them(self):
        corpo = self.corpo()

        posicoes = [corpo.index(nome) for nome in SECOES]
        self.assertEqual(posicoes, sorted(posicoes))

    def test_the_old_scattered_blocks_are_gone(self):
        """Dez fieldsets e seis inlines viraram sete painéis."""
        corpo = self.corpo()

        for antigo in (
            "2. CLIENTE",
            "4. COMPROVANTE DE PAGAMENTO",
            "AVISOS ENVIADOS",
            ">VALORES</h2>",
            ">OUTROS</h2>",
        ):
            with self.subTest(bloco=antigo):
                self.assertNotIn(antigo, corpo)

    def test_the_page_has_one_writable_block(self):
        """As notas internas — a única coisa que se escreve nesta tela."""
        self.assertEqual([i.__name__ for i in OrderAdmin.inlines], ["OrderNoteInline"])


class ResumoTests(TelaBase):
    def test_it_answers_the_first_questions(self):
        corpo = self.secao(SECAO_RESUMO, ate=SECAO_PAGAMENTO)

        self.assertIn(self.order.number, corpo)
        self.assertIn("Ana Ribeiro", corpo)
        self.assertIn("ana@exemplo.test", corpo)
        self.assertIn(f"{self.order.total:.2f}", corpo)

    def test_the_states_are_chips_side_by_side(self):
        corpo = self.secao(SECAO_RESUMO, ate=SECAO_PAGAMENTO)

        self.assertIn("jd-chips", corpo)
        self.assertIn("Aguardando pagamento", corpo)
        self.assertIn("Não iniciado", corpo)

    def test_the_cancellation_chip_appears_only_when_there_is_one(self):
        sem = self.secao(SECAO_RESUMO, ate=SECAO_PAGAMENTO)
        self.assertNotIn("Cancelamento solicitado", sem)

        pedido = self.pago_em_producao()
        services.request_cancellation(pedido, "Motivo")

        com = self.secao(SECAO_RESUMO, corpo=self.corpo(pedido), ate=SECAO_PAGAMENTO)
        self.assertIn("Cancelamento solicitado", com)

    def test_the_customer_note_only_shows_when_written(self):
        self.assertNotIn("Observação do cliente", self.corpo())

        pedido = self.novo_pedido(customer_note="Embrulhar com cuidado")

        self.assertIn("Embrulhar com cuidado", self.corpo(pedido))

    def pago_em_producao(self):
        pedido = self.novo_pedido()
        services.confirm_payment(pedido)
        pedido.refresh_from_db()
        services.change_fulfillment_status(pedido, FulfillmentStatus.IN_PRODUCTION)
        pedido.refresh_from_db()
        return pedido


class PagamentoTests(TelaBase):
    def test_everything_about_money_in_is_here(self):
        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("Transferência bancária", corpo)
        self.assertIn("Pendente", corpo)
        self.assertIn(f"{self.order.total:.2f}", corpo)

    def test_the_bank_account_is_masked(self):
        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("BE68 ···· 7034", corpo)
        self.assertNotIn("BE68 5390 0754 7034", corpo)

    def test_the_proof_is_inside_this_section(self):
        """Conferir uma transferência era olhar dois blocos ao mesmo tempo."""
        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("Comprovante", corpo)

    def test_it_says_when_no_proof_arrived(self):
        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("nenhum recebido", corpo)

    def test_the_payment_attempts_are_here_too(self):
        """O histórico da conta usada fica junto do pagamento, não num bloco à parte."""
        from apps.orders.models import Payment

        Payment.objects.create(
            order=self.order, provider="transfer", amount=self.order.total,
            currency=self.order.currency, method_label="Transferência bancária",
        )

        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("Tentativas de pagamento", corpo)
        self.assertIn("Transferência bancária", corpo)

    def test_the_actions_of_this_section_are_listed_here(self):
        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("Confirmar pagamento", corpo)

    def test_a_paid_order_is_not_offered_a_payment_confirmation(self):
        """Ação impossível não aparece."""
        antes = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)
        self.assertIn('jd-action">Confirmar pagamento', antes)

        services.confirm_payment(self.order)

        depois = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)
        self.assertNotIn('jd-action">Confirmar pagamento', depois)

    def test_the_payment_status_is_not_repeated_in_other_sections(self):
        """Estado repetido é estado que um dia diverge."""
        corpo = self.corpo()
        depois = corpo[corpo.index(SECAO_PRODUCAO):]

        self.assertNotIn("Situação do pagamento", depois)


class ProducaoTests(TelaBase):
    def test_delivery_and_production_share_one_section(self):
        corpo = self.secao(SECAO_PRODUCAO, ate=SECAO_ITENS)

        self.assertIn("Bpost", corpo)
        self.assertIn("Rue du Test", corpo)
        self.assertIn("Não iniciado", corpo)

    def test_dates_that_did_not_happen_are_not_shown(self):
        corpo = self.secao(SECAO_PRODUCAO, ate=SECAO_ITENS)

        self.assertNotIn("Entregue em", corpo)
        self.assertNotIn("Enviado em", corpo)

    def test_production_is_the_only_editable_state(self):
        corpo = self.secao(SECAO_PRODUCAO, ate=SECAO_ITENS)

        self.assertIn('name="fulfillment_status"', corpo)
        self.assertIn('name="tracking_number"', corpo)

    def test_halted_is_not_offered_on_a_live_order(self):
        corpo = self.corpo()

        self.assertNotIn('value="halted"', corpo)

    def test_a_halted_order_shows_its_real_state(self):
        pedido = self.novo_pedido()
        services.request_cancellation(pedido, "Motivo")

        corpo = self.secao(SECAO_PRODUCAO, corpo=self.corpo(pedido), ate=SECAO_ITENS)

        self.assertIn("Interrompido", corpo)


class ItensTests(TelaBase):
    def test_products_and_totals_share_one_section(self):
        corpo = self.secao(SECAO_ITENS, ate=SECAO_HISTORICO)

        self.assertIn("Vaso Espiral", corpo)
        self.assertIn("LAY-1", corpo)
        self.assertIn("Subtotal", corpo)
        self.assertIn("Frete", corpo)
        self.assertIn("TVA", corpo)
        self.assertIn("Total", corpo)

    def test_the_discount_only_shows_when_there_is_one(self):
        corpo = self.secao(SECAO_ITENS, ate=SECAO_HISTORICO)

        self.assertNotIn("Desconto", corpo)


class CancelamentoCondicionalTests(TelaBase):
    def test_the_section_is_absent_when_there_is_nothing_to_show(self):
        """A maioria dos pedidos nunca tem cancelamento. Seção vazia é espaço
        tirado das que importam."""
        self.assertNotIn(SECAO_CANCELAMENTO, self.corpo())

    def test_it_appears_with_a_request(self):
        services.request_cancellation(self.order, "Comprei errado")

        corpo = self.corpo()

        self.assertIn(SECAO_CANCELAMENTO, corpo)
        self.assertIn("Comprei errado", corpo)

    def test_it_separates_the_cancellation_from_the_refund(self):
        pedido = self.novo_pedido()
        services.confirm_payment(pedido)
        pedido.refresh_from_db()
        services.request_cancellation(pedido, "Motivo")

        corpo = self.secao(SECAO_CANCELAMENTO, corpo=self.corpo(pedido), ate=SECAO_HISTORICO)

        self.assertIn("Cancelamento</span>", corpo)
        self.assertIn("Reembolso</span>", corpo)
        self.assertIn("Falta devolver", corpo)

    def test_the_decision_actions_only_appear_while_a_decision_is_pending(self):
        pedido = self.novo_pedido()
        services.confirm_payment(pedido)
        pedido.refresh_from_db()
        services.change_fulfillment_status(pedido, FulfillmentStatus.IN_PRODUCTION)
        services.request_cancellation(pedido, "Motivo")

        pendente = self.secao(SECAO_CANCELAMENTO, corpo=self.corpo(pedido), ate=SECAO_HISTORICO)
        self.assertIn("Aprovar cancelamento", pendente)

        services.approve_cancellation(pedido, restore_stock=False)
        decidido = self.secao(SECAO_CANCELAMENTO, corpo=self.corpo(pedido), ate=SECAO_HISTORICO)
        self.assertNotIn("Aprovar cancelamento", decidido)
        self.assertIn("Registrar reembolso", decidido)


class HistoricoTests(TelaBase):
    def test_it_is_one_compact_table(self):
        corpo = self.secao(SECAO_HISTORICO, ate=SECAO_COMUNICACOES)

        self.assertIn("Data/hora", corpo)
        self.assertIn("Responsável", corpo)
        self.assertIn("Observação", corpo)
        self.assertIn("Pedido criado", corpo)

    def test_it_shows_events_from_every_area(self):
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        services.request_cancellation(self.order, "Motivo")

        corpo = self.secao(SECAO_HISTORICO, ate=SECAO_COMUNICACOES)

        for evento in (
            "Pagamento confirmado",
            "Cancelamento solicitado",
            "Estoque devolvido",
            "Produção interrompida",
            "Reembolso pendente",
        ):
            with self.subTest(evento=evento):
                self.assertIn(evento, corpo)

    def test_it_is_not_a_form(self):
        """Histórico não se edita — e desenhá-lo como formulário sugeria que sim."""
        corpo = self.secao(SECAO_HISTORICO, ate=SECAO_COMUNICACOES)

        self.assertNotIn('name="history-0-event"', corpo)


class ComunicacoesTests(TelaBase):
    def test_it_is_empty_before_anything_goes_out(self):
        pedido = self.novo_pedido(payment_method="")
        pedido.transfer_details_sent_at = None
        pedido.save(update_fields=["transfer_details_sent_at"])

        corpo = self.secao(SECAO_COMUNICACOES, corpo=self.corpo(pedido))

        self.assertIn("Nenhuma mensagem enviada ainda", corpo)

    def test_it_lists_every_kind_of_message(self):
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        services.request_cancellation(self.order, "Motivo")

        corpo = self.secao(SECAO_COMUNICACOES)

        for tipo in (
            "Confirmação da compra",
            "Ordem de produção",
            "Cancelamento aprovado",
            "Reembolso iniciado",
        ):
            with self.subTest(tipo=tipo):
                self.assertIn(tipo, corpo)

    def test_it_names_the_recipient(self):
        services.confirm_payment(self.order)

        corpo = self.secao(SECAO_COMUNICACOES)

        self.assertIn("ana@exemplo.test", corpo)
        self.assertIn("equipe@jdprint.test", corpo)

    def test_it_is_not_mixed_with_the_history(self):
        """Histórico é o que aconteceu; comunicações é o que saiu."""
        services.confirm_payment(self.order)

        historico = self.secao(SECAO_HISTORICO, ate=SECAO_COMUNICACOES)

        self.assertNotIn("Destinatário", historico)


# ---------------------------------------------------------------------------
# A reorganização não abriu porta nenhuma
# ---------------------------------------------------------------------------


class NadaFicouEditavelTests(TelaBase):
    def test_only_four_fields_can_be_typed(self):
        editaveis = set(OrderAdmin(Order, None).get_form(None, self.order)().fields)

        self.assertEqual(
            editaveis,
            {"fulfillment_status", "tracking_number", "is_gift", "gift_message"},
        )

    def test_no_state_field_is_in_the_form(self):
        corpo = self.corpo()

        for campo in (
            "status", "payment_status", "cancellation_status", "refund_status",
            "refunded_amount", "cancelled_at", "delivered_at", "paid_at",
            "stock_return_decision", "stock_returned_at", "stock_applied_at",
            "total", "subtotal", "customer", "number",
        ):
            with self.subTest(campo=campo):
                self.assertNotIn(f'name="{campo}"', corpo)

    def test_posting_a_state_changes_nothing(self):
        """A porta que a reorganização não pode ter aberto."""
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        dados = {
            "fulfillment_status": self.order.fulfillment_status,
            "tracking_number": "",
            "is_gift": "",
            "gift_message": "",
            "status": "cancelled",
            "payment_status": "not_charged",
            "refund_status": "refunded",
            "refunded_amount": "99.00",
            "notes-TOTAL_FORMS": "0",
            "notes-INITIAL_FORMS": "0",
            "notes-MIN_NUM_FORMS": "0",
            "notes-MAX_NUM_FORMS": "1000",
        }

        self.client.post(
            reverse("admin:orders_order_change", args=[self.order.pk]), dados, follow=True
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "confirmed")
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self.order.refund_status, "none")
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))

    def test_the_page_is_read_only_for_someone_who_may_only_look(self):
        olheiro = get_user_model().objects.create_user(
            username="olheiro", email="olheiro@jdprint.test",
            password="senha-de-teste-77", is_staff=True,
        )
        olheiro.user_permissions.set(
            Permission.objects.filter(
                codename="view_order", content_type__app_label="orders"
            )
        )
        self.client.force_login(olheiro)

        corpo = self.corpo()

        self.assertIn(self.order.number, corpo)
        self.assertNotIn('name="fulfillment_status"', corpo)
        self.assertNotIn('name="_save"', corpo)

    def test_a_viewer_gains_no_action(self):
        olheiro = get_user_model().objects.create_user(
            username="olheiro", email="olheiro@jdprint.test",
            password="senha-de-teste-77", is_staff=True,
        )
        olheiro.user_permissions.set(
            Permission.objects.filter(
                codename="view_order", content_type__app_label="orders"
            )
        )
        self.client.force_login(olheiro)

        corpo = self.client.get(reverse("admin:orders_order_changelist")).content.decode()

        for acao in (
            "action_confirm_payment",
            "action_approve_cancellation",
            "action_register_refund",
        ):
            with self.subTest(acao=acao):
                self.assertNotIn(acao, corpo)

    def test_the_section_shortcuts_do_not_execute_anything(self):
        """A faixa de ações diz o que existe; quem executa é a lista de pedidos."""
        corpo = self.secao(SECAO_PAGAMENTO, ate=SECAO_PRODUCAO)

        self.assertIn("jd-action", corpo)
        self.assertNotIn("<button", corpo)
