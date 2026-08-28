"""Faixa do topo e rodapé administráveis — etapa 16.

O que estes testes guardam não é "o campo existe": é que **a loja não quebra**
com o conteúdo pela metade. Um administrador configura por partes, e cada
estado intermediário tem de ser uma página apresentável — nunca um `None` na
tela, uma caixa vazia ou um botão sem texto.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.testing import LanguageResetMixin, make_category
from apps.storefront.models import (
    FooterColumn,
    FooterColumnTranslation,
    FooterLink,
    FooterLinkTranslation,
    FooterSettings,
    FooterSettingsTranslation,
    TopBarItem,
    TopBarItemTranslation,
)

HOME = "/"


class StorefrontBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        make_category(slug="modelos", name="Modelos")

    def html(self, url=HOME):
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()

    def topbar(self, **traducoes):
        item = TopBarItem.objects.create(
            internal_name=traducoes.pop("internal_name", "Item"),
            icon=traducoes.pop("icon", ""),
            sort_order=traducoes.pop("sort_order", 0),
            is_active=traducoes.pop("is_active", True),
        )
        for idioma, texto in traducoes.items():
            TopBarItemTranslation.objects.create(master=item, language=idioma, text=texto)
        return item


# ---------------------------------------------------------------------------
# Faixa do topo
# ---------------------------------------------------------------------------


class TopBarTests(StorefrontBase):
    def test_a_registered_item_shows_up(self):
        self.topbar(pt="Frete grátis acima de 50 €")

        self.assertIn("Frete grátis acima de 50 €", self.html())

    def test_the_old_hardcoded_text_is_gone(self):
        """Se sobrasse no HTML, mudar no Admin não mudaria nada na tela."""
        with open("templates/components/header.html", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        self.assertNotIn('{% translate "Produção sob encomenda" %}', fonte)

    def test_an_inactive_item_does_not_show_up(self):
        self.topbar(internal_name="Ativo", pt="Aparece")
        self.topbar(internal_name="Inativo", is_active=False, pt="Não aparece")

        html = self.html()

        self.assertIn("Aparece", html)
        self.assertNotIn("Não aparece", html)

    def test_the_order_is_respected(self):
        self.topbar(internal_name="B", sort_order=2, pt="Segundo")
        self.topbar(internal_name="A", sort_order=1, pt="Primeiro")

        html = self.html()

        self.assertLess(html.index("Primeiro"), html.index("Segundo"))

    def test_without_items_the_whole_bar_disappears(self):
        """Sem item cadastrado, a página abre direto no cabeçalho.

        Pelo gancho `data-top-bar`: a classe do fundo escuro é a mesma do
        rodapé e da chamada final da Home, e contá-la mediria outra coisa.

        A lista do hero é outra história — ela cai no texto padrão, para a Home
        não abrir sem promessa nenhuma (ver o teste seguinte).
        """
        self.assertNotIn("data-top-bar", self.html())

    def test_without_items_the_hero_keeps_the_default_promises(self):
        """A Home não pode abrir vazia só porque ninguém cadastrou ainda."""
        hero = self.html().split("<section", 2)[1]

        self.assertIn("Produção sob encomenda", hero)

    def test_registered_items_replace_the_hero_list_too(self):
        """Eram duas cópias no HTML: mudar uma deixava a outra desatualizada."""
        self.topbar(internal_name="Ateliê", icon="palette", pt="Feito no nosso ateliê")

        html = self.html()

        self.assertIn("Feito no nosso ateliê", html)
        self.assertNotIn("Produção sob encomenda", html)

    def test_the_hero_icon_comes_from_the_item(self):
        self.topbar(internal_name="Envio", icon="truck", pt="Enviamos para a Europa")

        html = self.html()
        hero = html.split("<section", 2)[1]

        self.assertIn("Enviamos para a Europa", hero)
        self.assertIn("text-brand-300", hero)

    def test_an_item_without_icon_still_shows_its_text(self):
        self.topbar(internal_name="Sem ícone", pt="Promessa sem ícone")

        self.assertIn("Promessa sem ícone", self.html())

    def test_with_items_the_bar_is_there(self):
        self.topbar(pt="Produção sob encomenda")

        self.assertIn("data-top-bar", self.html())

    def test_an_item_without_translation_is_not_drawn(self):
        """Um `<li>` vazio seria um ponto separador solto na faixa."""
        self.topbar(internal_name="Sem texto")

        html = self.html()

        self.assertNotIn("None", html)
        self.assertNotIn("<li></li>", html.replace(" ", "").replace("\n", ""))

    def test_it_is_translated(self):
        self.topbar(pt="Envio para a Europa", fr="Livraison en Europe")

        self.assertIn("Livraison en Europe", self.html("/fr/"))

    def test_it_falls_back_to_portuguese(self):
        self.topbar(pt="Envio para a Europa")

        self.assertIn("Envio para a Europa", self.html("/nl/"))

    def test_more_than_three_items_are_allowed(self):
        """Nada limita a faixa a três — era só o HTML que tinha três."""
        for numero in range(5):
            self.topbar(internal_name=f"Item {numero}", sort_order=numero, pt=f"Promessa {numero}")

        html = self.html()

        for numero in range(5):
            with self.subTest(numero=numero):
                self.assertIn(f"Promessa {numero}", html)


# ---------------------------------------------------------------------------
# Rodapé
# ---------------------------------------------------------------------------


class FooterSettingsTests(StorefrontBase):
    def configurar(self, **campos):
        traducoes = {
            idioma: campos.pop(idioma)
            for idioma in ("pt", "fr", "nl", "en")
            if idioma in campos
        }
        rodape = FooterSettings.load()
        for campo, valor in campos.items():
            setattr(rodape, campo, valor)
        rodape.save()
        for idioma, valores in traducoes.items():
            FooterSettingsTranslation.objects.update_or_create(
                master=rodape, language=idioma, defaults=valores
            )
        rodape.refresh_translations()
        return rodape

    def rodape(self, url=HOME):
        """Só o `<footer>`.

        A frase institucional também vive na `meta description` da Home, e
        medir a página inteira daria falso positivo.
        """
        return self.html(url).split("<footer", 1)[1]

    def test_the_default_text_is_used_without_configuration(self):
        """Uma instalação recém-migrada não pode abrir com o rodapé vazio."""
        rodape = self.rodape()

        self.assertIn("Produtos criativos feitos com impressão 3D", rodape)
        self.assertIn("Compra segura", rodape)

    def test_the_about_text_can_be_replaced(self):
        self.configurar(pt={"about_text": "Somos uma oficina em Bruxelas."})

        rodape = self.rodape()

        self.assertIn("Somos uma oficina em Bruxelas.", rodape)
        self.assertNotIn("Produtos criativos feitos com impressão 3D", rodape)

    def test_the_copyright_can_be_replaced(self):
        self.configurar(pt={"copyright_text": "© JD PRINT BV — Bruxelas"})

        self.assertIn("© JD PRINT BV — Bruxelas", self.html())

    def test_the_badge_can_be_replaced(self):
        self.configurar(pt={"badge_text": "Pagamento protegido"})

        html = self.html()

        self.assertIn("Pagamento protegido", html)
        self.assertNotIn("Compra segura", html)

    def test_the_categories_column_title_can_be_replaced(self):
        self.configurar(pt={"categories_title": "Nossas linhas"})

        self.assertIn("Nossas linhas", self.html())

    def test_the_category_list_itself_stays_automatic(self):
        """Trocar por lista manual seria trocar algo que se atualiza sozinho."""
        make_category(slug="filamentos", name="Filamentos")

        self.assertIn("Filamentos", self.html())

    def test_contact_appears_only_when_filled(self):
        html = self.html()
        self.assertNotIn("mailto:", html)

        self.configurar(contact_email="ola@jdprint.test", pt={"contact_title": "Fale conosco"})

        html = self.html()
        self.assertIn("Fale conosco", html)
        self.assertIn("mailto:ola@jdprint.test", html)

    def test_a_phone_without_email_still_works(self):
        self.configurar(contact_phone="+32 470 00 00 00")

        html = self.html()

        self.assertIn("+32 470 00 00 00", html)
        self.assertNotIn("mailto:", html)

    def test_deactivating_returns_to_the_default(self):
        self.configurar(pt={"about_text": "Texto do Admin"})
        self.assertIn("Texto do Admin", self.rodape())

        configuracao = FooterSettings.load()
        configuracao.is_active = False
        configuracao.save()

        rodape = self.rodape()
        self.assertNotIn("Texto do Admin", rodape)
        self.assertIn("Produtos criativos feitos com impressão 3D", rodape)

    def test_it_is_translated_with_fallback(self):
        self.configurar(
            pt={"about_text": "Oficina em Bruxelas."},
            fr={"about_text": "Atelier à Bruxelles."},
        )

        self.assertIn("Atelier à Bruxelles.", self.rodape("/fr/"))
        self.assertIn("Oficina em Bruxelas.", self.rodape("/nl/"))

    def test_load_always_returns_the_same_row(self):
        """O invariante é `load()`, e ele é idempotente."""
        primeiro = FooterSettings.load()
        segundo = FooterSettings.load()

        self.assertEqual(primeiro.pk, 1)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(FooterSettings.objects.count(), 1)

    def test_a_second_row_cannot_be_created(self):
        """A garantia fica na tabela, não numa convenção que alguém contorna.

        Como em `EmailSettings`: `save()` fixa `pk=1`, então um segundo
        `create()` esbarra no banco em vez de gerar uma segunda configuração
        silenciosa. O erro é o comportamento desejado.
        """
        from django.db import IntegrityError, transaction

        FooterSettings.load()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FooterSettings.objects.create(contact_phone="123")

        self.assertEqual(FooterSettings.objects.count(), 1)


class FooterColumnTests(StorefrontBase):
    def setUp(self):
        super().setUp()
        # A coluna de informações vem da migration de conteúdo (etapa 18).
        # Aqui o objeto do teste é a coluna que cada caso cadastra.
        FooterColumn.objects.all().delete()

    def coluna(self, nome="Ajuda", titulo="Ajuda", **campos):
        coluna = FooterColumn.objects.create(internal_name=nome, **campos)
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title=titulo)
        return coluna

    def link(self, coluna, rotulo="Contato", url="", **campos):
        link = FooterLink.objects.create(column=coluna, url=url, **campos)
        FooterLinkTranslation.objects.create(master=link, language="pt", label=rotulo)
        return link

    def test_without_any_column_there_is_no_help_block(self):
        """Mudou na etapa 18.

        Até a etapa 17 o rodapé tinha três **textos** sem link e um aviso de
        "em construção": as páginas não existiam. Agora existem, e quem as
        linka é uma coluna cadastrada — instalada pela migration de conteúdo.

        Sem coluna nenhuma o bloco simplesmente não aparece: apagar a coluna
        é decisão do administrador, e o rodapé não pode inventar uma no lugar.
        """
        html = self.html()

        self.assertNotIn('href="/envios-e-prazos/"', html)
        self.assertNotIn("Páginas em construção.", html)

    def test_a_registered_column_is_what_shows(self):
        coluna = self.coluna(titulo="Atendimento")
        self.link(coluna, rotulo="Fale conosco", url="/contato/")

        html = self.html()

        self.assertIn("Atendimento", html)
        self.assertIn("Fale conosco", html)
        self.assertNotIn("Páginas em construção.", html)

    def test_a_link_without_url_is_plain_text(self):
        """Link para uma página que dá 404 é pior que texto sem link."""
        coluna = self.coluna()
        self.link(coluna, rotulo="Trocas e devoluções")

        html = self.html()

        self.assertIn("Trocas e devoluções", html)
        self.assertNotIn('href="">', html)
        self.assertNotIn('href="None"', html)

    def test_a_column_without_links_is_not_drawn(self):
        """Uma coluna com título e nada embaixo é uma caixa vazia."""
        self.coluna(titulo="Coluna vazia")

        self.assertNotIn("Coluna vazia", self.html())

    def test_an_inactive_link_disappears(self):
        coluna = self.coluna()
        self.link(coluna, rotulo="Visível")
        self.link(coluna, rotulo="Escondido", is_active=False)

        html = self.html()

        self.assertIn("Visível", html)
        self.assertNotIn("Escondido", html)

    def test_an_inactive_column_disappears(self):
        coluna = self.coluna(titulo="Fora", is_active=False)
        self.link(coluna, rotulo="Também fora")

        html = self.html()

        self.assertNotIn("Fora", html)
        self.assertNotIn("Também fora", html)

    def test_the_link_order_is_respected(self):
        coluna = self.coluna()
        self.link(coluna, rotulo="Segundo", sort_order=2)
        self.link(coluna, rotulo="Primeiro", sort_order=1)

        html = self.html()

        self.assertLess(html.index("Primeiro"), html.index("Segundo"))

    def test_links_are_translated(self):
        coluna = self.coluna()
        link = self.link(coluna, rotulo="Contato", url="/contato/")
        FooterLinkTranslation.objects.create(master=link, language="fr", label="Nous contacter")

        self.assertIn("Nous contacter", self.html("/fr/"))

    def test_a_dangerous_url_is_refused(self):
        """O rodapé não é uma porta para `javascript:`."""
        coluna = self.coluna()
        link = FooterLink(column=coluna, url="javascript:alert(1)")

        with self.assertRaises(ValidationError) as erro:
            link.full_clean()
        self.assertIn("url", erro.exception.error_dict)

    def test_mailto_and_tel_are_allowed(self):
        coluna = self.coluna()

        for url in ("mailto:ola@jdprint.test", "tel:+32470000000", "/pagina/", "https://x.test"):
            with self.subTest(url=url):
                link = FooterLink(column=coluna, url=url)
                link.full_clean()  # não levanta


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------


class StorefrontPermissionTests(StorefrontBase):
    """12 — só quem tem acesso ao Admin edita isto."""

    URLS = (
        "/admin/storefront/topbaritem/",
        "/admin/storefront/footercolumn/",
        "/admin/storefront/footerlink/",
        "/admin/home/homecard/",
    )

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        for url in self.URLS:
            with self.subTest(url=url):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 302)
                self.assertIn("/admin/login/", resposta.url)

    def test_a_customer_cannot_reach_the_admin(self):
        User = get_user_model()
        cliente = User.objects.create_user(
            username="cliente", email="cliente@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(cliente)

        for url in self.URLS:
            with self.subTest(url=url):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 302)
                self.assertIn("/admin/login/", resposta.url)

    def test_a_staff_user_without_the_permission_gets_403(self):
        User = get_user_model()
        staff = User.objects.create_user(
            username="estagiario",
            email="estagiario@jdprint.test",
            password="senha-bem-comprida",
            is_staff=True,
        )
        self.client.force_login(staff)

        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_superuser_can_edit(self):
        User = get_user_model()
        admin = User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(admin)

        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_no_public_endpoint_was_created(self):
        """O conteúdo é lido pelo template, não por uma API aberta."""
        for url in ("/storefront/", "/api/storefront/", "/topbar/", "/footer/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_internal_names_never_reach_the_public_page(self):
        """`internal_name` é identificação administrativa, não conteúdo."""
        self.topbar(internal_name="NOME-INTERNO-TOPO", pt="Texto público")
        coluna = FooterColumn.objects.create(internal_name="NOME-INTERNO-COLUNA")
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title="Ajuda")
        link = FooterLink.objects.create(column=coluna)
        FooterLinkTranslation.objects.create(master=link, language="pt", label="Contato")

        html = self.html()

        self.assertIn("Texto público", html)
        self.assertNotIn("NOME-INTERNO-TOPO", html)
        self.assertNotIn("NOME-INTERNO-COLUNA", html)
