"""Os avisos da loja — CONFIGURAÇÕES DA LOJA › Comunicação › Avisos.

O que estes testes guardam:

1. **conteúdo**: título opcional, mensagem, link opcional com texto opcional
   («Saiba mais» quando falta), no idioma de quem lê, com a linha inteira de
   um idioma só e o português como reserva — e nunca uma faixa vazia;
2. **posições fechadas**: topo antes do cabeçalho, abaixo do banner depois do
   banner na Home (e abrindo o conteúdo nas outras páginas), após o conteúdo
   antes do rodapé, e o card do canto; mais de um aviso segue a ordem;
3. **onde exibir**: as áreas vêm das rotas da loja, e a página especial é só
   mais uma área;
4. **fechar**: o X existe só quando permitido; o fechamento vale para aquele
   aviso naquela versão — outro aviso não some, e o mesmo aviso reescrito
   volta; um cookie adulterado é ignorado;
5. **independência**: nenhum vínculo com a página especial ou com o frete; o
   aviso funciona sem página especial, durante e depois do lançamento;
6. **segurança**: nada do Admin vira HTML, e link com esquema perigoso não sai;
7. **Admin**: em Comunicação › Avisos, com qualquer idioma do conteúdo,
   português obrigatório, e as regras explicadas na própria tela.
"""

from datetime import date, time

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from apps.core.constants import Language
from apps.core.models import SiteLanguage
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_user
from apps.core.tests_admin_sections import linhas_da_secao
from apps.storefront.models import (
    NoticePage,
    NoticePosition,
    SpecialPage,
    SpecialPageKind,
    StoreNotice,
    StoreNoticeTranslation,
    safe_notice_link,
)
from apps.storefront.notices import COOKIE_NAME, page_for
from apps.storefront.tests.test_special_page import make_page

HOME = "/"


def aviso(message="Frete grátis acima de €50.", *, pages=("home",), position=NoticePosition.BELOW_BANNER,
          dismissible=True, link_url="", sort_order=0, is_active=True, title="", link_label="", **idiomas):
    notice = StoreNotice.objects.create(
        pages=list(pages), position=position, dismissible=dismissible, link_url=link_url,
        sort_order=sort_order, is_active=is_active,
    )
    StoreNoticeTranslation.objects.create(
        master=notice, language="pt", title=title, message=message, link_label=link_label
    )
    for idioma, campos in idiomas.items():
        StoreNoticeTranslation.objects.create(master=notice, language=idioma, **campos)
    return notice


def fechado(*avisos):
    """O valor do cookie de quem fechou estes avisos, na versão de agora."""
    return ".".join(f"{n.pk}-{StoreNotice.objects.get(pk=n.pk).version}" for n in avisos)


class NoticeBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(sku="AV-01", name="Vaso", category=self.category)

    def html(self, url=HOME):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()


# ---------------------------------------------------------------------------
# Conteúdo
# ---------------------------------------------------------------------------


