"""Duplicar produto = **criar um produto novo usando outro como modelo**.

O «Duplicar» abre o cadastro rápido de sempre (as mesmas cinco perguntas:
nome, categoria, status, SKU, marca), já preenchido com os do modelo. Quem
cadastra decide ali o que o produto é — uma variação do mesmo, ou uma peça
diferente — e o SKU acompanha o nome enquanto for automático. Ao salvar, o
produto novo nasce e `apply_product_template` traz o resto do modelo.

O que estes testes guardam:

* a identidade é do produto NOVO: SKU e slug nascem do nome escolhido, nunca
  copiados; mudar o nome muda os dois;
* as variantes vêm com SKU derivado do produto novo (``CAVALO-001-V01``…) e
  estoque zerado;
* fotos nunca acompanham; descrições sempre; o conteúdo nasce preparado nos
  seis idiomas;
* opções adicionais, paleta de cores, composição de materiais e as
  configurações do produto (modo de cores, personalização, moeda) acompanham;
* o produto de origem não é tocado em nada;
* o cadastro rápido comum continua exatamente como era.
"""

from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.text import slugify

from apps.catalog import sku as sku_rules
from apps.catalog.admin import ProductAdmin, QuickProductForm
from apps.catalog.models import (
    Color,
    ColorMode,
    Material,
    MediaType,
    PersonalizationType,
    Product,
    ProductColor,
    ProductMaterialComposition,
    ProductMedia,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.core.admin_mixins import DUPLICATE_PARAM
from apps.core.testing import make_category, make_product, make_variant
from apps.core.tests_admin_duplicate import DuplicarBase, campos_do_formulario

SEIS = {"pt", "fr", "nl", "en", "de", "es"}


def traducoes(produto):
    return {
        t.language: (t.name, t.short_description, t.description, t.extra_information)
        for t in ProductTranslation.objects.filter(master=produto)
    }


class ModeloBase(DuplicarBase):
    """O cenário do enunciado: um T-Rex completo, para virar molde."""

    def setUp(self):
        super().setUp()
        self.dinossauros = make_category(slug="dinossauros", name="Dinossauros")
        self.animais = make_category(slug="animais", name="Animais")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        self.trex = make_product(
            sku="DINO-TREX-001",
            name="Dinossauro T-Rex",
            category=self.dinossauros,
            status=ProductStatus.ACTIVE,
            with_variant=False,
            color_mode=ColorMode.SINGLE,
            personalization_type=PersonalizationType.TEXT,
            personalization_text_limit=20,
        )
        ProductTranslation.objects.filter(master=self.trex, language="pt").update(
            short_description="Curta em PT",
            description="Descrição em PT",
            extra_information="Extra em PT",
        )
        ProductTranslation.objects.create(
            master=self.trex,
            language="fr",
            name="Dinosaure T-Rex",
            short_description="Courte en FR",
            description="Description en FR",
        )
        make_variant(
            self.trex, sku="DINO-TREX-001-V01", price=Decimal("27.90"), stock=7, size="25 cm",
            color=self.branco, material=self.pla, weight_grams=Decimal("300"),
            production_lead_time_days=2,
        )
        make_variant(
            self.trex, sku="DINO-TREX-001-V02", price=Decimal("32.90"), stock=4, size="30 cm",
            color=self.branco, material=self.pla, weight_grams=Decimal("380"),
        )
        ProductColor.objects.create(
            product=self.trex, color=self.branco, sort_order=0, price_delta=Decimal("1.50")
        )
        ProductMaterialComposition.objects.create(
            product=self.trex, material=self.pla, percentage=Decimal("100"), sort_order=0
        )
        self.foto = ProductMedia.objects.create(
            product=self.trex,
            media_type=MediaType.IMAGE,
            file=SimpleUploadedFile("trex.jpg", b"conteudo-falso", content_type="image/jpeg"),
            alt_text="T-Rex",
        )
        self.trex.refresh_from_db()

    # -- o caminho ---------------------------------------------------------

    def tela(self, produto):
        """Clica em «Duplicar» e devolve (URL do cadastro rápido, campos)."""
        resposta = self.pedir_duplicacao(produto)
        self.assertEqual(resposta.status_code, 302, "a ação deveria levar a algum lugar")
        destino = resposta["Location"]
        self.assertIn(reverse("admin:catalog_product_quick_add"), destino)
        self.assertIn(f"{DUPLICATE_PARAM}={produto.pk}", destino)
        pagina = self.client.get(destino)
        self.assertEqual(pagina.status_code, 200)
        self.html = pagina.content.decode()
        campos = campos_do_formulario(self.html, "product_quick_form")
        self.assertTrue(campos, "nenhum campo lido do cadastro rápido")
        return destino, campos

    def criar(self, produto, **alteracoes):
        destino, campos = self.tela(produto)
        resposta = self.salvar(destino, campos, **alteracoes)
        self.assertCriou(resposta)
        return Product.objects.exclude(pk=produto.pk).order_by("-pk").first()

    @staticmethod
    def erros(resposta):
        try:
            return repr(resposta.context["form"].errors)
        except Exception:  # pragma: no cover - só melhora a mensagem
            return "(sem formulário no contexto)"


# ---------------------------------------------------------------------------
# 1. A tela é o cadastro rápido de sempre, preenchido
# ---------------------------------------------------------------------------


class TelaTests(ModeloBase):
    def test_duplicate_opens_the_quick_add_form_prefilled(self):
        _destino, campos = self.tela(self.trex)
        self.assertEqual(campos["name"], "Dinossauro T-Rex")
        self.assertEqual(campos["category"], str(self.dinossauros.pk))
        self.assertEqual(campos["status"], ProductStatus.ACTIVE)
        self.assertEqual(campos["brand"], "")
        self.assertIn("product_quick_form", self.html)
        self.assertIn("Dinossauro T-Rex", self.html)

    def test_the_suggested_sku_is_the_normal_rule_not_the_origin_plus_one(self):
        """A regra do cadastro: prefixo da categoria + primeira palavra do nome."""
        _destino, campos = self.tela(self.trex)
        self.assertEqual(campos["sku"], "DIN-DINOSSAURO-001")
        self.assertEqual(
            campos["sku"], sku_rules.suggest_product_sku(self.dinossauros, "Dinossauro T-Rex")
        )
        self.assertNotEqual(campos["sku"], "DINO-TREX-002")

    def test_the_screen_says_the_sku_is_a_suggestion_that_follows_the_name(self):
        self.tela(self.trex)
        self.assertIn("data-sku-auto", self.html)
        self.assertIn("SKU acompanha o nome", self.html)

    def test_the_first_variant_block_is_not_asked_when_there_is_a_template(self):
        """As variantes vêm do modelo: perguntar preço criaria uma variante a mais."""
        _destino, campos = self.tela(self.trex)
        self.assertNotIn("sale_price", campos)
        self.assertNotIn("stock_quantity", campos)
        self.assertNotIn("PRIMEIRA VARIANTE", self.html)

    def test_the_javascript_keeps_a_prefilled_suggestion_automatic(self):
        """Contrato com o script: sem ele, o SKU pré-preenchido seria «manual»."""
        from pathlib import Path

        from django.conf import settings

        script = (
            Path(settings.BASE_DIR) / "static" / "admin" / "js" / "product_sku_suggest.js"
        ).read_text(encoding="utf-8")
        self.assertIn('!marcador.hasAttribute("data-sku-auto")', script)
        self.assertIn('manual = sku.value.trim() !== ""', script)

    def test_the_click_creates_nothing(self):
        antes = (Product.objects.count(), ProductVariant.objects.count())
        self.tela(self.trex)
        self.assertEqual(antes, (Product.objects.count(), ProductVariant.objects.count()))

    def test_the_full_product_form_is_no_longer_the_duplication_screen(self):
        html = self.client.get(
            reverse("admin:catalog_product_add") + f"?{DUPLICATE_PARAM}={self.trex.pk}"
        ).content.decode()
        self.assertNotIn("DINO-TREX", html)
        self.assertNotIn("Dinossauro T-Rex", html)


# ---------------------------------------------------------------------------
# 2. Manter o T-Rex — a variação do mesmo produto
# ---------------------------------------------------------------------------


class MesmoProdutoTests(ModeloBase):
    def test_keeping_everything_creates_a_second_t_rex(self):
        copia = self.criar(self.trex)
        self.assertEqual(copia.name_in("pt"), "Dinossauro T-Rex")
        self.assertEqual(copia.category_id, self.dinossauros.pk)
        self.assertEqual(copia.status, ProductStatus.ACTIVE)
        self.assertEqual(copia.sku, "DIN-DINOSSAURO-001")
        self.assertNotEqual(copia.sku, self.trex.sku)
        self.assertEqual(Product.objects.filter(sku=copia.sku).count(), 1)

    def test_duplicating_twice_walks_the_sequence(self):
        c1 = self.criar(self.trex)
        c2 = self.criar(self.trex)
        self.assertEqual((c1.sku, c2.sku), ("DIN-DINOSSAURO-001", "DIN-DINOSSAURO-002"))
        self.assertEqual(
            Product.objects.values("sku").distinct().count(), Product.objects.count()
        )

    def test_the_variants_get_skus_from_the_new_product(self):
        copia = self.criar(self.trex)
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            ["DIN-DINOSSAURO-001-V01", "DIN-DINOSSAURO-001-V02"],
        )
        self.assertFalse(
            set(copia.variants.values_list("sku", flat=True))
            & set(self.trex.variants.values_list("sku", flat=True))
        )
        self.assertEqual(
            ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count()
        )

    def test_the_variants_keep_everything_but_the_sku_and_the_stock(self):
        copia = self.criar(self.trex)
        grande = copia.variants.get(size="30 cm")
        self.assertEqual(
            (grande.sale_price, grande.color_id, grande.material_id, grande.weight_grams),
            (Decimal("32.90"), self.branco.pk, self.pla.pk, Decimal("380.00")),
        )
        pequena = copia.variants.get(size="25 cm")
        self.assertEqual(pequena.production_lead_time_days, 2)
        self.assertEqual(list(copia.variants.values_list("stock_quantity", flat=True)), [0, 0])
        self.assertEqual(
            sorted(self.trex.variants.values_list("stock_quantity", flat=True)), [4, 7]
        )

    def test_nothing_is_shared_with_the_origin(self):
        copia = self.criar(self.trex)
        for relacao in ("translations", "variants", "product_colors", "material_composition"):
            with self.subTest(relacao=relacao):
                self.assertFalse(
                    set(getattr(copia, relacao).values_list("pk", flat=True))
                    & set(getattr(self.trex, relacao).values_list("pk", flat=True))
                )

    def test_the_origin_is_untouched(self):
        antes = (
            Product.objects.filter(pk=self.trex.pk).values().first(),
            sorted(self.trex.variants.values_list("pk", "sku", "sale_price", "stock_quantity")),
            traducoes(self.trex),
            sorted(self.trex.media.values_list("pk", "file")),
            sorted(self.trex.product_colors.values_list("pk", "color_id", "price_delta")),
        )
        self.criar(self.trex)
        self.criar(self.trex)
        depois = (
            Product.objects.filter(pk=self.trex.pk).values().first(),
            sorted(self.trex.variants.values_list("pk", "sku", "sale_price", "stock_quantity")),
            traducoes(self.trex),
            sorted(self.trex.media.values_list("pk", "file")),
            sorted(self.trex.product_colors.values_list("pk", "color_id", "price_delta")),
        )
        self.assertEqual(antes, depois)


