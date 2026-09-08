"""Filtros da ficha do produto no Admin.

Servem aos templates de `templates/admin/catalog/`, que desenham os campos
do formset de variantes por nome — em grupos (identificação, opções, preço…)
em vez de na ordem plana em que o Django os entrega.
"""

from django import template

register = template.Library()


@register.filter
def bound_field(form, name):
    """O campo `name` do formulário, já ligado ao valor — ou None se não existir.

    `form[name]` levanta KeyError para campo desconhecido; no template isso
    seria uma página quebrada por causa de um nome errado num grupo. Devolver
    None deixa o `{% if %}` decidir.
    """
    try:
        return form[name]
    except (KeyError, TypeError):
        return None


@register.filter
def get_item(mapping, key):
    """`mapping[key]`, ou None. Para dicionários de configuração no contexto."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def is_checkbox(field):
    """Se o campo é uma caixa de marcar — para o desenho de «cartão» no modal."""
    try:
        return getattr(field.field.widget, "input_type", "") == "checkbox"
    except AttributeError:
        return False
