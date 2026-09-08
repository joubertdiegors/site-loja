/* A ficha do produto: a PALETA DE CORES só aparece quando o modo de cores é
   «Uma cor» ou «Multicolorido». Nos outros modos a lista não tem o que dizer
   («Não se aplica», «Cores à escolha») ou a cor vem da variante («Opção
   comercial»). Só apresentação: nada é apagado ao trocar o modo, e sem
   JavaScript a lista continua na página. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var modo = document.getElementById("id_color_mode");
    var paleta = document.getElementById("product_colors-group");
    if (!modo || !paleta) {
      return;
    }

    function aplicar() {
      var lista = modo.value === "single" || modo.value === "multi";
      paleta.style.display = lista ? "" : "none";
    }

    modo.addEventListener("change", aplicar);
    aplicar();
  });
})();
