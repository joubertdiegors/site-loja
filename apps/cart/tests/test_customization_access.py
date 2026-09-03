"""A foto de personalização deixou de ser um arquivo público.

Antes bastava ter a URL de `/media/customizations/<uuid>.jpg`: ela circula no
e-mail interno de cada pedido, e-mail se reencaminha, e a foto de um cliente
ficava aberta na internet para sempre.

Agora quem entrega é uma view que confere quem está pedindo. Estes testes são
das **portas**: quem entra, quem não entra, e o que acontece com id que não
existe. Recusa é sempre 404 — um 403 confirmaria que o arquivo existe.
"""

from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from apps.cart.models import CustomizationUpload
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

#: Um PNG mínimo de verdade — a view devolve os bytes, então eles existem.
PNG = b"\x89PNG\r\n\x1a\n" + b"conteudo-da-foto-do-cliente"


class CustomizationAccessBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "4.90")

        self.dono = make_user(username="dono", email="dono@jdprint.test")
        self.estranho = make_user(username="estranho", email="estranho@jdprint.test")

        self.upload = CustomizationUpload.objects.create(
            original_name="minha-foto.png",
            content_type="image/png",
            extension="png",
            size_bytes=len(PNG),
            session_key="sessao-de-quem-enviou",
        )
        self.upload.file.save("teste.png", ContentFile(PNG), save=True)

        self.url = reverse("cart:customization_file", args=[self.upload.pk])

    def tearDown(self):
        try:
            self.upload.file.delete(save=False)
        except (FileNotFoundError, OSError):
            pass
        super().tearDown()

    def abrir(self, url=None):
        """GET que **fecha** a resposta.

        `FileResponse` mantém o arquivo aberto até alguém fechar; no Windows
        isso impede o `tearDown` de apagá-lo. Em produção quem fecha é o
        servidor WSGI, depois de escrever os bytes.
        """
        resposta = self.client.get(url or self.url)
        resposta.close()
        return resposta

    def fazer_pedido_do_dono(self):
        """Um pedido do `dono` cujo item aponta para o upload."""
        from apps.orders.models import Order, OrderItem, OrderStatus, PaymentStatus

        customer = self.dono.customer
        make_address(customer, self.country)
        produto = make_product(
            sku="PERS-01", name="Vaso", category=self.category,
            price=Decimal("19.90"), stock_quantity=5,
        )
        pedido = Order.objects.create(
            customer=customer,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            currency="EUR",
        )
        OrderItem.objects.create(
            order=pedido,
            product=produto,
            variant=produto.default_variant,
            product_name="Vaso",
            quantity=1,
            unit_price=Decimal("19.90"),
            personalization_type="photo",
            personalization_upload=self.upload,
        )
        return pedido


# ---------------------------------------------------------------------------
# Quem entra
# ---------------------------------------------------------------------------


class AuthorizedAccessTests(CustomizationAccessBase):
    def test_the_customer_who_ordered_it_can_open_it(self):
        """1. Acesso autorizado — o dono do pedido."""
        self.fazer_pedido_do_dono()
        self.client.force_login(self.dono)

        resposta = self.client.get(self.url)
        conteudo = b"".join(resposta.streaming_content)
        resposta.close()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(conteudo, PNG)

    def test_staff_with_permission_can_open_it(self):
        """7. Admin/Staff autorizado — é quem produz a peça."""
        equipe = make_user(username="equipe", email="equipe@jdprint.test", is_staff=True)
        equipe.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="cart", codename="view_customizationupload"
            )
        )
        self.client.force_login(equipe)

        self.assertEqual(self.abrir().status_code, 200)

    def test_staff_who_can_see_orders_can_open_it(self):
        """O link vive no admin do pedido: quem vê o pedido vê o anexo."""
        equipe = make_user(username="pedidos", email="pedidos@jdprint.test", is_staff=True)
        equipe.user_permissions.add(
            Permission.objects.get(content_type__app_label="orders", codename="view_order")
        )
        self.client.force_login(equipe)

        self.assertEqual(self.abrir().status_code, 200)

    def test_a_superuser_can_open_it(self):
        chefe = make_user(username="chefe", email="chefe@jdprint.test", is_staff=True,
                          is_superuser=True)
        self.client.force_login(chefe)

        self.assertEqual(self.abrir().status_code, 200)

    def test_whoever_just_uploaded_it_can_still_see_it(self):
        """O carrinho ainda aberto: o pedido nem existe, mas a sessão é a mesma."""
        sessao = self.client.session
        sessao.save()
        CustomizationUpload.objects.filter(pk=self.upload.pk).update(
            session_key=sessao.session_key
        )

        self.assertEqual(self.abrir().status_code, 200)


