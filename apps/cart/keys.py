"""Identidade de uma linha do carrinho.

Módulo minúsculo e sem dependências de propósito: a chave é usada pela sessão,
pelo banco e pelo merge, e nenhum dos três pode depender dos outros dois.

A chave é ``produto:variante:assinatura-da-personalização`` e, quando o
cliente escolheu alguma coisa acima da variante (a cor, em «Cores à escolha do
cliente»), ``:escolhas`` no fim — «12:34:-:color=5». Duas personalizações
diferentes do mesmo produto são **duas linhas**, e Branco e Dourado também —
é o que o cliente espera, e é a mesma identidade que ``OrderItem`` precisa.

Sem escolha nenhuma a chave é exatamente a de antes: nenhum carrinho guardado,
na sessão ou no banco, muda de identidade por causa desta etapa.
"""

import hashlib
import json


def customization_fingerprint(customization: dict | None) -> str:
    """Assinatura curta e estável da personalização.

    ``sort_keys`` garante que o mesmo conteúdo produza sempre a mesma
    assinatura, venha ele da sessão ou remontado a partir do banco.
    """
    if not customization:
        return "-"
    payload = json.dumps(customization, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def normalize_choices(raw) -> dict:
    """``{chave: id}`` com ids inteiros, ou vazio — o formato guardado.

    Tolerante de propósito: o que chega pode vir da sessão (JSON, chaves
    texto), do banco ou do formulário. O que não é um id vira ausência, e
    quem decide se a ausência é problema é ``resolve_choices``.
    """
    if not isinstance(raw, dict):
        return {}
    limpo = {}
    for chave, valor in raw.items():
        try:
            limpo[str(chave)] = int(valor)
        except (TypeError, ValueError):
            continue
    return limpo


def choices_signature(choices: dict | None) -> str:
    """«color=12» ou «color=12,opt-7=99»: as escolhas, em ordem estável.

    Só ids: o nome da cor pode ser traduzido ou renomeado, o id não.
    """
    limpo = normalize_choices(choices)
    return ",".join(f"{chave}={limpo[chave]}" for chave in sorted(limpo))


#: O tamanho de ``CartItem.line_key``.
LINE_KEY_MAX_LENGTH = 64


def line_key(
    product_id: int,
    variant_id: int | None,
    customization: dict | None,
    choices: dict | None = None,
) -> str:
    base = f"{product_id}:{variant_id or 0}:{customization_fingerprint(customization)}"
    assinatura = choices_signature(choices)
    if not assinatura:
        return base
    chave = f"{base}:{assinatura}"
    if len(chave) > LINE_KEY_MAX_LENGTH:
        # Muitas escolhas de uma vez: a assinatura vira uma impressão curta,
        # tão determinística quanto a legível.
        chave = f"{base}:{hashlib.sha256(assinatura.encode('utf-8')).hexdigest()[:12]}"
    return chave