class NoticeContentTests(NoticeBase):
    def test_title_message_and_link_with_its_text(self):
        aviso("Pedidos até dia 20 chegam antes do Natal.", title="Fim de ano",
              link_url="/envios-e-prazos/", link_label="Ver prazos")

        html = self.html()

        self.assertIn('class="notice-title" id="aviso-', html)
        self.assertIn("Fim de ano", html)
        self.assertIn("Pedidos até dia 20 chegam antes do Natal.", html)
        self.assertIn('class="notice-link focus-ring" href="/envios-e-prazos/"', html)
        self.assertIn("Ver prazos", html)

    def test_link_without_text_says_learn_more(self):
        aviso(link_url="https://exemplo.test/promo")

        html = self.html()

        self.assertIn('href="https://exemplo.test/promo"', html)
        self.assertIn("Saiba mais", html)

    def test_without_link_there_is_no_link_even_with_a_text(self):
        aviso(link_label="Ver mais")

        html = self.html()

        self.assertIn("Frete grátis acima de €50.", html)
        self.assertNotIn("notice-link", html)
        self.assertNotIn("Ver mais", html)

    def test_the_visitor_language_with_portuguese_as_the_fallback(self):
        aviso(fr={"message": "Livraison gratuite dès 50 €."}, en={"message": "Free shipping over €50."})

        self.assertIn("Livraison gratuite dès 50 €.", self.html("/fr/"))
        self.assertIn("Free shipping over €50.", self.html("/en/"))
        self.assertIn("Frete grátis acima de €50.", self.html("/nl/"))  # sem holandês

    def test_one_language_per_notice_never_a_mix(self):
        """O francês sem título não pega o título português emprestado."""
        aviso("Mensagem", title="Título em português", fr={"message": "Message en français"})

        html = self.html("/fr/")

        self.assertIn("Message en français", html)
        self.assertNotIn("Título em português", html)

    def test_never_an_empty_notice(self):
        StoreNotice.objects.create(pages=["home"])  # sem nenhuma tradução

        self.assertNotIn('class="notice"', self.html())

    def test_inactive_is_not_shown(self):
        aviso(is_active=False)

        self.assertNotIn("Frete grátis acima de €50.", self.html())

    def test_admin_text_never_becomes_html(self):
        aviso('<script>alert("x")</script>', title="<b>t</b>", link_url="/x/", link_label="<i>l</i>")

        html = self.html()

        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;b&gt;t&lt;/b&gt;", html)
        self.assertIn("&lt;i&gt;l&lt;/i&gt;", html)

    def test_a_dangerous_link_is_refused_and_never_rendered(self):
        for perigoso in ("javascript:alert(1)", "java\nscript:alert(1)", "data:text/html,x", "//outro.test/x"):
            with self.subTest(link=perigoso):
                self.assertEqual(safe_notice_link(perigoso), "")
                with self.assertRaises(ValidationError) as ctx:
                    StoreNotice(pages=["home"], link_url=perigoso).full_clean()
                self.assertIn("link_url", ctx.exception.error_dict)

        # Gravado por fora da validação (shell, importação): mesmo assim não sai.
        aviso(link_url="javascript:alert(1)")
        html = self.html()
        self.assertNotIn("javascript:", html)
        self.assertNotIn("notice-link", html)

    def test_safe_links(self):
        for bom in ("/modelos/", "https://exemplo.test", "http://exemplo.test", "mailto:a@b.test", "tel:+32470000000"):
            with self.subTest(link=bom):
                self.assertEqual(safe_notice_link(bom), bom)


# ---------------------------------------------------------------------------
# Posições e ordem
# ---------------------------------------------------------------------------


