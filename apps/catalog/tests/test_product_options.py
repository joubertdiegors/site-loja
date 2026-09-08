"""Etapa 3B, fase 1 — opções adicionais ilimitadas nas variantes (backend).

O que esta etapa promete, e o que estes testes seguram:

* `ProductOption` / `ProductOptionValue` pertencem ao produto, com tradução
  pelo mecanismo de sempre, ordem e unicidade;
* `ProductVariantOptionValue` liga a variante a um valor por opção, recusa
  valor de outra opção e opção de outro produto, e protege o que está em uso
  (RESTRICT: apagar o produto inteiro continua possível);
* cor, tamanho e material continuam exatamente como eram; `label` só cresce
  quando há opções («Preto · 25 cm · PLA · Parede · Fosco»);
* a combinação repetida (eixos fixos + escolhas) é recusada, como antes;
* o pedido congela `options_snapshot` no idioma do cliente, e renomear
  depois não muda o histórico; `variant_label` cabe em 500 caracteres;
* nada custa uma consulta por variante com o prefetch;
* as migrations (só schema) não tocam em produto, variante, SKU nem pedido.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import RestrictedError
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from apps.cart.cart import CartLine, load_variants
from apps.catalog.models import (
    Color,
    Material,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductVariant,
    ProductVariantOptionValue,
    variant_option_prefetches,
)
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
from apps.orders import services as order_services
from apps.orders.models import OrderItem


def opcao(produto, nome, *valores, sort_order=0, **traducoes):
    """Uma opção com os seus valores: `opcao(p, "Instalação", "Mesa", "Parede")`."""
    o = ProductOption.objects.create(product=produto, name=nome, sort_order=sort_order)
    for idioma, texto in traducoes.items():
        ProductOptionTranslation.objects.create(master=o, language=idioma, name=texto)
    for indice, valor in enumerate(valores):
        ProductOptionValue.objects.create(option=o, name=valor, sort_order=indice)
    return o


def valor(opcao_, nome):
    return opcao_.values.get(name=nome)


class Base(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="religioso", name="Religioso")
        self.preto = Color.objects.create(name="Preto", hex_code="#111111")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.pla = Material.objects.create(name="PLA")
        self.produto = make_product(
            sku="REL-LEAO-001", name="Leão de Judá", category=self.categoria, price=Decimal("29.90"),
            color=self.preto, material=self.pla, size="25 cm", stock_quantity=5, weight_grams=Decimal("100"),
        )
        self.variante = self.produto.variants.get()

    def fresh(self, variante):
        return ProductVariant.objects.prefetch_related(*variant_option_prefetches()).get(pk=variante.pk)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class OptionModelTests(Base):
    def test_an_option_with_values_and_translations(self):
        instalacao = opcao(self.produto, "Instalação", "Mesa", "Parede", fr="Installation", en="Installation")
        self.assertEqual(list(instalacao.values.values_list("name", flat=True)), ["Mesa", "Parede"])
        self.assertEqual(instalacao.display_name, "Instalação")
        with translation.override("fr"):
            self.assertEqual(instalacao.display_name, "Installation")
        with translation.override("nl"):  # sem tradução: cai no português
            self.assertEqual(instalacao.display_name, "Instalação")

    def test_values_are_translated_the_same_way(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        parede = valor(instalacao, "Parede")
        ProductOptionValueTranslation.objects.create(master=parede, language="fr", name="Mur")
        with translation.override("fr"):
            self.assertEqual(parede.display_name, "Mur")
        self.assertEqual(parede.display_name, "Parede")

    def test_options_and_values_keep_their_order(self):
        acabamento = opcao(self.produto, "Acabamento", "Fosco", "Brilhante", sort_order=2)
        instalacao = opcao(self.produto, "Instalação", "Parede", "Mesa", sort_order=1)
        self.assertEqual(list(self.produto.options.all()), [instalacao, acabamento])
        self.assertEqual([v.name for v in instalacao.values.all()], ["Parede", "Mesa"])

    def test_an_option_name_is_unique_per_product(self):
        opcao(self.produto, "Instalação")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductOption.objects.create(product=self.produto, name="Instalação")
        outro = make_product(sku="OUTRO", name="Outro", category=self.categoria, price=Decimal("5"))
        opcao(outro, "Instalação")  # o mesmo nome noutro produto é outra opção

    def test_a_value_name_is_unique_per_option(self):
        instalacao = opcao(self.produto, "Instalação", "Mesa")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductOptionValue.objects.create(option=instalacao, name="Mesa")
        acabamento = opcao(self.produto, "Acabamento")
        ProductOptionValue.objects.create(option=acabamento, name="Mesa")  # noutra opção pode

    def test_empty_names_are_refused(self):
        with self.assertRaises(ValidationError):
            ProductOption(product=self.produto, name="  ").full_clean()
        instalacao = opcao(self.produto, "Instalação")
        with self.assertRaises(ValidationError):
            ProductOptionValue(option=instalacao, name="").full_clean()

    def test_one_translation_per_language(self):
        instalacao = opcao(self.produto, "Instalação", fr="Installation")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductOptionTranslation.objects.create(master=instalacao, language="fr", name="Pose")

    def test_deleting_the_product_cascades_to_options_values_and_links(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        self.variante.set_option_values({instalacao: valor(instalacao, "Parede")})
        self.produto.delete()
        self.assertEqual(ProductOption.objects.count(), 0)
        self.assertEqual(ProductOptionValue.objects.count(), 0)
        self.assertEqual(ProductVariantOptionValue.objects.count(), 0)

    def test_deleting_the_variant_removes_only_its_choices(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        self.variante.set_option_values({instalacao: valor(instalacao, "Parede")})
        self.variante.delete()
        self.assertEqual(ProductVariantOptionValue.objects.count(), 0)
        self.assertEqual(ProductOptionValue.objects.count(), 1)
        self.assertEqual(ProductOption.objects.count(), 1)

    def test_a_value_in_use_is_protected(self):
        instalacao = opcao(self.produto, "Instalação", "Parede", "Mesa")
        parede = valor(instalacao, "Parede")
        self.variante.set_option_values({instalacao: parede})
        with self.assertRaises(RestrictedError):
            parede.delete()
        valor(instalacao, "Mesa").delete()  # sem uso, pode

    def test_an_option_in_use_is_protected(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        self.variante.set_option_values({instalacao: valor(instalacao, "Parede")})
        with self.assertRaises(RestrictedError):
            instalacao.delete()
        acabamento = opcao(self.produto, "Acabamento", "Fosco")
        acabamento.delete()  # sem uso, leva os valores junto
        self.assertFalse(ProductOptionValue.objects.filter(name="Fosco").exists())

    def test_renaming_is_free(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        self.variante.set_option_values({instalacao: valor(instalacao, "Parede")})
        instalacao.name = "Fixação"
        instalacao.save()
        parede = valor(instalacao, "Parede")
        parede.name = "Na parede"
        parede.save()
        self.assertEqual(self.fresh(self.variante).options_text, "Fixação: Na parede")


class LinkIntegrityTests(Base):
    def test_one_value_per_option_per_variant(self):
        instalacao = opcao(self.produto, "Instalação", "Mesa", "Parede")
        ProductVariantOptionValue.objects.create(variant=self.variante, option=instalacao, value=valor(instalacao, "Parede"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariantOptionValue.objects.create(variant=self.variante, option=instalacao, value=valor(instalacao, "Mesa"))

    def test_the_value_must_belong_to_the_option(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        acabamento = opcao(self.produto, "Acabamento", "Fosco")
        with self.assertRaises(ValidationError) as ctx:
            ProductVariantOptionValue.objects.create(variant=self.variante, option=instalacao, value=valor(acabamento, "Fosco"))
        self.assertIn("value", ctx.exception.message_dict)
        self.assertEqual(ProductVariantOptionValue.objects.count(), 0)

    def test_the_option_must_belong_to_the_product_of_the_variant(self):
        outro = make_product(sku="OUTRO", name="Outro", category=self.categoria, price=Decimal("5"))
        alheia = opcao(outro, "Instalação", "Parede")
        with self.assertRaises(ValidationError) as ctx:
            ProductVariantOptionValue.objects.create(variant=self.variante, option=alheia, value=valor(alheia, "Parede"))
        self.assertIn("option", ctx.exception.message_dict)

    def test_set_option_values_refuses_the_same_mistakes(self):
        outro = make_product(sku="OUTRO", name="Outro", category=self.categoria, price=Decimal("5"))
        alheia = opcao(outro, "Instalação", "Parede")
        instalacao = opcao(self.produto, "Instalação", "Parede")
        acabamento = opcao(self.produto, "Acabamento", "Fosco")
        with self.assertRaises(ValidationError):
            self.variante.set_option_values({alheia: valor(alheia, "Parede")})
        with self.assertRaises(ValidationError):
            self.variante.set_option_values({instalacao: valor(acabamento, "Fosco")})
        with self.assertRaises(ValidationError):
            self.variante.set_option_values({instalacao.pk: 999999})
        self.assertEqual(ProductVariantOptionValue.objects.count(), 0)

    def test_set_option_values_needs_a_saved_variant(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        nova = ProductVariant(product=self.produto, sku="X-V09", sale_price=Decimal("1"))
        with self.assertRaises(ValidationError):
            nova.set_option_values({instalacao: valor(instalacao, "Parede")})


# ---------------------------------------------------------------------------
# Variantes
# ---------------------------------------------------------------------------


class VariantChoicesTests(Base):
    def setUp(self):
        super().setUp()
        self.instalacao = opcao(self.produto, "Instalação", "Mesa", "Parede", sort_order=1)
        self.acabamento = opcao(self.produto, "Acabamento", "Fosco", "Brilhante", sort_order=2)
        self.modelo = opcao(self.produto, "Modelo", "Simples", "Decorado", sort_order=3)

    def nova(self, sku, **eixos):
        base = {"color": self.preto, "material": self.pla, "size": "25 cm"}
        base.update(eixos)
        return make_variant(self.produto, sku=sku, price=Decimal("9.90"), stock=1, **base)

    def test_a_variant_without_options(self):
        self.assertEqual(self.fresh(self.variante).option_labels, [])
        self.assertEqual(self.fresh(self.variante).options_text, "")
        self.assertEqual(self.variante.option_signature(), frozenset())

    def test_one_option(self):
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        v = self.fresh(self.variante)
        self.assertEqual(v.option_labels, ["Parede"])
        self.assertEqual(v.options_text, "Instalação: Parede")

    def test_two_options_in_the_order_of_the_product(self):
        # Gravadas ao contrário: a ordem que vale é a das opções, não a da gravação.
        self.variante.set_option_values({self.acabamento: valor(self.acabamento, "Fosco")})
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        v = self.fresh(self.variante)
        self.assertEqual(v.option_labels, ["Parede", "Fosco"])
        self.assertEqual(v.options_text, "Instalação: Parede · Acabamento: Fosco")

    def test_three_options(self):
        self.variante.set_option_values({
            self.modelo: valor(self.modelo, "Decorado"),
            self.instalacao: valor(self.instalacao, "Mesa"),
            self.acabamento: valor(self.acabamento, "Brilhante"),
        })
        self.assertEqual(self.fresh(self.variante).option_labels, ["Mesa", "Brilhante", "Decorado"])

    def test_a_choice_can_be_changed_or_cleared(self):
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Mesa")})
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        self.assertEqual(self.fresh(self.variante).option_labels, ["Parede"])
        self.assertEqual(ProductVariantOptionValue.objects.count(), 1)
        self.variante.set_option_values({self.instalacao: None})
        self.assertEqual(ProductVariantOptionValue.objects.count(), 0)

    def test_two_variants_with_different_values_are_two_variants(self):
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        outra = self.nova("REL-LEAO-001-V02")  # mesmos eixos fixos, ainda sem escolha
        outra.set_option_values({self.instalacao: valor(self.instalacao, "Mesa")})
        outra.full_clean()  # continua válida depois de escolher
        self.assertEqual(self.produto.variants.count(), 2)

    def test_the_same_combination_is_refused(self):
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        outra = self.nova("REL-LEAO-001-V02")
        with self.assertRaises(ValidationError) as ctx:
            outra.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        self.assertIn("Já existe uma variante com esta combinação", str(ctx.exception))
        self.assertEqual(ProductVariantOptionValue.objects.count(), 1)

    def test_clean_compares_the_choices_before_the_variant_exists(self):
        """O formulário pergunta antes de gravar: `_pending_option_choices`."""
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        nova = ProductVariant(product=self.produto, sku="REL-LEAO-001-V02", color=self.preto, material=self.pla, size="25 cm", sale_price=Decimal("9.90"))
        nova._pending_option_choices = {self.instalacao.pk: valor(self.instalacao, "Parede").pk}
        with self.assertRaises(ValidationError):
            nova.full_clean()
        nova._pending_option_choices = {self.instalacao.pk: valor(self.instalacao, "Mesa").pk}
        nova.full_clean()  # outra escolha: válida

    def test_without_choices_the_old_rule_is_unchanged(self):
        """Dois trios iguais sem opções continuam sendo recusados, como sempre."""
        with self.assertRaises(ValidationError) as ctx:
            self.nova("REL-LEAO-001-V02").full_clean()
        self.assertIn("size", ctx.exception.message_dict)
        self.nova("REL-LEAO-001-V03", size="30 cm").full_clean()

    def test_a_variant_with_choices_differs_from_one_without(self):
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede")})
        sem = self.nova("REL-LEAO-001-V02")
        sem.full_clean()  # conjunto vazio ≠ {Parede}

    def test_the_same_option_twice_is_refused(self):
        ProductVariantOptionValue.objects.create(variant=self.variante, option=self.instalacao, value=valor(self.instalacao, "Parede"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariantOptionValue.objects.create(variant=self.variante, option=self.instalacao, value=valor(self.instalacao, "Mesa"))


class CompatibilityTests(Base):
    """As variantes de antes continuam iguais: eixos, SKU, rótulo e regras."""

    def test_existing_axes_and_sku_are_untouched(self):
        v = self.fresh(self.variante)
        self.assertEqual((v.color, v.material, v.size, v.sku), (self.preto, self.pla, "25 cm", "REL-LEAO-001"))
        self.assertEqual(v.label, "Preto · 25 cm · PLA")
        self.assertEqual(v.display_label, "Preto · 25 cm · PLA")
        self.assertEqual(v.option_values.count(), 0)

    def test_every_axis_combination_still_labels_the_same(self):
        casos = {
            (None, "", None): "",
            (self.preto, "", None): "Preto",
            (None, "15 cm", None): "15 cm",
            (None, "", self.pla): "PLA",
            (self.preto, "15 cm", None): "Preto · 15 cm",
            (self.preto, "", self.pla): "Preto · PLA",
            (None, "15 cm", self.pla): "15 cm · PLA",
            (self.preto, "15 cm", self.pla): "Preto · 15 cm · PLA",
        }
        for numero, ((cor, tamanho, material), esperado) in enumerate(casos.items()):
            with self.subTest(rotulo=esperado):
                v = make_variant(self.produto, sku=f"COMB-{numero}", price=Decimal("1"), stock=1, color=cor, size=tamanho, material=material)
                self.assertEqual(self.fresh(v).label, esperado)

    def test_a_label_with_options_keeps_the_fixed_axes_first(self):
        instalacao = opcao(self.produto, "Instalação", "Parede", sort_order=1)
        acabamento = opcao(self.produto, "Acabamento", "Fosco", sort_order=2)
        self.variante.set_option_values({acabamento: valor(acabamento, "Fosco"), instalacao: valor(instalacao, "Parede")})
        self.assertEqual(self.fresh(self.variante).label, "Preto · 25 cm · PLA · Parede · Fosco")

    def test_a_variant_with_only_options_labels_them(self):
        v = make_variant(self.produto, sku="SO-OPCOES", price=Decimal("1"), stock=1)
        instalacao = opcao(self.produto, "Instalação", "Parede")
        v.set_option_values({instalacao: valor(instalacao, "Parede")})
        self.assertEqual(self.fresh(v).label, "Parede")

    def test_the_label_is_translated(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        ProductOptionValueTranslation.objects.create(master=valor(instalacao, "Parede"), language="en", name="Wall")
        self.variante.set_option_values({instalacao: valor(instalacao, "Parede")})
        with translation.override("en"):
            self.assertEqual(self.fresh(self.variante).label, "Preto · 25 cm · PLA · Wall")

    def test_the_cart_reads_the_label_with_the_options(self):
        instalacao = opcao(self.produto, "Instalação", "Parede")
        self.variante.set_option_values({instalacao: valor(instalacao, "Parede")})
        variantes = load_variants({"x": {"variant_id": self.variante.pk}})
        with self.assertNumQueries(0):
            self.assertEqual(variantes[self.variante.pk].label, "Preto · 25 cm · PLA · Parede")


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------


class OrderSnapshotTests(Base):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.user = make_user(username="cliente", email="cliente@example.com")
        self.address = make_address(self.user.customer, self.country)
        self.instalacao = opcao(self.produto, "Instalação", "Parede", sort_order=1, fr="Installation", nl="Montage", en="Installation")
        self.acabamento = opcao(self.produto, "Acabamento", "Fosco", sort_order=2, fr="Finition", nl="Afwerking", en="Finish")
        for nome, fr, nl, en in (("Parede", "Mur", "Muur", "Wall"), ("Fosco", "Mate", "Mat", "Matte")):
            v = ProductOptionValue.objects.get(name=nome)
            for idioma, texto in (("fr", fr), ("nl", nl), ("en", en)):
                ProductOptionValueTranslation.objects.create(master=v, language=idioma, name=texto)

    def pedido(self, variante, language="pt-br"):
        variante = load_variants({"x": {"variant_id": variante.pk}})[variante.pk]
        linha = CartLine(key=f"{self.produto.pk}:{variante.pk}:-", product=variante.product, variant=variante, quantity=1, customization=None, upload=None)
        return order_services.create_order(
            customer=self.user.customer, lines=[linha], shipping_address=self.address,
            billing_address=self.address, shipping_method=self.method, language=language,
        )

    def escolher(self):
        self.variante.set_option_values({self.instalacao: valor(self.instalacao, "Parede"), self.acabamento: valor(self.acabamento, "Fosco")})

    def test_an_order_without_options_has_an_empty_snapshot(self):
        item = self.pedido(self.variante).items.get()
        self.assertEqual(item.options_snapshot, "")
        self.assertEqual(item.variant_label, "Preto · 25 cm · PLA")
        self.assertEqual((item.color_name, item.size_name, item.material_name), ("Preto", "25 cm", "PLA"))

    def test_a_new_order_freezes_the_options(self):
        self.escolher()
        item = self.pedido(self.variante).items.get()
        self.assertEqual(item.options_snapshot, "Instalação: Parede · Acabamento: Fosco")
        self.assertEqual(item.variant_label, "Preto · 25 cm · PLA · Parede · Fosco")
        # Os campos de antes continuam iguais.
        self.assertEqual((item.color_name, item.size_name, item.material_name), ("Preto", "25 cm", "PLA"))
        self.assertEqual(item.sku, "REL-LEAO-001")

    def test_renaming_the_option_or_the_value_does_not_touch_the_order(self):
        self.escolher()
        item = self.pedido(self.variante).items.get()
        self.instalacao.name = "Fixação"
        self.instalacao.save()
        parede = valor(self.instalacao, "Parede")
        parede.name = "Na parede"
        parede.save()
        item.refresh_from_db()
        self.assertEqual(item.options_snapshot, "Instalação: Parede · Acabamento: Fosco")
        self.assertEqual(item.variant_label, "Preto · 25 cm · PLA · Parede · Fosco")

    def test_the_snapshot_follows_the_language_of_the_purchase(self):
        self.escolher()
        esperado = {
            "fr": "Installation: Mur · Finition: Mate",
            "nl": "Montage: Muur · Afwerking: Mat",
            "en": "Installation: Wall · Finish: Matte",
        }
        for idioma, texto in esperado.items():
            with self.subTest(idioma=idioma), translation.override(idioma):
                item = self.pedido(self.variante, language=idioma).items.get()
                self.assertEqual(item.options_snapshot, texto)

    def test_a_missing_translation_falls_back_to_portuguese(self):
        modelo = opcao(self.produto, "Modelo", "Decorado", sort_order=3)  # sem tradução
        self.variante.set_option_values({modelo: valor(modelo, "Decorado")})
        with translation.override("fr"):
            item = self.pedido(self.variante, language="fr").items.get()
        self.assertEqual(item.options_snapshot, "Modelo: Decorado")

    def test_the_variant_label_field_takes_five_hundred_characters(self):
        campo = OrderItem._meta.get_field("variant_label")
        self.assertEqual(campo.max_length, 500)
        item = OrderItem(order=self.pedido(self.variante), product=self.produto, product_name="x", variant_label="x" * 500)
        item.full_clean(exclude=["order"])
        self.assertEqual(OrderItem._meta.get_field("options_snapshot").get_internal_type(), "TextField")


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class QueryCountTests(Base):
    def test_labels_do_not_cost_a_query_per_variant(self):
        instalacao = opcao(self.produto, "Instalação", "Parede", "Mesa")
        acabamento = opcao(self.produto, "Acabamento", "Fosco")
        for numero in range(6):
            v = make_variant(self.produto, sku=f"MUITAS-{numero}", price=Decimal("1"), stock=1, size=f"{numero} cm")
            v.set_option_values({instalacao: valor(instalacao, "Parede" if numero % 2 else "Mesa"), acabamento: valor(acabamento, "Fosco")})

        def ler(quantas):
            variantes = list(
                ProductVariant.objects.filter(product=self.produto)
                .select_related("color", "material")
                .prefetch_related("color__translations", "material__translations", *variant_option_prefetches())[:quantas]
            )
            return [(v.label, v.options_text) for v in variantes]

        with CaptureQueriesContext(connection) as poucas:
            ler(2)
        with CaptureQueriesContext(connection) as muitas:
            ler(7)
        self.assertEqual(len(poucas), len(muitas))
        self.assertLessEqual(len(muitas), 6)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class SchemaMigrationTests(TransactionTestCase):
    """De antes (catalog 0012 / orders 0013) até 0013 / 0014.

    Só schema: produtos, variantes, SKUs, traduções, fotos e pedidos são
    fotografados antes e comparados depois; as tabelas novas nascem vazias e
    toda variante existente tem zero opções.
    """

    antes = (("catalog", "0012_derive_color_mode_and_composition"), ("orders", "0013_orderitem_colors_materials_snapshot"))
    depois = (("catalog", "0013_product_options"), ("orders", "0014_orderitem_options_snapshot"))

    def migrar(self, alvos):
        executor = MigrationExecutor(connection)
        executor.migrate(list(alvos))
        executor.loader.build_graph()
        return executor.loader.project_state(list(alvos)).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(list(self.depois))
        super().tearDown()

    def _dados(self, apps):
        Category = apps.get_model("categories", "Category")
        Product = apps.get_model("catalog", "Product")
        ProductTranslation = apps.get_model("catalog", "ProductTranslation")
        ProductVariant = apps.get_model("catalog", "ProductVariant")
        ProductMedia = apps.get_model("catalog", "ProductMedia")
        Color = apps.get_model("catalog", "Color")
        categoria = Category.objects.create(slug="modelos")
        preto = Color.objects.create(name="Preto", slug="preto")
        for sku in ("LEAO", "VASO"):
            p = Product.objects.create(sku=sku, slug=sku.lower(), status="active", category=categoria)
            ProductTranslation.objects.create(master=p, language="pt", name=sku)
            for n in range(2):
                ProductVariant.objects.create(product=p, sku=f"{sku}-V0{n + 1}", color=preto, size=f"{n} cm", sale_price=Decimal("10"), stock_quantity=n)
            ProductMedia.objects.create(product=p, file=f"products/{sku}/a.jpg", alt_text=sku)

    def _fotografia(self, apps):
        Product = apps.get_model("catalog", "Product")
        ProductVariant = apps.get_model("catalog", "ProductVariant")
        ProductTranslation = apps.get_model("catalog", "ProductTranslation")
        ProductMedia = apps.get_model("catalog", "ProductMedia")
        OrderItem = apps.get_model("orders", "OrderItem")
        return (
            list(Product.objects.order_by("pk").values_list("pk", "sku", "slug", "status")),
            list(ProductVariant.objects.order_by("pk").values_list("pk", "sku", "product_id", "color_id", "material_id", "size", "sale_price", "stock_quantity")),
            list(ProductTranslation.objects.order_by("pk").values_list("pk", "language", "name")),
            list(ProductMedia.objects.order_by("pk").values_list("pk", "file", "alt_text")),
            OrderItem.objects.count(),
        )

    def test_the_schema_migrations_touch_nothing(self):
        apps_antes = self.migrar(self.antes)
        self._dados(apps_antes)
        antes = self._fotografia(apps_antes)
        with connection.cursor() as cursor:
            tabelas = set(connection.introspection.table_names(cursor))
        self.assertNotIn("catalog_productoption", tabelas)

        apps_depois = self.migrar(self.depois)
        self.assertEqual(self._fotografia(apps_depois), antes)
        with connection.cursor() as cursor:
            tabelas = set(connection.introspection.table_names(cursor))
        for tabela in (
            "catalog_productoption", "catalog_productoptiontranslation", "catalog_productoptionvalue",
            "catalog_productoptionvaluetranslation", "catalog_productvariantoptionvalue",
        ):
            with self.subTest(tabela=tabela):
                self.assertIn(tabela, tabelas)
        Link = apps_depois.get_model("catalog", "ProductVariantOptionValue")
        self.assertEqual(Link.objects.count(), 0)
        OrderItem = apps_depois.get_model("orders", "OrderItem")
        self.assertEqual(OrderItem._meta.get_field("variant_label").max_length, 500)
        self.assertEqual(OrderItem._meta.get_field("options_snapshot").get_internal_type(), "TextField")

        # E volta: as tabelas somem, o resto fica.
        apps_volta = self.migrar(self.antes)
        self.assertEqual(self._fotografia(apps_volta), antes)
        with connection.cursor() as cursor:
            self.assertNotIn("catalog_productoption", set(connection.introspection.table_names(cursor)))
