"""Etapa 2B — cores do produto e composição de materiais.

Três coisas separadas: a VARIANTE (o que se vende: SKU, preço, estoque; cor e
material só quando são opções comerciais), as CORES da peça
(`Product.color_mode` + `ProductColor`) e a COMPOSIÇÃO de materiais
(`ProductMaterialComposition`). O que estes testes guardam:

1. cada modo de cor e o que o card/ficha mostram em cada um;
2. as duas tabelas novas: unicidade, ordem, percentual entre 0 e 100;
3. a migration de dados deriva a configuração das variantes sem inventar
   nada — e sem tocar SKU, preço, estoque, peso, fotos ou traduções;
4. o Admin: seções, formsets, validação do modo, duplicação, cadastro rápido
   intacto;
5. a loja: card (bolinhas, +N, «Cores à escolha»), ficha técnica, filtro de
   material por união, carrinho, e o snapshot do pedido;
6. nada disso custa uma consulta por produto.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.cart.cart import CartLine
from apps.catalog.models import (
    Color,
    ColorMode,
    Material,
    Product,
    ProductColor,
    ProductMaterialComposition,
    ProductStatus,
    ProductVariant,
)
from apps.core.tests_admin_duplicate import DuplicarBase, campos_do_formulario
from apps.core.testing import (
    LanguageResetMixin,
    make_address,
    make_bank_account,
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
    make_variant,
)
from apps.catalog.models import product_description_prefetches
from apps.home.services import product_card_queryset
from apps.orders import services as order_services


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


def make_color(name, hex_code="", **translations):
    from apps.catalog.models import ColorTranslation

    color = Color.objects.create(name=name, hex_code=hex_code)
    ColorTranslation.objects.create(master=color, language="pt", name=translations.get("pt", name))
    for language, nome in translations.items():
        if language != "pt":
            ColorTranslation.objects.create(master=color, language=language, name=nome)
    color.refresh_translations()
    return color


def make_material(name, **translations):
    from apps.catalog.models import MaterialTranslation

    material = Material.objects.create(name=name)
    MaterialTranslation.objects.create(master=material, language="pt", name=translations.get("pt", name))
    for language, nome in translations.items():
        if language != "pt":
            MaterialTranslation.objects.create(master=material, language=language, name=nome)
    material.refresh_translations()
    return material


class Fixtures:
    """Categoria, três cores e três materiais — o cenário de todos os testes."""

    def criar_fixtures(self):
        self.categoria = make_category(slug="modelos", name="Modelos")
        self.preto = make_color("Preto", "#111111", fr="Noir", en="Black")
        self.branco = make_color("Branco", "#FFFFFF", fr="Blanc", en="White")
        self.dourado = make_color("Dourado", "#D4AF37", fr="Doré", en="Gold")
        self.pla = make_material("PLA")
        self.petg = make_material("PETG")
        self.tpu = make_material("TPU")

    def produto(self, sku="REL-LEAO-001", nome="Leão de Judá", **campos):
        return make_product(sku=sku, name=nome, category=self.categoria, price=Decimal("29.90"), **campos)

    def fresh(self, produto):
        return Product.objects.for_listing().get(pk=produto.pk)


class Base(Fixtures, LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.criar_fixtures()


# ---------------------------------------------------------------------------
# 1–2. Modelos
# ---------------------------------------------------------------------------


class ColorModeTests(Base):
    def test_default_is_none_and_shows_nothing(self):
        produto = self.fresh(self.produto())
        self.assertEqual(produto.color_mode, ColorMode.NONE)
        self.assertEqual(produto.display_colors, [])
        self.assertEqual(produto.colors_text, "")
        self.assertFalse(produto.has_custom_colors)

    def test_single(self):
        produto = self.produto(color_mode=ColorMode.SINGLE)
        ProductColor.objects.create(product=produto, color=self.preto)
        produto = self.fresh(produto)
        self.assertEqual(produto.display_colors, [self.preto])
        self.assertEqual(produto.colors_text, "Preto")

    def test_multi_keeps_the_order(self):
        produto = self.produto(color_mode=ColorMode.MULTI)
        ProductColor.objects.create(product=produto, color=self.dourado, sort_order=2)
        ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        ProductColor.objects.create(product=produto, color=self.branco, sort_order=1)
        produto = self.fresh(produto)
        self.assertEqual(produto.display_colors, [self.preto, self.branco, self.dourado])
        self.assertEqual(produto.colors_text, "Preto + Branco + Dourado")

    def test_multi_has_no_limit(self):
        produto = self.produto(color_mode=ColorMode.MULTI)
        cores = [make_color(f"Cor {n}", "#000000") for n in range(6)]
        for posicao, cor in enumerate(cores):
            ProductColor.objects.create(product=produto, color=cor, sort_order=posicao)
        self.assertEqual(len(self.fresh(produto).display_colors), 6)

    def test_custom(self):
        produto = self.fresh(self.produto(color_mode=ColorMode.CUSTOM))
        self.assertTrue(produto.has_custom_colors)
        self.assertEqual(produto.display_colors, [])
        self.assertEqual(produto.colors_text, "Cores à escolha")

    def test_variant_mode_reads_the_variants_as_before(self):
        produto = self.produto(color_mode=ColorMode.VARIANT, color=self.preto)
        make_variant(produto, sku="REL-LEAO-001-V02", color=self.branco)
        ProductColor.objects.create(product=produto, color=self.dourado)  # ignorada neste modo
        produto = self.fresh(produto)
        self.assertEqual(produto.display_colors, [self.preto, self.branco])
        self.assertEqual(produto.colors_text, "")  # a cor que vale é a da variante escolhida

    def test_colors_text_is_translated(self):
        produto = self.produto(color_mode=ColorMode.MULTI)
        ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        ProductColor.objects.create(product=produto, color=self.branco, sort_order=1)
        resposta = self.client.get("/fr" + produto.get_absolute_url())
        self.assertContains(resposta, "Noir + Blanc")


class ProductColorTests(Base):
    def test_same_color_twice_is_refused_by_the_database(self):
        produto = self.produto()
        ProductColor.objects.create(product=produto, color=self.preto)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductColor.objects.create(product=produto, color=self.preto)

    def test_deleting_a_color_in_use_is_protected(self):
        from django.db.models import ProtectedError

        produto = self.produto()
        ProductColor.objects.create(product=produto, color=self.preto)
        with self.assertRaises(ProtectedError):
            self.preto.delete()

    def test_deleting_the_product_removes_its_palette(self):
        produto = self.produto(with_variant=False)
        ProductColor.objects.create(product=produto, color=self.preto)
        produto.delete()
        self.assertEqual(ProductColor.objects.count(), 0)
        self.assertTrue(Color.objects.filter(pk=self.preto.pk).exists())


class CompositionTests(Base):
    def test_display_with_and_without_percentage(self):
        produto = self.produto()
        a = ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("80"))
        b = ProductMaterialComposition.objects.create(product=produto, material=self.petg, percentage=Decimal("20.5"), sort_order=1)
        c = ProductMaterialComposition.objects.create(product=produto, material=self.tpu, sort_order=2)
        self.assertEqual(a.display, "PLA 80%")
        self.assertEqual(b.display, "PETG 20.5%")
        self.assertEqual(c.display, "TPU")
        self.assertEqual(self.fresh(produto).materials_text, "PLA 80% + PETG 20.5% + TPU")

    def test_order_is_respected(self):
        produto = self.produto()
        ProductMaterialComposition.objects.create(product=produto, material=self.petg, sort_order=5)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, sort_order=1)
        self.assertEqual(self.fresh(produto).materials_text, "PLA + PETG")

    def test_percentage_bounds(self):
        produto = self.produto()
        for valor in ("0", "100"):
            linha = ProductMaterialComposition(product=produto, material=self.pla, percentage=Decimal(valor))
            linha.full_clean()  # aceito
        for valor in ("-1", "100.01"):
            linha = ProductMaterialComposition(product=produto, material=self.pla, percentage=Decimal(valor))
            with self.assertRaises(ValidationError):
                linha.full_clean()
            with self.assertRaises(IntegrityError), transaction.atomic():
                ProductMaterialComposition.objects.create(product=produto, material=self.petg, percentage=Decimal(valor))

    def test_same_material_twice_is_refused(self):
        produto = self.produto()
        ProductMaterialComposition.objects.create(product=produto, material=self.pla)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductMaterialComposition.objects.create(product=produto, material=self.pla)

    def test_no_sum_to_100_required(self):
        produto = self.produto()
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("60"))
        ProductMaterialComposition.objects.create(product=produto, material=self.petg, percentage=Decimal("10"), sort_order=1)
        self.assertEqual(self.fresh(produto).materials_text, "PLA 60% + PETG 10%")


# ---------------------------------------------------------------------------
# 3. Migration de dados
# ---------------------------------------------------------------------------


class DerivationMigrationTests(TransactionTestCase):
    """Do banco de antes (0010) até 0012, com um catálogo realista.

    Nada é criado nem apagado nas variantes; SKU, preço, estoque, peso, fotos
    e traduções são iguais antes e depois — e a derivação só afirma o que as
    variantes dizem com clareza.
    """

    antes = ("catalog", "0010_media_variant_link")
    schema = ("catalog", "0011_product_colors_and_composition")
    depois = ("catalog", "0012_derive_color_mode_and_composition")

    def migrar(self, alvo):
        executor = MigrationExecutor(connection)
        executor.migrate([alvo])
        executor.loader.build_graph()
        return executor.loader.project_state([alvo]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate([self.depois])
        super().tearDown()

    def _catalogo(self, apps):
        Category = apps.get_model("categories", "Category")
        Product = apps.get_model("catalog", "Product")
        ProductTranslation = apps.get_model("catalog", "ProductTranslation")
        ProductVariant = apps.get_model("catalog", "ProductVariant")
        ProductMedia = apps.get_model("catalog", "ProductMedia")
        Color = apps.get_model("catalog", "Color")
        Material = apps.get_model("catalog", "Material")

        categoria = Category.objects.create(slug="modelos")
        preto = Color.objects.create(name="Preto", slug="preto")
        branco = Color.objects.create(name="Branco", slug="branco")
        pla = Material.objects.create(name="PLA", slug="pla")
        petg = Material.objects.create(name="PETG", slug="petg")

        def produto(sku, variantes):
            p = Product.objects.create(sku=sku, slug=sku.lower(), status="active", category=categoria)
            ProductTranslation.objects.create(master=p, language="pt", name=sku)
            ProductTranslation.objects.create(master=p, language="fr", name=sku + " fr")
            for numero, (cor, material, tamanho, preco, estoque) in enumerate(variantes):
                ProductVariant.objects.create(
                    product=p, sku=f"{sku}-{numero}", color=cor, material=material, size=tamanho,
                    sale_price=Decimal(preco), stock_quantity=estoque, weight_grams=Decimal("100") + numero,
                )
            return p

        # a. uma variante, preta, PLA -> uma cor + composição PLA
        produto("SIMPLES", [(preto, pla, "", "10.00", 5)])
        # b. três tamanhos, todos pretos e PLA -> uma cor + PLA
        produto("TAMANHOS", [(preto, pla, "10 cm", "10.00", 1), (preto, pla, "15 cm", "15.00", 2), (preto, pla, "20 cm", "20.00", 3)])
        # c. preto e branco como opções -> opção comercial, sem paleta; PLA comum -> composição PLA
        produto("OPCOES", [(preto, pla, "", "12.00", 4), (branco, pla, "", "12.00", 6)])
        # d. PLA e PETG como opções -> composição vazia; sem cor -> não se aplica
        produto("MATERIAIS", [(None, pla, "", "9.00", 1), (None, petg, "", "11.00", 1)])
        # e. só algumas variantes com cor/material -> opção comercial, composição vazia
        produto("PARCIAL", [(preto, pla, "P", "8.00", 1), (None, None, "G", "9.00", 1)])
        # f. sem variante -> nada
        Product.objects.create(sku="RASCUNHO", slug="rascunho", status="draft", category=categoria)
        # g. sem cor, sem material -> não se aplica, vazio
        produto("NEUTRO", [(None, None, "", "5.00", 9)])
        ProductMedia.objects.create(product=Product.objects.get(sku="SIMPLES"), file="products/simples/a.jpg", alt_text="a")

    def _fotografia(self, apps):
        Product = apps.get_model("catalog", "Product")
        ProductVariant = apps.get_model("catalog", "ProductVariant")
        ProductTranslation = apps.get_model("catalog", "ProductTranslation")
        ProductMedia = apps.get_model("catalog", "ProductMedia")
        return (
            list(Product.objects.order_by("pk").values_list("pk", "sku", "slug", "status", "category_id")),
            list(ProductVariant.objects.order_by("pk").values_list(
                "pk", "sku", "product_id", "color_id", "material_id", "size", "sale_price", "stock_quantity", "weight_grams"
            )),
            list(ProductTranslation.objects.order_by("pk").values_list("pk", "language", "name")),
            list(ProductMedia.objects.order_by("pk").values_list("pk", "file", "alt_text")),
        )

    def test_derivation_preserves_everything_and_invents_nothing(self):
        apps_antes = self.migrar(self.antes)
        self._catalogo(apps_antes)
        antes = self._fotografia(apps_antes)

        apps_schema = self.migrar(self.schema)
        ProductS = apps_schema.get_model("catalog", "Product")
        # A 0011 é só schema: tudo em «none», tabelas vazias.
        self.assertEqual(set(ProductS.objects.values_list("color_mode", flat=True)), {"none"})
        self.assertEqual(apps_schema.get_model("catalog", "ProductColor").objects.count(), 0)

        apps_depois = self.migrar(self.depois)
        self.assertEqual(self._fotografia(apps_depois), antes)  # nada mudou nas tabelas de sempre

        ProductD = apps_depois.get_model("catalog", "Product")
        ProductColorD = apps_depois.get_model("catalog", "ProductColor")
        ComposicaoD = apps_depois.get_model("catalog", "ProductMaterialComposition")

        def modo(sku):
            return ProductD.objects.get(sku=sku).color_mode

        def paleta(sku):
            return list(ProductColorD.objects.filter(product__sku=sku).order_by("sort_order").values_list("color__name", flat=True))

        def composicao(sku):
            return list(ComposicaoD.objects.filter(product__sku=sku).values_list("material__name", "percentage"))

        self.assertEqual((modo("SIMPLES"), paleta("SIMPLES"), composicao("SIMPLES")), ("single", ["Preto"], [("PLA", None)]))
        self.assertEqual((modo("TAMANHOS"), paleta("TAMANHOS"), composicao("TAMANHOS")), ("single", ["Preto"], [("PLA", None)]))
        self.assertEqual((modo("OPCOES"), paleta("OPCOES"), composicao("OPCOES")), ("variant", [], [("PLA", None)]))
        self.assertEqual((modo("MATERIAIS"), paleta("MATERIAIS"), composicao("MATERIAIS")), ("none", [], []))
        self.assertEqual((modo("PARCIAL"), paleta("PARCIAL"), composicao("PARCIAL")), ("variant", [], []))
        self.assertEqual((modo("RASCUNHO"), paleta("RASCUNHO"), composicao("RASCUNHO")), ("none", [], []))
        self.assertEqual((modo("NEUTRO"), paleta("NEUTRO"), composicao("NEUTRO")), ("none", [], []))
        # Nenhum percentual inventado.
        self.assertFalse(ComposicaoD.objects.exclude(percentage=None).exists())

        # Rodar de novo não duplica.
        from importlib import import_module

        import_module("apps.catalog.migrations.0012_derive_color_mode_and_composition").derivar(apps_depois, None)
        self.assertEqual(ProductColorD.objects.count(), 2)
        self.assertEqual(ComposicaoD.objects.count(), 3)

        # Reverter até antes da 0011 e voltar: as tabelas de sempre continuam iguais.
        apps_volta = self.migrar(self.antes)
        self.assertEqual(self._fotografia(apps_volta), antes)
        apps_final = self.migrar(self.depois)
        self.assertEqual(self._fotografia(apps_final), antes)


# ---------------------------------------------------------------------------
# 4. Admin
# ---------------------------------------------------------------------------


class AdminBase(Base):
    def setUp(self):
        super().setUp()
        self.chefe = make_user("chefe", is_staff=True, is_superuser=True, with_customer=False)
        self.client.force_login(self.chefe)

    def dados(self, produto, **extra):
        traducao = produto.translations.get(language="pt")
        variante = produto.variants.first()
        variantes = []
        if variante is not None:
            variantes = [{
                "id": variante.pk, "sku": variante.sku, "sort_order": "0", "is_active": "on",
                "pricing_mode": variante.pricing_mode, "sale_price": str(variante.sale_price), "stock_quantity": str(variante.stock_quantity),
                "filament_cost": "0", "energy_cost": "0", "dimension_unit": "mm",
            }]
        dados = {
            "sku": produto.sku, "status": produto.status, "slug": produto.slug,
            "category": str(self.categoria.pk), "currency": "EUR", "featured_order": "0",
            "personalization_type": "none", "personalization_text_limit": "0",
            "color_mode": produto.color_mode,
            **payload("translations", [{"id": traducao.pk, "language": "pt", "name": traducao.name}], initial=1),
            **payload("variants", variantes, initial=len(variantes)),
            **payload("media", []),
            **payload("product_colors", []),
            **payload("material_composition", []),
            "_save": "Salvar",
        }
        dados.update(extra)
        return dados


class AdminSectionsTests(AdminBase):
    def test_the_sheet_has_the_new_sections_and_help(self):
        produto = self.produto()
        html = self.client.get(reverse("admin:catalog_product_change", args=[produto.pk])).content.decode()
        for trecho in (
            "CORES", "PALETA DE CORES", "MATERIAIS", "composição de fabricação", 'name="color_mode"',
            "Isso não cria variantes", "Use cor ou material na variante somente quando forem opções comerciais",
            'id="product_colors-group"', 'id="material_composition-group"', "jd-compact-rows",
            "admin/js/product_colors_admin.js",
        ):
            self.assertIn(trecho, html)

    def test_quick_add_is_untouched(self):
        html = self.client.get(reverse("admin:catalog_product_quick_add")).content.decode()
        self.assertNotIn("color_mode", html)
        self.assertNotIn("product_colors", html)
        resposta = self.client.post(
            reverse("admin:catalog_product_quick_add"),
            {"name": "Jesus Cristo", "category": str(self.categoria.pk), "sku": "", "status": ProductStatus.DRAFT},
        )
        self.assertEqual(resposta.status_code, 302)
        criado = Product.objects.get(sku__startswith="MOD-JESUS")
        self.assertEqual(criado.color_mode, ColorMode.NONE)
        self.assertEqual(criado.product_colors.count(), 0)


class AdminEditingTests(AdminBase):
    def test_add_colors_and_materials_in_the_sheet(self):
        produto = self.produto()
        url = reverse("admin:catalog_product_change", args=[produto.pk])
        dados = self.dados(produto, color_mode=ColorMode.MULTI)
        dados.update(payload("product_colors", [
            {"color": str(self.branco.pk), "sort_order": "1"},
            {"color": str(self.preto.pk), "sort_order": "0"},
        ]))
        dados.update(payload("material_composition", [
            {"material": str(self.pla.pk), "percentage": "80", "sort_order": "0"},
            {"material": str(self.petg.pk), "percentage": "20", "sort_order": "1"},
        ]))
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 302, resposta.content[:800] if resposta.status_code != 302 else "")
        produto = self.fresh(produto)
        self.assertEqual(produto.color_mode, ColorMode.MULTI)
        self.assertEqual(produto.colors_text, "Preto + Branco")
        self.assertEqual(produto.materials_text, "PLA 80% + PETG 20%")
        # Nada mudou na variante.
        self.assertEqual(list(produto.variants.values_list("sku", "sale_price", "stock_quantity")), [("REL-LEAO-001", Decimal("29.90"), 0)])

    def test_remove_a_color(self):
        produto = self.produto(color_mode=ColorMode.MULTI)
        a = ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        b = ProductColor.objects.create(product=produto, color=self.branco, sort_order=1)
        dados = self.dados(produto)
        dados.update(payload("product_colors", [
            {"id": a.pk, "color": str(self.preto.pk), "sort_order": "0"},
            {"id": b.pk, "color": str(self.branco.pk), "sort_order": "1", "DELETE": "on"},
        ], initial=2))
        resposta = self.client.post(reverse("admin:catalog_product_change", args=[produto.pk]), dados)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(list(produto.product_colors.values_list("color__name", flat=True)), ["Preto"])

    def test_single_mode_refuses_two_colors(self):
        produto = self.produto()
        dados = self.dados(produto, color_mode=ColorMode.SINGLE)
        dados.update(payload("product_colors", [
            {"color": str(self.preto.pk), "sort_order": "0"}, {"color": str(self.branco.pk), "sort_order": "1"},
        ]))
        resposta = self.client.post(reverse("admin:catalog_product_change", args=[produto.pk]), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "cadastre uma cor só")

    def test_multi_mode_needs_a_color(self):
        produto = self.produto()
        resposta = self.client.post(reverse("admin:catalog_product_change", args=[produto.pk]), self.dados(produto, color_mode=ColorMode.MULTI))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Cadastre ao menos uma cor")

    def test_duplicate_color_and_material_are_refused_with_a_message(self):
        produto = self.produto()
        dados = self.dados(produto, color_mode=ColorMode.MULTI)
        dados.update(payload("product_colors", [
            {"color": str(self.preto.pk), "sort_order": "0"}, {"color": str(self.preto.pk), "sort_order": "1"},
        ]))
        dados.update(payload("material_composition", [
            {"material": str(self.pla.pk), "sort_order": "0"}, {"material": str(self.pla.pk), "sort_order": "1"},
        ]))
        resposta = self.client.post(reverse("admin:catalog_product_change", args=[produto.pk]), dados)
        # A mensagem pode ser a do próprio Django (unicidade por linha); o
        # que importa é recusar sem gravar nada.
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ProductColor.objects.count(), 0)
        self.assertEqual(ProductMaterialComposition.objects.count(), 0)

    def test_percentage_out_of_range_is_refused_in_the_form(self):
        produto = self.produto()
        dados = self.dados(produto)
        dados.update(payload("material_composition", [{"material": str(self.pla.pk), "percentage": "120", "sort_order": "0"}]))
        resposta = self.client.post(reverse("admin:catalog_product_change", args=[produto.pk]), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(ProductMaterialComposition.objects.count(), 0)

    def test_existing_product_keeps_sku_variants_and_prices(self):
        produto = self.produto(color_mode=ColorMode.VARIANT, color=self.preto)
        make_variant(produto, sku="LEAO-BRANCO", color=self.branco, price=Decimal("31.00"), stock=3)
        antes = list(produto.variants.order_by("pk").values_list("sku", "sale_price", "stock_quantity", "color_id"))
        dados = self.dados(produto, color_mode=ColorMode.VARIANT)
        vs = list(produto.variants.order_by("pk"))
        dados.update(payload("variants", [
            {"id": v.pk, "sku": v.sku, "sort_order": "0", "is_active": "on", "pricing_mode": v.pricing_mode,
             "sale_price": str(v.sale_price), "stock_quantity": str(v.stock_quantity), "color": str(v.color_id),
             "filament_cost": "0", "energy_cost": "0", "dimension_unit": "mm"} for v in vs
        ], initial=len(vs)))
        dados.update(payload("material_composition", [{"material": str(self.pla.pk), "sort_order": "0"}]))
        resposta = self.client.post(reverse("admin:catalog_product_change", args=[produto.pk]), dados)
        self.assertEqual(resposta.status_code, 302, resposta.content[:800] if resposta.status_code != 302 else "")
        self.assertEqual(list(produto.variants.order_by("pk").values_list("sku", "sale_price", "stock_quantity", "color_id")), antes)
        self.assertEqual(self.fresh(produto).materials_text, "PLA")


class DuplicateTests(Fixtures, DuplicarBase):
    """Com o mesmo leitor de formulário dos testes de duplicação."""

    def setUp(self):
        super().setUp()
        self.criar_fixtures()

    def test_duplicate_copies_palette_and_composition_with_a_new_sku(self):
        produto = self.produto(sku="VASO-01", nome="Vaso", color_mode=ColorMode.MULTI)
        ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        ProductColor.objects.create(product=produto, color=self.branco, sort_order=1)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("80"))
        ProductMaterialComposition.objects.create(product=produto, material=self.petg, percentage=Decimal("20"), sort_order=1)

        # Duplicar é criar um produto novo com este como modelo: a tela é o
        # cadastro rápido, e a paleta e a composição entram ao salvar.
        destino = self.pedir_duplicacao(produto)["Location"]
        self.assertIn(reverse("admin:catalog_product_quick_add"), destino)
        campos = campos_do_formulario(self.client.get(destino).content.decode(), "product_quick_form")
        self.assertEqual(campos["name"], "Vaso")
        self.assertEqual(campos["sku"], "MOD-VASO-001")

        resposta = self.salvar(destino, campos)
        self.assertEqual(resposta.status_code, 302)

        copia = self.fresh(Product.objects.get(sku="MOD-VASO-001"))
        self.assertEqual(copia.color_mode, ColorMode.MULTI)
        self.assertEqual(copia.colors_text, "Preto + Branco")
        self.assertEqual(copia.materials_text, "PLA 80% + PETG 20%")
        self.assertEqual(list(copia.variants.values_list("sku", flat=True)), ["MOD-VASO-001-V01"])
        original = self.fresh(Product.objects.get(pk=produto.pk))
        self.assertEqual(original.sku, "VASO-01")
        self.assertEqual(original.colors_text, "Preto + Branco")
        self.assertEqual(list(original.variants.values_list("sku", flat=True)), ["VASO-01"])
        self.assertEqual(Color.objects.count(), 3)
        self.assertEqual(Material.objects.count(), 3)


# ---------------------------------------------------------------------------
# 5. Loja
# ---------------------------------------------------------------------------


class CardTests(Base):
    def card(self, produto):
        html = self.client.get("/modelos/").content.decode()
        self.assertIn(produto.name_in("pt"), html)
        return html

    def test_none_shows_no_colors(self):
        produto = self.produto(color=self.preto)  # a variante tem cor, mas o modo diz «não se aplica»
        html = self.card(produto)
        self.assertNotIn("product-card-swatch", html)
        self.assertNotIn("1 cor", html)

    def test_single_shows_one_swatch(self):
        produto = self.produto(color_mode=ColorMode.SINGLE)
        ProductColor.objects.create(product=produto, color=self.preto)
        html = self.card(produto)
        self.assertEqual(html.count('class="product-card-swatch"'), 1)
        self.assertIn("#111111", html)
        self.assertIn("1 cor", html)

    def test_multi_shows_up_to_five_swatches_and_a_counter(self):
        produto = self.produto(color_mode=ColorMode.MULTI)
        cores = [self.preto, self.branco, self.dourado] + [make_color(f"C{n}", "#00000%d" % n) for n in range(4)]
        for posicao, cor in enumerate(cores):
            ProductColor.objects.create(product=produto, color=cor, sort_order=posicao)
        html = self.card(produto)
        self.assertEqual(html.count('class="product-card-swatch"'), 5)
        self.assertIn('class="product-card-swatch-more">+2<', html)
        self.assertIn("7 cores", html)

    def test_custom_shows_the_label(self):
        produto = self.produto(color_mode=ColorMode.CUSTOM)
        self.assertIn("Cores à escolha", self.card(produto))
        self.assertIn("Couleurs au choix", self.client.get("/fr/modelos/").content.decode())

    def test_variant_mode_keeps_the_variant_colors(self):
        produto = self.produto(color_mode=ColorMode.VARIANT, color=self.preto)
        make_variant(produto, sku="REL-LEAO-001-V02", color=self.branco)
        html = self.card(produto)
        self.assertEqual(html.count('class="product-card-swatch"'), 2)
        self.assertIn("2 cores", html)


class DetailTests(Base):
    def test_specs_show_colors_and_composition(self):
        produto = self.produto(color_mode=ColorMode.MULTI)
        ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        ProductColor.objects.create(product=produto, color=self.dourado, sort_order=1)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("80"))
        ProductMaterialComposition.objects.create(product=produto, material=self.petg, percentage=Decimal("20"), sort_order=1)
        resposta = self.client.get(produto.get_absolute_url())
        ficha = {chave: valor for chave, _r, valor in resposta.context["specifications"]}
        self.assertEqual(ficha["cores"], "Preto + Dourado")
        self.assertEqual(ficha["materiais"], "PLA 80% + PETG 20%")
        self.assertContains(resposta, "Preto + Dourado")

    def test_single_material_equal_to_the_variant_is_not_repeated(self):
        produto = self.produto(material=self.pla)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla)
        resposta = self.client.get(produto.get_absolute_url())
        chaves = [chave for chave, _r, _v in resposta.context["specifications"]]
        self.assertIn("material", chaves)
        self.assertNotIn("materiais", chaves)

    def test_variant_material_and_different_composition_both_appear(self):
        produto = self.produto(material=self.petg)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("90"))
        resposta = self.client.get(produto.get_absolute_url())
        ficha = {chave: valor for chave, _r, valor in resposta.context["specifications"]}
        self.assertEqual(ficha["material"], "PETG")
        self.assertEqual(ficha["materiais"], "PLA 90%")

    def test_custom_colors_in_the_specs_translated(self):
        produto = self.produto(color_mode=ColorMode.CUSTOM)
        resposta = self.client.get("/en" + produto.get_absolute_url())
        ficha = {chave: valor for chave, _r, valor in resposta.context["specifications"]}
        self.assertEqual(ficha["cores"], "Colours of your choice")


class MaterialFilterTests(Base):
    def setUp(self):
        super().setUp()
        # composição PLA + PETG, variante sem material
        self.composto = self.produto(sku="COMPOSTO", nome="Alfa Composto")
        ProductMaterialComposition.objects.create(product=self.composto, material=self.pla, sort_order=0)
        ProductMaterialComposition.objects.create(product=self.composto, material=self.petg, sort_order=1)
        # variante comercial PETG
        self.vaso = self.produto(sku="VASO-PETG", nome="Beta Vaso", material=self.petg)
        # PLA nas duas origens: conta uma vez
        self.duplo = self.produto(sku="DUPLO", nome="Gama Duplo", material=self.pla)
        ProductMaterialComposition.objects.create(product=self.duplo, material=self.pla)
        # sem material nenhum
        self.neutro = self.produto(sku="NEUTRO", nome="Delta Neutro")

    def test_filter_is_the_union_of_composition_and_variant(self):
        pla = self.client.get("/modelos/", {"material": "pla"}).content.decode()
        self.assertIn("Alfa Composto", pla)
        self.assertIn("Gama Duplo", pla)
        self.assertNotIn("Beta Vaso", pla)
        self.assertNotIn("Delta Neutro", pla)

        petg = self.client.get("/modelos/", {"material": "petg"}).content.decode()
        self.assertIn("Alfa Composto", petg)
        self.assertIn("Beta Vaso", petg)
        self.assertNotIn("Gama Duplo", petg)

    def test_available_materials_count_each_product_once(self):
        resposta = self.client.get("/modelos/")
        nos = {n["slug"]: n["count"] for n in resposta.context["material_nodes"]}
        self.assertEqual(nos, {"pla": 2, "petg": 2})

    def test_an_inactive_variant_does_not_count(self):
        ProductVariant.objects.filter(product=self.vaso).update(is_active=False)
        petg = self.client.get("/modelos/", {"material": "petg"}).content.decode()
        self.assertNotIn("Beta Vaso", petg)


class CartPanelTests(Base):
    def add(self, produto):
        variante = produto.variants.first()
        return self.client.post(reverse("cart:add"), {"product_id": produto.pk, "variant_id": variante.pk, "quantity": 1})

    def test_product_summary_when_the_variant_has_no_color_or_material(self):
        produto = self.produto(color_mode=ColorMode.MULTI, stock_quantity=5)
        ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        ProductColor.objects.create(product=produto, color=self.branco, sort_order=1)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("80"))
        self.add(produto)
        html = self.client.get(reverse("cart:detail")).content.decode()
        self.assertIn("Preto + Branco", html)
        self.assertIn("PLA 80%", html)

    def test_variant_color_and_material_win(self):
        produto = self.produto(color_mode=ColorMode.VARIANT, color=self.preto, material=self.petg, stock_quantity=5)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla)
        self.add(produto)
        html = self.client.get(reverse("cart:detail")).content.decode()
        pilulas = html.split('class="cart-chips"', 1)[1].split("</ul>", 1)[0]
        self.assertIn("Preto", pilulas)
        self.assertIn("PETG", pilulas)
        self.assertNotIn("PLA", pilulas)


class OrderSnapshotTests(Base):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="cliente", email="cliente@example.com")
        self.address = make_address(self.user.customer, self.country)

    def pedido(self, produto):
        variante = produto.default_variant
        linha = CartLine(key=f"{produto.pk}:{variante.pk}:-", product=produto, variant=variante, quantity=1, customization=None, upload=None)
        return order_services.create_order(
            customer=self.user.customer, lines=[linha], shipping_address=self.address,
            billing_address=self.address, shipping_method=self.method, language="pt-br",
        )

    def test_new_order_freezes_colors_and_composition(self):
        produto = self.produto(color_mode=ColorMode.MULTI, weight_grams=Decimal("100"), stock_quantity=5)
        ProductColor.objects.create(product=produto, color=self.preto, sort_order=0)
        ProductColor.objects.create(product=produto, color=self.branco, sort_order=1)
        ProductColor.objects.create(product=produto, color=self.dourado, sort_order=2)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla, percentage=Decimal("80"))
        ProductMaterialComposition.objects.create(product=produto, material=self.petg, percentage=Decimal("20"), sort_order=1)
        pedido = self.pedido(self.fresh(produto))
        item = pedido.items.get()
        self.assertEqual(item.colors_snapshot, "Preto + Branco + Dourado")
        self.assertEqual(item.materials_snapshot, "PLA 80% + PETG 20%")
        self.assertEqual(item.color_name, "")  # a variante não tem cor própria
        self.assertEqual(item.sku, "REL-LEAO-001")

        # Mudar o produto depois não muda o pedido.
        ProductColor.objects.filter(product=produto).delete()
        ProductMaterialComposition.objects.filter(product=produto).delete()
        produto.color_mode = ColorMode.NONE
        produto.save()
        item.refresh_from_db()
        self.assertEqual(item.colors_snapshot, "Preto + Branco + Dourado")
        self.assertEqual(item.materials_snapshot, "PLA 80% + PETG 20%")

    def test_simple_product_snapshot(self):
        produto = self.produto(weight_grams=Decimal("100"), stock_quantity=5)
        ProductMaterialComposition.objects.create(product=produto, material=self.pla)
        item = self.pedido(self.fresh(produto)).items.get()
        self.assertEqual(item.colors_snapshot, "")
        self.assertEqual(item.materials_snapshot, "PLA")

    def test_variant_axes_still_go_to_the_old_fields(self):
        produto = self.produto(color_mode=ColorMode.VARIANT, color=self.preto, material=self.petg, weight_grams=Decimal("100"), stock_quantity=5)
        item = self.pedido(self.fresh(produto)).items.get()
        self.assertEqual(item.color_name, "Preto")
        self.assertEqual(item.material_name, "PETG")
        self.assertEqual(item.colors_snapshot, "")
        self.assertEqual(item.materials_snapshot, "")

    def test_old_orders_have_empty_snapshots(self):
        produto = self.produto(weight_grams=Decimal("100"), stock_quantity=5)
        item = self.pedido(self.fresh(produto)).items.get()
        self.assertEqual((item.colors_snapshot, item.materials_snapshot), ("", ""))


class QueryCountTests(Base):
    def test_listing_and_cards_do_not_cost_a_query_per_product(self):
        def lote(n, base):
            for i in range(n):
                p = self.produto(sku=f"{base}-{i:03d}", nome=f"{base} {i}", color_mode=ColorMode.MULTI)
                ProductColor.objects.create(product=p, color=self.preto, sort_order=0)
                ProductColor.objects.create(product=p, color=self.branco, sort_order=1)
                ProductMaterialComposition.objects.create(product=p, material=self.pla, percentage=Decimal("80"))
                ProductMaterialComposition.objects.create(product=p, material=self.petg, percentage=Decimal("20"), sort_order=1)

        def medir():
            with CaptureQueriesContext(connection) as ctx:
                for produto in product_card_queryset():
                    produto.display_colors, produto.colors_text  # o card só lê a paleta
                self.client.get("/modelos/")
                for produto in Product.objects.prefetch_related(*product_description_prefetches()):
                    produto.colors_text, produto.materials_text
            return len(ctx)

        lote(2, "A")
        poucos = medir()
        lote(10, "B")
        self.assertEqual(medir(), poucos)
