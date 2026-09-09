"""Etapa 4C.2 — «Copiar de…»: os textos de um produto para outro.

O catálogo tem famílias inteiras que só mudam de assunto — «Pets — Kit para
Colorir», «Dinossauros — Kit para Colorir» — e o texto de apresentação é o
mesmo em quatro idiomas. Esta ação copia esse texto de um produto para outro.

O que os testes fixam:

* **por idioma, e sem lista fixa.** Copia o que a origem tiver: quatro idiomas
  hoje, cinco amanhã, sem tocar em código. Um idioma que existe no destino e
  não na origem **fica como está** — sobrescrever com vazio apagaria trabalho;
* **cópia independente.** Nada de vínculo, herança ou biblioteca de textos:
  cada tradução é linha própria do destino, e editar um lado depois não mexe
  no outro;
* **é cópia de texto, não duplicação de produto.** SKU, preço, estoque,
  variantes, categoria, opções e o **nome** do destino ficam intactos;
* **segurança.** Só quem pode alterar o destino copia; origem e destino são
  procurados no queryset do usuário; CSRF exigido; a operação é atômica.
"""

import json
from decimal import Decimal
from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import (
    Material,
    Product,
    ProductMaterialComposition,
    ProductOption,
    ProductOptionValue,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
    copy_product_content,
    product_content_copy_plan,
)
from apps.core.models import SiteLanguage
from apps.core.testing import make_category, make_product

RICO = (
    "<h2>Como usar</h2><p>Pinte com <strong>tinta acrílica</strong>.</p>"
    "<ul><li>Deixe secar</li></ul>"
    '<p style="text-align: center"><span style="color: #4a1a8c">Divirta-se</span></p>'
)


def traduzir(produto, idioma, nome, curta="", descricao="", extra=""):
    ProductTranslation.objects.update_or_create(
        master=produto,
        language=idioma,
        defaults={
            "name": nome,
            "short_description": curta,
            "description": descricao,
            "extra_information": extra,
        },
    )
    produto.refresh_translations()


class CopyBase(TestCase):
    SENHA = "senha-de-teste-77"

    def setUp(self):
        self.categoria = make_category(slug="kits", name="Kits")
        self.chefe = User.objects.create_superuser("chefe", "chefe@jdprint.test", self.SENHA)
        self.client.force_login(self.chefe)

        self.origem = make_product(sku="KIT-PETS", name="Pets — Kit para Colorir", category=self.categoria)
        traduzir(self.origem, "pt", "Pets — Kit para Colorir", "Kit de pets", RICO, "Cuidados PT")
        traduzir(self.origem, "fr", "Pets — Kit à colorier", "Kit chiens", "<p>Peignez</p>", "Soins FR")
        traduzir(self.origem, "nl", "Pets — Kleurset", "Huisdieren", "<p>Kleuren</p>", "Zorg NL")

        self.destino = make_product(sku="KIT-DINO", name="Dinossauros — Kit para Colorir", category=self.categoria)
        traduzir(self.destino, "pt", "Dinossauros — Kit para Colorir", "Antigo curto", "<p>Antigo</p>", "Antigo extra")

    # -- endereços ---------------------------------------------------------

    def url_busca(self, produto=None):
        return reverse("admin:catalog_product_content_copy_search", args=[(produto or self.destino).pk])

    def url_plano(self, origem=None, destino=None):
        return reverse(
            "admin:catalog_product_content_copy_plan",
            args=[(destino or self.destino).pk, (origem or self.origem).pk],
        )

    def url_copia(self, produto=None):
        return reverse("admin:catalog_product_content_copy", args=[(produto or self.destino).pk])

    # -- atalhos -----------------------------------------------------------

    def buscar(self, termo="", produto=None):
        return json.loads(self.client.get(self.url_busca(produto), {"q": termo}).content)

    def plano(self, origem=None, destino=None):
        return json.loads(self.client.get(self.url_plano(origem, destino)).content)

    def copiar(self, origem=None, destino=None, **extra):
        dados = {"source_id": (origem or self.origem).pk}
        dados.update(extra)
        return self.client.post(self.url_copia(destino), dados)

    def textos(self, produto, idioma):
        t = produto.translations.get(language=idioma)
        return (t.name, t.short_description, t.description, t.extra_information)