# ---------------------------------------------------------------------------
# Quem não entra
# ---------------------------------------------------------------------------


class UnauthorizedAccessTests(CustomizationAccessBase):
    def test_an_anonymous_visitor_with_the_url_gets_nothing(self):
        """3. Visitante anônimo — era exatamente o furo."""
        self.assertEqual(self.abrir().status_code, 404)

    def test_another_customer_cannot_open_it(self):
        """4. A foto pertence ao pedido de outra pessoa."""
        self.fazer_pedido_do_dono()
        self.client.force_login(self.estranho)

        self.assertEqual(self.abrir().status_code, 404)

    def test_a_logged_in_customer_without_any_order_cannot_open_it(self):
        """2. Acesso não autorizado — ter conta não basta."""
        self.client.force_login(self.estranho)

        self.assertEqual(self.abrir().status_code, 404)

    def test_staff_without_the_permissions_cannot_open_it(self):
        """`is_staff` sozinho não é autorização."""
        equipe = make_user(username="recepcao", email="recepcao@jdprint.test", is_staff=True)
        self.client.force_login(equipe)

        self.assertEqual(self.abrir().status_code, 404)

    def test_a_stale_session_key_does_not_open_it(self):
        """A sessão de quem enviou não vale para outra sessão qualquer."""
        sessao = self.client.session
        sessao.save()
        self.assertNotEqual(sessao.session_key, "sessao-de-quem-enviou")

        self.assertEqual(self.abrir().status_code, 404)

    def test_the_refusal_never_confirms_that_the_file_exists(self):
        """404 para tudo: 403 diria "existe, mas não é seu" — e a lista é curta."""
        existente = self.abrir()
        inexistente = self.abrir(reverse("cart:customization_file", args=[10**9]))

        self.assertEqual(existente.status_code, inexistente.status_code)


class BadIdentifierTests(CustomizationAccessBase):
    def test_an_unknown_id_is_a_404(self):
        """6. Arquivo inexistente."""
        chefe = make_user(username="chefe", email="chefe@jdprint.test", is_staff=True,
                          is_superuser=True)
        self.client.force_login(chefe)

        self.assertEqual(
            self.abrir(reverse("cart:customization_file", args=[10**9])).status_code, 404
        )

    def test_a_non_numeric_id_does_not_reach_the_view(self):
        """5. ID inválido — o conversor da rota recusa antes, sem 500."""
        for lixo in ("abc", "1.5", "../../etc/passwd", "1 OR 1=1"):
            with self.subTest(lixo=lixo):
                self.assertEqual(self.abrir(f"/personalizacao/{lixo}/").status_code, 404)

    def test_a_row_without_a_file_on_disk_is_a_404(self):
        """A linha existe, o arquivo sumiu: para quem pede, é a mesma coisa."""
        chefe = make_user(username="chefe", email="chefe@jdprint.test", is_staff=True,
                          is_superuser=True)
        self.client.force_login(chefe)
        self.upload.file.delete(save=True)

        self.assertEqual(self.abrir().status_code, 404)


# ---------------------------------------------------------------------------
# O que não podia mudar
# ---------------------------------------------------------------------------


class PublicMediaStillWorksTests(LanguageResetMixin, TestCase):
    """8. As outras mídias continuam públicas."""

    def test_only_the_public_folders_are_served_as_files(self):
        """O urlconf é montado no import, então a garantia é sobre a regra.

        Em produção quem serve arquivo é o proxy da hospedagem; o ramo `DEBUG`
        do `config/urls.py` existe para o desenvolvimento bater com ela. Servir
        `MEDIA_ROOT` inteiro aqui é o que faria `customizations/` voltar a ser
        público na máquina de quem desenvolve.
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
        self.assertNotIn(
            "static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)",
            fonte,
            "media/ voltou a ser servido em bloco",
        )

    def test_no_template_or_admin_links_straight_to_the_file(self):
        """A URL crua não pode voltar por descuido em nenhum consumidor."""
        import io
        from pathlib import Path

        suspeitos = []
        for caminho in list(Path("apps").rglob("*.py")) + list(Path("templates").rglob("*.html")):
            if "__pycache__" in caminho.parts or "tests" in str(caminho):
                continue
            texto = io.open(caminho, encoding="utf-8", errors="ignore").read()
            for linha in texto.splitlines():
                if "personalization_upload" in linha and ".file.url" in linha:
                    suspeitos.append(f"{caminho}: {linha.strip()}")
                if "upload.file.url" in linha:
                    suspeitos.append(f"{caminho}: {linha.strip()}")

        self.assertEqual(suspeitos, [], f"link direto para o arquivo: {suspeitos}")