# ---------------------------------------------------------------------------
# 3. Virar Cavalo — o modelo como ponto de partida de outra peça
# ---------------------------------------------------------------------------


class OutroProdutoTests(ModeloBase):
    def cavalo(self, **extra):
        return self.criar(
            self.trex,
            name="Cavalo Articulado",
            category=str(self.animais.pk),
            sku="ANI-CAVALO-001",
            **extra,
        )

    def test_the_new_product_is_the_horse(self):
        copia = self.cavalo()
        self.assertEqual(copia.name_in("pt"), "Cavalo Articulado")
        self.assertEqual(copia.category_id, self.animais.pk)
        self.assertEqual(copia.sku, "ANI-CAVALO-001")
        self.assertEqual(copia.slug, "cavalo-articulado")

    def test_the_new_sku_carries_nothing_of_the_origin(self):
        copia = self.cavalo()
        for pedaco in ("DINO", "TREX", "DIN-DINOSSAURO"):
            with self.subTest(pedaco=pedaco):
                self.assertNotIn(pedaco, copia.sku)
                for sku in copia.variants.values_list("sku", flat=True):
                    self.assertNotIn(pedaco, sku)

    def test_the_variants_follow_the_new_sku(self):
        copia = self.cavalo()
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            ["ANI-CAVALO-001-V01", "ANI-CAVALO-001-V02"],
        )

    def test_the_suggestion_endpoint_follows_the_new_name(self):
        """É o que o JavaScript pergunta quando o nome muda na tela."""
        url = reverse("admin:catalog_product_sku_suggestion")
        resposta = self.client.get(url, {"name": "Cavalo Articulado", "category": self.animais.pk})
        self.assertEqual(resposta.json()["sku"], "ANI-CAVALO-001")
        resposta = self.client.get(url, {"name": "Luminária Lua", "category": self.animais.pk})
        self.assertEqual(resposta.json()["sku"], "ANI-LUMINARIA-001")

    def test_the_structure_of_the_template_still_comes(self):
        copia = self.cavalo()
        self.assertEqual(copia.variants.count(), 2)
        self.assertEqual(copia.color_mode, ColorMode.SINGLE)
        self.assertEqual(copia.personalization_type, PersonalizationType.TEXT)
        self.assertEqual(traducoes(copia)["pt"][1], "Curta em PT")


