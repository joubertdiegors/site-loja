"""Os favoritos do cliente, disponíveis em todo template.

O coração aparece em **cada card** da loja. Perguntar ao banco "este produto é
favorito?" uma vez por card seria um N+1 clássico: uma vitrine de 24 produtos
custaria 24 consultas.

Aqui a resposta vem de uma consulta só, e sempre a mesma: o conjunto de ids
favoritados. O card pergunta `{% if product.pk in favorite_ids %}`, que é uma
busca em memória.

## Só o que o cliente poderia comprar

O conjunto traz apenas os favoritos **vendáveis**, e por dois motivos que
apontam para o mesmo lado:

* o card só existe na loja para produto vendável, então nada se perde;
* o contador do cabeçalho passa a bater com o que a página de favoritos
  mostra. Um "3" no coração e dois produtos na tela é o tipo de contradição
  que faz o cliente desconfiar da loja inteira.

O favorito de um produto fora do ar **continua no banco** — some da lista, não
da conta. Ver `Favorite`.

## Nada é guardado entre requisições

O conjunto é montado por requisição, a partir de `request.user`. Um cache
global aqui vazaria a lista de um cliente para o outro — e é exatamente o tipo
de bug que só aparece em produção, com dois usuários ao mesmo tempo.

Visitante não custa consulta nenhuma: sai um conjunto vazio sem tocar no banco.
"""

from django.utils.functional import SimpleLazyObject


def favorites(request):
    """`favorite_ids`: o conjunto de ids favoritados e visíveis.

    Preguiçoso: só vira consulta se o template usar. O Admin e as respostas
    parciais que não desenham cards não pagam nada.
    """

    def ids():
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return frozenset()

        from apps.accounts.models import Favorite

        return frozenset(
            Favorite.objects.for_user(user).visible().values_list("product_id", flat=True)
        )

    # Só `favorite_ids`. A contagem sai de `favorite_ids|length` no template:
    # um `SimpleLazyObject` embrulhando um inteiro não serve de contador para o
    # `{% blocktranslate count %}`, que precisa de um `int` de verdade para
    # escolher singular ou plural.
    return {"favorite_ids": SimpleLazyObject(ids)}
