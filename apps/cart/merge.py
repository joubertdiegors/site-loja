"""Da sessão para a conta: o carrinho do visitante não pode se perder.

Acontece no sinal ``user_logged_in`` (ver ``signals.py``), o que cobre os dois
caminhos com o mesmo código — quem acabou de se cadastrar e quem só entrou.

Regra do merge: a linha é **produto + variante + escolhas do cliente +
personalização**. Quantidades de linhas idênticas somam; linhas que diferem em
qualquer um dos quatro convivem (Branco e Dourado são duas linhas)::

    sessão:  A/Preto/25cm × 2   B × 1
    conta:   A/Preto/25cm × 1              C × 3
    ------------------------------------------------
    depois:  A/Preto/25cm × 3   B × 1      C × 3

Estoque **não** é reservado (não existe pedido ainda): a soma é limitada ao que
está disponível agora, exatamente como qualquer outra escrita no carrinho, e o
cliente é avisado. O checkout terá que validar de novo.
"""

from dataclasses import dataclass, field

from django.utils.translation import gettext as _

from apps.cart.cart import (
    choices_price_is_positive,
    load_products,
    load_uploads,
    load_variants,
    max_quantity_for,
)
from apps.cart.keys import normalize_choices
from apps.cart.storage import DatabaseStorage, SessionStorage
from apps.catalog.choices import ChoiceError, resolve_choices


@dataclass
class MergeReport:
    """O que aconteceu no merge — vira a mensagem mostrada ao cliente."""

    merged_lines: int = 0
    existing_lines: int = 0
    limited_lines: list[str] = field(default_factory=list)
    dropped_lines: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.merged_lines or self.limited_lines or self.dropped_lines)

    @property
    def message(self) -> str:
        """Só fala quando há algo que o cliente não esperava.

        Quantidade cortada pelo estoque, sempre. Encontro de dois carrinhos,
        sim — é surpreendente ver itens que não foram postos neste navegador.
        Carrinho que simplesmente veio junto do cadastro, não: o contador do
        header já conta essa história, e um aviso a mais só faria barulho.
        """
        if self.limited_lines:
            return _(
                "Seu carrinho foi recuperado. Ajustamos a quantidade de "
                "%(items)s ao estoque disponível."
            ) % {"items": ", ".join(self.limited_lines)}
        if self.merged_lines and self.existing_lines:
            return _("Seu carrinho foi recuperado.")
        return ""


def merge_session_cart(request, user) -> MergeReport:
    """Soma o carrinho da sessão ao carrinho do usuário e esvazia a sessão."""
    report = MergeReport()

    session_storage = SessionStorage(request.session)
    session_items = session_storage.read()
    if not session_items:
        return report

    database = DatabaseStorage(user)
    merged = dict(database.read())
    report.existing_lines = len(merged)

    for key, item in session_items.items():
        current = merged.get(key)
        if current is None:
            merged[key] = dict(item)
        else:
            merged[key] = {
                **current,
                "quantity": int(current.get("quantity", 0)) + int(item.get("quantity", 0)),
            }
        report.merged_lines += 1

    clamped, limited, dropped = clamp_items(merged)
    report.limited_lines = limited
    report.dropped_lines = dropped

    database.write(clamped)
    session_storage.clear()
    return report


def clamp_items(items: dict) -> tuple[dict, list[str], int]:
    """Limita cada linha ao estoque e descarta o que não existe mais.

    Devolve ``(linhas, nomes ajustados, linhas descartadas)``. Produto inativo,
    variante desligada ou arquivo de personalização apagado não entram — é a
    mesma limpeza que o carrinho já fazia ao ser lido.
    """
    products = load_products(items)
    variants = load_variants(items)
    uploads = load_uploads(items)

    clamped: dict = {}
    limited: list[str] = []
    dropped = 0

    for key, item in items.items():
        product = products.get(item.get("product_id"))
        if product is None:
            dropped += 1
            continue

        variant = None
        if item.get("variant_id"):
            variant = variants.get(item["variant_id"])
            if variant is None:
                dropped += 1
                continue

        customization = item.get("customization") or None
        if customization and customization.get("upload_id"):
            if uploads.get(customization["upload_id"]) is None:
                dropped += 1
                continue

        # A mesma regra de `Cart.lines()`: escolha que saiu do catálogo
        # descarta a linha; escolha que passou a faltar fica para o checkout
        # avisar.
        raw_choices = normalize_choices(item.get("choices"))
        try:
            escolhas = resolve_choices(product, raw_choices)
        except ChoiceError as erro:
            if erro.reason != "missing":
                dropped += 1
                continue
            escolhas = ()
            raw_choices = {}
        if not choices_price_is_positive(variant, escolhas):
            dropped += 1
            continue

        wanted = max(0, int(item.get("quantity", 0)))
        allowed = max_quantity_for(product, variant)
        quantity = min(wanted, allowed)

        if quantity <= 0:
            dropped += 1
            continue
        if quantity < wanted:
            limited.append(product.display_name)

        clamped[key] = {
            "product_id": product.pk,
            "variant_id": variant.pk if variant else None,
            "quantity": quantity,
            "customization": customization,
            "choices": raw_choices,
        }

    return clamped, limited, dropped