# ---------------------------------------------------------------------------
# 4. SKU: automático, manual, único, colisões
# ---------------------------------------------------------------------------


class SkuTests(ModeloBase):
    def test_a_hand_typed_sku_is_kept_and_the_variants_follow_it(self):
        copia = self.criar(self.trex, sku="MEU-CODIGO")
        self.assertEqual(copia.sku, "MEU-CODIGO")
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            ["MEU-CODIGO-V01", "MEU-CODIGO-V02"],
        )

    def test_a_hand_typed_sku_that_collides_is_refused(self):
        make_product(sku="MEU-CODIGO", name="Outro", category=self.animais, with_variant=False)
        destino, campos = self.tela(self.trex)
        resposta = self.salvar(destino, campos, sku="MEU-CODIGO")
        self.assertRecusou(resposta, "Já existe um produto com este SKU")
        self.assertEqual(Product.objects.count(), 2)

    def test_the_origin_sku_typed_back_is_refused(self):
        destino, campos = self.tela(self.trex)
        resposta = self.salvar(destino, campos, sku="DINO-TREX-001")
        self.assertRecusou(resposta, "Já existe um produto com este SKU")
        self.assertEqual(Product.objects.count(), 1)

    def test_a_suggested_sku_taken_before_saving_moves_to_the_next_free_one(self):
        """Outra aba levou a sugestão entre abrir a tela e salvar."""
        destino, campos = self.tela(self.trex)
        self.assertEqual(campos["sku"], "DIN-DINOSSAURO-001")
        make_product(
            sku="DIN-DINOSSAURO-001", name="Chegou antes", category=self.dinossauros,
            with_variant=False,
        )
        self.assertCriou(self.salvar(destino, campos))
        copia = Product.objects.get(sku="DIN-DINOSSAURO-002")
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            ["DIN-DINOSSAURO-002-V01", "DIN-DINOSSAURO-002-V02"],
        )
        self.assertEqual(
            ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count()
        )

    def test_a_collision_between_validation_and_saving_is_resolved(self):
        """A janela mínima: o SKU passou na validação e o banco recusou na gravação."""
        original = sku_rules.suggest_product_sku

        def sugerir_e_ser_atropelado(category, name, reserved=()):
            sku = original(category, name, reserved)
            if sku == "DIN-DINOSSAURO-001" and not Product.objects.filter(sku=sku).exists():
                make_product(sku=sku, name="Chegou no meio", category=self.dinossauros, with_variant=False)
            return sku

        destino, campos = self.tela(self.trex)
        with mock.patch.object(sku_rules, "suggest_product_sku", side_effect=sugerir_e_ser_atropelado):
            self.assertCriou(self.salvar(destino, campos))

        copia = Product.objects.get(sku="DIN-DINOSSAURO-002")
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            ["DIN-DINOSSAURO-002-V01", "DIN-DINOSSAURO-002-V02"],
        )

    def test_no_original_sku_changes(self):
        antes = sorted(self.trex.variants.values_list("pk", "sku"))
        self.criar(self.trex)
        self.criar(self.trex, sku="MEU-CODIGO")
        self.assertEqual(Product.objects.get(pk=self.trex.pk).sku, "DINO-TREX-001")
        self.assertEqual(sorted(self.trex.variants.values_list("pk", "sku")), antes)

    def test_many_variants(self):
        for n in range(3, 10):
            make_variant(self.trex, sku=f"DINO-TREX-001-V{n:02d}", price=Decimal("10.00"), size=f"{n} cm")
        copia = self.criar(self.trex)
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            [f"DIN-DINOSSAURO-001-V{n:02d}" for n in range(1, 10)],
        )


