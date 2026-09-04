# -*- coding: utf-8 -*-
"""Cor escolhida no Admin: presets da marca ou um hex livre.

O problema que este módulo resolve é o de sempre com cor configurável: dar
liberdade sem deixar a loja sair da marca nem produzir texto ilegível.

A solução tem três partes:

1. **Presets nomeados.** ``"yellow"`` vale mais que ``"#f5d547"``: se a marca
   ajustar o amarelo, quem escolheu "Amarelo" acompanha, e quem digitou o hex
   fica para trás. O Admin oferece os presets primeiro.

2. **Hex livre quando preciso.** Uma campanha pode pedir um tom que a paleta
   não tem. O campo aceita ``#rrggbb``, validado na entrada.

3. **Contraste conferido no `clean()`.** Uma cor de texto sobre uma de fundo
   abaixo de 4,5:1 é recusada com uma mensagem que diz o número obtido e o
   mínimo. Não é advertência: é erro de formulário. Uma cor mal escolhida no
   Admin não pode virar um texto que ninguém lê no site.

O valor guardado nunca vai direto para o HTML: quem o consome é
``resolve()``, e o template o entrega como **variável CSS** no atributo
``style`` do bloco. Nenhum hex fica escrito em template.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

#: A paleta da marca. ``(valor, rótulo, hex)``.
#:
#: A ordem é a que aparece no Admin: primeiro os fundos, depois as tintas de
#: texto, depois os acentos e os tons claros.
BRAND_COLORS: tuple[tuple[str, str, str], ...] = (
    ("cream", "Creme", "#faf8f4"),
    ("white", "Branco", "#ffffff"),
    ("navy", "Navy", "#1b1530"),
    ("navy-soft", "Navy claro", "#2a1f47"),
    ("purple", "Roxo", "#4a1a8c"),
    ("purple-dark", "Roxo escuro", "#2a0f52"),
    ("yellow", "Amarelo", "#f5d547"),
    ("mint", "Menta", "#7edcd8"),
    ("coral", "Coral", "#f5b5a3"),
    ("lavender", "Lavanda", "#efe8fa"),
    ("mint-light", "Menta claro", "#e3f7f6"),
    ("yellow-light", "Amarelo claro", "#fff3d6"),
    ("coral-light", "Coral claro", "#fde6de"),
    ("ink-soft", "Texto secundário", "#4b4560"),
    ("ink-on-deep", "Texto sobre escuro", "#c9c3d6"),
    ("border", "Borda", "#e8e3da"),
)

_POR_VALOR = {valor: hexa for valor, _rotulo, hexa in BRAND_COLORS}

#: Para o `choices` de um campo. O hex livre entra por um campo de texto à
#: parte, e não como opção da lista.
COLOR_CHOICES = tuple((valor, rotulo) for valor, rotulo, _hexa in BRAND_COLORS)

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: O mínimo da WCAG AA para texto corrente.
CONTRASTE_MINIMO = 4.5


def validate_color(value: str) -> None:
    """Aceita um preset da marca ou um hex de três/seis dígitos."""
    if not value:
        return
    if value in _POR_VALOR:
        return
    if HEX.match(value):
        return
    raise ValidationError(
        "Use uma cor da marca (%(presets)s) ou um código hexadecimal "
        "como #4A1A8C." % {"presets": ", ".join(sorted(_POR_VALOR))},
        code="cor_invalida",
    )


def resolve(value: str, default: str = "") -> str:
    """O hex de uma cor guardada — preset ou literal."""
    if not value:
        return resolve(default) if default else ""
    if value in _POR_VALOR:
        return _POR_VALOR[value]
    if HEX.match(value):
        return _expandir(value.lower())
    return resolve(default) if default else ""


def _expandir(hexa: str) -> str:
    """`#abc` vira `#aabbcc`, para o cálculo de luminância não errar."""
    if len(hexa) == 4:
        return "#" + "".join(c * 2 for c in hexa[1:])
    return hexa


def _canais(hexa: str) -> tuple[int, int, int]:
    hexa = _expandir(hexa).lstrip("#")
    return int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16)


def _luminancia(hexa: str) -> float:
    def canal(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _canais(hexa)
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def contrast(a: str, b: str) -> float:
    """Razão de contraste entre duas cores, como a WCAG a define."""
    la, lb = _luminancia(a), _luminancia(b)
    claro, escuro = max(la, lb), min(la, lb)
    return (claro + 0.05) / (escuro + 0.05)


def check_contrast(texto: str, fundo: str, *, campo: str, minimo: float = CONTRASTE_MINIMO):
    """Devolve o erro de contraste, ou ``None`` se a dupla for legível.

    Pensado para entrar no ``clean()`` de um modelo::

        erro = check_contrast(self.text_color, self.bg_color, campo="text_color")
        if erro:
            raise ValidationError(erro)
    """
    tinta, papel = resolve(texto), resolve(fundo)
    if not tinta or not papel:
        return None
    razao = contrast(tinta, papel)
    if razao >= minimo:
        return None
    return {
        campo: ValidationError(
            "Contraste de %(razao)s:1 entre o texto (%(tinta)s) e o fundo "
            "(%(papel)s). O mínimo legível é %(minimo)s:1 — escolha uma cor "
            "mais escura ou mais clara." % {
                "razao": ("%.2f" % razao).replace(".", ","),
                "tinta": tinta, "papel": papel,
                "minimo": ("%.1f" % minimo).replace(".", ","),
            },
            code="contraste_baixo",
        )
    }


def swatch(value: str) -> str:
    """Um quadradinho da cor, para a listagem do Admin."""
    hexa = resolve(value)
    if not hexa:
        return ""
    return (
        '<span style="display:inline-block;width:16px;height:16px;'
        'border-radius:4px;border:1px solid rgba(0,0,0,.2);'
        'background:%s;vertical-align:-3px;margin-right:6px"></span>%s' % (hexa, value)
    )
