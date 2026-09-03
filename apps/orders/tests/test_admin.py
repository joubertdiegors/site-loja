"""Admin dos pedidos: o que se opera e o que **não** se reescreve.

Um pedido é documento contábil. O admin serve para produzir e despachar — não
para corrigir um valor a posteriori. Estes testes prendem essa fronteira.
"""

from decimal import Decimal

from django.test import TestCase
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
from apps.orders.admin import OrderAdmin
from apps.orders.models import (
    CancellationStatus,
    FulfillmentStatus,
    Order,
    OrderNote,
    OrderStatus,
)


class OrderAdminBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()

        self.staff = make_user(username="operador", email="op@example.com", is_staff=True)
        self.staff.is_superuser = True
        self.staff.save()

        self.customer_user = make_user(username="cliente3d", email="cliente@example.com")
        self.address = make_address(self.customer_user.customer, self.country)
        self.product = make_product(
            sku="P1", name="Vaso Espiral", price=Decimal("19.90"), stock_quantity=10
        )
        self.order = services.create_order(
            customer=self.customer_user.customer,
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
        )
        self.client.force_login(self.staff)

    def change_url(self):
        return reverse("admin:orders_order_change", args=[self.order.pk])


class OrderAdminPagesTests(OrderAdminBase):
    def test_list_opens(self):
        response = self.client.get(reverse("admin:orders_order_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.number)

    def test_change_page_opens(self):
        response = self.client.get(self.change_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vaso Espiral")

    def test_the_seven_sections_are_on_the_page(self):
        """Uma seção por área de negócio, na ordem em que a operação lê."""
        corpo = self.client.get(self.change_url()).content.decode()

        posicoes = []
        for secao in (
            "1. RESUMO",
            "2. PAGAMENTO",
            "3. PRODUÇÃO E ENTREGA",
            "4. ITENS E VALORES",
            "6. HISTÓRICO",
            "7. COMUNICAÇÕES",
        ):
            with self.subTest(secao=secao):
                self.assertIn(secao, corpo)
            posicoes.append(corpo.index(secao))

        self.assertEqual(posicoes, sorted(posicoes))

    def test_the_states_are_side_by_side_in_the_summary(self):
        """Os selos respondem perguntas diferentes — e juntos contam a situação."""
        response = self.client.get(self.change_url())

        self.assertContains(response, "jd-chips")
        self.assertContains(response, self.order.get_status_display())
        self.assertContains(response, self.order.get_payment_status_display())
        self.assertContains(response, self.order.get_fulfillment_status_display())

    def test_related_pages_open(self):
        for name in (
            "admin:orders_payment_changelist",
            "admin:orders_ordernote_changelist",
            "admin:orders_orderstatushistory_changelist",
            "admin:orders_webhookevent_changelist",
            "admin:shipping_shippingcarrier_changelist",
            "admin:shipping_shippingmethod_changelist",
            "admin:shipping_shippingrate_changelist",
            "admin:core_deliverycountry_changelist",
            "admin:accounts_customeraddress_changelist",
        ):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class OrderAdminProtectionTests(OrderAdminBase):
    def test_orders_cannot_be_created_by_hand(self):
        """Pedido nasce de uma compra: criado aqui, não teria pagamento nem estoque."""
        admin = OrderAdmin(Order, None)

        self.assertFalse(admin.has_add_permission(None))
        self.assertEqual(
            self.client.get(reverse("admin:orders_order_add")).status_code, 403
        )

    def test_orders_cannot_be_deleted(self):
        """Apagar pedido é apagar contabilidade. Cancelar é outra coisa."""
        admin = OrderAdmin(Order, None)

        self.assertFalse(admin.has_delete_permission(None, self.order))

    def test_money_and_snapshots_cannot_be_edited(self):
        """A garantia não é o `readonly_fields`: é não haver campo no formulário.

        Dinheiro e snapshots deixaram de ser `fields` — eles são desenhados
        dentro dos painéis. A conferência passa a ser sobre o formulário de
        verdade, que é onde um `<input>` a mais faria estrago.
        """
        editaveis = set(
            OrderAdmin(Order, None).get_form(None, self.order)().fields
        )

        for field in (
            "number", "total", "subtotal", "tax_rate", "currency", "customer",
            "status", "payment_status", "cancellation_status", "refund_status",
            "refunded_amount", "cancelled_at", "delivered_at", "paid_at",
            "stock_return_decision", "stock_returned_at", "stock_applied_at",
        ):
            with self.subTest(field=field):
                self.assertNotIn(field, editaveis)

    def test_only_four_things_can_be_typed_on_an_order(self):
        """A lista completa do que um operador digita — e ela é curta."""
        editaveis = set(
            OrderAdmin(Order, None).get_form(None, self.order)().fields
        )

        self.assertEqual(
            editaveis,
            {"fulfillment_status", "tracking_number", "is_gift", "gift_message"},
        )

    def test_payments_are_read_only(self):
        from apps.orders.admin import PaymentAdmin
        from apps.orders.models import Payment

        admin = PaymentAdmin(Payment, None)

        self.assertFalse(admin.has_add_permission(None))
        self.assertFalse(admin.has_change_permission(None))

    def test_webhook_events_are_read_only(self):
        from apps.orders.admin import WebhookEventAdmin
        from apps.orders.models import WebhookEvent

        admin = WebhookEventAdmin(WebhookEvent, None)

        self.assertFalse(admin.has_add_permission(None))
        self.assertFalse(admin.has_change_permission(None))


class OrderAdminActionTests(OrderAdminBase):
    def post_action(self, action, **extra):
        dados = {"action": action, "_selected_action": [str(self.order.pk)]}
        dados.update(extra)
        return self.client.post(
            reverse("admin:orders_order_changelist"), dados, follow=True
        )

    def decidir(self, action, **extra):
        """A decisão de cancelamento, já com a confirmação da tela do meio.

        Aprovar e recusar deixaram de ser um clique só: a tela pergunta a
        resposta ao cliente e, quando a peça já entrou em produção, o que fazer
        com ela. É o que impede a aprovação de acontecer pela metade.
        """
        dados = {"confirmar": "1", "resposta": "", "restore_stock": "0"}
        dados.update(extra)
        return self.post_action(action, **dados)

    def test_mark_shipped_action(self):
        from django.core import mail

        self.order.tracking_number = "BE999"
        self.order.save()
        mail.outbox = []

        self.post_action("action_mark_shipped")

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.SHIPPED)
        self.assertEqual(len(mail.outbox), 1)

    def test_approve_cancellation_action(self):
        services.request_cancellation(self.order, "Comprei errado")

        self.post_action("action_approve_cancellation")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)

    def em_analise(self):
        """Pago e em produção: o único caso em que a decisão espera uma pessoa."""
        services.confirm_payment(self.order)
        self.order.refresh_from_db()
        services.change_fulfillment_status(self.order, FulfillmentStatus.IN_PRODUCTION)
        services.request_cancellation(self.order, "Mudei de ideia")
        self.order.refresh_from_db()

    def test_refuse_cancellation_action(self):
        self.em_analise()

        self.decidir("action_refuse_cancellation", resposta="A peça já está na impressora.")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REFUSED)
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)

    def test_the_decision_is_recorded_with_the_author(self):
        self.em_analise()

        self.decidir("action_approve_cancellation")

        entry = self.order.history.filter(event="cancellation_approved").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.created_by, self.staff)


class OrderNoteTests(OrderAdminBase):
    def test_internal_note_never_reaches_the_customer(self):
        OrderNote.objects.create(
            order=self.order, body="Cliente confirmou que deseja fonte maior.", author=self.staff
        )

        self.client.force_login(self.customer_user)
        response = self.client.get(self.order.get_absolute_url())

        self.assertNotContains(response, "fonte maior")

    def test_note_appears_in_the_admin(self):
        OrderNote.objects.create(order=self.order, body="Falar com a produção.", author=self.staff)

        response = self.client.get(self.change_url())

        self.assertContains(response, "Falar com a produção.")

    def test_note_gets_its_author(self):
        response = self.client.post(
            reverse("admin:orders_ordernote_add"),
            {"order": self.order.pk, "body": "Anotação de teste."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        note = OrderNote.objects.get(body="Anotação de teste.")
        self.assertEqual(note.author, self.staff)