# ---------------------------------------------------------------------------
# 5. Slug
# ---------------------------------------------------------------------------


class SlugTests(ModeloBase):
    def test_the_slug_is_generated_and_unique(self):
        copia = self.criar(self.trex)
        self.assertTrue(copia.slug)
        self.assertNotEqual(copia.slug, self.trex.slug)
        self.assertTrue(copia.slug.startswith(slugify("Dinossauro T-Rex")))
        self.assertEqual(Product.objects.filter(slug=copia.slug).count(), 1)
        self.assertEqual(Product.objects.get(pk=self.trex.pk).slug, self.trex.slug)

    def test_the_slug_follows_the_new_name(self):
        copia = self.criar(self.trex, name="Cavalo Articulado", sku="ANI-CAVALO-001")
        self.assertEqual(copia.slug, "cavalo-articulado")


# ---------------------------------------------------------------------------
# 6. Conteúdo: seis idiomas, descrições intactas
# ---------------------------------------------------------------------------


class ConteudoTests(ModeloBase):
    def test_the_six_languages_and_the_descriptions(self):
        antes = traducoes(self.trex)
        copia = self.criar(self.trex)
        depois = traducoes(copia)
        self.assertEqual(set(depois), SEIS)
        self.assertEqual(depois["pt"], antes["pt"])
        self.assertEqual(depois["fr"], antes["fr"])
        for idioma in ("nl", "en", "de", "es"):
            self.assertEqual(depois[idioma], ("Dinossauro T-Rex", "", "", ""))
        self.assertEqual(traducoes(self.trex), antes)

    def test_a_new_name_only_changes_the_portuguese_name(self):
        """O nome novo é a identidade; as descrições do modelo continuam lá."""
        copia = self.criar(self.trex, name="Cavalo Articulado", sku="ANI-CAVALO-001")
        depois = traducoes(copia)
        self.assertEqual(depois["pt"], ("Cavalo Articulado", "Curta em PT", "Descrição em PT", "Extra em PT"))
        self.assertEqual(depois["fr"][2], "Description en FR")
        for idioma in ("nl", "en", "de", "es"):
            self.assertEqual(depois[idioma], ("Cavalo Articulado", "", "", ""))

    def test_incomplete_translations_still_work(self):
        ProductTranslation.objects.filter(master=self.trex, language="fr").delete()
        self.trex.refresh_translations()
        depois = traducoes(self.criar(self.trex))
        self.assertEqual(set(depois), SEIS)
        self.assertEqual(depois["pt"][2], "Descrição em PT")
        for idioma in SEIS - {"pt"}:
            self.assertEqual(depois[idioma], ("Dinossauro T-Rex", "", "", ""))


