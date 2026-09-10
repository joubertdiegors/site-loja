/* JD PRINT — o shell do Admin: acordeão do menu, posição do menu entre
   páginas e a gaveta do celular.

   Complementa o `nav_sidebar.js` do Django, que continua cuidando do
   recolher/expandir no desktop (`#toggle-nav-sidebar`, `localStorage`) e do
   filtro (`#nav-filter`, `sessionStorage`). Este arquivo não mexe nesses dois:
   só lê o resultado deles na tela.

   ## O acordeão

   Cada seção do menu (`.module`, de `app_list.html`) tem um botão no título
   (`.jd-sec-btn`, `aria-expanded`) que esconde ou mostra as linhas. A seção
   da página aberta fica SEMPRE aberta; as outras ficam como a pessoa as
   deixou nesta sessão do navegador (`sessionStorage`, chave `jd.admin.menu`).
   Nada vai para o `localStorage`: fechar o navegador zera o estado, e o
   próximo uso começa de novo com a seção certa aberta.

   Enquanto o filtro tem texto, todas as seções se abrem e as que ficam sem
   resultado somem — senão o filtro acharia um item dentro de uma seção
   fechada e a pessoa não o veria.

   ## A posição

   Ao clicar num item, guarda-se o `href` e o `scrollTop` da barra. Na página
   seguinte, se o item ativo é o que foi clicado (ou se é um reload da mesma
   página), a barra volta ao mesmo `scrollTop`; depois, em qualquer caso, o
   item ativo é trazido para dentro da área visível da barra se não estiver.
   O que manda é o item ativo visível — o `scrollTop` só preserva o contexto
   quando as alturas não mudaram. O foco do teclado não é movido: ele começa
   no início do documento, como em toda página.

   ## O celular

   Abaixo de 768 px a barra vira uma gaveta (`body.jd-menu-open`), aberta pelo
   botão do topo (`#jd-menu-open`), fechada pelo «×», pelo véu ou por Escape.
   Ao navegar a página recarrega, e a gaveta nasce fechada — com a seção da
   página aberta e o item ativo visível, pelas regras acima. */
