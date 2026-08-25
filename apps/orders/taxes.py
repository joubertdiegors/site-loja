"""Cálculo da TVA/IVA.

**Os preços do catálogo já incluem a TVA.** No varejo B2C da União Europeia o
preço anunciado tem que ser o preço final (Diretiva 98/6/CE); somar imposto na
última tela do checkout, além de irregular, é a origem clássica de carrinho
abandonado. Então a conta aqui não é "somar imposto", é **separar** o imposto
que já está dentro do valor:

    imposto = bruto × taxa / (100 + taxa)

Com 21%, um total de €121,00 contém €21,00 de TVA. O cliente paga €121,00 nos
dois casos; a diferença é que o valor exibido nunca muda.

**Qual alíquota.** A do país de **destino** — é o regime de venda a distância
B2C dentro da UE (OSS). A taxa sai de ``core.DeliveryCountry.vat_rate``, nunca
de um número escrito no código, e é **copiada para o pedido**: mudança de
alíquota amanhã não pode reescrever a fatura de hoje.

**O frete é tributado junto.** Numa venda com entrega, o transporte é acessório
ao bem e segue a alíquota dele. É o tratamento normal para uma loja com um
único tipo de produto por pedido; regra por linha entra quando existirem
alíquotas diferentes no mesmo pedido.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value) -> Decimal:
    """Arredonda para centavos, sempre meio-para-cima (a regra contábil)."""
    return Decimal(value or 0).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxBreakdown:
    country_code: str
    rate: Decimal
    taxable: Decimal
    amount: Decimal
    prices_include_tax: bool = True

    @property
    def net(self) -> Decimal:
        """Base sem imposto — é o que vai para a contabilidade."""
        return money(self.taxable - self.amount)


def rate_for(country) -> Decimal:
    """Alíquota do país, ou zero quando não há país (cálculo preliminar)."""
    if country is None:
        return ZERO
    return Decimal(country.vat_rate or 0)


def included_tax(gross: Decimal, rate: Decimal) -> Decimal:
    """A parcela de imposto contida em um valor que já o inclui."""
    gross = money(gross)
    rate = Decimal(rate or 0)
    if gross <= ZERO or rate <= ZERO:
        return ZERO
    return money(gross * rate / (Decimal(100) + rate))


def breakdown(*, country, subtotal, shipping=ZERO, discount=ZERO) -> TaxBreakdown:
    """Quanto de TVA há no total deste pedido."""
    taxable = money(money(subtotal) - money(discount) + money(shipping))
    if taxable < ZERO:
        taxable = ZERO

    rate = rate_for(country)
    return TaxBreakdown(
        country_code=getattr(country, "iso_code", "") or "",
        rate=rate,
        taxable=taxable,
        amount=included_tax(taxable, rate),
        prices_include_tax=True,
    )


def store_country():
    """País da sede — usado como fallback quando não há endereço escolhido."""
    from apps.core.models import DeliveryCountry

    code = getattr(settings, "STORE_COUNTRY", "BE")
    return DeliveryCountry.objects.filter(iso_code=code).first()
