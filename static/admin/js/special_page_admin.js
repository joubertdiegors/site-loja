/* O formulário da página de manutenção/lançamento no Admin.

   1. Mostra só o que o tipo escolhido usa: a manutenção tem o progresso da
      impressora; o lançamento tem a contagem regressiva e o formulário de
      aviso. Nada é apagado — trocar o tipo de volta traz o que estava lá.

   2. Ligar "ativa" pede confirmação: a página bloqueia o site público na
      hora, e um clique distraído no checkbox não pode fechar a loja. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var kind = document.getElementById("id_kind");
    if (!kind) {
      return;
    }

    var MAINTENANCE = "maintenance", LAUNCH = "launch";
    var alvos = {
      "fieldset.jd-sp-maintenance": [MAINTENANCE],
      "fieldset.jd-sp-launch": [LAUNCH],
      ".field-progress_label": [MAINTENANCE],
      ".field-countdown_done_text": [LAUNCH],
      ".field-form_placeholder": [LAUNCH],
      ".field-form_button_label": [LAUNCH],
      ".field-form_note": [LAUNCH],
      ".field-form_success_text": [LAUNCH]
    };

    function aplicar() {
      var atual = kind.value;
      Object.keys(alvos).forEach(function (seletor) {
        var visivel = alvos[seletor].indexOf(atual) !== -1;
        document.querySelectorAll(seletor).forEach(function (node) {
          node.style.display = visivel ? "" : "none";
        });
      });
    }

    kind.addEventListener("change", aplicar);
    new MutationObserver(aplicar).observe(document.body, { childList: true, subtree: true });
    aplicar();

    var ativa = document.getElementById("id_is_active");
    var form = ativa && ativa.form;
    if (!ativa || !form) {
      return;
    }
    var estavaAtiva = ativa.checked;
    form.addEventListener("submit", function (event) {
      if (ativa.checked && !estavaAtiva) {
        var ok = window.confirm(
          "⚠️ ATIVAR ESTA PÁGINA BLOQUEARÁ O SITE PÚBLICO.\n\n" +
          "Todo visitante passa a ver só esta página, em qualquer endereço, " +
          "e qualquer outra página ativa é desligada. O Admin continua acessível.\n\n" +
          "Ativar agora?"
        );
        if (!ok) {
          event.preventDefault();
        }
      }
    });
  });
})();
