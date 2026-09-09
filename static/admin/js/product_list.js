/* A lista de produtos: "Ativar"/"Desativar" direto da linha.

   O link leva a uma página de confirmação (o caminho sem JavaScript); com
   JavaScript ele envia o POST na hora e recarrega a lista com os mesmos
   filtros. O servidor é quem decide se pode ativar — sem variante ativa, sem
   categoria ou sem nome em português a resposta é uma mensagem, não um
   produto ativo pela metade. */
(function () {
  "use strict";

  function csrfToken() {
    var campo = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (campo) {
      return campo.value;
    }
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("a[data-toggle-status]").forEach(function (link) {
      link.addEventListener("click", function (evento) {
        evento.preventDefault();
        /* Na ficha do produto o clique grava o status e recarrega: o que
           ainda não foi salvo se perde, e a pessoa tem de saber antes. */
        var aviso = link.getAttribute("data-toggle-confirm");
        if (aviso && !window.confirm(aviso)) {
          return;
        }
        var dados = new FormData();
        dados.append("next", window.location.pathname + window.location.search);
        link.setAttribute("aria-busy", "true");
        /* `redirect: "manual"`: o servidor responde com um redirecionamento
           para a lista; se o fetch o seguisse, a mensagem ("ativado", ou o
           motivo da recusa) seria consumida por ele e não apareceria no
           reload. */
        fetch(link.getAttribute("href"), {
          method: "POST",
          body: dados,
          credentials: "same-origin",
          redirect: "manual",
          headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" }
        })
          .then(function () { window.location.reload(); })
          .catch(function () { window.location.href = link.getAttribute("href"); });
      });
    });

    /* Um menu "⋮" aberto de cada vez; clicar fora fecha. */
    document.addEventListener("click", function (evento) {
      document.querySelectorAll("details.jd-row-menu[open]").forEach(function (menu) {
        if (!menu.contains(evento.target)) {
          menu.removeAttribute("open");
        }
      });
    });

    painelDeFiltros();
    barraDeFerramentas();
    barraDeAcoes();
  });

  /* ---- painel de filtros ------------------------------------------------

     Melhoria progressiva, e só isso: os grupos são `<details>`, as opções são
     caixas de seleção de verdade e "Aplicar" é um `submit`. Sem JavaScript o
     painel filtra igual — o que se ganha aqui é fechar o grupo ao clicar fora
     e não deixar dois abertos ao mesmo tempo, que numa grade de nove balões
     seria uma sopa. */

  function painelDeFiltros() {
    var grupos = document.querySelectorAll(".jd-grupo details.jd-grupo-caixa");
    if (!grupos.length) {
      return;
    }

    function fechar(exceto) {
      grupos.forEach(function (grupo) {
        if (grupo !== exceto) {
          grupo.removeAttribute("open");
        }
      });
    }

    grupos.forEach(function (grupo) {
      grupo.addEventListener("toggle", function () {
        if (grupo.open) {
          fechar(grupo);
        }
      });
    });

    document.addEventListener("click", function (evento) {
      grupos.forEach(function (grupo) {
        if (grupo.open && !grupo.contains(evento.target)) {
          grupo.removeAttribute("open");
        }
      });
    });

    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape") {
        fechar(null);
      }
    });
  }

  /* ---- barra de ferramentas --------------------------------------------

     Ordenação e itens por página moram fisicamente fora do formulário de
     filtros e pertencem a ele pelo atributo `form`. Sem JavaScript é preciso
     apertar "Buscar" depois de escolher; com ele, escolher já envia. */

  function barraDeFerramentas() {
    document.querySelectorAll("[data-jd-envia]").forEach(function (campo) {
      campo.addEventListener("change", function () {
        var formulario = campo.form || document.getElementById("jd-painel");
        if (formulario) {
          formulario.submit();
        }
      });
    });
  }

  /* ---- seleção e ações em massa ---------------------------------------

     O "selecionar tudo" da barra é um espelho do que o Django desenha no
     cabeçalho da tabela: ele não marca as linhas por conta própria, clica no
     original. É o `actions.js` do Admin que continua mandando — inclusive no
     contador e no "selecionar todos os N".

     A barra de ações só aparece quando há linha marcada. Ela nasce visível no
     HTML: se este arquivo não rodar, ela fica onde está e continua funcionando. */

  function barraDeAcoes() {
    var linhas = document.querySelectorAll("#changelist-form input.action-select");
    var barra = document.querySelector("[data-jd-massa]");
    var espelho = document.querySelector("[data-jd-selecionar]");
    var original = document.getElementById("action-toggle");

    if (barra && linhas.length) {
      barra.classList.add("jd-massa-flutuante");
    }

    function marcadas() {
      var total = 0;
      linhas.forEach(function (caixa) {
        if (caixa.checked) {
          total += 1;
        }
      });
      return total;
    }

    function atualizar() {
      var total = marcadas();
      if (barra && linhas.length) {
        barra.hidden = total === 0;
      }
      if (espelho && original) {
        var caixa = espelho.querySelector("[data-jd-selecionar-caixa]");
        if (caixa) {
          caixa.checked = total > 0 && total === linhas.length;
          caixa.indeterminate = total > 0 && total < linhas.length;
        }
      }
    }

    if (espelho && original && linhas.length) {
      espelho.hidden = false;
      var caixa = espelho.querySelector("[data-jd-selecionar-caixa]");
      if (caixa) {
        caixa.addEventListener("change", function () {
          /* Clicar no original em vez de mexer nas linhas: assim o
             `actions.js` do Django recebe o mesmo evento de sempre e o
             contador, o "selecionar todos" e o realce das linhas continuam
             sendo trabalho dele. */
          if (original.checked !== caixa.checked) {
            original.click();
          }
          atualizar();
        });
      }
    }

    linhas.forEach(function (linha) {
      linha.addEventListener("change", atualizar);
    });
    if (original) {
      original.addEventListener("change", atualizar);
    }
    atualizar();
  }
})();
