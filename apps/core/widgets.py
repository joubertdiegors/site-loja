"""Widgets do Admin que o projeto compartilha.

Por enquanto um só: o editor de texto rico. Ele nasce aqui, e não dentro do
`catalog`, porque a descrição do material é o primeiro campo rico da loja —
não será o último, e um editor por app seria um editor por lugar para
corrigir.
"""

from django import forms
from django.utils.safestring import mark_safe

#: As cores oferecidas no editor. Uma lista fechada, e não um seletor livre:
#: a descrição do material aparece dentro da página do produto, e um vermelho
#: qualquer digitado à mão brigaria com a paleta da loja. São os tons que o
#: projeto já usa (`static/src/input.css`), mais o preto do texto comum.
RICH_TEXT_COLORS = (
    ("", "Cor do texto"),
    ("#1b1530", "Padrão"),
    ("#4a1a8c", "Roxo"),
    ("#9b4322", "Coral"),
    ("#2f6b41", "Verde"),
    ("#6e6880", "Cinza"),
)

#: Tamanhos em `rem`, para o texto acompanhar a página do produto em vez de
#: fixar pixels que ignoram a preferência de quem lê.
RICH_TEXT_SIZES = (
    ("", "Tamanho"),
    ("0.9rem", "Pequeno"),
    ("1rem", "Normal"),
    ("1.15rem", "Grande"),
    ("1.35rem", "Maior"),
)


class RichTextWidget(forms.Textarea):
    """Uma área editável com barra de ferramentas, gravando HTML no `textarea`.

    ## Por que não uma biblioteca

    O que este campo precisa fazer — subtítulo, negrito, lista, alinhamento,
    tamanho e cor — cabe em `document.execCommand` e na API de seleção do
    próprio navegador. Trazer um editor de terceiros custaria centenas de
    kilobytes no Admin, uma versão para acompanhar e, principalmente, uma
    segunda ideia de que HTML é válido: a lista de permissões de
    `apps.core.richtext` é a única, e o editor produz exatamente o que ela
    aceita.

    ## Sem JavaScript

    O `textarea` continua lá, visível e editável: quem estiver sem JavaScript
    escreve o HTML à mão, e ele passa pela mesma limpeza ao gravar. O editor é
    uma comodidade por cima, não a única porta.
    """

    template_name = "django/forms/widgets/textarea.html"

    class Media:
        js = ("admin/js/rich_text_admin.js",)
        css = {"all": ("admin/css/jdprint_richtext.css",)}

    def __init__(self, attrs=None):
        padrao = {
            "class": "jd-rt-source vLargeTextField",
            "rows": 8,
            "data-rich-text": "true",
        }
        padrao.update(attrs or {})
        super().__init__(padrao)

    def render(self, name, value, attrs=None, renderer=None):
        campo = super().render(name, value, attrs, renderer)
        cores = "".join(
            f'<option value="{valor}">{rotulo}</option>' for valor, rotulo in RICH_TEXT_COLORS
        )
        tamanhos = "".join(
            f'<option value="{valor}">{rotulo}</option>' for valor, rotulo in RICH_TEXT_SIZES
        )
        # A barra e a área editável nascem aqui, escondidas; o JavaScript as
        # mostra e esconde o `textarea`. Sem ele, fica só o `textarea`.
        return mark_safe(
            f'<div class="jd-rt" data-rich-text-widget>'
            f'  <div class="jd-rt-bar" hidden>'
            f'    <button type="button" class="jd-rt-btn" data-rt="formatBlock" data-rt-arg="h2" title="Subtítulo">T¹</button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="formatBlock" data-rt-arg="h3" title="Subtítulo menor">T²</button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="formatBlock" data-rt-arg="p" title="Parágrafo">¶</button>'
            f'    <span class="jd-rt-sep"></span>'
            f'    <button type="button" class="jd-rt-btn" data-rt="bold" title="Negrito"><b>N</b></button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="italic" title="Itálico"><i>I</i></button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="underline" title="Sublinhado"><u>S</u></button>'
            f'    <span class="jd-rt-sep"></span>'
            f'    <button type="button" class="jd-rt-btn" data-rt="insertUnorderedList" title="Lista">•—</button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="insertOrderedList" title="Lista numerada">1—</button>'
            f'    <span class="jd-rt-sep"></span>'
            f'    <button type="button" class="jd-rt-btn" data-rt="justifyLeft" title="Alinhar à esquerda">⯇</button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="justifyCenter" title="Centralizar">≡</button>'
            f'    <button type="button" class="jd-rt-btn" data-rt="justifyRight" title="Alinhar à direita">⯈</button>'
            f'    <span class="jd-rt-sep"></span>'
            f'    <select class="jd-rt-select" data-rt-style="fontSize" title="Tamanho do texto">{tamanhos}</select>'
            f'    <select class="jd-rt-select" data-rt-style="color" title="Cor do texto">{cores}</select>'
            f'    <span class="jd-rt-sep"></span>'
            f'    <button type="button" class="jd-rt-btn" data-rt="removeFormat" title="Limpar formatação">✕</button>'
            f'    <button type="button" class="jd-rt-btn jd-rt-code" data-rt-source title="Ver o HTML">&lt;/&gt;</button>'
            f'  </div>'
            f'  <div class="jd-rt-area" data-rt-area contenteditable="true" hidden></div>'
            f'  {campo}'
            f'</div>'
        )
