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
            lines=[CartLine(key="k", product=self.product, variant=None, quantity=1)],
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

    def test_the_three_states_are_visible_side_by_side(self):
        response = self.client.get(self.change_url())

        self.assertContains(response, "SITUAÇÃO")
        self.assertContains(response, "CANCELAMENTO")
        self.assertContains(response, "VALORES")

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

    def test_money_and_snapshots_are_read_only(self):
        readonly = OrderAdmin(Order, None).readonly_fields

        for field in ("number", "total", "subtotal", "tax_rate", "currency", "customer"):
            with self.subTest(field=field):
                self.assertIn(field, readonly)

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
    def post_action(self, action):
        return self.client.post(
            reverse("admin:orders_order_changelist"),
            {"action": action, "_selected_action": [str(self.order.pk)]},
            follow=True,
        )

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

    def test_refuse_cancellation_action(self):
        services.request_cancellation(self.order, "Mudei de ideia")

        self.post_action("action_refuse_cancellation")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REFUSED)
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_the_decision_is_recorded_with_the_author(self):
        services.request_cancellation(self.order, "Comprei errado")

        self.post_action("action_approve_cancellation")

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
