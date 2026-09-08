"""Auditoria 4B — o ciclo de vida do pedido depois do checkout.

O que estes testes protegem, e que as suítes existentes ainda não diziam:

* **expedição de um pedido cancelado.** "Interrompido" é terminal: nem o
  serviço nem a ação do Admin marcam como enviado, e nenhum aviso de envio
  sai para uma compra que não existe mais;
* **contabilidade do reembolso.** O acumulado nunca passa do total do pedido,
  valor não positivo e referência vazia não entram, a mesma referência não
  conta duas vezes — mas a mesma referência em **outro** pedido entra, porque
  é outra transferência;
* **estoque.** Devolve-se exatamente o que a confirmação tirou: item sob
  encomenda não devolve nada, item que saiu pela metade devolve a metade, e
  uma segunda devolução não existe;
* **texto do cliente.** Observação, motivo do cancelamento, mensagem de
  presente e código de rastreio são texto — vão escapados para o Admin, para
  a conta e para os e-mails;
* **idiomas.** Envio, cancelamento e reembolso chegam ao cliente em NL e EN;
  o aviso da equipe continua em português;
* **a conta do cliente** acompanha a produção e para de oferecer pagamento
  quando o pedido é cancelado.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from apps.orders import services
from apps.orders.models import (
    FulfillmentStatus,
    Order,
    OrderNote,
    OrderStatus,
    PaymentStatus,
    RefundStatus,
)
from apps.orders.tests.test_checkout_audit_4a import AuditBase


@override_settings(PAYMENT_PROVIDER="transfer", ORDER_ADMIN_EMAILS=["producao@jd-print.test"])
class LifecycleBase(AuditBase):
    def pedido_pago(self, sku="CICLO", estoque=5, quantidade=1, **kw):
        p = self.produto(sku, estoque=estoque, **kw)
        self.add(p, quantidade=quantidade)
        self.checkout()
        order = self.pedido()
        services.confirm_payment(order)
        order.refresh_from_db()
        return p, order

    def staff(self):
        chefe = get_user_model().objects.create_superuser("chefe", "chefe@jdprint.test", "senha-de-teste-77")
        c = Client()
        c.force_login(chefe)
        return c

    def acao(self, cliente, order, nome, **extra):
        return cliente.post(
            reverse("admin:orders_order_changelist"),
            {"action": nome, "index": "0", "_selected_action": [str(order.pk)], **extra},
            follow=True,
        )


# ---------------------------------------------------------------------------
# Expedição de um pedido cancelado
# ---------------------------------------------------------------------------


class ShippingACancelledOrderTests(LifecycleBase):
    def setUp(self):
        super().setUp()
        _p, self.order = self.pedido_pago("ENV")
        services.approve_cancellation(self.order, "ok", auto=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)

    def test_the_service_refuses_and_sends_nothing(self):
        mail.outbox.clear()
        self.assertFalse(services.mark_shipped(self.order, "BE123"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)
        self.assertIsNone(self.order.shipped_at)
        self.assertEqual(mail.outbox, [])

    def test_the_admin_action_does_not_ship_it_either(self):
        cliente = self.staff()
        mail.outbox.clear()
        resposta = self.acao(cliente, self.order, "action_mark_shipped")
        self.assertEqual(resposta.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)
        self.assertIsNone(self.order.shipped_at)
        self.assertEqual(mail.outbox, [])

    def test_the_production_status_cannot_be_moved_out_of_halted(self):
        with self.assertRaises(services.StatusChangeRefused):
            services.change_fulfillment_status(self.order, FulfillmentStatus.IN_PRODUCTION)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FulfillmentStatus.HALTED)


# ---------------------------------------------------------------------------
# Contabilidade do reembolso
# ---------------------------------------------------------------------------


class RefundAccountingTests(LifecycleBase):
    def setUp(self):
        super().setUp()
        _p, self.order = self.pedido_pago("REEM")
        services.approve_cancellation(self.order, "ok", auto=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)
        self.total = self.order.total

    def test_a_refund_larger_than_the_order_is_capped_at_the_total(self):
        self.assertTrue(services.register_refund(self.order, Decimal("999.00"), reference="TRF-1"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, self.total)
        self.assertEqual(self.order.refund_status, RefundStatus.DONE)
        self.assertEqual(self.order.refund_due, Decimal("0.00"))
        registro = self.order.history.filter(event="refunded").first()
        self.assertIn(f"{self.total:.2f}", registro.message)  # creditou o que faltava, não 999
        self.assertNotIn("999", registro.message)

    def test_nothing_is_accepted_after_the_refund_is_finished(self):
        services.register_refund(self.order, self.total, reference="TRF-1")
        self.assertFalse(services.register_refund(self.order, Decimal("5.00"), reference="TRF-2"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, self.total)

    def test_zero_negative_and_a_missing_reference_are_refused(self):
        for valor, referencia in ((Decimal("0.00"), "TRF-X"), (Decimal("-5.00"), "TRF-X"), (Decimal("5.00"), ""), (Decimal("5.00"), "   ")):
            with self.subTest(valor=valor, referencia=referencia):
                self.assertFalse(services.register_refund(self.order, valor, reference=referencia))
        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("0.00"))
        self.assertEqual(self.order.refund_status, RefundStatus.PENDING)

    def test_the_same_reference_counts_once_but_counts_again_on_another_order(self):
        parcial = Decimal("5.00")
        self.assertTrue(services.register_refund(self.order, parcial, reference="TRF-MESMA"))
        self.assertFalse(services.register_refund(self.order, parcial, reference="TRF-MESMA"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, parcial)
        self.assertEqual(self.order.refund_status, RefundStatus.PARTIAL)

        _p, outro = self.pedido_pago("REEM2")
        services.approve_cancellation(outro, "ok", auto=True)
        outro.refresh_from_db()
        self.assertTrue(services.register_refund(outro, parcial, reference="TRF-MESMA"))
        outro.refresh_from_db()
        self.assertEqual(outro.refunded_amount, parcial)

    def test_partial_refunds_add_up_to_the_total_and_close_it(self):
        metade = (self.total / 2).quantize(Decimal("0.01"))
        services.register_refund(self.order, metade, reference="TRF-A")
        self.order.refresh_from_db()
        self.assertEqual(self.order.refund_status, RefundStatus.PARTIAL)
        self.assertEqual(self.order.refund_due, self.total - metade)
        services.register_refund(self.order, self.total, reference="TRF-B")
        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, self.total)
        self.assertEqual(self.order.refund_status, RefundStatus.DONE)
        self.assertIsNotNone(self.order.refunded_at)


# ---------------------------------------------------------------------------
# Estoque: devolve-se o que saiu
# ---------------------------------------------------------------------------


class StockReturnTests(LifecycleBase):
    def test_a_made_to_order_item_takes_nothing_and_gives_nothing_back(self):
        p = self.produto("SOB", estoque=0)
        v = p.default_variant
        v.made_to_order = True
        v.production_lead_time_days = 3
        v.save()
        self.add(p, quantidade=2)
        self.checkout()
        order = self.pedido()
        services.confirm_payment(order)
        order.refresh_from_db()
        v.refresh_from_db()
        self.assertEqual(v.stock_quantity, 0)
        self.assertEqual([i.stock_taken for i in order.items.all()], [0])

        services.approve_cancellation(order, "ok", restore_stock=True)
        v.refresh_from_db()
        self.assertEqual(v.stock_quantity, 0)  # nada de estoque fantasma

    def test_a_shortage_gives_back_only_what_really_left(self):
        p = self.produto("FALTA", estoque=5)
        self.add(p, quantidade=5)
        self.checkout()
        order = self.pedido()
        v = p.default_variant
        v.stock_quantity = 2  # alguém comprou três no meio do caminho
        v.save()

        faltas = services.apply_stock(order)
        order.refresh_from_db()
        v.refresh_from_db()
        self.assertEqual(v.stock_quantity, 0)
        self.assertEqual(order.items.get().stock_taken, 2)
        self.assertEqual(len(faltas), 1)
        self.assertTrue(OrderNote.objects.filter(order=order).exists())

        devolvidas = services.return_stock(order, motivo="teste")
        v.refresh_from_db()
        self.assertEqual(devolvidas, 2)
        self.assertEqual(v.stock_quantity, 2)  # nunca as cinco pedidas

    def test_the_stock_is_never_returned_twice(self):
        p, order = self.pedido_pago("DEV", estoque=5, quantidade=2)
        v = p.default_variant
        v.refresh_from_db()
        self.assertEqual(v.stock_quantity, 3)
        self.assertEqual(services.return_stock(order, motivo="primeira"), 2)
        self.assertEqual(services.return_stock(order, motivo="segunda"), 0)
        v.refresh_from_db()
        self.assertEqual(v.stock_quantity, 5)

    def test_an_unpaid_order_has_nothing_to_return(self):
        p = self.produto("NAOPAGO")
        self.add(p)
        self.checkout()
        order = self.pedido()
        self.assertEqual(services.return_stock(order), 0)
        self.assertIsNone(order.stock_returned_at)


# ---------------------------------------------------------------------------
# O texto do cliente é texto
# ---------------------------------------------------------------------------


class CustomerTextIsEscapedTests(LifecycleBase):
    SCRIPT = "<script>alert(1)</script>"
    IMG = "<img src=x onerror=alert(2)>"
    SVG = "<svg onload=alert(3)>"

    def test_note_reason_tracking_and_gift_are_escaped_everywhere(self):
        p = self.produto("XSS")
        self.add(p)
        self.checkout(customer_note=f"{self.SCRIPT} & obrigado", is_gift="1")
        order = self.pedido()
        # `gift_message` é campo do Admin, não do checkout: escrito aqui como a
        # equipe escreveria, para conferir a escapagem na tela do pedido.
        Order.objects.filter(pk=order.pk).update(gift_message=self.IMG)
        order.refresh_from_db()
        services.confirm_payment(order)
        order.refresh_from_db()
        services.mark_shipped(order, self.SVG)
        order.refresh_from_db()
        services.request_cancellation(order, f"{self.IMG} motivo")
        order.refresh_from_db()

        admin_html = self.staff().get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
        conta_html = self.client.get(reverse("orders:detail", args=[order.number])).content.decode()
        corpos = [m.body + "".join(a[0] for a in (m.alternatives or [])) for m in mail.outbox]

        for nome, html in (("admin", admin_html), ("conta", conta_html)):
            with self.subTest(tela=nome):
                self.assertNotIn(self.SCRIPT, html)
                self.assertNotIn(self.IMG, html)
                self.assertNotIn(self.SVG, html)
        self.assertIn("&lt;script&gt;", admin_html)  # aparece, mas como texto
        for corpo in corpos:
            self.assertNotIn(self.SCRIPT, corpo)
            self.assertNotIn(self.IMG, corpo)

    def test_the_note_is_stored_whole_and_not_stripped(self):
        p = self.produto("NOTA")
        self.add(p)
        self.checkout(customer_note=f"{self.SCRIPT} & obrigado")
        self.assertEqual(self.pedido().customer_note, f"{self.SCRIPT} & obrigado")


# ---------------------------------------------------------------------------
# Idiomas do ciclo de vida
# ---------------------------------------------------------------------------


class LifecycleLanguageTests(LifecycleBase):
    ESPERADO = {
        "nl": ("verzonden", "annulering", "Terugbetaling"),
        "en": ("shipped", "ancellation", "Refund"),
    }

    def pedido_no_idioma(self, idioma, sku):
        p = self.produto(sku)
        self.add(p)
        self.checkout()
        order = self.pedido()
        Order.objects.filter(pk=order.pk).update(language=idioma)
        order.refresh_from_db()
        services.confirm_payment(order)
        order.refresh_from_db()
        return order

    def test_shipping_cancellation_and_refund_reach_the_customer_in_nl_and_en(self):
        for idioma, (envio, cancelamento, reembolso) in self.ESPERADO.items():
            with self.subTest(idioma=idioma):
                order = self.pedido_no_idioma(idioma, f"IDI{idioma}")
                mail.outbox.clear()
                services.mark_shipped(order, "BE999")
                order.refresh_from_db()
                assunto_envio = mail.outbox[-1].subject
                self.assertIn(envio, assunto_envio)

                mail.outbox.clear()
                services.request_cancellation(order, "motivo")
                order.refresh_from_db()
                services.approve_cancellation(order, "ok", restore_stock=True)
                order.refresh_from_db()
                ao_cliente = [m for m in mail.outbox if self.user.email in m.to]
                self.assertTrue(any(cancelamento.lower() in m.subject.lower() for m in ao_cliente))

                mail.outbox.clear()
                services.register_refund(order, order.total, reference=f"TRF-{idioma}")
                self.assertIn(reembolso.lower(), mail.outbox[-1].subject.lower())

    def test_the_team_notice_stays_in_portuguese_when_a_person_has_to_decide(self):
        """Pedido em produção: o plano é manual, e a equipe é avisada — em português."""
        order = self.pedido_no_idioma("nl", "EQU")
        services.change_fulfillment_status(order, FulfillmentStatus.IN_PRODUCTION)
        order.refresh_from_db()
        mail.outbox.clear()

        services.request_cancellation(order, "motivo")
        order.refresh_from_db()

        # A equipe é quem não é o cliente: o destinatário sai da configuração da
        # loja (`core.mailer.admin_recipients`), nunca do idioma do pedido.
        equipe = [m for m in mail.outbox if self.user.email not in m.to]
        cliente = [m for m in mail.outbox if self.user.email in m.to]
        self.assertTrue(equipe, [m.subject for m in mail.outbox])
        self.assertIn("cancelamento solicitado", equipe[0].subject)
        self.assertNotIn("annulering", equipe[0].subject.lower())
        self.assertTrue(any("annulering" in m.subject.lower() for m in cliente))
        self.assertEqual(order.cancellation_status, "requested")  # espera uma pessoa

    def test_an_automatic_cancellation_writes_only_to_the_customer(self):
        """Documenta o desenho: resolvido na hora, ninguém tem o que decidir.

        A produção não começou, então o cancelamento acontece no clique: o
        cliente recebe a aprovação e, se pagou, o aviso do reembolso. A equipe
        não recebe e-mail — o reembolso pendente aparece na ficha e na lista do
        Admin, que é por onde ele é registrado.
        """
        order = self.pedido_no_idioma("nl", "AUTO")
        mail.outbox.clear()

        services.request_cancellation(order, "motivo")
        order.refresh_from_db()

        self.assertEqual(order.cancellation_status, "approved")
        self.assertEqual(order.refund_status, RefundStatus.PENDING)
        self.assertTrue(mail.outbox)
        self.assertTrue(all(self.user.email in m.to for m in mail.outbox))
        # E o que a equipe usa para não perder o reembolso: a ficha do pedido.
        html = self.staff().get(reverse("admin:orders_order_change", args=[order.pk])).content.decode()
        self.assertIn(f"{order.refund_due:.2f}", html)


# ---------------------------------------------------------------------------
# A conta do cliente ao longo do ciclo
# ---------------------------------------------------------------------------


class CustomerAccountThroughTheLifecycleTests(LifecycleBase):
    def pagina(self, order):
        return self.client.get(reverse("orders:detail", args=[order.number])).content.decode()

    def test_the_page_follows_the_production_and_ends_completed(self):
        _p, order = self.pedido_pago("CONTA")
        for passo in (FulfillmentStatus.IN_PRODUCTION, FulfillmentStatus.READY, FulfillmentStatus.SHIPPED, FulfillmentStatus.DELIVERED):
            with self.subTest(passo=passo):
                services.change_fulfillment_status(order, passo)
                order.refresh_from_db()
                html = self.pagina(order)
                self.assertIn(order.number, html)
                self.assertIn(str(FulfillmentStatus(passo).label), html)
        self.assertEqual(order.status, OrderStatus.COMPLETED)
        self.assertIsNotNone(order.delivered_at)

    def test_a_cancelled_order_says_so_and_stops_offering_payment(self):
        p = self.produto("CANC")
        self.add(p)
        self.checkout()
        order = self.pedido()
        html = self.pagina(order)
        self.assertIn(reverse("orders:retry_payment", args=[order.number]), html)

        services.request_cancellation(order, "Mudei de ideia")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)
        self.assertEqual(order.payment_status, PaymentStatus.NOT_CHARGED)
        html = self.pagina(order)
        self.assertNotIn(reverse("orders:retry_payment", args=[order.number]), html)
        self.assertIn(str(OrderStatus.CANCELLED.label), html)

    def test_the_cancel_page_is_not_offered_twice(self):
        p = self.produto("DUPLO")
        self.add(p)
        self.checkout()
        order = self.pedido()
        url = reverse("orders:cancel", args=[order.number])
        self.client.post(url, {"reason": "primeira"})
        order.refresh_from_db()
        self.assertTrue(order.is_cancelled)
        # segundo clique: nada muda, nenhum evento novo
        eventos = order.history.count()
        self.client.post(url, {"reason": "segunda"})
        order.refresh_from_db()
        self.assertEqual(order.history.count(), eventos)
        self.assertEqual(order.cancellation_reason, "primeira")