# ---------------------------------------------------------------------------
# A ação na tela
# ---------------------------------------------------------------------------


class TelaTests(CopyBase):
    def ficha(self, produto=None):
        url = reverse("admin:catalog_product_change", args=[(produto or self.destino).pk])
        return self.client.get(url).content.decode()

    def test_the_action_is_offered_in_the_content_section(self):
        html = self.ficha()
        self.assertIn("Copiar de…", html)
        self.assertIn("data-copy-open", html)
        self.assertIn("data-copy-modal", html)
        self.assertIn(self.url_busca(), html)
        self.assertIn(self.url_copia(), html)
        # e continua sendo a mesma seção de conteúdo, com o que já existia
        self.assertIn("+ Adicionar idioma", html)

    def test_the_add_screen_does_not_offer_it(self):
        """Sem produto gravado não há destino: a cópia grava direto no banco."""
        html = self.client.get(reverse("admin:catalog_product_add")).content.decode()
        self.assertNotIn("data-copy-modal", html)
        self.assertIn("+ Adicionar idioma", html)


# ---------------------------------------------------------------------------
# Procurar a origem
# ---------------------------------------------------------------------------


class BuscaTests(CopyBase):
    def test_searching_by_name(self):
        dados = self.buscar("pets")
        self.assertTrue(dados["ok"])
        self.assertEqual([r["sku"] for r in dados["results"]], ["KIT-PETS"])
        item = dados["results"][0]
        self.assertEqual(item["name"], "Pets — Kit para Colorir")
        self.assertEqual(item["category"], "Kits")
        self.assertEqual(item["languages"], ["FR", "NL", "PT"])

    def test_searching_by_sku(self):
        self.assertEqual([r["sku"] for r in self.buscar("kit-pets")["results"]], ["KIT-PETS"])

    def test_searching_by_a_translated_name_in_another_language(self):
        self.assertEqual([r["sku"] for r in self.buscar("colorier")["results"]], ["KIT-PETS"])

    def test_the_product_itself_is_never_an_option(self):
        skus = [r["sku"] for r in self.buscar("kit")["results"]]
        self.assertIn("KIT-PETS", skus)
        self.assertNotIn("KIT-DINO", skus)

    def test_without_a_term_it_offers_the_recently_changed_ones(self):
        dados = self.buscar("")
        self.assertTrue(dados["results"])
        self.assertNotIn("KIT-DINO", [r["sku"] for r in dados["results"]])

    def test_nothing_found_is_an_empty_list_not_an_error(self):
        dados = self.buscar("jacaré-de-plutão")
        self.assertTrue(dados["ok"])
        self.assertEqual(dados["results"], [])

    def test_the_list_is_capped(self):
        for n in range(25):
            make_product(sku=f"MASSA-{n:02d}", name=f"Massa {n}", category=self.categoria)
        self.assertEqual(len(self.buscar("massa")["results"]), 20)


# ---------------------------------------------------------------------------
# O plano, antes de gravar
# ---------------------------------------------------------------------------


class PlanoTests(CopyBase):
    def test_the_plan_separates_replace_create_and_keep(self):
        traduzir(self.destino, "en", "Dinos — Colouring Kit", "Old EN", "<p>Old</p>")
        dados = self.plano()
        self.assertTrue(dados["ok"])
        self.assertEqual([i["code"] for i in dados["plan"]["replace"]], ["pt"])
        self.assertEqual([i["code"] for i in dados["plan"]["create"]], ["fr", "nl"])
        self.assertEqual([i["code"] for i in dados["plan"]["keep"]], ["en"])
        self.assertEqual(dados["source"]["name"], "Pets — Kit para Colorir")
        self.assertEqual(dados["target"]["name"], "Dinossauros — Kit para Colorir")
        self.assertFalse(dados["empty"])

    def test_the_plan_writes_nothing(self):
        antes = self.textos(self.destino, "pt")
        self.plano()
        self.assertEqual(self.textos(Product.objects.get(pk=self.destino.pk), "pt"), antes)
        self.assertEqual(self.destino.translations.count(), 1)

    def test_a_source_without_content_is_flagged_as_empty(self):
        vazio = Product.objects.create(sku="VAZIO", slug="vazio", category=self.categoria)
        dados = self.plano(origem=vazio)
        self.assertTrue(dados["empty"])

    def test_the_plan_of_a_product_that_does_not_exist_is_a_404(self):
        self.assertEqual(self.client.get(
            reverse("admin:catalog_product_content_copy_plan", args=[self.destino.pk, 999999])
        ).status_code, 404)


