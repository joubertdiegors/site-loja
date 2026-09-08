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
  });
})();
