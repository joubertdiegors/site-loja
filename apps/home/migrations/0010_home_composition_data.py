"""A composição da Home — a etapa de DADOS, separada do schema (0009).

Os blocos que tinham posição fixa no template (categorias em destaque, como
trabalhamos, chamada final, sobre a loja) viram seções de `HomeSection`, na
ordem em que sempre apareceram. Esta migration **preserva tudo**: nenhum
card, bloco, chamada, passo, pílula ou tradução é apagado ou recriado. Ela só
cria as linhas de seção que representam a página de hoje e liga o conteúdo
existente a elas:

    [categorias em destaque]  <- os HomeCategoryCard existentes
    ...as seções de produtos, na ordem em que estavam...
    [como trabalhamos]        <- os HomeCard existentes (sem nenhum: os padrão)
    [chamada final]           <- o HomeCallout pk=1 (sem linha: o texto padrão)
    [sobre a loja]            <- o HomeAbout pk=1 (só se existir)

e renumera a ordem de dez em dez, para sobrar espaço entre elas. Num banco
sem nenhum conteúdo de Home (uma instalação nova) nada é criado: a Home mostra
a composição padrão até o primeiro cadastro.

Por que uma migration própria: no PostgreSQL, as chaves estrangeiras criadas
pela 0009 são constraints DEFERRABLE INITIALLY DEFERRED. Gravar linhas que as
usam na MESMA transação em que o schema foi alterado deixa eventos de trigger
pendentes, e o schema editor falha ao fechar a etapa ("cannot CREATE INDEX
... because it has pending trigger events"). Schema e dados em migrations
separadas é a arquitetura certa: cada uma roda na sua transação.
"""

from django.db import migrations

BLOCOS = ("category_cards", "how_we_work", "callout", "about")


def compor(apps, schema_editor):
    HomeSection = apps.get_model("home", "HomeSection")
    HomeCard = apps.get_model("home", "HomeCard")
    HomeCategoryCard = apps.get_model("home", "HomeCategoryCard")
    HomeCallout = apps.get_model("home", "HomeCallout")
    HomeAbout = apps.get_model("home", "HomeAbout")

    em_uso = any(
        Model.objects.exists()
        for Model in (HomeSection, HomeCard, HomeCategoryCard, HomeCallout, HomeAbout)
    )
    if not em_uso:
        return

    # Já composto? (um banco em que a versão antiga da 0009, com os dados
    # dentro, chegou a ser aplicada — como o de desenvolvimento). Antes da
    # composição nenhum destes tipos existia, então num banco legítimo de
    # "antes" isto nunca é verdadeiro; e rodar duas vezes duplicaria as seções.
    if HomeSection.objects.filter(section_type__in=BLOCOS).exists():
        return

    produtos = list(HomeSection.objects.order_by("sort_order", "id"))
    ordem = []

    if HomeCategoryCard.objects.exists():
        categorias = HomeSection.objects.create(
            internal_name="Categorias em destaque", section_type="category_cards", is_active=True
        )
        HomeCategoryCard.objects.update(section=categorias)
        ordem.append(categorias)

    ordem.extend(produtos)

    como = HomeSection.objects.create(
        internal_name="Como trabalhamos", section_type="how_we_work", is_active=True
    )
    HomeCard.objects.update(section=como)
    ordem.append(como)

    chamada = HomeCallout.objects.order_by("id").first()
    if chamada is not None and not chamada.internal_name:
        chamada.internal_name = "Chamada final"
        chamada.save(update_fields=["internal_name"])
    ordem.append(
        HomeSection.objects.create(
            internal_name="Chamada final", section_type="callout", callout=chamada, is_active=True
        )
    )

    sobre = HomeAbout.objects.order_by("id").first()
    if sobre is not None:
        if not sobre.internal_name:
            sobre.internal_name = "Sobre a loja"
            sobre.save(update_fields=["internal_name"])
        ordem.append(
            HomeSection.objects.create(
                internal_name="Sobre a loja", section_type="about", about=sobre, is_active=True
            )
        )

    for posicao, secao in enumerate(ordem):
        secao.sort_order = posicao * 10
        secao.save(update_fields=["sort_order"])


def descompor(apps, schema_editor):
    """Solta o conteúdo das seções antes de apagá-las — nada em cascata.

    Primeiro os cards e os blocos deixam de apontar para as seções (as FKs são
    CASCADE: apagar a seção antes levaria o conteúdo junto); só então as
    seções de bloco criadas pela composição são apagadas. As seções de
    produtos, a chamada, o "Sobre a loja" e todas as traduções ficam.
    """
    HomeSection = apps.get_model("home", "HomeSection")
    apps.get_model("home", "HomeCard").objects.update(section=None)
    apps.get_model("home", "HomeCategoryCard").objects.update(section=None)
    HomeSection.objects.filter(section_type__in=BLOCOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0009_home_composition"),
    ]

    operations = [
        migrations.RunPython(compor, descompor),
    ]