# ---------------------------------------------------------------------------
# A cópia
# ---------------------------------------------------------------------------


class CopiaTests(CopyBase):
    def test_one_language_is_copied_without_touching_the_name(self):
        resposta = self.copiar()
        self.assertEqual(resposta.status_code, 200, resposta.content)
        nome, curta, descricao, extra = self.textos(Product.objects.get(pk=self.destino.pk), "pt")
        self.assertEqual(nome, "Dinossauros — Kit para Colorir")  # a identidade não muda
        self.assertEqual((curta, descricao, extra), ("Kit de pets", RICO, "Cuidados PT"))

    def test_every_language_of_the_source_is_copied(self):
        self.copiar()
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(sorted(destino.translations.values_list("language", flat=True)), ["fr", "nl", "pt"])
        self.assertEqual(self.textos(destino, "fr")[1:], ("Kit chiens", "<p>Peignez</p>", "Soins FR"))
        self.assertEqual(self.textos(destino, "nl")[1:], ("Huisdieren", "<p>Kleuren</p>", "Zorg NL"))

    def test_a_new_language_row_starts_with_the_targets_portuguese_name(self):
        self.copiar()
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(destino.translations.get(language="fr").name, "Dinossauros — Kit para Colorir")

    def test_a_language_missing_from_the_source_is_preserved(self):
        traduzir(self.destino, "en", "Dinos — Colouring Kit", "Meu EN", "<p>Meu texto</p>", "Meu extra")
        resposta = self.copiar()
        self.assertEqual(resposta.status_code, 200)
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(self.textos(destino, "en"), ("Dinos — Colouring Kit", "Meu EN", "<p>Meu texto</p>", "Meu extra"))
        corpo = json.loads(resposta.content)
        self.assertEqual(corpo["copied"], 3)
        self.assertIn("preservada", corpo["message"])

    def test_a_target_without_any_translation_receives_them_all(self):
        novo = Product.objects.create(sku="NOVO", slug="novo", category=self.categoria)
        traduzir(novo, "pt", "Novo produto")
        novo.translations.all().delete()
        novo.refresh_translations()
        # sem tradução nenhuma, o nome de destino cai no SKU
        copy_product_content(self.origem, novo)
        novo.refresh_translations()
        self.assertEqual(sorted(novo.translations.values_list("language", flat=True)), ["fr", "nl", "pt"])
        self.assertEqual(novo.translations.get(language="pt").name, "NOVO")

    def test_the_rich_text_survives_exactly(self):
        self.copiar()
        copiado = Product.objects.get(pk=self.destino.pk).translations.get(language="pt").description
        self.assertEqual(copiado, RICO)
        for pedaco in ("<h2>Como usar</h2>", "<strong>tinta acrílica</strong>", "<li>Deixe secar</li>",
                       "text-align: center", "color: #4a1a8c"):
            with self.subTest(pedaco=pedaco):
                self.assertIn(pedaco, copiado)

    def test_a_language_added_later_needs_no_code_change(self):
        SiteLanguage.objects.update_or_create(code="de", defaults={"is_active": True, "sort_order": 9})
        traduzir(self.origem, "de", "Pets — Malset", "Haustiere", "<p>Malen</p>", "Pflege DE")
        self.copiar()
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertIn("de", destino.translations.values_list("language", flat=True))
        self.assertEqual(self.textos(destino, "de")[1:], ("Haustiere", "<p>Malen</p>", "Pflege DE"))

    def test_the_message_counts_what_happened(self):
        corpo = json.loads(self.copiar().content)
        self.assertIn("Pets — Kit para Colorir", corpo["message"])
        self.assertIn("3 tradução(ões) copiada(s)", corpo["message"])

    def test_copying_from_an_empty_product_is_refused(self):
        vazio = Product.objects.create(sku="VAZIO", slug="vazio", category=self.categoria)
        resposta = self.copiar(origem=vazio)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("não tem conteúdo", json.loads(resposta.content)["detail"])


