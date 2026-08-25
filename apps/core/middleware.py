"""Middlewares do projeto."""

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.translation import check_for_language
from django.utils.translation.trans_real import get_language_from_path

from apps.core.i18n import translate_path
from apps.core.languages import is_language_available

#: Rotas que não fazem parte da loja pública e nunca são redirecionadas.
EXCLUDED_PREFIXES = ("/admin/", "/i18n/", "/static/", "/media/")


class PreferredLanguageRedirectMiddleware:
    """Leva o visitante de volta ao idioma que ele escolheu.

    Com ``i18n_patterns(prefix_default_language=False)``, o Django trata as
    URLs sem prefixo como sendo sempre do idioma padrão e **ignora o cookie**
    nesse caso (ver ``LocaleMiddleware.process_request``). Isso é bom para SEO
    — ``/modelos/`` é a versão canônica em português — mas tem um efeito
    colateral ruim: quem escolheu francês e volta ao site digitando o endereço
    sem prefixo cai no português, apesar da escolha registrada.

    Este middleware fecha essa lacuna com um redirecionamento explícito, para
    a URL e o conteúdo nunca discordarem: cookie de idioma diferente do padrão
    + URL sem prefixo => manda para a versão prefixada.

    Só age em ``GET``/``HEAD`` (um ``POST`` redirecionado perderia os dados) e
    só nas rotas públicas.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect = self._redirect_for(request)
        return redirect if redirect is not None else self.get_response(request)

    def _redirect_for(self, request):
        if request.method not in ("GET", "HEAD"):
            return None

        path = request.path_info
        if path.startswith(EXCLUDED_PREFIXES):
            return None

        prefix_language = get_language_from_path(path)
        if prefix_language:
            # A URL diz o idioma. Se esse idioma não está mais disponível na
            # loja, devolve o visitante para a versão no idioma padrão em vez
            # de servir uma página que o administrador desligou.
            if not is_language_available(prefix_language):
                return HttpResponseRedirect(
                    translate_path(request.get_full_path(), settings.LANGUAGE_CODE)
                )
            return None

        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if not language or language == settings.LANGUAGE_CODE:
            return None
        if not check_for_language(language) or not is_language_available(language):
            return None

        current = request.get_full_path()
        target = translate_path(current, language)
        if target == current:
            return None

        return HttpResponseRedirect(target)
