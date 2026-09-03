"""O caminho inteiro, com as repetições que a vida real produz.

Um teste de ponta a ponta encontrou o que a suíte não pegava: um duplo clique
em "Concluir pedido" criou dois pedidos e dois conjuntos de e-mails. A partir
dali, a pergunta que organiza este arquivo é sempre a mesma — **o que acontece
quando a mesma coisa é feita duas vezes?**

Cada etapa do fluxo é exercida uma vez e depois de novo:

    finalizar · finalizar        -> um pedido
    enviar dados · enviar dados  -> um e-mail
    comprovante · comprovante    -> um arquivo
    confirmar · confirmar        -> um estoque
    pagar agora                  -> mesmo pedido, dados de hoje

E uma pergunta que não é sobre repetição, mas que veio do mesmo teste: **o que
o cliente lê?** A linha do tempo dele mostrava anotações escritas para a
equipe, IBAN mascarado incluído.
"""

import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.catalog.models import ProductStatus, ProductVariant
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
from apps.orders.models import (
    BankAccount,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentProof,
    PaymentStatus,
)

TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="jdprint-test-journey-")

JPG = b"\xff\xd8\xff" + b"bytes-de-um-comprovante"
PDF = b"%PDF-1.7\n" + b"bytes-de-um-pdf"


