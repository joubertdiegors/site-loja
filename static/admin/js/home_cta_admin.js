/* Mostra apenas o campo de destino correspondente ao tipo de CTA escolhido. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var targetField = document.getElementById("id_cta_target");
    if (!targetField) {
      return;
    }

    var map = {
      category: ".field-cta_category",
      product: ".field-cta_product",
      url: ".field-cta_url"
    };

    function apply() {
      Object.keys(map).forEach(function (key) {
        var visible = targetField.value === key;
        document.querySelectorAll(map[key]).forEach(function (node) {
          node.style.display = visible ? "" : "none";
        });
      });
    }

    targetField.addEventListener("change", apply);
    apply();
  });
})();
