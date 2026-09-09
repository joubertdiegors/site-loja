/* O editor de texto rico do Admin (etapa 4C.3).

   Transforma um `<textarea>` marcado com `data-rich-text` numa área editável
   com barra de ferramentas. O `textarea` continua sendo quem envia o valor:
   toda edição é copiada para ele, e é dele que o servidor lê. Sem JavaScript,
   o `textarea` aparece normalmente e o HTML é digitado à mão — o campo nunca
   depende deste arquivo para funcionar.

   ## O que ele produz

   Só o que `apps/core/richtext.py` aceita. Negrito, itálico, sublinhado,
   listas, subtítulos e alinhamento saem de `document.execCommand` com
   `styleWithCSS` ligado, que emite `style="text-align: …"` em vez das marcas
   antigas. Tamanho e cor **não** usam `execCommand`: ele produziria
   `<font size=7>` ou `x-large`, que a lista de permissões recusa. Eles
   envolvem a seleção num `<span>` com o estilo exato, pela API de seleção do
   próprio navegador.

   O servidor limpa de novo ao gravar. O que este arquivo faz é não sujar.

   ## Linhas novas do inline

   O Admin clona o formulário em branco quando alguém clica em "Adicionar
   outro"; o clone chega sem os ouvintes. O evento `formset:added` do Django
   avisa, e o editor é montado na linha nova. */
(function () {
  "use strict";

  var PRONTO = "data-rt-ready";

  function html(area) {
    return area.innerHTML.trim();
  }

  function montar(caixa) {
    if (caixa.getAttribute(PRONTO)) {
      return;
    }
    var fonte = caixa.querySelector("[data-rich-text]");
    var area = caixa.querySelector("[data-rt-area]");
    var barra = caixa.querySelector(".jd-rt-bar");
    if (!fonte || !area || !barra) {
      return;
    }
    caixa.setAttribute(PRONTO, "1");

    /* O conteúdo já gravado entra na área; o `textarea` sai de cena. */
    area.innerHTML = fonte.value || "";
    area.hidden = false;
    barra.hidden = false;
    fonte.classList.add("jd-rt-hidden");

    try {
      document.execCommand("styleWithCSS", false, true);
    } catch (erro) {
      /* Navegador antigo: o alinhamento sai como atributo e a limpeza do
         servidor o descarta. Formatar continua funcionando. */
    }

    function sincronizar() {
      fonte.value = html(area);
    }

    area.addEventListener("input", sincronizar);
    area.addEventListener("blur", sincronizar);

    /* Colar traz o HTML do Word, do site de origem, do que for. Aqui entra
       como texto: a formatação se aplica com os botões, e o servidor não
       precisa recusar meia página de `<span class=MsoNormal>`. */
    area.addEventListener("paste", function (evento) {
      evento.preventDefault();
      var texto = (evento.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, texto);
      sincronizar();
    });

    /* Um parágrafo por Enter, em vez de `<div>` ou `<br>` soltos. */
    area.addEventListener("keydown", function (evento) {
      if (evento.key === "Enter" && !evento.shiftKey) {
        var bloco = document.queryCommandValue("formatBlock");
        if (!bloco || bloco === "div") {
          document.execCommand("formatBlock", false, "p");
        }
      }
    });

    barra.querySelectorAll("[data-rt]").forEach(function (botao) {
      botao.addEventListener("mousedown", function (evento) {
        /* `mousedown` e não `click`: o clique tira o foco da área e leva a
           seleção junto, e aí o comando não teria sobre o que agir. */
        evento.preventDefault();
      });
      botao.addEventListener("click", function () {
        area.focus();
        var comando = botao.getAttribute("data-rt");
        var argumento = botao.getAttribute("data-rt-arg") || null;
        document.execCommand(comando, false, argumento);
        sincronizar();
      });
    });

    barra.querySelectorAll("[data-rt-style]").forEach(function (seletor) {
      seletor.addEventListener("change", function () {
        var propriedade = seletor.getAttribute("data-rt-style");
        var valor = seletor.value;
        seletor.selectedIndex = 0;
        if (!valor) {
          return;
        }
        area.focus();
        envolver(propriedade, valor);
        sincronizar();
      });
    });

    var codigo = barra.querySelector("[data-rt-source]");
    if (codigo) {
      codigo.addEventListener("click", function () {
        /* Ver e corrigir o HTML na mão. Voltar traz o que estiver no
           `textarea`, que é o que vai para o servidor de qualquer forma. */
        var mostrandoFonte = area.hidden;
        if (mostrandoFonte) {
          area.innerHTML = fonte.value || "";
          area.hidden = false;
          fonte.classList.add("jd-rt-hidden");
        } else {
          sincronizar();
          area.hidden = true;
          fonte.classList.remove("jd-rt-hidden");
        }
        codigo.classList.toggle("is-on", !mostrandoFonte);
      });
    }

    /* O formulário pode ser enviado com o foco ainda na área. */
    var formulario = caixa.closest("form");
    if (formulario) {
      formulario.addEventListener("submit", sincronizar);
    }
  }

  /** Envolve a seleção num `<span>` com um estilo da lista de permissões. */
  function envolver(propriedade, valor) {
    var selecao = window.getSelection();
    if (!selecao || !selecao.rangeCount || selecao.isCollapsed) {
      return;
    }
    var intervalo = selecao.getRangeAt(0);
    var span = document.createElement("span");
    span.style[propriedade] = valor;
    try {
      intervalo.surroundContents(span);
    } catch (erro) {
      /* A seleção atravessa o fim de um elemento (meio de um parágrafo até o
         meio do seguinte): `surroundContents` recusa, e aí o conteúdo é
         extraído e reinserido dentro do span. */
      span.appendChild(intervalo.extractContents());
      intervalo.insertNode(span);
    }
    selecao.removeAllRanges();
  }

  function montarTodos(raiz) {
    (raiz || document).querySelectorAll("[data-rich-text-widget]").forEach(montar);
  }

  document.addEventListener("DOMContentLoaded", function () {
    montarTodos(document);

    /* Linha nova do inline: o Django avisa, e o clone é montado. */
    document.addEventListener("formset:added", function (evento) {
      montarTodos(evento.target || document);
    });
  });
})();
