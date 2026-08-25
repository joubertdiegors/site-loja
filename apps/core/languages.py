"""Quais idiomas a loja oferece agora.

Ponto único de leitura: seletor, middleware e troca de idioma passam por aqui.
Nenhum template decide idioma no `{% if %}`.

Sem cache de propósito. São duas consultas minúsculas por página, e um cache
aqui erra feio em teste (uma alteração revertida pelo rollback deixaria o valor
antigo em memória). Se um dia pesar, o lugar de resolver é este arquivo.
"""

from django.conf import settings

from apps.core.models import SiteLanguage


def active_languages() -> list[SiteLanguage]:
    """Idiomas disponíveis na loja, na ordem definida no admin.

    Se a tabela estiver vazia (instalação sem a migration de dados), devolve o
    idioma padrão: a loja nunca fica sem seletor nem sem fallback.
    """
    languages = list(SiteLanguage.objects.active())
    if languages:
        return languages
    return [SiteLanguage(code=settings.LANGUAGE_CODE, is_active=True, sort_order=0)]


def active_language_codes() -> list[str]:
    return [language.code for language in active_languages()]


def is_language_available(code: str | None) -> bool:
    """O idioma pode ser usado pelo cliente neste momento?"""
    if not code:
        return False
    if code == settings.LANGUAGE_CODE:  # o padrão está sempre disponível
        return True
    return code in set(active_language_codes())