class NoticePositionTests(NoticeBase):
    def test_below_the_banner_on_the_home(self):
        aviso("Abaixo do banner")

        html = self.html()

        self.assertIn("notices-below_banner", html)
        banner = html.index('aria-labelledby="hero-titulo"')
        faixa = html.index("Abaixo do banner")
        self.assertLess(banner, faixa)
        self.assertEqual(html.count("notices-below_banner"), 1)  # não sai duas vezes

    def test_below_the_banner_opens_the_content_where_there_is_no_banner(self):
        aviso("No topo do conteúdo", pages=["catalog"])

        html = self.html("/modelos/")

        main = html.index('<main id="conteudo"')
        faixa = html.index("No topo do conteúdo")
        conteudo = html.index("</main>")
        self.assertLess(main, faixa)
        self.assertLess(faixa, conteudo)

    def test_top_is_before_the_header(self):
        aviso("No topo", position=NoticePosition.TOP)

        html = self.html()

        self.assertIn("notices-top", html)
        self.assertLess(html.index("No topo"), html.index("<header"))

    def test_after_content_is_between_the_content_and_the_footer(self):
        aviso("Depois do conteúdo", position=NoticePosition.AFTER_CONTENT)

        html = self.html()

        self.assertIn("notices-after_content", html)
        self.assertLess(html.index("home_composition") if "home_composition" in html else html.index("callout"),
                        html.index("Depois do conteúdo"))
        self.assertLess(html.index("Depois do conteúdo"), html.index("</main>"))
        self.assertLess(html.index("</main>"), html.index("<footer"))

    def test_corner_is_a_floating_card(self):
        aviso("No canto", position=NoticePosition.CORNER)

        html = self.html()

        self.assertIn('class="notices notices-corner" data-notice-stack', html)
        self.assertLess(html.index("<footer"), html.index("No canto"))

    def test_multiple_notices_follow_the_order(self):
        aviso("Terceiro", sort_order=30)
        aviso("Primeiro", sort_order=10)
        aviso("Segundo", sort_order=20)
        aviso("Outra posição", sort_order=0, position=NoticePosition.TOP)

        html = self.html()

        self.assertLess(html.index("Primeiro"), html.index("Segundo"))
        self.assertLess(html.index("Segundo"), html.index("Terceiro"))
        self.assertEqual(html.count('class="notices notices-below_banner"'), 1)  # um grupo, três faixas

    def test_corner_keeps_the_order_and_renders_one_group(self):
        """No canto, a ordem decide qual card aparece: o CSS mostra só o primeiro."""
        aviso("Canto B", position=NoticePosition.CORNER, sort_order=2)
        aviso("Canto A", position=NoticePosition.CORNER, sort_order=1)

        html = self.html()

        self.assertEqual(html.count("notices-corner"), 1)
        self.assertLess(html.index("Canto A"), html.index("Canto B"))

    def test_corner_must_allow_closing(self):
        with self.assertRaises(ValidationError) as ctx:
            StoreNotice(pages=["home"], position=NoticePosition.CORNER, dismissible=False).full_clean()

        self.assertIn("dismissible", ctx.exception.error_dict)


# ---------------------------------------------------------------------------
# Onde exibir
# ---------------------------------------------------------------------------


class NoticePageTests(NoticeBase):
    def test_each_area_comes_from_the_routes(self):
        esperado = {
            "/": NoticePage.HOME,
            "/modelos/": NoticePage.CATALOG,
            f"/categorias/{self.category.slug}/": NoticePage.CATALOG,
            "/buscar/": NoticePage.CATALOG,
            f"/produtos/{self.product.slug}/": NoticePage.PRODUCT,
            "/carrinho/": NoticePage.CART,
            "/carrinho/finalizar/": NoticePage.CHECKOUT,
            "/conta/entrar/": NoticePage.ACCOUNT,
            "/conta/pedidos/": NoticePage.ACCOUNT,
            "/favoritos/": NoticePage.ACCOUNT,
            "/contato/": NoticePage.INSTITUTIONAL,
            "/envios-e-prazos/": NoticePage.INSTITUTIONAL,
        }
        for url, area in esperado.items():
            with self.subTest(url=url):
                request = self.client.get(url).wsgi_request
                request.resolver_match = resolve(url)
                self.assertEqual(page_for(request), area.value)

    def test_shows_only_where_chosen(self):
        aviso("Só no catálogo e no produto", pages=["catalog", "product"])

        self.assertNotIn("Só no catálogo", self.html())
        self.assertIn("Só no catálogo", self.html("/modelos/"))
        self.assertIn("Só no catálogo", self.html(f"/produtos/{self.product.slug}/"))
        self.assertNotIn("Só no catálogo", self.html("/contato/"))

    def test_every_page_at_once(self):
        aviso("Em todas", pages=NoticePage.values)

        for url in ("/", "/modelos/", f"/produtos/{self.product.slug}/", "/carrinho/", "/contato/", "/conta/entrar/"):
            with self.subTest(url=url):
                self.assertIn("Em todas", self.html(url))

    def test_never_in_the_admin(self):
        aviso("Aviso de todas as áreas", pages=NoticePage.values)
        self.client.force_login(make_user("chefe", is_staff=True, is_superuser=True))

        html = self.client.get(reverse("admin:index")).content.decode()

        self.assertNotIn("Aviso de todas as áreas", html)
        self.assertNotIn("data-notice=", html)

    def test_pages_must_be_chosen_and_known(self):
        for paginas in ([], ["home", "marte"], "home"):
            with self.subTest(pages=paginas):
                with self.assertRaises(ValidationError) as ctx:
                    StoreNotice(pages=paginas).full_clean()
                self.assertIn("pages", ctx.exception.error_dict)

    def test_a_new_notice_starts_on_the_home_below_the_banner(self):
        novo = StoreNotice()

        self.assertEqual(novo.pages, ["home"])
        self.assertEqual(novo.position, NoticePosition.BELOW_BANNER)
        self.assertTrue(novo.dismissible)


