"""Quais avisos da loja aparecem nesta requisição, e onde.

Três perguntas, cada uma respondida uma vez só:

* **Em que área da loja estamos?** — `page_for(request)`. A resposta vem da
  rota que o Django já resolveu: o namespace de cada `urls.py` (`catalog`,
  `cart`, `accounts`...) diz a área, e algumas rotas pedem uma área própria
  (a Home, o produto, a finalização). Nenhuma view precisa saber que avisos
  existem.
* **Quais avisos o visitante já fechou?** — `dismissed(request)`. O cookie
  `jd_avisos_fechados` guarda pares `id-versão`, escritos por
  `static/js/notices.js` quando alguém clica no X. Lido aqui, no servidor, o
  aviso fechado nem chega ao HTML: não pisca na tela antes de sumir, e a
  página seguinte já sai sem ele.
* **Quais avisos, nesta posição?** — `notices_for(...)`. Os avisos ligados
  vêm do banco uma vez por requisição (uma consulta, mais a das traduções
  quando há algum), e cada posição filtra a mesma lista.
"""

import re

from apps.storefront.models import NoticePage, NoticePosition, StoreNotice

#: Rotas que têm área própria, pelo nome completo.
PAGE_BY_VIEW = {
    "home:index": NoticePage.HOME,
    "catalog:product_detail": NoticePage.PRODUCT,
    "cart:checkout": NoticePage.CHECKOUT,
}

#: As demais, pela área (namespace) do `urls.py` a que pertencem.
PAGE_BY_NAMESPACE = {
    "catalog": NoticePage.CATALOG,
    "cart": NoticePage.CART,
    "accounts": NoticePage.ACCOUNT,
    "orders": NoticePage.ACCOUNT,
    "storefront": NoticePage.INSTITUTIONAL,
}

COOKIE_NAME = "jd_avisos_fechados"

#: Um par `id-versão`. Nada fora deste desenho é lido do cookie.
_PAR = re.compile(r"^(\d{1,9})-([0-9a-f]{10})$")

#: Mais que isso é cookie adulterado: o script guarda no máximo 30.
_MAXIMO = 60


def page_for(request) -> str | None:
    """A área da loja desta requisição — ou ``None`` fora da loja (Admin, 404)."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return None
    area = PAGE_BY_VIEW.get(match.view_name)
    if area is None:
        area = PAGE_BY_NAMESPACE.get(match.namespace)
    return area.value if area else None


def dismissed(request) -> set[tuple[int, str]]:
    """Os pares (aviso, versão) que o visitante fechou."""
    bruto = request.COOKIES.get(COOKIE_NAME, "")
    pares = set()
    for pedaco in bruto.split(".")[:_MAXIMO]:
        encontrado = _PAR.match(pedaco)
        if encontrado:
            pares.add((int(encontrado.group(1)), encontrado.group(2)))
    return pares


def _active_notices(request) -> list[StoreNotice]:
    """Os avisos ligados, lidos uma vez por requisição e guardados nela."""
    cache = getattr(request, "_jd_store_notices", None)
    if cache is None:
        cache = list(StoreNotice.objects.for_display())
        request._jd_store_notices = cache
    return cache


def notices_for(request, position: str, page: str | None) -> list[dict]:
    """Os avisos desta posição, nesta área, prontos para o template.

    Cada item leva a linha de tradução do idioma de quem lê (ver
    `StoreNotice.display_translation`) — sem ela, sem aviso: nunca sai uma
    faixa em branco. Um aviso que pode ser fechado e já foi fechado nesta
    mesma versão fica de fora.
    """
    if not page or position not in NoticePosition.values:
        return []
    fechados = dismissed(request)
    itens = []
    for aviso in _active_notices(request):
        if aviso.position != position or page not in (aviso.pages or []):
            continue
        linha = aviso.display_translation()
        if linha is None:
            continue
        versao = aviso.version
        if aviso.dismissible and (aviso.pk, versao) in fechados:
            continue
        itens.append({
            "notice": aviso,
            "version": versao,
            "title": linha.title,
            "message": linha.message,
            "href": aviso.href,
            "link_label": linha.link_label,
        })
    return itens
