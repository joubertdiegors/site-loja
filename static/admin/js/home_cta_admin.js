/* Mostra apenas o campo de destino correspondente ao tipo de CTA escolhido.
   Usa `jdShowField` (admin/js/jd_fields.js): os quatro campos do botão
   dividem a mesma linha, e é a caixa de cada um que some, não a linha. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var targetField = document.getElementById("id_cta_target");
    if (!targetField) {
      return;
    }

    var map = { category: "cta_category", product: "cta_product", url: "cta_url" };

    function apply() {
      Object.keys(map).forEach(function (key) {
        window.jdShowField(map[key], targetField.value === key);
      });
    }

    targetField.addEventListener("change", apply);
    apply();
  });
})();
