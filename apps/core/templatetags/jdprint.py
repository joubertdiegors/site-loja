"""Tags de template da interface pública."""

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

#: Nomes de arquivo aceitos para a logo, na ordem de preferência.
LOGO_CANDIDATES = (
    "images/logo/jdprint-logo.svg",
    "images/logo/jdprint-logo.png",
    "images/logo/jdprint-logo.webp",
    "images/logo/logo.svg",
    "images/logo/logo.png",
)

_logo_cache: dict[str, str] = {}


@register.simple_tag
def brand_logo_url() -> str:
    """URL da logo oficial, ou string vazia se o arquivo ainda não existir.

    Enquanto não existir, o header mostra a versão tipográfica (ver
    ``components/logo.html``). A logo NÃO é recriada em código.
    """
    if not settings.DEBUG and "url" in _logo_cache:
        return _logo_cache["url"]

    url = ""
    for candidate in LOGO_CANDIDATES:
        if finders.find(candidate):
            url = static(candidate)
            break

    _logo_cache["url"] = url
    return url


FAVICON_CANDIDATES = (
    "images/logo/favicon.svg",
    "images/logo/favicon.png",
    "images/logo/favicon.ico",
)


@register.simple_tag
def brand_favicon_url() -> str:
    """Favicon oficial, se existir. Sem arquivo, nenhum <link> é gerado."""
    if not settings.DEBUG and "favicon" in _logo_cache:
        return _logo_cache["favicon"]

    url = ""
    for candidate in FAVICON_CANDIDATES:
        if finders.find(candidate):
            url = static(candidate)
            break

    _logo_cache["favicon"] = url
    return url


@register.inclusion_tag("components/icon.html")
def icon(name: str, css_class: str = "h-5 w-5"):
    """Ícone SVG inline (sem biblioteca externa, sem requisição extra)."""
    return {"name": name, "css_class": css_class}


@register.filter
def has(container, value) -> bool:
    """`{{ favorite_ids|has:product.pk }}` — pertence ao conjunto?

    O `{% templatetag openblock %} if x in y {% templatetag closeblock %}` do
    Django faz o mesmo, mas só dentro de um `if`: não dá para guardar o
    resultado num `{% templatetag openblock %} with {% templatetag closeblock %}`.
    Sem isto, o botão de favorito precisaria repetir a mesma marcação nos dois
    ramos do `if` — dois lugares para corrigir cada vez que ele mudar.
    """
    try:
        return value in container
    except TypeError:
        return False


@register.filter
def initial(value: str) -> str:
    """Primeira letra visível de um texto, para os espaços reservados."""
    text = (value or "").strip()
    return text[0].upper() if text else "•"


#: Paletas usadas pelos blocos de categoria. As classes ficam escritas por
#: extenso porque o Tailwind lê este arquivo (`@source "../../apps"`) e só
#: encontra o que está literal.
CATEGORY_ACCENTS = (
    "bg-brand-100 text-brand-700",
    "bg-cyan-100 text-cyan-700",
    "bg-indigo-100 text-indigo-700",
    "bg-magenta-100 text-magenta-700",
    "bg-state-warning-soft text-state-warning-dark",
    "bg-state-success-soft text-state-success-dark",
)


@register.filter
def category_accent(slug: str) -> str:
    """Cor do bloco de uma categoria, derivada do slug.

    Category não tem campo de cor — e criar um só para pintar um card seria
    migration por decoração. Derivar do slug dá uma cor estável: ela não muda
    quando o administrador reordena as categorias, e muda se ele renomear.
    """
    if not slug:
        return CATEGORY_ACCENTS[0]
    total = sum(ord(char) for char in str(slug))
    return CATEGORY_ACCENTS[total % len(CATEGORY_ACCENTS)]


@register.simple_tag
def rich_text(value: str):
    """Texto do Admin virando HTML seguro.

    O corpo das páginas institucionais é escrito num `TextField`, e o lojista
    não deveria precisar saber HTML para separar um parágrafo. Uma marcação
    mínima resolve:

    ==========================  ==================================
    linha começando com ``# ``  subtítulo
    linha começando com ``- ``  item de lista
    linha em branco             separa parágrafos
    ==========================  ==================================

    **Tudo é escapado.** Nada do que for digitado vira marcação: um `<script>`
    colado no Admin aparece como texto na tela. Um editor de HTML rico entra
    quando fizer falta — e aí com uma biblioteca de sanitização, não com
    `|safe`, que é o caminho curto para um XSS armazenado.
    """
    from django.utils.html import conditional_escape, format_html_join
    from django.utils.safestring import mark_safe

    texto = (value or "").replace("\r\n", "\n").strip()
    if not texto:
        return ""

    partes = []
    lista_aberta = []

    def fechar_lista():
        if lista_aberta:
            itens = format_html_join("", "<li>{}</li>", ((item,) for item in lista_aberta))
            partes.append(f"<ul>{itens}</ul>")
            lista_aberta.clear()

    for bloco in texto.split("\n\n"):
        bloco = bloco.strip()
        if not bloco:
            continue
        for linha in bloco.split("\n"):
            limpa = linha.strip()
            if not limpa:
                continue
            if limpa.startswith("# "):
                fechar_lista()
                partes.append(f"<h2>{conditional_escape(limpa[2:].strip())}</h2>")
            elif limpa.startswith("- "):
                lista_aberta.append(limpa[2:].strip())
            else:
                fechar_lista()
                partes.append(f"<p>{conditional_escape(limpa)}</p>")
        fechar_lista()

    return mark_safe("".join(partes))
