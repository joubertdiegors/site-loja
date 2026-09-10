/* A ficha do produto — o que é só apresentação e vale para a página inteira.

   * Navegação rápida: clicar em «4 · Cores» rola até a seção e a abre se
     estiver recolhida (um `<details>` fechado esconderia o destino).
   * PERSONALIZAÇÃO: o limite de caracteres só aparece quando o tipo aceita
     texto. Nada é apagado ao trocar — só some da vista.
   * PALETA DE CORES: a bolinha da cor ao lado do <select>, a partir do
     `data-hex` que cada opção traz (`ColorSelect`, no admin). Vale também para
     as linhas que o Django acrescenta ao clicar em «Adicionar».

   Sem JavaScript nada disto é necessário: a página continua completa. */
(function () {
  "use strict";

  /* ---- navegação rápida ------------------------------------------------- */

  function abrirSecao(hash) {
    if (!hash || hash.charAt(0) !== "#") {
      return;
    }
    var alvo = document.getElementById(hash.slice(1));
    if (!alvo) {
      return;
    }
    var detalhes = alvo.querySelector("details");
    if (detalhes && !detalhes.open) {
      detalhes.open = true;
    }
  }

  function ligarNavegacao() {
    var nav = document.querySelector("[data-section-nav]");
    if (!nav) {
      return;
    }
    nav.querySelectorAll("a[href^='#']").forEach(function (link) {
      link.addEventListener("click", function () {
        abrirSecao(link.getAttribute("href"));
      });
    });
    if (window.location.hash) {
      abrirSecao(window.location.hash);
    }
  }

  /* ---- personalização ---------------------------------------------------- */

  function ligarPersonalizacao() {
    var tipo = document.getElementById("id_personalization_type");
    if (!tipo || typeof window.jdShowField !== "function") {
      return;
    }
    function aplicar() {
      var aceitaTexto = tipo.value === "text" || tipo.value === "photo_or_text";
      window.jdShowField("personalization_text_limit", aceitaTexto);
    }
    tipo.addEventListener("change", aplicar);
    aplicar();
  }

  /* ---- a bolinha da cor na paleta ---------------------------------------- */

  function pintar(select) {
    var bolinha = select.parentNode.querySelector(".jd-swatch");
    if (!bolinha) {
      bolinha = document.createElement("span");
      bolinha.className = "jd-swatch";
      bolinha.setAttribute("aria-hidden", "true");
      select.parentNode.insertBefore(bolinha, select);
    }
    var opcao = select.options[select.selectedIndex];
    /* `data-swatch` traz o fundo inteiro (a cor composta é um degradê com
       todas as componentes); `data-hex` continua existindo para a simples. */
    var fundo = opcao ? opcao.getAttribute("data-swatch") || opcao.getAttribute("data-hex") : "";
    bolinha.style.background = fundo || "transparent";
  }

  function ligarPaleta() {
    var paleta = document.getElementById("product_colors-group");
    if (!paleta) {
      return;
    }
    function todos() {
      paleta.querySelectorAll('select[name$="-color"]').forEach(function (select) {
        if (select.name.indexOf("__prefix__") !== -1) {
          return; /* o molde do «Adicionar» não é uma linha */
        }
        pintar(select);
        if (!select.hasAttribute("data-swatch")) {
          select.setAttribute("data-swatch", "1");
          select.addEventListener("change", function () {
            pintar(select);
          });
        }
      });
    }
    todos();
    document.addEventListener("formset:added", todos);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!document.querySelector("[data-product-sheet]")) {
      return;
    }
    ligarNavegacao();
    ligarPersonalizacao();
    ligarPaleta();
  });
})();
