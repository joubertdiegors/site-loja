"""O Admin de categorias: a lista em árvore e o modal.

A tela substitui a `change_list` padrão, e por isso estes testes cobrem tanto
o que ela mostra quanto o que os endpoints gravam:

* a árvore sai indentada, com pais antes de filhos, e o filtro/busca/ordem
  vêm do servidor — um filho que casa com a busca traz o pai junto, senão a
  linha ficaria sem o contexto que a distingue;
* o modal grava pelo mesmo formulário de domínio da ficha completa: nome em
  português obrigatório, nome repetido no mesmo nível recusado, hierarquia
  sem ciclo, slug gerado do nome, traduções criadas e apagadas;
* permissões: quem não pode criar não cria, quem não pode alterar não
  alterna, quem não pode excluir não exclui — nem forçando o POST;
* CSRF exigido, id de categoria inexistente dá 404, e o `PROTECT` de
  produtos e subcategorias vira mensagem, nunca erro 500.
"""

import json

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.categories.models import Category, CategoryTranslation
from apps.core.testing import make_product

LISTA = reverse("admin:categories_category_changelist")
GRAVAR = reverse("admin:categories_category_modal_save")
MASSA = reverse("admin:categories_category_bulk")
HISTORICO = reverse("admin:categories_category_history_all")


def categoria(slug, nome, parent=None, ordem=0, ativa=True, **traducoes):
    obj = Category.objects.create(slug=slug, parent=parent, sort_order=ordem, is_active=ativa)
    CategoryTranslation.objects.create(master=obj, language="pt", name=nome)
    for idioma, texto in traducoes.items():
        CategoryTranslation.objects.create(master=obj, language=idioma, name=texto)
    obj.refresh_translations()
    return obj


class AdminBase(TestCase):
    SENHA = "senha-de-teste-77"

    def setUp(self):
        self.chefe = User.objects.create_superuser("chefe", "chefe@jdprint.test", self.SENHA)
        self.client.force_login(self.chefe)
        self.modelos = categoria("modelos", "Modelos", ordem=0)
        self.animais = categoria("animais", "Animais", parent=self.modelos, ordem=1, fr="Animaux")
        self.gatos = categoria("gatos", "Gatos", parent=self.animais, ordem=1)
        self.brinquedos = categoria("brinquedos", "Brinquedos", ordem=1, ativa=False)

    def lista(self, **params):
        return self.client.get(LISTA, params)

    def linhas(self, resposta):
        return [(l["name"], l["depth"]) for l in resposta.context["jd_rows"]]

    def dados(self, categoria_id):
        url = reverse("admin:categories_category_modal_data", args=[categoria_id])
        return json.loads(self.client.get(url).content)

    def gravar(self, **campos):
        dados = {"name": "", "parent": "", "sort_order": "0", "slug": "", **campos}
        return self.client.post(GRAVAR, dados)


# ---------------------------------------------------------------------------
# A lista
# ---------------------------------------------------------------------------


