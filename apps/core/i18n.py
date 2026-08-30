"""Idiomas: ponte com o conteúdo traduzido e tradução de URLs.

Duas responsabilidades:

* ``normalize_language`` / ``get_content_language`` — do idioma da interface
  (``pt-br``, ``fr-be``) para o idioma do conteúdo cadastrado (``pt``, ``fr``);
* ``translate_path`` — a mesma URL em outro idioma, usada na troca de idioma.
"""

from urllib.parse import unquote, urlsplit, urlunsplit

from django.conf import settings
from django.urls import Resolver404, NoReverseMatch, resolve, reverse
from django.utils import translation
from django.utils.translation import get_language
from django.utils.translation.trans_real import get_language_from_path

from apps.core.constants import DEFAULT_LANGUAGE, Language

SUPPORTED_CONTENT_LANGUAGES = {choice.value for choice in Language}


def normalize_language(language: str | None) -> str:
    """Converte um código do Django (``pt-br``, ``fr-be``) em idioma de conteúdo.

    Retorna sempre um idioma suportado; cai no idioma padrão quando não houver
    correspondência.
    """
    if not language:
        return DEFAULT_LANGUAGE.value

    code = language.strip().lower().replace("_", "-")
    if code in SUPPORTED_CONTENT_LANGUAGES:
        return code

    base = code.split("-", 1)[0]
    if base in SUPPORTED_CONTENT_LANGUAGES:
        return base

    return DEFAULT_LANGUAGE.value


def get_content_language() -> str:
    """Idioma de conteúdo correspondente ao idioma ativo da requisição."""
    return normalize_language(get_language())


# ---------------------------------------------------------------------------
# Tradução de URL
# ---------------------------------------------------------------------------


def _interface_language_codes() -> set[str]:
    return {code for code, _ in settings.LANGUAGES}


def _strip_language_prefix(path: str) -> str:
    """Remove o prefixo de idioma do caminho, se houver (``/fr/x/`` -> ``/x/``)."""
    parts = path.lstrip("/").split("/", 1)
    if parts and parts[0] in _interface_language_codes():
        remainder = parts[1] if len(parts) > 1 else ""
        return "/" + remainder
    return path


def _add_language_prefix(path: str, language: str) -> str:
    """Acrescenta o prefixo do idioma, respeitando ``prefix_default_language``."""
    if language == settings.LANGUAGE_CODE:
        return path
    return "/" + language + path


def translate_path(url: str, target_language: str) -> str:
    """Devolve ``url`` apontando para a mesma página, no idioma pedido.

    Por que não usar ``django.urls.translate_url``: ela executa ``resolve()``
    com o idioma ATIVO da requisição. Na troca de idioma, a requisição vai para
    ``/i18n/setlang/`` — fora do ``i18n_patterns`` —, então o idioma ativo é o
    antigo (ou o padrão), e um caminho como ``/en/`` simplesmente não resolve:
    a URL volta intacta e o prefixo antigo continua mandando na página.

    Aqui a resolução é feita sob o idioma da PRÓPRIA URL de origem e a
    remontagem sob o idioma de destino. Se a rota não resolver (página fora do
    ``i18n_patterns``, 404, arquivo estático), cai na troca direta de prefixo.
    A query string e o fragmento são preservados.
    """
    parts = urlsplit(url)
    path = parts.path or "/"

    source_language = get_language_from_path(path) or settings.LANGUAGE_CODE
    translated = None

    with translation.override(source_language):
        try:
            match = resolve(unquote(path))
        except Resolver404:
            match = None

    if match is not None:
        route = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
        if route:
            with translation.override(target_language):
                try:
                    translated = reverse(route, args=match.args, kwargs=match.kwargs)
                except NoReverseMatch:
                    translated = None

    if translated is None:
        translated = _add_language_prefix(_strip_language_prefix(path), target_language)

    # Sempre um caminho absoluto DESTE site. Sem esta linha, um caminho como
    # `/de//evil.com` (o `de` é engolido como prefixo de idioma e sobram duas
    # barras) sairia daqui como `////evil.com` — que o navegador resolve como
    # `https://evil.com/`, porque referência começando com `//` é
    # protocol-relative. Era um open redirect anônimo, de um GET só, usando o
    # domínio da loja para phishing.
    #
    # O corte fica aqui, na raiz, e não em cada chamador: quem chama são o
    # `PreferredLanguageRedirectMiddleware` e a view `set_language`, e os dois
    # precisam da mesma garantia. É a mesma regra que o Django aplica em
    # `url_has_allowed_host_and_scheme`, que recusa `//`, `///` e `////`.
    translated = "/" + (translated or "").lstrip("/")

    return urlunsplit(("", "", translated, parts.query, parts.fragment))
