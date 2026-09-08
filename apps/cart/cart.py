"""O carrinho: leitura, escrita e regras.

Onde ele é guardado depende de quem está comprando (ver ``storage.py``):

* **visitante** — na sessão do Django, como desde a etapa 3;
* **autenticado** — no banco (``cart.Cart`` / ``cart.CartItem``), para
  sobreviver a trocar de dispositivo.

Nos dois casos o formato é o mesmo dicionário — só identidade e quantidade; o
resto é lido do catálogo na hora::

    {
        "12:0:-": {"product_id": 12, "variant_id": None, "quantity": 2,
                   "customization": None},
        "12:34:a1b2c3d4e5": {"product_id": 12, "variant_id": 34, "quantity": 1,
                             "customization": {"type": "photo", "upload_id": 7,
                                               "text": "", "notes": "..."}},
    }

A chave da linha é ``produto:variante:personalização`` (ver ``keys.py``).

O formato antigo (``{"12": {"quantity": 2}}``, da etapa 3) é convertido na
primeira leitura: nenhum carrinho em sessão se perde.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db.models import Prefetch
from django.utils.translation import gettext as _

from apps.cart.keys import customization_fingerprint, line_key
from apps.cart.models import CustomizationUpload
from apps.cart.storage import CART_SESSION_KEY, DatabaseStorage, SessionStorage
from apps.catalog.models import (
    Product,
    ProductStatus,
    ProductVariant,
    product_description_prefetches,
    variant_option_prefetches,
)

__all__ = [
    "CART_SESSION_KEY",
    "Cart",
    "CartLine",
    "CartResult",
    "customization_fingerprint",
    "line_key",
    "load_products",
    "load_variants",
    "load_uploads",
    "max_quantity_for",
]


def load_products(items: dict) -> dict:
    """Produtos ativos das linhas, com tudo que o card e o carrinho precisam.

    ``variants`` está no prefetch — com cor e material — porque desde a etapa 8
    é a variante que carrega preço, estoque e amostras de cor. Sem ele, cada
    linha do carrinho custaria uma consulta extra.
    """
    ids = {item.get("product_id") for item in items.values() if item.get("product_id")}
    if not ids:
        return {}
    queryset = (
        Product.objects.filter(pk__in=ids, status=ProductStatus.ACTIVE)
        .select_related("category")
        .prefetch_related(
            "translations",
            *product_description_prefetches(),
            "media",
            "category__translations",
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.select_related("color", "material")
                .prefetch_related("color__translations", "material__translations")
                .order_by(
                    "sort_order", "id"
                ),
            ),
        )
    )
    return {product.pk: product for product in queryset}


def load_variants(items: dict) -> dict:
    ids = {item.get("variant_id") for item in items.values() if item.get("variant_id")}
    if not ids:
        return {}
    queryset = (
        ProductVariant.objects.filter(pk__in=ids, is_active=True)
        .select_related("color", "material", "product")
        # A foto vinculada à variante (etapa 13). Sem isto, `display_media`
        # custaria uma consulta por linha do carrinho. As traduções de cor e
        # material, pelo mesmo motivo: a página do carrinho escreve o nome de
        # cada uma no idioma do cliente.
        .prefetch_related(
            "media", "color__translations", "material__translations", *variant_option_prefetches()
        )
    )
    return {variant.pk: variant for variant in queryset}


def load_uploads(items: dict) -> dict:
    ids = {
        (item.get("customization") or {}).get("upload_id")
        for item in items.values()
        if (item.get("customization") or {}).get("upload_id")
    }
    if not ids:
        return {}
    return {upload.pk: upload for upload in CustomizationUpload.objects.filter(pk__in=ids)}


def max_quantity_for(product: Product, variant: ProductVariant | None = None) -> int:
    """Quantas unidades desta linha podem ir para o carrinho.

    O número vem **da variante** e só dela: é ela que tem estoque, e é ela que
    diz se é sob encomenda ou se aceita venda sem saldo. Sem variante não há
    quantidade possível — não existe o que vender.

    Sem pedidos não há reserva: a validação é sobre o estoque de agora e terá
    que ser refeita no checkout.
    """
    ceiling = getattr(settings, "CART_MAX_QUANTITY_PER_LINE", 99)
    if variant is None:
        return 0
    if variant.made_to_order or variant.allow_backorder:
        return ceiling
    return min(variant.stock_quantity, ceiling)


@dataclass(frozen=True)
class CartLine:
    key: str
    product: Product
    variant: ProductVariant | None
    quantity: int
    customization: dict | None = None
    upload: CustomizationUpload | None = None

    @property
    def unit_price(self) -> Decimal:
        """Sempre o preço da variante. O produto não tem preço."""
        if self.variant is None:
            return Decimal("0.00")
        return self.variant.sale_price or Decimal("0.00")

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity

    @property
    def max_quantity(self) -> int:
        return max_quantity_for(self.product, self.variant)

    @property
    def at_maximum(self) -> bool:
        return self.quantity >= self.max_quantity

    @property
    def display_media(self):
        """A foto da linha: a da variante comprada, quando existe.

        Desde a etapa 13 uma foto pode estar vinculada a uma variante. Quem
        comprou o vaso preto tem de ver o vaso preto no carrinho — mostrar a
        foto de abertura do produto seria mostrar outra peça.

        Sem foto própria, a do produto: é o caso comum, não falta de dado.
        """
        if self.variant is not None:
            propria = self.variant.display_media
            if propria is not None:
                return propria
        return self.product.display_media

    @property
    def display_name(self) -> str:
        return self.product.display_name

    @property
    def variant_label(self) -> str:
        return self.variant.label if self.variant is not None else ""

    @property
    def unit_weight_grams(self) -> int:
        """Peso unitário desta linha — da variante, para o frete."""
        if self.variant is None or self.variant.weight_grams is None:
            return 0
        return int(self.variant.weight_grams)

    @property
    def production_days(self) -> int:
        """Prazo de produção desta linha — da variante."""
        if self.variant is None:
            return 0
        return self.variant.production_lead_time_days or 0

    # -- personalização ----------------------------------------------------

    @property
    def customization_type(self) -> str:
        return (self.customization or {}).get("type", "")

    @property
    def customization_text(self) -> str:
        return (self.customization or {}).get("text", "")

    @property
    def customization_notes(self) -> str:
        return (self.customization or {}).get("notes", "")

    @property
    def has_customization(self) -> bool:
        return bool(self.customization)


@dataclass(frozen=True)
class CartResult:
    """Resultado de uma operação, com mensagem pronta para o usuário."""

    ok: bool
    message: str
    level: str = "success"  # success | warning | error
    quantity: int = 0
    key: str = ""


class Cart:
    """Leitura e escrita do carrinho, onde quer que ele esteja guardado.

    Instanciar é barato: o armazenamento é escolhido aqui, mas nada é lido até
    alguém pedir. O contador do header usa só ``total_quantity``, que é uma
    soma na sessão (visitante) ou uma agregação de uma linha (autenticado).
    """

    def __init__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            self.storage = DatabaseStorage(user)
        else:
            self.storage = SessionStorage(request.session)
        self._lines_cache: list[CartLine] | None = None

    @property
    def is_persistent(self) -> bool:
        """O carrinho sobrevive a fechar o navegador?

        É o que o aviso discreto da gaveta consulta para convidar o visitante a
        criar conta — sem bloquear nada.
        """
        return isinstance(self.storage, DatabaseStorage)

    # -- acesso bruto ------------------------------------------------------

    @property
    def _items(self) -> dict:
        return self.storage.read()

    def _save(self, items: dict) -> None:
        self.storage.write(items)
        self._lines_cache = None

    # -- consultas ---------------------------------------------------------

    @property
    def total_quantity(self) -> int:
        """Total de UNIDADES (não de linhas) — é o número do header."""
        return self.storage.total_quantity()

    @property
    def is_empty(self) -> bool:
        return self.total_quantity == 0

    def quantity_of(self, key: str) -> int:
        return int(self._items.get(key, {}).get("quantity", 0))

    def lines(self) -> list[CartLine]:
        """Linhas do carrinho com produto, variante e anexo carregados.

        O que saiu do ar (produto inativo, variante desativada, arquivo
        apagado) é removido da sessão em silêncio: um carrinho não pode mostrar
        item que não existe mais.
        """
        if self._lines_cache is not None:
            return self._lines_cache

        items = self._items
        if not items:
            self._lines_cache = []
            return self._lines_cache

        products = load_products(items)
        variants = load_variants(items)
        uploads = load_uploads(items)

        lines: list[CartLine] = []
        cleaned: dict = {}

        for key, item in items.items():
            product = products.get(item.get("product_id"))
            if product is None:
                continue

            variant = None
            if item.get("variant_id"):
                variant = variants.get(item["variant_id"])
                if variant is None:  # variante desativada ou removida
                    continue

            quantity = max(0, int(item.get("quantity", 0)))
            if quantity == 0:
                continue

            customization = item.get("customization") or None
            upload = None
            if customization and customization.get("upload_id"):
                upload = uploads.get(customization["upload_id"])
                if upload is None:  # arquivo sumiu do storage
                    continue

            lines.append(
                CartLine(
                    key=key,
                    product=product,
                    variant=variant,
                    quantity=quantity,
                    customization=customization,
                    upload=upload,
                )
            )
            cleaned[key] = {
                "product_id": product.pk,
                "variant_id": variant.pk if variant else None,
                "quantity": quantity,
                "customization": customization,
            }

        if cleaned != items:
            self._save(cleaned)

        self._lines_cache = lines
        return lines

    def __iter__(self):
        return iter(self.lines())

    def __len__(self) -> int:
        return len(self.lines())

    @property
    def subtotal(self) -> Decimal:
        """Soma em ``Decimal`` — nenhum float participa do cálculo."""
        return sum((line.total for line in self.lines()), Decimal("0.00"))

    # -- escrita -----------------------------------------------------------

    def add(
        self,
        product: Product,
        variant: ProductVariant | None = None,
        quantity: int = 1,
        customization: dict | None = None,
    ) -> CartResult:
        if product.status != ProductStatus.ACTIVE:
            return CartResult(False, _("Este produto não está disponível."), "error")

        if variant is not None and (not variant.is_active or variant.product_id != product.pk):
            return CartResult(False, _("Esta opção não está disponível."), "error")

        # Sem variante escolhida, a padrão do produto — que é o que a página de
        # opção única já manda num campo oculto. O que não existe é linha sem
        # variante nenhuma: preço, peso, prazo e estoque moram nela.
        if variant is None:
            variant = product.default_variant
        if variant is None:
            return CartResult(False, _("Este produto não está disponível."), "error")

        quantity = max(1, int(quantity))
        allowed = max_quantity_for(product, variant)
        if allowed <= 0:
            return CartResult(False, _("Produto esgotado no momento."), "error")

        key = line_key(product.pk, variant.pk, customization)
        wanted = self.quantity_of(key) + quantity
        final = min(wanted, allowed)

        items = dict(self._items)
        items[key] = {
            "product_id": product.pk,
            "variant_id": variant.pk,
            "quantity": final,
            "customization": customization,
        }
        self._save(items)

        if final < wanted:
            return CartResult(
                True,
                _("Adicionamos o máximo disponível: %(count)s unidade(s).") % {"count": final},
                "warning",
                final,
                key,
            )
        return CartResult(True, _("Produto adicionado ao carrinho."), "success", final, key)

    def set_quantity(self, key: str, quantity: int) -> CartResult:
        """Define a quantidade absoluta de uma linha. Zero (ou menos) remove."""
        item = self._items.get(key)
        if item is None:
            return CartResult(False, _("Este item não estava no carrinho."), "warning")

        quantity = int(quantity)
        if quantity <= 0:
            return self.remove(key)

        line = self._line_for(key)
        if line is None:
            return self.remove(key)

        allowed = max_quantity_for(line.product, line.variant)
        if allowed <= 0:
            self.remove(key)
            return CartResult(False, _("Produto esgotado: item removido."), "error")

        final = min(quantity, allowed)
        items = dict(self._items)
        items[key] = {**item, "quantity": final}
        self._save(items)

        if final < quantity:
            return CartResult(
                True,
                _("Disponível apenas %(count)s unidade(s) deste produto.") % {"count": final},
                "warning",
                final,
                key,
            )
        return CartResult(True, _("Carrinho atualizado."), "success", final, key)

    def remove(self, key: str) -> CartResult:
        items = dict(self._items)
        if items.pop(key, None) is None:
            return CartResult(False, _("Este item não estava no carrinho."), "warning")
        self._save(items)
        return CartResult(True, _("Produto removido do carrinho."), "success", 0, key)

    def clear(self) -> None:
        self._save({})


    # -- auxiliares --------------------------------------------------------

    def _line_for(self, key: str) -> CartLine | None:
        for line in self.lines():
            if line.key == key:
                return line
        return None
