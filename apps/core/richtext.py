"""HTML rico do Admin, limpo por lista de permissões.

O projeto tinha uma resposta para "texto formatado": `{% rich_text %}`, que
escapa tudo e reconhece uma marcação mínima (`# título`, `- item`). Ela serve
o corpo das páginas institucionais e continua onde está.

A descrição do material pede mais — subtítulos, negrito, listas, alinhamento,
tamanho e cor no mesmo campo — e isso é HTML de verdade. O comentário daquele
arquivo já dizia como fazer quando chegasse a hora: *"com uma biblioteca de
sanitização, não com `|safe`, que é o caminho curto para um XSS armazenado"*.

Aqui está a sanitização, sem biblioteca nova, porque o que ela precisa fazer
cabe em uma lista de permissões e um `HTMLParser` da biblioteca padrão.

## A regra

**Nada do que entra é reaproveitado.** O texto não é filtrado por remoção — o
que sairia daqui seria "o que sobrou do ataque". Ele é **reconstruído**: o
parser lê marcas e texto, e a saída é montada do zero com o que está na lista.
Uma marca desconhecida some (o texto dentro dela fica), um atributo fora da
lista some, um valor de estilo que não casa com o padrão some. `<script>`,
`onerror=`, `javascript:` e `style` com `url()` não têm por onde passar,
porque não existe caminho que copie a entrada para a saída.

## Onde roda

Duas vezes, de propósito:

* ao **gravar** (no formulário do Admin), para o banco guardar já limpo;
* ao **renderizar** (`{% rich_html %}`), porque o que já está no banco foi
  gravado por uma versão anterior deste arquivo — e um dia pode ter vindo de
  uma importação, de um `loaddata` ou de um `shell`.

O custo é um parse por campo exibido; a página do produto mostra um punhado.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

#: As marcas que o editor produz e a loja sabe desenhar.
#:
#: Sem `div`: o editor emite `p`, e um `div` solto só existiria para carregar
#: estilo. Sem `img`, `iframe`, `table`: descrição de material é texto.
ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s",
    "h2", "h3", "h4",
    "ul", "ol", "li",
    "blockquote", "span", "a",
}

#: Marcas antigas que os navegadores ainda emitem, traduzidas para a semântica.
TAG_ALIASES = {"b": "strong", "i": "em", "strike": "s", "del": "s", "ins": "u"}

#: Marcas sem fechamento.
VOID_TAGS = {"br"}

#: `style` é aceito onde o editor o aplica; `href` só no link.
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "p": {"style"},
    "h2": {"style"},
    "h3": {"style"},
    "h4": {"style"},
    "li": {"style"},
    "ul": {"style"},
    "ol": {"style"},
    "span": {"style"},
    "blockquote": {"style"},
}

#: Cada propriedade de estilo com o formato exato que ela aceita. O que não
#: casar não entra — inclusive `url(...)`, `expression(...)` e `!important`.
ALLOWED_STYLES = {
    "text-align": re.compile(r"^(left|right|center|justify)$"),
    "color": re.compile(r"^(#[0-9a-f]{3}|#[0-9a-f]{6}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\))$"),
    "background-color": re.compile(r"^(#[0-9a-f]{3}|#[0-9a-f]{6}|rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|transparent)$"),
    "font-size": re.compile(r"^\d{1,3}(\.\d+)?(px|rem|em|%)$"),
    "font-weight": re.compile(r"^(normal|bold|[1-9]00)$"),
    "text-decoration": re.compile(r"^(none|underline|line-through)$"),
    "font-style": re.compile(r"^(normal|italic)$"),
}

#: Marcas cujo **conteúdo** também some, e não só a marca.
#:
#: Numa lista de permissões, `<script>alert(1)</script>` já não executa — a
#: marca não é reescrita. Mas o corpo dela é texto para o parser, e sairia
#: escapado na tela: o cliente leria «alert(1)» no meio da descrição. Estas
#: marcas não têm conteúdo legível, então ele é descartado junto.
SUPPRESSED_CONTENT_TAGS = {"script", "style", "title", "textarea", "noscript", "iframe", "template"}

#: Esquemas que um link pode usar. `javascript:` e `data:` ficam de fora, e é
#: por isso que a lista é de permissão: um esquema novo e estranho não passa
#: por esquecimento.
SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:", "/", "#")


def _clean_style(valor: str) -> str:
    """Só as declarações que casam com o padrão da sua propriedade."""
    limpas = []
    for declaracao in (valor or "").split(";"):
        if ":" not in declaracao:
            continue
        propriedade, _, conteudo = declaracao.partition(":")
        propriedade = propriedade.strip().lower()
        conteudo = conteudo.strip().lower()
        padrao = ALLOWED_STYLES.get(propriedade)
        if padrao and padrao.match(conteudo):
            limpas.append(f"{propriedade}: {conteudo}")
    return "; ".join(limpas)


def _clean_href(valor: str) -> str:
    """Endereço de link, se o esquema for um dos permitidos."""
    endereco = (valor or "").strip()
    # Espaços e quebras no meio do esquema («java\nscript:») são o truque de
    # sempre; o navegador os ignora ao resolver a URL, então some com eles
    # antes de comparar.
    comparavel = re.sub(r"\s+", "", endereco).lower()
    if comparavel.startswith(SAFE_SCHEMES):
        return endereco
    return ""


class _Limpador(HTMLParser):
    """Lê a entrada e escreve a saída permitida. Nunca copia, sempre reescreve."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.abertas: list[str] = []
        self.silencio = 0

    # -- marcas ------------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        nome = TAG_ALIASES.get(tag.lower(), tag.lower())
        if nome in SUPPRESSED_CONTENT_TAGS:
            self.silencio += 1
            return
        if self.silencio:
            return
        if nome not in ALLOWED_TAGS:
            return  # a marca some; o texto dentro dela continua

        permitidos = ALLOWED_ATTRS.get(nome, set())
        escritos = []
        for atributo, valor in attrs:
            atributo = (atributo or "").lower()
            if atributo not in permitidos:
                continue
            if atributo == "style":
                valor = _clean_style(valor or "")
            elif atributo == "href":
                valor = _clean_href(valor or "")
            elif atributo == "title":
                valor = (valor or "").strip()
            if valor:
                escritos.append(f'{atributo}="{escape(valor, quote=True)}"')

        if nome == "a" and not any(a.startswith("href=") for a in escritos):
            return  # link sem destino seguro: vira texto

        if nome == "a":
            # Link do Admin abre fora e não empresta a aba de volta.
            escritos.append('rel="noopener noreferrer"')

        atributos = (" " + " ".join(escritos)) if escritos else ""
        if nome in VOID_TAGS:
            self.partes.append(f"<{nome}{atributos}>")
            return

        self.partes.append(f"<{nome}{atributos}>")
        self.abertas.append(nome)

    def handle_startendtag(self, tag, attrs):
        nome = TAG_ALIASES.get(tag.lower(), tag.lower())
        if self.silencio:
            return
        if nome in VOID_TAGS:
            self.partes.append(f"<{nome}>")

    def handle_endtag(self, tag):
        nome = TAG_ALIASES.get(tag.lower(), tag.lower())
        if nome in SUPPRESSED_CONTENT_TAGS:
            self.silencio = max(0, self.silencio - 1)
            return
        if self.silencio:
            return
        if nome not in ALLOWED_TAGS or nome in VOID_TAGS:
            return
        if nome not in self.abertas:
            return  # fechamento sem abertura: ignorado
        # Fecha o que ficou aberto por dentro, para a saída sair equilibrada.
        while self.abertas:
            atual = self.abertas.pop()
            self.partes.append(f"</{atual}>")
            if atual == nome:
                break

    # -- conteúdo ----------------------------------------------------------

    def handle_data(self, data):
        if data and not self.silencio:
            self.partes.append(escape(data, quote=False))

    def handle_comment(self, data):
        return  # comentário não é conteúdo

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    # -- resultado ---------------------------------------------------------

    def resultado(self) -> str:
        while self.abertas:
            self.partes.append(f"</{self.abertas.pop()}>")
        return "".join(self.partes)


#: Um documento vazio de editor: o `contenteditable` deixa isto quando o
#: usuário apaga tudo, e gravar «<p><br></p>» faria a loja desenhar um bloco
#: em branco.
_VAZIO = re.compile(r"^(?:\s|&nbsp;|<p>|</p>|<br\s*/?>|<br>)*$", re.I)


def sanitize_rich_text(valor: str) -> str:
    """Devolve o HTML permitido de ``valor`` — string vazia quando não sobra nada."""
    texto = (valor or "").strip()
    if not texto:
        return ""

    limpador = _Limpador()
    limpador.feed(texto)
    limpador.close()
    limpo = limpador.resultado().strip()

    if _VAZIO.match(limpo):
        return ""
    return limpo


def rich_text_is_empty(valor: str) -> bool:
    """O campo tem conteúdo depois de limpo? Usado para não desenhar seção vazia."""
    return not sanitize_rich_text(valor)