@override_settings(
    MEDIA_ROOT=TEMP_MEDIA_ROOT,
    PAYMENT_PROVIDER="transfer",
    ORDER_ADMIN_EMAILS=["loja@jdprint.test"],
)
class JourneyBase(LanguageResetMixin, TestCase):
    """Um carrinho pronto, uma conta padrão e alguém da equipe."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    _equipes = 0

    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="modelos", name="Modelos")
        self.pais = make_country("BE", vat_rate="21.00")
        self.metodo = make_method(min_days=2, max_days=3)
        make_rate(self.metodo, self.pais, 0, 5000, "4.90")

        self.user = make_user(username="ana", email="ana@exemplo.test")
        self.customer = self.user.customer
        self.customer.first_name = "Ana"
        self.customer.last_name = "Ribeiro"
        self.customer.save()
        self.address = make_address(
            self.customer, self.pais,
            first_name="Ana", last_name="Ribeiro", street="Rue du Test 1",
        )

        self.produto = make_product(
            sku="JOR-01", name="Vaso Espiral", category=self.categoria,
            price=Decimal("19.90"), stock_quantity=10, weight_grams=Decimal("300"),
        )
        self.variante = self.produto.default_variant

        self.conta = BankAccount.objects.create(
            label="Principal", beneficiary="JD PRINT SRL",
            iban="BE68 5390 0754 7034", bic="GEBABEBB",
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
                "product_id": self.produto.pk,
                "variant_id": self.variante.pk,
                "quantity": str(quantity),
            },
        )

    def campos_do_checkout(self):
        """O que o navegador enviaria — a chave inclusa, como no HTML."""
        pagina = self.client.get("/carrinho/finalizar/")
        chave = pagina.context["form"]["checkout_token"].value()
        return {
            "shipping_address": self.address.pk,
            "billing_same_as_shipping": "on",
            "shipping_method": self.metodo.pk,
            "payment_method": "transfer",
            "checkout_token": chave,
        }

    def finalizar(self, dados=None, follow=True):
        return self.client.post("/carrinho/finalizar/", dados or self.campos_do_checkout(),
                                follow=follow)

    def comprar(self):
        """Um pedido, do carrinho à confirmação."""
        self.add_to_cart()
        self.finalizar()
        return Order.objects.get()

    def enviar_comprovante(self, conteudo=JPG, nome="comprovante.jpg", pedido=None):
        pedido = pedido or Order.objects.get()
        return self.client.post(
            reverse("orders:payment_proof", kwargs={"number": pedido.number}),
            {"file": SimpleUploadedFile(nome, conteudo, content_type="image/jpeg")},
        )

    def equipe(self, permissoes=("view_order", "change_order", "view_paymentproof")):
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

    def acao(self, nome, pedido, extra=None):
        dados = {"action": nome, "index": "0", "_selected_action": [str(pedido.pk)]}
        dados.update(extra or {})
        return self.client.post(reverse("admin:orders_order_changelist"), dados)


# ---------------------------------------------------------------------------
# 1. Duplo clique em "Concluir pedido"
# ---------------------------------------------------------------------------


class DuploCliqueTests(JourneyBase):
    """Foi o que o teste real encontrou: dois pedidos, dois e-mails."""

    def test_two_submissions_of_the_same_screen_create_one_order(self):
        self.add_to_cart()
        dados = self.campos_do_checkout()

        self.finalizar(dados)
        self.finalizar(dados)

        self.assertEqual(Order.objects.count(), 1)

    def test_the_second_submission_sends_no_second_email(self):
        self.add_to_cart()
        dados = self.campos_do_checkout()

        self.finalizar(dados)
        enviados = len(mail.outbox)
        self.finalizar(dados)

        self.assertEqual(len(mail.outbox), enviados)

    def test_the_second_submission_creates_no_second_payment_attempt(self):
        self.add_to_cart()
        dados = self.campos_do_checkout()

        self.finalizar(dados)
        self.finalizar(dados)

        self.assertEqual(Order.objects.get().payments.count(), 1)

    def test_the_second_submission_duplicates_no_event(self):
        self.add_to_cart()
        dados = self.campos_do_checkout()

        self.finalizar(dados)
        self.finalizar(dados)

        pedido = Order.objects.get()
        self.assertEqual(pedido.history.filter(event=OrderEvent.CREATED).count(), 1)
        self.assertEqual(
            pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).count(), 1
        )

    def test_the_second_submission_lands_on_the_order_that_exists(self):
        """Não é um erro na cara de quem clicou: é a confirmação do pedido dele."""
        self.add_to_cart()
        dados = self.campos_do_checkout()
        self.finalizar(dados)
        numero = Order.objects.get().number

        resposta = self.finalizar(dados)

        self.assertContains(resposta, numero)
        self.assertContains(resposta, "Pedido recebido")

    def test_the_database_refuses_two_orders_with_the_same_key(self):
        """O caso que nenhum `if` em Python resolve.

        Duas requisições simultâneas passam pela consulta antes de qualquer uma
        gravar: nenhuma enxerga a linha que a outra ainda não commitou. A
        garantia tem de ser do banco, e é ela que está sob teste aqui.
        """
        from django.db import IntegrityError, transaction

        self.add_to_cart()
        dados = self.campos_do_checkout()
        self.finalizar(dados)
        pedido = Order.objects.get()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Order.objects.create(
                    customer=self.customer,
                    total=Decimal("10.00"),
                    currency="EUR",
                    checkout_token=pedido.checkout_token,
                )

    def test_the_loser_of_a_race_lands_on_the_order_instead_of_a_500(self):
        """O que o teste no navegador encontrou.

        Duas requisições de verdade, ao mesmo tempo: uma cria o pedido, a outra
        esbarra no banco. O resultado já estava certo — um pedido só —, mas
        quem perdeu a corrida via uma página de erro depois de ter comprado.

        O erro é de forma diferente em cada banco: no PostgreSQL a segunda
        gravação viola a constraint (`IntegrityError`); no SQLite o arquivo
        inteiro fica travado e sai `OperationalError`. As duas dizem a mesma
        coisa, e as duas terminam na confirmação do pedido que existe.
        """
        from django.db import DatabaseError, OperationalError

        self.add_to_cart()
        dados = self.campos_do_checkout()
        self.finalizar(dados)
        numero = Order.objects.get().number

        for erro in (DatabaseError("unique"), OperationalError("database is locked")):
            with self.subTest(erro=type(erro).__name__):
                self.add_to_cart()
                with mock.patch(
                    "apps.orders.views.services.create_order", side_effect=erro
                ):
                    resposta = self.client.post("/carrinho/finalizar/", dados, follow=True)

                self.assertEqual(resposta.status_code, 200)
                self.assertContains(resposta, numero)
                self.assertEqual(Order.objects.count(), 1)

    def test_a_real_database_failure_is_not_swallowed(self):
        """A recuperação só vale se o pedido do outro clique existir mesmo."""
        from django.db import DatabaseError

        self.add_to_cart()
        dados = self.campos_do_checkout()

        with mock.patch(
            "apps.orders.views.services.create_order", side_effect=DatabaseError("disco cheio")
        ):
            with self.assertRaises(DatabaseError):
                self.client.post("/carrinho/finalizar/", dados)

    def test_an_empty_key_repeats_freely(self):
        """Pedido sem chave (comando, importação) não colide com outro."""
        Order.objects.create(customer=self.customer, total=Decimal("10.00"), currency="EUR")
        Order.objects.create(customer=self.customer, total=Decimal("10.00"), currency="EUR")

        self.assertEqual(Order.objects.filter(checkout_token="").count(), 2)

    def test_a_new_screen_is_a_new_order(self):
        """A proteção é contra o clique repetido, não contra comprar de novo."""
        self.add_to_cart()
        self.finalizar(self.campos_do_checkout())

        self.add_to_cart()
        self.finalizar(self.campos_do_checkout())

        self.assertEqual(Order.objects.count(), 2)

    def test_an_order_without_a_key_still_works(self):
        """Pedido criado fora do checkout (comando, importação) não tem chave."""
        self.add_to_cart()
        dados = self.campos_do_checkout()
        dados.pop("checkout_token")

        self.finalizar(dados)

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get().checkout_token, "")


# ---------------------------------------------------------------------------
# 2. O comprovante — um por pedido
# ---------------------------------------------------------------------------


class ComprovanteUnicoTests(JourneyBase):
    def setUp(self):
        super().setUp()
        self.pedido = self.comprar()
        self.url = reverse("orders:payment_proof", kwargs={"number": self.pedido.number})
        mail.outbox = []

    # -- a tela de anexar --------------------------------------------------

    def test_the_upload_area_says_what_to_do(self):
        resposta = self.client.get(self.url)

        self.assertContains(resposta, "Anexe o comprovante")
        self.assertContains(resposta, "Escolher o arquivo")
        self.assertContains(resposta, 'type="file"')
        self.assertContains(resposta, "Enviar comprovante")

    def test_the_screen_says_only_once(self):
        resposta = self.client.get(self.url)

        self.assertContains(resposta, "uma vez")

    # -- o primeiro envio --------------------------------------------------

    def test_the_first_upload_works(self):
        resposta = self.enviar_comprovante()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.pedido.payment_proofs.count(), 1)

    def test_a_pdf_is_accepted_too(self):
        self.enviar_comprovante(conteudo=PDF, nome="extrato.pdf")

        self.assertEqual(self.pedido.payment_proofs.get().extension, "pdf")

    # -- o segundo, não ----------------------------------------------------

    def test_the_second_upload_is_refused(self):
        self.enviar_comprovante(nome="primeiro.jpg")

        self.enviar_comprovante(nome="segundo.jpg")

        self.assertEqual(self.pedido.payment_proofs.count(), 1)
        self.assertEqual(self.pedido.payment_proofs.get().original_name, "primeiro.jpg")

    def test_the_page_stops_offering_the_form(self):
        self.enviar_comprovante()

        resposta = self.client.get(self.url)

        self.assertNotContains(resposta, 'type="file"')
        self.assertNotContains(resposta, "Escolher o arquivo")

    def test_the_page_says_the_proof_arrived(self):
        self.enviar_comprovante(nome="meu-comprovante.jpg")

        resposta = self.client.get(self.url)

        self.assertContains(resposta, "Recebemos o seu comprovante")
        self.assertContains(resposta, "meu-comprovante.jpg")

    def test_the_page_explains_how_to_fix_a_wrong_file(self):
        """Sem saída, "não pode substituir" vira parede."""
        self.enviar_comprovante()

        resposta = self.client.get(self.url)

        self.assertContains(resposta, "Fale com a gente")

    def test_the_second_upload_sends_no_second_notice(self):
        self.enviar_comprovante()
        enviados = len(mail.outbox)

        self.enviar_comprovante(nome="segundo.jpg")

        self.assertEqual(len(mail.outbox), enviados)

    def test_the_second_upload_writes_no_second_event(self):
        self.enviar_comprovante()

        self.enviar_comprovante(nome="segundo.jpg")

        self.assertEqual(
            self.pedido.history.filter(event=OrderEvent.PAYMENT_PROOF_RECEIVED).count(), 1
        )

    def test_the_database_refuses_a_second_row(self):
        """A tela recusa antes; isto é a rede embaixo."""
        from django.db import IntegrityError, transaction

        self.enviar_comprovante()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentProof.objects.create(
                    order=self.pedido, original_name="forcado.jpg", extension="jpg"
                )

    def test_uploading_still_does_not_confirm_the_payment(self):
        self.enviar_comprovante()
        self.pedido.refresh_from_db()

        self.assertEqual(self.pedido.payment_status, PaymentStatus.PENDING)
        self.assertIsNone(self.pedido.stock_applied_at)

    # -- a segurança não mudou ---------------------------------------------

    def test_another_customer_still_gets_404(self):
        outro = make_user(username="bruno", email="bruno@exemplo.test")
        self.enviar_comprovante()
        self.client.force_login(outro)

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {}).status_code, 404)

    def test_the_file_is_still_behind_the_permission_check(self):
        self.enviar_comprovante()
        proof = self.pedido.payment_proofs.get()
        url = reverse("orders:payment_proof_file", args=[proof.pk])

        propria = self.client.get(url)
        propria.close()
        self.assertEqual(propria.status_code, 200)

        self.client.force_login(make_user(username="bruno", email="bruno@exemplo.test"))
        alheia = self.client.get(url)
        alheia.close()
        self.assertEqual(alheia.status_code, 404)

    def test_the_stored_name_is_still_unpredictable(self):
        self.enviar_comprovante(nome="../../etc/passwd.jpg")

        caminho = self.pedido.payment_proofs.get().file.name
        self.assertRegex(caminho, r"^payment-proofs/[0-9a-f]{32}\.jpg$")

    def test_an_svg_dressed_as_a_photo_is_still_refused(self):
        resposta = self.enviar_comprovante(
            conteudo=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', nome="falso.jpg"
        )

        self.assertContains(resposta, "Envie uma imagem")
        self.assertEqual(self.pedido.payment_proofs.count(), 0)


# ---------------------------------------------------------------------------
# 3. O Admin acha o comprovante
# ---------------------------------------------------------------------------


class ComprovanteNoAdminTests(JourneyBase):
    def setUp(self):
        super().setUp()
        self.pedido = self.comprar()
        self.client.force_login(self.equipe())

    def pagina(self):
        return self.client.get(reverse("admin:orders_order_change", args=[self.pedido.pk]))

    def test_the_proof_lives_inside_the_payment_section(self):
        """Conferir um pagamento por transferência era olhar dois lugares da
        página ao mesmo tempo: o estado num bloco e o comprovante em outro."""
        corpo = self.pagina().content.decode()
        secao = corpo[corpo.index("2. PAGAMENTO"):corpo.index("3. PRODUÇÃO E ENTREGA")]

        self.assertIn("Comprovante", secao)

    def test_without_a_proof_it_says_so(self):
        resposta = self.pagina()

        self.assertContains(resposta, "nenhum recebido")

    def test_an_image_shows_a_thumbnail(self):
        self.client.force_login(self.user)
        self.enviar_comprovante()
        self.client.force_login(self.equipe())
        proof = self.pedido.payment_proofs.get()

        resposta = self.pagina()

        url = reverse("orders:payment_proof_file", args=[proof.pk])
        self.assertContains(resposta, f'<img src="{url}"')

    def test_a_pdf_shows_a_button_instead(self):
        self.client.force_login(self.user)
        self.enviar_comprovante(conteudo=PDF, nome="extrato.pdf")
        self.client.force_login(self.equipe())

        resposta = self.pagina()

        self.assertContains(resposta, "Abrir o comprovante")
        self.assertNotContains(resposta, "<img src=\"/conta/comprovantes/")

    def test_the_link_never_points_at_media(self):
        """`payment-proofs/` não é servido publicamente — nem por descuido."""
        self.client.force_login(self.user)
        self.enviar_comprovante()
        self.client.force_login(self.equipe())

        resposta = self.pagina()

        self.assertNotContains(resposta, "/media/payment-proofs/")

    def test_the_sections_come_in_the_order_the_operation_reads_them(self):
        corpo = self.pagina().content.decode()
        posicoes = [
            corpo.index(marca)
            for marca in (
                "1. RESUMO",
                "2. PAGAMENTO",
                "3. PRODUÇÃO E ENTREGA",
                "4. ITENS E VALORES",
                "6. HISTÓRICO",
                "7. COMUNICAÇÕES",
            )
        ]

        self.assertEqual(posicoes, sorted(posicoes))

    def test_the_products_are_inside_the_items_section(self):
        """A tabela de itens é um painel da seção 4, não um bloco solto."""
        corpo = self.pagina().content.decode()

        self.assertLess(corpo.index("4. ITENS E VALORES"), corpo.index("Vaso Espiral"))
        self.assertLess(corpo.index("Vaso Espiral"), corpo.index("6. HISTÓRICO"))

    def test_nothing_was_lost_from_the_old_screen(self):
        """As informações das dez seções antigas continuam na tela."""
        corpo = self.pagina().content.decode()

        for informacao in (
            self.pedido.number,          # resumo
            "Transferência bancária",     # pagamento
            "Vaso Espiral",               # itens
            "Subtotal",                   # valores
            "Frete",
            "Total",
            "Rue du Test",                # endereço de entrega
            "Pedido criado",              # histórico
            "Dados para transferência",   # comunicações
        ):
            with self.subTest(informacao=informacao):
                self.assertIn(informacao, corpo)

    def test_the_payment_block_says_whether_the_details_went_out(self):
        resposta = self.pagina()

        self.assertContains(resposta, "Dados enviados em")

    def test_the_payment_block_masks_the_iban(self):
        resposta = self.pagina()

        self.assertContains(resposta, "BE68 ···· 7034")
        self.assertNotContains(resposta, "BE68 5390 0754 7034")


# ---------------------------------------------------------------------------
# 4. Enviar dados bancários duas vezes
# ---------------------------------------------------------------------------


class EnvioRepetidoTests(JourneyBase):
    def setUp(self):
        super().setUp()
        self.pedido = self.comprar()
        self.client.force_login(self.equipe())
        mail.outbox = []

    def test_the_checkout_already_sent_them(self):
        self.pedido.refresh_from_db()

        self.assertIsNotNone(self.pedido.transfer_details_sent_at)

    def test_clicking_send_again_does_not_send(self):
        """O clique por hábito mandava um segundo e-mail idêntico."""
        self.acao("action_send_transfer_details", self.pedido)

        self.assertEqual(mail.outbox, [])

    def test_it_says_why_nothing_happened(self):
        resposta = self.acao("action_send_transfer_details", self.pedido, follow_extra())

        self.assertIn("Reenviar", conteudo_das_mensagens(self.client, resposta))

    def test_clicking_send_again_writes_no_second_event(self):
        antes = self.pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).count()

        self.acao("action_send_transfer_details", self.pedido)

        self.assertEqual(
            self.pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).count(), antes
        )

    def test_resending_is_a_separate_deliberate_action(self):
        resposta = self.acao(
            "action_resend_transfer_details", self.pedido, {"conta": str(self.conta.pk)}
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertNotEqual(resposta.status_code, 500)

    def test_resending_records_the_second_send(self):
        self.acao("action_resend_transfer_details", self.pedido, {"conta": str(self.conta.pk)})

        self.assertEqual(
            self.pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).count(), 2
        )

    def test_an_order_that_never_received_them_still_gets_them(self):
        """A proteção é contra repetir, não contra enviar."""
        self.pedido.transfer_details_sent_at = None
        self.pedido.save(update_fields=["transfer_details_sent_at"])

        self.acao("action_send_transfer_details", self.pedido, {"conta": str(self.conta.pk)})

        self.assertEqual(len(mail.outbox), 1)


def follow_extra():
    return {}


def conteudo_das_mensagens(client, resposta):
    from django.contrib.messages import get_messages

    return " ".join(str(m) for m in get_messages(resposta.wsgi_request))


# ---------------------------------------------------------------------------
# 5. "Pagar agora"
# ---------------------------------------------------------------------------


class PagarAgoraTests(JourneyBase):
    def setUp(self):
        super().setUp()
        self.pedido = self.comprar()
        self.url = reverse("orders:retry_payment", kwargs={"number": self.pedido.number})
        mail.outbox = []

    def test_it_opens_a_page_that_asks_again(self):
        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Forma de pagamento")
        self.assertContains(resposta, self.pedido.number)

    def test_the_available_methods_are_the_ones_of_today(self):
        resposta = self.client.get(self.url)

        self.assertContains(resposta, 'value="transfer"')
        self.assertContains(resposta, "Em breve")

    def test_paying_again_creates_no_second_order(self):
        self.client.post(self.url, {"payment_method": "transfer"})

        self.assertEqual(Order.objects.count(), 1)

    def test_paying_again_sends_the_details_again(self):
        self.client.post(self.url, {"payment_method": "transfer"})

        cliente = [m for m in mail.outbox if m.to == [self.user.email]]
        self.assertEqual(len(cliente), 1)
        self.assertIn("BE68 5390 0754 7034", cliente[0].body)

    def test_it_uses_the_default_account_of_today(self):
        """A conta mudou entre as tentativas: vale a de agora."""
        BankAccount.objects.create(
            label="Nova", beneficiary="JD PRINT SRL",
            iban="BE99 8888 7777 6666", is_default=True,
        )

        self.client.post(self.url, {"payment_method": "transfer"})

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.bank_iban, "BE99 8888 7777 6666")
        cliente = [m for m in mail.outbox if m.to == [self.user.email]][0]
        self.assertIn("BE99 8888 7777 6666", cliente.body)
        self.assertNotIn("BE68 5390 0754 7034", cliente.body)

    def test_the_previous_attempt_stays_in_the_history(self):
        """Trocar a conta não apaga para onde o cliente foi mandado antes."""
        BankAccount.objects.create(
            label="Nova", beneficiary="JD PRINT SRL",
            iban="BE99 8888 7777 6666", is_default=True,
        )

        self.client.post(self.url, {"payment_method": "transfer"})

        mensagens = [
            e.message
            for e in self.pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT)
        ]
        self.assertEqual(len(mensagens), 2)
        self.assertTrue(any("BE68 ···· 7034" in m for m in mensagens))
        self.assertTrue(any("BE99 ···· 6666" in m for m in mensagens))

    def test_it_refuses_a_method_that_is_not_available(self):
        resposta = self.client.post(self.url, {"payment_method": "card"})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "forma de pagamento")
        self.assertEqual(mail.outbox, [])

    def test_with_one_option_the_silence_means_that_one(self):
        self.client.post(self.url, {})

        self.assertEqual(len(mail.outbox) > 0, True)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.payment_method, "transfer")

    def test_it_does_not_assume_the_method_chosen_before(self):
        """A escolha antiga não vale como resposta de hoje."""
        with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY=""):
            resposta = self.client.post(self.url, {})

        self.assertContains(resposta, "forma de pagamento")
        self.assertEqual(mail.outbox, [])

    # -- o estoque de hoje --------------------------------------------------

    def test_a_product_that_ran_out_blocks_the_payment(self):
        ProductVariant.objects.filter(pk=self.variante.pk).update(stock_quantity=0)

        resposta = self.client.get(self.url)

        self.assertContains(resposta, "esgotado")
        self.assertNotContains(resposta, 'value="transfer"')

    def test_a_product_that_ran_out_is_refused_on_post_too(self):
        """A tela pode ter sido aberta antes de a última unidade sair."""
        ProductVariant.objects.filter(pk=self.variante.pk).update(stock_quantity=0)

        self.client.post(self.url, {"payment_method": "transfer"})

        self.assertEqual(mail.outbox, [])

    def test_a_product_that_left_the_catalogue_blocks_it(self):
        self.produto.status = ProductStatus.INACTIVE
        self.produto.save(update_fields=["status"])

        resposta = self.client.get(self.url)

        self.assertContains(resposta, "não está mais disponível")

    def test_a_product_still_available_lets_it_through(self):
        resposta = self.client.get(self.url)

        self.assertNotContains(resposta, "esgotado")
        self.assertContains(resposta, "Continuar")

    def test_a_paid_order_goes_back_to_the_order(self):
        self.pedido.payment_status = PaymentStatus.PAID
        self.pedido.save(update_fields=["payment_status"])

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 302)

    def test_another_customer_gets_404(self):
        self.client.force_login(make_user(username="bruno", email="bruno@exemplo.test"))

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {}).status_code, 404)

    def test_without_a_default_account_it_says_so(self):
        BankAccount.objects.all().update(is_active=False)

        resposta = self.client.post(self.url, {"payment_method": "transfer"})

        self.assertContains(resposta, "transferência está indisponível")
        self.assertEqual(mail.outbox, [])


# ---------------------------------------------------------------------------
# 6. O que o cliente lê
# ---------------------------------------------------------------------------


class AcompanhamentoDoClienteTests(JourneyBase):
    def setUp(self):
        super().setUp()
        self.pedido = self.comprar()
        self.url = reverse("orders:detail", kwargs={"number": self.pedido.number})

    def test_the_timeline_speaks_to_the_customer(self):
        resposta = self.client.get(self.url)

        self.assertContains(resposta, "Recebemos a sua encomenda")
        self.assertContains(resposta, "Enviámos para o seu e-mail os dados")

    def test_the_account_name_and_iban_never_appear(self):
        resposta = self.client.get(self.url)

        self.assertNotContains(resposta, "Principal")
        self.assertNotContains(resposta, "BE68")
        self.assertNotContains(resposta, "conta:")

    def test_the_file_name_never_appears(self):
        self.enviar_comprovante(nome="Teste novo.png")

        resposta = self.client.get(self.url)

        self.assertNotContains(resposta, "Teste novo.png")
        self.assertContains(resposta, "Recebemos o seu comprovativo")

    def test_the_date_comes_before_the_message(self):
        corpo = self.client.get(self.url).content.decode()
        bloco = corpo[corpo.index("Acompanhamento") :]
        from django.utils.timezone import localtime

        data = bloco.index(localtime(self.pedido.created_at).strftime("%d/%m/%Y"))
        frase = bloco.index("Recebemos a sua encomenda")

        self.assertLess(data, frase)

    def test_the_technical_event_label_is_gone(self):
        resposta = self.client.get(self.url)

        self.assertNotContains(resposta, "Dados bancários enviados")

    def test_the_internal_history_keeps_the_detail_for_the_team(self):
        """A anotação não some — ela muda de público."""
        entrada = self.pedido.history.get(event=OrderEvent.TRANSFER_DETAILS_SENT)

        self.assertIn("BE68 ···· 7034", entrada.message)
        self.assertNotIn("BE68", entrada.customer_message)

    def test_the_messages_are_translated(self):
        esperado = {
            "/fr": "Nous avons bien reçu votre commande",
            "/nl": "We hebben uw bestelling ontvangen",
            # O apóstrofo sai escapado no HTML; o trecho evita a questão.
            "/en": "received your order",
        }
        for prefixo, texto in esperado.items():
            with self.subTest(idioma=prefixo):
                resposta = self.client.get(f"{prefixo}{self.url}")

                self.assertContains(resposta, texto)

    def test_the_tracking_code_reaches_the_customer(self):
        self.pedido.log(OrderEvent.SHIPPED, "BE555444333")

        resposta = self.client.get(self.url)

        self.assertContains(resposta, "BE555444333")


# ---------------------------------------------------------------------------
# 7. O fluxo inteiro, uma vez — e depois de novo
# ---------------------------------------------------------------------------


class FluxoCompletoTests(JourneyBase):
    def test_the_whole_journey_end_to_end(self):
        """Checkout → dados → comprovante → confirmação → envio."""
        pedido = self.comprar()

        # 1. o pedido nasceu pendente, com os dados já enviados
        self.assertEqual(pedido.payment_status, PaymentStatus.PENDING)
        self.assertIsNotNone(pedido.transfer_details_sent_at)
        self.assertEqual(len([m for m in mail.outbox if m.to == [self.user.email]]), 1)

        # 2. o cliente manda o comprovante — e a equipe é avisada
        mail.outbox = []
        self.enviar_comprovante()
        self.assertEqual(pedido.payment_proofs.count(), 1)
        self.assertEqual(len([m for m in mail.outbox if m.to == ["loja@jdprint.test"]]), 1)
        pedido.refresh_from_db()
        self.assertEqual(pedido.payment_status, PaymentStatus.PENDING)

        # 3. a equipe confirma — estoque, e-mail e produção
        mail.outbox = []
        estoque = ProductVariant.objects.get(pk=self.variante.pk).stock_quantity
        self.client.force_login(self.equipe())
        self.acao("action_confirm_payment", pedido, {"confirmar": "1"})

        pedido.refresh_from_db()
        self.assertEqual(pedido.payment_status, PaymentStatus.PAID)
        self.assertIsNotNone(pedido.paid_at)
        self.assertIsNotNone(pedido.stock_applied_at)
        self.assertEqual(
            ProductVariant.objects.get(pk=self.variante.pk).stock_quantity, estoque - 1
        )
        self.assertEqual(len([m for m in mail.outbox if m.to == [self.user.email]]), 1)

        # 4. confirmar de novo não refaz nada
        mail.outbox = []
        self.acao("action_confirm_payment", pedido, {"confirmar": "1"})
        self.assertEqual(
            ProductVariant.objects.get(pk=self.variante.pk).stock_quantity, estoque - 1
        )
        self.assertEqual(mail.outbox, [])

        # 5. o cliente lê a história dele, em frases
        self.client.force_login(self.user)
        resposta = self.client.get(
            reverse("orders:detail", kwargs={"number": pedido.number})
        )
        self.assertContains(resposta, "O seu pagamento foi confirmado")
        self.assertNotContains(resposta, "BE68")


class StripeSegueIntactaTests(JourneyBase):
    """A etapa mexeu na repetição, não no gateway."""

    def test_the_checkout_key_does_not_depend_on_the_provider(self):
        with override_settings(PAYMENT_PROVIDER="stripe", STRIPE_SECRET_KEY="sk_test_x"):
            self.add_to_cart()
            pagina = self.client.get("/carrinho/finalizar/")

            self.assertIsNotNone(pagina.context["form"]["checkout_token"].value())

    def test_confirm_payment_is_still_the_shared_routine(self):
        import inspect

        from apps.orders import services

        assinatura = inspect.signature(services.confirm_payment)
        self.assertIn("user", assinatura.parameters)
        self.assertIsNone(assinatura.parameters["user"].default)
