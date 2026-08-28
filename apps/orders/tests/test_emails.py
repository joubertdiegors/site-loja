"""Os três e-mails do pedido: conteúdo, idioma e envio único.

O e-mail administrativo é testado com o mesmo cuidado que o do cliente: ele é
a ordem de produção, e faltar um SKU ou o link do arquivo enviado significa
alguém abrindo o Admin no meio da impressão.
"""

from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings

from apps.cart.cart import CartLine
from apps.cart.models import CustomizationUpload
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
from apps.orders.emails import (
    send_admin_order_email,
    send_order_confirmation_email,
    send_order_shipped_email,
)

ADMINS = ["producao@jd-print.test"]


@override_settings(ORDER_ADMIN_EMAILS=ADMINS)
class OrderEmailBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")

        self.user = make_user(username="diego3d", email="diego@example.com")
        self.customer = self.user.customer
        self.customer.first_name = "Diego"
        self.customer.last_name = "Joubert"
        self.customer.phone = "+32470123456"
        self.customer.save()

        self.address = make_address(self.customer, self.country)
        self.product = make_product(
            sku="CHAVE-01",
            name="Chaveiro com Nome",
            price=Decimal("7.90"),
            stock_quantity=10,
            production_lead_time_days=3,
        )
        self.variant = self.product.default_variant
        self.product.personalization_type = "text"
        self.product.save()

        self.upload = CustomizationUpload.objects.create(
            original_name="foto-do-lucas.jpg", extension="jpg"
        )
        self.upload.file.name = "customizations/abc.jpg"
        self.upload.save()

        self.order = self.make_order()
        mail.outbox = []

    def make_order(self, **overrides):
        options = {
            "customer": self.customer,
            "lines": [
                CartLine(
                    key="k",
                    product=self.product,
                    variant=self.variant,
                    quantity=2,
                    customization={"type": "text", "text": "Lucas", "notes": "fonte maior"},
                )
            ],
            "shipping_address": self.address,
            "billing_address": self.address,
            "shipping_method": self.method,
            "language": "pt-br",
        }
        options.update(overrides)
        return services.create_order(**options)


class CustomerEmailTests(OrderEmailBase):
    def test_confirmation_goes_to_the_customer(self):
        send_order_confirmation_email(self.order)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["diego@example.com"])
        self.assertIn(self.order.number, mail.outbox[0].subject)

    def test_confirmation_carries_the_whole_order(self):
        send_order_confirmation_email(self.order)
        body = mail.outbox[0].body

        self.assertIn("Chaveiro com Nome", body)
        self.assertIn("Lucas", body)  # personalização
        self.assertIn("Rue du Test 12", body)  # endereço de entrega
        self.assertIn("15,80", body)  # subtotal
        self.assertIn("5,90", body)  # frete
        self.assertIn("21,70", body)  # total
        self.assertIn("5–6", body)  # prazo estimado

    def test_confirmation_has_an_html_alternative(self):
        send_order_confirmation_email(self.order)

        alternatives = mail.outbox[0].alternatives
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(alternatives[0][1], "text/html")
        self.assertIn(self.order.number, alternatives[0][0])

    def test_confirmation_is_sent_only_once(self):
        self.assertTrue(send_order_confirmation_email(self.order))
        self.assertFalse(send_order_confirmation_email(self.order))

        self.assertEqual(len(mail.outbox), 1)

    def test_confirmation_records_when_it_went_out(self):
        send_order_confirmation_email(self.order)

        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.confirmation_email_sent_at)

    def test_no_sensitive_data_travels(self):
        """Nem a senha da conta, nem nada de cartão.

        (A palavra "senha" aparece no rodapé de todo e-mail — "a JD PRINT nunca
        pede senha" —, então o que se procura aqui é o **valor**.)
        """
        send_order_confirmation_email(self.order)
        body = mail.outbox[0].body.lower()

        self.assertNotIn("senha-de-teste-77", body)
        self.assertNotIn(self.user.password.lower(), body)
        for forbidden in ("cvv", "pbkdf2", "número do cartão"):
            self.assertNotIn(forbidden, body)


