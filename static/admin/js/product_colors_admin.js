/* A ficha do produto: a PALETA DE CORES só aparece quando o modo de cores
   usa a paleta — «Uma cor», «Multicolorido» e «Cores à escolha do cliente».
   Nos outros modos a lista não tem o que dizer («Não se aplica») ou a cor vem
   da variante («Opção comercial»).

   A coluna "adicional (€)" é só de «Cores à escolha do cliente»: é o que se
   soma ao preço da variante quando o cliente escolhe aquela cor. Nos modos
   descritivos ela não teria efeito, então some da vista.

   Só apresentação: nada é apagado ao trocar o modo, e sem JavaScript a lista
   inteira continua na página. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var modo = document.getElementById("id_color_mode");
    var paleta = document.getElementById("product_colors-group");
    if (!modo || !paleta) {
      return;
    }

    function aplicar() {
      var lista = modo.value === "single" || modo.value === "multi" || modo.value === "custom";
      var escolha = modo.value === "custom";
      paleta.style.display = lista ? "" : "none";
      paleta.classList.toggle("jd-paleta-escolha", escolha);
      paleta
        .querySelectorAll("th.column-price_delta, td.field-price_delta")
        .forEach(function (celula) {
          celula.style.display = escolha ? "" : "none";
        });
    }

    modo.addEventListener("change", aplicar);
    /* As linhas que o Django acrescenta ao clicar em «Adicionar» nascem
       depois: a coluna delas precisa da mesma regra. */
    document.addEventListener("formset:added", aplicar);
    aplicar();
  });
})();
