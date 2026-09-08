/* A sugestão de SKU enquanto se digita — no cadastro rápido E no completo.

   Um script só para os dois formulários: o que muda é onde ficam o nome e a
   categoria, e isso vem de um marcador na página (`[data-sku-suggest]`):

     data-url    a rota `admin:catalog_product_sku_suggestion`
     data-mode   "quick" (campos `name`/`category` do formulário) ou
                 "full" (a categoria é `#id_category`; o nome é o da linha em
                 português do inline CONTEÚDO, `translations-N-name`)

   A regra é a do servidor (`apps/catalog/sku.py`): o JavaScript só pergunta e
   preenche. O campo SKU é preenchido enquanto ninguém digitou nele; a partir
   do primeiro toque o SKU é da pessoa e não é mais sobrescrito. Limpar o
   campo devolve o modo automático. Um SKU que já veio preenchido (duplicar)
   conta como manual.

   Só em produto NOVO: na ficha de um produto existente o marcador não é
   desenhado e nada acontece. Sem JavaScript, o servidor sugere ao gravar. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var marcador = document.querySelector("[data-sku-suggest]");
    if (!marcador) {
      return;
    }
    var url = marcador.getAttribute("data-url");
    var modo = marcador.getAttribute("data-mode") || "quick";
    var form = marcador.closest("form") || document.querySelector("#product_quick_form, #product_form");
    if (!url || !form) {
      return;
    }

    var sku = form.querySelector('[name="sku"]');
    if (!sku) {
      return;
    }
    var ajuda = form.querySelector("[data-sku-help]") ||
      (sku.closest(".form-row") && sku.closest(".form-row").querySelector(".help"));
    var previaVariante = form.querySelector("[data-variant-sku-preview]");

    /* De onde vêm nome e categoria, em cada formulário. */
    function categoria() {
      var campo = modo === "full" ? form.querySelector("#id_category") : form.querySelector('[name="category"]');
      return campo ? campo.value : "";
    }

    function nome() {
      if (modo !== "full") {
        var campo = form.querySelector('[name="name"]');
        return campo ? campo.value : "";
      }
      /* A linha em português do inline de conteúdo (o molde `__prefix__` não conta). */
      var idiomas = form.querySelectorAll('select[name^="translations-"][name$="-language"]');
      for (var i = 0; i < idiomas.length; i++) {
        var select = idiomas[i];
        if (select.name.indexOf("__prefix__") !== -1 || select.value !== "pt") {
          continue;
        }
        var apagar = form.querySelector('[name="' + select.name.replace(/-language$/, "-DELETE") + '"]');
        if (apagar && apagar.checked) {
          continue;
        }
        var campoNome = form.querySelector('[name="' + select.name.replace(/-language$/, "-name") + '"]');
        if (campoNome) {
          return campoNome.value;
        }
      }
      return "";
    }

    var manual = Boolean(sku.value.trim());
    var timer = null;
    var pedido = null;
    var textoAjuda = ajuda ? ajuda.textContent : "";

    function atualizarPrevia() {
      if (previaVariante) {
        previaVariante.textContent = (sku.value || "SKU") + "-V01";
      }
    }

    function dizer(texto) {
      if (ajuda) {
        ajuda.textContent = texto;
      }
    }

    function sugerir() {
      var texto = nome().trim();
      if (manual) {
        return;
      }
      if (!texto) {
        sku.value = "";
        dizer(textoAjuda);
        atualizarPrevia();
        return;
      }
      if (pedido && pedido.abort) {
        pedido.abort();
      }
      var controller = window.AbortController ? new AbortController() : null;
      pedido = controller;
      var query = "?name=" + encodeURIComponent(texto) + "&category=" + encodeURIComponent(categoria() || "");
      fetch(url + query, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: controller ? controller.signal : undefined
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (dados) {
          if (!dados || manual) {
            return;
          }
          sku.value = dados.sku || "";
          dizer("Gerado automaticamente a partir da categoria e do nome. Você pode alterar.");
          atualizarPrevia();
        })
        .catch(function () { /* sem sugestão agora: o servidor sugere ao gravar */ });
    }

    function agendar() {
      window.clearTimeout(timer);
      timer = window.setTimeout(sugerir, 300);
    }

    /* Nome e categoria: qualquer um dos dois muda a sugestão. No formulário
       completo o nome está num inline que pode ganhar linhas, e a categoria é
       um select2 (que dispara `change` pelo jQuery, não pelo DOM) — daí ouvir
       no formulário inteiro, e também pelo jQuery do Admin quando existir. */
    form.addEventListener("input", function (evento) {
      if (evento.target === sku) {
        return;
      }
      agendar();
    });
    form.addEventListener("change", function (evento) {
      if (evento.target === sku) {
        return;
      }
      agendar();
    });
    if (window.django && window.django.jQuery) {
      window.django.jQuery(form).on("change", "#id_category", agendar);
    }
    document.addEventListener("formset:added", agendar);

    sku.addEventListener("input", function () {
      manual = sku.value.trim() !== "";
      dizer(manual ? "SKU definido por você — a sugestão automática não o substitui." : textoAjuda);
      atualizarPrevia();
      if (!manual) {
        agendar();
      }
    });

    atualizarPrevia();
    agendar();
  });
})();
