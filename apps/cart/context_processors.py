"""O carrinho disponível em todos os templates (contador do header)."""

from apps.cart.cart import Cart


def cart(request):
    """Instância leve: só lê a sessão, não consulta o banco.

    ``cart.total_quantity`` soma o que já está na sessão; as linhas (com os
    produtos) só são carregadas se o template pedir.
    """
    return {"cart": Cart(request)}
