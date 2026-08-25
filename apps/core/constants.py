"""Listas de valores compartilhadas por todo o projeto."""

from decimal import Decimal

from django.db import models


class Language(models.TextChoices):
    """Idiomas em que o CONTEÚDO (produtos, categorias) pode ser cadastrado.

    Não confundir com ``settings.LANGUAGES``, que define o idioma da interface.
    Os códigos seguem ISO 639-1 para que possam ser usados em URLs, cabeçalhos
    ``Accept-Language`` e ``hreflang`` no futuro.
    """

    PT = "pt", "Português"
    FR = "fr", "Francês"
    NL = "nl", "Holandês"
    EN = "en", "Inglês"
    DE = "de", "Alemão"
    ES = "es", "Espanhol"
    IT = "it", "Italiano"
    TR = "tr", "Turco"
    AR = "ar", "Árabe"


#: Idioma padrão do cadastro administrativo e fallback de exibição.
DEFAULT_LANGUAGE = Language.PT


class LengthUnit(models.TextChoices):
    """Unidade das dimensões físicas do produto."""

    MM = "mm", "Milímetro (mm)"
    CM = "cm", "Centímetro (cm)"
    M = "m", "Metro (m)"
    IN = "in", "Polegada (in)"


#: Fator de conversão de cada unidade para milímetros.
LENGTH_UNIT_TO_MM = {
    LengthUnit.MM: Decimal("1"),
    LengthUnit.CM: Decimal("10"),
    LengthUnit.M: Decimal("1000"),
    LengthUnit.IN: Decimal("25.4"),
}


class Currency(models.TextChoices):
    """Moedas aceitas. A operação começa em EUR (Bélgica)."""

    EUR = "EUR", "Euro (€)"
    USD = "USD", "Dólar americano ($)"
    GBP = "GBP", "Libra esterlina (£)"
    BRL = "BRL", "Real brasileiro (R$)"


DEFAULT_CURRENCY = Currency.EUR

#: Símbolo usado apenas para exibição no admin.
CURRENCY_SYMBOLS = {
    Currency.EUR: "€",
    Currency.USD: "$",
    Currency.GBP: "£",
    Currency.BRL: "R$",
}
