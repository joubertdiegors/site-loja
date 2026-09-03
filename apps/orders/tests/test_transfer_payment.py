"""Pagamento por transferência bancária — etapa 20.

Provisório e assumidamente provisório: a loja precisa vender antes de o gateway
existir. O que estes testes guardam:

1. **o servidor decide o meio de pagamento** — nada do que o navegador mandar
   muda qual provedor cobra;
2. **o pedido não nasce pago** — quem confirma recebimento é uma pessoa, no
   Admin, depois de ver o extrato;
3. **o pedido sobrevive à falha de e-mail** — provedor fora do ar não pode
   apagar uma venda.
"""

from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.orders.models import (
    BankAccount,
    Order,
    OrderStatus,
    PaymentState,
    PaymentStatus,
)
from apps.orders.payments import PROVIDERS, WebhookError, get_provider
from apps.orders.payments.transfer_provider import TransferProvider
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
)

TRANSFER = override_settings(PAYMENT_PROVIDER="transfer")


class TransferBase(LanguageResetMixin, TestCase):
    """Um carrinho pronto para fechar, com endereço e frete."""

    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "4.90")

        self.user = make_user(username="ana", email="ana@exemplo.test")
        self.customer = self.user.customer
        self.customer.first_name = "Ana"
        self.customer.last_name = "Ribeiro"
        self.customer.save()
        self.address = make_address(
            self.customer, self.country,
            first_name="Ana", last_name="Ribeiro", street="Rue du Test 1",
        )

        self.product = make_product(
            sku="TRANSF-01", name="Vaso Espiral", category=self.category,
            price=Decimal("19.90"), stock_quantity=10, weight_grams=Decimal("300"),
        )
        # A conta padrão faz parte do cenário: sem ela a loja não aceita
        # transferência, e é isso que `SemContaPadraoTests` cobra.
        self.account = BankAccount.objects.create(
            label="Principal",
            beneficiary="JD PRINT SRL",
            iban="BE68 5390 0754 7034",
            bic="GEBABEBB",
            instructions="Use o número do pedido na comunicação.",
            is_default=True,
        )
        self.client.force_login(self.user)

    def add_to_cart(self, quantity=1):
        self.client.post(
            reverse("cart:add"),
            {"product_id": self.product.pk,
             "variant_id": self.product.default_variant.pk,
             "quantity": str(quantity)},
        )

    def checkout(self, prefix=""):
        self.add_to_cart()
        return self.client.post(
            f"{prefix}/carrinho/finalizar/",
            {
                "shipping_address": self.address.pk,
                "billing_same_as_shipping": "on",
                "shipping_method": self.method.pk,
            },
            follow=True,
        )


# ---------------------------------------------------------------------------
# O provedor
# ---------------------------------------------------------------------------


class TransferProviderTests(TestCase):
    def test_it_is_a_registered_payment_method(self):
        self.assertIn("transfer", PROVIDERS)

    @TRANSFER
    def test_the_server_setting_decides_which_provider_charges(self):
        self.assertIsInstance(get_provider(), TransferProvider)

    def test_it_needs_no_credentials(self):
        """Conta bancária não é segredo de código: nunca fica indisponível."""
        self.assertTrue(TransferProvider().is_configured)

    def test_it_never_accepts_a_notification(self):
        """Não há webhook: quem vê o dinheiro entrar é uma pessoa."""
        provider = TransferProvider()

        with self.assertRaises(WebhookError):
            provider.parse_webhook(b"{}", "assinatura")
        with self.assertRaises(WebhookError):
            provider.handle(object())

    def test_stripe_is_still_available(self):
        """A etapa é provisória: trocar de volta é uma linha no `.env`."""
        self.assertIn("stripe", PROVIDERS)


# ---------------------------------------------------------------------------
# O pedido
# ---------------------------------------------------------------------------


