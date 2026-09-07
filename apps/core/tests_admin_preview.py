"""A pré-visualização ao vivo e a grade de campos do Admin.

O que estes testes guardam:

1. o endpoint do preview só responde a POST, só a quem entra no Admin e só a
   quem tem permissão no modelo — e nunca grava nada;
2. o quadro mostra o que está no formulário AGORA, inclusive o texto por
   idioma que ainda não foi salvo, no idioma pedido;
3. o que a pessoa digita é texto, nunca HTML;
4. a seção da Home é resolvida pela MESMA rotina da página, e um registro
   novo aparece entre os irmãos já cadastrados, na posição dele;
5. a página especial usa o MESMO renderizador do visitante;
6. as telas de edição levam o painel, os campos relacionados dividem linha
   e as classes dos campos condicionais continuam lá;
7. o custo do preview não cresce com o conteúdo.
"""

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.models import BrandAssets
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_section, make_user
from apps.home.models import (
    HomeBanner,
    HomeCallout,
    HomeCalloutTranslation,
    HomeCategoryCard,
    HomeCategoryCardTranslation,
    HomeSection,
    HomeSectionType,
)
from apps.storefront.models import (
    FooterColumn,
    FooterColumnTranslation,
    FooterLink,
    FooterLinkTranslation,
    FooterSettings,
    InstitutionalPage,
    InstitutionalPageTranslation,
    PageSlug,
    SpecialPage,
    SpecialPageKind,
    SpecialPageTranslation,
    TopBarItem,
    TopBarItemTranslation,
)


def inline(prefix, rows, initial=0):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"{prefix}-{index}-{field}"] = value
    return data


def preview_url(model, pk=None):
    nome = f"admin:{model._meta.app_label}_{model._meta.model_name}_live_preview"
    if pk is None:
        return reverse(nome)
    return reverse(nome + "_change", args=[pk])


class PreviewBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.chefe = make_user("chefe", is_staff=True, is_superuser=True, with_customer=False)
        self.client.force_login(self.chefe)

    def preview(self, model, data, pk=None, lang=None, status=200):
        url = preview_url(model, pk)
        if lang:
            url += f"?lang={lang}"
        resposta = self.client.post(url, data)
        self.assertEqual(resposta.status_code, status, getattr(resposta, "content", b"")[:300])
        return resposta

    def top_bar_payload(self, texto="Frete grátis acima de 50 €", **campos):
        data = {"internal_name": "Frete", "is_active": "on", "sort_order": "5", "icon": "", "color": "", "link_url": ""}
        data.update(campos)
        data.update(inline("translations", [{"language": "pt", "text": texto}]))
        return data


class AcessoAoPreviewTests(PreviewBase):
    def test_get_is_not_allowed(self):
        resposta = self.client.get(preview_url(TopBarItem))
        self.assertEqual(resposta.status_code, 405)

    def test_anonymous_is_sent_to_the_login(self):
        self.client.logout()
        resposta = self.client.post(preview_url(TopBarItem), self.top_bar_payload())
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("admin:login"), resposta["Location"])

    def test_staff_without_permission_gets_403(self):
        funcionario = make_user("func", is_staff=True, with_customer=False)
        self.client.force_login(funcionario)
        self.preview(TopBarItem, self.top_bar_payload(), status=403)

        item = TopBarItem.objects.create(internal_name="Item")
        self.preview(TopBarItem, self.top_bar_payload(), pk=item.pk, status=403)

    def test_view_permission_is_enough_for_an_existing_record(self):
        leitor = make_user("leitor", is_staff=True, with_customer=False)
        leitor.user_permissions.add(Permission.objects.get(codename="view_topbaritem"))
        self.client.force_login(leitor)
        item = TopBarItem.objects.create(internal_name="Item")
        self.preview(TopBarItem, self.top_bar_payload(), pk=item.pk)
        # ...mas não para criar um registro que ele não pode criar.
        self.preview(TopBarItem, self.top_bar_payload(), status=403)

    def test_unknown_record_is_404(self):
        self.preview(TopBarItem, self.top_bar_payload(), pk=999, status=404)

    def test_response_is_not_cacheable_nor_indexable(self):
        resposta = self.preview(TopBarItem, self.top_bar_payload())
        self.assertIn("no-store", resposta["Cache-Control"])
        self.assertEqual(resposta["X-Robots-Tag"], "noindex, nofollow")

    def test_preview_never_saves(self):
        self.preview(TopBarItem, self.top_bar_payload())
        self.assertEqual(TopBarItem.objects.count(), 0)
        self.assertEqual(TopBarItemTranslation.objects.count(), 0)

        item = TopBarItem.objects.create(internal_name="Item")
        TopBarItemTranslation.objects.create(master=item, language="pt", text="Antigo")
        self.preview(TopBarItem, self.top_bar_payload("Novo"), pk=item.pk)
        item.refresh_from_db()
        self.assertEqual(item.translations.get(language="pt").text, "Antigo")


