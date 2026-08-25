"""Identidade de uma linha do carrinho.

Módulo minúsculo e sem dependências de propósito: a chave é usada pela sessão,
pelo banco e pelo merge, e nenhum dos três pode depender dos outros dois.

A chave é ``produto:variante:assinatura-da-personalização``. Duas
personalizações diferentes do mesmo produto são **duas linhas** — é o que o
cliente espera, e é a mesma identidade que ``OrderItem`` vai precisar.
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


def line_key(product_id: int, variant_id: int | None, customization: dict | None) -> str:
    return f"{product_id}:{variant_id or 0}:{customization_fingerprint(customization)}"
