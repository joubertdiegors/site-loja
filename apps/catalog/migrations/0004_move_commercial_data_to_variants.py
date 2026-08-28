"""Etapa 8 (2/3) — os dados comerciais mudam de casa.

Roda entre a ``0003`` (a variante ganhou os campos) e a ``0005`` (o produto os
perde). É aqui que os produtos já cadastrados atravessam a mudança sem perder
nada.

Dois casos, e só dois:

**Produto sem variante** ganha uma, copiando tudo — preço, custo, estoque,
peso, dimensões, prazo, sob-encomenda. O SKU dela é o SKU do produto, porque
até agora era esse o código do que se vendia; nada de gerar código novo para
uma coisa que já tinha nome. Cor e material saem dos M2M do produto quando ele
tinha exatamente um de cada — mais de um não define uma variante, define
várias, e adivinhar qual seria pior do que deixar em branco.

**Produto com variantes** tem cada campo vazio preenchido a partir do produto.
Vazio significava "herda do produto" até esta migration; a herança some, então
o valor herdado é gravado. Quem já tinha valor próprio não é tocado.

Reversível: a volta recopia da variante padrão para o produto.
"""

from django.db import migrations


def sku_da_variante(produto_sku, existentes, sufixo=""):
    """SKU determinístico e único para a variante criada.

    Sem sufixo é o próprio SKU do produto — é o mesmo objeto comercial, só
    mudou de tabela. O sufixo entra apenas se aquele código já estiver em uso.
    """
    base = f"{produto_sku}{sufixo}"[:64]
    if base not in existentes:
        return base
    contador = 2
    while True:
        candidato = f"{produto_sku}-{contador}"[:64]
        if candidato not in existentes:
            return candidato
        contador += 1


def mover_para_variantes(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductVariant = apps.get_model("catalog", "ProductVariant")

    existentes = set(ProductVariant.objects.values_list("sku", flat=True))

    #: (campo no produto, campo na variante). Mesmo nome dos dois lados; a
    #: lista existe para o código ser explícito sobre o que atravessa.
    COMERCIAIS = (
        "pricing_mode",
        "sale_price",
        "profit_margin",
        "filament_cost",
        "energy_cost",
        "total_cost",
        "stock_quantity",
        "allow_backorder",
        "made_to_order",
        "production_lead_time_days",
        "weight_grams",
        "width",
        "height",
        "depth",
        "dimension_unit",
        "print_time",
    )

    #: Campos cujo "vazio" significava herdar do produto. Os demais (custo,
    #: modo de preço, sob encomenda) nunca existiram na variante: são copiados
    #: sempre, porque a variante ainda não tem nada.
    HERDAVEIS = ("sale_price", "weight_grams", "width", "height", "depth")

    criadas = 0
    completadas = 0

    for produto in Product.objects.all().prefetch_related("variants", "colors", "materials"):
        variantes = list(produto.variants.all())

        if not variantes:
            # Produto que era vendido "direto": vira produto + uma variante.
            cores = list(produto.colors.all())
            materiais = list(produto.materials.all())
            variante = ProductVariant(
                product=produto,
                sku=sku_da_variante(produto.sku, existentes),
                color=cores[0] if len(cores) == 1 else None,
                material=materiais[0] if len(materiais) == 1 else None,
                size="",
                is_active=True,
                sort_order=0,
            )
            for campo in COMERCIAIS:
                setattr(variante, campo, getattr(produto, campo))
            variante.save()
            existentes.add(variante.sku)
            criadas += 1
            continue

        # Produto que já tinha variantes: o que estava vazio herdava do
        # produto. A herança acaba aqui, então o valor é gravado.
        for variante in variantes:
            mudou = []
            for campo in COMERCIAIS:
                if campo in HERDAVEIS:
                    if getattr(variante, campo) is None:
                        setattr(variante, campo, getattr(produto, campo))
                        mudou.append(campo)
                else:
                    setattr(variante, campo, getattr(produto, campo))
                    mudou.append(campo)
            if mudou:
                variante.save(update_fields=mudou)
                completadas += 1

    print(f"\n    catalog 0004: {criadas} variante(s) criada(s), "
          f"{completadas} completada(s) a partir do produto.")


def devolver_ao_produto(apps, schema_editor):
    """Volta: o produto recupera os valores da primeira variante ativa.

    Não apaga variantes — a volta é para permitir inspeção, não para desfazer
    o cadastro que alguém tenha feito depois.
    """
    Product = apps.get_model("catalog", "Product")

    for produto in Product.objects.all().prefetch_related("variants"):
        variante = next(
            (v for v in produto.variants.all() if v.is_active),
            next(iter(produto.variants.all()), None),
        )
        if variante is None:
            continue
        for campo in (
            "pricing_mode", "sale_price", "profit_margin", "filament_cost", "energy_cost",
            "total_cost", "stock_quantity", "allow_backorder", "made_to_order",
            "production_lead_time_days", "weight_grams", "width", "height", "depth",
            "dimension_unit", "print_time",
        ):
            setattr(produto, campo, getattr(variante, campo))
        produto.save()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_variant_commercial_fields")]

    operations = [migrations.RunPython(mover_para_variantes, devolver_ao_produto)]