class ConteudoDoPreviewTests(PreviewBase):
    def test_new_record_shows_the_typed_text_with_the_public_component(self):
        html = self.preview(TopBarItem, self.top_bar_payload()).content.decode()
        self.assertIn("Frete grátis acima de 50 €", html)
        self.assertIn('data-top-bar', html)
        self.assertIn("tailwind", html)

    def test_new_item_takes_its_place_among_the_saved_ones(self):
        primeiro = TopBarItem.objects.create(internal_name="A", sort_order=0)
        TopBarItemTranslation.objects.create(master=primeiro, language="pt", text="Primeiro item salvo")
        ultimo = TopBarItem.objects.create(internal_name="Z", sort_order=10)
        TopBarItemTranslation.objects.create(master=ultimo, language="pt", text="Último item salvo")

        html = self.preview(TopBarItem, self.top_bar_payload("Item do meio", sort_order="5")).content.decode()
        self.assertLess(html.index("Primeiro item salvo"), html.index("Item do meio"))
        self.assertLess(html.index("Item do meio"), html.index("Último item salvo"))

    def test_existing_record_shows_the_unsaved_values(self):
        item = TopBarItem.objects.create(internal_name="Item")
        TopBarItemTranslation.objects.create(master=item, language="pt", text="Texto gravado")
        html = self.preview(TopBarItem, self.top_bar_payload("Texto só no formulário"), pk=item.pk).content.decode()
        self.assertIn("Texto só no formulário", html)
        self.assertNotIn("Texto gravado", html)

    def test_language_switch_uses_the_typed_translation(self):
        data = self.top_bar_payload()
        data.update(
            inline(
                "translations",
                [{"language": "pt", "text": "Entrega rápida"}, {"language": "fr", "text": "Livraison rapide"}],
            )
        )
        self.assertIn("Entrega rápida", self.preview(TopBarItem, data).content.decode())
        html_fr = self.preview(TopBarItem, data, lang="fr").content.decode()
        self.assertIn("Livraison rapide", html_fr)
        self.assertNotIn("Entrega rápida", html_fr)
        # Idioma inventado cai no padrão em vez de quebrar.
        self.assertIn("Entrega rápida", self.preview(TopBarItem, data, lang="xx").content.decode())

    def test_typed_html_is_escaped(self):
        html = self.preview(TopBarItem, self.top_bar_payload("<script>alert(1)</script>")).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_footer_preview_reflects_the_form_and_the_saved_columns(self):
        FooterSettings.objects.create(pk=1, is_active=True)
        coluna = FooterColumn.objects.create(internal_name="ajuda")
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title="Coluna de ajuda")
        link = FooterLink.objects.create(column=coluna, url="https://exemplo.test")
        FooterLinkTranslation.objects.create(master=link, language="pt", label="Fale conosco")

        data = {
            "is_active": "on",
            "contact_email": "oi@jdprint.test",
            "contact_phone": "",
            "surface_color": "",
            "text_color": "",
            "heading_color": "",
            "accent_color": "",
        }
        data.update(inline("translations", [{"language": "pt", "copyright_text": "© Um texto que ninguém salvou"}]))
        html = self.preview(FooterSettings, data, pk=1).content.decode()
        self.assertIn("© Um texto que ninguém salvou", html)
        self.assertIn("oi@jdprint.test", html)
        self.assertIn("Coluna de ajuda", html)
        self.assertIn("Fale conosco", html)

    def test_footer_link_preview_shows_the_new_link_in_its_column(self):
        coluna = FooterColumn.objects.create(internal_name="ajuda")
        FooterColumnTranslation.objects.create(master=coluna, language="pt", title="Coluna de ajuda")
        data = {"column": str(coluna.pk), "page": "", "url": "https://exemplo.test", "is_active": "on", "sort_order": "0"}
        data.update(inline("translations", [{"language": "pt", "label": "Link ainda não salvo"}]))
        antes = FooterLink.objects.count()  # a instalação já traz links de fábrica
        html = self.preview(FooterLink, data).content.decode()
        self.assertIn("Coluna de ajuda", html)
        self.assertIn("Link ainda não salvo", html)
        self.assertEqual(FooterLink.objects.count(), antes)

    def test_institutional_page_preview_renders_the_article(self):
        data = {"slug": PageSlug.SHIPPING, "is_active": "on", "show_in_footer": "on", "sort_order": "0"}
        corpo = "# Nossa oficina" + chr(10) + "- impressoras próprias"
        data.update(
            inline(
                "translations",
                [{"language": "pt", "title": "Quem faz a JD Print", "intro": "", "body": corpo, "meta_description": ""}],
            )
        )
        html = self.preview(InstitutionalPage, data).content.decode()
        self.assertIn("Quem faz a JD Print", html)
        self.assertIn("Nossa oficina", html)
        self.assertIn("impressoras próprias", html)

    def special_payload(self, kind, **campos):
        data = {
            "internal_name": "Página",
            "kind": kind,
            "logo_mark": "JD",
            "logo_text": "Print",
            "logo_url": "",
            "status_color": "mint",
            "primary_url": "",
            "secondary_url": "",
            "show_progress": "on",
            "progress_percent": "50",
            "launch_date": "",
            "launch_time": "",
            "launch_timezone": "Europe/Brussels",
            "show_countdown": "on",
            "show_form": "on",
            "sticker_1_tone": "mint",
            "sticker_2_tone": "yellow",
            "sticker_3_tone": "coral",
            "instagram_url": "",
            "whatsapp_url": "",
            "contact_email": "",
        }
        data.update(campos)
        return data

    def test_special_page_preview_uses_the_public_renderer(self):
        data = self.special_payload(SpecialPageKind.LAUNCH)
        data.update(
            inline(
                "translations",
                [{"language": "pt", "title": "Estamos quase abrindo a loja", "status_text": "Em breve", "form_button_label": "Quero saber"}],
            )
        )
        html = self.preview(SpecialPage, data).content.decode()
        self.assertIn("sp-body", html)
        self.assertIn("Estamos quase abrindo a loja", html)
        self.assertIn("Quero saber", html)
        self.assertEqual(SpecialPage.objects.count(), 0)
        self.assertIsNone(SpecialPage.objects.current())

    def test_special_page_preview_of_a_saved_page_follows_the_form(self):
        pagina = SpecialPage.objects.create(internal_name="Manutenção", kind=SpecialPageKind.MAINTENANCE)
        SpecialPageTranslation.objects.create(master=pagina, language="pt", title="Voltamos já", progress_label="Calibrando")
        data = self.special_payload(SpecialPageKind.MAINTENANCE, progress_percent="72")
        data.update(inline("translations", [{"language": "pt", "title": "Voltamos já", "progress_label": "Aquecendo a mesa"}]))
        html = self.preview(SpecialPage, data, pk=pagina.pk).content.decode()
        self.assertIn("Aquecendo a mesa", html)
        self.assertIn("72", html)
        pagina.refresh_from_db()
        self.assertFalse(pagina.is_active)
        self.assertEqual(pagina.translations.get(language="pt").progress_label, "Calibrando")

    def test_brand_assets_preview_shows_header_and_footer(self):
        BrandAssets.objects.create(pk=1)
        html = self.preview(BrandAssets, {}, pk=1).content.decode()
        self.assertIn("<header", html)
        self.assertIn("<footer", html)


