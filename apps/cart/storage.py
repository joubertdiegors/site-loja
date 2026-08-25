"""Onde o carrinho é guardado.

Duas implementações com a mesma interface mínima — ``read()``, ``write()``,
``total_quantity()`` e ``clear()``:

* ``SessionStorage``  — visitante. Nada no banco: quem talvez nunca volte não
  precisa deixar linha em tabela nenhuma;
* ``DatabaseStorage`` — usuário autenticado. O carrinho sobrevive a trocar de
  navegador, de computador e a limpar cookies.

O ``Cart`` (``apps/cart/cart.py``) não sabe qual das duas está em uso: escolhe
uma no construtor e trabalha sempre com o mesmo dicionário::

    {"12:34:a1b2c3": {"product_id": 12, "variant_id": 34, "quantity": 2,
                      "customization": {...}}}

Manter esse formato como moeda comum é o que permitiu acrescentar persistência
sem reescrever o carrinho, as views e os templates.
"""

from django.db import transaction
from django.db.models import Sum

from apps.cart.models import Cart as CartModel
from apps.cart.models import CartItem

CART_SESSION_KEY = "cart"


class SessionStorage:
    """Carrinho do visitante, dentro da sessão do Django."""

    def __init__(self, session):
        self.session = session

    def read(self) -> dict:
        return self._migrate(self.session.get(CART_SESSION_KEY, {}) or {})

    def write(self, items: dict) -> None:
        self.session[CART_SESSION_KEY] = items
        self.session.modified = True

    def total_quantity(self) -> int:
        # Não toca no banco: é o contador do header, presente em toda página.
        return sum(int(item.get("quantity", 0)) for item in self.read().values())

    def clear(self) -> None:
        self.write({})

    @staticmethod
    def _migrate(items: dict) -> dict:
        """Converte o formato da etapa 3 (``{"12": {"quantity": 2}}``)."""
        from apps.cart.keys import line_key

        migrated = {}
        for key, item in items.items():
            if not isinstance(item, dict):
                continue
            if "product_id" in item:
                migrated[key] = item
                continue
            if not str(key).isdigit():
                continue
            product_id = int(key)
            migrated[line_key(product_id, None, None)] = {
                "product_id": product_id,
                "variant_id": None,
                "quantity": int(item.get("quantity", 0)),
                "customization": None,
            }
        return migrated


class DatabaseStorage:
    """Carrinho de quem está autenticado, em ``cart.Cart`` / ``cart.CartItem``.

    A linha só é criada quando há o que guardar: entrar na loja e não colocar
    nada no carrinho não cria registro nenhum.
    """

    def __init__(self, user):
        self.user = user
        self._cart = None
        self._items = None

    # -- acesso ------------------------------------------------------------

    def cart(self, create: bool = False):
        if self._cart is None:
            if create:
                self._cart, _created = CartModel.objects.get_or_create(user=self.user)
            else:
                self._cart = CartModel.objects.filter(user=self.user).first()
        return self._cart

    def read(self) -> dict:
        if self._items is not None:
            return self._items

        cart = self.cart()
        if cart is None:
            self._items = {}
            return self._items

        self._items = {item.line_key: item.to_item() for item in cart.items.all()}
        return self._items

    def write(self, items: dict) -> None:
        """Sincroniza as linhas do banco com o dicionário recebido.

        Escrita completa e não incremental de propósito: é a mesma chamada que
        a sessão recebe, o volume é de dezenas de linhas e o resultado é
        trivialmente correto — some o que saiu, entra o que chegou, atualiza o
        que mudou.
        """
        if not items:
            cart = self.cart()
            if cart is not None:
                cart.items.all().delete()
                # ``updated_at`` precisa mexer: é o relógio do carrinho
                # abandonado.
                cart.save(update_fields=["updated_at"])
            self._items = {}
            return

        cart = self.cart(create=True)
        with transaction.atomic():
            existing = {item.line_key: item for item in cart.items.all()}

            for key in set(existing) - set(items):
                existing[key].delete()

            for key, item in items.items():
                fields = CartItem.fields_from_item(item)
                current = existing.get(key)
                if current is None:
                    CartItem.objects.create(cart=cart, line_key=key, **fields)
                    continue
                changed = [
                    name
                    for name, value in fields.items()
                    if getattr(current, name) != value
                ]
                if changed:
                    for name in changed:
                        setattr(current, name, fields[name])
                    current.save(update_fields=[*changed, "updated_at"])

            cart.save(update_fields=["updated_at"])

        self._items = None  # relê do banco na próxima leitura

    def total_quantity(self) -> int:
        if self._items is not None:
            return sum(int(item.get("quantity", 0)) for item in self._items.values())
        # Uma agregação barata, em vez de carregar as linhas só para somar.
        total = CartItem.objects.filter(cart__user=self.user).aggregate(total=Sum("quantity"))
        return int(total["total"] or 0)

    def clear(self) -> None:
        self.write({})