class ListaTests(AdminBase):
    def test_the_tree_comes_indented_with_parents_before_children(self):
        resposta = self.lista()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            self.linhas(resposta),
            [("Modelos", 0), ("Animais", 1), ("Gatos", 2), ("Brinquedos", 0)],
        )
        self.assertEqual(resposta.context["jd_summary"], "2 categorias · 2 subcategorias")
        self.assertIn("4 de 4 registros visíveis", resposta.context["jd_footer"])

    def test_the_page_shows_the_model_anatomy(self):
        html = self.lista().content.decode()
        for pedaco in (
            "Categorias",
            "+ Nova categoria",
            "Histórico",
            "Buscar categoria ou slug",
            "Ações em massa",
            "CATEGORIA",
            "SLUG",
            "PRODUTOS",
            "ORDEM",
            "ATIVA",
            "Editar",
            "Limpar ordenação",
            "Reordenar manualmente",
            "jd-cat-modal",
            "Identificação",
            "Traduções",
            "Subcategorias",
            "Auditoria",
        ):
            with self.subTest(pedaco=pedaco):
                self.assertIn(pedaco, html)

    def test_searching_brings_the_parent_along_for_context(self):
        resposta = self.lista(q="gatos")
        self.assertEqual(self.linhas(resposta), [("Modelos", 0), ("Animais", 1), ("Gatos", 2)])
        self.assertIn("3 de 4", resposta.context["jd_footer"])

    def test_searching_by_slug_works_too(self):
        self.assertEqual(self.linhas(self.lista(q="brinquedos")), [("Brinquedos", 0)])

    def test_a_search_without_matches_shows_the_empty_state(self):
        resposta = self.lista(q="jacaré")
        self.assertEqual(resposta.context["jd_rows"], [])
        self.assertIn("Nenhuma categoria corresponde aos filtros.", resposta.content.decode())

    def test_the_status_filter(self):
        self.assertEqual(self.linhas(self.lista(status="inactive")), [("Brinquedos", 0)])
        ativos = [nome for nome, _d in self.linhas(self.lista(status="active"))]
        self.assertNotIn("Brinquedos", ativos)

    def test_sorting_by_a_column_and_clearing_it(self):
        resposta = self.lista(o="name.desc")
        self.assertEqual([n for n, _d in self.linhas(resposta)][0], "Modelos")
        self.assertTrue(resposta.context["jd_sorted"])
        coluna = resposta.context["jd_columns"][0]
        self.assertTrue(coluna["is_sorted"])
        self.assertIn("o=name.asc", coluna["url"])  # o próximo clique inverte
        self.assertFalse(self.lista().context["jd_sorted"])

    def test_an_invalid_sort_key_is_ignored(self):
        resposta = self.lista(o="rm -rf")
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["jd_sorted"])

    def test_the_product_count_is_the_real_one(self):
        make_product(sku="P1", name="Vaso", category=self.gatos)
        linha = next(l for l in self.lista().context["jd_rows"] if l["name"] == "Gatos")
        self.assertEqual(linha["products"], 1)

    def test_the_parent_options_are_the_whole_tree_indented(self):
        """Qualquer categoria pode ser pai — inclusive uma de terceiro nível.

        Oferecer só as raízes deixaria o terceiro nível para baixo
        inalcançável pelo modal. O traço por nível distingue duas categorias
        de mesmo nome em galhos diferentes.
        """
        nomes = [p["name"] for p in self.lista().context["jd_parents"]]
        self.assertEqual(nomes, ["Modelos", "— Animais", "— — Gatos", "Brinquedos"])

    def test_the_languages_come_from_the_store(self):
        idiomas = self.lista().context["jd_languages"]
        self.assertEqual(idiomas[0]["code"], "pt")
        self.assertTrue(idiomas[0]["is_default"])
        self.assertIn("fr", [i["code"] for i in idiomas])


# ---------------------------------------------------------------------------
# O modal: abrir
# ---------------------------------------------------------------------------


class ModalDataTests(AdminBase):
    def test_opening_a_category_brings_fields_children_and_audit(self):
        dados = self.dados(self.animais.pk)
        self.assertTrue(dados["ok"])
        self.assertEqual(dados["title"], "Animais")
        self.assertEqual(dados["kicker"], "MODELOS ›")
        self.assertEqual(dados["fields"]["name"], "Animais")
        self.assertEqual(dados["fields"]["name_fr"], "Animaux")
        self.assertEqual(dados["fields"]["parent"], str(self.modelos.pk))
        self.assertEqual(dados["fields"]["slug"], "animais")
        self.assertTrue(dados["fields"]["is_active"])
        self.assertEqual([c["name"] for c in dados["children"]], ["Gatos"])
        self.assertIn("/", dados["audit"]["created_at"])
        self.assertEqual(dados["audit"]["log"], [])  # criada fora do Admin: nada inventado

    def test_a_root_category_says_so_in_the_kicker(self):
        self.assertEqual(self.dados(self.modelos.pk)["kicker"], "PRIMEIRO NÍVEL")

    def test_the_audit_shows_the_real_admin_log(self):
        self.gravar(category_id=self.gatos.pk, name="Gatos", parent=self.animais.pk, is_active="on")
        registro = self.dados(self.gatos.pk)["audit"]["log"]
        self.assertTrue(registro)
        self.assertIn("modal", registro[0]["what"].lower())
        self.assertEqual(registro[0]["who"], "chefe")

    def test_a_category_that_does_not_exist_is_a_404(self):
        url = reverse("admin:categories_category_modal_data", args=[999999])
        self.assertEqual(self.client.get(url).status_code, 404)


# ---------------------------------------------------------------------------
# O modal: gravar
# ---------------------------------------------------------------------------


