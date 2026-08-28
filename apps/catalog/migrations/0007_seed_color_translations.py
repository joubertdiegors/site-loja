"""Traduções das cores existentes, sem perder nada.

Toda cor já cadastrada ganha a tradução em português a partir do nome interno
que ela já tinha — é o mesmo texto que a loja mostrava antes, agora na tabela
certa. As cores do catálogo atual ganham também francês, neerlandês e inglês.

Cor que este dicionário não conhece fica só com o português: o fallback da casa
mostra o nome em português nos outros idiomas, que é exatamente o que acontecia
antes desta migration. Nada regride; o que falta é visível e traduzível pelo
Admin.
"""

from django.db import migrations

#: nome interno (pt) -> (fr, nl, en)
CORES = {
    "Preto": ("Noir", "Zwart", "Black"),
    "Branco": ("Blanc", "Wit", "White"),
    "Roxo": ("Violet", "Paars", "Purple"),
    "Verde": ("Vert", "Groen", "Green"),
    "Rosa": ("Rose", "Roze", "Pink"),
    "Âmbar": ("Ambre", "Amber", "Amber"),
    "Amarelo": ("Jaune", "Geel", "Yellow"),
    "Azul": ("Bleu", "Blauw", "Blue"),
    "Vermelho": ("Rouge", "Rood", "Red"),
    "Cinza": ("Gris", "Grijs", "Grey"),
    "Laranja": ("Orange", "Oranje", "Orange"),
    "Transparente": ("Transparent", "Transparant", "Clear"),
    "Dourado": ("Doré", "Goud", "Gold"),
    "Prateado": ("Argenté", "Zilver", "Silver"),
}

IDIOMAS = ("fr", "nl", "en")


def traduzir(apps, schema_editor):
    Color = apps.get_model("catalog", "Color")
    ColorTranslation = apps.get_model("catalog", "ColorTranslation")

    criadas = 0
    for color in Color.objects.all():
        # O português vem do nome interno: é o texto que a loja já mostrava.
        _, novo = ColorTranslation.objects.get_or_create(
            master=color, language="pt", defaults={"name": color.name}
        )
        criadas += int(novo)

        traducoes = CORES.get(color.name.strip())
        if not traducoes:
            continue
        for idioma, nome in zip(IDIOMAS, traducoes):
            _, novo = ColorTranslation.objects.get_or_create(
                master=color, language=idioma, defaults={"name": nome}
            )
            criadas += int(novo)

    if criadas:
        print(f"\n    catalog 0007: {criadas} tradução(ões) de cor criada(s).")


def desfazer(apps, schema_editor):
    """Devolve o nome interno a partir do português e apaga as traduções.

    O nome interno nunca foi apagado, então isto é só limpeza — mas mantém a
    migration reversível de verdade em vez de fingir que é.
    """
    ColorTranslation = apps.get_model("catalog", "ColorTranslation")
    ColorTranslation.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_color_translation"),
    ]

    operations = [
        migrations.RunPython(traduzir, desfazer),
    ]
