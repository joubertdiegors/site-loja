/* A contagem regressiva da página de lançamento.

   O instante vem do cadastro, em `data-launch-at` (ISO 8601 com o fuso que
   o Admin escolheu) — nada de data escrita aqui.

   A conta é feita no relógio do SERVIDOR, não no do visitante: a página traz
   `data-server-now` (o instante em que foi gerada), e a diferença para o
   relógio do navegador vira um ajuste. Um computador adiantado uma hora não
   vê a contagem zerar antes da hora — nem é mandado para uma loja que o
   servidor ainda não abriu. A latência da rede só atrasa o ajuste, nunca o
   adianta: o script zera, no máximo, um instante depois do servidor.

   Ao chegar a zero, os mostradores ficam em 00, `data-done` entra no
   contêiner (o CSS troca as caixas pelo texto final, na mesma célula) e, logo
   depois, a página vai para `data-home-url` — a Home, no idioma da página.
   Esse é só o lado visual: quem garante a loja aberta é o servidor, que
   depois da hora deixa de entregar esta página em qualquer endereço. Na
   pré-visualização do Admin não há `data-home-url`, e o estado final fica. */
(function () {
  "use strict";

  var root = document.querySelector("[data-countdown]");
  if (!root) {
    return;
  }
  var launchAt = Date.parse(root.getAttribute("data-launch-at") || "");
  if (isNaN(launchAt)) {
    return;
  }
  var serverNow = Date.parse(root.getAttribute("data-server-now") || "");
  var offset = isNaN(serverNow) ? 0 : serverNow - Date.now();
  var homeUrl = root.getAttribute("data-home-url") || "";

  /* O tempo que o texto final fica na tela antes de a Home abrir. */
  var PAUSA_MS = 1200;

  var cells = {
    d: root.querySelector("[data-count-d]"),
    h: root.querySelector("[data-count-h]"),
    m: root.querySelector("[data-count-m]"),
    s: root.querySelector("[data-count-s]")
  };
  var timer = null;

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function tick() {
    var left = Math.max(0, (launchAt - (Date.now() + offset)) / 1000);
    if (cells.d) { cells.d.textContent = pad(Math.floor(left / 86400)); }
    if (cells.h) { cells.h.textContent = pad(Math.floor((left % 86400) / 3600)); }
    if (cells.m) { cells.m.textContent = pad(Math.floor((left % 3600) / 60)); }
    if (cells.s) { cells.s.textContent = pad(Math.floor(left % 60)); }
    if (left <= 0) {
      root.setAttribute("data-done", "");
      if (timer) {
        window.clearInterval(timer);
        timer = null;
      }
      if (homeUrl) {
        /* `replace`: o «voltar» do navegador não retorna à contagem. */
        window.setTimeout(function () {
          window.location.replace(homeUrl);
        }, PAUSA_MS);
      }
      return true;
    }
    return false;
  }

  if (!tick()) {
    timer = window.setInterval(tick, 1000);
  }
})();
