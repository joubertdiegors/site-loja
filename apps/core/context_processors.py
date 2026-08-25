"""Contexto disponível em todos os templates."""

from django.conf import settings

from apps.categories.models import Category
from apps.core.languages import active_languages


def site(request):
    """Dados de marca e navegação usados pelo header/footer.

    O queryset é preguiçoso: só vira consulta se o template percorrer o menu.
    """
    return {
        "site_name": "JD PRINT",
        "nav_categories": (
            Category.objects.filter(is_active=True, parent__isnull=True)
            .prefetch_related("translations")
            .order_by("sort_order", "slug")
        ),
        "debug": settings.DEBUG,
        # Idiomas oferecidos na loja (tabela core.SiteLanguage), nunca uma
        # lista escrita no template.
        "site_languages": active_languages(),
    }