# ---------------------------------------------------------------------------
# Fechar
# ---------------------------------------------------------------------------


class NoticeDismissTests(NoticeBase):
    def test_close_button_only_when_allowed(self):
        aviso("Pode fechar")
        aviso("Não pode fechar", dismissible=False, sort_order=1)

        html = self.html()

        self.assertEqual(html.count("data-notice-close"), 1)
        self.assertIn('<button type="button" class="notice-close focus-ring" data-notice-close aria-label="Fechar aviso">', html)

    def test_close_button_is_named_in_the_visitor_language(self):
        aviso(fr={"message": "Livraison gratuite."})

        self.assertIn('aria-label="Fermer l’annonce"', self.html("/fr/"))

    def test_a_closed_notice_stays_closed_on_every_page(self):
        fechar = aviso("Fechado", pages=["home", "catalog"])
        self.client.cookies[COOKIE_NAME] = fechado(fechar)

        self.assertNotIn("Fechado", self.html())
        self.assertNotIn("Fechado", self.html("/modelos/"))

    def test_closing_one_notice_never_hides_another(self):
        fechar = aviso("Fechado")
        aviso("Outro aviso", sort_order=1)
        self.client.cookies[COOKIE_NAME] = fechado(fechar)

        html = self.html()

        self.assertNotIn("Fechado", html)
        self.assertIn("Outro aviso", html)

    def test_a_rewritten_notice_comes_back(self):
        fechar = aviso("Versão um")
        self.client.cookies[COOKIE_NAME] = fechado(fechar)
        self.assertNotIn("Versão um", self.html())

        StoreNoticeTranslation.objects.filter(master=fechar).update(message="Versão dois")

        self.assertIn("Versão dois", self.html())

    def test_new_link_or_position_is_a_new_version_but_order_and_state_are_not(self):
        notice = aviso("Mensagem")
        original = StoreNotice.objects.get(pk=notice.pk).version

        for campo, valor, muda in (
            ("sort_order", 9, False),
            ("pages", ["home", "catalog"], False),
            ("is_active", True, False),
            ("link_url", "/modelos/", True),
            ("position", NoticePosition.TOP, True),
        ):
            with self.subTest(campo=campo):
                StoreNotice.objects.filter(pk=notice.pk).update(**{campo: valor})
                agora = StoreNotice.objects.get(pk=notice.pk).version
                (self.assertNotEqual if muda else self.assertEqual)(agora, original)
                StoreNotice.objects.filter(pk=notice.pk).update(
                    sort_order=0, pages=["home"], link_url="", position=NoticePosition.BELOW_BANNER
                )

    def test_a_notice_that_cannot_be_closed_ignores_the_cookie(self):
        fixo = aviso("Sempre visível", dismissible=False)
        self.client.cookies[COOKIE_NAME] = fechado(fixo)

        self.assertIn("Sempre visível", self.html())

    def test_a_tampered_cookie_is_ignored(self):
        aviso("Continua aqui")
        for lixo in ("<script>", "1-zzzzzzzzzz", "abc", "9" * 5000, "1-abc.2-def", "-.-.-."):
            with self.subTest(cookie=lixo[:20]):
                self.client.cookies[COOKIE_NAME] = lixo
                self.assertIn("Continua aqui", self.html())