class HomePreviewTests(PreviewBase):
    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="modelos", name="Modelos")

    def secao_categorias(self, blocos=("Bloco um", "Bloco dois")):
        secao = HomeSection.objects.create(
            internal_name="Categorias", section_type=HomeSectionType.CATEGORY_CARDS, sort_order=0
        )
        for posicao, titulo in enumerate(blocos):
            bloco = HomeCategoryCard.objects.create(
                section=secao, internal_name=titulo, category=self.categoria, sort_order=posicao
            )
            HomeCategoryCardTranslation.objects.create(master=bloco, language="pt", title=titulo)
        return secao

    def secao_payload(self, secao, **campos):
        data = {
            "internal_name": secao.internal_name,
            "section_type": secao.section_type,
            "is_active": "on",
            "sort_order": str(secao.sort_order),
            "layout": secao.layout,
            "product_limit": str(secao.product_limit),
            "category": "",
            "cta_target": "none",
            "cta_category": "",
            "cta_product": "",
            "cta_url": "",
            "callout": "",
            "about": "",
        }
        data.update(campos)
        data.update(inline("translations", []))
        data.update(inline("items", []))
        return data

    def test_section_preview_uses_the_home_resolver(self):
        secao = self.secao_categorias()
        html = self.preview(HomeSection, self.secao_payload(secao), pk=secao.pk).content.decode()
        self.assertIn(f'id="secao-{secao.pk}"', html)
        self.assertIn("Bloco um", html)
        self.assertIn("Bloco dois", html)

    def test_section_that_would_not_be_drawn_explains_why(self):
        secao = make_section(section_type=HomeSectionType.FEATURED_PRODUCTS)
        html = self.preview(HomeSection, self.secao_payload(secao), pk=secao.pk).content.decode()
        self.assertIn("não seria desenhada", html)
        self.assertNotIn(f'id="secao-{secao.pk}"', html)

    def test_section_preview_shows_the_typed_title_for_products(self):
        secao = make_section(section_type=HomeSectionType.FEATURED_PRODUCTS, title="Título gravado")
        make_product(sku="P1", category=self.categoria, is_featured=True)
        data = self.secao_payload(secao)
        data.update(inline("translations", [{"language": "pt", "title": "Título só do formulário"}]))
        html = self.preview(HomeSection, data, pk=secao.pk).content.decode()
        self.assertIn("Título só do formulário", html)
        self.assertNotIn("Título gravado", html)

    def test_new_block_appears_among_its_siblings_in_order(self):
        secao = self.secao_categorias(("Bloco um", "Bloco três"))
        HomeCategoryCard.objects.filter(internal_name="Bloco três").update(sort_order=10)
        data = {
            "section": str(secao.pk),
            "category": str(self.categoria.pk),
            "internal_name": "novo",
            "is_active": "on",
            "sort_order": "5",
            "icon": "",
            "bg_color": "",
            "text_color": "",
            "accent_color": "",
        }
        data.update(inline("translations", [{"language": "pt", "title": "Bloco dois"}]))
        html = self.preview(HomeCategoryCard, data).content.decode()
        self.assertLess(html.index("Bloco um"), html.index("Bloco dois"))
        self.assertLess(html.index("Bloco dois"), html.index("Bloco três"))
        self.assertEqual(HomeCategoryCard.objects.count(), 2)

    def test_each_callout_instance_previews_its_own_content(self):
        textos = []
        for titulo in ("Primeira chamada", "Segunda chamada"):
            texto = HomeCallout.objects.create(internal_name=titulo)
            HomeCalloutTranslation.objects.create(master=texto, language="pt", title=titulo)
            textos.append(texto)
        for texto in textos:
            data = {"internal_name": texto.internal_name, "is_active": "on", "cta_target": "none"}
            data.update(inline("translations", [{"language": "pt", "title": texto.internal_name}]))
            html = self.preview(HomeCallout, data, pk=texto.pk).content.decode()
            self.assertIn(texto.internal_name, html)
            outro = [t for t in textos if t is not texto][0]
            self.assertNotIn(outro.internal_name, html)

    def test_banner_preview_renders_the_frame_without_saving(self):
        banner = HomeBanner.objects.create(internal_name="Banner", layout="editorial")
        data = {
            "internal_name": "Banner",
            "layout": "poster_pop",
            "is_active": "on",
            "sort_order": "0",
            "cta_target": "none",
            "cta_url": "",
            "cta_secondary_url": "",
            "plate_color": "",
            "frame_color": "",
            "surface_color": "",
        }
        data.update(inline("translations", [{"language": "pt", "title": "Título digitado agora"}]))
        html = self.preview(HomeBanner, data, pk=banner.pk).content.decode()
        self.assertIn("Título digitado agora", html)
        banner.refresh_from_db()
        self.assertEqual(banner.layout, "editorial")

    def test_preview_cost_does_not_grow_with_the_content(self):
        secao = self.secao_categorias(("A", "B"))
        with CaptureQueriesContext(connection) as poucos:
            self.preview(HomeSection, self.secao_payload(secao), pk=secao.pk)
        for titulo in ("C", "D", "E", "F"):
            bloco = HomeCategoryCard.objects.create(
                section=secao, internal_name=titulo, category=self.categoria, sort_order=9
            )
            HomeCategoryCardTranslation.objects.create(master=bloco, language="pt", title=titulo)
        with CaptureQueriesContext(connection) as muitos:
            self.preview(HomeSection, self.secao_payload(secao), pk=secao.pk)
        self.assertEqual(len(poucos), len(muitos))


