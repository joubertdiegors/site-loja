"""Criação do pedido, snapshots, numeração, estoque e cancelamento.

O teste que mais importa deste arquivo é ``test_order_survives_the_catalogue``:
ele muda o produto **depois** da compra e confere que o pedido não se mexe. Se
alguém um dia trocar um snapshot por uma leitura do catálogo, é ele que cai.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cart.cart import CartLine
from apps.catalog.models import ProductStatus, ProductVariant
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
from apps.orders.models import (
    CancellationStatus,
    FulfillmentStatus,
    FulfillmentType,
    Order,
    OrderEvent,
    OrderNote,
    OrderStatus,
    PaymentStatus,
    RefundStatus,
    next_order_number,
)


def line_for(product, quantity=1, variant=None, customization=None, upload=None):
    """Uma linha de carrinho pronta para o checkout.

    Sem variante explícita usa a padrão do produto: desde a etapa 8 não existe
    linha comercial sem variante — preço, peso e prazo saem dela.
    """
    variant = variant or product.default_variant
    return CartLine(
        key=f"{product.pk}:{variant.pk if variant else 0}:-",
        product=product,
        variant=variant,
        quantity=quantity,
        customization=customization,
        upload=upload,
    )


class OrderNumberTests(TestCase):
    def test_number_follows_the_public_format(self):
        number = next_order_number(2026)

        self.assertRegex(number, r"^JD-2026-\d{6}$")

    def test_numbers_are_sequential(self):
        first = next_order_number(2026)
        second = next_order_number(2026)

        self.assertEqual(int(second[-6:]), int(first[-6:]) + 1)

    def test_each_year_starts_over(self):
        next_order_number(2026)
        self.assertTrue(next_order_number(2027).endswith("000001"))

    def test_number_is_independent_from_the_pk(self):
        """O número tem vida própria: ele conta pedidos, o PK conta linhas.

        Aqui dois números são queimados por tentativas que não viraram pedido
        (é o que uma transação revertida faz). O primeiro pedido gravado sai
        com o terceiro número — e com o PK 1.
        """
        next_order_number()
        next_order_number()

        user = make_user(username="cliente1")
        order = Order.objects.create(customer=user.customer, total=Decimal("10.00"))
        year = timezone.now().year

        self.assertEqual(order.number, f"JD-{year}-000003")
        self.assertNotEqual(order.number.split("-")[-1], f"{order.pk:06d}")


class CreateOrderTests(TestCase):
    def setUp(self):
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()

        self.user = make_user(username="diego3d", email="diego@example.com")
        self.customer = self.user.customer
        self.address = make_address(self.customer, self.country)

        self.product = make_product(
            sku="GATO-01",
            name="Gato Pompom",
            price=Decimal("20.00"),
            stock_quantity=10,
            weight_grams=Decimal("250"),
            production_lead_time_days=3,
        )
        self.variant = self.product.default_variant

    def create(self, lines=None, **overrides):
        options = {
            "customer": self.customer,
            "lines": lines or [line_for(self.product, 2)],
            "shipping_address": self.address,
            "billing_address": self.address,
            "shipping_method": self.method,
            "language": "pt-br",
        }
        options.update(overrides)
        return services.create_order(**options)

    def test_order_is_created_with_the_server_side_totals(self):
        order = self.create()

        self.assertEqual(order.subtotal, Decimal("40.00"))
        self.assertEqual(order.shipping_total, Decimal("5.90"))
        self.assertEqual(order.total, Decimal("45.90"))

    def test_tax_is_the_part_contained_in_the_total(self):
        order = self.create()

        self.assertEqual(order.tax_country, "BE")
        self.assertEqual(order.tax_rate, Decimal("21.00"))
        self.assertEqual(order.tax_total, Decimal("7.97"))
        self.assertTrue(order.prices_include_tax)

    def test_currency_is_stored_in_the_order(self):
        """Nunca reconstruído a partir de settings."""
        order = self.create()

        self.assertEqual(order.currency, "EUR")

    def test_states_start_apart(self):
        order = self.create()

        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.payment_status, PaymentStatus.PENDING)
        self.assertEqual(order.fulfillment_status, FulfillmentStatus.NOT_STARTED)

    def test_creation_is_logged(self):
        order = self.create()

        self.assertTrue(order.history.filter(event=OrderEvent.CREATED).exists())

    def test_weight_and_production_are_snapshotted(self):
        order = self.create()

        self.assertEqual(order.total_weight_grams, 500)
        self.assertEqual(order.production_days, 3)

    def test_estimate_is_production_plus_transit(self):
        order = self.create()

        self.assertEqual(order.shipping_min_days, 5)
        self.assertEqual(order.shipping_max_days, 6)
        self.assertEqual(order.delivery_days_display, "5–6")

    def test_shipping_method_label_is_a_snapshot(self):
        order = self.create()
        self.method.name = "Outro nome"
        self.method.save()

        order.refresh_from_db()
        self.assertIn("Standard", order.shipping_method_label)

    def test_stock_is_not_touched_before_payment(self):
        """Carrinho não é compromisso; pedido pendente também não reserva."""
        self.create()

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)
        self.assertIsNone(Order.objects.get().stock_applied_at)

    def test_language_is_kept_for_the_emails(self):
        order = self.create(language="fr")

        self.assertEqual(order.language, "fr")


class OrderItemSnapshotTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()

        self.user = make_user(username="diego3d")
        self.address = make_address(self.user.customer, self.country)

        self.product = make_product(
            sku="DINO-TREX", name="Dinossauro T-Rex", price=Decimal("27.90"), stock_quantity=5
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="DINO-TREX-P25", size="25 cm", sale_price=Decimal("29.90"),
            stock_quantity=5, weight_grams=Decimal("300"),
        )

    def create(self, **kwargs):
        return services.create_order(
            customer=self.user.customer,
            lines=[line_for(self.product, 1, variant=self.variant, **kwargs)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
        )

    def test_item_copies_name_sku_and_variant(self):
        item = self.create().items.get()

        self.assertEqual(item.product_name, "Dinossauro T-Rex")
        self.assertEqual(item.sku, "DINO-TREX-P25")
        self.assertIn("25 cm", item.variant_label)
        self.assertEqual(item.size_name, "25 cm")

    def test_item_copies_the_price_of_the_variant(self):
        item = self.create().items.get()

        self.assertEqual(item.unit_price, Decimal("29.90"))
        self.assertEqual(item.total, Decimal("29.90"))

    def test_item_copies_the_production_lead_time(self):
        self.variant.production_lead_time_days = 4
        self.variant.save()

        item = self.create().items.get()
        self.assertEqual(item.production_days, 4)

    def test_order_survives_the_catalogue(self):
        """O teste que sustenta o arquivo inteiro."""
        order = self.create()

        # O catálogo muda de todas as formas possíveis...
        self.product.sku = "OUTRO-SKU"
        self.product.status = ProductStatus.INACTIVE
        self.product.save()
        translation = self.product.translations.get(language="pt")
        translation.name = "Nome completamente diferente"
        translation.save()
        self.variant.size = "10 cm"
        self.variant.sale_price = Decimal("5.00")
        self.variant.is_active = False
        self.variant.save()

        # ...e o pedido não se mexe.
        item = Order.objects.get(pk=order.pk).items.get()
        self.assertEqual(item.product_name, "Dinossauro T-Rex")
        self.assertEqual(item.sku, "DINO-TREX-P25")
        self.assertEqual(item.unit_price, Decimal("29.90"))
        self.assertEqual(item.size_name, "25 cm")

    def test_fulfillment_type_records_how_it_was_made(self):
        self.assertEqual(self.create().items.get().fulfillment_type, FulfillmentType.STOCK)

    def test_made_to_order_is_recorded(self):
        self.variant.made_to_order = True
        self.variant.save()

        self.assertEqual(self.create().items.get().fulfillment_type, FulfillmentType.MADE_TO_ORDER)


class PersonalizationSnapshotTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="diego3d")
        self.address = make_address(self.user.customer, self.country)
        self.product = make_product(
            sku="CHAVE-01", name="Chaveiro com Nome", price=Decimal("7.90"), stock_quantity=5
        )
        self.product.personalization_type = "text"
        self.product.save()

    def create(self, customization, upload=None):
        return services.create_order(
            customer=self.user.customer,
            lines=[line_for(self.product, 1, customization=customization, upload=upload)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
        )

    def test_text_personalization_is_copied(self):
        item = self.create(
            {"type": "text", "text": "Lucas", "notes": "fonte maior", "upload_id": None}
        ).items.get()

        self.assertEqual(item.personalization_type, "text")
        self.assertEqual(item.personalization_text, "Lucas")
        self.assertEqual(item.personalization_notes, "fonte maior")
        self.assertTrue(item.has_personalization)

    def test_personalized_item_is_marked_as_such(self):
        item = self.create({"type": "text", "text": "Lucas"}).items.get()

        self.assertEqual(item.fulfillment_type, FulfillmentType.PERSONALIZED)

    def test_photo_upload_stays_linked_to_the_order(self):
        from apps.cart.models import CustomizationUpload

        upload = CustomizationUpload.objects.create(original_name="foto.jpg", extension="jpg")
        item = self.create(
            {"type": "photo", "upload_id": upload.pk, "text": "", "notes": ""}, upload=upload
        ).items.get()

        self.assertEqual(item.personalization_upload_id, upload.pk)

    def test_upload_cannot_be_deleted_while_an_order_uses_it(self):
        """PROTECT: o arquivo do cliente tem que continuar existindo."""
        from django.db.models import ProtectedError

        from apps.cart.models import CustomizationUpload

        upload = CustomizationUpload.objects.create(original_name="foto.jpg", extension="jpg")
        self.create({"type": "photo", "upload_id": upload.pk}, upload=upload)

        with self.assertRaises(ProtectedError):
            upload.delete()


class AddressSnapshotTests(LanguageResetMixin, TestCase):
    """O nome do país é copiado no idioma do pedido — por isso o reset."""

    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="diego3d")
        self.customer = self.user.customer
        self.shipping = make_address(self.customer, self.country, label="Casa", street="Rua A 1")
        self.billing = make_address(
            self.customer, self.country, label="Empresa", street="Rua B 2", company_name="JD SRL"
        )
        self.product = make_product(sku="P1", name="Vaso", price=Decimal("10.00"), stock_quantity=5)

    def create(self, **overrides):
        options = {
            "customer": self.customer,
            "lines": [line_for(self.product)],
            "shipping_address": self.shipping,
            "billing_address": self.billing,
            "shipping_method": self.method,
        }
        options.update(overrides)
        return services.create_order(**options)

    def test_both_addresses_are_copied(self):
        order = self.create()

        self.assertEqual(order.shipping_address.street, "Rua A 1")
        self.assertEqual(order.billing_address.street, "Rua B 2")
        self.assertEqual(order.billing_address.company_name, "JD SRL")

    def test_country_name_is_copied_too(self):
        order = self.create()

        self.assertEqual(order.shipping_address.country_code, "BE")
        self.assertEqual(order.shipping_address.country_name, "Bélgica")

    def test_editing_the_address_does_not_rewrite_history(self):
        order = self.create()

        self.shipping.street = "Rua Z 99"
        self.shipping.save()

        self.assertEqual(Order.objects.get(pk=order.pk).shipping_address.street, "Rua A 1")

    def test_deleting_the_address_does_not_break_the_order(self):
        order = self.create()
        self.shipping.delete()

        snapshot = Order.objects.get(pk=order.pk).shipping_address
        self.assertEqual(snapshot.street, "Rua A 1")
        self.assertIsNone(snapshot.source_id)

    def test_same_address_for_both_is_allowed(self):
        order = self.create(billing_address=self.shipping)

        self.assertEqual(order.shipping_address.street, order.billing_address.street)

    def test_gift_order_records_the_flag(self):
        order = self.create(is_gift=True)

        self.assertTrue(order.is_gift)


class ValidationTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="diego3d")
        self.address = make_address(self.user.customer, self.country)
        self.product = make_product(sku="P1", name="Vaso", price=Decimal("10.00"), stock_quantity=2)
        self.variant = self.product.default_variant

    def create(self, lines=None, **overrides):
        options = {
            "customer": self.user.customer,
            "lines": lines if lines is not None else [line_for(self.product)],
            "shipping_address": self.address,
            "billing_address": self.address,
            "shipping_method": self.method,
        }
        options.update(overrides)
        return services.create_order(**options)

    def test_empty_cart_is_refused(self):
        with self.assertRaises(services.CheckoutError):
            self.create(lines=[])

    def test_quantity_above_stock_is_refused(self):
        with self.assertRaises(services.CheckoutError):
            self.create(lines=[line_for(self.product, 5)])

        self.assertFalse(Order.objects.exists())

    def test_inactive_product_is_refused(self):
        self.product.status = ProductStatus.INACTIVE
        self.product.save()

        with self.assertRaises(services.CheckoutError):
            self.create()

    def test_product_without_price_is_refused(self):
        self.variant.sale_price = None
        self.variant.save()

        with self.assertRaises(services.CheckoutError):
            self.create()

    def test_missing_personalization_is_refused(self):
        self.product.personalization_type = "text"
        self.product.save()

        with self.assertRaises(services.CheckoutError):
            self.create()

    def test_inactive_country_is_refused(self):
        self.country.is_active = False
        self.country.save()

        with self.assertRaises(services.CheckoutError):
            self.create()

    def test_method_that_does_not_serve_the_destination_is_refused(self):
        """O método vem de um <input>; o preço é sempre recalculado no servidor."""
        other = make_method(code="express", name="Express")

        with self.assertRaises(services.CheckoutError):
            self.create(shipping_method=other)

    def test_nothing_is_left_behind_when_creation_fails(self):
        """A transação também devolve o número reservado."""
        with self.assertRaises(services.CheckoutError):
            self.create(lines=[line_for(self.product, 99)])

        self.assertFalse(Order.objects.exists())


class StockTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="diego3d")
        self.address = make_address(self.user.customer, self.country)
        self.product = make_product(sku="P1", name="Vaso", price=Decimal("10.00"), stock_quantity=10)
        self.variant = self.product.default_variant

    def create(self, quantity=3):
        return services.create_order(
            customer=self.user.customer,
            lines=[line_for(self.product, quantity)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
        )

    def test_stock_falls_only_when_the_payment_is_confirmed(self):
        order = self.create()
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

        services.apply_stock(order)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 7)

    def test_applying_twice_does_not_subtract_twice(self):
        """O webhook da Stripe reenvia eventos."""
        order = self.create()
        services.apply_stock(order)
        services.apply_stock(order)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 7)

    def test_variant_stock_is_the_one_that_falls(self):
        """Cada variante tem o seu estoque; a irmã não é tocada."""
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="P1-A",
            size="P",
            sale_price=Decimal("10.00"),
            stock_quantity=4,
        )
        order = services.create_order(
            customer=self.user.customer,
            lines=[line_for(self.product, 2, variant=variant)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
        )

        services.apply_stock(order)

        variant.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(variant.stock_quantity, 2)
        self.assertEqual(self.variant.stock_quantity, 10)

    def test_made_to_order_does_not_consume_stock(self):
        self.variant.made_to_order = True
        self.variant.save()
        order = self.create()

        services.apply_stock(order)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

    def test_shortage_never_makes_the_stock_negative(self):
        order = self.create(quantity=3)
        self.variant.stock_quantity = 1
        self.variant.save()

        shortages = services.apply_stock(order)

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 0)
        self.assertEqual(len(shortages), 1)

    def test_shortage_is_recorded_for_the_team(self):
        """O dinheiro já entrou: recusar seria pior. Avisar é o certo."""
        order = self.create(quantity=3)
        self.variant.stock_quantity = 0
        self.variant.save()

        services.apply_stock(order)

        self.assertTrue(order.history.filter(event=OrderEvent.STOCK_SHORTAGE).exists())
        self.assertTrue(OrderNote.objects.filter(order=order).exists())

    def test_shortage_note_is_internal(self):
        order = self.create(quantity=3)
        self.variant.stock_quantity = 0
        self.variant.save()

        services.apply_stock(order)

        entry = order.history.get(event=OrderEvent.STOCK_SHORTAGE)
        self.assertFalse(entry.is_customer_visible)


class CancellationTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="diego3d")
        self.address = make_address(self.user.customer, self.country)
        self.product = make_product(sku="P1", name="Vaso", price=Decimal("10.00"), stock_quantity=5)
        self.order = services.create_order(
            customer=self.user.customer,
            lines=[line_for(self.product)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
        )

    def em_producao(self, order):
        """Pago e na impressora — o cenário em que a decisão é de uma pessoa."""
        services.confirm_payment(order)
        order.refresh_from_db()
        services.change_fulfillment_status(order, FulfillmentStatus.IN_PRODUCTION)
        order.refresh_from_db()
        return order

    def test_an_unpaid_order_is_cancelled_on_the_spot(self):
        """Sem cobrança não há o que analisar: a regra resolve na hora."""
        services.request_cancellation(self.order, "Comprei errado", user=self.user)

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertEqual(self.order.payment_status, PaymentStatus.NOT_CHARGED)

    def test_the_request_is_logged_with_the_reason(self):
        services.request_cancellation(self.order, "Comprei errado", user=self.user)

        entry = self.order.history.get(event=OrderEvent.CANCELLATION_REQUESTED)
        self.assertEqual(entry.message, "Comprei errado")

    def test_approving_cancels_the_order(self):
        self.em_producao(self.order)
        services.request_cancellation(self.order, "Comprei errado")
        services.approve_cancellation(self.order, "Sucata.", restore_stock=False)

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertIsNotNone(self.order.cancelled_at)

    def test_approving_opens_the_refund_but_does_not_pay_it(self):
        """Aprovar abre o processo. Quem devolve o dinheiro é uma pessoa."""
        self.em_producao(self.order)
        services.request_cancellation(self.order, "Mudei de ideia")
        services.approve_cancellation(self.order, restore_stock=False)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PAID)
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))

    def test_approving_stops_production(self):
        self.em_producao(self.order)
        services.request_cancellation(self.order, "Mudei de ideia")
        services.approve_cancellation(self.order, restore_stock=False)

        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)

    def test_refusing_keeps_the_order_alive(self):
        self.em_producao(self.order)
        services.request_cancellation(self.order, "Mudei de ideia")
        services.refuse_cancellation(self.order, "A peça já está na impressora.")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REFUSED)
        self.assertEqual(self.order.status, OrderStatus.CONFIRMED)
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.IN_PRODUCTION)
        self.assertEqual(self.order.cancellation_decision_note, "A peça já está na impressora.")

    def test_a_decision_needs_a_request(self):
        self.em_producao(self.order)

        self.assertFalse(services.approve_cancellation(self.order, restore_stock=False))
        self.assertFalse(services.refuse_cancellation(self.order))

    def test_second_request_is_ignored(self):
        self.em_producao(self.order)
        services.request_cancellation(self.order, "Primeiro")

        self.assertFalse(services.request_cancellation(self.order, "Segundo"))

    def test_a_shipped_order_can_still_be_asked_about(self):
        """Enviado não é o fim do direito: o prazo legal nem começou a correr."""
        self.em_producao(self.order)
        services.mark_shipped(self.order, "BE123")
        self.order.refresh_from_db()

        self.assertTrue(self.order.can_request_cancellation)
        self.assertTrue(services.request_cancellation(self.order, "Quero devolver"))

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)


class ShippingTests(TestCase):
    def setUp(self):
        self.country = make_country("BE")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="diego3d")
        self.address = make_address(self.user.customer, self.country)
        self.product = make_product(sku="P1", name="Vaso", price=Decimal("10.00"), stock_quantity=5)
        self.order = services.create_order(
            customer=self.user.customer,
            lines=[line_for(self.product)],
            shipping_address=self.address,
            billing_address=self.address,
            shipping_method=self.method,
        )

    def test_marking_shipped_records_everything(self):
        services.mark_shipped(self.order, "BE123456789")

        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.SHIPPED)
        self.assertEqual(self.order.tracking_number, "BE123456789")
        self.assertIsNotNone(self.order.shipped_at)
        self.assertTrue(self.order.history.filter(event=OrderEvent.SHIPPED).exists())

    def test_marking_shipped_twice_does_not_resend_the_email(self):
        from django.core import mail

        services.mark_shipped(self.order, "BE123")
        mail.outbox = []

        self.assertFalse(services.mark_shipped(self.order, "BE123"))
        self.assertEqual(len(mail.outbox), 0)

    def test_tracking_url_uses_the_carrier_template(self):
        carrier = self.method.carrier
        carrier.tracking_url_template = "https://track.example/{tracking}"
        carrier.save()

        services.mark_shipped(self.order, "BE123")
        self.order.refresh_from_db()

        self.assertEqual(self.order.tracking_url, "https://track.example/BE123")
