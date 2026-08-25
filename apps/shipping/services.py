"""Cálculo do frete: peso do pedido e opções disponíveis.

Nada aqui sabe o que é um produto 3D. O peso vem do catálogo (variante quando
existe, produto quando não) e o preço vem da tabela cadastrada. É o mesmo
cálculo para uma peça impressa, um rolo de filamento ou uma impressora.
"""

from dataclasses import dataclass
from decimal import Decimal

from apps.shipping.models import ShippingMethod, ShippingRate


def line_weight_grams(product, variant=None) -> int:
    """Peso unitário da linha, em gramas.

    A variante manda quando tem peso próprio (um vaso de 25 cm não pesa o
    mesmo que o de 10 cm). Produto sem peso cadastrado conta zero — e é isso
    que o administrador vê quando esquece de preencher, em vez de um frete
    inventado.
    """
    if variant is not None:
        weight = variant.effective_weight
    else:
        weight = product.weight_grams
    if not weight:
        return 0
    return int(Decimal(weight).quantize(Decimal("1")))


def cart_weight_grams(lines) -> int:
    """Peso total das linhas do carrinho (ou dos itens de um pedido)."""
    return sum(line_weight_grams(line.product, line.variant) * line.quantity for line in lines)


def production_days(lines) -> int:
    """Prazo de produção do pedido: o **maior** entre os itens, não a soma.

    A oficina imprime em paralelo. Somar daria um prazo que nunca acontece na
    prática e que só serviria para assustar o cliente.
    """
    return max(
        (line.product.production_lead_time_days or 0 for line in lines),
        default=0,
    )


@dataclass(frozen=True)
class ShippingOption:
    """Uma escolha de entrega já precificada para este pedido."""

    method: ShippingMethod
    rate: ShippingRate
    price: Decimal
    production_days: int = 0

    @property
    def id(self) -> int:
        return self.method.pk

    @property
    def label(self) -> str:
        return self.method.label

    @property
    def carrier_name(self) -> str:
        return self.method.carrier.name

    @property
    def transit_min_days(self) -> int:
        return self.method.min_days

    @property
    def transit_max_days(self) -> int:
        return self.method.max_days

    # -- prazo total: produção + transporte --------------------------------

    @property
    def min_days(self) -> int:
        return self.production_days + self.method.min_days

    @property
    def max_days(self) -> int:
        return self.production_days + self.method.max_days

    @property
    def days_display(self) -> str:
        if self.min_days == self.max_days:
            return str(self.min_days)
        return f"{self.min_days}–{self.max_days}"


def quote(country, weight_grams: int, production_days_value: int = 0) -> list[ShippingOption]:
    """Opções de entrega para este país e este peso, da mais barata para a mais cara.

    Sem país (o cliente ainda não escolheu endereço) ou sem país ativo, a lista
    é vazia: é melhor o checkout dizer "escolha o endereço" do que mostrar um
    preço que talvez não valha para o destino.
    """
    if country is None or not country.is_active:
        return []

    rates = (
        ShippingRate.objects.active()
        .for_country(country)
        .for_weight(max(0, int(weight_grams)))
        .select_related("method", "method__carrier")
    )

    # Um método pode ter mais de uma faixa cadastrada cobrindo o peso apenas se
    # alguém burlou a validação; ficamos com a mais barata, nunca com duas
    # linhas do mesmo método no checkout.
    best: dict[int, ShippingOption] = {}
    for rate in rates:
        option = ShippingOption(
            method=rate.method,
            rate=rate,
            price=rate.price,
            production_days=production_days_value,
        )
        current = best.get(rate.method_id)
        if current is None or option.price < current.price:
            best[rate.method_id] = option

    return sorted(
        best.values(),
        key=lambda option: (option.price, option.method.sort_order, option.method.pk),
    )


def quote_for_method(country, weight_grams: int, method, production_days_value: int = 0):
    """A opção de um método específico — ``None`` se ele não serve este pedido.

    É o que o checkout usa para revalidar a escolha antes de criar o pedido:
    o método veio de um ``<input>``, e input de cliente não decide preço.
    """
    wanted = getattr(method, "pk", method)
    for option in quote(country, weight_grams, production_days_value):
        if option.method.pk == wanted:
            return option
    return None
