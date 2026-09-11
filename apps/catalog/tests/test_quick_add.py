"""Etapa 1 do cadastro de produtos: cadastrar primeiro, detalhar depois.

O que estes testes guardam:

1. o SKU sugerido segue a regra (categoria + primeira palavra do nome +
   sequência), sem acento, único e incremental — e o digitado à mão vence;
2. o cadastro rápido cria o produto (rascunho, sem foto, sem preço, sem
   variante, sem outros idiomas) com o nome em português e o slug de sempre;
3. a colisão de SKU no meio do caminho é resolvida com a sequência seguinte;
4. a variante nova ganha PRODUTO-V01, V02…; a existente nunca é renomeada;
5. a lista mostra as colunas operacionais sem uma consulta por linha;
6. ativar/desativar pela lista respeita as regras de ativação;
7. o que já existia continua exatamente igual.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.catalog import sku as sku_rules
from apps.catalog.models import PricingMode, Product, ProductStatus, ProductTranslation, ProductVariant
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_user, make_variant


def payload(prefix, rows, initial=0):
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


class SkuRulesTests(TestCase):
    def setUp(self):
        self.religioso = make_category(slug="religioso", name="Religioso")
        self.decoracao = make_category(slug="decoracao", name="Decoração")
        self.animais = make_category(slug="animais", name="Animais")

    def test_prefix_and_stem(self):
        self.assertEqual(sku_rules.suggest_product_sku(self.religioso, "Leão de Judá"), "REL-LEAO-001")
        self.assertEqual(sku_rules.suggest_product_sku(self.religioso, "Jesus Cristo"), "REL-JESUS-001")
        self.assertEqual(sku_rules.suggest_product_sku(self.decoracao, "Vaso Decorativo"), "DEC-VASO-001")
        self.assertEqual(
            sku_rules.suggest_product_sku(self.animais, "Dinossauro Parasaurolophus"), "ANI-DINOSSAURO-001"
        )
        self.assertEqual(sku_rules.suggest_product_sku(self.animais, "Parasaurolophus"), "ANI-PARASAUROLOPHUS-001")

    def test_accents_and_special_characters_are_normalized(self):
        self.assertEqual(sku_rules.category_prefix(self.decoracao), "DEC")
        self.assertEqual(sku_rules.name_stem("Coração & Flechas!"), "CORACAO")
        self.assertEqual(sku_rules.name_stem("  ção  "), "CAO")

    def test_without_category_or_name_there_is_still_a_sku(self):
        self.assertEqual(sku_rules.suggest_product_sku(None, "Vaso"), "PRD-VASO-001")
        self.assertEqual(sku_rules.suggest_product_sku(self.animais, "de"), "ANI-DE-001")
        self.assertEqual(sku_rules.suggest_product_sku(self.animais, "!!!"), "ANI-ITEM-001")

    def test_sequence_is_the_highest_used_plus_one(self):
        make_product(sku="REL-LEAO-001", name="Leão", category=self.religioso, with_variant=False)
        self.assertEqual(sku_rules.suggest_product_sku(self.religioso, "Leão de Judá"), "REL-LEAO-002")
        # Um número alto não é "preenchido por baixo": a sequência só avança.
        make_product(sku="REL-LEAO-007", name="Leão 7", category=self.religioso, with_variant=False)
        self.assertEqual(sku_rules.suggest_product_sku(self.religioso, "Leão"), "REL-LEAO-008")
        # Outro miolo, outra sequência.
        self.assertEqual(sku_rules.suggest_product_sku(self.religioso, "Jesus"), "REL-JESUS-001")

    def test_reserved_skus_count(self):
        self.assertEqual(
            sku_rules.suggest_product_sku(self.religioso, "Leão", reserved={"REL-LEAO-001"}), "REL-LEAO-002"
        )

    def test_variant_sequence(self):
        produto = make_product(sku="REL-LEAO-001", name="Leão", category=self.religioso, with_variant=False)
        self.assertEqual(sku_rules.suggest_variant_sku(produto.sku), "REL-LEAO-001-V01")
        make_variant(produto, sku="REL-LEAO-001-V01")
        self.assertEqual(sku_rules.suggest_variant_sku(produto.sku), "REL-LEAO-001-V02")
        # A variante com SKU manual não entra na sequência — e não é renomeada.
        make_variant(produto, sku="LEAO-PRETO", size="20 cm")
        self.assertEqual(sku_rules.suggest_variant_sku(produto.sku), "REL-LEAO-001-V02")

    def test_a_sku_belongs_to_the_family_of_a_name(self):
        """É como o servidor reconhece o SKU que ele mesmo sugeriu."""
        self.assertTrue(sku_rules.matches_suggested_base("REL-LEAO-007", self.religioso, "Leão de Judá"))
        self.assertTrue(sku_rules.matches_suggested_base("rel-leao-001", self.religioso, "Leão de Judá"))
        self.assertFalse(sku_rules.matches_suggested_base("MEU-CODIGO", self.religioso, "Leão de Judá"))
        self.assertFalse(sku_rules.matches_suggested_base("REL-LEAO", self.religioso, "Leão de Judá"))
        self.assertFalse(sku_rules.matches_suggested_base("ANI-LEAO-001", self.religioso, "Leão de Judá"))

    def test_collision_retries_with_the_next_sequence(self):
        """Dois cadastros ao mesmo tempo: o banco recusa o segundo, e ele tenta o próximo."""
        tentativas = []

        def sugerir(reservados):
            # A primeira sugestão "já foi gravada por outra pessoa" no meio do caminho.
            sku = sku_rules.suggest_product_sku(self.religioso, "Leão", reserved=reservados)
            if not tentativas:
                Product.objects.create(sku=sku)  # a outra pessoa
            tentativas.append(sku)
            return sku

        def criar(sku):
            return Product.objects.create(sku=sku, category=self.religioso)

        produto = sku_rules.com_sku_livre(sugerir, criar)

        self.assertEqual(tentativas, ["REL-LEAO-001", "REL-LEAO-002"])
        self.assertEqual(produto.sku, "REL-LEAO-002")
        self.assertEqual(Product.objects.filter(sku__startswith="REL-LEAO-").count(), 2)

    def test_other_integrity_errors_are_not_swallowed(self):
        from django.db import IntegrityError

        def sugerir(reservados):
            return "X-001"

        def criar(sku):
            raise IntegrityError("NOT NULL constraint failed: catalog_product.currency")

        with self.assertRaises(IntegrityError):
            sku_rules.com_sku_livre(sugerir, criar, attempts=3)


class QuickAddBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.chefe = make_user("chefe", is_staff=True, is_superuser=True, with_customer=False)
        self.client.force_login(self.chefe)
        self.religioso = make_category(slug="religioso", name="Religioso")
        self.url = reverse("admin:catalog_product_quick_add")

    def criar(self, **campos):
        dados = {"name": "Leão de Judá", "category": str(self.religioso.pk), "sku": "", "status": ProductStatus.DRAFT}
        dados.update(campos)
        return self.client.post(self.url, dados)


class QuickAddTests(QuickAddBase):
    def test_page_loads_with_the_four_fields(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        for campo in ('name="name"', 'name="category"', 'name="sku"', 'name="status"'):
            self.assertIn(campo, html)
        self.assertIn("Você pode alterar", html)
        self.assertIn(reverse("admin:catalog_product_sku_suggestion"), html)

    def test_category_field_is_the_admin_search(self):
        """O campo Categoria é o mesmo select2 da ficha, com o endpoint do Admin."""
        html = self.client.get(self.url).content.decode()

        self.assertIn("admin-autocomplete", html)
        # O widget aponta para a busca do próprio Admin, com o campo declarado
        # em `ProductAdmin.autocomplete_fields` — e por isso a view a autoriza.
        self.assertIn(reverse("admin:autocomplete"), html)
        self.assertIn('data-model-name="product"', html)
        self.assertIn('data-field-name="category"', html)
        self.assertIn("vendor/select2/select2.full", html)

    def test_category_search_answers_with_the_whole_path(self):
        """Digitar o nome do ramo acha a folha, e o resultado diz de onde ela vem."""
        gatos = make_category(slug="gatos", name="Gatos", parent=self.religioso)

        resposta = self.client.get(
            reverse("admin:autocomplete"),
            {"term": "gato", "app_label": "catalog", "model_name": "product", "field_name": "category"},
        )

        self.assertEqual(resposta.status_code, 200)
        achados = resposta.json()["results"]
        self.assertEqual([r["id"] for r in achados], [str(gatos.pk)])
        self.assertEqual(achados[0]["text"], "Religioso › Gatos")

    def test_duplicating_opens_with_the_category_already_chosen(self):
        """Na tela de duplicar, a categoria do modelo já vem escrita na caixa."""
        modelo = make_product(sku="REL-BASE-001", name="Base", category=self.religioso)

        html = self.client.get(self.url, {"_duplicar": modelo.pk}).content.decode()

        self.assertIn(f'value="{self.religioso.pk}" selected', html)
        self.assertIn("Religioso", html)

    def test_creates_a_draft_with_only_name_and_category(self):
        resposta = self.criar()
        self.assertRedirects(resposta, reverse("admin:catalog_product_changelist"))

        produto = Product.objects.get(sku="REL-LEAO-001")
        self.assertEqual(produto.status, ProductStatus.DRAFT)
        self.assertEqual(produto.category, self.religioso)
        self.assertEqual(produto.created_by, self.chefe)
        self.assertEqual(produto.name_in("pt"), "Leão de Judá")
        self.assertEqual(produto.translations.count(), 1)  # só o português
        self.assertEqual(produto.slug, "leao-de-juda")  # do nome, nunca do SKU
        self.assertEqual(produto.variants.count(), 0)
        self.assertEqual(produto.media.count(), 0)
        self.assertFalse(produto.is_sellable)
        self.assertEqual(produto.price_range, (None, None))

    def test_category_is_optional_for_a_draft(self):
        resposta = self.criar(category="", name="Vaso")
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(Product.objects.filter(sku="PRD-VASO-001").exists())

    def test_manual_sku_is_respected(self):
        self.criar(sku="lp-leao-2026")
        produto = Product.objects.get(sku="LP-LEAO-2026")
        self.assertEqual(produto.sku, "LP-LEAO-2026")
        self.assertFalse(Product.objects.filter(sku="REL-LEAO-001").exists())

    def test_duplicate_manual_sku_is_refused(self):
        make_product(sku="LP-LEAO-2026", name="Outro", with_variant=False)
        resposta = self.criar(sku="LP-LEAO-2026")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Já existe um produto com este SKU")

    def test_sequence_increments_across_quick_adds(self):
        self.criar()
        self.criar()
        self.assertEqual(
            sorted(Product.objects.values_list("sku", flat=True)), ["REL-LEAO-001", "REL-LEAO-002"]
        )

    def test_collision_at_save_time_takes_the_next_sequence(self):
        """A sugestão foi REL-LEAO-001, mas alguém gravou esse SKU antes do commit."""
        original = sku_rules.suggest_product_sku

        def sugerir_e_ser_atropelado(category, name, reserved=()):
            sku = original(category, name, reserved)
            if sku == "REL-LEAO-001":
                Product.objects.create(sku="REL-LEAO-001")
            return sku

        with mock.patch.object(sku_rules, "suggest_product_sku", side_effect=sugerir_e_ser_atropelado):
            resposta = self.criar()

        self.assertEqual(resposta.status_code, 302)
        criado = Product.objects.get(translations__name="Leão de Judá")
        self.assertEqual(criado.sku, "REL-LEAO-002")

    def test_optional_first_variant_gets_v01(self):
        self.criar(sale_price="29.90", stock_quantity="8")
        produto = Product.objects.get(sku="REL-LEAO-001")
        variante = produto.variants.get()
        self.assertEqual(variante.sku, "REL-LEAO-001-V01")
        self.assertEqual(variante.sale_price, Decimal("29.90"))
        self.assertEqual(variante.stock_quantity, 8)
        self.assertEqual(variante.pricing_mode, PricingMode.PRICE)
        self.assertTrue(variante.is_active)

    def test_active_without_price_is_refused(self):
        resposta = self.criar(status=ProductStatus.ACTIVE)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "precisa de uma variante com preço")
        self.assertEqual(Product.objects.count(), 0)

    def test_active_with_price_is_sellable_at_once(self):
        self.criar(status=ProductStatus.ACTIVE, sale_price="19.90")
        produto = Product.objects.get(sku="REL-LEAO-001")
        self.assertTrue(produto.is_sellable)

    def test_continue_opens_the_product_sheet(self):
        resposta = self.criar(_continue="1")
        produto = Product.objects.get(sku="REL-LEAO-001")
        self.assertRedirects(resposta, reverse("admin:catalog_product_change", args=[produto.pk]))

    def test_add_another_stays_on_the_quick_form(self):
        resposta = self.criar(_addanother="1")
        self.assertRedirects(resposta, self.url)

    def test_the_addition_is_logged(self):
        from django.contrib.admin.models import ADDITION, LogEntry

        self.criar()
        produto = Product.objects.get(sku="REL-LEAO-001")
        self.assertTrue(
            LogEntry.objects.filter(object_id=str(produto.pk), action_flag=ADDITION).exists()
        )

    def test_suggestion_endpoint(self):
        resposta = self.client.get(
            reverse("admin:catalog_product_sku_suggestion"),
            {"name": "Leão de Judá", "category": self.religioso.pk},
        )
        self.assertEqual(resposta.json(), {"sku": "REL-LEAO-001"})
        self.assertEqual(
            self.client.get(reverse("admin:catalog_product_sku_suggestion"), {"name": ""}).json(), {"sku": ""}
        )

    def test_requires_add_permission(self):
        leitor = make_user("leitor", is_staff=True, with_customer=False)
        leitor.user_permissions.add(Permission.objects.get(codename="view_product"))
        self.client.force_login(leitor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.criar().status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin:catalog_product_sku_suggestion"), {"name": "x"}).status_code, 403
        )
        self.assertEqual(Product.objects.count(), 0)

    def test_anonymous_is_sent_to_the_login(self):
        self.client.logout()
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("admin:login"), resposta["Location"])


class VariantSkuTests(QuickAddBase):
    def setUp(self):
        super().setUp()
        self.produto = make_product(sku="REL-LEAO-001", name="Leão", category=self.religioso, with_variant=False)

    def variante(self, **campos):
        dados = {"sku": "", "sort_order": "0", "is_active": "on", "pricing_mode": PricingMode.PRICE,
                 "sale_price": "10.00", "stock_quantity": "1", "dimension_unit": "mm"}
        dados.update(campos)
        return dados

    def test_modal_saves_a_new_variant_with_v01_then_v02(self):
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        primeira = self.client.post(url, self.variante()).json()
        self.assertTrue(primeira["ok"], primeira)
        self.assertEqual(primeira["fields"]["sku"], "REL-LEAO-001-V01")
        segunda = self.client.post(url, self.variante(size="20 cm")).json()
        self.assertEqual(segunda["fields"]["sku"], "REL-LEAO-001-V02")

    def test_manual_variant_sku_still_works(self):
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        resposta = self.client.post(url, self.variante(sku="leao-preto")).json()
        self.assertEqual(resposta["fields"]["sku"], "LEAO-PRETO")

    def test_editing_an_existing_variant_keeps_its_sku(self):
        variante = make_variant(self.produto, sku="LEAO-ANTIGO")
        url = reverse("admin:catalog_product_variant_save", args=[self.produto.pk])
        resposta = self.client.post(
            url, self.variante(variant_id=variante.pk, sku="LEAO-ANTIGO", sale_price="12.00")
        ).json()
        self.assertEqual(resposta["fields"]["sku"], "LEAO-ANTIGO")
        variante.refresh_from_db()
        self.assertEqual(variante.sku, "LEAO-ANTIGO")
        self.assertEqual(variante.sale_price, Decimal("12.00"))

    def test_product_form_inline_numbers_two_new_variants(self):
        """Duas variantes novas no mesmo salvar: V01 e V02, sem repetir."""
        url = reverse("admin:catalog_product_change", args=[self.produto.pk])
        dados = {
            "sku": self.produto.sku, "status": ProductStatus.DRAFT, "slug": self.produto.slug,
            "category": str(self.religioso.pk), "currency": "EUR", "featured_order": "0",
            "personalization_type": "none", "personalization_text_limit": "0",
            **payload("translations", [{"id": self.produto.translations.get().pk, "language": "pt", "name": "Leão"}], initial=1),
            **payload("variants", [self.variante(), self.variante(size="20 cm")]),
            **payload("media", []),
            **payload("product_colors", []),
            **payload("material_composition", []),
            "_save": "Salvar",
        }
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 302, resposta.content[:500] if resposta.status_code != 302 else "")
        self.assertEqual(
            sorted(self.produto.variants.values_list("sku", flat=True)), ["REL-LEAO-001-V01", "REL-LEAO-001-V02"]
        )

    def test_variant_admin_form_suggests_too(self):
        url = reverse("admin:catalog_productvariant_add")
        dados = self.variante(product=str(self.produto.pk), filament_cost="0", energy_cost="0")
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 302, resposta.content[:800] if resposta.status_code != 302 else "")
        self.assertTrue(ProductVariant.objects.filter(sku="REL-LEAO-001-V01").exists())


class FullFormSkuTests(QuickAddBase):
    """O formulário completo usa a mesma sugestão — só para produto novo."""

    def dados(self, nome="Leão de Judá", sku="", categoria=None, **extra):
        categoria = self.religioso if categoria is None else categoria
        dados = {
            "sku": sku, "status": ProductStatus.DRAFT, "slug": "",
            "category": str(categoria.pk) if categoria else "", "currency": "EUR", "featured_order": "0",
            "personalization_type": "none", "personalization_text_limit": "0",
            **payload("translations", [{"language": "pt", "name": nome}]),
            **payload("variants", []),
            **payload("media", []),
            **payload("product_colors", []),
            **payload("material_composition", []),
            "_save": "Salvar",
        }
        dados.update(extra)
        return dados

    def test_add_page_carries_the_live_suggestion(self):
        html = self.client.get(reverse("admin:catalog_product_add")).content.decode()
        self.assertIn('data-sku-suggest data-url="%s" data-mode="full"' % reverse("admin:catalog_product_sku_suggestion"), html)
        self.assertIn("admin/js/product_sku_suggest.js", html)
        self.assertIn("Em branco, é gerado a partir da categoria e do nome", html)
        # O campo SKU não é obrigatório no produto novo.
        self.assertNotIn('name="sku" class="vTextField" maxlength="64" required', html)

    def test_blank_sku_is_generated_from_category_and_portuguese_name(self):
        resposta = self.client.post(reverse("admin:catalog_product_add"), self.dados())
        self.assertEqual(resposta.status_code, 302, resposta.content[:600] if resposta.status_code != 302 else "")
        produto = Product.objects.get(translations__name="Leão de Judá")
        self.assertEqual(produto.sku, "REL-LEAO-001")
        self.assertEqual(produto.slug, "leao-de-juda")

        self.client.post(reverse("admin:catalog_product_add"), self.dados(nome="Jesus Cristo"))
        self.assertTrue(Product.objects.filter(sku="REL-JESUS-001").exists())

    def test_sequence_respects_existing_skus(self):
        for n in (1, 2, 3):
            make_product(sku=f"REL-LEAO-{n:03d}", name=f"Leão {n}", category=self.religioso, with_variant=False)
        self.client.post(reverse("admin:catalog_product_add"), self.dados())
        self.assertTrue(Product.objects.filter(sku="REL-LEAO-004").exists())

    def test_manual_sku_wins(self):
        resposta = self.client.post(reverse("admin:catalog_product_add"), self.dados(sku="lp-leao-2026"))
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(Product.objects.filter(sku="LP-LEAO-2026").exists())
        self.assertFalse(Product.objects.filter(sku="REL-LEAO-001").exists())

    def test_blank_sku_without_a_portuguese_name_is_an_error(self):
        dados = self.dados()
        dados.update(payload("translations", [{"language": "fr", "name": "Lion de Juda"}]))
        resposta = self.client.post(reverse("admin:catalog_product_add"), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Informe o SKU")
        self.assertEqual(Product.objects.count(), 0)

    def test_editing_an_existing_product_never_rewrites_the_sku(self):
        produto = make_product(sku="LP-LEAO-2026", name="Leão de Judá", category=self.religioso, with_variant=False)
        outra = make_category(slug="decoracao", name="Decoração")
        url = reverse("admin:catalog_product_change", args=[produto.pk])
        html = self.client.get(url).content.decode()
        self.assertNotIn("data-sku-suggest", html)

        dados = self.dados(nome="Jesus Cristo", sku="LP-LEAO-2026", categoria=outra, slug=produto.slug)
        dados.update(payload("translations", [{"id": produto.translations.get().pk, "language": "pt", "name": "Jesus Cristo"}], initial=1))
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 302)
        produto.refresh_from_db()
        self.assertEqual(produto.sku, "LP-LEAO-2026")
        self.assertEqual(produto.category, outra)
        self.assertEqual(Product.objects.get(pk=produto.pk).name_in("pt"), "Jesus Cristo")

    def test_editing_an_existing_product_with_a_blank_sku_is_refused(self):
        produto = make_product(sku="REL-LEAO-001", name="Leão", category=self.religioso, with_variant=False)
        url = reverse("admin:catalog_product_change", args=[produto.pk])
        dados = self.dados(nome="Leão", sku="", slug=produto.slug)
        dados.update(payload("translations", [{"id": produto.translations.get().pk, "language": "pt", "name": "Leão"}], initial=1))
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 200)
        produto.refresh_from_db()
        self.assertEqual(produto.sku, "REL-LEAO-001")

    def test_collision_at_save_time_takes_the_next_sequence(self):
        """A sugestão passou na validação, mas alguém gravou o mesmo SKU antes do save."""
        original = sku_rules.suggest_product_sku
        chamadas = []

        def sugerir(category, name, reserved=()):
            chamadas.append(1)
            if len(chamadas) == 2:
                # Entre a validação (1ª chamada) e a gravação (2ª), outro
                # cadastro levou o REL-LEAO-001: a sugestão volta igual, e é o
                # banco que recusa.
                Product.objects.create(sku="REL-LEAO-001")
                return "REL-LEAO-001"
            return original(category, name, reserved)

        with mock.patch.object(sku_rules, "suggest_product_sku", side_effect=sugerir):
            resposta = self.client.post(reverse("admin:catalog_product_add"), self.dados())

        self.assertEqual(resposta.status_code, 302, resposta.content[:600] if resposta.status_code != 302 else "")
        self.assertEqual(len(chamadas), 3)
        self.assertEqual(Product.objects.get(translations__name="Leão de Judá").sku, "REL-LEAO-002")

    def test_suggestion_endpoint_follows_name_and_category(self):
        url = reverse("admin:catalog_product_sku_suggestion")
        self.assertEqual(self.client.get(url, {"name": "Leão de Judá", "category": self.religioso.pk}).json()["sku"], "REL-LEAO-001")
        self.assertEqual(self.client.get(url, {"name": "Jesus Cristo", "category": self.religioso.pk}).json()["sku"], "REL-JESUS-001")
        decoracao = make_category(slug="decoracao", name="Decoração")
        self.assertEqual(self.client.get(url, {"name": "Leão de Judá", "category": decoracao.pk}).json()["sku"], "DEC-LEAO-001")

    def test_full_form_new_product_with_variants_gets_v01_and_v02(self):
        dados = self.dados()
        dados.update(payload("variants", [
            {"sku": "", "sort_order": "0", "is_active": "on", "pricing_mode": PricingMode.PRICE, "sale_price": "10.00", "dimension_unit": "mm"},
            {"sku": "", "sort_order": "1", "is_active": "on", "pricing_mode": PricingMode.PRICE, "sale_price": "12.00", "dimension_unit": "mm", "size": "20 cm"},
        ]))
        resposta = self.client.post(reverse("admin:catalog_product_add"), dados)
        self.assertEqual(resposta.status_code, 302, resposta.content[:600] if resposta.status_code != 302 else "")
        produto = Product.objects.get(sku="REL-LEAO-001")
        self.assertEqual(sorted(produto.variants.values_list("sku", flat=True)), ["REL-LEAO-001-V01", "REL-LEAO-001-V02"])

    def test_the_full_form_is_not_the_duplication_screen(self):
        """Duplicar é o cadastro rápido com um modelo (`ProductAdmin.duplicate_url`)."""
        produto = make_product(sku="REL-LEAO-001", name="Leão", category=self.religioso)
        html = self.client.get(reverse("admin:catalog_product_add") + f"?_duplicar={produto.pk}").content.decode()
        self.assertNotIn('value="REL-LEAO-001"', html)
        self.assertNotIn('value="REL-LEAO-002"', html)
        produto.refresh_from_db()
        self.assertEqual(produto.sku, "REL-LEAO-001")


class ListingTests(QuickAddBase):
    def setUp(self):
        super().setUp()
        self.leao = make_product(sku="REL-LEAO-001", name="Leão de Judá", category=self.religioso,
                                 price=Decimal("29.90"), stock_quantity=8)
        self.vaso = make_product(sku="DEC-VASO-001", name="Vaso", category=self.religioso,
                                 price=Decimal("29.90"), stock_quantity=10, variant_sku="DEC-VASO-001-V01")
        make_variant(self.vaso, sku="DEC-VASO-001-V02", price=Decimal("39.90"), stock=4, size="20 cm")
        make_variant(self.vaso, sku="DEC-VASO-001-V03", price=Decimal("35.00"), stock=0, size="30 cm")
        self.rascunho = make_product(sku="REL-JESUS-001", name="Jesus Cristo", category=self.religioso,
                                     status=ProductStatus.DRAFT, with_variant=False)

    def html(self):
        resposta = self.client.get(reverse("admin:catalog_product_changelist"))
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()

    def test_columns(self):
        html = self.html()
        for cabecalho in ("ID", "SKU", "Produto", "Categoria", "Status", "Preço", "Estoque", "Variantes", "Ações"):
            self.assertIn(cabecalho, html)
        self.assertIn("€29,90", html)
        self.assertIn("€29,90 – €39,90", html)
        self.assertIn("1 opção", html)
        self.assertIn("3 opções", html)
        self.assertIn("Sem configuração", html)
        self.assertIn("+ Novo produto", html)
        self.assertIn(reverse("admin:catalog_product_quick_add"), html)
        self.assertIn(f"{reverse('admin:catalog_product_change', args=[self.vaso.pk])}#variants-group", html)

    def test_stock_is_the_sum_of_active_variants(self):
        html = self.html()
        linha = html.split("DEC-VASO-001", 1)[1].split("</tr>", 1)[0]
        self.assertIn(">14<", linha)  # 10 + 4 + 0

    def test_actions_column(self):
        html = self.html()
        self.assertIn("Editar", html)
        self.assertIn("Duplicar", html)
        self.assertIn("Ver produto", html)
        self.assertIn(reverse("admin:catalog_product_toggle_status", args=[self.leao.pk]), html)
        linha = html.split("REL-JESUS-001", 1)[1].split("</tr>", 1)[0]
        self.assertIn(">Ativar<", linha)
        linha = html.split("REL-LEAO-001", 1)[1].split("</tr>", 1)[0]
        self.assertIn(">Desativar<", linha)

    def test_search_and_filters_still_work(self):
        resposta = self.client.get(reverse("admin:catalog_product_changelist"), {"q": "Jesus"})
        self.assertContains(resposta, "REL-JESUS-001")
        self.assertNotContains(resposta, "DEC-VASO-001")
        resposta = self.client.get(reverse("admin:catalog_product_changelist"), {"status__exact": ProductStatus.DRAFT})
        self.assertContains(resposta, "REL-JESUS-001")
        self.assertNotContains(resposta, "REL-LEAO-001")

    def test_no_query_per_row(self):
        with CaptureQueriesContext(connection) as poucos:
            self.html()
        for i in range(12):
            p = make_product(sku=f"ANI-DINO-{i:03d}", name=f"Dino {i}", category=self.religioso,
                             price=Decimal("5.00"), variant_sku=f"ANI-DINO-{i:03d}-V01")
            make_variant(p, sku=f"ANI-DINO-{i:03d}-V02", price=Decimal("7.00"), size="x")
        with CaptureQueriesContext(connection) as muitos:
            self.html()
        self.assertEqual(len(poucos), len(muitos))

    def test_toggle_status_from_the_list(self):
        url = reverse("admin:catalog_product_toggle_status", args=[self.leao.pk])
        pagina = self.client.get(url)
        self.assertContains(pagina, "Desativar produto")
        resposta = self.client.post(url, {"next": reverse("admin:catalog_product_changelist") + "?q=leao"})
        self.assertRedirects(resposta, reverse("admin:catalog_product_changelist") + "?q=leao")
        self.leao.refresh_from_db()
        self.assertEqual(self.leao.status, ProductStatus.INACTIVE)
        self.client.post(url)
        self.leao.refresh_from_db()
        self.assertEqual(self.leao.status, ProductStatus.ACTIVE)

    def test_activating_a_product_without_variant_is_refused(self):
        url = reverse("admin:catalog_product_toggle_status", args=[self.rascunho.pk])
        self.assertContains(self.client.get(url), "pelo menos uma variante ativa")
        self.client.post(url)
        self.rascunho.refresh_from_db()
        self.assertEqual(self.rascunho.status, ProductStatus.DRAFT)

    def test_unsafe_next_falls_back_to_the_list(self):
        url = reverse("admin:catalog_product_toggle_status", args=[self.leao.pk])
        resposta = self.client.post(url, {"next": "https://evil.test/"})
        self.assertRedirects(resposta, reverse("admin:catalog_product_changelist"))

    def test_toggle_requires_change_permission(self):
        leitor = make_user("leitor", is_staff=True, with_customer=False)
        leitor.user_permissions.add(Permission.objects.get(codename="view_product"))
        self.client.force_login(leitor)
        url = reverse("admin:catalog_product_toggle_status", args=[self.leao.pk])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.leao.refresh_from_db()
        self.assertEqual(self.leao.status, ProductStatus.ACTIVE)

    def test_bulk_activate_refuses_a_product_without_variant(self):
        resposta = self.client.post(
            reverse("admin:catalog_product_changelist"),
            {"action": "action_activate", "_selected_action": [self.rascunho.pk]},
            follow=True,
        )
        self.assertContains(resposta, "pelo menos uma variante ativa")
        self.rascunho.refresh_from_db()
        self.assertEqual(self.rascunho.status, ProductStatus.DRAFT)


class ProductSheetTests(QuickAddBase):
    def test_sheet_shows_name_sku_sections_and_shortcuts(self):
        produto = make_product(sku="REL-LEAO-001", name="Leão de Judá", category=self.religioso)
        resposta = self.client.get(reverse("admin:catalog_product_change", args=[produto.pk]))
        html = resposta.content.decode()
        self.assertIn("<h1>Leão de Judá</h1>", html)
        self.assertIn("<code>REL-LEAO-001</code>", html)
        for titulo in ("INFORMAÇÕES BÁSICAS", "CONTEÚDO", "FOTOS", "PERSONALIZAÇÃO", "VARIANTES", "OUTRAS INFORMAÇÕES", "AUDITORIA"):
            self.assertIn(titulo, html)
        self.assertIn("Duplicar", html)
        self.assertIn("Ver produto", html)
        # A variante de fábrica do helper chama-se REL-LEAO-001 (sem -V): a
        # sequência começa em V01 e nada existente é renomeado.
        self.assertIn('data-variant-sku-next="REL-LEAO-001-V01"', html)
        self.assertIn("jd-media-grid", html)
        self.assertIn("admin/js/jd_tabular_cards.js", html)

    def test_sheet_of_a_draft_without_variant_explains_it(self):
        produto = make_product(sku="REL-JESUS-001", name="Jesus", status=ProductStatus.DRAFT, with_variant=False)
        resposta = self.client.get(reverse("admin:catalog_product_change", args=[produto.pk]))
        self.assertContains(resposta, "ainda não possui uma configuração vendável")
        self.assertContains(resposta, 'data-variant-sku-next="REL-JESUS-001-V01"')

    def test_saving_the_sheet_of_a_draft_needs_no_variant_price_or_photo(self):
        produto = make_product(sku="REL-JESUS-001", name="Jesus", category=self.religioso,
                               status=ProductStatus.DRAFT, with_variant=False)
        url = reverse("admin:catalog_product_change", args=[produto.pk])
        dados = {
            "sku": produto.sku, "status": ProductStatus.DRAFT, "slug": produto.slug,
            "category": str(self.religioso.pk), "currency": "EUR", "featured_order": "0",
            "personalization_type": "none", "personalization_text_limit": "0",
            **payload("translations", [{"id": produto.translations.get().pk, "language": "pt", "name": "Jesus Cristo"}], initial=1),
            **payload("variants", []),
            **payload("media", []),
            **payload("product_colors", []),
            **payload("material_composition", []),
            "_save": "Salvar",
        }
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Product.objects.get(pk=produto.pk).name_in("pt"), "Jesus Cristo")


class ExistingDataTests(QuickAddBase):
    """O que já existia continua exatamente igual depois de usar as telas novas."""

    def snapshot(self):
        return (
            list(Product.objects.order_by("pk").values_list("pk", "sku", "slug", "status", "category_id")),
            list(ProductVariant.objects.order_by("pk").values_list("pk", "sku", "sale_price", "stock_quantity")),
            list(ProductTranslation.objects.order_by("pk").values_list("pk", "language", "name")),
        )

    def test_existing_products_are_untouched(self):
        antigo = make_product(sku="GATO-POMPOM-01", name="Gato Pompom", category=self.religioso,
                              price=Decimal("12.00"), variant_sku="GATO-POMPOM-01-PRETO")
        antes = self.snapshot()

        self.client.get(reverse("admin:catalog_product_changelist"))
        self.client.get(reverse("admin:catalog_product_change", args=[antigo.pk]))
        self.client.post(self.url, {"name": "Leão de Judá", "category": str(self.religioso.pk), "sku": "", "status": ProductStatus.DRAFT})
        self.client.get(reverse("admin:catalog_product_add") + f"?_duplicar={antigo.pk}")

        depois = self.snapshot()
        self.assertEqual(antes[0], [p for p in depois[0] if p[1] != "REL-LEAO-001"])
        self.assertEqual(antes[1], depois[1])
        self.assertEqual(antes[2], [t for t in depois[2] if t[2] != "Leão de Judá"])
        antigo.refresh_from_db()
        self.assertEqual(antigo.sku, "GATO-POMPOM-01")
        self.assertEqual(antigo.variants.get().sku, "GATO-POMPOM-01-PRETO")