@TRANSFER
class TransferCheckoutTests(TransferBase):
    def test_the_order_is_created(self):
        self.checkout()

        pedido = Order.objects.get()
        self.assertEqual(pedido.customer, self.customer)
        self.assertEqual(pedido.items.count(), 1)

    def test_the_order_is_not_paid(self):
        """O ponto da etapa: registrado não é pago."""
        self.checkout()

        pedido = Order.objects.get()
        self.assertEqual(pedido.payment_status, PaymentStatus.PENDING)
        self.assertEqual(pedido.status, OrderStatus.PENDING)
        self.assertFalse(pedido.is_paid)
        self.assertIsNone(pedido.paid_at)

    def test_the_payment_attempt_records_the_method(self):
        self.checkout()

        pagamento = Order.objects.get().payments.get()
        self.assertEqual(pagamento.provider, "transfer")
        self.assertEqual(pagamento.status, PaymentState.CREATED)
        self.assertEqual(pagamento.method_label, "Transferência bancária")

    def test_the_amount_comes_from_the_order_and_not_from_the_browser(self):
        self.add_to_cart(quantity=2)
        self.client.post(
            "/carrinho/finalizar/",
            {
                "shipping_address": self.address.pk,
                "billing_same_as_shipping": "on",
                "shipping_method": self.method.pk,
                # Tudo abaixo é ruído: nada disso é lido.
                #
                # `payment_method` saiu daqui porque deixou de ser ruído: ele é
                # um campo de verdade, e forjá-lo tem teste próprio
                # (`FormaDePagamentoTests`). Mandá-lo inválido aqui recusaria o
                # pedido inteiro e este teste deixaria de falar do valor.
                "amount": "0.01",
                "total": "0.01",
                "provider": "stripe",
            },
            follow=True,
        )

        pedido = Order.objects.get()
        pagamento = pedido.payments.get()
        self.assertEqual(pagamento.amount, pedido.total)
        self.assertEqual(pagamento.provider, "transfer")
        self.assertGreater(pedido.total, Decimal("0.01"))

    def test_the_customer_lands_on_the_confirmation(self):
        resposta = self.checkout()

        pedido = Order.objects.get()
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, pedido.number)

    def test_the_confirmation_says_the_details_were_already_sent(self):
        """A promessa mudou, e é essa a diferença da etapa.

        Antes: "entraremos em contato para enviar os dados" — o cliente ficava
        esperando alguém da equipe agir. Agora o e-mail já saiu quando esta
        página carrega, e é isso que ela diz.
        """
        resposta = self.checkout()

        self.assertContains(resposta, "Pedido recebido")
        self.assertContains(resposta, "Enviamos para")
        self.assertContains(resposta, self.user.email)
        self.assertContains(resposta, "Enviar comprovante")
        self.assertNotContains(resposta, "Stripe")

    def test_the_cart_is_emptied(self):
        self.checkout()

        self.assertEqual(self.client.get(reverse("cart:detail")).context["cart"].total_quantity, 0)

    def test_the_checkout_page_offers_only_bank_transfer(self):
        self.add_to_cart()

        html = self.client.get("/carrinho/finalizar/").content.decode()

        self.assertIn("Transferência bancária", html)
        self.assertNotIn("Stripe", html)
        self.assertNotIn("cartão não passam", html)


# ---------------------------------------------------------------------------
# O aviso para a loja
# ---------------------------------------------------------------------------


@TRANSFER
@override_settings(ORDER_ADMIN_EMAILS=["loja@jdprint.test"])
class TransferStoreEmailTests(TransferBase):
    """O aviso que vai para a **equipe** — não o que vai para o cliente.

    Desde que os dados bancários passaram a sair sozinhos, o checkout manda
    dois e-mails. Procurar pelo destinatário, e não pelo índice, é o que faz
    estes testes continuarem falando do aviso interno quando um terceiro
    e-mail entrar na fila.
    """

    def setUp(self):
        super().setUp()
        mail.outbox = []

    def aviso(self):
        """O e-mail endereçado à equipe."""
        internos = [m for m in mail.outbox if m.to == ["loja@jdprint.test"]]
        self.assertEqual(len(internos), 1, f"esperava um aviso interno, veio {len(internos)}")
        return internos[0]

    def test_the_store_is_notified(self):
        self.checkout()

        self.assertEqual(self.aviso().to, ["loja@jdprint.test"])

    def test_the_notice_carries_what_the_team_needs(self):
        self.checkout()

        pedido = Order.objects.get()
        aviso = self.aviso()
        corpo = aviso.body
        self.assertIn(pedido.number, aviso.subject)
        self.assertIn(pedido.number, corpo)
        self.assertIn("Ana Ribeiro", corpo)
        self.assertIn("ana@exemplo.test", corpo)
        # No assunto o valor vai cru; no corpo, formatado no idioma da loja.
        self.assertIn(f"{pedido.total:.2f}", aviso.subject)
        self.assertIn(f"{pedido.total:.2f}".replace(".", ","), corpo)
        self.assertIn("Transferência bancária", corpo)
        self.assertIn("Rue du Test 1", corpo)
        # O template imprime no fuso da loja; o campo guarda em UTC.
        from django.utils.timezone import localtime

        self.assertIn(localtime(pedido.created_at).strftime("%d/%m/%Y"), corpo)

    def test_the_notice_is_in_portuguese_even_for_a_french_order(self):
        """Quem lê é a equipe."""
        self.add_to_cart()
        self.client.post(
            "/fr/carrinho/finalizar/",
            {
                "shipping_address": self.address.pk,
                "billing_same_as_shipping": "on",
                "shipping_method": self.method.pk,
            },
            follow=True,
        )

        self.assertIn("aguardando transferência", self.aviso().subject)

    def test_it_names_the_accounts_available_without_spelling_out_the_iban(self):
        """O aviso interno diz que há conta; não é de onde se copia o IBAN.

        Quem manda os dados ao cliente é a ação do Admin, que escolhe a conta.
        Este e-mail é interno e se reencaminha como qualquer outro — o IBAN
        inteiro fica no cadastro e no e-mail que o cliente pediu.
        """
        BankAccount.objects.create(
            label="Segunda",
            beneficiary="JD PRINT SRL",
            iban="BE00 0000 0000 0000",
            bic="GEBABEBB",
        )

        self.checkout()

        corpo = self.aviso().body
        self.assertIn("Segunda", corpo)
        self.assertIn("BE00 ···· 0000", corpo)
        self.assertNotIn("BE00 0000 0000 0000", corpo)

    def test_an_inactive_account_is_not_offered(self):
        BankAccount.objects.create(
            label="Antiga", beneficiary="JD PRINT SRL", iban="BE11 1111 1111 1111",
            is_active=False,
        )

        self.checkout()

        self.assertNotIn("Antiga", self.aviso().body)

    def test_without_any_account_the_checkout_does_not_get_that_far(self):
        """Sem conta padrão não há pedido — e portanto não há aviso.

        Era um cenário possível quando o e-mail era só um lembrete para a
        equipe mandar os dados à mão. Agora a conta é condição da venda:
        `create_order` recusa antes, com uma mensagem para o cliente
        (ver `SemContaPadraoTests`).
        """
        BankAccount.objects.all().delete()

        self.checkout()

        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_a_dead_smtp_never_loses_the_order(self):
        """Provedor de e-mail fora do ar não pode apagar uma venda."""
        with patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("sem rede")
        ):
            resposta = self.checkout()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get().payments.count(), 1)

    def test_the_production_order_is_not_sent_before_the_money(self):
        """A oficina não pode imprimir antes de o pagamento entrar."""
        self.checkout()

        self.assertIsNone(Order.objects.get().admin_email_sent_at)
        self.assertNotIn("Novo pedido para produzir", mail.outbox[0].body)


