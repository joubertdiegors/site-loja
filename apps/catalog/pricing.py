"""Regras de custo, preço e margem.

Funções puras, sem dependência de modelo, para que possam ser testadas
isoladamente e reaproveitadas por variantes, kits ou promoções no futuro.

Definições usadas (margem sobre o PREÇO DE VENDA, não markup)::

    custo_total = soma dos componentes de custo
    margem (%)  = ((preço - custo_total) / preço) * 100
    preço       = custo_total / (1 - margem/100)

Todo cálculo usa ``Decimal``. Nenhum ``float`` participa de valores monetários.
"""

from collections.abc import Mapping
from decimal import Decimal, ROUND_HALF_UP

MONEY_EXPONENT = Decimal("0.01")
PERCENT_EXPONENT = Decimal("0.01")

#: Limites da margem DIGITADA pelo administrador (modo "informar margem").
#: 100% seria uma divisão por zero.
MAX_MARGIN = Decimal("99.99")
MIN_MARGIN = Decimal("0")

#: Limites da margem GRAVADA. No modo "informar preço" a margem é derivada e
#: pode chegar a 100% (produto sem custo cadastrado) ou ficar negativa (preço
#: abaixo do custo, ex.: liquidação). Os extremos apenas respeitam a
#: capacidade do campo DecimalField(max_digits=5, decimal_places=2).
MAX_STORED_MARGIN = Decimal("100.00")
MIN_STORED_MARGIN = Decimal("-999.99")


def clamp_stored_margin(margin: Decimal | None) -> Decimal | None:
    """Mantém a margem derivada dentro do que o campo consegue armazenar."""
    if margin is None:
        return None
    return max(MIN_STORED_MARGIN, min(MAX_STORED_MARGIN, margin))


def money(value) -> Decimal:
    """Arredonda para 2 casas usando arredondamento comercial."""
    return Decimal(value).quantize(MONEY_EXPONENT, rounding=ROUND_HALF_UP)


def percent(value) -> Decimal:
    return Decimal(value).quantize(PERCENT_EXPONENT, rounding=ROUND_HALF_UP)


def total_cost(components: Mapping[str, Decimal]) -> Decimal:
    """Soma os componentes de custo.

    Receber um dicionário (e não parâmetros fixos) é o que permite acrescentar
    mão de obra, embalagem, manutenção, desperdício ou taxas depois sem tocar
    nesta função nem na lógica de preço.
    """
    return money(sum((Decimal(value) for value in components.values()), Decimal("0")))


def margin_from_price(cost: Decimal, price: Decimal) -> Decimal | None:
    """Margem obtida ao vender por ``price`` um item que custa ``cost``.

    Retorna ``None`` quando o preço não é positivo (margem indefinida).
    """
    price = Decimal(price)
    if price <= 0:
        return None
    return percent((price - Decimal(cost)) / price * Decimal("100"))


def price_from_margin(cost: Decimal, margin: Decimal) -> Decimal:
    """Preço necessário para obter ``margin``% sobre um custo de ``cost``."""
    margin = Decimal(margin)
    if margin < MIN_MARGIN or margin > MAX_MARGIN:
        raise ValueError(f"Margem deve estar entre {MIN_MARGIN} e {MAX_MARGIN}.")
    return money(Decimal(cost) / (Decimal("1") - margin / Decimal("100")))


def profit(cost: Decimal, price: Decimal) -> Decimal:
    return money(Decimal(price) - Decimal(cost))