(function () {
  "use strict";

  var KEY = "jd.admin.menu";
  var nav = document.getElementById("nav-sidebar");
  if (!nav) {
    return;
  }
  var body = document.body;
  var celular = window.matchMedia("(max-width: 767px)");

  function ler() {
    try {
      return JSON.parse(window.sessionStorage.getItem(KEY) || "{}") || {};
    } catch (erro) {
      return {};
    }
  }
  function gravar(estado) {
    try {
      window.sessionStorage.setItem(KEY, JSON.stringify(estado));
    } catch (erro) {
      /* modo privado, cota cheia: o menu continua funcionando sem memória */
    }
  }

  var estado = ler();
  estado.open = estado.open || {};

  /* ---- acordeão ---------------------------------------------------------- */

  var secoes = [].slice.call(nav.querySelectorAll(".module"));
  var linkAtivo = nav.querySelector(".current-model th a, th a[aria-current='page']");
  var secaoAtiva = linkAtivo ? linkAtivo.closest(".module") : null;

  function idDaSecao(secao) {
    var botao = secao.querySelector(".jd-sec-btn");
    return botao ? botao.getAttribute("aria-controls") : null;
  }

  function abrir(secao, aberta, lembrar) {
    var botao = secao.querySelector(".jd-sec-btn");
    if (!botao) {
      return;
    }
    secao.classList.toggle("jd-collapsed", !aberta);
    botao.setAttribute("aria-expanded", aberta ? "true" : "false");
    if (lembrar) {
      estado.open[idDaSecao(secao)] = aberta;
      gravar(estado);
    }
  }

  secoes.forEach(function (secao) {
    var id = idDaSecao(secao);
    if (!id) {
      return; /* caixa sem botão (um app fora das seções): sempre aberta */
    }
    var aberta = secao === secaoAtiva ? true : !!estado.open[id];
    if (secao === secaoAtiva) {
      estado.open[id] = true; /* a seção em uso fica aberta daqui em diante */
    }
    abrir(secao, aberta, false);
    secao.querySelector(".jd-sec-btn").addEventListener("click", function () {
      if (nav.classList.contains("jd-filtering")) {
        return; /* filtrando, tudo fica aberto */
      }
      abrir(secao, secao.classList.contains("jd-collapsed"), true);
    });
  });
  gravar(estado);
  nav.classList.add("jd-ready");

  /* ---- filtro: tudo aberto enquanto se procura ------------------------------ */

  var filtro = document.getElementById("nav-filter");

  function aplicarFiltro() {
    var texto = filtro ? filtro.value.trim() : "";
    nav.classList.toggle("jd-filtering", !!texto);
    secoes.forEach(function (secao) {
      var visiveis = 0;
      var grupo = null;
      [].slice.call(secao.querySelectorAll("tbody tr")).forEach(function (linha) {
        if (linha.classList.contains("jd-grupo")) {
          grupo = linha;
          grupo.classList.toggle("jd-oculto", !!texto); /* volta a aparecer se algo abaixo casar */
          return;
        }
        var escondida = linha.style.display === "none";
        if (!escondida) {
          visiveis++;
          if (grupo) {
            grupo.classList.remove("jd-oculto");
          }
        }
      });
      secao.classList.toggle("jd-empty", !!texto && visiveis === 0);
    });
  }

  if (filtro) {
    /* O `nav_sidebar.js` escuta os mesmos eventos e roda antes (foi registrado
       antes); o `setTimeout` garante que a leitura veja o resultado dele. */
    ["input", "change", "keyup"].forEach(function (nome) {
      filtro.addEventListener(nome, function () {
        window.setTimeout(aplicarFiltro, 0);
      });
    });
    aplicarFiltro();
  }

  /* ---- posição da barra entre páginas ------------------------------------- */

  var caminho = window.location.pathname;

  function garantirVisivel() {
    if (!linkAtivo) {
      return;
    }
    var item = linkAtivo.getBoundingClientRect();
    var caixa = nav.getBoundingClientRect();
    var margem = 12;
    if (item.top >= caixa.top + margem && item.bottom <= caixa.bottom - margem) {
      return;
    }
    /* Rola só a barra, e não a página: o centro da barra recebe o item. */
    nav.scrollTop += (item.top - caixa.top) - (caixa.height / 2 - item.height / 2);
  }

  var memoria = estado.nav || {};
  var veioDoClique = !!(linkAtivo && memoria.href && linkAtivo.getAttribute("href") === memoria.href);
  var recarregou = memoria.path === caminho;
  if (typeof memoria.top === "number" && (veioDoClique || recarregou)) {
    nav.scrollTop = memoria.top;
  }
  garantirVisivel();

  nav.addEventListener(
    "click",
    function (evento) {
      var link = evento.target.closest("a[href]");
      if (!link) {
        return;
      }
      estado.nav = { href: link.getAttribute("href"), top: nav.scrollTop, path: caminho };
      gravar(estado);
    },
    true
  );

  window.addEventListener("pagehide", function () {
    var atual = estado.nav && estado.nav.path === caminho ? estado.nav : { href: "" };
    estado.nav = { href: atual.href, top: nav.scrollTop, path: caminho };
    gravar(estado);
  });

  /* ---- a gaveta do celular -------------------------------------------------- */

  var abrirBotao = document.getElementById("jd-menu-open");
  var fecharBotao = nav.querySelector(".jd-sb-close");
  var veu = document.querySelector("[data-jd-sb-overlay]");

  function gaveta(aberta) {
    body.classList.toggle("jd-menu-open", aberta);
    if (abrirBotao) {
      abrirBotao.setAttribute("aria-expanded", aberta ? "true" : "false");
    }
    if (aberta) {
      nav.setAttribute("tabindex", "-1");
      nav.focus({ preventScroll: true });
      garantirVisivel();
    } else if (abrirBotao && nav.contains(document.activeElement)) {
      abrirBotao.focus();
    }
  }

  if (abrirBotao) {
    abrirBotao.addEventListener("click", function () {
      gaveta(!body.classList.contains("jd-menu-open"));
    });
  }
  if (fecharBotao) {
    fecharBotao.addEventListener("click", function () {
      gaveta(false);
    });
  }
  if (veu) {
    veu.addEventListener("click", function () {
      gaveta(false);
    });
  }
  document.addEventListener("keydown", function (evento) {
    if (evento.key === "Escape" && body.classList.contains("jd-menu-open")) {
      gaveta(false);
    }
  });
  if (celular.addEventListener) {
    celular.addEventListener("change", function (evento) {
      if (!evento.matches) {
        gaveta(false); /* virou a tela: a gaveta não faz sentido no desktop */
      }
    });
  }
})();
