"""Páginas institucionais e o formulário de contato — etapa 18.

Quatro páginas, um template, um model. O que estes testes guardam:

1. **nenhum texto institucional no HTML** — trocar a política de trocas é
   trabalho do Admin, não de quem edita template;
2. **a página nunca abre quebrada** — sem conteúdo, ela responde 200 com o
   título da rota, porque o rodapé a linka;
3. **o que a pessoa escreveu não se perde** — a mensagem é gravada antes de o
   e-mail sair, e o provedor fora do ar não apaga um pedido de cliente;
4. **todo conteúdo cadastrável tem endereço público** — `PageSlug` é lista
   fechada justamente para não existir página sem rota.

A migration `0005` cadastra o conteúdo inicial, então o banco de teste já
nasce com as quatro páginas. `PageBase` apaga tudo no `setUp`: quase todo
teste aqui é sobre o comportamento com um conteúdo específico (ou com
nenhum), e o texto de fábrica esconderia os dois. Quem confere o conteúdo
inicial é `SeededContentTests`, que não apaga nada.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from apps.core.models import EmailSettings
from apps.core.testing import LanguageResetMixin, make_category
from apps.storefront.models import (
    ContactMessage,
    FooterColumn,
    FooterColumnTranslation,
    FooterLink,
    FooterLinkTranslation,
    InstitutionalPage,
    InstitutionalPageTranslation,
    PageSlug,
)

ENVIOS = "/envios-e-prazos/"
TROCAS = "/trocas-e-devolucoes/"
CONTATO = "/contato/"
REVENDA = "/revenda/"
SENHA = "senha-bem-comprida"

CONTATO_OK = {
    "name": "Ana Ribeiro",
    "email": "ana@exemplo.test",
    "subject": "Dúvida sobre o prazo",
    "message": "Quanto tempo leva para produzir um vaso personalizado?",
    "website": "",
}


class PageBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        make_category(slug="modelos", name="Modelos")
        # O conteúdo de fábrica (migration 0005) sai de cena: aqui o que
        # está sendo testado é o comportamento com um conteúdo específico,
        # ou com nenhum. Ver `SeededContentTests`.
        InstitutionalPage.objects.all().delete()
        FooterColumn.objects.all().delete()

    def pagina(self, slug, **traducoes):
        page = InstitutionalPage.objects.create(
            slug=slug,
            is_active=traducoes.pop("is_active", True),
            show_in_footer=traducoes.pop("show_in_footer", True),
            sort_order=traducoes.pop("sort_order", 0),
        )
        for idioma, valores in traducoes.items():
            InstitutionalPageTranslation.objects.create(master=page, language=idioma, **valores)
        return page

    def html(self, url):
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()


# ---------------------------------------------------------------------------
# As páginas de texto
# ---------------------------------------------------------------------------


class TextPageTests(PageBase):
    def test_the_urls_answer_without_any_content(self):
        """O rodapé já linka: 404 quebraria o link em vez de explicar."""
        for url in (ENVIOS, TROCAS, CONTATO, REVENDA):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_without_content_the_route_label_is_the_title(self):
        html = self.html(ENVIOS)

        self.assertIn("Envios e prazos", html)
        self.assertEqual(html.count("<h1"), 1)
        self.assertNotIn(">None<", html)

    def test_without_content_it_says_so_and_offers_contact(self):
        html = self.html(TROCAS)

        self.assertIn("Conteúdo em preparação", html)
        self.assertIn('href="/contato/"', html)

    def test_the_registered_content_shows_up(self):
        self.pagina(
            PageSlug.SHIPPING,
            pt={
                "title": "Como enviamos",
                "intro": "Produzimos e despachamos da Bélgica.",
                "body": "Enviamos por Bpost.\n\nO prazo começa depois da produção.",
            },
        )

        html = self.html(ENVIOS)

        self.assertIn("Como enviamos", html)
        self.assertIn("Produzimos e despachamos da Bélgica.", html)
        self.assertIn("Enviamos por Bpost.", html)
        self.assertIn("O prazo começa depois da produção.", html)

    def test_no_institutional_text_is_written_in_the_template(self):
        """Trocar a política é trabalho do Admin, não de quem edita template."""
        with open("templates/storefront/page.html", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        for proibido in ("Bpost", "devolução em 14 dias", "prazo de entrega é"):
            with self.subTest(texto=proibido):
                self.assertNotIn(proibido, fonte)

    def test_an_unpublished_page_is_404(self):
        self.pagina(PageSlug.SHIPPING, is_active=False, pt={"title": "Rascunho"})

        self.assertEqual(self.client.get(ENVIOS).status_code, 404)

    def test_the_body_becomes_paragraphs_and_headings(self):
        self.pagina(
            PageSlug.RETURNS,
            pt={"body": "# Prazo\n\nVocê tem 14 dias.\n\n- Produto sem uso\n- Embalagem original"},
        )

        html = self.html(TROCAS)

        self.assertIn("<h2>Prazo</h2>", html)
        self.assertIn("<p>Você tem 14 dias.</p>", html)
        self.assertIn("<li>Produto sem uso</li>", html)

    def test_html_in_the_body_is_escaped(self):
        """O corpo é texto, não marcação: `|safe` seria um XSS armazenado."""
        self.pagina(PageSlug.RETURNS, pt={"body": "<script>alert(1)</script>"})

        html = self.html(TROCAS)

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_meta_description_falls_back_to_the_intro(self):
        self.pagina(PageSlug.SHIPPING, pt={"intro": "Prazos e transportadoras."})

        self.assertContains(self.client.get(ENVIOS), "Prazos e transportadoras.")


# ---------------------------------------------------------------------------
# Idiomas
# ---------------------------------------------------------------------------


class PageLanguageTests(PageBase):
    def test_the_four_languages_answer(self):
        self.pagina(PageSlug.SHIPPING, pt={"title": "Como enviamos"})

        for prefixo in ("", "/fr", "/nl", "/en"):
            with self.subTest(idioma=prefixo or "pt"):
                self.assertEqual(self.client.get(f"{prefixo}{ENVIOS}").status_code, 200)

    def test_the_content_follows_the_language(self):
        self.pagina(
            PageSlug.SHIPPING,
            pt={"title": "Como enviamos", "body": "Enviamos por Bpost."},
            fr={"title": "Nos livraisons", "body": "Nous expédions via Bpost."},
        )

        self.assertContains(self.client.get(f"/fr{ENVIOS}"), "Nos livraisons")
        self.assertContains(self.client.get(f"/fr{ENVIOS}"), "Nous expédions via Bpost.")

    def test_it_falls_back_to_portuguese(self):
        """O mesmo fallback do resto do conteúdo traduzível."""
        self.pagina(PageSlug.SHIPPING, pt={"title": "Como enviamos", "body": "Enviamos por Bpost."})

        self.assertContains(self.client.get(f"/nl{ENVIOS}"), "Como enviamos")
        self.assertContains(self.client.get(f"/nl{ENVIOS}"), "Enviamos por Bpost.")

    def test_the_fallback_is_per_field(self):
        """Título em francês e corpo só em português: usa os dois."""
        self.pagina(
            PageSlug.SHIPPING,
            pt={"title": "Como enviamos", "body": "Enviamos por Bpost."},
            fr={"title": "Nos livraisons", "body": ""},
        )

        html = self.html(f"/fr{ENVIOS}")

        self.assertIn("Nos livraisons", html)
        self.assertIn("Enviamos por Bpost.", html)

    def test_the_route_label_is_translated_without_content(self):
        """Sem cadastro, o título de reserva não pode sair em português no /fr."""
        resposta = self.client.get(f"/fr{CONTATO}")

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "<h1 class=\"section-title mt-2\">Contato</h1>")


# ---------------------------------------------------------------------------
# Contato
# ---------------------------------------------------------------------------


class ContactFormTests(PageBase):
    def test_the_form_is_on_the_page(self):
        html = self.html(CONTATO)

        self.assertIn('name="name"', html)
        self.assertIn('name="email"', html)
        self.assertIn('name="subject"', html)
        self.assertIn('name="message"', html)
        self.assertIn("csrfmiddlewaretoken", html)

    def test_it_works_without_any_registered_text(self):
        """Nessas páginas o formulário é o conteúdo."""
        html = self.html(CONTATO)

        self.assertNotIn("Conteúdo em preparação", html)
        self.assertIn('name="subject"', html)

    def test_a_valid_message_is_saved(self):
        resposta = self.client.post(CONTATO, CONTATO_OK, follow=True)

        self.assertEqual(resposta.status_code, 200)
        mensagem = ContactMessage.objects.get()
        self.assertEqual(mensagem.name, "Ana Ribeiro")
        self.assertEqual(mensagem.subject, "Dúvida sobre o prazo")
        self.assertFalse(mensagem.is_handled)

    def test_the_success_message_is_shown(self):
        resposta = self.client.post(CONTATO, CONTATO_OK, follow=True)

        self.assertContains(resposta, "Mensagem enviada")

    def test_it_redirects_so_a_refresh_does_not_resend(self):
        resposta = self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.url, CONTATO)

    def test_an_empty_form_is_refused(self):
        resposta = self.client.post(CONTATO, {})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)
        self.assertContains(resposta, "Informe o seu nome.")

    def test_an_invalid_email_is_refused(self):
        resposta = self.client.post(CONTATO, {**CONTATO_OK, "email": "nao-e-email"})

        self.assertContains(resposta, "Informe um e-mail válido.")
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_a_message_that_says_nothing_is_refused(self):
        resposta = self.client.post(CONTATO, {**CONTATO_OK, "message": "oi"})

        self.assertContains(resposta, "Escreva um pouco mais")
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_what_was_typed_comes_back(self):
        """Recusar não pode custar o texto que a pessoa escreveu."""
        resposta = self.client.post(CONTATO, {**CONTATO_OK, "email": "errado"})

        self.assertContains(resposta, "Dúvida sobre o prazo")

    def test_the_honeypot_stops_a_robot(self):
        resposta = self.client.post(CONTATO, {**CONTATO_OK, "website": "http://spam.test"})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_csrf_is_required(self):
        cliente = self.client_class(enforce_csrf_checks=True)

        resposta = cliente.post(CONTATO, CONTATO_OK)

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_the_language_is_recorded(self):
        self.client.post(f"/fr{CONTATO}", CONTATO_OK)

        self.assertEqual(ContactMessage.objects.get().language, "fr")

    def test_a_very_long_message_is_refused(self):
        resposta = self.client.post(CONTATO, {**CONTATO_OK, "message": "x" * 5000})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_html_in_the_fields_does_not_reach_the_page_as_markup(self):
        self.client.post(CONTATO, {**CONTATO_OK, "name": "<script>alert(1)</script>"})
        resposta = self.client.get(CONTATO)

        self.assertNotContains(resposta, "<script>alert(1)</script>")


class ContactEmailTests(PageBase):
    def setUp(self):
        super().setUp()
        mail.outbox = []

    @override_settings(ORDER_ADMIN_EMAILS=["equipe@jdprint.test"])
    def test_the_team_is_notified(self):
        self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(len(mail.outbox), 1)
        aviso = mail.outbox[0]
        self.assertEqual(aviso.to, ["equipe@jdprint.test"])
        self.assertIn("Dúvida sobre o prazo", aviso.subject)
        self.assertIn("Ana Ribeiro", aviso.body)

    @override_settings(ORDER_ADMIN_EMAILS=["equipe@jdprint.test"])
    def test_replying_goes_to_whoever_wrote(self):
        """Sem isso, responder exigiria copiar o endereço à mão."""
        self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(mail.outbox[0].reply_to, ["ana@exemplo.test"])

    @override_settings(ORDER_ADMIN_EMAILS=["padrao@jdprint.test"])
    def test_the_admin_recipient_wins(self):
        configuracao = EmailSettings.load()
        configuracao.contact_recipients = "contato@jdprint.test\nvendas@jdprint.test"
        configuracao.save()

        self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(
            mail.outbox[0].to, ["contato@jdprint.test", "vendas@jdprint.test"]
        )

    @override_settings(ORDER_ADMIN_EMAILS=["padrao@jdprint.test"])
    def test_without_a_contact_recipient_it_falls_back_to_the_order_chain(self):
        """Em branco, cai em quem recebe os pedidos — a cadeia de sempre.

        E a cadeia dos pedidos é a da etapa 10: a linha do Admin só entra em
        cena quando está **ativa e com servidor**. Aqui ela não está, então o
        `.env` manda — comportamento já validado, que esta etapa não mexe.
        """
        configuracao = EmailSettings.load()
        configuracao.admin_recipients = "pedidos@jdprint.test"
        configuracao.save()

        self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(mail.outbox[0].to, ["padrao@jdprint.test"])

    @override_settings(ORDER_ADMIN_EMAILS=["padrao@jdprint.test"])
    def test_with_the_admin_config_active_the_order_recipient_is_used(self):
        configuracao = EmailSettings.load()
        configuracao.host = "smtp.jdprint.test"
        configuracao.from_email = "loja@jdprint.test"
        configuracao.admin_recipients = "pedidos@jdprint.test"
        configuracao.is_active = True
        configuracao.save()

        self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(mail.outbox[0].to, ["pedidos@jdprint.test"])

    @override_settings(ORDER_ADMIN_EMAILS=["padrao@jdprint.test"])
    def test_the_contact_recipient_works_without_configuring_the_server(self):
        """O ponto da decisão: destinatário não é credencial.

        Quem usa o SMTP do `.env` — o caso do PythonAnywhere hoje — precisa
        poder dizer quem recebe os contatos sem recadastrar o servidor.
        """
        configuracao = EmailSettings.load()
        configuracao.contact_recipients = "contato@jdprint.test"
        configuracao.is_active = False
        configuracao.save()

        self.client.post(CONTATO, CONTATO_OK)

        self.assertEqual(mail.outbox[0].to, ["contato@jdprint.test"])

    def test_a_failed_send_does_not_lose_the_message(self):
        """Provedor fora do ar não pode apagar o pedido de um cliente."""
        from unittest.mock import patch

        with patch(
            "django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("sem rede")
        ):
            resposta = self.client.post(CONTATO, CONTATO_OK, follow=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ContactMessage.objects.count(), 1)
        self.assertContains(resposta, "Mensagem enviada")


# ---------------------------------------------------------------------------
# Revenda
# ---------------------------------------------------------------------------


class ResellerPageTests(PageBase):
    """A revenda é informativa e termina numa chamada para o contato.

    Formulário próprio, com empresa/país/tipo de negócio e caixa de entrada
    separada, já seria um sistema de revendedores — e isso não é desta etapa.
    """

    def test_it_has_no_form_of_its_own(self):
        html = self.html(REVENDA)

        for campo in ("company", "phone", "country", "city", "business_type"):
            with self.subTest(campo=campo):
                self.assertNotIn(f'name="{campo}"', html)
        # Só o miolo: o seletor de idioma do cabeçalho tem CSRF próprio.
        miolo = html.split('<main', 1)[1].split("</main>", 1)[0]
        self.assertNotIn("<form", miolo)

    def test_the_registered_text_shows_up(self):
        self.pagina(
            PageSlug.RESELLER,
            pt={"title": "Seja um revendedor", "body": "# O programa\n\nProduzimos sob encomenda."},
        )

        html = self.html(REVENDA)

        self.assertIn("<h2>O programa</h2>", html)
        self.assertIn("Produzimos sob encomenda.", html)

    def test_it_calls_the_reader_to_the_contact_page(self):
        self.pagina(PageSlug.RESELLER, pt={"body": "Fale com a gente."})

        html = self.html(REVENDA)

        self.assertIn(f'href="{CONTATO}"', html)

    def test_the_call_follows_the_language(self):
        self.pagina(PageSlug.RESELLER, pt={"body": "Fale com a gente."})

        self.assertContains(self.client.get(f"/fr{REVENDA}"), f'href="/fr{CONTATO}"')

    def test_without_text_it_still_points_to_the_contact(self):
        """Sem conteúdo cadastrado, quem oferece o caminho é o estado vazio."""
        html = self.html(REVENDA)

        self.assertIn("Conteúdo em preparação", html)
        self.assertIn(f'href="{CONTATO}"', html)

    def test_nothing_is_saved_by_posting_to_it(self):
        """Sem formulário, um POST não cria registro nenhum."""
        resposta = self.client.post(REVENDA, {"name": "Robô", "company": "Spam"})

        self.assertIn(resposta.status_code, (405, 200))
        self.assertEqual(ContactMessage.objects.count(), 0)


# ---------------------------------------------------------------------------
# Rodapé
# ---------------------------------------------------------------------------


class FooterLinkTests(PageBase):
    """O rodapé é cadastrado — e o cadastro aponta para a página, não para uma string.

    Com `i18n_patterns`, a mesma página mora em `/contato/` e `/fr/contato/`.
    Um endereço digitado à mão mandaria o visitante francês para a página
    portuguesa; a referência acompanha o idioma sozinha.
    """

    def coluna_com_paginas(self):
        coluna = FooterColumn.objects.create(internal_name="informacoes", sort_order=10)
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title="Informações")
        paginas = {}
        for ordem, slug in enumerate(PageSlug.values, start=1):
            pagina = self.pagina(slug, pt={"title": f"Página {slug}"})
            FooterLink.objects.create(column=coluna, page=pagina, sort_order=ordem)
            paginas[slug] = pagina
        return coluna, paginas

    def test_the_four_pages_are_linked(self):
        self.coluna_com_paginas()

        html = self.html("/")

        for url in (ENVIOS, TROCAS, CONTATO, REVENDA):
            with self.subTest(url=url):
                self.assertIn(f'href="{url}"', html)

    def test_the_link_text_is_the_page_title(self):
        """Sem rótulo próprio, o nome da página é escrito num lugar só."""
        self.coluna_com_paginas()

        self.assertIn("Página contato", self.html("/"))

    def test_an_own_label_still_wins(self):
        coluna, paginas = self.coluna_com_paginas()
        link = FooterLink.objects.get(page=paginas[PageSlug.CONTACT])
        FooterLinkTranslation.objects.create(master=link, language="pt", label="Fale conosco")

        html = self.html("/")

        self.assertIn("Fale conosco", html)
        self.assertIn(f'href="{CONTATO}"', html)

    def test_the_links_follow_the_language(self):
        self.coluna_com_paginas()

        for prefixo in ("", "/fr", "/nl", "/en"):
            with self.subTest(idioma=prefixo or "pt"):
                self.assertIn(f'href="{prefixo}{CONTATO}"', self.html(f"{prefixo}/"))

    def test_the_title_follows_the_language(self):
        coluna, paginas = self.coluna_com_paginas()
        InstitutionalPageTranslation.objects.create(
            master=paginas[PageSlug.CONTACT], language="fr", title="Nous écrire"
        )

        self.assertContains(self.client.get("/fr/"), "Nous écrire")

    def test_a_page_out_of_the_footer_hides_its_link(self):
        coluna, paginas = self.coluna_com_paginas()
        InstitutionalPage.objects.filter(pk=paginas[PageSlug.RESELLER].pk).update(
            show_in_footer=False
        )

        html = self.html("/")

        self.assertNotIn(f'href="{REVENDA}"', html)
        self.assertIn(f'href="{CONTATO}"', html)

    def test_an_unpublished_page_hides_its_link(self):
        """Senão o rodapé linkaria um 404 — pior que não linkar."""
        coluna, paginas = self.coluna_com_paginas()
        InstitutionalPage.objects.filter(pk=paginas[PageSlug.RETURNS].pk).update(is_active=False)

        self.assertNotIn(f'href="{TROCAS}"', self.html("/"))

    def test_the_order_is_the_registered_one(self):
        coluna = FooterColumn.objects.create(internal_name="informacoes")
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title="Informações")
        contato = self.pagina(PageSlug.CONTACT, pt={"title": "Contato"})
        envios = self.pagina(PageSlug.SHIPPING, pt={"title": "Envios"})
        FooterLink.objects.create(column=coluna, page=contato, sort_order=1)
        FooterLink.objects.create(column=coluna, page=envios, sort_order=2)

        rodape = self.html("/").split("<footer", 1)[1]

        self.assertLess(rodape.index(f'href="{CONTATO}"'), rodape.index(f'href="{ENVIOS}"'))

    def test_an_external_link_still_works(self):
        """A etapa 16 não foi desfeita: endereço digitado continua valendo."""
        coluna = FooterColumn.objects.create(internal_name="redes")
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title="Redes")
        link = FooterLink.objects.create(column=coluna, url="https://exemplo.test/jdprint")
        FooterLinkTranslation.objects.create(master=link, language="pt", label="Instagram")

        html = self.html("/")

        self.assertIn('href="https://exemplo.test/jdprint"', html)
        self.assertIn("Instagram", html)

    def test_the_two_destinations_cannot_be_used_at_once(self):
        from django.core.exceptions import ValidationError

        coluna = FooterColumn.objects.create(internal_name="informacoes")
        pagina = self.pagina(PageSlug.CONTACT, pt={"title": "Contato"})

        with self.assertRaises(ValidationError):
            FooterLink(column=coluna, page=pagina, url="/outro/").full_clean()

    def test_without_any_column_the_footer_has_no_help_block(self):
        """Apagar a coluna é decisão do administrador, e ela some mesmo."""
        html = self.html("/")

        self.assertNotIn(f'href="{ENVIOS}"', html)
        self.assertNotIn("Páginas em construção.", html)


# ---------------------------------------------------------------------------
# O conteúdo que a migration cadastra
# ---------------------------------------------------------------------------


class SeededContentTests(LanguageResetMixin, TestCase):
    """O que a instalação nova entrega, sem ninguém tocar no Admin.

    Não herda de `PageBase` de propósito: aqui o conteúdo de fábrica é o
    objeto do teste, não um estorvo.
    """

    IDIOMAS = ("pt", "fr", "nl", "en")

    def setUp(self):
        super().setUp()
        make_category(slug="modelos", name="Modelos")

    def test_the_four_pages_are_registered(self):
        self.assertEqual(
            sorted(InstitutionalPage.objects.values_list("slug", flat=True)),
            sorted(PageSlug.values),
        )

    def test_every_page_is_written_in_the_four_languages(self):
        for pagina in InstitutionalPage.objects.all():
            with self.subTest(pagina=pagina.slug):
                idiomas = set(pagina.translations.values_list("language", flat=True))
                self.assertEqual(idiomas, set(self.IDIOMAS))

    def test_every_page_has_a_title_and_a_body_in_every_language(self):
        for pagina in InstitutionalPage.objects.all():
            for traducao in pagina.translations.all():
                with self.subTest(pagina=pagina.slug, idioma=traducao.language):
                    self.assertTrue(traducao.title.strip())
                    self.assertTrue(traducao.body.strip())
                    self.assertTrue(traducao.intro.strip())

    def test_the_content_reaches_the_page_in_each_language(self):
        esperado = {
            "": "Envios e prazos",
            "/fr": "Livraisons et délais",
            "/nl": "Verzending en levertijden",
            "/en": "Shipping and lead times",
        }
        for prefixo, titulo in esperado.items():
            with self.subTest(idioma=prefixo or "pt"):
                self.assertContains(self.client.get(f"{prefixo}{ENVIOS}"), titulo)

    def test_no_page_opens_with_the_preparing_notice(self):
        for url in (ENVIOS, TROCAS, CONTATO, REVENDA):
            with self.subTest(url=url):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 200)
                self.assertNotContains(resposta, "Conteúdo em preparação")

    def test_no_invented_deadline_or_legal_text(self):
        """O pedido da etapa: nada de prazo ou regra que o projeto não definiu."""
        import re

        proibido = re.compile(
            r"\b\d+\s*(dias|jours|dagen|days|horas|heures|uur|hours)\b", re.IGNORECASE
        )
        for traducao in InstitutionalPageTranslation.objects.all():
            with self.subTest(pagina=traducao.master.slug, idioma=traducao.language):
                texto = f"{traducao.intro} {traducao.body}"
                self.assertIsNone(proibido.search(texto), texto[:120])

    def test_the_footer_column_links_the_four_pages(self):
        html = self.client.get("/").content.decode()

        for url in (ENVIOS, TROCAS, CONTATO, REVENDA):
            with self.subTest(url=url):
                self.assertIn(f'href="{url}"', html)

    def test_the_footer_column_is_translated(self):
        esperado = {"": "Informações", "/fr": "Informations", "/nl": "Informatie", "/en": "Information"}
        for prefixo, titulo in esperado.items():
            with self.subTest(idioma=prefixo or "pt"):
                self.assertContains(self.client.get(f"{prefixo}/"), titulo)

    def test_the_footer_links_have_no_hardcoded_address(self):
        """Endereço digitado quebraria o idioma; a referência não."""
        for link in FooterLink.objects.filter(column__internal_name="paginas-institucionais"):
            with self.subTest(link=link.pk):
                self.assertEqual(link.url, "")
                self.assertIsNotNone(link.page_id)

    def test_the_admin_can_rewrite_it(self):
        """O texto de fábrica é ponto de partida, não conteúdo fixo."""
        pagina = InstitutionalPage.objects.get(slug=PageSlug.SHIPPING)
        pagina.translations.filter(language="pt").update(title="Outro título")

        self.assertContains(self.client.get(ENVIOS), "Outro título")


# ---------------------------------------------------------------------------
# Rota para todo conteúdo
# ---------------------------------------------------------------------------


class RouteCoverageTests(LanguageResetMixin, TestCase):
    """Nenhuma página cadastrável pode ficar sem endereço público.

    É o motivo de `PageSlug` ser uma lista fechada: um slug livre no Admin
    criaria conteúdo escrito que ninguém consegue abrir.
    """

    def setUp(self):
        super().setUp()
        make_category(slug="modelos", name="Modelos")

    def test_every_choice_has_a_route(self):
        from django.urls import reverse

        for escolha in PageSlug:
            with self.subTest(slug=escolha.value):
                self.assertTrue(reverse(f"storefront:page_{escolha.name.lower()}"))

    def test_every_registered_page_answers_at_its_own_url(self):
        for pagina in InstitutionalPage.objects.all():
            with self.subTest(slug=pagina.slug):
                self.assertEqual(self.client.get(pagina.get_absolute_url()).status_code, 200)

    def test_the_admin_cannot_register_a_slug_without_a_route(self):
        from django.core.exceptions import ValidationError

        pagina = InstitutionalPage(slug="pagina-inventada")

        with self.assertRaises(ValidationError):
            pagina.full_clean()

    def test_the_four_routes_answer_in_the_four_languages(self):
        for prefixo in ("", "/fr", "/nl", "/en"):
            for url in (ENVIOS, TROCAS, CONTATO, REVENDA):
                with self.subTest(idioma=prefixo or "pt", url=url):
                    self.assertEqual(self.client.get(f"{prefixo}{url}").status_code, 200)


# ---------------------------------------------------------------------------
# Admin e segurança
# ---------------------------------------------------------------------------


class PageAdminTests(PageBase):
    URLS = (
        "/admin/storefront/institutionalpage/",
        "/admin/storefront/contactmessage/",
    )

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        for url in self.URLS:
            with self.subTest(url=url):
                resposta = self.client.get(url)
                self.assertEqual(resposta.status_code, 302)
                self.assertIn("/admin/login/", resposta.url)

    def test_a_customer_cannot_reach_it(self):
        User = get_user_model()
        cliente = User.objects.create_user(
            username="cliente", email="c@jdprint.test", password=SENHA
        )
        self.client.force_login(cliente)

        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_a_superuser_can_manage(self):
        User = get_user_model()
        admin = User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password=SENHA
        )
        self.client.force_login(admin)

        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_received_messages_cannot_be_created_by_hand(self):
        """A mensagem é o que a pessoa escreveu; inventar uma seria falsificar."""
        from apps.storefront.admin import ContactMessageAdmin

        self.assertFalse(ContactMessageAdmin.has_add_permission(None, None))

    def test_nothing_administrative_leaks_into_the_public_page(self):
        self.pagina(PageSlug.SHIPPING, pt={"title": "Como enviamos"})
        self.client.post(CONTATO, CONTATO_OK)

        html = self.html(ENVIOS)

        for proibido in ("is_handled", "contact_recipients", "admin_recipients",
                         "ana@exemplo.test"):
            with self.subTest(termo=proibido):
                self.assertNotIn(proibido, html)

    def test_what_was_received_never_shows_on_a_public_page(self):
        self.client.post(CONTATO, CONTATO_OK)

        for url in (REVENDA, ENVIOS, TROCAS, CONTATO):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "Dúvida sobre o prazo")
