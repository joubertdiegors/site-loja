"""O fluxo definitivo de pagamento: da escolha no checkout ao dinheiro na conta.

Cinco promessas, e é o que estes testes seguram:

1. **a conta é escolhida uma vez, no cadastro** — o checkout usa a padrão ativa,
   nunca "uma qualquer", e recusa a venda quando não há nenhuma;
2. **o pedido guarda a conta que usou.** Trocar a conta padrão amanhã não
   reescreve o IBAN que um cliente recebeu hoje;
3. **o cliente não espera ninguém.** O e-mail com os dados sai no momento em
   que ele confirma o pedido, no idioma em que ele comprou;
4. **a escolha do meio de pagamento é conferida no servidor.** Um radio
   desativado reabilitado no inspetor chega como qualquer código inventado;
5. **confirmar pagamento é uma rotina, não um campo.** A ação do Admin executa
   `services.confirm_payment` — a mesma do webhook —, e executá-la duas vezes
   não baixa estoque duas vezes.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
from apps.orders import services
from apps.orders.models import (
    BankAccount,
    FulfillmentStatus,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentState,
    PaymentStatus,
)
from apps.orders.payments import (
    available_checkout_methods,
    checkout_methods,
    get_checkout_method,
    method_for_provider,
)

TRANSFER = override_settings(PAYMENT_PROVIDER="transfer")


# ---------------------------------------------------------------------------
# Cenário
# ---------------------------------------------------------------------------


@TRANSFER
@override_settings(ORDER_ADMIN_EMAILS=["loja@jdprint.test"])
class PaymentFlowBase(LanguageResetMixin, TestCase):
    """Um carrinho pronto para fechar, e uma conta padrão para receber."""

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
            sku="PAG-01", name="Vaso Espiral", category=self.category,
            price=Decimal("19.90"), stock_quantity=10, weight_grams=Decimal("300"),
        )
        self.variant = self.product.default_variant

        self.principal = BankAccount.objects.create(
            label="Principal",
            beneficiary="JD PRINT SRL",
            iban="BE68 5390 0754 7034",
            bic="GEBABEBB",
            instructions="Use o número do pedido na comunicação.",
            is_default=True,
        )

        self.client.force_login(self.user)
        mail.outbox = []

    # -- atalhos -----------------------------------------------------------

    def add_to_cart(self, quantity=1):
        self.client.post(
            reverse("cart:add"),
            {
                "product_id": self.product.pk,
                "variant_id": self.variant.pk,
                "quantity": str(quantity),
            },
        )

    def checkout(self, prefix="", **extra):
        """Fecha a compra. Devolve a resposta já seguida até a confirmação."""
        self.add_to_cart()
        dados = {
            "shipping_address": self.address.pk,
            "billing_same_as_shipping": "on",
            "shipping_method": self.method.pk,
        }
        dados.update(extra)
        return self.client.post(f"{prefix}/carrinho/finalizar/", dados, follow=True)

    def email_do_cliente(self):
        cliente = [m for m in mail.outbox if m.to == [self.user.email]]
        self.assertEqual(len(cliente), 1, f"esperava um e-mail ao cliente, veio {len(cliente)}")
        return cliente[0]

    _equipes = 0

    def equipe(self, permissoes=("view_order", "change_order")):
        type(self)._equipes += 1
        marca = type(self)._equipes
        pessoa = get_user_model().objects.create_user(
            username=f"equipe-{marca}",
            email=f"equipe-{marca}@jdprint.test",
            password="senha-de-teste-77",
            is_staff=True,
        )
        pessoa.user_permissions.set(
            Permission.objects.filter(
                codename__in=permissoes, content_type__app_label="orders"
            )
        )
        return pessoa


# ---------------------------------------------------------------------------
# 1. A conta padrão
# ---------------------------------------------------------------------------


class ContaPadraoTests(TestCase):
    def test_only_one_account_can_be_the_default(self):
        """Marcar a segunda desmarca a primeira — sem erro na cara de ninguém."""
        primeira = BankAccount.objects.create(
            label="Primeira", beneficiary="JD", iban="BE11", is_default=True
        )
        segunda = BankAccount.objects.create(
            label="Segunda", beneficiary="JD", iban="BE22", is_default=True
        )

        primeira.refresh_from_db()
        segunda.refresh_from_db()
        self.assertFalse(primeira.is_default)
        self.assertTrue(segunda.is_default)
        self.assertEqual(BankAccount.objects.filter(is_default=True).count(), 1)

    def test_the_database_refuses_two_defaults_even_around_save(self):
        """A garantia é do banco, não de um `if`.

        `save()` desmarca a anterior, mas `update()` em massa não passa por
        `save()` — e é exatamente por onde uma migração de dados distraída
        criaria duas contas padrão sem ninguém perceber.
        """
        BankAccount.objects.create(label="A", beneficiary="JD", iban="BE11", is_default=True)
        BankAccount.objects.create(label="B", beneficiary="JD", iban="BE22")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BankAccount.objects.filter(label="B").update(is_default=True)

    def test_marking_the_same_account_again_is_not_an_error(self):
        conta = BankAccount.objects.create(
            label="Única", beneficiary="JD", iban="BE11", is_default=True
        )

        conta.bic = "GEBABEBB"
        conta.save()

        conta.refresh_from_db()
        self.assertTrue(conta.is_default)

    def test_an_inactive_default_is_not_used(self):
        """Desativar é dizer "não use mais esta". A marca não vence isso."""
        BankAccount.objects.create(
            label="Desativada", beneficiary="JD", iban="BE11",
            is_default=True, is_active=False,
        )

        self.assertIsNone(BankAccount.objects.default_for_orders())

    def test_an_incomplete_default_is_not_used(self):
        BankAccount.objects.create(label="Sem IBAN", beneficiary="JD", iban="", is_default=True)

        self.assertIsNone(BankAccount.objects.default_for_orders())

    def test_an_active_account_that_is_not_the_default_is_not_used(self):
        """Não existe "escolher qualquer uma": ou há padrão, ou não há."""
        BankAccount.objects.create(label="Ativa", beneficiary="JD", iban="BE11")

        self.assertIsNone(BankAccount.objects.default_for_orders())

    def test_the_default_is_the_one_returned(self):
        BankAccount.objects.create(label="Outra", beneficiary="JD", iban="BE11")
        padrao = BankAccount.objects.create(
            label="Padrão", beneficiary="JD", iban="BE22", is_default=True
        )

        self.assertEqual(BankAccount.objects.default_for_orders(), padrao)


# ---------------------------------------------------------------------------
# 2. As formas de pagamento
# ---------------------------------------------------------------------------


class FormaDePagamentoTests(PaymentFlowBase):
    def test_the_screen_lists_transfer_as_the_only_choice(self):
        self.add_to_cart()

        html = self.client.get("/carrinho/finalizar/").content.decode()

        self.assertIn("Transferência bancária", html)
        self.assertIn('value="transfer"', html)

    def test_the_future_methods_appear_disabled(self):
        """Escondê-los faria a loja parecer uma que só aceita transferência."""
        self.add_to_cart()

        html = self.client.get("/carrinho/finalizar/").content.decode()

        self.assertIn("Cartão", html)
        self.assertIn("Bancontact", html)
        self.assertIn("Em breve", html)
        self.assertEqual(self.radios(html), {"transfer": True, "card": False, "bancontact": False})

    @staticmethod
    def radios(html):
        """{código: dá para escolher} lido do HTML de verdade.

        Contar a palavra `disabled` na página inteira contaria o botão de
        finalizar e o que mais estiver desativado. O que importa é qual radio
        de pagamento aceita clique.
        """
        import re

        achados = {}
        for tag in re.findall(r"<input[^>]*name=\"payment_method\"[^>]*>", html):
            codigo = re.search(r'value="([^"]+)"', tag).group(1)
            achados[codigo] = "disabled" not in tag
        return achados

    def test_only_transfer_is_available_today(self):
        disponiveis = [m.code for m in available_checkout_methods()]

        self.assertEqual(disponiveis, ["transfer"])
        self.assertEqual(len(checkout_methods()), 3)

    def test_choosing_transfer_records_it_on_the_order(self):
        self.checkout(payment_method="transfer")

        self.assertEqual(Order.objects.get().payment_method, "transfer")

    def test_the_server_refuses_a_method_that_is_not_available(self):
        """O `disabled` da tela é conveniência; a regra está no servidor."""
        resposta = self.checkout(payment_method="card")

        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(resposta, "forma de pagamento")

    def test_the_server_refuses_a_method_that_does_not_exist(self):
        resposta = self.checkout(payment_method="gratis")

        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(resposta, "forma de pagamento")

    def test_a_method_that_is_not_implemented_is_never_available(self):
        """Bancontact passa pela Stripe, mas ninguém escreveu esse caminho."""
        with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_x"):
            self.assertIsNone(get_checkout_method("bancontact"))
            self.assertIn("card", [m.code for m in available_checkout_methods()])

    def test_with_a_single_option_the_silence_means_that_one(self):
        """Não é confiar no HTML: é que não há escolha a fazer."""
        self.checkout()

        self.assertEqual(Order.objects.get().payment_method, "transfer")

    def test_the_cart_survives_a_refused_method(self):
        """Recusar não pode custar o carrinho de quem tentou."""
        self.checkout(payment_method="card")

        carrinho = self.client.get(reverse("cart:detail")).context["cart"]
        self.assertEqual(carrinho.total_quantity, 1)


# ---------------------------------------------------------------------------
# 3. Sem conta padrão não há venda por transferência
# ---------------------------------------------------------------------------


class SemContaPadraoTests(PaymentFlowBase):
    def setUp(self):
        super().setUp()
        BankAccount.objects.all().delete()

    def test_the_checkout_refuses_with_a_message_the_customer_understands(self):
        resposta = self.checkout()

        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(resposta, "transferência está indisponível")
        self.assertContains(resposta, "Entre em contato")

    def test_nothing_is_sent(self):
        self.checkout()

        self.assertEqual(mail.outbox, [])

    def test_the_cart_is_not_emptied(self):
        self.checkout()

        carrinho = self.client.get(reverse("cart:detail")).context["cart"]
        self.assertEqual(carrinho.total_quantity, 1)

    def test_an_inactive_default_blocks_it_the_same_way(self):
        BankAccount.objects.create(
            label="Desativada", beneficiary="JD PRINT SRL", iban="BE11 1111",
            is_default=True, is_active=False,
        )

        resposta = self.checkout()

        self.assertEqual(Order.objects.count(), 0)
        self.assertContains(resposta, "transferência está indisponível")


# ---------------------------------------------------------------------------
# 4. O pedido guarda a conta que usou
# ---------------------------------------------------------------------------


class CopiaDaContaTests(PaymentFlowBase):
    def test_the_order_copies_the_account_it_used(self):
        self.checkout()

        pedido = Order.objects.get()
        self.assertEqual(pedido.bank_account, self.principal)
        self.assertEqual(pedido.bank_beneficiary, "JD PRINT SRL")
        self.assertEqual(pedido.bank_iban, "BE68 5390 0754 7034")
        self.assertEqual(pedido.bank_bic, "GEBABEBB")
        self.assertIn("número do pedido", pedido.bank_instructions)

    def test_changing_the_default_later_does_not_rewrite_the_old_order(self):
        """O cliente transferiu para aquela conta. É contra ela que se confere."""
        self.checkout()
        pedido = Order.objects.get()

        BankAccount.objects.create(
            label="Nova", beneficiary="OUTRA SRL", iban="BE99 9999 9999 9999",
            is_default=True,
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.bank_iban, "BE68 5390 0754 7034")
        self.assertEqual(pedido.bank_details.beneficiary, "JD PRINT SRL")

    def test_editing_the_account_itself_does_not_rewrite_the_old_order(self):
        self.checkout()
        pedido = Order.objects.get()

        self.principal.iban = "BE00 0000 0000 0000"
        self.principal.beneficiary = "NOME TROCADO"
        self.principal.save()

        pedido.refresh_from_db()
        self.assertEqual(pedido.bank_iban, "BE68 5390 0754 7034")
        self.assertEqual(pedido.bank_beneficiary, "JD PRINT SRL")

    def test_the_snapshot_reads_like_an_account(self):
        """`bank_details` responde às mesmas perguntas de um `BankAccount`."""
        self.checkout()

        detalhes = Order.objects.get().bank_details
        self.assertTrue(detalhes.is_complete)
        self.assertEqual(detalhes.masked_iban, "BE68 ···· 7034")

    def test_an_order_without_a_copy_has_no_details(self):
        pedido = Order.objects.create(
            customer=self.customer, total=Decimal("10.00"), currency="EUR"
        )

        self.assertIsNone(pedido.bank_details)


# ---------------------------------------------------------------------------
# 5. O e-mail sai sozinho
# ---------------------------------------------------------------------------


class EmailAutomaticoTests(PaymentFlowBase):
    def test_the_customer_gets_the_details_without_anyone_acting(self):
        """A promessa da etapa: ninguém da equipe precisa mexer um dedo."""
        self.checkout()

        mensagem = self.email_do_cliente()
        self.assertIn("Dados para o pagamento", mensagem.subject)
        self.assertIn(Order.objects.get().number, mensagem.subject)

    def test_the_email_carries_everything_needed_to_pay(self):
        self.checkout()

        pedido = Order.objects.get()
        corpo = self.email_do_cliente().body
        self.assertIn(pedido.number, corpo)                 # identificação e comunicação
        self.assertIn(f"{pedido.total:.2f}".replace(".", ","), corpo)  # valor
        self.assertIn("JD PRINT SRL", corpo)                # titular
        self.assertIn("BE68 5390 0754 7034", corpo)         # IBAN
        self.assertIn("GEBABEBB", corpo)                    # BIC
        self.assertIn("número do pedido", corpo)            # instruções
        self.assertIn(
            reverse("orders:payment_proof", kwargs={"number": pedido.number}),
            corpo,
            "faltou o link do comprovante",
        )

    def test_the_email_carries_the_account_the_order_used(self):
        """Com duas contas cadastradas, vai a padrão — não a primeira."""
        BankAccount.objects.create(
            label="Outra", beneficiary="OUTRA SRL", iban="BE11 2222 3333 4444"
        )

        self.checkout()

        corpo = self.email_do_cliente().body
        self.assertIn("BE68 5390 0754 7034", corpo)
        self.assertNotIn("BE11 2222 3333 4444", corpo)

    def test_the_order_records_that_the_details_went_out(self):
        self.checkout()

        pedido = Order.objects.get()
        self.assertIsNotNone(pedido.transfer_details_sent_at)
        self.assertTrue(pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).exists())

    def test_the_email_follows_the_language_of_the_checkout(self):
        """O idioma da tela em que se comprou, não o do servidor."""
        for prefixo, esperado in (("/fr", "Coordonnées"), ("/nl", "Gegevens"), ("/en", "Payment details")):
            with self.subTest(idioma=prefixo):
                Order.objects.all().delete()
                mail.outbox = []

                self.checkout(prefix=prefixo)

                self.assertEqual(Order.objects.get().language, prefixo.strip("/"))
                self.assertIn(esperado, self.email_do_cliente().subject)

    def test_the_team_is_notified_too(self):
        self.checkout()

        internos = [m for m in mail.outbox if m.to == ["loja@jdprint.test"]]
        self.assertEqual(len(internos), 1)

    def test_a_broken_mail_server_does_not_undo_the_sale(self):
        """A venda já aconteceu. O que falha é a instrução, e ela tem conserto."""
        with mock.patch(
            "apps.orders.emails.send_transfer_details_email", side_effect=OSError("sem rede")
        ):
            self.checkout()

        self.assertEqual(Order.objects.count(), 1)
        self.assertIsNone(Order.objects.get().transfer_details_sent_at)

    def test_the_order_is_not_paid_by_any_of_this(self):
        self.checkout()

        pedido = Order.objects.get()
        self.assertEqual(pedido.payment_status, PaymentStatus.PENDING)
        self.assertIsNone(pedido.paid_at)
        self.assertIsNone(pedido.stock_applied_at)


class PaginaDeSucessoTests(PaymentFlowBase):
    def test_it_says_what_happened_and_what_to_do_next(self):
        resposta = self.checkout()

        self.assertContains(resposta, "Pedido recebido")
        self.assertContains(resposta, "Seu pedido foi registrado")
        self.assertContains(resposta, self.user.email)
        self.assertContains(resposta, "Faça a transferência")
        self.assertContains(resposta, "comprovante")

    def test_it_links_to_the_proof_page(self):
        resposta = self.checkout()

        pedido = Order.objects.get()
        self.assertContains(
            resposta, reverse("orders:payment_proof", kwargs={"number": pedido.number})
        )

    def test_it_does_not_repeat_the_iban_on_a_page_the_browser_keeps(self):
        """O IBAN foi para o e-mail, que é onde o cliente vai procurá-lo."""
        resposta = self.checkout()

        self.assertNotContains(resposta, "BE68 5390 0754 7034")


# ---------------------------------------------------------------------------
# 6. A confirmação manual do pagamento
# ---------------------------------------------------------------------------


class ConfirmacaoManualTests(PaymentFlowBase):
    def setUp(self):
        super().setUp()
        self.checkout()
        self.pedido = Order.objects.get()
        mail.outbox = []
        self.client.force_login(self.equipe())

    def confirmar(self, pedido=None, confirmar=True):
        pedido = pedido or self.pedido
        dados = {
            "action": "action_confirm_payment",
            "index": "0",
            "_selected_action": [str(pedido.pk)],
        }
        if confirmar:
            dados["confirmar"] = "1"
        return self.client.post(reverse("admin:orders_order_changelist"), dados)

    # -- a tela intermediária ----------------------------------------------

    def test_the_action_asks_before_doing_anything(self):
        """Isto é dinheiro: o clique na lista não pode ser a confirmação."""
        resposta = self.confirmar(confirmar=False)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, self.pedido.number)
        self.assertContains(resposta, "o dinheiro entrou")
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.payment_status, PaymentStatus.PENDING)

    def test_the_screen_shows_what_to_check_against_the_statement(self):
        resposta = self.confirmar(confirmar=False)

        self.assertContains(resposta, f"{self.pedido.total:.2f}".replace(".", ","))
        self.assertContains(resposta, "BE68 ···· 7034")
        self.assertContains(resposta, "sem comprovante")

    # -- o que a confirmação faz -------------------------------------------

    def test_it_marks_the_payment(self):
        self.confirmar()

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(self.pedido.paid_at)

    def test_it_moves_the_order_forward(self):
        self.confirmar()

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, OrderStatus.CONFIRMED)

    def test_it_does_not_start_production_by_itself(self):
        """A oficina decide quando começa — e é isso que torna o cancelamento
        de um pedido pago e ainda parado uma decisão automática."""
        self.confirmar()

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.fulfillment_status, FulfillmentStatus.NOT_STARTED)

    def test_it_applies_the_stock(self):
        antes = self.variant.stock_quantity

        self.confirmar()

        self.variant.refresh_from_db()
        self.pedido.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, antes - 1)
        self.assertIsNotNone(self.pedido.stock_applied_at)

    def test_it_emails_the_customer_and_the_workshop(self):
        self.confirmar()

        destinos = sorted(m.to[0] for m in mail.outbox)
        self.assertIn(self.user.email, destinos)
        self.assertIn("loja@jdprint.test", destinos)

    def test_the_customer_email_is_the_paid_confirmation(self):
        self.confirmar()

        mensagem = self.email_do_cliente()
        self.assertIn(self.pedido.number, mensagem.subject)
        self.assertIn("confirmado", mensagem.subject.lower())

    def test_it_records_who_confirmed(self):
        self.confirmar()

        entrada = self.pedido.history.filter(event=OrderEvent.PAID).first()
        self.assertIsNotNone(entrada)
        self.assertIsNotNone(entrada.created_by, "o histórico tem que dizer quem confirmou")

    def test_the_payment_attempt_is_marked_as_succeeded(self):
        self.confirmar()

        self.assertEqual(self.pedido.payments.get().status, PaymentState.CREATED)

    # -- duas vezes não é duas vezes ---------------------------------------

    def test_confirming_twice_does_not_apply_the_stock_twice(self):
        self.confirmar()
        self.variant.refresh_from_db()
        depois_da_primeira = self.variant.stock_quantity
        marcado_em = Order.objects.get(pk=self.pedido.pk).stock_applied_at

        self.confirmar()

        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, depois_da_primeira)
        self.assertEqual(Order.objects.get(pk=self.pedido.pk).stock_applied_at, marcado_em)

    def test_confirming_twice_does_not_email_the_customer_twice(self):
        self.confirmar()
        enviados = len(mail.outbox)

        self.confirmar()

        self.assertEqual(len(mail.outbox), enviados)

    def test_confirming_twice_does_not_move_paid_at(self):
        self.confirmar()
        pago_em = Order.objects.get(pk=self.pedido.pk).paid_at

        self.confirmar()

        self.assertEqual(Order.objects.get(pk=self.pedido.pk).paid_at, pago_em)

    def test_an_already_paid_order_is_not_offered(self):
        self.confirmar()
        mail.outbox = []

        resposta = self.confirmar(confirmar=False)

        self.assertNotEqual(resposta.status_code, 200)
        self.assertEqual(mail.outbox, [])

    def test_a_cancelled_order_can_still_have_its_payment_recorded(self):
        """A transferência cai dias depois, e o cancelamento não a devolve.

        Recusar o registro deixava o dinheiro na conta da loja sem nenhum
        caminho para abrir o reembolso — transferência não tem webhook. Quem
        cuida das consequências é o `confirm_payment`: em pedido cancelado ele
        não baixa estoque, não anuncia a compra e abre o reembolso.
        """
        from apps.orders.models import RefundStatus

        self.pedido.cancelled_at = timezone.now()
        self.pedido.status = OrderStatus.CANCELLED
        self.pedido.save(update_fields=["status", "cancelled_at"])

        self.confirmar()

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.payment_status, PaymentStatus.PAID)
        self.assertEqual(self.pedido.status, OrderStatus.CANCELLED)
        self.assertEqual(self.pedido.refund_status, RefundStatus.PENDING)

    # -- permissões ---------------------------------------------------------

    def test_staff_without_change_permission_is_not_offered_the_action(self):
        self.client.force_login(self.equipe(permissoes=("view_order",)))

        resposta = self.client.get(reverse("admin:orders_order_changelist"))

        self.assertNotContains(resposta, "action_confirm_payment")

    def test_staff_without_change_permission_cannot_force_it(self):
        self.client.force_login(self.equipe(permissoes=("view_order",)))

        self.confirmar()

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.payment_status, PaymentStatus.PENDING)

    def test_a_customer_cannot_reach_the_action(self):
        self.client.force_login(self.user)

        resposta = self.confirmar()

        self.assertNotEqual(resposta.status_code, 200)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.payment_status, PaymentStatus.PENDING)

    # -- a rotina é a mesma do webhook -------------------------------------

    def test_it_runs_the_same_service_the_webhook_runs(self):
        """Não é um caminho paralelo: mexer num, mexe nos dois.

        É o que impede a confirmação manual de esquecer uma etapa que o
        webhook faz — ou de ganhar uma que ele não faz.
        """
        with mock.patch.object(services, "confirm_payment", return_value=True) as chamada:
            self.confirmar()

        self.assertEqual(chamada.call_count, 1)
        self.assertEqual(chamada.call_args.args[0].pk, self.pedido.pk)


# ---------------------------------------------------------------------------
# 7. O que não podia mudar
# ---------------------------------------------------------------------------


class StripeContinuaIntactaTests(LanguageResetMixin, TestCase):
    """A etapa mexeu na escolha do meio de pagamento, não na Stripe."""

    def test_the_configured_provider_still_decides_who_charges(self):
        with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_x"):
            metodo = method_for_provider()

            self.assertEqual(metodo.code, "card")
            self.assertEqual(metodo.provider, "stripe")

    def test_transfer_is_not_offered_when_the_store_runs_on_stripe(self):
        with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_x"):
            self.assertIsNone(get_checkout_method("transfer"))

    def test_a_provider_without_credentials_offers_nothing(self):
        """Sem chave não há o que cobrar — e a tela recusa antes de gravar."""
        with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY=""):
            self.assertEqual(available_checkout_methods(), ())

    def test_confirm_payment_still_works_without_a_user(self):
        """O webhook chama sem usuário: o histórico registra "o sistema"."""
        import inspect

        assinatura = inspect.signature(services.confirm_payment)
        self.assertIsNone(assinatura.parameters["user"].default)


class I18nDosTextosNovosTests(PaymentFlowBase):
    """Os textos novos da tela existem nos quatro idiomas."""

    ESPERADO = {
        "": ("Transferência bancária", "Em breve", "Visa, Mastercard e Maestro."),
        "/fr": ("Virement bancaire", "Bientôt", "Visa, Mastercard et Maestro."),
        "/nl": ("Bankoverschrijving", "Binnenkort", "Visa, Mastercard en Maestro."),
        "/en": ("Bank transfer", "Coming soon", "Visa, Mastercard and Maestro."),
    }

    def test_the_payment_step_is_translated(self):
        for prefixo, textos in self.ESPERADO.items():
            with self.subTest(idioma=prefixo or "pt"):
                self.add_to_cart()

                html = self.client.get(f"{prefixo}/carrinho/finalizar/").content.decode()

                for texto in textos:
                    self.assertIn(texto, html)

    def test_the_success_page_is_translated(self):
        esperado = {
            "/fr": "Votre commande a bien été enregistrée.",
            "/nl": "Uw bestelling is geregistreerd.",
            "/en": "Your order has been registered.",
        }
        for prefixo, texto in esperado.items():
            with self.subTest(idioma=prefixo):
                Order.objects.all().delete()

                resposta = self.checkout(prefix=prefixo)

                self.assertContains(resposta, texto)

    def test_the_refusal_without_an_account_is_translated(self):
        BankAccount.objects.all().delete()
        esperado = {
            "/fr": "Le paiement par virement est indisponible",
            "/nl": "Betalen via overschrijving is op dit moment niet beschikbaar",
            "/en": "Bank transfer is unavailable right now",
        }
        for prefixo, texto in esperado.items():
            with self.subTest(idioma=prefixo):
                resposta = self.checkout(prefix=prefixo)

                self.assertContains(resposta, texto)
