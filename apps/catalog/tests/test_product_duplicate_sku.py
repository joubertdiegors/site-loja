"""Duplicação de produto: o que NUNCA vai junto e o que vai sempre.

* SKU do produto e das variantes: nunca copiados. A cópia nasce com a
  sequência seguinte à da origem (DEMO-GATO-01 → DEMO-GATO-02, variantes
  DEMO-GATO-02-V01…), e se alguém levar esse SKU entre a tela e a gravação a
  cópia avança sozinha para o próximo livre — o `unique` do banco é o juiz
  final, e o formulário nunca para num «já existe» por um SKU que ele mesmo
  sugeriu. Um SKU digitado pela pessoa continua sendo dela.
* Slug: nunca copiado; nasce do nome em português, único.
* Fotos: nunca copiadas.
* Conteúdo: os idiomas da origem vêm copiados exatamente (nome e descrições)
  e a cópia nasce preparada para PT, FR, NL, EN, DE e ES — os que faltam só
  com o nome, para traduzir depois; nada é inventado.
* Todo o resto (categoria, marca, modo de cores, personalização, paleta,
  composição, variantes com preço, eixos e prazo) continua vindo junto. O
  estoque continua zerado na cópia, como já era.
"""

from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.text import slugify

from apps.catalog.admin import ProductAdmin
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
    ProductTranslation,
    ProductVariant,
)
from apps.core.testing import make_category, make_product, make_variant
from apps.core.tests_admin_duplicate import DuplicarBase

SEIS = {"pt", "fr", "nl", "en", "de", "es"}


def traducoes(produto):
    return {
        t.language: (t.name, t.short_description, t.description, t.extra_information)
        for t in ProductTranslation.objects.filter(master=produto)
    }


class DuplicarProdutoBase(DuplicarBase):
    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="marcadores", name="Marcadores")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        # Como nos dados reais: a primeira variante leva o SKU do produto.
        self.gato = make_product(
            sku="DEMO-GATO-01", name="Marcador Gato", category=self.categoria,
            price=Decimal("8.90"), stock_quantity=3, weight_grams=Decimal("12.00"),
            variant_sku="DEMO-GATO-01", color_mode=ColorMode.SINGLE,
            personalization_type=PersonalizationType.TEXT, personalization_text_limit=20,
        )
        ProductTranslation.objects.filter(master=self.gato, language="pt").update(
            short_description="Curta em PT", description="Descrição em PT", extra_information="Extra em PT",
        )
        ProductTranslation.objects.create(
            master=self.gato, language="fr", name="Marque-page Chat",
            short_description="Courte en FR", description="Description en FR",
        )
        make_variant(
            self.gato, sku="DEMO-GATO-01-V01", price=Decimal("9.90"), stock=5, size="Grande",
            color=self.branco, material=self.pla, production_lead_time_days=2, weight_grams=Decimal("15.00"),
        )
        ProductColor.objects.create(product=self.gato, color=self.branco, sort_order=0, price_delta=Decimal("1.50"))
        ProductMaterialComposition.objects.create(
            product=self.gato, material=self.pla, percentage=Decimal("100"), sort_order=0
        )
        self.foto = ProductMedia.objects.create(
            product=self.gato, media_type=MediaType.IMAGE,
            file=SimpleUploadedFile("gato.jpg", b"conteudo-falso", content_type="image/jpeg"), alt_text="Gato",
        )
        self.gato.refresh_from_db()

    def duplicar(self, produto, **alteracoes):
        destino, campos = self.tela_de_criacao(produto)
        resposta = self.salvar(destino, campos, **alteracoes)
        self.assertCriou(resposta)
        return Product.objects.exclude(pk=produto.pk).order_by("-pk").first()

    @staticmethod
    def skus_de_variante(campos):
        return sorted(v for k, v in campos.items() if k.startswith("variants-") and k.endswith("-sku") and v)

    @staticmethod
    def linhas_de_conteudo(campos):
        """``{idioma: {campo: valor}}`` das linhas do inline CONTEÚDO na tela."""
        linhas = {}
        indice = 0
        while f"translations-{indice}-language" in campos:
            idioma = campos[f"translations-{indice}-language"]
            if idioma:
                linhas[idioma] = {
                    # O `<textarea>` nasce com uma quebra de linha que o navegador
                    # descarta (e o formulário também, ao gravar); o leitor não.
                    campo: campos.get(f"translations-{indice}-{campo}", "").removeprefix("\n")
                    for campo in ("name", "short_description", "description", "extra_information")
                }
            indice += 1
        return linhas

    @staticmethod
    def indice_do_idioma(campos, idioma):
        indice = 0
        while f"translations-{indice}-language" in campos:
            if campos[f"translations-{indice}-language"] == idioma:
                return indice
            indice += 1
        raise AssertionError(f"sem linha para {idioma}")


