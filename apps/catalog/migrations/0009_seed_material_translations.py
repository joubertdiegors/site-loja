"""Traduções dos materiais existentes, sem perder nada.

Mesma estratégia da 0007 (cores): todo material já cadastrado ganha a tradução
em português a partir do nome interno que ele já tinha — é o mesmo texto que a
loja mostrava antes, agora na tabela certa. Os materiais conhecidos ganham
também francês, neerlandês e inglês.

"PLA", "PETG", "ABS" e afins são nomes próprios: repetem-se iguais nos quatro
idiomas, e é isso que as entradas abaixo dizem. O que muda de verdade é
"Resina" e "Madeira".

Material que este dicionário não conhece fica só com o português, e o fallback
da casa mostra o português nos outros idiomas — exatamente o que acontecia
antes desta migration.
"""

from django.db import migrations

#: nome interno (pt) -> (fr, nl, en)
MATERIAIS = {
    "Resina": ("Résine", "Hars", "Resin"),
    "Madeira": ("Bois", "Hout", "Wood"),
    "PLA": ("PLA", "PLA", "PLA"),
    "PETG": ("PETG", "PETG", "PETG"),
    "ABS": ("ABS", "ABS", "ABS"),
    "ASA": ("ASA", "ASA", "ASA"),
    "TPU": ("TPU", "TPU", "TPU"),
    "Nylon": ("Nylon", "Nylon", "Nylon"),
    "Fibra de carbono": ("Fibre de carbone", "Koolstofvezel", "Carbon fibre"),
    "Metal": ("Métal", "Metaal", "Metal"),
    "Cerâmica": ("Céramique", "Keramiek", "Ceramic"),
    "Bronze": ("Bronze", "Brons", "Bronze"),
}

IDIOMAS = ("fr", "nl", "en")


def traduzir(apps, schema_editor):
    Material = apps.get_model("catalog", "Material")
    MaterialTranslation = apps.get_model("catalog", "MaterialTranslation")

    criadas = 0
    for material in Material.objects.all():
        # O português vem do nome interno: é o texto que a loja já mostrava.
        _, novo = MaterialTranslation.objects.get_or_create(
            master=material, language="pt", defaults={"name": material.name}
        )
        criadas += int(novo)

        traducoes = MATERIAIS.get(material.name.strip())
        if not traducoes:
            continue
        for idioma, nome in zip(IDIOMAS, traducoes):
            _, novo = MaterialTranslation.objects.get_or_create(
                master=material, language=idioma, defaults={"name": nome}
            )
            criadas += int(novo)

    if criadas:
        print(f"\n    catalog 0009: {criadas} tradução(ões) de material criada(s).")


def desfazer(apps, schema_editor):
    """O nome interno nunca foi apagado; isto é só limpeza."""
    MaterialTranslation = apps.get_model("catalog", "MaterialTranslation")
    MaterialTranslation.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0008_material_translation"),
    ]

    operations = [
        migrations.RunPython(traduzir, desfazer),
    ]
