/* Mostrar e esconder campos do formulário do Admin, campo a campo.

   Com campos relacionados dividindo a mesma linha (`fields = (("a", "b"),)`),
   a linha inteira leva as classes de todos eles — `.form-row.field-a.field-b`
   — e esconder pelo seletor `.field-a` esconderia também o `b`. Este helper
   esconde só a caixa do campo (`.fieldBox.field-a`) e, se todas as caixas de
   uma linha ficarem escondidas, a linha vai junto. Linhas de um campo só
   continuam sendo escondidas inteiras. */
(function () {
  "use strict";

  function fieldSelector(name) {
    return name.charAt(0) === "." ? name : ".field-" + name;
  }

  window.jdShowField = function (name, visible) {
    var selector = fieldSelector(name);
    var boxes = document.querySelectorAll(".fieldBox" + selector);
    boxes.forEach(function (box) {
      box.style.display = visible ? "" : "none";
      var row = box.closest(".form-row");
      if (row) {
        var all = row.querySelectorAll(".fieldBox");
        var hidden = row.querySelectorAll('.fieldBox[style*="display: none"]');
        row.style.display = all.length && hidden.length === all.length ? "none" : "";
      }
    });
    document.querySelectorAll(".form-row" + selector + ":not(.form-multiline)").forEach(function (row) {
      row.style.display = visible ? "" : "none";
    });
  };

  window.jdShowBlock = function (selector, visible) {
    document.querySelectorAll(selector).forEach(function (node) {
      node.style.display = visible ? "" : "none";
    });
  };
})();