# ---------------------------------------------------------------------------
# 1. SKU
# ---------------------------------------------------------------------------


class SkuTests(DuplicarProdutoBase):
    def test_the_screen_never_offers_the_original_skus_nor_the_slug(self):
        _destino, campos = self.tela_de_criacao(self.gato)
        self.assertNotEqual(campos["sku"], "DEMO-GATO-01")
        self.assertEqual(campos["sku"], "DEMO-GATO-02")
        self.assertEqual(self.skus_de_variante(campos), ["DEMO-GATO-02-V01", "DEMO-GATO-02-V02"])
        self.assertEqual(campos["slug"], "")

    def test_the_copy_gets_a_new_unique_sku_and_the_original_keeps_its_own(self):
        copia = self.duplicar(self.gato)
        self.assertNotEqual(copia.sku, "DEMO-GATO-01")
        self.assertEqual(Product.objects.filter(sku=copia.sku).count(), 1)
        self.assertEqual(Product.objects.values("sku").distinct().count(), Product.objects.count())
        self.assertEqual(Product.objects.get(pk=self.gato.pk).sku, "DEMO-GATO-01")

    def test_the_variants_get_new_unique_skus_and_keep_everything_else(self):
        copia = self.duplicar(self.gato)
        originais = set(self.gato.variants.values_list("sku", flat=True))
        novos = list(copia.variants.values_list("sku", flat=True))
        self.assertEqual(len(novos), 2)
        self.assertEqual(len(set(novos)), 2)
        self.assertFalse(set(novos) & originais)
        self.assertTrue(all(sku.startswith(f"{copia.sku}-V") for sku in novos))
        self.assertEqual(ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count())
        grande = copia.variants.get(size="Grande")
        self.assertEqual(
            (grande.sale_price, grande.color_id, grande.material_id, grande.production_lead_time_days, grande.weight_grams),
            (Decimal("9.90"), self.branco.pk, self.pla.pk, 2, Decimal("15.00")),
        )
        self.assertEqual(grande.stock_quantity, 0)  # o estoque continua não acompanhando

    def test_no_original_sku_changes(self):
        antes = (self.gato.sku, sorted(self.gato.variants.values_list("pk", "sku")))
        self.duplicar(self.gato)
        self.duplicar(self.gato)
        depois = (Product.objects.get(pk=self.gato.pk).sku, sorted(self.gato.variants.values_list("pk", "sku")))
        self.assertEqual(antes, depois)

    def test_duplicating_twice_walks_the_sequence(self):
        c2 = self.duplicar(self.gato)
        c3 = self.duplicar(self.gato)
        self.assertEqual((c2.sku, c3.sku), ("DEMO-GATO-02", "DEMO-GATO-03"))
        self.assertEqual(sorted(c3.variants.values_list("sku", flat=True)), ["DEMO-GATO-03-V01", "DEMO-GATO-03-V02"])

    def test_a_suggested_sku_taken_before_saving_moves_to_the_next_free_one(self):
        destino, campos = self.tela_de_criacao(self.gato)
        self.assertEqual(campos["sku"], "DEMO-GATO-02")
        # Outra aba levou DEMO-GATO-02 (e a V01 dele) entre a tela e o envio.
        make_product(sku="DEMO-GATO-02", name="Chegou antes", category=self.categoria, variant_sku="DEMO-GATO-02-V01")

        self.assertCriou(self.salvar(destino, campos))

        copia = Product.objects.get(sku="DEMO-GATO-03")
        self.assertEqual(sorted(copia.variants.values_list("sku", flat=True)), ["DEMO-GATO-03-V01", "DEMO-GATO-03-V02"])
        self.assertEqual(list(Product.objects.get(sku="DEMO-GATO-02").variants.values_list("sku", flat=True)), ["DEMO-GATO-02-V01"])
        self.assertEqual(ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count())

    def test_a_collision_between_validation_and_saving_is_resolved(self):
        """A janela mínima: o SKU passou na validação e o banco recusou na gravação.

        `save_form` roda depois de o formulário validar e antes de `save_model`:
        é aí que «outra pessoa» grava o mesmo SKU. A cópia avança para o
        seguinte e as variantes a acompanham.
        """
        original = ProductAdmin.save_form

        def save_form(admin, request, form, change):
            obj = original(admin, request, form, change)
            if not change and not Product.objects.filter(sku=obj.sku).exists():
                make_product(sku=obj.sku, name="Chegou no meio", category=self.categoria, variant_sku=f"{obj.sku}-V01")
            return obj

        destino, campos = self.tela_de_criacao(self.gato)
        with mock.patch.object(ProductAdmin, "save_form", save_form):
            self.assertCriou(self.salvar(destino, campos))

        copia = Product.objects.get(sku="DEMO-GATO-03")
        self.assertEqual(sorted(copia.variants.values_list("sku", flat=True)), ["DEMO-GATO-03-V01", "DEMO-GATO-03-V02"])
        self.assertEqual(ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count())

    def test_a_hand_typed_sku_that_collides_is_still_refused(self):
        destino, campos = self.tela_de_criacao(self.gato)
        self.assertRecusou(self.salvar(destino, campos, sku="DEMO-GATO-01"), "já existe")
        self.assertEqual(Product.objects.count(), 1)

    def test_a_hand_typed_free_sku_is_kept_and_the_variants_follow_it(self):
        copia = self.duplicar(self.gato, sku="MEU-GATO")
        self.assertEqual(copia.sku, "MEU-GATO")
        self.assertEqual(sorted(copia.variants.values_list("sku", flat=True)), ["MEU-GATO-V01", "MEU-GATO-V02"])

    def test_many_variants(self):
        for n in range(2, 8):
            make_variant(self.gato, sku=f"DEMO-GATO-01-V{n:02d}", price=Decimal("10.00"), size=f"{n} cm")
        copia = self.duplicar(self.gato)
        self.assertEqual(copia.variants.count(), 8)
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)),
            [f"DEMO-GATO-02-V{n:02d}" for n in range(1, 9)],
        )
        self.assertEqual(ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count())

    def test_a_sku_without_a_number_still_gets_a_new_one(self):
        produto = make_product(sku="CENARIO-TRI", name="Peça", category=self.categoria, variant_sku="CENARIO-TRI")
        copia = self.duplicar(produto)
        self.assertEqual(copia.sku, "CENARIO-TRI-001")
        self.assertEqual(list(copia.variants.values_list("sku", flat=True)), ["CENARIO-TRI-001-V01"])


