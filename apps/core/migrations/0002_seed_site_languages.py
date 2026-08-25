"""Cria as linhas de idioma da loja.

Os nove idiomas suportados entram na tabela; ficam **disponíveis** apenas os
quatro que a loja oferece hoje. Os outros continuam cadastrados e desligados —
ativar é um clique no admin, não uma alteração de código.
"""

from django.db import migrations

# (código, ordem, disponível agora)
LANGUAGES = [
    ("pt-br", 1, True),
    ("fr", 2, True),
    ("nl", 3, True),
    ("en", 4, True),
    ("de", 5, False),
    ("es", 6, False),
    ("it", 7, False),
    ("tr", 8, False),
    ("ar", 9, False),
]


def create_languages(apps, schema_editor):
    SiteLanguage = apps.get_model("core", "SiteLanguage")
    for code, order, is_active in LANGUAGES:
        SiteLanguage.objects.get_or_create(
            code=code, defaults={"sort_order": order, "is_active": is_active}
        )


def remove_languages(apps, schema_editor):
    SiteLanguage = apps.get_model("core", "SiteLanguage")
    SiteLanguage.objects.filter(code__in=[code for code, _order, _active in LANGUAGES]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(create_languages, remove_languages)]