# ---------------------------------------------------------------------------
# Independência: nenhum vínculo depois da cópia
# ---------------------------------------------------------------------------


class IndependenciaTests(CopyBase):
    def test_editing_the_target_afterwards_does_not_touch_the_source(self):
        self.copiar()
        destino = Product.objects.get(pk=self.destino.pk)
        traducao = destino.translations.get(language="pt")
        traducao.description = "<p>Texto só dos dinossauros</p>"
        traducao.save()

        origem = Product.objects.get(pk=self.origem.pk)
        self.assertEqual(origem.translations.get(language="pt").description, RICO)

    def test_editing_the_source_afterwards_does_not_touch_the_target(self):
        self.copiar()
        origem = Product.objects.get(pk=self.origem.pk)
        traducao = origem.translations.get(language="pt")
        traducao.description = "<p>Texto só dos pets</p>"
        traducao.save()

        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(destino.translations.get(language="pt").description, RICO)

    def test_the_rows_are_the_targets_own(self):
        self.copiar()
        da_origem = set(self.origem.translations.values_list("pk", flat=True))
        do_destino = set(Product.objects.get(pk=self.destino.pk).translations.values_list("pk", flat=True))
        self.assertFalse(da_origem & do_destino)
        for traducao in Product.objects.get(pk=self.destino.pk).translations.all():
            self.assertEqual(traducao.master_id, self.destino.pk)

    def test_deleting_the_source_leaves_the_target_intact(self):
        self.copiar()
        Product.objects.filter(pk=self.origem.pk).delete()
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(destino.translations.count(), 3)
        self.assertEqual(destino.translations.get(language="pt").description, RICO)


# ---------------------------------------------------------------------------
# Nada além dos textos
# ---------------------------------------------------------------------------


class EscopoTests(CopyBase):
    def retrato(self, produto):
        produto = Product.objects.get(pk=produto.pk)
        return {
            "sku": produto.sku,
            "slug": produto.slug,
            "status": produto.status,
            "category": produto.category_id,
            "variantes": sorted(produto.variants.values_list("sku", "stock_quantity", "sale_price")),
            "opcoes": sorted(produto.options.values_list("name", flat=True)),
            "materiais": sorted(produto.material_composition.values_list("material_id", flat=True)),
            "midia": produto.media.count(),
            "personalizacao": produto.personalization_type,
        }

    def test_nothing_commercial_changes(self):
        material = Material.objects.create(name="PLA")
        ProductMaterialComposition.objects.create(product=self.destino, material=material)
        opcao = ProductOption.objects.create(product=self.destino, name="Instalação")
        ProductOptionValue.objects.create(option=opcao, name="Mesa")
        variante = self.destino.default_variant
        variante.stock_quantity = 7
        variante.sale_price = Decimal("19.90")
        variante.save()

        antes = self.retrato(self.destino)
        self.copiar()
        self.assertEqual(self.retrato(self.destino), antes)

    def test_the_source_is_not_changed_either(self):
        antes = self.retrato(self.origem)
        traducoes = sorted(self.origem.translations.values_list("pk", "language", "name", "description"))
        self.copiar()
        self.assertEqual(self.retrato(self.origem), antes)
        self.assertEqual(
            sorted(Product.objects.get(pk=self.origem.pk).translations.values_list("pk", "language", "name", "description")),
            traducoes,
        )

    def test_the_slug_is_not_regenerated(self):
        slug = self.destino.slug
        self.copiar()
        self.assertEqual(Product.objects.get(pk=self.destino.pk).slug, slug)


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------


