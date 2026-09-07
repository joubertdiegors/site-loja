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
    var blocos = {
      "fieldset.jd-sp-maintenance": [MAINTENANCE],
      "fieldset.jd-sp-launch": [LAUNCH]
    };
    var campos = {
      progress_label: [MAINTENANCE],
      countdown_done_text: [LAUNCH],
      form_placeholder: [LAUNCH],
      form_button_label: [LAUNCH],
      form_note: [LAUNCH],
      form_success_text: [LAUNCH]
    };

    function aplicar() {
      var atual = kind.value;
      Object.keys(blocos).forEach(function (seletor) {
        window.jdShowBlock(seletor, blocos[seletor].indexOf(atual) !== -1);
      });
      Object.keys(campos).forEach(function (nome) {
        window.jdShowField(nome, campos[nome].indexOf(atual) !== -1);
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