# ---------------------------------------------------------------------------
# Idiomas
# ---------------------------------------------------------------------------


@TRANSFER
class TransferLanguageTests(TransferBase):
    def test_the_flow_works_in_the_four_languages(self):
        esperado = {
            "": "Pedido recebido",
            "/fr": "Commande reçue",
            "/nl": "Bestelling ontvangen",
            "/en": "Order received",
        }
        for prefixo, trecho in esperado.items():
            with self.subTest(idioma=prefixo or "pt"):
                Order.objects.all().delete()
                resposta = self.checkout(prefix=prefixo)
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(Order.objects.count(), 1)
                self.assertContains(resposta, trecho)


# ---------------------------------------------------------------------------
# Dados bancários
# ---------------------------------------------------------------------------


class BankAccountTests(TestCase):
    """A conta deixou de ser singleton — era o que impedia escolher.

    Enquanto os dados só apareciam num aviso interno, uma linha bastava:
    ninguém escolhia nada, alguém copiava o IBAN. A partir do momento em que o
    operador **seleciona** a conta antes de enviar (ver
    `EnvioDeDadosBancariosTests`), uma linha só não responde à pergunta.
    """

    def test_more_than_one_account_can_exist(self):
        BankAccount.objects.create(label="Principal", beneficiary="JD PRINT SRL", iban="BE00")
        BankAccount.objects.create(label="Segunda", beneficiary="JD PRINT SRL", iban="BE11")

        self.assertEqual(BankAccount.objects.count(), 2)

    def test_it_is_only_usable_with_a_beneficiary_and_an_iban(self):
        linha = BankAccount(beneficiary="JD PRINT SRL")
        self.assertFalse(linha.is_complete)

        linha.iban = "BE00 0000 0000 0000"
        self.assertTrue(linha.is_complete)

    def test_usable_leaves_out_the_inactive_and_the_incomplete(self):
        """A lista de escolha não pode oferecer o que não dá para enviar."""
        boa = BankAccount.objects.create(beneficiary="JD", iban="BE00 0000 0000 0000")
        BankAccount.objects.create(beneficiary="JD", iban="BE11", is_active=False)
        BankAccount.objects.create(beneficiary="", iban="BE22")  # sem titular
        BankAccount.objects.create(beneficiary="JD", iban="")  # sem IBAN

        self.assertEqual(list(BankAccount.objects.usable()), [boa])

    def test_the_masked_iban_shows_only_the_ends(self):
        """Para identificar a conta no histórico e na lista, não para usá-la."""
        conta = BankAccount(iban="BE68 5390 0754 7034")

        self.assertEqual(conta.masked_iban, "BE68 ···· 7034")

    def test_a_short_iban_is_not_broken_by_the_mask(self):
        self.assertEqual(BankAccount(iban="BE68").masked_iban, "BE68")

    def test_it_holds_no_customer_data(self):
        """A loja recebe uma transferência; o IBAN de quem paga não é nosso.

        A lista é fechada de propósito: um campo novo nesta tabela tem que
        passar por aqui antes de existir.
        """
        campos = {campo.name for campo in BankAccount._meta.fields}

        self.assertEqual(
            campos,
            {
                "id", "created_at", "updated_at",
                "label", "beneficiary", "iban", "bic", "instructions",
                "is_default", "is_active", "sort_order",
            },
        )