class SegurancaTests(CopyBase):
    def operador(self, *codenames):
        u = User.objects.create_user("operador", "op@jdprint.test", self.SENHA, is_staff=True)
        u.user_permissions.set(
            Permission.objects.filter(codename__in=codenames, content_type__app_label="catalog")
        )
        c = Client()
        c.force_login(u)
        return c

    def test_a_staff_user_without_change_permission_cannot_copy(self):
        cliente = self.operador("view_product")
        antes = self.textos(self.destino, "pt")
        for resposta in (
            cliente.get(self.url_busca()),
            cliente.get(self.url_plano()),
            cliente.post(self.url_copia(), {"source_id": self.origem.pk}),
        ):
            with self.subTest(url=resposta.request["PATH_INFO"]):
                self.assertEqual(resposta.status_code, 403)
        self.assertEqual(self.textos(Product.objects.get(pk=self.destino.pk), "pt"), antes)

    def test_an_anonymous_visitor_is_sent_to_the_login(self):
        cliente = Client()
        self.assertEqual(cliente.post(self.url_copia(), {"source_id": self.origem.pk}).status_code, 302)
        self.assertEqual(Product.objects.get(pk=self.destino.pk).translations.count(), 1)

    def test_copying_to_itself_is_refused(self):
        antes = self.textos(self.destino, "pt")
        resposta = self.copiar(origem=self.destino)
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.textos(Product.objects.get(pk=self.destino.pk), "pt"), antes)
        with self.assertRaises(ValueError):
            copy_product_content(self.destino, self.destino)

    def test_a_source_that_does_not_exist_is_a_404(self):
        self.assertEqual(self.copiar(origem=None, source_id=999999).status_code, 404)

    def test_a_target_that_does_not_exist_is_a_404(self):
        url = reverse("admin:catalog_product_content_copy", args=[999999])
        self.assertEqual(self.client.post(url, {"source_id": self.origem.pk}).status_code, 404)

    def test_a_source_id_that_is_not_a_number_is_refused(self):
        for lixo in ("abc", "", "1 OR 1=1", "-1"):
            with self.subTest(lixo=lixo):
                resposta = self.client.post(self.url_copia(), {"source_id": lixo})
                self.assertIn(resposta.status_code, (400, 404))
        self.assertEqual(Product.objects.get(pk=self.destino.pk).translations.count(), 1)

    def test_the_copy_requires_a_post(self):
        self.assertEqual(self.client.get(self.url_copia()).status_code, 405)

    def test_csrf_is_enforced(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.chefe)
        resposta = cliente.post(self.url_copia(), {"source_id": self.origem.pk})
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(Product.objects.get(pk=self.destino.pk).translations.count(), 1)


# ---------------------------------------------------------------------------
# Transação e histórico
# ---------------------------------------------------------------------------


class TransacaoTests(CopyBase):
    def test_a_failure_in_the_middle_leaves_nothing_copied(self):
        antes = self.textos(self.destino, "pt")
        with mock.patch(
            "apps.catalog.models.ProductTranslation.objects.create",
            side_effect=RuntimeError("banco caiu"),
        ):
            with self.assertRaises(RuntimeError):
                self.copiar()
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(destino.translations.count(), 1)  # nenhuma nasceu
        self.assertEqual(self.textos(destino, "pt"), antes)  # nem a que seria substituída

    def test_the_operation_is_recorded_in_the_admin_history(self):
        self.copiar()
        registro = LogEntry.objects.filter(object_id=str(self.destino.pk)).order_by("-id").first()
        self.assertIsNotNone(registro)
        self.assertIn("Descrições copiadas de KIT-PETS", registro.change_message)
        self.assertIn("3 tradução(ões)", registro.change_message)

    def test_copying_twice_is_simply_copying_again(self):
        self.copiar()
        traducao = Product.objects.get(pk=self.destino.pk).translations.get(language="pt")
        traducao.description = "<p>Alterado à mão</p>"
        traducao.save()

        self.copiar()
        destino = Product.objects.get(pk=self.destino.pk)
        self.assertEqual(destino.translations.count(), 3)
        self.assertEqual(destino.translations.get(language="pt").description, RICO)


# ---------------------------------------------------------------------------
# O serviço, sem passar pela tela
# ---------------------------------------------------------------------------


class ServicoTests(CopyBase):
    def test_the_plan_is_the_same_the_copy_applies(self):
        traduzir(self.destino, "en", "Dinos", "EN")
        plano = product_content_copy_plan(self.origem, self.destino)
        self.assertEqual(plano, {"replace": ["pt"], "create": ["fr", "nl"], "keep": ["en"]})
        self.assertEqual(copy_product_content(self.origem, self.destino), plano)

    def test_it_does_not_touch_the_name_field_list(self):
        from apps.catalog.models import CONTENT_COPY_FIELDS

        self.assertNotIn("name", CONTENT_COPY_FIELDS)
        self.assertEqual(
            set(CONTENT_COPY_FIELDS), {"short_description", "description", "extra_information"}
        )
