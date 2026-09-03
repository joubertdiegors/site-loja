"""Views de infraestrutura (não pertencem a nenhum domínio)."""

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import check_for_language
from django.views.decorators.http import require_POST

from apps.core.i18n import translate_path
from apps.core.languages import is_language_available


@require_POST
def set_language(request):
    """Troca o idioma da interface e volta para a mesma página.

    Substitui ``django.views.i18n.set_language``. A view do Django traduz a URL
    de retorno com ``translate_url()``, que resolve o caminho usando o idioma
    ATIVO da requisição — e esta requisição chega em ``/i18n/setlang/``, fora do
    ``i18n_patterns``. Resultado: vindo de ``/en/``, o caminho não resolve, a
    URL volta intacta e o prefixo ``/en/`` continua determinando o idioma da
    página, apesar do cookie novo. Ver ``apps.core.i18n.translate_path``.

    O resto do comportamento é o do Django: só POST, validação do destino
    contra host externo e gravação no cookie de idioma.
    """
    language = request.POST.get("language")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"

    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "/"

    # Suportado pelo sistema E oferecido na loja hoje (core.SiteLanguage).
    if not (language and check_for_language(language) and is_language_available(language)):
        return HttpResponseRedirect(next_url)

    response = HttpResponseRedirect(translate_path(next_url, language))
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


def favicon(request):
    """Redireciona ``/favicon.ico`` para o ícone que estiver configurado.

    O navegador pede este caminho sozinho, em toda visita, mesmo com o
    ``<link rel="icon">`` do ``<head>`` apontando para outro arquivo. Sem a
    rota é um 404 por visitante no log — ruído que esconde os 404 que importam.

    Não é `RedirectView` com URL fixa porque o ícone deixou de ser um arquivo
    fixo: ele vem do Admin. Sem nada cadastrado, cai no arquivo estático que já
    existia — o mesmo caminho de transição das logos.

    `permanent=False` de propósito: um 301 fica no cache do navegador e do
    proxy, e quem trocar o favicon amanhã ficaria vendo o antigo por meses.
    """
    from django.shortcuts import redirect
    from django.templatetags.static import static as static_url

    from apps.core.models import BrandAssets

    url = BrandAssets.url_for("favicon") or static_url("images/logo/favicon.svg")
    return redirect(url, permanent=False)
