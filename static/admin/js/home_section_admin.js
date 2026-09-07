/* Mostra no formulário de seção apenas os campos que fazem sentido para o
   tipo escolhido. Sem JavaScript, todos os campos continuam visíveis e o
   formulário segue funcionando — a validação de verdade está no servidor.

   Nove tipos, quatro conjuntos de campos: as faixas de produtos têm layout,
   limite, botão e o conteúdo por idioma; "Produtos de uma categoria" tem a
   categoria; "Produtos escolhidos manualmente" tem a lista de produtos; a
   chamada final e o "Sobre a loja" escolhem o conteúdo; categorias em
   destaque e como trabalhamos têm os seus blocos/cards em cadastros
   próprios (o bloco BLOCOS DESTA SEÇÃO mostra os links). */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var typeField = document.getElementById("id_section_type");
    if (!typeField) {
      return;
    }

    var PRODUCTS = ["manual", "category", "featured", "newest", "best_sellers"];
    var groups = {
      "fieldset.jd-produtos": PRODUCTS,
      "fieldset.jd-cta": PRODUCTS,
      "fieldset.jd-callout": ["callout"],
      "fieldset.jd-about": ["about"],
      "fieldset.jd-blocos": ["category_cards", "how_we_work"],
      "#translations-group": PRODUCTS,
      "#items-group, #homesectionproduct_set-group": ["manual"]
    };
    var fields = {
      category: ["category"],
      include_subcategories: ["category"]
    };

    function apply() {
      var value = typeField.value;
      Object.keys(groups).forEach(function (selector) {
        window.jdShowBlock(selector, groups[selector].indexOf(value) !== -1);
      });
      Object.keys(fields).forEach(function (name) {
        window.jdShowField(name, fields[name].indexOf(value) !== -1);
      });
    }

    typeField.addEventListener("change", apply);
    apply();
  });
})();