# ---------------------------------------------------------------------------
# Independência
# ---------------------------------------------------------------------------


class NoticeIndependenceTests(NoticeBase):
    def test_no_relation_with_the_special_page(self):
        for campo in StoreNotice._meta.get_fields():
            self.assertNotEqual(getattr(campo, "related_model", None), SpecialPage, campo.name)
        for campo in SpecialPage._meta.get_fields():
            self.assertNotIn(getattr(campo, "related_model", None), (StoreNotice, StoreNoticeTranslation), campo.name)

    def test_works_without_any_special_page(self):
        aviso("Sem página especial")

        self.assertFalse(SpecialPage.objects.exists())
        self.assertIn("Sem página especial", self.html())

    def test_during_a_launch_only_where_the_special_page_is_chosen(self):
        make_page(kind=SpecialPageKind.LAUNCH, is_active=True, launch_date=date(2099, 1, 1), launch_time=time(10, 0))
        aviso("Na página especial", pages=["special"], position=NoticePosition.TOP)
        aviso("Só na Home", pages=["home"], sort_order=1)

        html = self.html()

        self.assertIn("sp-poster", html)
        self.assertIn("Na página especial", html)
        self.assertNotIn("Só na Home", html)
        self.assertLess(html.index("Na página especial"), html.index('<header class="sp-header"'))
        self.assertIn("js/notices.js", html)

    def test_after_the_launch_the_store_notices_are_back(self):
        make_page(kind=SpecialPageKind.LAUNCH, is_active=True, launch_date=date(2020, 1, 1), launch_time=time(10, 0))
        aviso("Depois do lançamento")

        html = self.html()

        self.assertNotIn("sp-poster", html)
        self.assertIn("Depois do lançamento", html)

    def test_showing_notices_reads_no_shipping_rule(self):
        aviso("Frete grátis acima de €50.", link_url="/envios-e-prazos/")

        with CaptureQueriesContext(connection) as consultas:
            html = self.html()

        self.assertIn("Frete grátis acima de €50.", html)
        sql = " ".join(q["sql"].lower() for q in consultas.captured_queries)
        for tabela in ("shipping", "deliverycountry"):
            self.assertNotIn(tabela, sql)

    def test_more_notices_do_not_add_queries(self):
        aviso("Um")
        with CaptureQueriesContext(connection) as um:
            self.html()
        for numero in range(5):
            aviso(f"Mais {numero}", sort_order=numero + 1, position=NoticePosition.AFTER_CONTENT,
                  fr={"message": f"Plus {numero}"})
        with CaptureQueriesContext(connection) as seis:
            self.html()

        self.assertEqual(len(um.captured_queries), len(seis.captured_queries))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def dados(linhas, *, initial=0, pages=("home",), position="below_banner", dismissible=True, **extra):
    corpo = {
        "pages": list(pages),
        "position": position,
        "sort_order": "0",
        "link_url": "",
        "is_active": "on",
        "translations-TOTAL_FORMS": str(len(linhas)),
        "translations-INITIAL_FORMS": str(initial),
        "translations-MIN_NUM_FORMS": "0",
        "translations-MAX_NUM_FORMS": "1000",
        "_save": "Salvar",
        **extra,
    }
    if dismissible:
        corpo["dismissible"] = "on"
    for indice, linha in enumerate(linhas):
        for campo, valor in linha.items():
            corpo[f"translations-{indice}-{campo}"] = valor
    return corpo


