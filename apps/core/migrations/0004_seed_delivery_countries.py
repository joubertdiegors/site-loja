"""Cria a lista inicial de países de entrega.

Os quatro países vizinhos com que a JD PRINT trabalha desde o começo entram
ativos; os outros ficam cadastrados e **desligados** — ligar é um clique no
admin, não uma alteração de código. É o mesmo critério já usado para os
idiomas da loja (ver ``0002_seed_site_languages``).

As alíquotas são as taxas normais de TVA/IVA vigentes em cada país. Elas ficam
no banco justamente para que uma mudança de alíquota seja uma edição no admin.
"""

from decimal import Decimal

from django.db import migrations

# (ISO, ordem, ativo, TVA, {idioma: nome})
#
# Os códigos de idioma aqui são os de CONTEÚDO (``core.constants.Language``:
# pt, fr, nl, en) — não os da interface (pt-br). É a mesma tabela de tradução
# usada por produtos e categorias.
COUNTRIES = [
    ("BE", 1, True, "21.00", {
        "pt": "Bélgica", "fr": "Belgique", "nl": "België", "en": "Belgium",
    }),
    ("FR", 2, True, "20.00", {
        "pt": "França", "fr": "France", "nl": "Frankrijk", "en": "France",
    }),
    ("NL", 3, True, "21.00", {
        "pt": "Países Baixos", "fr": "Pays-Bas", "nl": "Nederland", "en": "Netherlands",
    }),
    ("LU", 4, True, "17.00", {
        "pt": "Luxemburgo", "fr": "Luxembourg", "nl": "Luxemburg", "en": "Luxembourg",
    }),
    ("DE", 5, False, "19.00", {
        "pt": "Alemanha", "fr": "Allemagne", "nl": "Duitsland", "en": "Germany",
    }),
    ("ES", 6, False, "21.00", {
        "pt": "Espanha", "fr": "Espagne", "nl": "Spanje", "en": "Spain",
    }),
    ("IT", 7, False, "22.00", {
        "pt": "Itália", "fr": "Italie", "nl": "Italië", "en": "Italy",
    }),
    ("PT", 8, False, "23.00", {
        "pt": "Portugal", "fr": "Portugal", "nl": "Portugal", "en": "Portugal",
    }),
]


def create_countries(apps, schema_editor):
    DeliveryCountry = apps.get_model("core", "DeliveryCountry")
    DeliveryCountryTranslation = apps.get_model("core", "DeliveryCountryTranslation")

    for iso, order, is_active, vat, names in COUNTRIES:
        country, _created = DeliveryCountry.objects.get_or_create(
            iso_code=iso,
            defaults={"sort_order": order, "is_active": is_active, "vat_rate": Decimal(vat)},
        )
        for language, name in names.items():
            DeliveryCountryTranslation.objects.get_or_create(
                master=country, language=language, defaults={"name": name}
            )


def remove_countries(apps, schema_editor):
    DeliveryCountry = apps.get_model("core", "DeliveryCountry")
    DeliveryCountry.objects.filter(iso_code__in=[iso for iso, *_rest in COUNTRIES]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0003_delivery_country")]

    operations = [migrations.RunPython(create_countries, remove_countries)]