class ModalSaveTests(AdminBase):
    def test_creating_a_root_category_with_translations(self):
        resposta = self.gravar(
            name="Papelaria", sort_order="3", is_active="on",
            name_fr="Papeterie", description_pt="Cadernos e afins",
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = json.loads(resposta.content)
        self.assertTrue(corpo["ok"])
        self.assertTrue(corpo["created"])

        nova = Category.objects.get(pk=corpo["id"])
        self.assertEqual(nova.name_in("pt"), "Papelaria")
        self.assertEqual(nova.name_in("fr"), "Papeterie")
        self.assertEqual(nova.slug, "papelaria")  # gerado do nome
        self.assertEqual((nova.parent_id, nova.sort_order, nova.is_active), (None, 3, True))
        self.assertEqual(
            nova.translations.get(language="pt").description, "Cadernos e afins"
        )

    def test_creating_a_subcategory(self):
        resposta = self.gravar(name="Cães", parent=self.animais.pk, is_active="on")
        nova = Category.objects.get(pk=json.loads(resposta.content)["id"])
        self.assertEqual(nova.parent, self.animais)
        self.assertEqual(nova.depth, 2)

    def test_editing_changes_name_parent_order_slug_and_status(self):
        resposta = self.gravar(
            category_id=self.gatos.pk, name="Felinos", parent=self.modelos.pk,
            sort_order="7", slug="felinos",
        )
        self.assertTrue(json.loads(resposta.content)["ok"])
        self.gatos.refresh_from_db()
        self.gatos.refresh_translations()
        self.assertEqual(self.gatos.name_in("pt"), "Felinos")
        self.assertEqual(self.gatos.parent, self.modelos)
        self.assertEqual((self.gatos.sort_order, self.gatos.slug, self.gatos.is_active), (7, "felinos", False))

    def test_a_translation_is_removed_when_its_name_is_cleared(self):
        self.gravar(category_id=self.animais.pk, name="Animais", parent=self.modelos.pk, name_fr="", is_active="on")
        self.assertFalse(self.animais.translations.filter(language="fr").exists())
        self.assertTrue(self.animais.translations.filter(language="pt").exists())

    def test_the_portuguese_name_is_required(self):
        resposta = self.gravar(name="   ")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("name", json.loads(resposta.content)["errors"])
        self.assertEqual(Category.objects.count(), 4)

    def test_two_categories_with_the_same_name_at_the_same_level_are_refused(self):
        resposta = self.gravar(name="gatos ", parent=self.animais.pk)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Já existe uma categoria", json.loads(resposta.content)["errors"]["name"][0])

    def test_the_same_name_at_another_level_is_allowed(self):
        resposta = self.gravar(name="Gatos", parent=self.modelos.pk, is_active="on")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Category.objects.filter(translations__name="Gatos").count(), 2)

    def test_a_category_cannot_become_its_own_parent_or_a_descendants_child(self):
        for pai, caso in ((self.animais.pk, "ela mesma"), (self.gatos.pk, "um filho")):
            with self.subTest(caso=caso):
                resposta = self.gravar(category_id=self.animais.pk, name="Animais", parent=pai)
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("parent", json.loads(resposta.content)["errors"])
        self.animais.refresh_from_db()
        self.assertEqual(self.animais.parent, self.modelos)

    def test_a_slug_typed_by_hand_wins_over_the_generated_one(self):
        resposta = self.gravar(name="Luminárias", slug="luzes", is_active="on")
        self.assertEqual(Category.objects.get(pk=json.loads(resposta.content)["id"]).slug, "luzes")

    def test_saving_writes_the_admin_history(self):
        antes = LogEntry.objects.count()
        self.gravar(name="Papelaria", is_active="on")
        self.assertEqual(LogEntry.objects.count(), antes + 1)

    def test_get_is_not_accepted(self):
        self.assertEqual(self.client.get(GRAVAR).status_code, 405)


# ---------------------------------------------------------------------------
# Alternar, excluir e ações em massa
# ---------------------------------------------------------------------------