class NoticeAdminTests(NoticeBase):
    def setUp(self):
        super().setUp()
        self.chefe = make_user("chefe", is_staff=True, is_superuser=True)
        self.client.force_login(self.chefe)
        self.add = reverse("admin:storefront_storenotice_add")
        SiteLanguage.objects.update_or_create(code="de", defaults={"is_active": False})

    def test_the_menu_puts_it_in_communication(self):
        linhas = linhas_da_secao(self.client.get(reverse("admin:index")), "CONFIGURAÇÕES DA LOJA")
        inicio = linhas.index("[Comunicação]")

        self.assertEqual(linhas[inicio:inicio + 3], ["[Comunicação]", "Configuração de e-mail", "Avisos"])

    def test_the_form_explains_each_option(self):
        html = self.client.get(self.add).content.decode()

        for texto in ("EXIBIÇÃO", "ONDE EXIBIR", "LINK", "Permitir fechar", "Posição", "Onde exibir",
                      "Texto do link", "Abaixo do banner", "Canto inferior direito", "Página de manutenção ou de lançamento"):
            self.assertIn(texto.lower(), html.lower(), texto)
        self.assertIn('type="checkbox" name="pages" value="product"', html)
        self.assertIn('type="radio" name="position" value="corner"', html)
        self.assertNotIn("frete", html.lower())

    def test_every_content_language_even_those_the_store_does_not_offer(self):
        html = self.client.get(self.add).content.decode()

        self.assertFalse(SiteLanguage.objects.get(code="de").is_active)
        for idioma in Language:
            self.assertIn(f'<option value="{idioma.value}"', html)

    def test_creates_a_notice_with_a_future_language(self):
        response = self.client.post(self.add, dados(
            [
                {"language": "pt", "title": "Novidade", "message": "Chegaram as cores metálicas.", "link_label": "Ver"},
                {"language": "de", "title": "", "message": "Neue Metallicfarben sind da.", "link_label": ""},
            ],
            pages=("home", "catalog"), position="top", link_url="/modelos/",
        ))

        self.assertEqual(response.status_code, 302, response.content.decode()[:3000])
        notice = StoreNotice.objects.get()
        self.assertEqual(notice.pages, ["home", "catalog"])
        self.assertEqual(notice.position, "top")
        self.assertEqual(notice.link_url, "/modelos/")
        self.assertEqual(notice.tr("message", language="de", fallback=False), "Neue Metallicfarben sind da.")

    def test_portuguese_is_required(self):
        response = self.client.post(self.add, dados([{"language": "fr", "message": "Nouveau."}]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("A tradução em português é obrigatória.", response.content.decode())
        self.assertFalse(StoreNotice.objects.exists())

    def test_edit_and_remove_a_translation(self):
        notice = aviso("Antes", fr={"message": "Avant"})
        pt = notice.translations.get(language="pt")
        fr = notice.translations.get(language="fr")
        url = reverse("admin:storefront_storenotice_change", args=[notice.pk])

        response = self.client.post(url, dados([
            {"id": str(pt.pk), "language": "pt", "message": "Depois"},
            {"id": str(fr.pk), "language": "fr", "message": "Avant", "DELETE": "on"},
        ], initial=2))

        self.assertEqual(response.status_code, 302, response.content.decode()[:3000])
        notice.refresh_translations()
        self.assertEqual(notice.available_languages(), ["pt"])
        self.assertEqual(notice.tr("message"), "Depois")

    def test_corner_without_closing_is_refused_in_the_form(self):
        response = self.client.post(self.add, dados(
            [{"language": "pt", "message": "No canto"}], position="corner", dismissible=False,
        ))

        self.assertEqual(response.status_code, 200)
        self.assertIn("O card do canto sempre pode ser fechado", response.content.decode())

    def test_the_list_shows_what_each_notice_does(self):
        aviso("Frete grátis acima de €50.", pages=["home", "product"], position=NoticePosition.CORNER)

        html = self.client.get(reverse("admin:storefront_storenotice_changelist")).content.decode()

        self.assertIn("Frete grátis acima de €50.", html)
        self.assertIn("Canto inferior direito", html)
        self.assertIn("Home, Páginas de produto", html)
