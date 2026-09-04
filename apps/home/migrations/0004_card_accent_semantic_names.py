# -*- coding: utf-8 -*-
"""O acento do card da Home passa a se chamar pelo que é.

`HomeCard.accent` guardava "cyan" e "magenta" — os nomes que as duas cores
tinham antes do redesenho. Depois dele, a paleta da marca é roxo, **menta**,
coral e amarelo, e aqueles dois valores só continuavam funcionando porque o
tema mantinha um apelido apontando para o acento certo: `--color-cyan-100`
literalmente era `var(--color-mint-100)`.

Ou seja: um card gravado como "cyan" JÁ SAÍA menta na tela, e um "magenta" JÁ
SAÍA coral. Esta migração não muda cor nenhuma — renomeia o rótulo para o que
ele já pintava, e com isso o último apelido de compatibilidade pode sair do
Design System.

A leitura é confirmada pelos três cards padrão da Home, que existem em código
para o caso de não haver cadastro: o card de "cores e materiais" (mesmo ícone
`palette`, mesma posição do "cyan" do seed) é `bg-mint-100`, e o de
"personalização" (ícone `sparkles`, o "magenta" do seed) é `bg-coral-100`.

Reversível: `reverse` desfaz exatamente o mesmo par. Nenhuma linha é criada ou
apagada, e nenhum outro valor é tocado — um `accent` fora do par (inclusive
"brand" ou algo escrito à mão fora das escolhas) passa intacto pelos dois
sentidos.
"""

from django.db import migrations, models

#: (valor antigo, valor novo). Só estes dois; o "brand" não muda de nome.
PARES = (("cyan", "mint"), ("magenta", "coral"))


def renomear(apps, schema_editor, de_para):
    HomeCard = apps.get_model("home", "HomeCard")
    for antigo, novo in de_para:
        HomeCard.objects.filter(accent=antigo).update(accent=novo)


def frente(apps, schema_editor):
    renomear(apps, schema_editor, PARES)


def tras(apps, schema_editor):
    renomear(apps, schema_editor, [(novo, antigo) for antigo, novo in PARES])


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0003_alter_homebanner_image_desktop"),
    ]

    operations = [
        # Os dados primeiro: enquanto as escolhas antigas ainda valem, um
        # `update` direto não esbarra em validação nenhuma.
        migrations.RunPython(frente, tras),
        migrations.AlterField(
            model_name="homecard",
            name="accent",
            field=models.CharField(
                "cor do ícone",
                max_length=10,
                choices=[("brand", "Roxo"), ("mint", "Menta"), ("coral", "Coral")],
                default="brand",
            ),
        ),
    ]