class TelasDeEdicaoTests(PreviewBase):
    def html(self, url):
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()

    def test_banner_form_has_the_panel_the_grid_and_the_conditional_classes(self):
        html = self.html(reverse("admin:home_homebanner_add"))
        self.assertIn("data-live-preview", html)
        self.assertIn(reverse("admin:home_homebanner_live_preview"), html)
        self.assertIn("admin/js/live_preview.js", html)
        self.assertIn("admin/css/jdprint_forms.css", html)
        self.assertIn("admin/js/jd_fields.js", html)
        # Os controles do painel: idiomas, três larguras e recolher.
        for trecho in ('data-width="768"', 'data-width="390"', 'class="jd-preview-toggle"', 'data-lang="fr"',
                       'data-storage-key="jd-preview:home.homebanner"'):
            self.assertIn(trecho, html)
        # Campos relacionados dividem a linha.
        self.assertIn("form-multiline field-internal_name field-layout", html)
        self.assertIn("fieldBox field-internal_name", html)
        self.assertIn("fieldBox field-layout", html)
        # Os blocos condicionais continuam com as classes que o JS procura.
        for classe in ("jd-poster", "jd-composicao"):
            self.assertIn(classe, html)
        # A tradução também vem em grade.
        self.assertIn("field-language field-eyebrow field-title_highlight", html)
        for titulo in ("IDENTIFICAÇÃO", "IMAGENS", "BOTÕES", "AUDITORIA"):
            self.assertIn(titulo, html)

    def test_change_form_points_the_panel_at_the_record(self):
        item = TopBarItem.objects.create(internal_name="Item")
        html = self.html(reverse("admin:storefront_topbaritem_change", args=[item.pk]))
        self.assertIn(reverse("admin:storefront_topbaritem_live_preview_change", args=[item.pk]), html)

    def test_footer_column_links_inline_is_card_ready(self):
        coluna = FooterColumn.objects.create(internal_name="ajuda")
        html = self.html(reverse("admin:storefront_footercolumn_change", args=[coluna.pk]))
        self.assertIn('class="module jd-cards"', html)
        self.assertIn("admin/js/jd_tabular_cards.js", html)

    def test_section_form_keeps_the_conditional_blocks(self):
        html = self.html(reverse("admin:home_homesection_add"))
        for classe in ("jd-produtos", "jd-cta", "jd-callout", "jd-about", "jd-blocos"):
            self.assertIn(classe, html)
        self.assertIn("field-cta_target field-cta_category field-cta_product field-cta_url", html)
        self.assertIn("field-category field-include_subcategories", html)

    def test_special_page_form_has_the_requested_order(self):
        html = self.html(reverse("admin:storefront_specialpage_add"))
        titulos = ["IDENTIFICAÇÃO", "MARCA", "BOTÕES", "MANUTENÇÃO", "LANÇAMENTO", "SELOS", "RODAPÉ E CONTATO", "CORES", "AUDITORIA"]
        posicoes = [html.index(f'class="fieldset-heading">{t}') for t in titulos]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertIn("jd-sp-maintenance", html)
        self.assertIn("jd-sp-launch", html)
        self.assertIn("data-live-preview", html)

    def test_product_form_keeps_its_own_template(self):
        produto = make_product(sku="P1", category=make_category())
        html = self.html(reverse("admin:catalog_product_change", args=[produto.pk]))
        self.assertNotIn("data-live-preview", html)
        self.assertNotIn("live_preview", html)


class FrontendIntactoTests(LanguageResetMixin, TestCase):
    """Os componentes extraídos para o preview continuam no site público."""

    def test_top_bar_still_renders_in_the_header(self):
        item = TopBarItem.objects.create(internal_name="Item")
        TopBarItemTranslation.objects.create(master=item, language="pt", text="Frete combinado")
        html = self.client.get("/").content.decode()
        self.assertIn('data-top-bar', html)
        self.assertIn("Frete combinado", html)

    def test_institutional_page_still_renders_the_article(self):
        # A instalação já traz esta página (migration de conteúdo): só o texto muda.
        pagina, _ = InstitutionalPage.objects.update_or_create(slug=PageSlug.SHIPPING, defaults={"is_active": True})
        InstitutionalPageTranslation.objects.update_or_create(
            master=pagina, language="pt", defaults={"title": "Sobre nós", "body": "Texto da página"}
        )
        resposta = self.client.get(reverse("storefront:page_shipping"))
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        self.assertIn("Sobre nós", html)
        self.assertIn("Texto da página", html)
