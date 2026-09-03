"""Da conta escolhida no Admin ao comprovante que o cliente devolve.

O que o fluxo promete, e o que estes testes seguram:

1. **nada de dados bancários sai sozinho.** O e-mail com o IBAN só existe
   depois de uma pessoa da equipe abrir o pedido, escolher a conta e clicar;
2. **a conta enviada é a escolhida** — não "a primeira", não "a única";
3. **o comprovante é do dono** e de mais ninguém: nem por URL, nem por id, nem
   trocando o número do pedido;
4. **receber comprovante não é receber dinheiro.** O pagamento continua
   pendente até alguém ver o extrato.
"""

import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

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

TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="jdprint-test-proof-")

#: Arquivos de verdade: o reconhecimento é por assinatura, não por extensão.
JPG = b"\xff\xd8\xff" + b"bytes-de-uma-foto-de-comprovante"
PDF = b"%PDF-1.7\n" + b"bytes-de-um-pdf-do-banco"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


# ---------------------------------------------------------------------------
# Cenário
# ---------------------------------------------------------------------------


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT, PAYMENT_PROVIDER="transfer")
class TransferFlowBase(LanguageResetMixin, TestCase):
    """Um pedido pendente, o dono dele, um estranho e a equipe."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="modelos", name="Modelos")
        self.pais = make_country("BE", vat_rate="21.00")
        self.metodo = make_method(min_days=2, max_days=3)
        make_rate(self.metodo, self.pais, 0, 5000, "4.90")
        self.produto = make_product(
            sku="TRANSF-01", name="Vaso Espiral", category=self.categoria,
            price=Decimal("19.90"), stock_quantity=10, weight_grams=Decimal("300"),
        )

        self.dono = make_user(username="ana", email="ana@exemplo.test")
        self.dono.customer.first_name = "Ana"
        self.dono.customer.save()
        make_address(self.dono.customer, self.pais, first_name="Ana", last_name="Ribeiro")

        self.estranho = make_user(username="bruno", email="bruno@exemplo.test")
        make_address(self.estranho.customer, self.pais, first_name="Bruno", last_name="Silva")

        self.pedido = self.fazer_pedido(self.dono)
        self.pedido_alheio = self.fazer_pedido(self.estranho)

        self.principal = BankAccount.objects.create(
            label="Principal", beneficiary="JD PRINT SRL",
            iban="BE68 5390 0754 7034", bic="GEBABEBB",
            instructions="Use o número do pedido na comunicação.",
            sort_order=0,
        )
        self.segunda = BankAccount.objects.create(
            label="Segunda", beneficiary="JD PRINT SRL",
            iban="BE11 2222 3333 4444", sort_order=1,
        )
        mail.outbox = []

    def fazer_pedido(self, user, **campos):
        from apps.orders.models import Order

        return Order.objects.create(
            customer=user.customer,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            total=Decimal("24.80"),
            currency="EUR",
            language="pt",
            **campos,
        )

    # -- atalhos -----------------------------------------------------------

    def equipe(self, permissoes=("view_order", "change_order", "view_paymentproof")):
        """Alguém da equipe.

        ``view_paymentproof`` entra no conjunto padrão porque é ele que faz o
        Admin desenhar o inline dos comprovantes: o Django esconde um inline de
        quem não tem permissão naquele model, e é o comportamento certo.

        A rota do arquivo é mais larga de propósito — aceita também
        ``view_order`` —, pelo mesmo desenho da foto de personalização: quem
        pode ver o pedido precisa poder abrir o comprovante dele para conferir
        o pagamento.
        """
        pessoa = get_user_model().objects.create_user(
            username=f"equipe{Permission.objects.count()}",
            email="equipe@jdprint.test",
            password="senha-de-teste-77",
            is_staff=True,
        )
        pessoa.user_permissions.set(
            Permission.objects.filter(
                codename__in=permissoes, content_type__app_label="orders"
            )
        )
        return pessoa

    def enviar_dados(self, conta=None, pedido=None, index="0"):
        """A ação do Admin. Sem `conta`, para na tela de escolha."""
        pedido = pedido or self.pedido
        dados = {
            "action": "action_send_transfer_details",
            "index": index,
            "_selected_action": [str(pedido.pk)],
        }
        if conta is not None:
            dados["conta"] = str(conta.pk)
        return self.client.post(reverse("admin:orders_order_changelist"), dados)

    def comprovante(self, conteudo=JPG, nome="comprovante.jpg", pedido=None):
        from django.core.files.uploadedfile import SimpleUploadedFile

        pedido = pedido or self.pedido
        return self.client.post(
            reverse("orders:payment_proof", kwargs={"number": pedido.number}),
            {"file": SimpleUploadedFile(nome, conteudo, content_type="image/jpeg")},
        )

    def abrir(self, url):
        """GET que **fecha** a resposta.

        `FileResponse` mantém o arquivo aberto até alguém fechar; no Windows
        isso impede a limpeza do diretório temporário.
        """
        resposta = self.client.get(url)
        resposta.close()
        return resposta


# ---------------------------------------------------------------------------
# 1. Enviar os dados bancários — só por ação de uma pessoa
# ---------------------------------------------------------------------------


class EnvioDeDadosBancariosTests(TransferFlowBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.equipe())

    def test_the_action_opens_the_screen_that_asks_which_account(self):
        """O clique na lista não envia: abre a escolha, como a exclusão faz."""
        resposta = self.enviar_dados()

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Principal")
        self.assertContains(resposta, "Segunda")
        self.assertEqual(len(mail.outbox), 0, "nada pode sair antes de escolher")

    def test_the_whole_flow_works_from_the_order_screen_too(self):
        """Os dois caminhos até o envio, e o segundo é o que se usa de verdade.

        Existe porque quebrou: o formulário da tela de escolha postava
        ``action``, que é o nome que a **lista** procura. Disparada de dentro do
        pedido, a mesma tela posta de volta para a URL do pedido — e lá o Django
        procura ``CHANGE_FORM-action``, com o prefixo do ``ActionLocation``. Sem
        a chave certa, o POST deixava de ser uma ação e virava uma tentativa de
        salvar o pedido: nenhum e-mail saía, e nada acusava.

        Os testes que existiam disparavam tudo pela lista e passavam.
        """
        url = reverse("admin:orders_order_change", args=[self.pedido.pk])

        # 1. a ação é oferecida na tela do pedido
        pagina = self.client.get(url)
        escolhas = dict(pagina.context["action_form"].fields["action"].choices)
        self.assertIn("action_send_transfer_details", escolhas)

        # 2. disparada de lá, abre a escolha da conta
        escolha = self.client.post(
            url,
            {
                "CHANGE_FORM-action": "action_send_transfer_details",
                "_selected_action": [str(self.pedido.pk)],
            },
        )
        self.assertEqual(escolha.status_code, 200)
        self.assertContains(escolha, "Principal")
        self.assertEqual(len(mail.outbox), 0, "ainda não escolheu nada")

        # 3. o formulário devolve a chave que a tela do pedido procura
        self.assertContains(escolha, 'name="CHANGE_FORM-action"')

        # 4. e escolher envia de verdade
        self.client.post(
            url,
            {
                "CHANGE_FORM-action": "action_send_transfer_details",
                "_selected_action": [str(self.pedido.pk)],
                "conta": str(self.segunda.pk),
                "index": "0",
            },
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("BE11 2222 3333 4444", mail.outbox[0].body)
        self.pedido.refresh_from_db()
        self.assertIsNotNone(self.pedido.transfer_details_sent_at)

    def test_only_usable_accounts_are_offered(self):
        BankAccount.objects.create(label="Desativada", beneficiary="JD", iban="BE99", is_active=False)
        BankAccount.objects.create(label="Sem IBAN", beneficiary="JD", iban="")

        resposta = self.enviar_dados()

        self.assertNotContains(resposta, "Desativada")
        self.assertNotContains(resposta, "Sem IBAN")

    def test_choosing_the_account_sends_that_account(self):
        self.enviar_dados(conta=self.segunda)

        self.assertEqual(len(mail.outbox), 1)
        corpo = mail.outbox[0].body
        self.assertIn("BE11 2222 3333 4444", corpo)
        self.assertNotIn("BE68 5390 0754 7034", corpo, "foi a conta errada")
        self.assertEqual(mail.outbox[0].to, ["ana@exemplo.test"])

    def test_the_email_carries_what_the_customer_needs_to_pay(self):
        self.enviar_dados(conta=self.principal)

        corpo = mail.outbox[0].body
        self.assertIn(self.pedido.number, corpo)  # identificação e comunicação
        self.assertIn("24,80", corpo)  # valor, no formato do idioma do pedido
        self.assertIn("JD PRINT SRL", corpo)  # titular
        self.assertIn("BE68 5390 0754 7034", corpo)  # conta
        self.assertIn("Use o número do pedido", corpo)  # instruções
        self.assertIn(
            reverse("orders:payment_proof", kwargs={"number": self.pedido.number}),
            corpo,
            "faltou o link do comprovante",
        )

    def test_the_order_records_that_the_details_went_out(self):
        self.enviar_dados(conta=self.principal)
        self.pedido.refresh_from_db()

        self.assertIsNotNone(self.pedido.transfer_details_sent_at)
        entrada = self.pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).first()
        self.assertIsNotNone(entrada)
        self.assertIn("Principal", entrada.message)

    def test_the_history_identifies_the_account_without_repeating_the_iban(self):
        """O histórico é lido por muita gente; o IBAN inteiro não precisa estar lá."""
        self.enviar_dados(conta=self.principal)

        mensagem = self.pedido.history.filter(event=OrderEvent.TRANSFER_DETAILS_SENT).first().message
        self.assertIn("BE68 ···· 7034", mensagem)
        self.assertNotIn("5390 0754", mensagem)

    def test_nothing_is_sent_to_the_customer_without_the_action(self):
        """A regra central: dados bancários não saem sozinhos."""
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(self.pedido.transfer_details_sent_at)

    def test_an_account_deactivated_meanwhile_is_refused(self):
        """O id vem do navegador; a conta é procurada de novo entre as usáveis."""
        self.segunda.is_active = False
        self.segunda.save(update_fields=["is_active"])

        self.enviar_dados(conta=self.segunda)

        self.assertEqual(len(mail.outbox), 0)
        self.pedido.refresh_from_db()
        self.assertIsNone(self.pedido.transfer_details_sent_at)

    def test_without_any_account_the_action_explains_instead_of_failing(self):
        BankAccount.objects.all().update(is_active=False)

        resposta = self.enviar_dados()

        self.assertEqual(len(mail.outbox), 0)
        self.assertNotEqual(resposta.status_code, 500)

    def test_sending_does_not_confirm_the_payment(self):
        self.enviar_dados(conta=self.principal)
        self.pedido.refresh_from_db()

        self.assertEqual(self.pedido.payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.pedido.status, OrderStatus.PENDING)


class EnvioSemPermissaoTests(TransferFlowBase):
    """Quem não pode alterar pedidos não manda IBAN para cliente nenhum."""

    def test_staff_without_change_permission_is_not_offered_the_action(self):
        self.client.force_login(self.equipe(permissoes=("view_order",)))

        resposta = self.client.get(reverse("admin:orders_order_changelist"))

        self.assertNotContains(resposta, "action_send_transfer_details")

    def test_staff_without_change_permission_cannot_force_the_action(self):
        self.client.force_login(self.equipe(permissoes=("view_order",)))

        self.enviar_dados(conta=self.principal)

        self.assertEqual(len(mail.outbox), 0)
        self.pedido.refresh_from_db()
        self.assertIsNone(self.pedido.transfer_details_sent_at)

    def test_a_customer_cannot_reach_the_admin_action(self):
        self.client.force_login(self.dono)

        resposta = self.enviar_dados(conta=self.principal)

        self.assertNotEqual(resposta.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


# ---------------------------------------------------------------------------
# 2. O comprovante
# ---------------------------------------------------------------------------


class ComprovanteTests(TransferFlowBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.dono)
        self.url = reverse("orders:payment_proof", kwargs={"number": self.pedido.number})

    # -- a página ----------------------------------------------------------

    def test_the_page_identifies_the_order(self):
        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, self.pedido.number)
        self.assertContains(resposta, "24,80")

    def test_the_page_says_that_sending_is_not_confirming(self):
        resposta = self.client.get(self.url)

        self.assertContains(resposta, "não confirma o pagamento")

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        self.client.logout()

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta["Location"])

    # -- o arquivo ---------------------------------------------------------

    def test_a_photo_is_accepted(self):
        resposta = self.comprovante()

        self.assertEqual(resposta.status_code, 302)
        proof = self.pedido.payment_proofs.get()
        self.assertEqual(proof.extension, "jpg")
        self.assertEqual(proof.original_name, "comprovante.jpg")

    def test_a_pdf_is_accepted(self):
        """É o que o banco entrega a quem paga pelo internet banking."""
        self.comprovante(conteudo=PDF, nome="extrato.pdf")

        self.assertEqual(self.pedido.payment_proofs.get().extension, "pdf")

    def test_an_svg_dressed_as_a_photo_is_refused(self):
        """O nome do arquivo é do cliente; os primeiros bytes, não."""
        resposta = self.comprovante(conteudo=SVG, nome="comprovante.jpg")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Envie uma imagem")
        self.assertEqual(self.pedido.payment_proofs.count(), 0)

    def test_an_html_file_is_refused(self):
        resposta = self.comprovante(conteudo=b"<html><script>x</script></html>", nome="a.png")

        self.assertEqual(self.pedido.payment_proofs.count(), 0)
        self.assertContains(resposta, "Envie uma imagem")

    def test_an_empty_file_is_refused(self):
        resposta = self.comprovante(conteudo=b"", nome="vazio.jpg")

        self.assertEqual(self.pedido.payment_proofs.count(), 0)
        self.assertContains(resposta, "está vazio")

    @override_settings(PAYMENT_PROOF_MAX_UPLOAD_SIZE=64)
    def test_a_file_over_the_limit_is_refused(self):
        resposta = self.comprovante(conteudo=JPG + b"0" * 500)

        self.assertEqual(self.pedido.payment_proofs.count(), 0)
        self.assertContains(resposta, "no máximo")

    def test_the_stored_name_is_not_the_one_the_customer_chose(self):
        self.comprovante(nome="../../etc/passwd.jpg")

        caminho = self.pedido.payment_proofs.get().file.name
        self.assertTrue(caminho.startswith("payment-proofs/"))
        self.assertNotIn("passwd", caminho)
        self.assertNotIn("..", caminho)

    # -- o que acontece depois ---------------------------------------------

    def test_the_team_is_notified(self):
        self.comprovante()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Comprovante recebido", mail.outbox[0].subject)
        self.assertIn(self.pedido.number, mail.outbox[0].subject)

    def test_the_notice_says_the_payment_is_not_confirmed(self):
        self.comprovante()

        self.assertIn("NÃO está confirmado", mail.outbox[0].body)

    def test_the_notice_does_not_carry_the_file(self):
        """E-mail se reencaminha; o comprovante fica atrás da conferência."""
        self.comprovante()

        self.assertEqual(mail.outbox[0].attachments, [])

    def test_the_order_records_the_proof(self):
        self.comprovante()

        entrada = self.pedido.history.filter(event=OrderEvent.PAYMENT_PROOF_RECEIVED).first()
        self.assertIsNotNone(entrada)
        self.assertIn("comprovante.jpg", entrada.message)

    def test_the_payment_is_not_confirmed_automatically(self):
        """A regra que não pode cair: arquivo não é dinheiro."""
        self.comprovante()
        self.pedido.refresh_from_db()

        self.assertEqual(self.pedido.payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.pedido.status, OrderStatus.PENDING)
        self.assertIsNone(self.pedido.paid_at)
        self.assertIsNone(self.pedido.stock_applied_at)

    def test_a_second_proof_is_refused_and_does_not_erase_the_first(self):
        """Um comprovante por pedido — e o primeiro não é substituído.

        Trocar o documento sozinho depois de a equipe já ter olhado é
        exatamente o que não pode acontecer. Se o cliente mandou o arquivo
        errado, quem resolve é a equipe.
        """
        self.comprovante(nome="primeiro.jpg")

        self.comprovante(nome="segundo.jpg")

        self.assertEqual(self.pedido.payment_proofs.count(), 1)
        self.assertEqual(self.pedido.payment_proofs.get().original_name, "primeiro.jpg")


# ---------------------------------------------------------------------------
# 3. Ninguém alcança o pedido nem o comprovante de outra pessoa
# ---------------------------------------------------------------------------


class IsolamentoEntreClientesTests(TransferFlowBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.dono)
        self.comprovante()
        self.proof = self.pedido.payment_proofs.get()
        self.arquivo_url = reverse("orders:payment_proof_file", args=[self.proof.pk])

    def test_another_customer_gets_404_on_the_order_page(self):
        """404 e não 403: um 403 confirmaria que aquele número existe."""
        self.client.force_login(self.estranho)

        resposta = self.client.get(
            reverse("orders:payment_proof", kwargs={"number": self.pedido.number})
        )

        self.assertEqual(resposta.status_code, 404)

    def test_another_customer_cannot_post_a_proof_to_someone_elses_order(self):
        self.client.force_login(self.estranho)

        resposta = self.comprovante(nome="intruso.jpg")

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.pedido.payment_proofs.count(), 1)

    def test_a_number_that_does_not_exist_answers_the_same_404(self):
        resposta = self.client.get(
            reverse("orders:payment_proof", kwargs={"number": "JD-2026-999999"})
        )

        self.assertEqual(resposta.status_code, 404)

    def test_the_owner_can_read_the_file(self):
        resposta = self.abrir(self.arquivo_url)

        self.assertEqual(resposta.status_code, 200)

    def test_another_customer_cannot_read_the_file(self):
        self.client.force_login(self.estranho)

        self.assertEqual(self.abrir(self.arquivo_url).status_code, 404)

    def test_an_anonymous_visitor_cannot_read_the_file(self):
        self.client.logout()

        self.assertEqual(self.abrir(self.arquivo_url).status_code, 404)

    def test_an_id_that_does_not_exist_answers_the_same_404(self):
        """Existir e não poder ver respondem igual — nada a enumerar."""
        self.client.force_login(self.estranho)
        existe = self.abrir(self.arquivo_url).status_code
        nao_existe = self.abrir(reverse("orders:payment_proof_file", args=[10**9])).status_code

        self.assertEqual(existe, nao_existe)
        self.assertEqual(existe, 404)

    def test_the_team_can_read_the_file(self):
        self.client.force_login(self.equipe())

        self.assertEqual(self.abrir(self.arquivo_url).status_code, 200)

    def test_staff_without_order_permission_cannot_read_the_file(self):
        self.client.force_login(self.equipe(permissoes=("add_ordernote",)))

        self.assertEqual(self.abrir(self.arquivo_url).status_code, 404)


class ComprovanteNaoEhPublicoTests(TransferFlowBase):
    """O arquivo não sai por conhecer o caminho — nem em desenvolvimento."""

    def test_the_folder_is_not_among_the_ones_served_as_files(self):
        """O urlconf é montado no import; a garantia é sobre a regra.

        Em produção quem serve arquivo é o proxy da hospedagem, e o mapeamento
        aponta para as mesmas duas pastas. `payment-proofs/` não está em
        nenhuma das duas listas.
        """
        import io
        import re

        fonte = io.open("config/urls.py", encoding="utf-8").read()

        publicas = re.search(r"for _publica in \(([^)]*)\)", fonte)
        self.assertIsNotNone(publicas, "o laço das pastas públicas sumiu de config/urls.py")
        servidas = {nome.strip(" \"'") for nome in publicas.group(1).split(",") if nome.strip()}

        # A lista cresce quando o site ganha um tipo novo de imagem pública
        # (`brand/` entrou com as logos administráveis). O que não pode crescer
        # é o outro lado.
        self.assertIn("products", servidas)
        for privada in ("customizations", "payment-proofs"):
            with self.subTest(pasta=privada):
                self.assertNotIn(privada, servidas)
        self.assertNotIn("payment-proofs", fonte)
        self.assertNotIn(
            "static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)", fonte
        )

    def test_no_template_links_straight_to_the_stored_file(self):
        """`proof.file.url` num template devolveria o caminho cru."""
        import io
        from pathlib import Path

        suspeitos = []
        for caminho in list(Path("templates").rglob("*.html")):
            conteudo = io.open(caminho, encoding="utf-8").read()
            if "proof.file.url" in conteudo or "payment-proofs/" in conteudo:
                suspeitos.append(str(caminho))

        self.assertEqual(suspeitos, [])

    def test_the_upload_path_is_unpredictable(self):
        self.client.force_login(self.dono)
        self.comprovante()

        nome = self.pedido.payment_proofs.get().file.name
        self.assertRegex(nome, r"^payment-proofs/[0-9a-f]{32}\.jpg$")


# ---------------------------------------------------------------------------
# 4. O Admin mostra o comprovante para a equipe
# ---------------------------------------------------------------------------


class ComprovanteNoAdminTests(TransferFlowBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.dono)
        self.comprovante()
        self.client.force_login(self.equipe())

    def test_the_order_page_shows_the_proof(self):
        resposta = self.client.get(
            reverse("admin:orders_order_change", args=[self.pedido.pk])
        )

        self.assertContains(resposta, "Comprovante enviado pelo cliente")
        self.assertContains(resposta, "comprovante.jpg")

    def test_the_link_goes_through_the_protected_route(self):
        proof = self.pedido.payment_proofs.get()

        resposta = self.client.get(
            reverse("admin:orders_order_change", args=[self.pedido.pk])
        )

        self.assertContains(
            resposta, reverse("orders:payment_proof_file", args=[proof.pk])
        )