# ---------------------------------------------------------------------------
# 7. Fotos fora; o resto junto
# ---------------------------------------------------------------------------


class TudoMaisTests(ModeloBase):
    def test_photos_are_never_copied(self):
        copia = self.criar(self.trex)
        self.assertEqual(copia.media.count(), 0)
        self.assertEqual(
            list(self.trex.media.values_list("pk", "file")), [(self.foto.pk, self.foto.file.name)]
        )

    def test_palette_composition_and_settings_come(self):
        copia = self.criar(self.trex)
        self.assertEqual(
            list(copia.product_colors.values_list("color_id", "sort_order", "price_delta")),
            list(self.trex.product_colors.values_list("color_id", "sort_order", "price_delta")),
        )
        self.assertEqual(
            list(copia.material_composition.values_list("material_id", "percentage", "sort_order")),
            list(self.trex.material_composition.values_list("material_id", "percentage", "sort_order")),
        )
        self.assertEqual(
            (copia.color_mode, copia.personalization_type, copia.personalization_text_limit, copia.currency),
            (self.trex.color_mode, self.trex.personalization_type, self.trex.personalization_text_limit, self.trex.currency),
        )

    def test_the_message_says_what_came_and_what_did_not(self):
        destino, campos = self.tela(self.trex)
        resposta = self.salvar(destino, campos)
        mensagens = [str(m) for m in resposta.wsgi_request._messages]
        texto = " ".join(mensagens)
        self.assertIn("Copiado de <b>DINO-TREX-001</b>", texto)
        self.assertIn("2 variante(s)", texto)
        self.assertIn("Fotos não acompanham", texto)

    def test_an_active_template_without_a_priced_variant_asks_for_a_draft(self):
        vazio = make_product(
            sku="SEM-PRECO", name="Sem preço", category=self.animais,
            status=ProductStatus.DRAFT, with_variant=False,
        )
        destino, campos = self.tela(vazio)
        resposta = self.salvar(destino, campos, status=ProductStatus.ACTIVE)
        self.assertRecusou(resposta, "não tem variante ativa com preço")
        self.assertEqual(Product.objects.count(), 2)


