/* Inline tabular como cards nas telas estreitas.

   O CSS (`jdprint_forms.css`, fieldset `.jd-cards`) empilha cada linha da
   tabela como um bloco e mostra o nome da coluna antes de cada campo. O nome
   vem do próprio cabeçalho da tabela: este script copia o texto de cada `th`
   para `data-label` nas células da mesma coluna — inclusive nas linhas que o
   Django cria ao clicar em "Adicionar". Nada muda no desktop. */
(function () {
  "use strict";

  function label(th) {
    var text = "";
    th.childNodes.forEach(function (node) {
      if (node.nodeType === 3) {
        text += node.textContent;
      }
    });
    return text.replace(/\s+/g, " ").trim();
  }

  function stamp(table) {
    var heads = table.querySelectorAll("thead th");
    var labels = [];
    heads.forEach(function (th) { labels.push(label(th)); });
    table.querySelectorAll("tbody tr").forEach(function (row) {
      var cells = row.querySelectorAll(":scope > td");
      cells.forEach(function (cell, index) {
        if (cells.length === heads.length && labels[index]) {
          cell.setAttribute("data-label", labels[index]);
        }
      });
    });
  }

  function run() {
    document.querySelectorAll("fieldset.jd-cards table").forEach(stamp);
  }

  document.addEventListener("DOMContentLoaded", run);
  document.addEventListener("formset:added", run);
})();
