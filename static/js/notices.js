/* O X dos avisos da loja.

   Fechar some com o aviso na hora e grava no cookie `jd_avisos_fechados` o
   par `id-versão` dele. Quem decide o que aparece é o servidor
   (`apps/storefront/notices.py`), que lê esse cookie: a página seguinte já
   sai sem o aviso, sem piscar. A versão é uma impressão do conteúdo — se o
   Admin reescreve o aviso, a versão muda e ele volta a aparecer; fechar um
   aviso nunca esconde outro.

   O cookie guarda só números e letras de 0 a f: nada pessoal, nada que o
   servidor execute. No máximo 30 pares, os mais recentes; um aviso fechado
   de novo troca a versão antiga pela nova, em vez de acumular. */
(function () {
  "use strict";

  if (window.jdNotices) {
    return;
  }
  window.jdNotices = true;

  var COOKIE = "jd_avisos_fechados";
  var MAXIMO = 30;
  var PAR = /^\d{1,9}-[0-9a-f]{10}$/;

  function lidos() {
    var achado = document.cookie.split("; ").filter(function (parte) {
      return parte.indexOf(COOKIE + "=") === 0;
    })[0];
    if (!achado) {
      return [];
    }
    return achado.slice(COOKIE.length + 1).split(".").filter(function (par) {
      return PAR.test(par);
    });
  }

  function gravar(pares) {
    var seguro = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = COOKIE + "=" + pares.join(".") +
      "; path=/; max-age=15552000; SameSite=Lax" + seguro;
  }

  document.addEventListener("click", function (evento) {
    var botao = evento.target.closest("[data-notice-close]");
    if (!botao) {
      return;
    }
    var aviso = botao.closest("[data-notice]");
    if (!aviso) {
      return;
    }
    var id = aviso.getAttribute("data-notice");
    var versao = aviso.getAttribute("data-notice-version");
    var par = id + "-" + versao;
    if (PAR.test(par)) {
      var pares = lidos().filter(function (item) {
        return item.split("-")[0] !== id;
      });
      pares.push(par);
      gravar(pares.slice(-MAXIMO));
    }

    /* O foco não pode cair no vazio: vai para o X do próximo aviso do mesmo
       grupo (no canto, o card que aparece agora), ou para o conteúdo. */
    var grupo = aviso.parentElement;
    aviso.remove();
    var proximo = grupo && grupo.querySelector("[data-notice-close]");
    if (proximo) {
      proximo.focus();
    } else {
      if (grupo && !grupo.querySelector("[data-notice]")) {
        grupo.remove();
      }
      var conteudo = document.getElementById("conteudo") || document.querySelector("main");
      if (conteudo) {
        if (!conteudo.hasAttribute("tabindex")) {
          conteudo.setAttribute("tabindex", "-1");
        }
        conteudo.focus({ preventScroll: true });
      }
    }
  });
})();
