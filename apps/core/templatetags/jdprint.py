"""Tags de template da interface pública."""

import hashlib
import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

# ---------------------------------------------------------------------------
# Identidade visual
#
# As quatro imagens da marca vêm do Admin (`core.BrandAssets`), não de nomes de
# arquivo procurados em `static/images/logo/`. A diferença prática: trocar a
# logo deixou de exigir um commit e um deploy.
#
# Enquanto o dono da loja não abrir a tela e enviar as imagens, duas delas caem
# nos arquivos que já estavam no repositório — é uma ponte de transição, e ela
# tem prazo: assim que as imagens estiverem cadastradas, os arquivos de
# `static/images/logo/` podem sair. Sem essa ponte, esta etapa deixaria o site
# no ar sem logo até alguém fazer o upload.
# ---------------------------------------------------------------------------

#: Onde procurar cada imagem em `static/`, enquanto o Admin estiver vazio.
#: Só o que já existia no repositório: rodapé e imagem de produto nunca tiveram
#: arquivo, e por isso não têm ponte.
STATIC_FALLBACKS = {
    "header_logo": (
        "images/logo/jdprint-logo.svg",
        "images/logo/jdprint-logo.png",
        "images/logo/jdprint-logo.webp",
        "images/logo/logo.svg",
        "images/logo/logo.png",
    ),
    "favicon": (
        "images/logo/favicon.svg",
        "images/logo/favicon.png",
        "images/logo/favicon.ico",
    ),
}

_logo_cache: dict[str, str] = {}


def _brand_assets(context):
    """A linha da marca, lida **uma vez por requisição**.

    Guardada no `request` porque `product_placeholder_url` roda dentro do laço
    da grade: sem isto, uma listagem de quarenta produtos fazia quarenta
    consultas para ler a mesma linha.

    O cache morre com a requisição, de propósito. Um cache de módulo seria mais
    rápido e traria o problema descrito em `apps/core/languages.py`: o valor
    antigo sobrevive ao rollback do teste, e o resultado passa a depender da
    ordem em que a suíte roda.
    """
    from apps.core.models import BrandAssets

    request = context.get("request") if hasattr(context, "get") else None
    if request is None:
        return BrandAssets.current()

    if not hasattr(request, "_jd_brand"):
        request._jd_brand = BrandAssets.current()
    return request._jd_brand


def _brand_url(campo: str, context=None) -> str:
    """A imagem cadastrada; na falta dela, o arquivo estático de transição."""
    config = _brand_assets(context) if context is not None else None
    if config is None and context is None:
        from apps.core.models import BrandAssets

        config = BrandAssets.current()

    arquivo = getattr(config, campo, None) if config is not None else None
    if arquivo:
        return arquivo.url

    # O cache vale só para a ponte estática: o caminho do arquivo em `static/`
    # não muda enquanto o processo vive. O que vem do banco **não** é cacheado
    # — quem troca a logo no Admin espera vê-la na página seguinte.
    if not settings.DEBUG and campo in _logo_cache:
        return _logo_cache[campo]

    encontrado = ""
    for candidato in STATIC_FALLBACKS.get(campo, ()):
        if finders.find(candidato):
            encontrado = static(candidato)
            break

    _logo_cache[campo] = encontrado
    return encontrado


@register.simple_tag(takes_context=True)
def brand_header_logo_url(context) -> str:
    """A logo do cabeçalho. Vazia = o header usa a marca tipográfica."""
    return _brand_url("header_logo", context)


@register.simple_tag(takes_context=True)
def brand_footer_logo_url(context) -> str:
    """A logo do rodapé — normalmente a versão clara, sobre fundo escuro.

    Sem ponte estática de propósito: usar a logo do topo no rodapé escuro é
    justamente o que esta etapa veio permitir corrigir.
    """
    return _brand_url("footer_logo", context)


@register.simple_tag(takes_context=True)
def brand_favicon_url(context) -> str:
    """O ícone da aba. Vazio = nenhum `<link rel="icon">` é gerado."""
    return _brand_url("favicon", context)


@register.simple_tag(takes_context=True)
def product_placeholder_url(context) -> str:
    """A imagem padrão de produto. Vazia = o espaço reservado tipográfico."""
    return _brand_url("product_placeholder", context)


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
    "bg-mint-100 text-mint-700",
    "bg-yellow-100 text-yellow-700",
    "bg-coral-100 text-coral-700",
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


# ---------------------------------------------------------------------------
# Estáticos com versão na URL
#
# `{% static 'js/app.js' %}` devolve sempre a mesma URL. O servidor de
# estáticos (o do Django em desenvolvimento, o da hospedagem em produção)
# responde só com `Last-Modified`, sem `Cache-Control`, e aí o navegador aplica
# a regra heurística: um arquivo que não mudava há dias é tido como "fresco"
# por horas e sai do cache sem consulta ao servidor — inclusive num F5, que
# revalida a página, não os arquivos que ela puxa. Foi assim que o carrossel de
# banners chegou a um navegador com o HTML novo e o app.js antigo: setas na
# tela, clique sem efeito.
#
# Com `?v=<hash do conteúdo>` a URL muda quando o arquivo muda e o cache
# antigo nem é consultado. O hash é do conteúdo, não da data: um deploy que não
# altera o arquivo mantém a URL — e o cache — do visitante.

#: Hash por arquivo, reaproveitado enquanto o arquivo não muda em disco
#: (caminho, mtime e tamanho iguais). Um `stat` por arquivo por página.
_STATIC_VERSIONS: dict[tuple[str, int, int], str] = {}


def static_version(path: str) -> str:
    """Os dez primeiros dígitos do SHA-256 do arquivo; ``""`` se não existir."""
    encontrado = finders.find(path)
    if not encontrado:
        return ""
    try:
        estado = os.stat(encontrado)
    except OSError:
        return ""
    chave = (encontrado, estado.st_mtime_ns, estado.st_size)
    versao = _STATIC_VERSIONS.get(chave)
    if versao is None:
        with open(encontrado, "rb") as arquivo:
            versao = hashlib.sha256(arquivo.read()).hexdigest()[:10]
        # Versões antigas do mesmo arquivo não voltam: só a atual fica.
        for antiga in [k for k in _STATIC_VERSIONS if k[0] == encontrado]:
            del _STATIC_VERSIONS[antiga]
        _STATIC_VERSIONS[chave] = versao
    return versao


@register.simple_tag
def static_versioned(path: str) -> str:
    """``{% static_versioned 'js/app.js' %}`` → ``/static/js/app.js?v=3f9a1c…``.

    Para o CSS e o JavaScript do site. Sem o arquivo, cai na URL do
    ``{% static %}`` de sempre.
    """
    url = static(path)
    versao = static_version(path)
    return f"{url}?v={versao}" if versao else url
