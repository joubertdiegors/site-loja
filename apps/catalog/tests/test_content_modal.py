"""CONTEÚDO como tabela + modal — §6 e §7 da etapa 13.

Antes, os cinco campos de cada idioma ficavam abertos na página: com quatro
idiomas eram vinte campos empilhados antes de chegar às variantes. Agora é uma
linha por idioma e um modal de cada vez.

O modal é o **mesmo** das variantes: a casca (`jd_modal.js`), o CSS
(`.jd-modal*`), o ESC, o foco, a trava de rolagem e a pintura de erro são
literalmente compartilhados. Estes testes seguram esse compartilhamento — é
fácil, sob pressa, nascer um segundo sistema de modal.

Duas telas, dois comportamentos, e a diferença está escrita na própria tela:

* **edição** — o modal grava o idioma sozinho, por `content_save`;
* **cadastro** — o produto ainda não tem PK: o modal alimenta o formset e a
  gravação acontece junto com o produto. Sem isso haveria registro órfão.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import ProductStatus, ProductTranslation
from apps.core.testing import LanguageResetMixin, make_category, make_product


class ContentModalBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            username="ana", email="ana@jdprint.test", password="senha-bem-comprida"
        )
        self.client.force_login(self.staff)

        self.category = make_category(slug="animais", name="Animais")
        self.product = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.category
        )
        self.pt = self.product.translations.get(language="pt")

    def change_url(self, product=None):
        return f"/admin/catalog/product/{(product or self.product).pk}/change/"

    def save_url(self, product=None):
        return reverse(
            "admin:catalog_product_content_save",
            args=[(product or self.product).pk],
        )

    def delete_url(self, translation, product=None):
        return reverse(
            "admin:catalog_product_content_delete",
            args=[(product or self.product).pk, translation.pk],
        )

    def content_html(self):
        html = self.client.get(self.change_url()).content.decode()
        return html.split("data-content-inline", 1)[1].split("</fieldset>", 1)[0]


class ContentTableTests(ContentModalBase):
    """1 — a tabela aparece, e os campos não ficam abaixo dela."""

    def test_the_page_has_the_content_table(self):
        resposta = self.client.get(self.change_url())

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "data-content-inline")
        self.assertContains(resposta, "data-content-rows")
        self.assertContains(resposta, "jd-content-table")

    def test_the_table_shows_one_column_per_thing_worth_comparing(self):
        cabecalho = self.content_html().split("<thead>")[1].split("</thead>")[0]

        for coluna in ("Idioma", "Nome", "Descrição curta", "Descrição"):
            with self.subTest(coluna=coluna):
                self.assertIn(coluna, cabecalho)

    def test_the_extra_information_field_is_not_a_column(self):
        """O campo existe no modal; na grade só cabe o que se compara."""
        cabecalho = self.content_html().split("<thead>")[1].split("</thead>")[0]

        self.assertNotIn("Informações extras", cabecalho)

    def test_the_fields_live_inside_a_modal(self):
        bloco = self.content_html()

        self.assertIn("data-content-modal", bloco)
        self.assertIn('class="jd-modal"', bloco)
        self.assertIn('role="dialog"', bloco)
        self.assertIn('aria-modal="true"', bloco)

    def test_every_language_has_its_own_modal(self):
        ProductTranslation.objects.create(
            master=self.product, language="fr", name="Chat Pompom"
        )

        bloco = self.content_html()

        # Um por idioma gravado, mais o molde do "+ Adicionar idioma".
        self.assertEqual(bloco.count('class="jd-modal"'), 3)

    def test_there_is_a_button_to_add_a_language(self):
        self.assertIn("data-content-add", self.content_html())
        self.assertIn("Adicionar idioma", self.content_html())

    def test_there_is_only_one_add_button(self):
        """Achado no navegador: o Django desenhava um segundo, fora do modal.

        `js-inline-admin-formset` + `data-inline-formset` são as marcas por onde
        o `inlines.js` do Django entra e acrescenta o próprio "Adicionar
        outro(a)…". Ele monta a linha do jeito antigo, que o nosso JavaScript
        não conhece — dois botões de adicionar, um deles ignorando o modal.
        """
        bloco = self.content_html()

        self.assertNotIn("js-inline-admin-formset", bloco)
        self.assertNotIn("data-inline-formset", bloco)
        self.assertEqual(bloco.count("data-content-add"), 1)

    def test_the_variant_table_does_not_get_one_either(self):
        """Mesma marca, mesmo risco — e as duas tabelas são o mesmo padrão."""
        html = self.client.get(self.change_url()).content.decode()
        bloco = html.split("data-variant-inline", 1)[1].split("</fieldset>", 1)[0]

        self.assertNotIn("js-inline-admin-formset", bloco)
        self.assertEqual(bloco.count("data-variant-add"), 1)

    def test_the_section_is_collapsible_and_starts_open(self):
        bloco = self.content_html()

        self.assertIn("<details open>", bloco)
        self.assertIn("<summary>", bloco)


class ContentModalSharesTheVariantShellTests(ContentModalBase):
    """7 — um sistema de modal só. Dois seria um para corrigir duas vezes."""

    def test_both_modals_load_the_same_shell(self):
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn("admin/js/jd_modal.js", html)
        self.assertIn("admin/js/variant_admin.js", html)
        self.assertIn("admin/js/content_admin.js", html)

    def test_the_content_javascript_uses_the_shell(self):
        with open("static/admin/js/content_admin.js", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        self.assertIn("new window.JDModal(", fonte)

    def test_the_content_javascript_does_not_reimplement_escape_or_focus(self):
        """Duas respostas para o ESC brigariam entre si."""
        with open("static/admin/js/content_admin.js", encoding="utf-8") as arquivo:
            fonte = arquivo.read()

        for repetido in ('evento.key === "Escape"', "jd-modal-lock", "csrfmiddlewaretoken"):
            with self.subTest(repetido=repetido):
                self.assertNotIn(repetido, fonte)

    def test_both_modals_wear_the_same_css(self):
        bloco = self.content_html()

        for classe in ("jd-modal-backdrop", "jd-modal-dialog", "jd-modal-head",
                       "jd-modal-body", "jd-modal-foot", "jd-modal-field"):
            with self.subTest(classe=classe):
                self.assertIn(classe, bloco)

    def test_the_variant_modal_still_works_the_way_it_did(self):
        """§7: refatorar a casca não pode mexer no que já foi validado."""
        html = self.client.get(self.change_url()).content.decode()

        self.assertIn("data-variant-inline", html)
        self.assertIn("data-variant-modal", html)
        self.assertIn("data-variant-save", html)
        self.assertIn("data-variant-modal-error", html)

    def test_the_error_hook_is_the_shared_one(self):
        """A casca procura `data-modal-error`; sem ele o erro não apareceria."""
        self.assertIn("data-modal-error", self.content_html())

    def test_the_empty_error_box_is_really_hidden(self):
        """Achado no navegador: `hidden` sozinho não escondia a tarja.

        O CSS do Admin declara `.errornote { display: block }`, e uma classe
        vence o `[hidden]` do navegador — todo modal abria com uma tarja
        vermelha vazia no topo, um erro que não existia. Vale para os dois
        modais, porque a caixa é a mesma.
        """
        with open("static/admin/css/jdprint_admin.css", encoding="utf-8") as arquivo:
            css = arquivo.read()

        self.assertIn(".jd-modal .errornote[hidden]", css)
        self.assertIn("data-modal-error hidden", self.content_html())


class ModalBackdropTests(ContentModalBase):
    """Clicar fora não fecha — §10 a §12 da etapa 14.

    Antes: abria o modal, digitava, clicava fora sem querer e perdia tudo. Num
    campo de descrição de produto isso custava parágrafos, e nada na tela
    avisava que ia acontecer.

    Agora sair do modal é uma decisão, com três portas: Cancelar, o X e o ESC.
    Cancelar continua desfazendo o que foi digitado — é essa a diferença entre
    "não quero" (Cancelar) e "cliquei errado" (fora).

    A regra vive na casca (`jd_modal.js`), então vale para os dois modais do
    Admin. Um segundo mecanismo só para o conteúdo seria um lugar a mais para
    a regra se perder.
    """

    def shell(self):
        with open("static/admin/js/jd_modal.js", encoding="utf-8") as arquivo:
            return arquivo.read()

    def test_the_shell_swallows_the_click_outside(self):
        fonte = self.shell()

        self.assertIn("stopPropagation", fonte)
        self.assertIn("dialog.contains(evento.target)", fonte)

    def test_the_shell_never_cancels_on_a_click_outside(self):
        """A guarda não pode chamar `cancel()` — seria fechar por outro nome."""
        fonte = self.shell()
        guarda = fonte.split('element.addEventListener("click"', 1)[1].split("});", 1)[0]

        self.assertNotIn("cancel", guarda)
        self.assertNotIn("close", guarda)

    def test_no_modal_wires_the_backdrop_to_cancel(self):
        for arquivo in ("variant_admin.js", "content_admin.js"):
            with self.subTest(arquivo=arquivo):
                with open(f"static/admin/js/{arquivo}", encoding="utf-8") as origem:
                    fonte = origem.read()
                self.assertNotIn("backdrop", fonte)

    def test_the_dead_backdrop_hooks_are_gone_from_the_markup(self):
        """Gancho que ninguém lê engana quem for mexer nisto depois."""
        html = self.client.get(self.change_url()).content.decode()

        self.assertNotIn("data-content-backdrop", html)
        self.assertNotIn("data-variant-backdrop", html)
        self.assertIn('class="jd-modal-backdrop"', html)

    def test_escape_still_closes(self):
        """ESC continua sendo porta de saída, e continua cancelando."""
        fonte = self.shell()

        self.assertIn('evento.key === "Escape"', fonte)
        self.assertIn("aberto.cancel()", fonte)

    def test_cancel_still_restores_what_was_there(self):
        """Reabrir depois de cancelar tem que mostrar o valor original."""
        fonte = self.shell()

        self.assertIn("JDModal.prototype.capture", fonte)
        self.assertIn("this.restore(this.snapshot)", fonte)

    def test_both_modals_still_offer_cancel_and_close(self):
        bloco = self.content_html()

        self.assertIn("data-content-cancel", bloco)
        self.assertIn("jd-modal-x", bloco)


class ContentSaveTests(ContentModalBase):
    """2 — gravar pelo modal, com validação do servidor."""

    def post(self, **campos):
        dados = {
            "language": "fr",
            "name": "Chat Pompom",
            "short_description": "",
            "description": "",
            "extra_information": "",
        }
        dados.update(campos)
        return self.client.post(self.save_url(), dados)

    def test_saving_creates_the_translation(self):
        resposta = self.post()

        self.assertEqual(resposta.status_code, 200)
        corpo = json.loads(resposta.content)
        self.assertTrue(corpo["ok"])
        self.assertTrue(corpo["created"])
        self.assertTrue(
            self.product.translations.filter(language="fr", name="Chat Pompom").exists()
        )

    def test_saving_an_existing_translation_updates_it(self):
        frances = ProductTranslation.objects.create(
            master=self.product, language="fr", name="Antigo"
        )

        resposta = self.post(translation_id=frances.pk, name="Chat Pompom")

        corpo = json.loads(resposta.content)
        self.assertTrue(corpo["ok"])
        self.assertFalse(corpo["created"])
        frances.refresh_from_db()
        self.assertEqual(frances.name, "Chat Pompom")
        self.assertEqual(self.product.translations.count(), 2)

    def test_the_server_answers_with_what_it_wrote(self):
        """A tela reescreve os campos com o valor gravado, não com o digitado."""
        resposta = self.post(name="  Chat Pompom  ")

        corpo = json.loads(resposta.content)
        self.assertEqual(corpo["fields"]["name"], "Chat Pompom")

    def test_an_empty_name_is_refused_by_the_server(self):
        """§2: não confiar no JavaScript. O modal fica aberto com o erro."""
        resposta = self.post(name="")

        self.assertEqual(resposta.status_code, 400)
        corpo = json.loads(resposta.content)
        self.assertFalse(corpo["ok"])
        self.assertIn("name", corpo["errors"])
        self.assertEqual(self.product.translations.count(), 1)

    def test_a_duplicate_language_is_refused_with_a_readable_message(self):
        ProductTranslation.objects.create(
            master=self.product, language="fr", name="Chat"
        )

        resposta = self.post()

        self.assertEqual(resposta.status_code, 400)
        corpo = json.loads(resposta.content)
        self.assertIn("language", corpo["errors"])
        self.assertIn("já tem conteúdo", " ".join(corpo["errors"]["language"]))

    def test_renaming_does_not_move_the_product_url(self):
        """O slug é estável depois de criado — é ele que a loja publica.

        Mesma regra do caminho normal do Admin (`TranslatedSlugAdminMixin` só
        gera o slug quando o campo fica em branco). O modal não podia inventar
        uma regra própria: renomear um produto em cartaz derrubaria o link que
        o cliente já tem.
        """
        antes = self.product.slug

        self.post(translation_id=self.pt.pk, language="pt", name="Gato Malhado")

        self.product.refresh_from_db()
        self.assertEqual(self.product.slug, antes)
        self.assertEqual(self.product.translations.get(language="pt").name, "Gato Malhado")

    def test_a_get_is_refused(self):
        self.assertEqual(self.client.get(self.save_url()).status_code, 405)

    def test_a_stranger_cannot_save(self):
        self.client.logout()

        resposta = self.client.post(self.save_url(), {"language": "fr", "name": "X"})

        self.assertIn(resposta.status_code, (302, 403))
        self.assertEqual(self.product.translations.count(), 1)

    def test_a_reader_cannot_save(self):
        """Permissão de leitura não é permissão de gravar."""
        User = get_user_model()
        leitor = User.objects.create_user(
            username="leo", email="leo@jdprint.test",
            password="senha-bem-comprida", is_staff=True,
        )
        self.client.force_login(leitor)

        resposta = self.client.post(self.save_url(), {"language": "fr", "name": "X"})

        self.assertIn(resposta.status_code, (302, 403))
        self.assertEqual(self.product.translations.count(), 1)

    def test_it_cannot_write_into_another_product(self):
        outro = make_product(sku="CAO-01", name="Cão Bola", category=self.category)
        alheia = outro.translations.get(language="pt")

        resposta = self.client.post(
            self.save_url(),
            {"translation_id": alheia.pk, "language": "pt", "name": "Invadido"},
        )

        self.assertEqual(resposta.status_code, 404)
        alheia.refresh_from_db()
        self.assertEqual(alheia.name, "Cão Bola")


class ContentDeleteTests(ContentModalBase):
    """5 — remover um idioma, menos quando isso deixaria a loja sem nome."""

    def test_removing_a_secondary_language_works(self):
        frances = ProductTranslation.objects.create(
            master=self.product, language="fr", name="Chat"
        )

        resposta = self.client.post(self.delete_url(frances))

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(json.loads(resposta.content)["ok"])
        self.assertFalse(self.product.translations.filter(language="fr").exists())

    def test_the_last_translation_cannot_be_removed(self):
        resposta = self.client.post(self.delete_url(self.pt))

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(self.product.translations.count(), 1)

    def test_portuguese_cannot_leave_an_active_product(self):
        """Sem o português a loja fica sem fallback e o card sai com o SKU."""
        ProductTranslation.objects.create(
            master=self.product, language="fr", name="Chat"
        )
        self.product.status = ProductStatus.ACTIVE
        self.product.save()

        resposta = self.client.post(self.delete_url(self.pt))

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("fallback", json.loads(resposta.content)["detail"])
        self.assertTrue(self.product.translations.filter(language="pt").exists())

    def test_portuguese_can_leave_a_draft(self):
        ProductTranslation.objects.create(
            master=self.product, language="fr", name="Chat"
        )
        self.product.status = ProductStatus.DRAFT
        self.product.save()

        resposta = self.client.post(self.delete_url(self.pt))

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(self.product.translations.filter(language="pt").exists())

    def test_a_get_is_refused(self):
        frances = ProductTranslation.objects.create(
            master=self.product, language="fr", name="Chat"
        )

        self.assertEqual(self.client.get(self.delete_url(frances)).status_code, 405)

    def test_it_cannot_delete_from_another_product(self):
        outro = make_product(sku="CAO-01", name="Cão Bola", category=self.category)
        alheia = outro.translations.get(language="pt")

        resposta = self.client.post(
            reverse(
                "admin:catalog_product_content_delete",
                args=[self.product.pk, alheia.pk],
            )
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertTrue(outro.translations.filter(pk=alheia.pk).exists())


class ContentOnTheAddPageTests(ContentModalBase):
    """4 — produto sem PK: nada é gravado antes do produto, e a tela diz isso."""

    def add_html(self):
        html = self.client.get("/admin/catalog/product/add/").content.decode()
        return html.split("data-content-inline", 1)[1].split("</fieldset>", 1)[0]

    def test_the_add_page_offers_no_save_url(self):
        """Sem PK não há a que prender o conteúdo — gravar criaria órfão."""
        bloco = self.add_html()

        self.assertNotIn("data-content-save-url", bloco)
        self.assertNotIn("data-content-delete-url", bloco)

    def test_the_add_page_says_when_the_content_will_be_saved(self):
        self.assertIn("gravado junto com ele", self.add_html())

    def test_the_add_page_still_offers_the_modal_and_the_template(self):
        bloco = self.add_html()

        self.assertIn("data-content-modal", bloco)
        self.assertIn("data-content-template", bloco)

    def test_creating_a_product_through_the_formset_saves_its_content(self):
        """O caminho normal do Django continua funcionando, sem JavaScript."""
        resposta = self.client.post(
            "/admin/catalog/product/add/",
            {
                "sku": "COPO-01",
                "status": ProductStatus.DRAFT.value,
                "slug": "",
                "category": self.category.pk,
                "currency": "EUR",
                "personalization_type": "none",
                "personalization_text_limit": 0,
                "featured_order": 0,
                "translations-TOTAL_FORMS": "1",
                "translations-INITIAL_FORMS": "0",
                "translations-MIN_NUM_FORMS": "1",
                "translations-MAX_NUM_FORMS": "1000",
                "translations-0-language": "pt",
                "translations-0-name": "Copo Térmico",
                "translations-0-short_description": "",
                "translations-0-description": "",
                "translations-0-extra_information": "",
                "media-TOTAL_FORMS": "0",
                "media-INITIAL_FORMS": "0",
                "media-MIN_NUM_FORMS": "0",
                "media-MAX_NUM_FORMS": "1000",
                "product_colors-TOTAL_FORMS": "0",
                "product_colors-INITIAL_FORMS": "0",
                "product_colors-MIN_NUM_FORMS": "0",
                "product_colors-MAX_NUM_FORMS": "1000",
                "material_composition-TOTAL_FORMS": "0",
                "material_composition-INITIAL_FORMS": "0",
                "material_composition-MIN_NUM_FORMS": "0",
                "material_composition-MAX_NUM_FORMS": "1000",
                "variants-TOTAL_FORMS": "0",
                "variants-INITIAL_FORMS": "0",
                "variants-MIN_NUM_FORMS": "0",
                "variants-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        from apps.catalog.models import Product

        copo = Product.objects.get(sku="COPO-01")
        self.assertEqual(copo.translations.get(language="pt").name, "Copo Térmico")
        self.assertEqual(copo.slug, "copo-termico")
