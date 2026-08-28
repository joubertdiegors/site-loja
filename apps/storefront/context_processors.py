"""Conteúdo da loja disponível em todo template.

A faixa do topo e o rodapé aparecem em **toda** página, então precisam estar no
contexto de todas elas. O custo disso é real — são consultas que rodariam
inclusive no Admin, que nem desenha rodapé.

Por isso nada aqui é avaliado na entrada:

* as listas são querysets preguiçosos — viram consulta só se o template as
  percorrer;
* os registros únicos vêm num `SimpleLazyObject`, que só consulta no primeiro
  acesso a um atributo.

O `base.html` sempre desenha os dois blocos, então na loja as consultas
acontecem; no Admin, não.
"""

from django.db.models import Prefetch
from django.utils.functional import SimpleLazyObject

from apps.storefront.models import FooterColumn, FooterLink, FooterSettings, TopBarItem


def _footer_columns():
    """As colunas ativas do rodapé, com links, títulos e páginas.

    Cinco consultas fixas, independentes de quantos links existam: as colunas,
    os títulos delas, os links (com a página junto, por `select_related`), os
    rótulos dos links e os títulos das páginas.

    A página vem no mesmo `SELECT` dos links porque é uma chave estrangeira —
    `prefetch_related("links__page")` custaria uma consulta a mais para trazer
    o mesmo punhado de linhas.
    """
    links = FooterLink.objects.select_related("page").prefetch_related(
        "translations", "page__translations"
    )
    return list(
        FooterColumn.objects.filter(is_active=True)
        .order_by("sort_order", "id")
        .prefetch_related("translations", Prefetch("links", queryset=links))
    )


def storefront(request):
    return {
        "top_bar_items": TopBarItem.objects.for_display(),
        "footer_settings": SimpleLazyObject(FooterSettings.current),
        "footer_columns": SimpleLazyObject(_footer_columns),
    }