# ---------------------------------------------------------------------------
# 2. Slug
# ---------------------------------------------------------------------------


class SlugTests(DuplicarProdutoBase):
    def test_the_slug_is_not_copied_and_the_new_one_is_unique(self):
        copia = self.duplicar(self.gato)
        self.assertTrue(copia.slug)
        self.assertNotEqual(copia.slug, self.gato.slug)
        self.assertTrue(copia.slug.startswith(slugify("Marcador Gato")))
        self.assertEqual(Product.objects.filter(slug=copia.slug).count(), 1)
        self.assertEqual(Product.objects.get(pk=self.gato.pk).slug, self.gato.slug)

    def test_a_new_name_on_the_screen_gives_the_slug_of_the_new_name(self):
        destino, campos = self.tela_de_criacao(self.gato)
        indice = self.indice_do_idioma(campos, "pt")
        self.assertCriou(self.salvar(destino, campos, **{f"translations-{indice}-name": "Marcador Cachorro"}))
        copia = Product.objects.get(sku="DEMO-GATO-02")
        self.assertEqual(copia.slug, "marcador-cachorro")
        self.assertEqual(copia.display_name, "Marcador Cachorro")


# ---------------------------------------------------------------------------
# 3. Conteúdo: seis idiomas, descrições intactas
# ---------------------------------------------------------------------------


