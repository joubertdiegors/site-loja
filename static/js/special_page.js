/* A contagem regressiva da página de lançamento.

   O instante vem do cadastro, em `data-launch-at` (ISO 8601 com o fuso que
   o Admin escolheu) — nada de data escrita aqui. O navegador conta no relógio
   dele; ao chegar a zero, os mostradores ficam em 00, o elemento
   `[data-countdown-done]` aparece e a contagem para. Se a data já passou
   quando a página abre, o servidor já a entrega nesse estado. */
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

  var cells = {
    d: root.querySelector("[data-count-d]"),
    h: root.querySelector("[data-count-h]"),
    m: root.querySelector("[data-count-m]"),
    s: root.querySelector("[data-count-s]")
  };
  var done = document.querySelector("[data-countdown-done]");
  var timer = null;

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function tick() {
    var left = Math.max(0, (launchAt - Date.now()) / 1000);
    if (cells.d) { cells.d.textContent = pad(Math.floor(left / 86400)); }
    if (cells.h) { cells.h.textContent = pad(Math.floor((left % 86400) / 3600)); }
    if (cells.m) { cells.m.textContent = pad(Math.floor((left % 3600) / 60)); }
    if (cells.s) { cells.s.textContent = pad(Math.floor(left % 60)); }
    if (left <= 0) {
      root.setAttribute("data-done", "");
      if (done) {
        done.hidden = false;
      }
      if (timer) {
        window.clearInterval(timer);
      }
    }
  }

  tick();
  timer = window.setInterval(tick, 1000);
})();
