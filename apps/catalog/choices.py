"""Escolhas do cliente: a camada acima da variante.

A variante (``ProductVariant``) responde "qual unidade estou comprando": SKU,
estoque, disponibilidade e preço base. Uma **escolha do cliente** é o que ele
decide *depois* disso e que não muda a unidade produzida nem o estoque — só o
acabamento e, às vezes, o preço: «Cor: Dourado (+ € 2,00)».

Por isso ela não é um eixo (não resolve variante), não é personalização (não é
foto nem texto livre) e não cria uma variante para cada combinação:
«Porta-Retrato + Foto» vezes cinco cores continua sendo **uma** variante, com
um estoque, um SKU e um preço base.

## O que mora aqui

* ``ChoiceGroup`` — o que o produto oferece para escolher («Cor», com as suas
  opções). Hoje há um provedor só: a paleta (``ProductColor``), quando
  ``Product.color_mode`` é «Cores à escolha do cliente». Um ``ProductOption``
  de modo «escolha do cliente» — material, acabamento, LED — entra por
  ``choice_groups()`` como segundo provedor, sem mexer no carrinho, no pedido
  nem nos templates: eles só conhecem ``CustomerChoice``.
* ``resolve_choices()`` — do que o navegador mandou (só **ids**) para as
  escolhas válidas, ou ``ChoiceError``. É a única porta: nada do que vem do
  cliente vira preço sem passar por aqui, e o adicional sai sempre do banco.
* ``price_adjustment()`` / ``choices_text()`` — o que se soma ao preço da
  variante e o texto que o pedido congela, no idioma do cliente.

A identidade de uma escolha é ``chave do grupo = id do valor`` («color=12»):
estável e sem texto exibido — é o que entra na chave da linha do carrinho.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import formats
from django.utils.functional import lazy
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.catalog.models import OPTIONS_TEXT_SEPARATOR, ColorMode
from apps.catalog.pricing import money

#: A chave do grupo de cor. Não colide com os eixos da variante de propósito:
#: na página o eixo é ``data-variant-group="color"`` e o POST leva
#: ``option_color``; a escolha é ``data-choice-group="color"`` e o POST leva
#: ``choice_color``. Duas perguntas diferentes, dois nomes de campo.
COLOR_GROUP = "color"

ZERO = Decimal("0.00")


class ChoiceError(ValueError):
    """Uma escolha que não pode ser aceita — com a mensagem para o cliente.

    ``reason`` distingue **faltou** («missing»: o produto pede a escolha e ela
    não veio) de **inválida** («invalid»: veio um id que não é deste produto,
    ou que saiu da paleta). O carrinho trata as duas de jeitos diferentes.
    """

    def __init__(self, key: str, message: str, reason: str = "invalid"):
        super().__init__(message)
        self.key = key
        self.message = message
        self.reason = reason


@dataclass(frozen=True)
class CustomerChoice:
    """Uma opção escolhível — ou escolhida — de um grupo.

    Os textos são resolvidos **na leitura**, não na construção: o rótulo do
    grupo é uma string preguiçosa e o nome da cor sai de ``Color.display_name``
    na hora — como ``variant.label`` e ``product.display_name``. É o que faz o
    carrinho falar o idioma da página e o pedido congelar o texto no idioma
    ativo em ``create_order``, e não no de quem montou a linha.
    """

    key: str
    value_id: int
    price_delta: Decimal
    currency_symbol: str = "€"
    #: O rótulo do grupo — texto ou ``gettext_lazy``.
    label: object = ""
    #: A ``Color`` escolhida, quando a escolha é uma cor (nome, hex, traduções).
    color: object = field(default=None, compare=False, repr=False)
    #: O rótulo fixo do valor, quando a escolha não é uma cor.
    value_name: str = ""

    @property
    def signature(self) -> str:
        return f"{self.key}={self.value_id}"

    @property
    def label_text(self) -> str:
        return str(self.label)

    @property
    def value_label(self) -> str:
        if self.color is not None:
            return self.color.display_name
        return self.value_name

    @property
    def hex_code(self) -> str:
        if self.color is not None:
            return self.color.hex_code or ""
        return ""

    @property
    def swatch_style(self) -> str:
        """O fundo da bolinha — o degradê, quando a cor escolhida é composta."""
        if self.color is not None:
            return self.color.swatch_style
        return ""

    @property
    def delta_display(self) -> str:
        """«+ € 2,00», «− € 1,50», ou vazio quando não altera o preço."""
        return money_delta(self.price_delta, self.currency_symbol)

    @property
    def text(self) -> str:
        """«Cor: Dourado (+ € 2,00)» — o carrinho mostra, o pedido congela."""
        if self.delta_display:
            return f"{self.label_text}: {self.value_label} ({self.delta_display})"
        return f"{self.label_text}: {self.value_label}"

    @property
    def value_text(self) -> str:
        """«Dourado (+ € 2,00)» — o rótulo de um botão."""
        if self.delta_display:
            return f"{self.value_label} ({self.delta_display})"
        return self.value_label


@dataclass(frozen=True)
class ChoiceGroup:
    """O que o produto oferece para escolher num grupo."""

    key: str
    label: object
    options: tuple
    required: bool = True
    missing_message: object = ""
    invalid_message: object = ""


def money_delta(delta: Decimal, currency_symbol: str) -> str:
    """«+ € 2,00», «− € 1,50», ou vazio quando não altera o preço."""
    delta = Decimal(delta or 0)
    if delta == 0:
        return ""
    sinal = "+" if delta > 0 else "−"
    numero = formats.number_format(abs(delta), 2, use_l10n=True)
    return f"{sinal} {currency_symbol} {numero}"


# ---------------------------------------------------------------------------
# Os provedores: de onde vêm os grupos
# ---------------------------------------------------------------------------


def _color_group(product) -> ChoiceGroup | None:
    """A paleta como escolha — só em «Cores à escolha do cliente».

    Lê ``product.customer_color_rows`` (vazio fora do modo «custom» e com
    paleta vazia), do prefetch de sempre. Com paleta vazia não há grupo: não
    se inventa cor, e a compra segue exatamente como antes desta etapa.
    """
    rows = product.customer_color_rows
    if not rows:
        return None
    simbolo = product.currency_symbol
    # O rótulo é lido na hora — «Cor do pompom» no idioma da página, ou
    # «Cor» —, como o nome da cor: o carrinho fala o idioma da página e o
    # pedido congela o texto no idioma em que o cliente comprou.
    rotulo = lazy(lambda: product.color_choice_label, str)()
    opcoes = tuple(
        CustomerChoice(
            key=COLOR_GROUP,
            value_id=row.pk,
            # `money` quantiza: o SQLite devolve Decimal("0") para 0,00, e o
            # payload da página e a assinatura do pedido precisam de "0.00".
            price_delta=money(row.price_delta or 0),
            currency_symbol=simbolo,
            label=rotulo,
            color=row.color,
        )
        for row in rows
    )
    return ChoiceGroup(
        key=COLOR_GROUP,
        label=rotulo,
        options=opcoes,
        required=True,
        missing_message=gettext_lazy("Escolha uma cor antes de continuar."),
        invalid_message=gettext_lazy("Esta cor não está disponível."),
    )


def choice_groups(product) -> list[ChoiceGroup]:
    """Tudo o que este produto oferece para o cliente escolher, na ordem.

    É aqui que um provedor novo entra (um ``ProductOption`` de modo «escolha
    do cliente», por exemplo): quem consome — página, formulário, carrinho,
    pedido — não muda.
    """
    grupos = []
    cor = _color_group(product)
    if cor is not None:
        grupos.append(cor)
    return grupos


# ---------------------------------------------------------------------------
# Resolução: do que veio do cliente para escolhas válidas
# ---------------------------------------------------------------------------


def resolve_choices(product, raw) -> tuple:
    """Confere ``{chave do grupo: id}`` contra o que o produto oferece.

    Regras, na ordem em que doem:

    * grupo oferecido e obrigatório sem valor → ``ChoiceError`` «missing»;
    * valor que não é uma das opções do grupo (id de outro produto, cor que
      saiu da paleta, texto qualquer) → ``ChoiceError`` «invalid»;
    * chave que o produto **não** oferece → ignorada. Um produto «Uma cor» que
      receba ``choice_color`` não passa a ter escolha por isso — o modo dele
      é quem manda, e a chave sobrando é ruído, não fraude.

    O adicional de cada escolha devolvida vem do banco (da opção resolvida),
    nunca do que o cliente mandou: o navegador só fala em ids.
    """
    raw = raw or {}
    escolhidas = []
    for grupo in choice_groups(product):
        bruto = raw.get(grupo.key)
        bruto = "" if bruto is None else str(bruto).strip()
        if not bruto:
            if grupo.required:
                raise ChoiceError(grupo.key, str(grupo.missing_message), "missing")
            continue
        alvo = next((opcao for opcao in grupo.options if str(opcao.value_id) == bruto), None)
        if alvo is None:
            raise ChoiceError(grupo.key, str(grupo.invalid_message), "invalid")
        escolhidas.append(alvo)
    return tuple(escolhidas)


def raw_from(choices) -> dict:
    """``{chave: id}`` — o formato guardado no carrinho."""
    return {choice.key: int(choice.value_id) for choice in choices}


def price_adjustment(choices) -> Decimal:
    """A soma dos adicionais — o que se soma ao preço da variante."""
    return sum((Decimal(choice.price_delta) for choice in choices), ZERO)


def choices_text(choices) -> str:
    """«Cor: Dourado (+ € 2,00)», uma escolha por trecho, no idioma atual.

    O mesmo separador das opções da variante (``OPTIONS_TEXT_SEPARATOR``),
    para o pedido quebrar as duas listas com a mesma regra.
    """
    return OPTIONS_TEXT_SEPARATOR.join(choice.text for choice in choices)
