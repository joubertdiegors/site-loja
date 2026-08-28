/* A casca dos modais do Admin — usada por VARIANTES e por CONTEÚDO.

   O que é comum a qualquer modal deste projeto vive aqui: abrir, fechar,
   travar a rolagem do fundo, prender o Tab dentro do diálogo, fechar no ESC,
   devolver o foco a quem abriu, fotografar os campos para o Cancelar poder
   desfazer, e pintar os erros que o servidor devolveu por campo.

   **Clicar fora não fecha.** Só Cancelar, o X e o ESC fecham. O fundo escuro
   engole o clique e não faz mais nada: fechar ali descartaria o que foi
   digitado, e é o gesto mais fácil de fazer sem querer.

   O que é do domínio — preço, margem, selo de estoque, idioma — fica em
   `variant_admin.js` e `content_admin.js`. É a divisão que permite os dois
   modais parecerem e se comportarem igual sem que um saiba do outro.

   Não é um framework: são ~150 linhas sem dependência, e o CSS
   (`.jd-modal*` em `jdprint_admin.css`) é o mesmo para os dois. */
(function (window, document) {
  "use strict";

  /* Só um modal aberto por vez na página inteira. Dois abertos ao mesmo tempo
     brigariam pela trava de rolagem e pelo ESC. */
  var aberto = null;

  function csrfToken() {
    var campo = document.querySelector("input[name=csrfmiddlewaretoken]");
    return campo ? campo.value : "";
  }

  /**
   * @param {HTMLElement} element  a raiz `.jd-modal`
   * @param {object} opcoes
   *   - fieldSelector: como achar os campos do formulário dentro do modal
   *   - onClose:       chamado depois de fechar (para atualizar a linha)
   */
  function JDModal(element, opcoes) {
    this.element = element;
    this.opcoes = opcoes || {};
    this.dialog = element.querySelector(".jd-modal-dialog");
    this.errorNode = element.querySelector("[data-modal-error]");
    this.titleNode = element.querySelector("[data-modal-title]");
    this.opener = null;
    this.snapshot = null;
    this.fields = {};

    /* Clicar fora NÃO fecha. Fechar aqui descartaria o que foi digitado, e um
       clique fora do diálogo é a coisa mais fácil de fazer sem querer — num
       campo de descrição isso custava parágrafos. Sair do modal é uma decisão,
       e ela tem três portas: Cancelar, o X e o ESC.

       O ouvinte existe para **engolir** o clique. Sem ele, o clique chegaria ao
       formulário do produto atrás, que o `aria-modal` diz não estar lá.

       A guarda fica no `.jd-modal` inteiro, e não só no `.jd-modal-backdrop`:
       o fundo escuro cobre a área toda hoje, mas basta um `padding` novo para
       aparecer uma faixa que não é nem fundo nem diálogo. Perguntar "o clique
       veio de dentro do diálogo?" não depende da geometria do CSS. */
    var self = this;
    element.addEventListener("click", function (evento) {
      if (self.dialog && self.dialog.contains(evento.target)) {
        return;
      }
      evento.preventDefault();
      evento.stopPropagation();
    });
  }

  /* ---- campos ------------------------------------------------------------ */

  JDModal.prototype.field = function (nome) {
    return this.fields[nome] || null;
  };

  JDModal.prototype.value = function (nome) {
    var campo = this.field(nome);
    return campo ? campo.value : "";
  };

  JDModal.prototype.checked = function (nome) {
    var campo = this.field(nome);
    return !!(campo && campo.checked);
  };

  /* Fotografia dos campos, para o Cancelar poder desfazer. Sem isto, fechar
     sem salvar deixaria os valores no DOM — e eles iriam para o banco no
     próximo "Salvar" do produto. Um cancelamento que não cancela. */
  JDModal.prototype.capture = function () {
    var estado = {};
    var self = this;
    Object.keys(this.fields).forEach(function (nome) {
      var campo = self.fields[nome];
      if (campo) {
        estado[nome] = campo.type === "checkbox" ? campo.checked : campo.value;
      }
    });
    return estado;
  };

  JDModal.prototype.restore = function (estado) {
    var self = this;
    Object.keys(estado || {}).forEach(function (nome) {
      var campo = self.fields[nome];
      if (!campo) {
        return;
      }
      if (campo.type === "checkbox") {
        campo.checked = estado[nome];
      } else {
        campo.value = estado[nome];
      }
    });
  };

  /* ---- abrir e fechar ---------------------------------------------------- */

  JDModal.prototype.open = function (opener) {
    if (aberto && aberto !== this) {
      aberto.close();
    }
    this.opener = opener || null;
    this.snapshot = this.capture();
    this.clearErrors();

    this.element.hidden = false;
    this.element.classList.add("jd-modal-open");
    document.body.classList.add("jd-modal-lock");
    aberto = this;

    if (this.opcoes.onOpen) {
      this.opcoes.onOpen.call(this);
    }

    var primeiro = this.dialog.querySelector(
      "input:not([type=hidden]), select, textarea"
    );
    if (primeiro) {
      primeiro.focus();
    }
  };

  JDModal.prototype.close = function () {
    this.element.hidden = true;
    this.element.classList.remove("jd-modal-open");
    document.body.classList.remove("jd-modal-lock");
    if (aberto === this) {
      aberto = null;
    }
    if (this.opcoes.onClose) {
      this.opcoes.onClose.call(this);
    }
    /* Devolve o foco a quem abriu: sem isto o teclado volta para o topo. */
    if (this.opener && document.contains(this.opener)) {
      this.opener.focus();
    }
    this.opener = null;
  };

  JDModal.prototype.cancel = function () {
    if (this.snapshot) {
      this.restore(this.snapshot);
    }
    this.close();
  };

  JDModal.prototype.isOpen = function () {
    return !this.element.hidden;
  };

  /* ---- erros ------------------------------------------------------------- */

  JDModal.prototype.clearErrors = function () {
    this.element.querySelectorAll(".jd-field-error").forEach(function (node) {
      node.remove();
    });
    this.element.querySelectorAll(".jd-modal-field").forEach(function (node) {
      node.classList.remove("jd-field-invalid");
    });
    if (this.errorNode) {
      this.errorNode.hidden = true;
      this.errorNode.textContent = "";
    }
  };

  /* Pinta os erros que o servidor devolveu, por campo, e mantém o modal
     aberto com o que já foi digitado — recarregar perderia tudo. */
  JDModal.prototype.showErrors = function (errors, detail) {
    this.clearErrors();
    var self = this;
    var primeiro = null;
    var soltos = [];

    Object.keys(errors || {}).forEach(function (campo) {
      var input = self.field(campo);
      var caixa = input
        ? input.closest(".jd-modal-field")
        : self.element.querySelector(".field-" + campo);
      if (!caixa) {
        soltos.push((errors[campo] || []).join(" "));
        return;
      }
      caixa.classList.add("jd-field-invalid");
      var lista = document.createElement("ul");
      lista.className = "errorlist jd-field-error";
      (errors[campo] || []).forEach(function (mensagem) {
        var item = document.createElement("li");
        item.textContent = mensagem;
        lista.appendChild(item);
      });
      caixa.insertBefore(lista, caixa.firstChild);
      if (!primeiro) {
        primeiro = input || caixa;
      }
    });

    var texto = [detail || ""].concat(soltos).join(" ").trim();
    if (this.errorNode && texto) {
      this.errorNode.textContent = texto;
      this.errorNode.hidden = false;
    }
    if (primeiro && primeiro.focus) {
      primeiro.focus();
    }
  };

  /* ---- POST -------------------------------------------------------------- */

  /** Envia `dados` e devolve `{status, dados}` — sem tratar o resultado. */
  JDModal.prototype.post = function (url, dados) {
    dados.append("csrfmiddlewaretoken", csrfToken());
    return fetch(url, {
      method: "POST",
      body: dados,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    }).then(function (resposta) {
      return resposta.json().then(function (corpo) {
        return { status: resposta.status, dados: corpo };
      });
    });
  };

  /* ---- teclado ------------------------------------------------------------
     Um ouvinte para a página inteira, e não um por modal: com vinte modais no
     DOM seriam vinte ouvintes fazendo a mesma pergunta. */
  document.addEventListener("keydown", function (evento) {
    if (!aberto) {
      return;
    }

    if (evento.key === "Escape") {
      evento.preventDefault();
      aberto.cancel();
      return;
    }

    if (evento.key !== "Tab") {
      return;
    }

    /* Prende o Tab dentro do diálogo: sem isto o foco escapa para o formulário
       do produto atrás, que o `aria-modal` diz não estar lá. */
    var focaveis = aberto.dialog.querySelectorAll(
      'a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]),' +
      " select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
    );
    if (!focaveis.length) {
      return;
    }
    var primeiro = focaveis[0];
    var ultimo = focaveis[focaveis.length - 1];

    if (evento.shiftKey && document.activeElement === primeiro) {
      evento.preventDefault();
      ultimo.focus();
    } else if (!evento.shiftKey && document.activeElement === ultimo) {
      evento.preventDefault();
      primeiro.focus();
    }
  });

  window.JDModal = JDModal;
  window.JDModal.csrfToken = csrfToken;
})(window, document);
