/* A ficha do país: a MODALIDADE grátis segue a TRANSPORTADORA escolhida.

   Cada opção do select de modalidade traz a transportadora dela
   (`data-carrier`, posto por `MethodSelect` em `apps/core/admin.py`). Ao
   escolher a transportadora, as modalidades das outras saem da lista; sem
   transportadora, a lista fica só com a linha vazia, porque escolher uma
   modalidade antes não diz nada.

   Só apresentação, e a mesma regra vale no servidor: `DeliveryCountry.clean()`
   recusa o par que não combina, venha ele desta tela ou de um POST direto.
   Sem JavaScript a lista aparece inteira e a validação continua fazendo o
   trabalho. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var transportadora = document.getElementById("id_free_shipping_carrier");
    var modalidade = document.getElementById("id_free_shipping_method");
    if (!transportadora || !modalidade) {
      return;
    }

    /* A lista inteira, guardada antes do primeiro filtro: remover e recolocar
       `<option>` é o único jeito que todos os navegadores respeitam. */
    var todas = [].slice.call(modalidade.options).map(function (opcao) {
      return {
        value: opcao.value,
        text: opcao.textContent,
        carrier: opcao.getAttribute("data-carrier") || ""
      };
    });

    function aplicar() {
      var escolhida = transportadora.value;
      var atual = modalidade.value;
      modalidade.innerHTML = "";
      var aindaVale = false;

      todas.forEach(function (dados) {
        var vazia = !dados.value;
        if (!vazia && (!escolhida || dados.carrier !== escolhida)) {
          return;
        }
        var opcao = document.createElement("option");
        opcao.value = dados.value;
        opcao.textContent = dados.text;
        if (dados.carrier) {
          opcao.setAttribute("data-carrier", dados.carrier);
        }
        modalidade.appendChild(opcao);
        if (dados.value && dados.value === atual) {
          aindaVale = true;
        }
      });

      /* A modalidade que ficou fora da lista não pode continuar escolhida. */
      modalidade.value = aindaVale ? atual : "";
    }

    transportadora.addEventListener("change", aplicar);
    aplicar();
  });
})();
