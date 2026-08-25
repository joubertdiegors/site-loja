/* Mostra no formulário de seção apenas os campos que fazem sentido para o
   tipo escolhido. Sem JavaScript, todos os campos continuam visíveis e o
   formulário segue funcionando — a validação de verdade está no servidor. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var typeField = document.getElementById("id_section_type");
    if (!typeField) {
      return;
    }

    var categoryRows = [".field-category", ".field-include_subcategories"];
    var manualGroup = "#items-group, #homesectionproduct_set-group";

    function show(selector, visible) {
      document.querySelectorAll(selector).forEach(function (node) {
        node.style.display = visible ? "" : "none";
      });
    }

    function apply() {
      var value = typeField.value;
      categoryRows.forEach(function (selector) {
        show(selector, value === "category");
      });
      show(manualGroup, value === "manual");
    }

    typeField.addEventListener("change", apply);
    apply();
  });
})();
