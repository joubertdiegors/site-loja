"""Utilitários pequenos e sem dependência de app específico."""

from django.utils.text import slugify


def unique_slugify(instance, value: str, slug_field: str = "slug", max_length: int = 220) -> str:
    """Gera um slug único para ``instance`` dentro da sua própria tabela.

    Acrescenta ``-2``, ``-3``... enquanto houver colisão. A unicidade final
    continua garantida pelo banco (``unique=True``); esta função apenas evita
    o erro previsível.
    """
    base = slugify(value)[:max_length].strip("-") or "item"
    model = instance.__class__
    candidate = base
    counter = 2

    queryset = model._default_manager.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)

    while queryset.filter(**{slug_field: candidate}).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: max_length - len(suffix)]}{suffix}"
        counter += 1

    return candidate