# ---------------------------------------------------------------------------
# 8. O cadastro comum não mudou
# ---------------------------------------------------------------------------


class CadastroComumTests(ModeloBase):
    def test_the_plain_quick_add_still_asks_for_the_first_variant(self):
        html = self.client.get(reverse("admin:catalog_product_quick_add")).content.decode()
        self.assertIn("PRIMEIRA VARIANTE", html)
        self.assertIn('name="sale_price"', html)
        self.assertNotIn("data-sku-auto", html)
        self.assertIn("Só o essencial para o produto existir", html)

    def test_the_plain_quick_add_creates_a_lone_product(self):
        resposta = self.client.post(
            reverse("admin:catalog_product_quick_add"),
            {"name": "Vaso Facetado", "category": str(self.animais.pk),
             "status": ProductStatus.DRAFT, "sku": "", "brand": "", "_save": "Salvar"},
        )
        self.assertEqual(resposta.status_code, 302)
        novo = Product.objects.get(sku="ANI-VASO-001")
        self.assertEqual(novo.translations.count(), 1)  # só o português, como sempre
        self.assertEqual(novo.variants.count(), 0)
        self.assertEqual(novo.slug, "vaso-facetado")

    def test_the_form_without_a_template_is_the_form_of_always(self):
        form = QuickProductForm()
        self.assertIsNone(form.template)
        self.assertIn("sale_price", form.fields)
        self.assertIn("stock_quantity", form.fields)


# ---------------------------------------------------------------------------
# 9. Permissão
# ---------------------------------------------------------------------------


class PermissaoTests(ModeloBase):
    def test_without_view_permission_the_template_is_ignored(self):
        from django.contrib.auth.models import Permission

        from apps.accounts.models import User

        operador = User.objects.create_user(
            "operador", "op@jdprint.test", self.SENHA, is_staff=True
        )
        operador.user_permissions.set(
            Permission.objects.filter(codename="add_product", content_type__app_label="catalog")
        )
        self.client.force_login(operador)
        html = self.client.get(
            reverse("admin:catalog_product_quick_add") + f"?{DUPLICATE_PARAM}={self.trex.pk}"
        ).content.decode()
        self.assertNotIn("Dinossauro T-Rex", html)
        self.assertNotIn("data-sku-auto", html)

    def test_the_duplicate_action_still_belongs_to_the_product_admin(self):
        self.assertIn("duplicate_action", ProductAdmin.actions)
