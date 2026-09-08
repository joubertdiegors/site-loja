"""Auditoria 3G — regressões dos achados e das garantias conferidas.

* uma variante com escolhas não muda de produto (os vínculos apontariam para
  opções de outro produto);
* a assinatura da combinação não depende da ordem em que as escolhas chegam;
* depois de duplicar, original e cópia não compartilham nada mutável: mudar
  um não muda o outro, nos dois sentidos;
* o caminho de corrida conhecido termina recusado e sem resto quando passa
  pelo endpoint do modal (a transação desfaz a variante).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import (
    Color,
    Material,
    Product,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductStatus,
    ProductVariant,
    ProductVariantOptionValue,
)
from apps.catalog.tests.test_product_duplicate_options import DuplicarOpcoesBase, opcao, valor
from apps.core.testing import make_category, make_product


class Base(TestCase):
    def setUp(self):
        self.cat = make_category(slug="c", name="C")
        self.preto = Color.objects.create(name="Preto", hex_code="#000000")
        self.pla = Material.objects.create(name="PLA")
        self.p = make_product(sku="A-001", name="A", category=self.cat, status=ProductStatus.ACTIVE, with_variant=False)
        self.inst = opcao(self.p, "Instalação", "Mesa", "Parede", sort_order=1)
        self.acab = opcao(self.p, "Acabamento", "Fosco", "Brilhante", sort_order=2)

    def variante(self, sku, produto=None, **eixos):
        eixos.setdefault("color", self.preto)
        eixos.setdefault("material", self.pla)
        eixos.setdefault("size", "20 cm")
        return ProductVariant.objects.create(
            product=produto or self.p, sku=sku, sale_price=Decimal("10"), stock_quantity=5, weight_grams=Decimal("10"), **eixos
        )


class VariantProductSwitchTests(Base):
    def test_a_variant_with_choices_cannot_move_to_another_product(self):
        v = self.variante("A-001-V01")
        v.set_option_values({self.inst: valor(self.inst, "Parede")})
        outro = make_product(sku="B-001", name="B", category=self.cat, status=ProductStatus.ACTIVE, with_variant=False)
        v.product = outro
        with self.assertRaises(ValidationError) as ctx:
            v.full_clean()
        self.assertIn("product", ctx.exception.message_dict)
        self.assertEqual(ProductVariant.objects.get(pk=v.pk).product_id, self.p.pk)

    def test_without_choices_the_move_is_still_allowed(self):
        v = self.variante("A-001-V01")
        outro = make_product(sku="B-001", name="B", category=self.cat, status=ProductStatus.ACTIVE, with_variant=False)
        v.product = outro
        v.full_clean()
        v.save()
        self.assertEqual(ProductVariant.objects.get(pk=v.pk).product_id, outro.pk)

    def test_after_removing_the_choices_the_move_is_allowed(self):
        v = self.variante("A-001-V01")
        v.set_option_values({self.inst: valor(self.inst, "Parede")})
        v.set_option_values({self.inst: None})
        outro = make_product(sku="B-001", name="B", category=self.cat, status=ProductStatus.ACTIVE, with_variant=False)
        v.product = outro
        v.full_clean()
        v.save()
        self.assertEqual(ProductVariantOptionValue.objects.filter(variant=v).count(), 0)

    def test_the_standalone_admin_refuses_the_move_with_a_message(self):
        from django.contrib.auth import get_user_model

        chefe = get_user_model().objects.create_superuser("chefe", "c@x.test", "x")
        self.client.force_login(chefe)
        v = self.variante("A-001-V01")
        v.set_option_values({self.inst: valor(self.inst, "Parede")})
        outro = make_product(sku="B-001", name="B", category=self.cat, status=ProductStatus.ACTIVE, with_variant=False)
        url = reverse("admin:catalog_productvariant_change", args=[v.pk])
        campos = self.client.get(url).context["adminform"].form.initial
        dados = {k: ("" if val is None else val) for k, val in campos.items()}
        dados.update({"product": outro.pk, "sku": v.sku, "_save": "1"})
        resposta = self.client.post(url, dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Remova as escolhas antes", resposta.content.decode())
        self.assertEqual(ProductVariant.objects.get(pk=v.pk).product_id, self.p.pk)


class CombinationSignatureTests(Base):
    def test_the_order_of_the_choices_does_not_matter(self):
        a = self.variante("A-001-V01")
        a.set_option_values({self.inst: valor(self.inst, "Parede"), self.acab: valor(self.acab, "Fosco")})
        b = self.variante("A-001-V02")
        with self.assertRaises(ValidationError):
            b.set_option_values({self.acab: valor(self.acab, "Fosco"), self.inst: valor(self.inst, "Parede")})
        self.assertEqual(ProductVariantOptionValue.objects.filter(variant=b).count(), 0)
        b.set_option_values({self.acab: valor(self.acab, "Brilhante"), self.inst: valor(self.inst, "Parede")})  # diferente: passa
        self.assertEqual(b.label, "Preto · 20 cm · PLA · Parede · Brilhante")

    def test_a_partial_combination_is_not_the_full_one(self):
        a = self.variante("A-001-V01")
        a.set_option_values({self.inst: valor(self.inst, "Parede"), self.acab: valor(self.acab, "Fosco")})
        b = self.variante("A-001-V02")
        b.set_option_values({self.inst: valor(self.inst, "Parede")})  # sem acabamento: outra combinação
        self.assertEqual(ProductVariant.objects.filter(product=self.p).count(), 2)
        c = ProductVariant(product=self.p, sku="A-001-V03", color=self.preto, material=self.pla, size="20 cm", sale_price=Decimal("10"), weight_grams=Decimal("10"))
        c._pending_option_choices = {self.inst.pk: valor(self.inst, "Parede").pk}
        with self.assertRaises(ValidationError):
            c.full_clean()  # igual à B

    def test_the_modal_race_ends_refused_and_without_leftovers(self):
        """Duas gravações da mesma combinação pelo modal: a segunda volta 400 e não deixa variante."""
        from django.contrib.auth import get_user_model

        chefe = get_user_model().objects.create_superuser("chefe", "c@x.test", "x")
        self.client.force_login(chefe)
        url = reverse("admin:catalog_product_variant_save", args=[self.p.pk])
        dados = {"sku": "", "color": self.preto.pk, "material": self.pla.pk, "size": "20 cm", "sale_price": "10.00", "weight_grams": "10",
                 "pricing_mode": "price", "is_active": "on", "sort_order": "0",
                 "dimension_unit": ProductVariant._meta.get_field("dimension_unit").get_default(),
                 f"opt_{self.inst.pk}": valor(self.inst, "Parede").pk}
        primeira = self.client.post(url, dados)
        self.assertEqual(primeira.status_code, 200, primeira.content)
        segunda = self.client.post(url, dados)
        self.assertEqual(segunda.status_code, 400)
        self.assertEqual(ProductVariant.objects.filter(product=self.p).count(), 1)
        self.assertEqual(ProductVariantOptionValue.objects.count(), 1)


class ZeroSharedStateTests(DuplicarOpcoesBase):
    def retrato_das_opcoes(self, produto):
        return {
            o.name: (o.sort_order, dict(o.translations.values_list("language", "name")),
                     [(v.name, v.sort_order, dict(v.translations.values_list("language", "name"))) for v in o.values.order_by("sort_order", "id")])
            for o in Product.objects.get(pk=produto.pk).options.order_by("sort_order", "id")
        }

    def rotulos(self, produto):
        return sorted(v.label for v in Product.objects.get(pk=produto.pk).variants.all())

    def test_changing_the_original_after_duplicating_does_not_touch_the_copy(self):
        p = self.leao()
        copia = self.duplicar(p)
        copia_antes = (self.retrato_das_opcoes(copia), self.rotulos(copia))
        inst = p.options.get(name="Instalação")
        inst.name = "Montagem"
        inst.save()
        ProductOptionTranslation.objects.filter(master=inst).update(name="Montage")
        parede = valor(inst, "Parede")
        parede.name = "Muro"
        parede.save()
        ProductOptionValueTranslation.objects.filter(master=parede).update(name="Mauer")
        v02 = p.variants.get(sku="REL-LEAO-001-V02")
        v02.set_option_values({inst: None})  # tira a escolha (Parede+Fosco vira só Fosco: combinação nova)
        ProductOptionValue.objects.filter(option=inst, name="Mesa").update(sort_order=9)
        self.assertEqual((self.retrato_das_opcoes(copia), self.rotulos(copia)), copia_antes)

    def test_changing_the_copy_does_not_touch_the_original(self):
        p = self.leao()
        original_antes = (self.retrato_das_opcoes(p), self.rotulos(p))
        copia = self.duplicar(p)
        inst = copia.options.get(name="Instalação")
        inst.name = "Montagem"
        inst.save()
        ProductOptionTranslation.objects.filter(master=inst).update(name="Montage")
        muro = valor(inst, "Parede")
        muro.name = "Muro"
        muro.save()
        ProductOptionValueTranslation.objects.filter(master=muro).delete()
        copia.variants.get(sku="REL-LEAO-002-V02").set_option_values({inst: None})
        inst.values.get(name="Mesa").delete() if not ProductVariantOptionValue.objects.filter(value__name="Mesa", variant__product=copia).exists() else None
        self.assertEqual((self.retrato_das_opcoes(p), self.rotulos(p)), original_antes)

    def test_deleting_the_copy_leaves_the_original_whole(self):
        p = self.leao()
        antes = (self.retrato_das_opcoes(p), self.rotulos(p), ProductVariantOptionValue.objects.filter(variant__product=p).count())
        copia = self.duplicar(p)
        copia.delete()
        self.assertEqual((self.retrato_das_opcoes(p), self.rotulos(p), ProductVariantOptionValue.objects.filter(variant__product=p).count()), antes)
        self.assertEqual(ProductOption.objects.count(), 2)
        self.assertEqual(ProductOptionValue.objects.count(), 4)
