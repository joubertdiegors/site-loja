"""`{% store_notices "posição" %}` — os avisos da loja numa das posições fixas.

Quem chama decide só a posição; a área da loja vem da rota (ver
`apps/storefront/notices.py`). A página especial, que responde fora das rotas
normais, diz a área dela: `{% store_notices "top" page="special" %}`.
"""

from django import template

from apps.storefront.notices import notices_for, page_for

register = template.Library()


@register.inclusion_tag("components/store_notices.html", takes_context=True)
def store_notices(context, position, page=None):
    request = context.get("request")
    if request is None:
        return {"items": [], "position": position}
    area = page or page_for(request)
    return {"items": notices_for(request, position, area), "position": position}