class AcoesTests(AdminBase):
    def alternar(self, categoria):
        url = reverse("admin:categories_category_toggle", args=[categoria.pk])
        return self.client.post(url)

    def excluir(self, categoria):
        url = reverse("admin:categories_category_modal_delete", args=[categoria.pk])
        return self.client.post(url)

    def test_the_pill_toggles_the_category(self):
        resposta = self.alternar(self.gatos)
        self.assertFalse(json.loads(resposta.content)["is_active"])
        self.gatos.refresh_from_db()
        self.assertFalse(self.gatos.is_active)
        self.alternar(self.gatos)
        self.gatos.refresh_from_db()
        self.assertTrue(self.gatos.is_active)

    def test_deleting_a_free_category(self):
        livre = categoria("livre", "Livre")
        resposta = self.excluir(livre)
        self.assertTrue(json.loads(resposta.content)["ok"])
        self.assertFalse(Category.objects.filter(pk=livre.pk).exists())

    def test_deleting_a_category_with_children_is_refused_with_the_reason(self):
        resposta = self.excluir(self.animais)
        self.assertEqual(resposta.status_code, 400)
        detalhe = json.loads(resposta.content)["detail"]
        self.assertIn("subcategoria", detalhe)
        self.assertTrue(Category.objects.filter(pk=self.animais.pk).exists())

    def test_deleting_a_category_with_products_is_refused(self):
        make_product(sku="P1", name="Vaso", category=self.gatos)
        resposta = self.excluir(self.gatos)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("produto", json.loads(resposta.content)["detail"])
        self.assertTrue(Category.objects.filter(pk=self.gatos.pk).exists())

    def test_bulk_activate_and_deactivate(self):
        resposta = self.client.post(MASSA, {"action": "deactivate", "ids": [self.gatos.pk, self.brinquedos.pk]})
        self.assertTrue(json.loads(resposta.content)["ok"])
        self.gatos.refresh_from_db()
        self.assertFalse(self.gatos.is_active)

        self.client.post(MASSA, {"action": "activate", "ids": [self.gatos.pk, self.brinquedos.pk]})
        self.gatos.refresh_from_db()
        self.brinquedos.refresh_from_db()
        self.assertTrue(self.gatos.is_active and self.brinquedos.is_active)

    def test_bulk_delete_keeps_what_it_cannot_remove_and_says_why(self):
        livre = categoria("livre", "Livre")
        resposta = self.client.post(MASSA, {"action": "delete", "ids": [livre.pk, self.animais.pk]})
        corpo = json.loads(resposta.content)
        self.assertEqual(corpo["done"], 1)
        self.assertIn("Animais", corpo["message"])
        self.assertFalse(Category.objects.filter(pk=livre.pk).exists())
        self.assertTrue(Category.objects.filter(pk=self.animais.pk).exists())

    def test_a_bulk_without_action_or_without_ids_is_refused(self):
        for dados in ({"action": "", "ids": [self.gatos.pk]}, {"action": "delete"}, {"action": "voar", "ids": [self.gatos.pk]}):
            with self.subTest(dados=dados):
                self.assertEqual(self.client.post(MASSA, dados).status_code, 400)

    def test_the_history_page_lists_what_the_admin_recorded(self):
        self.gravar(name="Papelaria", is_active="on")
        resposta = self.client.get(HISTORICO)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Histórico das categorias", resposta.content.decode())
        self.assertTrue(resposta.context["jd_log"])


# ---------------------------------------------------------------------------
# Permissões e CSRF
# ---------------------------------------------------------------------------


class PermissaoTests(AdminBase):
    def operador(self, *codenames):
        user = User.objects.create_user("operador", "op@jdprint.test", self.SENHA, is_staff=True)
        user.user_permissions.set(
            Permission.objects.filter(codename__in=codenames, content_type__app_label="categories")
        )
        cliente = Client()
        cliente.force_login(user)
        return cliente

    def test_who_can_only_view_sees_the_list_without_the_buttons(self):
        cliente = self.operador("view_category")
        resposta = cliente.get(LISTA)
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["jd_can_add"])
        self.assertFalse(resposta.context["jd_can_change"])
        self.assertNotIn("+ Nova categoria", resposta.content.decode())

    def test_who_cannot_add_cannot_create_even_forcing_the_post(self):
        cliente = self.operador("view_category")
        resposta = cliente.post(GRAVAR, {"name": "Intrusa", "sort_order": "0"})
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Category.objects.filter(translations__name="Intrusa").exists())

    def test_who_cannot_change_cannot_toggle_or_edit(self):
        cliente = self.operador("view_category", "add_category")
        url = reverse("admin:categories_category_toggle", args=[self.gatos.pk])
        self.assertEqual(cliente.post(url).status_code, 403)
        resposta = cliente.post(GRAVAR, {"category_id": self.gatos.pk, "name": "Outro", "sort_order": "0"})
        self.assertEqual(resposta.status_code, 403)
        self.gatos.refresh_from_db()
        self.assertTrue(self.gatos.is_active)

    def test_who_cannot_delete_cannot_delete(self):
        livre = categoria("livre", "Livre")
        cliente = self.operador("view_category", "change_category")
        url = reverse("admin:categories_category_modal_delete", args=[livre.pk])
        self.assertEqual(cliente.post(url).status_code, 403)
        self.assertEqual(cliente.post(MASSA, {"action": "delete", "ids": [livre.pk]}).status_code, 403)
        self.assertTrue(Category.objects.filter(pk=livre.pk).exists())

    def test_a_visitor_is_sent_to_the_admin_login(self):
        cliente = Client()
        for url in (LISTA, GRAVAR, MASSA, HISTORICO):
            with self.subTest(url=url):
                resposta = cliente.get(url)
                self.assertEqual(resposta.status_code, 302)
                self.assertIn("/admin/login/", resposta["Location"])

    def test_csrf_is_enforced(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.chefe)
        self.assertEqual(cliente.post(GRAVAR, {"name": "Sem token"}).status_code, 403)
        self.assertEqual(cliente.post(MASSA, {"action": "activate", "ids": [self.gatos.pk]}).status_code, 403)


