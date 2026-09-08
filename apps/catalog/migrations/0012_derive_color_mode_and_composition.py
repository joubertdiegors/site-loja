"""Etapa 2B (dados) — o modo de cores e a composição derivados das variantes.

Roda DEPOIS da ``0011`` (só schema) e não contém operação de schema nenhuma:
no PostgreSQL, gravar pelas chaves estrangeiras recém-criadas na mesma
transação em que elas nasceram deixa eventos de trigger pendentes e o schema
editor falha ("cannot CREATE INDEX ... because it has pending trigger
events") — foi o que aconteceu na ``home.0009`` original.

## As regras de derivação (ver a análise da etapa 2A)

Só o que as variantes dizem com clareza vira configuração. Em dúvida, nada é
inventado: a composição fica vazia e o modo fica em «opção comercial», que é
exatamente o comportamento que a loja já tinha.

Cores (todas as variantes do produto, ativas ou não):

* nenhuma variante com cor            -> ``none``   (não se aplica)
* todas com a MESMA cor               -> ``single`` + essa cor em ``ProductColor``
* cores diferentes, ou só algumas com -> ``variant`` (a cor continua sendo o eixo
  cor                                    da variante; nada vai para ``ProductColor``)

Materiais:

* todas as variantes com o MESMO      -> composição com esse material, sem
  material                               percentual (não se inventa 100%)
* materiais diferentes                -> composição vazia (é eixo comercial)
* só algumas variantes com material,  -> composição vazia (não dá para saber)
  ou nenhuma

Nenhuma variante é criada, apagada ou alterada; nenhum SKU, preço, estoque,
peso, foto ou tradução muda. A migration só toca produtos que ainda estão no
padrão (``none`` e sem linhas nas duas tabelas), então rodá-la duas vezes não
duplica nada.

Reverso: nada a desfazer — a ``0011`` revertida derruba a coluna e as duas
tabelas. Apagar aqui o que o Admin possa ter cadastrado depois seria pior.
"""

from django.db import migrations

NONE, SINGLE, VARIANT = "none", "single", "variant"


def derivar(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductColor = apps.get_model("catalog", "ProductColor")
    ProductMaterialComposition = apps.get_model("catalog", "ProductMaterialComposition")

    produtos = (
        Product.objects.filter(color_mode=NONE, product_colors__isnull=True, material_composition__isnull=True)
        .distinct()
        .prefetch_related("variants")
    )
    for produto in produtos:
        variantes = list(produto.variants.all())
        if not variantes:
            continue

        cores = {v.color_id for v in variantes if v.color_id}
        todas_com_cor = all(v.color_id for v in variantes)
        if cores and len(cores) == 1 and todas_com_cor:
            produto.color_mode = SINGLE
            ProductColor.objects.create(product=produto, color_id=cores.pop(), sort_order=0)
        elif cores:
            produto.color_mode = VARIANT
        else:
            produto.color_mode = NONE

        materiais = {v.material_id for v in variantes if v.material_id}
        todas_com_material = all(v.material_id for v in variantes)
        if materiais and len(materiais) == 1 and todas_com_material:
            ProductMaterialComposition.objects.create(
                product=produto, material_id=materiais.pop(), percentage=None, sort_order=0
            )

        if produto.color_mode != NONE:
            produto.save(update_fields=["color_mode"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0011_product_colors_and_composition"),
    ]

    operations = [
        migrations.RunPython(derivar, migrations.RunPython.noop),
    ]