class EmailLanguageTests(OrderEmailBase):
    def test_email_follows_the_language_of_the_order(self):
        """Comprou em /fr/ recebe em francês — mesmo três meses depois."""
        order = self.make_order(language="fr")

        send_order_confirmation_email(order)

        self.assertIn("Commande", mail.outbox[0].subject)

    def test_english_order_gets_an_english_email(self):
        order = self.make_order(language="en")

        send_order_confirmation_email(order)

        self.assertIn("Order", mail.outbox[0].subject)

    def test_language_of_the_order_wins_over_the_active_one(self):
        from django.utils import translation

        order = self.make_order(language="fr")

        with translation.override("pt-br"):
            send_order_confirmation_email(order)

        self.assertIn("Commande", mail.outbox[0].subject)

    def test_order_without_language_falls_back_to_the_customer(self):
        self.user.preferred_language = "nl"
        self.user.save()
        order = self.make_order(language="")

        send_order_confirmation_email(order)

        self.assertIn("Bestelling", mail.outbox[0].subject)


class AdminEmailTests(OrderEmailBase):
    def test_admin_email_goes_to_the_configured_addresses(self):
        send_admin_order_email(self.order)

        self.assertEqual(mail.outbox[0].to, ADMINS)

    def test_admin_email_is_written_for_production(self):
        send_admin_order_email(self.order)
        body = mail.outbox[0].body

        self.assertIn(self.order.number, body)
        self.assertIn("CHAVE-01", body)  # SKU
        self.assertIn("Lucas", body)  # o que gravar na peça
        self.assertIn("fonte maior", body)  # observação da personalização
        self.assertIn("diego@example.com", body)  # contato
        self.assertIn("+32470123456", body)
        self.assertIn("Rue du Test 12", body)  # para onde vai

    def test_admin_email_carries_the_production_lead_time(self):
        send_admin_order_email(self.order)

        self.assertIn("3", mail.outbox[0].body)

    def test_admin_email_links_the_uploaded_file(self):
        """Sem o link, alguém tem que abrir o Admin para achar a foto."""
        order = self.make_order(
            lines=[
                CartLine(
                    key="k2",
                    product=self.product,
                    variant=self.variant,
                    quantity=1,
                    customization={"type": "photo", "upload_id": self.upload.pk},
                    upload=self.upload,
                )
            ]
        )
        mail.outbox = []

        send_admin_order_email(order)

        self.assertIn("foto-do-lucas.jpg", mail.outbox[0].body)
        self.assertIn("customizations/abc.jpg", mail.outbox[0].body)

    def test_admin_email_flags_a_gift(self):
        order = self.make_order(is_gift=True)
        mail.outbox = []

        send_admin_order_email(order)

        self.assertIn("PRESENTE", mail.outbox[0].body.upper())

    def test_admin_email_is_in_portuguese_even_for_a_french_order(self):
        """Quem lê é a equipe, não o cliente."""
        order = self.make_order(language="fr")
        mail.outbox = []

        send_admin_order_email(order)

        self.assertIn("Novo pedido", mail.outbox[0].subject)

    def test_without_configured_addresses_nothing_is_sent(self):
        with override_settings(ORDER_ADMIN_EMAILS=[]):
            self.assertFalse(send_admin_order_email(self.order))

        self.assertEqual(len(mail.outbox), 0)


class ShippedEmailTests(OrderEmailBase):
    def test_shipped_email_carries_the_tracking(self):
        carrier = self.method.carrier
        carrier.tracking_url_template = "https://track.example/{tracking}"
        carrier.save()

        services.mark_shipped(self.order, "BE123456789")

        body = mail.outbox[-1].body
        self.assertIn("BE123456789", body)
        self.assertIn("https://track.example/BE123456789", body)

    def test_shipped_email_is_sent_only_once(self):
        services.mark_shipped(self.order, "BE123")
        mail.outbox = []

        self.assertFalse(send_order_shipped_email(self.order))
        self.assertEqual(len(mail.outbox), 0)

    def test_order_without_tracking_still_notifies(self):
        services.mark_shipped(self.order)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.order.number, mail.outbox[0].body)


class ConfirmationFlowTests(OrderEmailBase):
    def test_confirming_the_payment_sends_both_emails(self):
        services.confirm_payment(self.order)

        recipients = [address for message in mail.outbox for address in message.to]
        self.assertIn("diego@example.com", recipients)
        self.assertIn(ADMINS[0], recipients)

    def test_confirming_twice_does_not_resend(self):
        services.confirm_payment(self.order)
        count = len(mail.outbox)

        services.confirm_payment(self.order)

        self.assertEqual(len(mail.outbox), count)

    def test_a_broken_mail_server_does_not_undo_the_payment(self):
        """Pagamento confirmado é pagamento confirmado."""
        from unittest import mock

        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp caiu")
        ):
            services.confirm_payment(self.order)

        self.order.refresh_from_db()
        self.assertTrue(self.order.is_paid)
