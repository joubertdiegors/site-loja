/* A pré-visualização ao vivo do Admin (ver apps/core/admin_preview.py).

   Manda o formulário, como está, para a rota `live-preview/` do cadastro a
   cada alteração (com um pequeno atraso para não disparar a cada tecla) e
   coloca o HTML devolvido no iframe. Arquivos ficam de fora do envio: a
   imagem nova aparece depois de salvar, e o painel diz isso.

   Também organiza a tela: o painel vai para o topo da área principal, acima
   do formulário e na largura toda, e fica grudado no alto da janela enquanto
   a página rola (`position: sticky`). No topo da página o quadro abre no
   fluxo, empurrando o formulário; depois de rolar, abre como uma camada
   flutuante sob o cabeçalho, sem mexer no formulário nem na rolagem — um
   espaçador guarda a altura que o quadro tinha no fluxo para nada pular.
   "Recolher" deixa só o cabeçalho (e suspende as atualizações até expandir
   de novo); Desktop/Tablet/Celular mudam só a largura visual do quadro. Os
   dois estados ficam no localStorage do navegador — nada vai para o
   servidor. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var panel = document.querySelector("[data-live-preview]");
    var form = document.querySelector("#content-main form");
    if (!panel || !form) {
      return;
    }

    /* Preview em cima, formulário embaixo. A âncora marca a posição natural
       do painel (para saber quando ele está grudado); o espaçador guarda o
       lugar do quadro quando ele passa a flutuar. */
    var anchor = document.createElement("div");
    anchor.className = "jd-preview-anchor";
    var spacer = document.createElement("div");
    spacer.className = "jd-preview-spacer";
    form.parentNode.insertBefore(anchor, form);
    form.parentNode.insertBefore(panel, form);
    form.parentNode.insertBefore(spacer, form);
    panel.hidden = false;
    document.body.classList.add("jd-has-preview");

    var frame = panel.querySelector("iframe");
    var status = panel.querySelector(".jd-preview-status");
    var stage = panel.querySelector(".jd-preview-stage");
    var head = panel.querySelector(".jd-preview-head");
    var body = panel.querySelector(".jd-preview-body");
    var toggle = panel.querySelector(".jd-preview-toggle");
    var url = panel.getAttribute("data-url");
    var storageKey = panel.getAttribute("data-storage-key") || "jd-preview";
    var lang = (panel.querySelector(".jd-preview-lang.is-on") || {}).getAttribute
      ? panel.querySelector(".jd-preview-lang.is-on").getAttribute("data-lang")
      : "";
    var timer = null;
    var pending = null;
    var collapsed = false;
    var stuck = false;
    var dirty = true;
    var statusDefault = status ? status.textContent : "";

    function remember(name, value) {
      try { window.localStorage.setItem(storageKey + ":" + name, value); } catch (error) { /* modo privado etc. */ }
    }

    function recall(name) {
      try { return window.localStorage.getItem(storageKey + ":" + name); } catch (error) { return null; }
    }

    function csrfToken() {
      var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
      return input ? input.value : "";
    }

    function payload() {
      var data = new FormData(form);
      /* Arquivos não vão: seria um upload a cada tecla, e o registro não
         gravado não tem URL para a imagem de qualquer maneira. */
      var keys = [];
      data.forEach(function (value, key) {
        if (value instanceof File) {
          keys.push(key);
        }
      });
      keys.forEach(function (key) { data.delete(key); });
      return data;
    }

    function overlay() {
      return stuck && !collapsed;
    }

    function fit() {
      try {
        var doc = frame.contentDocument;
        if (!doc || !doc.documentElement) {
          return;
        }
        var height = Math.max(doc.documentElement.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
        /* Flutuando, o quadro tem de caber na janela junto com o cabeçalho. */
        var cap = overlay() ? window.innerHeight - head.offsetHeight - 110 : window.innerHeight - 120;
        frame.style.height = Math.max(200, Math.min(height + 2, cap)) + "px";
      } catch (error) {
        /* iframe de outra origem não acontece aqui; por via das dúvidas, fica a altura atual. */
      }
    }

    function refresh() {
      if (collapsed) {
        dirty = true; /* atualiza quando expandir */
        return;
      }
      dirty = false;
      if (pending && pending.abort) {
        pending.abort();
      }
      var controller = window.AbortController ? new AbortController() : null;
      pending = controller;
      if (status) { status.textContent = "Atualizando…"; }
      fetch(url + (url.indexOf("?") === -1 ? "?" : "&") + "lang=" + encodeURIComponent(lang), {
        method: "POST",
        body: payload(),
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" },
        signal: controller ? controller.signal : undefined
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("HTTP " + response.status);
          }
          return response.text();
        })
        .then(function (html) {
          frame.srcdoc = html;
          if (status) {
            var agora = new Date();
            status.textContent = "Atualizada às " + agora.toLocaleTimeString() + " · " + statusDefault;
          }
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") {
            return;
          }
          if (status) {
            status.textContent = "Não foi possível atualizar a pré-visualização (" + error.message + ").";
          }
        });
    }

    function schedule() {
      window.clearTimeout(timer);
      timer = window.setTimeout(refresh, 350);
    }

    function bodyHeight() {
      var rect = body.getBoundingClientRect();
      var margin = parseFloat(window.getComputedStyle(body).marginTop) || 0;
      return Math.round(rect.height + margin);
    }

    function applyMode() {
      panel.classList.toggle("is-stuck", stuck);
      panel.classList.toggle("is-overlay", overlay());
    }

    /* Grudado ou não: a âncora é a posição natural do painel. Ao passar a
       flutuar com o quadro aberto, o espaçador assume a altura que o quadro
       ocupava no fluxo, e nada abaixo se move. Ao voltar para o topo, o
       quadro volta ao fluxo e o espaçador some. */
    function onScroll() {
      var wasStuck = stuck;
      stuck = anchor.getBoundingClientRect().top < 0;
      if (stuck === wasStuck) {
        return;
      }
      if (stuck) {
        if (!collapsed) {
          spacer.style.height = bodyHeight() + "px";
        }
        applyMode();
      } else {
        applyMode();
        spacer.style.height = "0px";
      }
      fit();
    }

    function setCollapsed(value) {
      collapsed = Boolean(value);
      panel.classList.toggle("is-collapsed", collapsed);
      if (toggle) {
        toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        toggle.textContent = toggle.getAttribute(collapsed ? "data-label-expand" : "data-label-collapse");
      }
      applyMode();
      remember("collapsed", collapsed ? "1" : "0");
      if (!collapsed && dirty) {
        refresh();
      }
      fit();
    }

    function setSize(width) {
      panel.querySelectorAll(".jd-preview-size").forEach(function (b) {
        b.classList.toggle("is-on", b.getAttribute("data-width") === width);
      });
      stage.classList.toggle("is-narrow", Boolean(width));
      frame.style.width = width ? width + "px" : "";
      remember("width", width);
      window.setTimeout(fit, 50);
    }

    frame.addEventListener("load", function () {
      fit();
      /* Fontes e imagens chegam depois do load do documento. */
      window.setTimeout(fit, 300);
      window.setTimeout(fit, 1200);
    });
    window.addEventListener("resize", function () { onScroll(); fit(); });
    window.addEventListener("scroll", onScroll, { passive: true });

    form.addEventListener("input", schedule);
    form.addEventListener("change", schedule);
    /* Linhas de inline acrescentadas ou removidas pelo Django. */
    document.addEventListener("formset:added", schedule);
    document.addEventListener("formset:removed", schedule);

    panel.querySelectorAll(".jd-preview-lang").forEach(function (button) {
      button.addEventListener("click", function () {
        panel.querySelectorAll(".jd-preview-lang").forEach(function (b) { b.classList.remove("is-on"); });
        button.classList.add("is-on");
        lang = button.getAttribute("data-lang");
        refresh();
      });
    });

    panel.querySelectorAll(".jd-preview-size").forEach(function (button) {
      button.addEventListener("click", function () {
        setSize(button.getAttribute("data-width"));
      });
    });

    if (toggle) {
      toggle.addEventListener("click", function () {
        setCollapsed(!collapsed);
      });
    }

    /* Estado guardado: largura escolhida e recolhido/expandido. */
    var savedWidth = recall("width");
    if (savedWidth !== null && panel.querySelector('.jd-preview-size[data-width="' + savedWidth + '"]')) {
      setSize(savedWidth);
    }
    setCollapsed(recall("collapsed") === "1");
    onScroll();
  });
})();
