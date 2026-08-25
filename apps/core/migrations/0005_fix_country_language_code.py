"""Corrige o código de idioma das traduções de país.

A carga inicial gravou ``pt-br`` — que é o código da **interface**. As tabelas
de tradução usam os códigos de **conteúdo** (``core.constants.Language``:
``pt``, ``fr``, ``nl``, ``en``), os mesmos de produtos e categorias. Com o
código errado, a leitura caía no fallback e um cliente brasileiro via
"Belgium".

Em banco novo esta migration não encontra nada para corrigir.
"""

from django.db import migrations


def to_content_code(apps, schema_editor):
    Translation = apps.get_model("core", "DeliveryCountryTranslation")
    Translation.objects.filter(language="pt-br").update(language="pt")


def back_to_interface_code(apps, schema_editor):
    Translation = apps.get_model("core", "DeliveryCountryTranslation")
    Translation.objects.filter(language="pt").update(language="pt-br")


class Migration(migrations.Migration):
    dependencies = [("core", "0004_seed_delivery_countries")]

    operations = [migrations.RunPython(to_content_code, back_to_interface_code)]
