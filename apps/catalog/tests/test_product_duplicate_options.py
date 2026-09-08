"""Etapa 3F — duplicar um produto copia as opções adicionais, e nada mais.

A duplicação continua sendo a tela de criação preenchida (``DuplicateAdminMixin``):
o clique não grava; "Salvar" cria o produto novo com o SKU seguinte livre, as
variantes com SKUs novos, as traduções, a paleta e a composição. Aqui entra o
que faltava: as linhas das variantes já vêm com as escolhas das opções
adicionais, e ao gravar nascem ``ProductOption``, ``ProductOptionValue`` e as
traduções **novas** no produto novo, com as variantes religadas por mapas
explícitos — nunca aos registros da origem.

Fora da cópia: pedidos, itens de pedido, carrinhos, favoritos, snapshots,
fotos e estoque. O original fica exatamente como estava.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import Favorite, User
from apps.cart.cart import CartLine, load_variants
from apps.cart.models import Cart, CartItem
from apps.catalog.models import (
    Color,
    Material,
    Product,
    ProductColor,
    ProductMaterialComposition,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductStatus,
    ProductVariant,
    ProductVariantOptionValue,
    copy_product_options,
)
from apps.core.admin_mixins import DUPLICATE_PARAM
from apps.core.testing import (
    make_address,
    make_bank_account,
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_user,
    translate_product,
)
from apps.core.tests_admin_duplicate import DuplicarBase
from apps.orders import services as order_services
from apps.orders.models import OrderItem


def opcao(produto, nome, *valores, sort_order=0, **traducoes):
    o = ProductOption.objects.create(product=produto, name=nome, sort_order=sort_order)
    for idioma, texto in traducoes.items():
        ProductOptionTranslation.objects.create(master=o, language=idioma, name=texto)
    for i, v in enumerate(valores):
        ProductOptionValue.objects.create(option=o, name=v, sort_order=i)
    return o


def traduzir_valor(valor, **traducoes):
    for idioma, texto in traducoes.items():
        ProductOptionValueTranslation.objects.create(master=valor, language=idioma, name=texto)


def valor(o, nome):
    return o.values.get(name=nome)


def retrato(produto):
    """Tudo do produto que a duplicação não pode mudar, em texto comparável."""
    produto = Product.objects.get(pk=produto.pk)
    return {
        "sku": produto.sku,
        "status": produto.status,
        "traducoes": sorted(produto.translations.values_list("pk", "language", "name")),
        "variantes": sorted(
            (v.pk, v.sku, v.color_id, v.size, v.material_id, str(v.sale_price), v.stock_quantity, v.label)
            for v in produto.variants.all()
        ),
        "opcoes": sorted((o.pk, o.name, o.sort_order) for o in produto.options.all()),
        "valores": sorted(
            (v.pk, v.option_id, v.name, v.sort_order) for v in ProductOptionValue.objects.filter(option__product=produto)
        ),
        "trad_opcoes": sorted(
            ProductOptionTranslation.objects.filter(master__product=produto).values_list("pk", "language", "name")
        ),
        "trad_valores": sorted(
            ProductOptionValueTranslation.objects.filter(master__option__product=produto).values_list("pk", "language", "name")
        ),
        "links": sorted(
            ProductVariantOptionValue.objects.filter(variant__product=produto).values_list("pk", "variant_id", "option_id", "value_id")
        ),
        "cores": sorted(produto.product_colors.values_list("pk", "color_id", "sort_order")),
        "materiais": sorted(produto.material_composition.values_list("pk", "material_id", "percentage", "sort_order")),
        "fotos": sorted(produto.media.values_list("pk", flat=True)),
    }


class DuplicarOpcoesBase(DuplicarBase):
    def setUp(self):
        super().setUp()
        self.categoria = make_category(slug="religiosos", name="Religiosos")
        self.branco = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.pla = Material.objects.create(name="PLA")
        self.petg = Material.objects.create(name="PETG")

    def produto(self, sku="REL-LEAO-001", nome="Suporte Leão de Judá", variantes=()):
        p = make_product(sku=sku, name=nome, category=self.categoria, status=ProductStatus.ACTIVE, with_variant=False)
        translate_product(p, "fr", "Support Lion de Juda")
        for i, dados in enumerate(variantes, start=1):
            dados = dict(dados)
            escolhas = dados.pop("escolhas", None)
            v = ProductVariant.objects.create(
                product=p, sku=dados.pop("sku", f"{sku}-V{i:02d}"), sale_price=Decimal(dados.pop("price", "30.00")),
                stock_quantity=dados.pop("stock", 5), weight_grams=Decimal("150"), production_lead_time_days=2, **dados,
            )
            if escolhas:
                v.set_option_values(escolhas)
        return p

    def duas_opcoes(self, p):
        inst = opcao(p, "Instalação", "Mesa", "Parede", sort_order=1, fr="Installation", nl="Installatie", en="Installation")
        acab = opcao(p, "Acabamento", "Fosco", "Brilhante", sort_order=2, fr="Finition", nl="Afwerking", en="Finish")
        traduzir_valor(valor(inst, "Parede"), fr="Mur", nl="Muur", en="Wall")
        traduzir_valor(valor(inst, "Mesa"), fr="Table")
        traduzir_valor(valor(acab, "Fosco"), fr="Mat", nl="Mat", en="Matte")
        return inst, acab

    def leao(self):
        """REL-LEAO-001 com V01 = Mesa+Fosco, V02 = Parede+Fosco, V03 = Parede+Brilhante."""
        p = self.produto()
        inst, acab = self.duas_opcoes(p)
        m, pa, f, b = valor(inst, "Mesa"), valor(inst, "Parede"), valor(acab, "Fosco"), valor(acab, "Brilhante")
        eixos = dict(color=self.branco, size="25 cm", material=self.pla)
        self.produto_variantes(p, [
            dict(escolhas={inst: m, acab: f}, price="30.00", **eixos),
            dict(escolhas={inst: pa, acab: f}, price="32.00", **eixos),
            dict(escolhas={inst: pa, acab: b}, price="35.00", **eixos),
        ])
        return p

    def produto_variantes(self, p, variantes):
        for i, dados in enumerate(variantes, start=1):
            dados = dict(dados)
            escolhas = dados.pop("escolhas", None)
            v = ProductVariant.objects.create(
                product=p, sku=dados.pop("sku", f"{p.sku}-V{i:02d}"), sale_price=Decimal(dados.pop("price", "30.00")),
                stock_quantity=dados.pop("stock", 5), weight_grams=Decimal("150"), production_lead_time_days=2, **dados,
            )
            if escolhas:
                v.set_option_values(escolhas)

    def duplicar(self, p, **alteracoes):
        destino, campos = self.tela_de_criacao(p)
        resposta = self.salvar(destino, campos, **alteracoes)
        self.assertCriou(resposta)
        return Product.objects.exclude(pk=p.pk).order_by("-pk").first()

    def escolhas(self, variante):
        return {link.option.name: link.value.name for link in variante.option_links}


# ---------------------------------------------------------------------------
# A tela
# ---------------------------------------------------------------------------


class TelaTests(DuplicarOpcoesBase):
    def test_the_variant_rows_come_with_the_option_choices_of_the_source(self):
        p = self.leao()
        inst, acab = p.options.order_by("sort_order")
        _destino, campos = self.tela_de_criacao(p)
        self.assertEqual(campos["sku"], "REL-LEAO-002")
        linhas = {}
        for chave, v in campos.items():
            if chave.startswith("variants-") and chave.endswith("-sku") and v:
                idx = chave.split("-")[1]
                linhas[v] = (campos.get(f"variants-{idx}-opt_{inst.pk}"), campos.get(f"variants-{idx}-opt_{acab.pk}"))
        self.assertEqual(linhas, {
            "REL-LEAO-002-V01": (str(valor(inst, "Mesa").pk), str(valor(acab, "Fosco").pk)),
            "REL-LEAO-002-V02": (str(valor(inst, "Parede").pk), str(valor(acab, "Fosco").pk)),
            "REL-LEAO-002-V03": (str(valor(inst, "Parede").pk), str(valor(acab, "Brilhante").pk)),
        })
        self.assertIn("Opções adicionais", self.html)
        self.assertIn("as 2 opção(ões) adicionais de", self.html)
        self.assertIn("<b>REL-LEAO-001</b> (4 valor(es), com as", self.html)
        self.assertNotIn("Salve o produto primeiro para adicionar", self.html)

    def test_a_product_without_options_shows_no_option_fields(self):
        p = self.produto(variantes=[dict(color=self.preto)])
        _destino, campos = self.tela_de_criacao(p)
        self.assertFalse([k for k in campos if "-opt_" in k])
        self.assertEqual(campos["sku"], "REL-LEAO-002")
        self.assertIn("não tem opções adicionais; nada será copiado", self.html)

    def test_the_click_creates_nothing(self):
        p = self.leao()
        antes = (Product.objects.count(), ProductOption.objects.count(), ProductOptionValue.objects.count())
        self.tela_de_criacao(p)
        self.assertEqual(antes, (Product.objects.count(), ProductOption.objects.count(), ProductOptionValue.objects.count()))

    def test_the_screen_does_not_cost_a_query_per_variant_or_option(self):
        p = self.leao()
        destino = f"{self.url(p, 'add')}?{DUPLICATE_PARAM}={p.pk}"
        with CaptureQueriesContext(connection) as poucas:
            self.client.get(destino)
        # Cinco opções a mais, com valores e traduções, em todas as variantes:
        # o custo da tela não muda. (Cada linha de variante já custa o seu — a
        # sugestão de SKU e os selects de cor e material — desde antes desta
        # etapa; aqui o número de variantes fica o mesmo.)
        variantes = list(p.variants.order_by("pk"))
        for n in range(3, 8):
            o = opcao(p, f"Opção {n}", "A", "B", "C", sort_order=n, fr=f"Option {n}", nl=f"Optie {n}")
            traduzir_valor(valor(o, "A"), fr="A-fr")
            for i, v in enumerate(variantes):
                escolhas = {l.option: l.value for l in v.option_links}
                escolhas[o] = valor(o, "ABC"[i])
                v.set_option_values(escolhas)
        with CaptureQueriesContext(connection) as muitas:
            self.client.get(destino)
        self.assertEqual(len(poucas), len(muitas))


# ---------------------------------------------------------------------------
# Salvar: sem opção, uma, várias
# ---------------------------------------------------------------------------


class SalvarTests(DuplicarOpcoesBase):
    def test_a_product_without_options_duplicates_exactly_as_before(self):
        p = self.produto(sku="REL-CRUZ-001", nome="Cruz", variantes=[dict(color=self.preto)])
        copia = self.duplicar(p)
        self.assertEqual(copia.sku, "REL-CRUZ-002")
        self.assertEqual(list(copia.variants.values_list("sku", flat=True)), ["REL-CRUZ-002-V01"])
        self.assertEqual(copia.options.count(), 0)
        self.assertEqual(ProductVariantOptionValue.objects.filter(variant__product=copia).count(), 0)
        self.assertEqual(copia.variants.get().stock_quantity, 0)
        self.assertEqual(copia.variants.get().color, self.preto)

    def test_one_option(self):
        p = self.produto()
        inst = opcao(p, "Instalação", "Mesa", "Parede", sort_order=1, fr="Installation", nl="Installatie", en="Installation")
        traduzir_valor(valor(inst, "Parede"), fr="Mur")
        self.produto_variantes(p, [dict(escolhas={inst: valor(inst, "Mesa")}), dict(escolhas={inst: valor(inst, "Parede")})])
        copia = self.duplicar(p)
        nova = copia.options.get()
        self.assertNotEqual(nova.pk, inst.pk)
        self.assertEqual((nova.name, nova.sort_order), ("Instalação", 1))
        self.assertEqual(sorted(nova.translations.values_list("language", "name")), [("en", "Installation"), ("fr", "Installation"), ("nl", "Installatie")])
        self.assertEqual(list(nova.values.order_by("sort_order").values_list("name", flat=True)), ["Mesa", "Parede"])
        self.assertEqual(list(nova.values.get(name="Parede").translations.values_list("language", "name")), [("fr", "Mur")])
        por_sku = {v.sku: v for v in copia.variants.all()}
        self.assertEqual(self.escolhas(por_sku["REL-LEAO-002-V01"]), {"Instalação": "Mesa"})
        self.assertEqual(self.escolhas(por_sku["REL-LEAO-002-V02"]), {"Instalação": "Parede"})
        self.assertEqual(por_sku["REL-LEAO-002-V02"].label, "Parede")

    def test_many_options_many_values_many_variants(self):
        p = self.produto()
        inst, acab = self.duas_opcoes(p)
        modelo = opcao(p, "Modelo", "Simples", "Decorado", sort_order=3)
        combos = [("Mesa", "Fosco", "Simples"), ("Parede", "Fosco", "Decorado"), ("Parede", "Brilhante", "Simples"), ("Mesa", "Brilhante", "Decorado")]
        self.produto_variantes(p, [
            dict(escolhas={inst: valor(inst, i), acab: valor(acab, a), modelo: valor(modelo, m)}, price=f"{30 + n}.00")
            for n, (i, a, m) in enumerate(combos)
        ])
        copia = self.duplicar(p)
        self.assertEqual(list(copia.options.order_by("sort_order").values_list("name", "sort_order")), [("Instalação", 1), ("Acabamento", 2), ("Modelo", 3)])
        esperado = {f"REL-LEAO-002-V{n + 1:02d}": {"Instalação": i, "Acabamento": a, "Modelo": m} for n, (i, a, m) in enumerate(combos)}
        self.assertEqual({v.sku: self.escolhas(v) for v in copia.variants.all()}, esperado)
        self.assertEqual({v.sku: str(v.sale_price) for v in copia.variants.all()}, {f"REL-LEAO-002-V{n + 1:02d}": f"{30 + n}.00" for n in range(4)})
        self.assertEqual(copia.variants.get(sku="REL-LEAO-002-V02").label, "Parede · Fosco · Decorado")

    def test_the_full_lion_scenario(self):
        p = self.leao()
        copia = self.duplicar(p)
        self.assertEqual(copia.sku, "REL-LEAO-002")
        self.assertEqual(sorted(copia.variants.values_list("sku", flat=True)), ["REL-LEAO-002-V01", "REL-LEAO-002-V02", "REL-LEAO-002-V03"])
        por_sku = {v.sku: v for v in copia.variants.all()}
        self.assertEqual(por_sku["REL-LEAO-002-V01"].label, "Branco · 25 cm · PLA · Mesa · Fosco")
        self.assertEqual(por_sku["REL-LEAO-002-V02"].label, "Branco · 25 cm · PLA · Parede · Fosco")
        self.assertEqual(por_sku["REL-LEAO-002-V03"].label, "Branco · 25 cm · PLA · Parede · Brilhante")
        self.assertEqual(por_sku["REL-LEAO-002-V02"].options_text, "Instalação: Parede · Acabamento: Fosco")
        self.assertEqual(por_sku["REL-LEAO-002-V02"].stock_quantity, 0)  # estoque nunca acompanha
        self.assertEqual(str(por_sku["REL-LEAO-002-V03"].sale_price), "35.00")
        self.assertEqual(copia.translations.count(), 2)
        self.assertEqual(copia.name_in("fr"), "Support Lion de Juda")

    def test_the_sort_order_of_options_and_values_is_kept_even_when_it_is_not_the_creation_order(self):
        p = self.produto()
        z = opcao(p, "Zona", "Norte", "Sul", sort_order=3)
        a = opcao(p, "Acabamento", "Fosco", sort_order=1)
        ProductOptionValue.objects.filter(option=z, name="Sul").update(sort_order=0)
        ProductOptionValue.objects.filter(option=z, name="Norte").update(sort_order=1)
        self.produto_variantes(p, [dict(escolhas={z: valor(z, "Sul"), a: valor(a, "Fosco")})])
        copia = self.duplicar(p)
        self.assertEqual(list(copia.options.order_by("sort_order", "id").values_list("name", "sort_order")), [("Acabamento", 1), ("Zona", 3)])
        zona = copia.options.get(name="Zona")
        self.assertEqual(list(zona.values.order_by("sort_order", "id").values_list("name", "sort_order")), [("Sul", 0), ("Norte", 1)])
        self.assertEqual(copia.variants.get().label, "Fosco · Sul")

    def test_all_four_languages_are_copied_and_nothing_is_invented(self):
        p = self.leao()
        copia = self.duplicar(p)
        inst = copia.options.get(name="Instalação")
        self.assertEqual(dict(inst.translations.values_list("language", "name")), {"fr": "Installation", "nl": "Installatie", "en": "Installation"})
        self.assertEqual(dict(inst.values.get(name="Parede").translations.values_list("language", "name")), {"fr": "Mur", "nl": "Muur", "en": "Wall"})
        self.assertEqual(dict(inst.values.get(name="Mesa").translations.values_list("language", "name")), {"fr": "Table"})
        brilhante = copia.options.get(name="Acabamento").values.get(name="Brilhante")
        self.assertEqual(brilhante.translations.count(), 0)
        self.assertEqual(ProductOptionTranslation.objects.filter(master__product=copia).count(), 6)
        self.assertEqual(ProductOptionValueTranslation.objects.filter(master__option__product=copia).count(), 7)

    def test_a_variant_that_does_not_answer_an_option_stays_that_way(self):
        p = self.produto()
        inst, _acab = self.duas_opcoes(p)
        self.produto_variantes(p, [dict(escolhas={inst: valor(inst, "Mesa")}), dict(size="30 cm")])
        copia = self.duplicar(p)
        por_sku = {v.sku: v for v in copia.variants.all()}
        self.assertEqual(self.escolhas(por_sku["REL-LEAO-002-V01"]), {"Instalação": "Mesa"})
        self.assertEqual(self.escolhas(por_sku["REL-LEAO-002-V02"]), {})
        self.assertEqual(copia.options.count(), 2)  # a opção existe no produto, mesmo sem variante usando

    def test_the_operator_can_change_a_choice_before_saving(self):
        p = self.leao()
        inst, acab = p.options.order_by("sort_order")
        destino, campos = self.tela_de_criacao(p)
        idx = next(k.split("-")[1] for k, v in campos.items() if v == "REL-LEAO-002-V01")
        resposta = self.salvar(destino, campos, **{f"variants-{idx}-opt_{acab.pk}": str(valor(acab, "Brilhante").pk)})
        self.assertCriou(resposta)
        copia = Product.objects.get(sku="REL-LEAO-002")
        self.assertEqual(self.escolhas(copia.variants.get(sku="REL-LEAO-002-V01")), {"Instalação": "Mesa", "Acabamento": "Brilhante"})

    def test_the_success_message_names_the_copy_and_what_was_copied(self):
        p = self.leao()
        destino, campos = self.tela_de_criacao(p)
        resposta = self.client.post(destino, {**campos, "_save": "Salvar"}, follow=True)
        texto = resposta.content.decode()
        self.assertIn("REL-LEAO-002", texto)
        self.assertIn("Opções adicionais copiadas de REL-LEAO-001: 2 opção(ões) e 4 valor(es)", texto)


# ---------------------------------------------------------------------------
# Integridade: nada aponta para a origem
# ---------------------------------------------------------------------------


class IntegridadeTests(DuplicarOpcoesBase):
    def test_every_option_value_translation_and_link_is_new_and_belongs_to_the_copy(self):
        p = self.leao()
        antes = retrato(p)
        copia = self.duplicar(p)

        opcoes_origem = set(p.options.values_list("pk", flat=True))
        valores_origem = set(ProductOptionValue.objects.filter(option__product=p).values_list("pk", flat=True))
        links_origem = set(ProductVariantOptionValue.objects.filter(variant__product=p).values_list("pk", flat=True))
        trad_o_origem = set(ProductOptionTranslation.objects.filter(master__product=p).values_list("pk", flat=True))
        trad_v_origem = set(ProductOptionValueTranslation.objects.filter(master__option__product=p).values_list("pk", flat=True))

        for o in copia.options.all():
            self.assertNotIn(o.pk, opcoes_origem)
            self.assertEqual(o.product_id, copia.pk)
            for t in o.translations.all():
                self.assertNotIn(t.pk, trad_o_origem)
                self.assertEqual(t.master_id, o.pk)
            for v in o.values.all():
                self.assertNotIn(v.pk, valores_origem)
                self.assertEqual(v.option.product_id, copia.pk)
                for t in v.translations.all():
                    self.assertNotIn(t.pk, trad_v_origem)
                    self.assertEqual(t.master_id, v.pk)
        links = list(ProductVariantOptionValue.objects.filter(variant__product=copia).select_related("option", "value__option", "variant"))
        self.assertEqual(len(links), 6)
        for link in links:
            self.assertNotIn(link.pk, links_origem)
            self.assertNotIn(link.option_id, opcoes_origem)
            self.assertNotIn(link.value_id, valores_origem)
            self.assertEqual(link.option.product_id, copia.pk)
            self.assertEqual(link.value.option.product_id, copia.pk)
            self.assertEqual(link.value.option_id, link.option_id)
            self.assertEqual(link.variant.product_id, copia.pk)
        # e a origem não ganhou nem perdeu nada
        self.assertEqual(retrato(p), antes)
        self.assertEqual(ProductVariantOptionValue.objects.filter(variant__product=p).count(), 6)

    def test_the_original_is_untouched_in_every_detail(self):
        p = self.leao()
        ProductColor.objects.create(product=p, color=self.branco, sort_order=0)
        ProductColor.objects.create(product=p, color=self.preto, sort_order=1)
        ProductMaterialComposition.objects.create(product=p, material=self.pla, percentage=Decimal("80"))
        ProductMaterialComposition.objects.create(product=p, material=self.petg, percentage=Decimal("20"), sort_order=1)
        antes = retrato(p)
        copia = self.duplicar(p)
        self.assertEqual(retrato(p), antes)
        self.assertNotEqual(copia.pk, p.pk)
        self.assertEqual(Product.objects.get(pk=p.pk).variants.get(sku="REL-LEAO-001-V02").stock_quantity, 5)

    def test_palette_and_composition_keep_pointing_to_the_same_global_colours_and_materials(self):
        p = self.leao()
        ProductColor.objects.create(product=p, color=self.branco, sort_order=0)
        ProductColor.objects.create(product=p, color=self.preto, sort_order=1)
        ProductMaterialComposition.objects.create(product=p, material=self.pla, percentage=Decimal("80"))
        ProductMaterialComposition.objects.create(product=p, material=self.petg, percentage=Decimal("20"), sort_order=1)
        cores, materiais = Color.objects.count(), Material.objects.count()
        copia = self.duplicar(p)
        self.assertEqual(list(copia.product_colors.order_by("sort_order").values_list("color_id", "sort_order")), [(self.branco.pk, 0), (self.preto.pk, 1)])
        self.assertEqual(list(copia.material_composition.order_by("sort_order").values_list("material_id", "percentage")), [(self.pla.pk, Decimal("80.00")), (self.petg.pk, Decimal("20.00"))])
        self.assertEqual((Color.objects.count(), Material.objects.count()), (cores, materiais))
        self.assertFalse(set(copia.product_colors.values_list("pk", flat=True)) & set(p.product_colors.values_list("pk", flat=True)))

    def test_personalisation_and_media_follow_the_current_rule(self):
        p = self.leao()
        p.personalization_type = "text"
        p.save()
        copia = self.duplicar(p)
        self.assertEqual(copia.personalization_type, "text")  # campo do produto: acompanha
        self.assertEqual(copia.media.count(), 0)  # fotos: nunca (é vínculo, não cópia)

    def test_the_service_refuses_nonsense(self):
        p = self.leao()
        with self.assertRaises(ValueError):
            copy_product_options(p, p)
        with self.assertRaises(ValueError):
            copy_product_options(p, Product(sku="X"))

    def test_the_service_alone_copies_in_a_fixed_number_of_queries(self):
        p = self.leao()
        destino = make_product(sku="DEST-1", name="Destino", category=self.categoria, with_variant=False)
        with CaptureQueriesContext(connection) as poucas:
            mapas = copy_product_options(p, destino)
        self.assertEqual(set(mapas[0]), set(p.options.values_list("pk", flat=True)))
        self.assertEqual(len(mapas[1]), 4)
        for n in range(3, 9):
            o = opcao(p, f"Opção {n}", "A", "B", "C", sort_order=n, fr=f"Option {n}")
            traduzir_valor(valor(o, "A"), fr="A-fr", nl="A-nl")
        destino2 = make_product(sku="DEST-2", name="Destino 2", category=self.categoria, with_variant=False)
        with CaptureQueriesContext(connection) as muitas:
            mapas = copy_product_options(p, destino2)
        self.assertEqual(len(mapas[0]), 8)
        self.assertEqual(len(poucas), len(muitas))


# ---------------------------------------------------------------------------
# SKU
# ---------------------------------------------------------------------------


class SkuTests(DuplicarOpcoesBase):
    def test_duplicating_twice_and_thrice_walks_the_sequence(self):
        p = self.leao()
        c2 = self.duplicar(p)
        c3 = self.duplicar(p)
        c4 = self.duplicar(c2)
        self.assertEqual((c2.sku, c3.sku, c4.sku), ("REL-LEAO-002", "REL-LEAO-003", "REL-LEAO-004"))
        for copia in (c2, c3, c4):
            self.assertEqual(sorted(copia.variants.values_list("sku", flat=True)), [f"{copia.sku}-V0{n}" for n in (1, 2, 3)])
            self.assertEqual(copia.options.count(), 2)
            self.assertEqual(ProductVariantOptionValue.objects.filter(variant__product=copia).count(), 6)
        self.assertEqual(ProductVariant.objects.values("sku").distinct().count(), ProductVariant.objects.count())
        self.assertEqual(Product.objects.get(pk=p.pk).sku, "REL-LEAO-001")

    def test_when_the_next_sku_is_taken_the_screen_suggests_the_next_free_one(self):
        p = self.leao()
        make_product(sku="REL-LEAO-002", name="Já existe", category=self.categoria)
        _destino, campos = self.tela_de_criacao(p)
        self.assertEqual(campos["sku"], "REL-LEAO-003")
        skus = {v for k, v in campos.items() if k.startswith("variants-") and k.endswith("-sku") and v}
        self.assertEqual(skus, {"REL-LEAO-003-V01", "REL-LEAO-003-V02", "REL-LEAO-003-V03"})

    def test_a_collision_between_the_screen_and_the_save_is_refused_without_leaving_anything_behind(self):
        p = self.leao()
        destino, campos = self.tela_de_criacao(p)
        make_product(sku="REL-LEAO-002", name="Chegou antes", category=self.categoria)  # outra pessoa levou o SKU
        antes = (Product.objects.count(), ProductOption.objects.count(), ProductOptionValue.objects.count(), ProductVariant.objects.count())
        resposta = self.salvar(destino, campos)
        self.assertRecusou(resposta, "já existe")
        self.assertEqual(antes, (Product.objects.count(), ProductOption.objects.count(), ProductOptionValue.objects.count(), ProductVariant.objects.count()))

    def test_the_original_sku_is_refused(self):
        p = self.leao()
        destino, campos = self.tela_de_criacao(p)
        resposta = self.salvar(destino, campos, sku="REL-LEAO-001")
        self.assertRecusou(resposta, "já existe")
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(ProductOption.objects.count(), 2)

    def test_many_variants(self):
        p = self.produto()
        inst = opcao(p, "Instalação", *[f"Lugar {n}" for n in range(12)], sort_order=1)
        self.produto_variantes(p, [dict(escolhas={inst: valor(inst, f"Lugar {n}")}, size=f"{n} cm") for n in range(12)])
        copia = self.duplicar(p)
        self.assertEqual(copia.variants.count(), 12)
        self.assertEqual(sorted(copia.variants.values_list("sku", flat=True)), [f"REL-LEAO-002-V{n:02d}" for n in range(1, 13)])
        self.assertEqual({self.escolhas(v)["Instalação"] for v in copia.variants.all()}, {f"Lugar {n}" for n in range(12)})


# ---------------------------------------------------------------------------
# Histórico: nada transacional acompanha
# ---------------------------------------------------------------------------


class HistoricoTests(DuplicarOpcoesBase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE", vat_rate="21.00")
        self.method = make_method(min_days=2, max_days=3)
        make_rate(self.method, self.country, 0, 5000, "5.90")
        make_bank_account()
        self.cliente = make_user(username="cliente", email="cliente@example.com")
        self.address = make_address(self.cliente.customer, self.country)

    def test_orders_carts_favourites_and_snapshots_stay_with_the_original(self):
        p = self.leao()
        v02 = p.variants.get(sku="REL-LEAO-001-V02")
        carregada = load_variants({"x": {"variant_id": v02.pk}})[v02.pk]
        order = order_services.create_order(
            customer=self.cliente.customer, lines=[CartLine(key="k", product=carregada.product, variant=carregada, quantity=1, customization=None, upload=None)],
            shipping_address=self.address, billing_address=self.address, shipping_method=self.method, language="pt-br",
        )
        carrinho = Cart.objects.create(user=self.cliente)
        CartItem.objects.create(cart=carrinho, line_key="k", product=p, variant=v02, quantity=2)
        Favorite.objects.create(user=self.cliente, product=p)
        snapshot = OrderItem.objects.get(order=order).options_snapshot
        self.assertEqual(snapshot, "Instalação: Parede · Acabamento: Fosco")

        copia = self.duplicar(p)

        self.assertEqual(copia.order_items.count(), 0)
        self.assertEqual(copia.cart_items.count(), 0)
        self.assertEqual(Favorite.objects.filter(product=copia).count(), 0)
        self.assertEqual(ProductVariant.objects.filter(product=copia, order_items__isnull=False).count(), 0)
        self.assertEqual(p.order_items.count(), 1)
        self.assertEqual(p.cart_items.count(), 1)
        self.assertEqual(Favorite.objects.filter(product=p).count(), 1)
        item = OrderItem.objects.get(order=order)
        self.assertEqual((item.product_id, item.variant_id, item.options_snapshot, item.variant_label, item.sku),
                         (p.pk, v02.pk, snapshot, "Branco · 25 cm · PLA · Parede · Fosco", "REL-LEAO-001-V02"))
        self.assertEqual(CartItem.objects.get().variant_id, v02.pk)


# ---------------------------------------------------------------------------
# Transação e permissões
# ---------------------------------------------------------------------------


class TransacaoTests(DuplicarOpcoesBase):
    def test_a_failure_while_copying_the_options_leaves_nothing_behind(self):
        p = self.leao()
        destino, campos = self.tela_de_criacao(p)
        antes = (Product.objects.count(), ProductOption.objects.count(), ProductOptionValue.objects.count(),
                 ProductVariant.objects.count(), ProductVariantOptionValue.objects.count(), ProductOptionTranslation.objects.count())
        with mock.patch("apps.catalog.models.ProductOptionValueTranslation.objects.bulk_create", side_effect=RuntimeError("banco caiu")):
            with self.assertRaises(RuntimeError):
                self.client.post(destino, {**campos, "_save": "Salvar"})
        self.assertEqual(antes, (Product.objects.count(), ProductOption.objects.count(), ProductOptionValue.objects.count(),
                                 ProductVariant.objects.count(), ProductVariantOptionValue.objects.count(), ProductOptionTranslation.objects.count()))

    def test_a_failure_in_a_variant_leaves_no_options_behind(self):
        p = self.leao()
        destino, campos = self.tela_de_criacao(p)
        idx = next(k.split("-")[1] for k, v in campos.items() if v == "REL-LEAO-002-V03")
        antes = (Product.objects.count(), ProductOption.objects.count())
        # V03 com o mesmo SKU de V01: o formset recusa; nada é gravado
        resposta = self.salvar(destino, campos, **{f"variants-{idx}-sku": "REL-LEAO-002-V01"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(antes, (Product.objects.count(), ProductOption.objects.count()))

    def test_a_forged_choice_of_another_product_is_refused(self):
        p = self.leao()
        outro = self.produto(sku="OUTRO-001", nome="Outro")
        alheia = opcao(outro, "Instalação", "Teto")
        inst = p.options.get(name="Instalação")
        destino, campos = self.tela_de_criacao(p)
        idx = next(k.split("-")[1] for k, v in campos.items() if v == "REL-LEAO-002-V01")
        resposta = self.salvar(destino, campos, **{f"variants-{idx}-opt_{inst.pk}": str(valor(alheia, "Teto").pk)})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Product.objects.count(), 2)
        self.assertFalse(ProductVariantOptionValue.objects.filter(value__option=alheia).exists())


class PermissaoTests(DuplicarOpcoesBase):
    def operador(self, *codenames):
        u = User.objects.create_user("operador", "op@jdprint.test", self.SENHA, is_staff=True)
        u.user_permissions.set(Permission.objects.filter(codename__in=codenames, content_type__app_label="catalog"))
        self.client.force_login(u)
        return u

    def test_without_add_permission_the_screen_is_denied_and_nothing_is_created(self):
        p = self.leao()
        self.operador("view_product", "view_productvariant", "view_productoption")
        destino = f"{self.url(p, 'add')}?{DUPLICATE_PARAM}={p.pk}"
        self.assertEqual(self.client.get(destino).status_code, 403)
        self.assertEqual(self.client.post(destino, {"sku": "REL-LEAO-002", "_save": "1"}).status_code, 403)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(ProductOption.objects.count(), 2)

    def test_who_can_add_but_not_view_gets_an_empty_screen_without_the_options(self):
        p = self.leao()
        ProductOption.objects.filter(product=p, name="Instalação").update(name="Fixação especial XYZ")
        self.operador("add_product", "add_productvariant", "add_producttranslation", "add_productcolor", "add_productmaterialcomposition")
        destino = f"{self.url(p, 'add')}?{DUPLICATE_PARAM}={p.pk}"
        html = self.client.get(destino).content.decode()
        self.assertNotIn("REL-LEAO-002", html)
        self.assertNotIn(f'name="variants-0-opt_{p.options.first().pk}"', html)
        self.assertNotIn("Fixação especial XYZ", html)
        self.assertNotIn("Support Lion de Juda", html)

    def test_the_duplicate_action_is_offered_to_who_can_add(self):
        p = self.leao()
        html = self.client.get(reverse("admin:catalog_product_change", args=[p.pk])).content.decode()
        self.assertIn(f"{DUPLICATE_PARAM}={p.pk}", html)
