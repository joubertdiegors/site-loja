"""Cancelamento: quem é avisado, quando, e com que palavras.

O buraco era simples e caro: o cliente pedia para cancelar, o pedido registrava
a solicitação — e ninguém ficava sabendo. O histórico não é um canal; ninguém
abre o Admin para descobrir que precisa abrir o Admin. Do outro lado, aprovar o
cancelamento não dizia nada ao cliente.

Este arquivo cobre os dois lados e a política que os une: o prazo do reembolso
vive no Admin, e **sem prazo cadastrado a loja não promete prazo nenhum**.
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
from apps.orders.models import (
    CancellationSettings,
    CancellationSettingsTranslation,
    CancellationStatus,
    FulfillmentStatus,
    Order,
    OrderEvent,
    OrderStatus,
    RefundStatus,
)

EQUIPE = ["equipe@jdprint.test"]


@override_settings(ORDER_ADMIN_EMAILS=EQUIPE)
class CancelamentoBase(LanguageResetMixin, TestCase):
    _staff = 0

    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method()
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.product = make_product(
            sku="CAN-01", name="Vaso Espiral", price=Decimal("19.90"), stock_quantity=10
        )

        self.user = make_user(username="ana", email="ana@exemplo.test")
        self.customer = self.user.customer
        self.customer.first_name = "Ana"
        self.customer.last_name = "Ribeiro"
        self.customer.save()
        self.address = make_address(self.customer, self.country)

        # Pago e já em produção: é o cenário em que a decisão é de uma pessoa.
        # O caminho automático (não pago, ou pago sem produção) tem arquivo
        # próprio — `test_cancellation_rules`.
        self.order = self.em_producao(self.fazer_pedido())
        mail.outbox = []

    def em_producao(self, order):
        """Paga e manda para a impressora — o estado em que cancelar exige gente."""
        services.confirm_payment(order)
        order.refresh_from_db()
        services.change_fulfillment_status(order, FulfillmentStatus.IN_PRODUCTION)
        order.refresh_from_db()
        return order

    def fazer_pedido(self, **extra):
        return services.create_order(
            customer=self.customer,
            lines=[
                CartLine(
                    key=f"{self.product.pk}:0:-",
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

    def equipe(self, permissoes=("view_order", "change_order")):
        type(self)._staff += 1
        marca = type(self)._staff
        pessoa = get_user_model().objects.create_user(
            username=f"staff-{marca}",
            email=f"staff-{marca}@jdprint.test",
            password="senha-de-teste-77",
            is_staff=True,
        )
        pessoa.user_permissions.set(
            Permission.objects.filter(
                codename__in=permissoes, content_type__app_label="orders"
            )
        )
        return pessoa

    def para(self, destinatario):
        return [m for m in mail.outbox if m.to == [destinatario]]


# ---------------------------------------------------------------------------
# 1. O cliente pede — a equipe fica sabendo
# ---------------------------------------------------------------------------


class SolicitacaoAvisaAEquipeTests(CancelamentoBase):
    def test_requesting_emails_the_team(self):
        services.request_cancellation(self.order, "Comprei o tamanho errado.")

        self.assertEqual(len(self.para(EQUIPE[0])), 1)

    def test_the_subject_says_an_action_is_needed(self):
        services.request_cancellation(self.order, "Comprei o tamanho errado.")

        assunto = self.para(EQUIPE[0])[0].subject
        self.assertIn("AÇÃO NECESSÁRIA", assunto)
        self.assertIn("cancelamento", assunto.lower())
        self.assertIn(self.order.number, assunto)

    def test_the_email_carries_what_the_team_needs_to_decide(self):
        services.request_cancellation(self.order, "Comprei o tamanho errado.")

        corpo = self.para(EQUIPE[0])[0].body
        self.assertIn(self.order.number, corpo)
        self.assertIn("Ana Ribeiro", corpo)
        self.assertIn("ana@exemplo.test", corpo)
        self.assertIn(f"{self.order.total:.2f}".replace(".", ","), corpo)
        self.assertIn("Comprei o tamanho errado.", corpo)

    def test_the_email_links_straight_to_the_order_in_the_admin(self):
        services.request_cancellation(self.order, "Motivo qualquer")

        corpo = self.para(EQUIPE[0])[0].body
        self.assertIn(f"/admin/orders/order/{self.order.pk}/change/", corpo)

    def test_the_team_email_is_in_portuguese_even_for_a_french_order(self):
        """Quem lê é a equipe."""
        pedido = self.em_producao(self.fazer_pedido(language="fr"))
        mail.outbox = []

        services.request_cancellation(pedido, "Je me suis trompé.")

        self.assertIn("AÇÃO NECESSÁRIA", self.para(EQUIPE[0])[0].subject)

    def test_the_request_is_marked_as_pending(self):
        services.request_cancellation(self.order, "Motivo")

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)

    def test_asking_twice_sends_only_one_email(self):
        services.request_cancellation(self.order, "Primeira vez")

        self.assertFalse(services.request_cancellation(self.order, "Segunda vez"))
        self.assertEqual(len(self.para(EQUIPE[0])), 1)

    def test_a_broken_mail_server_does_not_undo_the_request(self):
        """O aviso tem conserto; a solicitação perdida, não."""
        from unittest import mock

        with mock.patch(
            "apps.orders.emails.send_cancellation_requested_email",
            side_effect=OSError("sem rede"),
        ):
            self.assertTrue(services.request_cancellation(self.order, "Motivo"))

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)

    def test_the_customer_page_still_records_it(self):
        """O histórico continua existindo — o e-mail não o substitui."""
        services.request_cancellation(self.order, "Motivo")

        self.assertTrue(
            self.order.history.filter(event=OrderEvent.CANCELLATION_REQUESTED).exists()
        )


# ---------------------------------------------------------------------------
# 1b. O cliente pede — e recebe a confirmação de que o pedido dele chegou
# ---------------------------------------------------------------------------


class SolicitacaoConfirmaAoClienteTests(CancelamentoBase):
    """Quem clica em "cancelar" e não recebe nada assume que não funcionou.

    O e-mail sai na mesma hora do aviso à equipe e diz uma coisa só: chegou.
    O que ele **não** diz é o que importa — nada de aprovação, reembolso ou
    prazo, porque nada disso foi decidido ainda.
    """

    def test_requesting_emails_the_customer(self):
        services.request_cancellation(self.order, "Comprei o tamanho errado.")

        self.assertEqual(len(self.para(self.user.email)), 1)

    def test_both_sides_are_warned_at_once(self):
        services.request_cancellation(self.order, "Motivo")

        self.assertEqual(len(self.para(EQUIPE[0])), 1)
        self.assertEqual(len(self.para(self.user.email)), 1)

    def test_the_subject_says_the_request_arrived(self):
        services.request_cancellation(self.order, "Motivo")

        assunto = self.para(self.user.email)[0].subject
        self.assertIn("Recebemos o seu pedido de cancelamento", assunto)
        self.assertIn(self.order.number, assunto)

    def test_the_email_names_the_order(self):
        services.request_cancellation(self.order, "Motivo")

        self.assertIn(self.order.number, self.para(self.user.email)[0].body)

    def test_the_email_links_to_the_order_page(self):
        services.request_cancellation(self.order, "Motivo")

        corpo = self.para(self.user.email)[0].body
        self.assertIn(
            reverse("orders:detail", kwargs={"number": self.order.number}), corpo
        )

    def test_the_email_says_someone_will_look_and_answer(self):
        services.request_cancellation(self.order, "Motivo")

        corpo = self.para(self.user.email)[0].body
        self.assertIn("Vamos analisá-la", corpo)
        self.assertIn("Ainda não há nada decidido", corpo)

    def test_the_email_promises_nothing(self):
        """O erro caro seria aqui: prometer agora e recusar depois."""
        services.request_cancellation(self.order, "Motivo")

        corpo = self.para(self.user.email)[0].body.lower()
        self.assertNotIn("aprovado", corpo)
        self.assertNotIn("reembolso", corpo)
        self.assertNotIn("dias úteis", corpo)

    def test_the_email_does_not_leak_the_internal_notice(self):
        """O que a equipe lê não é o que o cliente lê."""
        services.request_cancellation(self.order, "Motivo")

        corpo = self.para(self.user.email)[0].body
        self.assertNotIn("AÇÃO NECESSÁRIA", corpo)
        self.assertNotIn("/admin/", corpo)

    def test_asking_twice_sends_only_one_email_to_each_side(self):
        services.request_cancellation(self.order, "Primeira vez")

        self.assertFalse(services.request_cancellation(self.order, "Segunda vez"))
        self.assertEqual(len(self.para(self.user.email)), 1)
        self.assertEqual(len(self.para(EQUIPE[0])), 1)

    def test_a_failure_on_one_side_does_not_silence_the_other(self):
        """São dois avisos independentes; um caindo não leva o outro."""
        from unittest import mock

        with mock.patch(
            "apps.orders.emails.send_cancellation_requested_email",
            side_effect=OSError("sem rede"),
        ):
            self.assertTrue(services.request_cancellation(self.order, "Motivo"))

        self.assertEqual(len(self.para(self.user.email)), 1)

    def test_a_broken_mail_server_still_does_not_undo_the_request(self):
        from unittest import mock

        with mock.patch(
            "apps.orders.emails.send_cancellation_received_email",
            side_effect=OSError("sem rede"),
        ):
            self.assertTrue(services.request_cancellation(self.order, "Motivo"))

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)

    def test_refusing_later_does_not_resend_the_receipt_email(self):
        services.request_cancellation(self.order, "Motivo")
        mail.outbox = []

        services.refuse_cancellation(self.order, "A peça já saiu para entrega.")

        assuntos = " | ".join(m.subject for m in self.para(self.user.email))
        self.assertNotIn("Recebemos o seu pedido de cancelamento", assuntos)


class MensagemDoAcompanhamentoTests(CancelamentoBase):
    """A frase no painel do cliente, logo depois de pedir o cancelamento.

    Tem de dizer o mesmo que o e-mail: chegou, vamos olhar, avisamos. Sem
    prometer, e sem o vocabulário de quem opera a loja.
    """

    def setUp(self):
        super().setUp()
        services.request_cancellation(self.order, "Comprei o tamanho errado.")
        self.client.force_login(self.user)

    def pagina(self):
        return self.client.get(
            reverse("orders:detail", kwargs={"number": self.order.number})
        )

    def test_the_panel_says_the_request_arrived(self):
        resposta = self.pagina()

        self.assertContains(resposta, "Recebemos o seu pedido de cancelamento")
        self.assertContains(resposta, "vamos analisá-lo")

    def test_the_panel_promises_nothing(self):
        resposta = self.pagina()

        self.assertNotContains(resposta, "aprovado")
        self.assertNotContains(resposta, "reembolso")

    def test_the_panel_does_not_show_the_internal_note(self):
        """`message` é anotação da equipe — aqui só entra `customer_message`."""
        entrada = self.order.history.get(event=OrderEvent.CANCELLATION_REQUESTED)
        self.assertEqual(entrada.message, "Comprei o tamanho errado.")

        self.assertNotContains(self.pagina(), "Comprei o tamanho errado.")

    def test_the_panel_and_the_email_tell_the_same_story(self):
        entrada = self.order.history.get(event=OrderEvent.CANCELLATION_REQUESTED)

        self.assertIn("Recebemos o seu pedido de cancelamento", entrada.customer_message)
        self.assertIn(
            "Recebemos o seu pedido de cancelamento",
            self.para(self.user.email)[0].subject,
        )


# ---------------------------------------------------------------------------
# 2. A equipe aprova — o cliente fica sabendo
# ---------------------------------------------------------------------------


class AprovacaoAvisaOClienteTests(CancelamentoBase):
    def setUp(self):
        super().setUp()
        services.request_cancellation(self.order, "Motivo")
        mail.outbox = []

    def test_approving_emails_the_customer(self):
        services.approve_cancellation(self.order, restore_stock=False)

        assuntos = [m.subject for m in self.para(self.user.email)]
        self.assertEqual(len(assuntos), 2, assuntos)

    def test_approving_a_paid_order_also_opens_the_refund(self):
        """São dois assuntos porque são duas notícias: cancelou, e o dinheiro volta."""
        services.approve_cancellation(self.order, restore_stock=False)

        assuntos = " | ".join(m.subject for m in self.para(self.user.email))
        self.assertIn("Cancelamento do pedido", assuntos)
        self.assertIn("Reembolso do pedido", assuntos)

    def test_the_subject_names_the_order(self):
        services.approve_cancellation(self.order, restore_stock=False)

        self.assertIn(self.order.number, self.para(self.user.email)[0].subject)

    def test_the_email_says_the_refund_will_happen(self):
        services.approve_cancellation(self.order, restore_stock=False)

        corpo = self.para(self.user.email)[0].body
        self.assertIn("cancelamento foi aprovado", corpo)
        self.assertIn("reembolso será feito", corpo)
        self.assertIn("depende do meio de pagamento", corpo)

    def test_the_timeline_shows_the_same_message(self):
        services.approve_cancellation(self.order, restore_stock=False)

        entrada = self.order.history.get(event=OrderEvent.CANCELLATION_APPROVED)
        self.assertIn("cancelamento foi aprovado", entrada.customer_message)
        self.assertIn("reembolso", entrada.customer_message)

    def test_the_customer_sees_it_on_the_order_page(self):
        services.approve_cancellation(self.order, restore_stock=False)
        self.client.force_login(self.user)

        resposta = self.client.get(
            reverse("orders:detail", kwargs={"number": self.order.number})
        )

        self.assertContains(resposta, "cancelamento foi aprovado")
        self.assertContains(resposta, "reembolso")

    def test_approving_sends_the_approval_email_and_not_the_receipt_one(self):
        """O segundo e-mail é o da decisão — não uma segunda cópia do primeiro."""
        services.approve_cancellation(self.order, restore_stock=False)

        assunto = self.para(self.user.email)[0].subject
        self.assertIn("Cancelamento do pedido", assunto)
        self.assertNotIn("Recebemos o seu pedido de cancelamento", assunto)

    def test_approving_does_not_email_the_team_again(self):
        services.approve_cancellation(self.order, restore_stock=False)

        self.assertEqual(self.para(EQUIPE[0]), [])

    def test_approving_twice_sends_no_extra_email(self):
        services.approve_cancellation(self.order, restore_stock=False)
        quantos = len(self.para(self.user.email))

        self.assertFalse(services.approve_cancellation(self.order, restore_stock=False))
        self.assertEqual(len(self.para(self.user.email)), quantos)

    def test_refusing_emails_the_customer_with_the_reason(self):
        """A recusa também é uma notícia — e antes desta etapa ela não saía."""
        services.refuse_cancellation(self.order, "A peça já está na impressora.")

        corpo = self.para(self.user.email)[0].body
        self.assertIn("não foi possível cancelar", corpo)
        self.assertIn("A peça já está na impressora.", corpo)

    def test_refusing_does_not_send_the_approval_email(self):
        services.refuse_cancellation(self.order, "A peça já saiu para entrega.")

        assuntos = " | ".join(m.subject for m in self.para(self.user.email))
        self.assertNotIn("Cancelamento do pedido", assuntos)
        self.assertNotIn("Reembolso", assuntos)

    def test_the_order_is_cancelled(self):
        services.approve_cancellation(self.order, restore_stock=False)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)


# ---------------------------------------------------------------------------
# 3. O prazo do reembolso — configurável, e nunca inventado
# ---------------------------------------------------------------------------


class PoliticaDeReembolsoTests(CancelamentoBase):
    def test_without_a_configured_window_nothing_is_promised(self):
        """A regra que vale mais que todas: não inventar prazo."""
        mensagem = CancellationSettings.approved_message()

        self.assertIn("O reembolso será feito.", mensagem)
        self.assertNotIn("dias", mensagem)

    def test_a_single_number_becomes_up_to(self):
        CancellationSettings.objects.create(refund_days_min=10)

        self.assertIn("em até 10 dias úteis", CancellationSettings.approved_message())

    def test_two_numbers_become_a_range(self):
        CancellationSettings.objects.create(refund_days_min=5, refund_days_max=10)

        self.assertIn("em 5 a 10 dias úteis", CancellationSettings.approved_message())

    def test_the_same_number_twice_does_not_say_five_to_five(self):
        CancellationSettings.objects.create(refund_days_min=5, refund_days_max=5)

        self.assertIn("em até 5 dias úteis", CancellationSettings.approved_message())

    def test_the_message_always_warns_that_the_bank_decides(self):
        CancellationSettings.objects.create(refund_days_min=5, refund_days_max=10)

        self.assertIn("depende do meio de pagamento", CancellationSettings.approved_message())

    def test_the_extra_note_is_appended(self):
        config = CancellationSettings.objects.create(refund_days_min=5)
        CancellationSettingsTranslation.objects.create(
            master=config, language="pt", extra_note="O frete não é reembolsado."
        )
        config.refresh_translations()

        self.assertIn("O frete não é reembolsado.", CancellationSettings.approved_message())

    def test_the_window_reaches_the_customer_email(self):
        CancellationSettings.objects.create(refund_days_min=5, refund_days_max=10)
        services.request_cancellation(self.order, "Motivo")
        mail.outbox = []

        services.approve_cancellation(self.order, restore_stock=False)

        self.assertIn("em 5 a 10 dias úteis", self.para(self.user.email)[0].body)

    def test_a_max_below_the_min_is_refused(self):
        from django.core.exceptions import ValidationError

        config = CancellationSettings(refund_days_min=10, refund_days_max=5)

        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_only_one_row_exists(self):
        CancellationSettings.objects.create(refund_days_min=5)
        CancellationSettings.load().save()

        self.assertEqual(CancellationSettings.objects.count(), 1)


class IdiomaDoCancelamentoTests(CancelamentoBase):
    IDIOMAS = {
        "fr": ("annulation a été acceptée", "remboursement"),
        "nl": ("annuleringsverzoek is goedgekeurd", "terugbetaling"),
        "en": ("cancellation request has been approved", "refund"),
    }

    #: A confirmação de recebimento, nos mesmos idiomas.
    RECEBIDO = {
        "fr": "Nous avons bien reçu votre demande",
        "nl": "We hebben uw annuleringsverzoek ontvangen",
        "en": "We have received your cancellation request",
    }

    def test_the_confirmation_follows_the_language_of_the_order(self):
        for idioma, trecho in self.RECEBIDO.items():
            with self.subTest(idioma=idioma):
                pedido = self.em_producao(self.fazer_pedido(language=idioma))
                mail.outbox = []

                services.request_cancellation(pedido, "Motivo")

                self.assertIn(trecho, self.para(self.user.email)[0].subject)
                self.assertIn(pedido.number, self.para(self.user.email)[0].body)

    def test_the_panel_message_follows_the_language_of_the_page(self):
        services.request_cancellation(self.order, "Motivo")
        self.client.force_login(self.user)
        caminho = reverse("orders:detail", kwargs={"number": self.order.number})

        for prefixo, trecho in (
            ("/fr", "Nous avons bien reçu votre demande"),
            ("/nl", "We hebben uw annuleringsverzoek ontvangen"),
            ("/en", "We have received your cancellation request"),
        ):
            with self.subTest(idioma=prefixo):
                self.assertContains(self.client.get(f"{prefixo}{caminho}"), trecho)

    def test_the_customer_email_follows_the_language_of_the_order(self):
        for idioma, (trecho, _outro) in self.IDIOMAS.items():
            with self.subTest(idioma=idioma):
                pedido = self.em_producao(self.fazer_pedido(language=idioma))
                services.request_cancellation(pedido, "Motivo")
                mail.outbox = []

                services.approve_cancellation(pedido, restore_stock=False)

                self.assertIn(trecho, self.para(self.user.email)[0].body)

    def test_the_timeline_follows_the_language_of_the_page(self):
        """O pedido pode ser lido em outro idioma semanas depois."""
        services.request_cancellation(self.order, "Motivo")
        services.approve_cancellation(self.order, restore_stock=False)
        self.client.force_login(self.user)
        # Resolvida **antes** do laço: cada requisição deixa o seu idioma ativo
        # na thread, e um `reverse()` no meio do caminho já viria com o prefixo
        # do idioma anterior.
        caminho = reverse("orders:detail", kwargs={"number": self.order.number})

        for prefixo, (trecho, _outro) in (
            ("/fr", self.IDIOMAS["fr"]),
            ("/nl", self.IDIOMAS["nl"]),
            ("/en", self.IDIOMAS["en"]),
        ):
            with self.subTest(idioma=prefixo):
                resposta = self.client.get(f"{prefixo}{caminho}")

                self.assertContains(resposta, trecho)


# ---------------------------------------------------------------------------
# 4. O Admin
# ---------------------------------------------------------------------------


class CancelamentoNoAdminTests(CancelamentoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.equipe())

    def pagina(self):
        return self.client.get(reverse("admin:orders_order_change", args=[self.order.pk]))

    def test_a_pending_request_is_loud_on_the_order(self):
        services.request_cancellation(self.order, "Comprei o tamanho errado.")

        resposta = self.pagina()

        self.assertContains(resposta, "aguardando decisão da equipe")
        self.assertContains(resposta, "Comprei o tamanho errado.")

    def test_an_order_without_a_request_shows_no_banner(self):
        resposta = self.pagina()

        self.assertNotContains(resposta, "aguardando decisão da equipe")

    def test_an_order_without_a_request_does_not_leave_an_empty_row(self):
        """Sem solicitação, o campo nem entra no formulário.

        Um `readonly` devolvendo `""` continuaria desenhando a linha e o
        rótulo — um ":" solto no alto do resumo do pedido.
        """
        resposta = self.pagina()

        # A linha do formulário, e não a classe solta: ela também aparece na
        # folha de estilo da tela, que é onde o rótulo vazio é escondido.
        self.assertNotContains(resposta, "form-row field-cancelamento_aviso")

    def test_the_banner_row_comes_back_when_there_is_a_request(self):
        services.request_cancellation(self.order, "Motivo")

        self.assertContains(self.pagina(), "form-row field-cancelamento_aviso")

    def test_the_changelist_flags_it(self):
        services.request_cancellation(self.order, "Motivo")

        resposta = self.client.get(reverse("admin:orders_order_changelist"))

        self.assertContains(resposta, "solicitado")

    def test_the_history_is_still_there(self):
        services.request_cancellation(self.order, "Motivo")

        resposta = self.pagina()

        self.assertContains(resposta, "CANCELAMENTO")

    def test_the_policy_has_its_own_screen(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("chefe", "chefe@x.test", "x")
        )

        resposta = self.client.get(reverse("admin:orders_cancellationsettings_changelist"))

        self.assertEqual(resposta.status_code, 200)

    def test_the_policy_screen_previews_what_the_customer_reads(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("chefe", "chefe@x.test", "x")
        )
        config = CancellationSettings.objects.create(refund_days_min=5, refund_days_max=10)

        resposta = self.client.get(
            reverse("admin:orders_cancellationsettings_change", args=[config.pk])
        )

        self.assertContains(resposta, "em 5 a 10 dias úteis")

    def test_staff_without_change_permission_cannot_decide(self):
        services.request_cancellation(self.order, "Motivo")
        self.client.force_login(self.equipe(permissoes=("view_order",)))
        mail.outbox = []

        self.acao_aprovar()

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertEqual(mail.outbox, [])

    def acao_aprovar(self, **extra):
        """A ação de aprovar, já com a confirmação da tela intermediária."""
        dados = {
            "action": "action_approve_cancellation",
            "index": "0",
            "_selected_action": [str(self.order.pk)],
            "confirmar": "1",
            "resposta": "",
            "restore_stock": "0",
        }
        dados.update(extra)
        return self.client.post(reverse("admin:orders_order_changelist"), dados)

    def test_the_action_asks_before_acting(self):
        """Sem a confirmação, nada acontece: a tela pergunta primeiro."""
        services.request_cancellation(self.order, "Motivo")
        mail.outbox = []

        resposta = self.client.post(
            reverse("admin:orders_order_changelist"),
            {
                "action": "action_approve_cancellation",
                "index": "0",
                "_selected_action": [str(self.order.pk)],
            },
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.REQUESTED)
        self.assertContains(resposta, "as peças voltam ao estoque")

    def test_approving_from_the_admin_emails_the_customer(self):
        services.request_cancellation(self.order, "Motivo")
        mail.outbox = []

        self.acao_aprovar()

        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_status, CancellationStatus.APPROVED)
        self.assertTrue(self.para(self.user.email))