class ConteudoTests(DuplicarProdutoBase):
    def test_the_screen_prepares_the_six_languages(self):
        _destino, campos = self.tela_de_criacao(self.gato)
        linhas = self.linhas_de_conteudo(campos)
        self.assertEqual(set(linhas), SEIS)
        self.assertEqual(
            linhas["pt"],
            {"name": "Marcador Gato", "short_description": "Curta em PT", "description": "Descrição em PT", "extra_information": "Extra em PT"},
        )
        self.assertEqual(
            linhas["fr"],
            {"name": "Marque-page Chat", "short_description": "Courte en FR", "description": "Description en FR", "extra_information": ""},
        )
        for idioma in ("nl", "en", "de", "es"):
            with self.subTest(idioma=idioma):
                self.assertEqual(
                    linhas[idioma],
                    {"name": "Marcador Gato", "short_description": "", "description": "", "extra_information": ""},
                )

    def test_saving_keeps_the_descriptions_exactly_and_creates_the_six_rows(self):
        antes = traducoes(self.gato)
        copia = self.duplicar(self.gato)
        depois = traducoes(copia)
        self.assertEqual(set(depois), SEIS)
        self.assertEqual(depois["pt"], antes["pt"])
        self.assertEqual(depois["fr"], antes["fr"])
        for idioma in ("nl", "en", "de", "es"):
            self.assertEqual(depois[idioma], ("Marcador Gato", "", "", ""))
        self.assertEqual(traducoes(self.gato), antes)
        self.assertFalse(
            set(copia.translations.values_list("pk", flat=True)) & set(self.gato.translations.values_list("pk", flat=True))
        )

    def test_incomplete_translations_still_duplicate(self):
        ProductTranslation.objects.filter(master=self.gato, language="fr").delete()
        self.gato.refresh_translations()
        copia = self.duplicar(self.gato)
        depois = traducoes(copia)
        self.assertEqual(set(depois), SEIS)
        self.assertEqual(depois["pt"], ("Marcador Gato", "Curta em PT", "Descrição em PT", "Extra em PT"))
        for idioma in SEIS - {"pt"}:
            self.assertEqual(depois[idioma], ("Marcador Gato", "", "", ""))

    def test_the_names_can_be_changed_on_the_screen_before_saving(self):
        destino, campos = self.tela_de_criacao(self.gato)
        indice = self.indice_do_idioma(campos, "de")
        self.assertCriou(self.salvar(destino, campos, **{f"translations-{indice}-name": "Lesezeichen Katze"}))
        copia = Product.objects.get(sku="DEMO-GATO-02")
        self.assertEqual(copia.name_in("de"), "Lesezeichen Katze")
        self.assertEqual(copia.name_in("pt"), "Marcador Gato")


# ---------------------------------------------------------------------------
# 4. Fotos fora; todo o resto junto; o original intacto
# ---------------------------------------------------------------------------


class TudoMaisTests(DuplicarProdutoBase):
    def test_photos_are_not_copied(self):
        copia = self.duplicar(self.gato)
        self.assertEqual(copia.media.count(), 0)
        self.assertEqual(list(self.gato.media.values_list("pk", "file")), [(self.foto.pk, self.foto.file.name)])

    def test_everything_else_is_copied(self):
        copia = self.duplicar(self.gato)
        self.assertEqual(
            (copia.category_id, copia.status, copia.color_mode, copia.personalization_type, copia.personalization_text_limit, copia.currency),
            (self.gato.category_id, self.gato.status, self.gato.color_mode, self.gato.personalization_type, self.gato.personalization_text_limit, self.gato.currency),
        )
        self.assertEqual(
            list(copia.product_colors.values_list("color_id", "sort_order", "price_delta")),
            list(self.gato.product_colors.values_list("color_id", "sort_order", "price_delta")),
        )
        self.assertEqual(
            list(copia.material_composition.values_list("material_id", "percentage", "sort_order")),
            list(self.gato.material_composition.values_list("material_id", "percentage", "sort_order")),
        )
        self.assertEqual(
            sorted(copia.variants.values_list("size", "sale_price", "color_id", "material_id")),
            sorted(self.gato.variants.values_list("size", "sale_price", "color_id", "material_id")),
        )

    def test_the_original_is_untouched(self):
        antes = (
            Product.objects.filter(pk=self.gato.pk).values().first(),
            sorted(self.gato.variants.values_list("pk", "sku", "sale_price", "stock_quantity")),
            traducoes(self.gato),
            sorted(self.gato.media.values_list("pk", "file")),
            sorted(self.gato.product_colors.values_list("pk", "color_id")),
        )
        self.duplicar(self.gato)
        depois = (
            Product.objects.filter(pk=self.gato.pk).values().first(),
            sorted(self.gato.variants.values_list("pk", "sku", "sale_price", "stock_quantity")),
            traducoes(self.gato),
            sorted(self.gato.media.values_list("pk", "file")),
            sorted(self.gato.product_colors.values_list("pk", "color_id")),
        )
        self.assertEqual(antes, depois)