# ---------------------------------------------------------------------------
# Hierarquia de qualquer profundidade
# ---------------------------------------------------------------------------


class HierarquiaProfundaTests(AdminBase):
    """A árvore não tem dois níveis.

    O desenho de referência desta tela mostrava categoria e subcategoria; a
    loja tem «Impressões 3D › Animais › Gatos › Raças › Persas». Estes testes
    fixam que a lista, o modal e a aba de subcategorias acompanham qualquer
    nível — e que o único limite é o do modelo (`MAX_CATEGORY_DEPTH`), que
    existe desde antes desta tela para não deixar nascer um menu absurdo.
    """

    def setUp(self):
        super().setUp()
        self.impressoes = categoria("impressoes", "Impressões 3D", ordem=0)
        self.nivel2 = categoria("animais-3d", "Animais 3D", parent=self.impressoes)
        self.nivel3 = categoria("gatos-3d", "Gatos 3D", parent=self.nivel2)
        self.nivel4 = categoria("racas", "Raças", parent=self.nivel3)
        self.nivel5 = categoria("persas", "Persas", parent=self.nivel4)

    def linha_de(self, resposta, nome):
        return next(l for l in resposta.context["jd_rows"] if l["name"] == nome)

    def test_the_five_levels_are_stored_with_their_real_depth(self):
        self.assertEqual(
            [c.depth for c in (self.impressoes, self.nivel2, self.nivel3, self.nivel4, self.nivel5)],
            [0, 1, 2, 3, 4],
        )
        self.assertEqual(
            self.nivel5.full_path(" > "),
            "Impressões 3D > Animais 3D > Gatos 3D > Raças > Persas",
        )

    def test_the_list_renders_every_level_in_order_and_indented(self):
        resposta = self.lista()
        nomes = [n for n, _d in self.linhas(resposta)]
        esperado = ["Impressões 3D", "Animais 3D", "Gatos 3D", "Raças", "Persas"]
        posicoes = [nomes.index(n) for n in esperado]
        self.assertEqual(posicoes[1:], [p + 1 for p in posicoes[:-1]])

        for nivel, nome in enumerate(esperado):
            with self.subTest(nivel=nivel, nome=nome):
                linha = self.linha_de(resposta, nome)
                self.assertEqual(linha["depth"], nivel)
                self.assertEqual(linha["indent"], 16 + nivel * 28)

    def test_every_level_but_the_last_says_it_has_children(self):
        resposta = self.lista()
        for nome in ("Impressões 3D", "Animais 3D", "Gatos 3D", "Raças"):
            with self.subTest(nome=nome):
                self.assertTrue(self.linha_de(resposta, nome)["has_children"])
        self.assertFalse(self.linha_de(resposta, "Persas")["has_children"])

    def test_a_deep_search_still_brings_the_whole_branch(self):
        nomes = [n for n, _d in self.linhas(self.lista(q="persas"))]
        self.assertEqual(nomes, ["Impressões 3D", "Animais 3D", "Gatos 3D", "Raças", "Persas"])

    def test_the_parent_select_offers_every_level_not_only_the_roots(self):
        opcoes = self.lista().context["jd_parents"]
        por_id = {o["id"]: o for o in opcoes}
        niveis = (self.impressoes, self.nivel2, self.nivel3, self.nivel4, self.nivel5)
        for nivel, alvo in enumerate(niveis):
            with self.subTest(nivel=nivel):
                self.assertIn(alvo.pk, por_id)
                self.assertEqual(por_id[alvo.pk]["depth"], nivel)
        self.assertEqual(por_id[self.nivel3.pk]["name"], "— — Gatos 3D")

    def test_creating_a_child_of_a_child_of_a_child(self):
        """Um quarto nível nasce pelo modal como qualquer outro."""
        resposta = self.gravar(name="Siameses", parent=self.nivel4.pk, is_active="on")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        nova = Category.objects.get(pk=json.loads(resposta.content)["id"])
        self.assertEqual(nova.parent, self.nivel4)
        self.assertEqual(nova.depth, 4)
        nomes = [n for n, _d in self.linhas(self.lista(q="siameses"))]
        self.assertEqual(nomes, ["Impressões 3D", "Animais 3D", "Gatos 3D", "Raças", "Siameses"])
        # e ele aparece na aba de subcategorias do pai, ao lado do irmão
        filhos = [c["name"] for c in self.dados(self.nivel4.pk)["children"]]
        self.assertEqual(sorted(filhos), ["Persas", "Siameses"])

    def test_the_children_tab_shows_the_direct_children_at_any_level(self):
        dados = self.dados(self.nivel3.pk)
        self.assertEqual([c["name"] for c in dados["children"]], ["Raças"])
        self.assertEqual(dados["kicker"], "ANIMAIS 3D ›")
        self.assertEqual([c["name"] for c in self.dados(self.nivel4.pk)["children"]], ["Persas"])

    def test_moving_a_branch_to_another_level_is_allowed(self):
        resposta = self.gravar(
            category_id=self.nivel4.pk, name="Raças", parent=self.nivel2.pk, is_active="on"
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.nivel4.refresh_from_db()
        self.nivel5.refresh_from_db()
        self.assertEqual(self.nivel4.parent, self.nivel2)
        self.assertEqual(self.nivel4.depth, 2)
        self.assertEqual(self.nivel5.depth, 3)

    def test_promoting_a_deep_category_to_the_root_is_allowed(self):
        resposta = self.gravar(category_id=self.nivel3.pk, name="Gatos 3D", parent="", is_active="on")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.nivel3.refresh_from_db()
        self.assertIsNone(self.nivel3.parent)
        self.assertEqual(self.nivel3.depth, 0)

    def test_a_cycle_is_refused_at_any_distance(self):
        casos = (
            (self.impressoes, self.nivel5, "raiz sob o seu tataraneto"),
            (self.nivel2, self.nivel4, "avô sob o neto"),
            (self.nivel3, self.nivel3, "ela mesma"),
        )
        for alvo, pai, caso in casos:
            with self.subTest(caso=caso):
                antes = Category.objects.get(pk=alvo.pk).parent_id
                resposta = self.gravar(
                    category_id=alvo.pk, name=alvo.name_in("pt"), parent=pai.pk, is_active="on"
                )
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("parent", json.loads(resposta.content)["errors"])
                self.assertEqual(Category.objects.get(pk=alvo.pk).parent_id, antes)

    def test_the_model_guard_against_absurd_depth_still_answers(self):
        """O limite é do modelo, não desta tela — e chega como mensagem, não como erro 500."""
        from apps.categories.models import MAX_CATEGORY_DEPTH

        self.assertEqual(MAX_CATEGORY_DEPTH, 5)
        atual = self.nivel5
        for n in range(2):
            resposta = self.gravar(name=f"Nível extra {n}", parent=atual.pk, is_active="on")
            if resposta.status_code == 400:
                self.assertIn("parent", json.loads(resposta.content)["errors"])
                return
            atual = Category.objects.get(pk=json.loads(resposta.content)["id"])
        self.fail("a profundidade máxima do modelo não foi aplicada")


# ---------------------------------------------------------------------------
# A ficha completa continua existindo
# ---------------------------------------------------------------------------


class FichaCompletaTests(AdminBase):
    def test_the_change_form_still_opens_and_saves(self):
        url = reverse("admin:categories_category_change", args=[self.gatos.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_add_form_still_opens(self):
        self.assertEqual(self.client.get(reverse("admin:categories_category_add")).status_code, 200)

    def test_duplicating_still_works(self):
        resposta = self.client.post(
            LISTA,
            {"action": "duplicate_action", "index": "0", "_selected_action": [str(self.gatos.pk)]},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/add/", resposta["Location"])
