/* CONTEÚDO no cadastro do produto: tabela por idioma + modal de edição.

   Mesmo desenho e mesma casca (`jd_modal.js`) das VARIANTES — abrir, fechar,
   ESC, foco, trava de rolagem, Cancelar que desfaz, erros por campo. Aqui fica
   só o que é de conteúdo: o resumo da linha e o que enviar ao servidor.

   Antes, os cinco campos de cada idioma ficavam abertos na página: com quatro
   idiomas eram vinte campos empilhados antes de chegar às variantes.

   ## Onde o "Salvar" grava

   Na tela de EDIÇÃO grava o idioma sozinho, por
   `admin:catalog_product_content_save`. Na de CADASTRO o produto ainda não tem
   PK: lá o modal alimenta o formset e a gravação acontece junto com o produto.
   A tela diz isso.

   ## Criar e remover recarregam a página

   Pelo mesmo motivo das variantes: um idioma criado por fora do formset
   passaria a existir duas vezes — um no banco, outro como formulário "novo" —
   e o próximo "Salvar" do produto tentaria inseri-lo de novo. Editar não
   recarrega, porque aí o formulário já é o do idioma certo. */
(function () {
  "use strict";

  var CAMPOS = ["language", "name", "short_description", "description", "extra_information"];

  function corta(texto, limite) {
    var limpo = String(texto || "").replace(/\s+/g, " ").trim();
    if (!limpo) {
      return "—";
    }
    return limpo.length > limite ? limpo.slice(0, limite - 1) + "…" : limpo;
  }

  /* ---- Um modal de conteúdo ---------------------------------------------- */

  function ContentModal(element, table) {
    this.element = element;
    this.table = table;
    this.row = null;
    this.fields = {};

    var self = this;
    CAMPOS.concat(["id", "DELETE"]).forEach(function (nome) {
      self.fields[nome] = element.querySelector('[name$="-' + nome + '"]');
    });

    this.titleNode = element.querySelector("[data-content-modal-title]");
    this.saveButton = element.querySelector("[data-content-save]");
    this.removeButton = element.querySelector("[data-content-remove]");

    this.shell = new window.JDModal(element, {
      onOpen: function () {
        if (self.removeButton) {
          /* Remover no servidor só faz sentido para idioma que já existe. */
          self.removeButton.hidden = !(self.translationId() && self.table.deleteUrl);
        }
      },
      onClose: function () {
        self.syncRow();
      }
    });
    this.shell.fields = this.fields;
  }

  ContentModal.prototype.value = function (nome) {
    var campo = this.fields[nome];
    return campo ? campo.value : "";
  };

  ContentModal.prototype.checked = function (nome) {
    var campo = this.fields[nome];
    return !!(campo && campo.checked);
  };

  ContentModal.prototype.languageLabel = function () {
    var campo = this.fields.language;
    if (!campo) {
      return "—";
    }
    if (campo.tagName === "SELECT") {
      var opcao = campo.options[campo.selectedIndex];
      return opcao && opcao.value ? opcao.textContent.trim() : "—";
    }
    return campo.value || "—";
  };

  ContentModal.prototype.translationId = function () {
    return this.value("id");
  };

  ContentModal.prototype.open = function (opener) {
    this.shell.open(opener);
  };

  ContentModal.prototype.close = function () {
    this.shell.close();
  };

  ContentModal.prototype.cancel = function () {
    this.shell.cancel();
  };

  ContentModal.prototype.showErrors = function (errors, detail) {
    this.shell.showErrors(errors, detail);
  };

  ContentModal.prototype.hasErrors = function () {
    return !!this.element.querySelector(".errorlist");
  };

  /* ---- resumo ------------------------------------------------------------ */

  ContentModal.prototype.summary = function () {
    return {
      language: this.languageLabel(),
      name: corta(this.value("name"), 40),
      short_description: corta(this.value("short_description"), 50),
      description: corta(this.value("description"), 50)
    };
  };

  ContentModal.prototype.syncRow = function () {
    if (!this.row) {
      return;
    }
    var dados = this.summary();
    this.row.querySelectorAll("[data-cell]").forEach(function (celula) {
      celula.textContent = dados[celula.getAttribute("data-cell")];
    });
    this.row.classList.toggle("jd-row-deleted", this.checked("DELETE"));
    this.row.classList.toggle("jd-row-error", this.hasErrors());
    if (this.titleNode) {
      this.titleNode.textContent =
        (this.translationId() ? "Editar conteúdo" : "Novo conteúdo") + " — " + dados.language;
    }
  };

  /* ---- gravar ------------------------------------------------------------ */

  ContentModal.prototype.formData = function () {
    var dados = new FormData();
    if (this.translationId()) {
      dados.append("translation_id", this.translationId());
    }
    var self = this;
    CAMPOS.forEach(function (nome) {
      var campo = self.fields[nome];
      if (campo) {
        dados.append(nome, campo.value);
      }
    });
    return dados;
  };

  ContentModal.prototype.applyServerValues = function (payload) {
    var self = this;
    Object.keys(payload.fields || {}).forEach(function (nome) {
      var campo = self.fields[nome];
      if (campo) {
        campo.value = payload.fields[nome];
      }
    });
    if (self.fields.id) {
      self.fields.id.value = payload.id;
    }
  };

  ContentModal.prototype.save = function () {
    if (!this.table.saveUrl) {
      /* Cadastro: o produto ainda não existe. O modal só confirma; quem grava
         é o "Salvar" do produto, com o formset. */
      this.shell.snapshot = null;
      this.close();
      this.table.flash("Conteúdo preparado. Será gravado ao salvar o produto.", true);
      return;
    }

    var self = this;
    this.saveButton.disabled = true;
    this.saveButton.textContent = "Salvando…";

    this.shell
      .post(this.table.saveUrl, this.formData())
      .then(function (r) {
        if (!r.dados.ok) {
          self.showErrors(r.dados.errors, r.dados.detail);
          return;
        }
        self.applyServerValues(r.dados);
        self.shell.snapshot = null;
        self.close();

        if (r.dados.created) {
          /* Criou por fora do formset: recarregar devolve um formset coerente
             (ver o comentário no topo do arquivo). */
          self.table.flash(r.dados.message, true);
          self.table.reload();
          return;
        }
        self.table.flash(r.dados.message, true);
      })
      .catch(function () {
        self.showErrors(null, "Não foi possível falar com o servidor. Tente de novo.");
      })
      .then(function () {
        self.saveButton.disabled = false;
        self.saveButton.textContent = "Salvar";
      });
  };

  ContentModal.prototype.remove = function () {
    if (!this.table.deleteUrl || !this.translationId()) {
      return;
    }
    var idioma = this.languageLabel();
    if (!window.confirm("Remover o conteúdo em " + idioma + "? Isto não pode ser desfeito.")) {
      return;
    }

    var self = this;
    var url = this.table.deleteUrl.replace(/0\/excluir\/$/, this.translationId() + "/excluir/");

    this.removeButton.disabled = true;
    this.shell
      .post(url, new FormData())
      .then(function (envelope) {
        var r = envelope.dados;
        if (!r.ok) {
          self.showErrors(null, r.detail || "Não foi possível remover.");
          return;
        }
        self.shell.snapshot = null;
        self.close();
        self.table.flash(r.message, true);
        self.table.reload();
      })
      .catch(function () {
        self.showErrors(null, "Não foi possível falar com o servidor.");
      })
      .then(function () {
        self.removeButton.disabled = false;
      });
  };

  /* ---- A tabela ---------------------------------------------------------- */

  var COLUNAS = ["language", "name", "short_description", "description"];

  function ContentTable(root) {
    this.root = root;
    this.tbody = root.querySelector("[data-content-rows]");
    this.emptyNote = root.querySelector("[data-content-empty]");
    this.flashNode = root.querySelector("[data-content-flash]");
    this.addButton = root.querySelector("[data-content-add]");
    this.template = root.querySelector("[data-content-template]");
    this.prefix = this.root.id.replace(/-group$/, "");
    this.totalForms = document.getElementById("id_" + this.prefix + "-TOTAL_FORMS");
    this.saveUrl = root.getAttribute("data-content-save-url") || "";
    this.deleteUrl = root.getAttribute("data-content-delete-url") || "";
    this.modals = [];
  }

  ContentTable.prototype.flash = function (mensagem, ok) {
    if (!this.flashNode) {
      return;
    }
    this.flashNode.textContent = mensagem;
    this.flashNode.className = "jd-variant-flash " + (ok ? "jd-flash-ok" : "jd-flash-erro");
    this.flashNode.hidden = false;
  };

  ContentTable.prototype.reload = function () {
    window.setTimeout(function () {
      window.location.reload();
    }, 700);
  };

  ContentTable.prototype.makeRow = function (modal) {
    var linha = document.createElement("tr");
    linha.className = "jd-variant-row";

    COLUNAS.forEach(function (chave) {
      var celula = document.createElement("td");
      celula.setAttribute("data-cell", chave);
      linha.appendChild(celula);
    });

    var acoes = document.createElement("td");
    var botao = document.createElement("button");
    botao.type = "button";
    botao.className = "jd-variant-edit";
    botao.textContent = "Editar";
    acoes.appendChild(botao);
    linha.appendChild(acoes);

    botao.addEventListener("click", function (evento) {
      evento.preventDefault();
      modal.open(botao);
    });

    return linha;
  };

  ContentTable.prototype.register = function (element) {
    var modal = new ContentModal(element, this);
    modal.row = this.makeRow(modal);
    this.tbody.appendChild(modal.row);
    this.modals.push(modal);

    var self = this;
    element.addEventListener("input", function () {
      modal.syncRow();
    });
    element.addEventListener("change", function (evento) {
      modal.syncRow();
      if (evento.target === modal.fields.DELETE) {
        self.refreshEmptyNote();
      }
    });

    /* Só o Cancelar e o X. O fundo escuro não fecha — ver `jd_modal.js`. */
    element.querySelectorAll("[data-content-cancel]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.cancel();
      });
    });
    element.querySelectorAll("[data-content-save]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.save();
      });
    });
    element.querySelectorAll("[data-content-remove]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.remove();
      });
    });

    modal.syncRow();

    /* Modal com erro do servidor abre sozinho: o administrador não pode ter
       que caçar em qual dos quatro idiomas o servidor reclamou. */
    if (modal.hasErrors()) {
      modal.open(null);
    }
    return modal;
  };

  ContentTable.prototype.refreshEmptyNote = function () {
    if (!this.emptyNote) {
      return;
    }
    var vivos = this.modals.filter(function (modal) {
      return !modal.checked("DELETE");
    });
    this.emptyNote.hidden = vivos.length > 0;
  };

  ContentTable.prototype.add = function (opener) {
    if (!this.template || !this.totalForms) {
      return;
    }
    var indice = parseInt(this.totalForms.value, 10) || 0;
    var novo = this.template.cloneNode(true);
    novo.removeAttribute("data-content-template");

    /* Troca `__prefix__` pelo índice em name, id e for — sem isso o Django não
       enxerga o formulário novo. */
    novo.innerHTML = novo.innerHTML.replace(/__prefix__/g, String(indice));
    ["name", "id", "for", "aria-labelledby"].forEach(function (atributo) {
      novo.querySelectorAll("[" + atributo + "*='__prefix__']").forEach(function (node) {
        node.setAttribute(
          atributo,
          node.getAttribute(atributo).replace(/__prefix__/g, String(indice))
        );
      });
    });

    this.template.parentNode.insertBefore(novo, this.template);
    this.totalForms.value = String(indice + 1);

    var modal = this.register(novo);
    this.refreshEmptyNote();
    modal.open(opener || this.addButton);
  };

  ContentTable.prototype.start = function () {
    var self = this;
    this.root.querySelectorAll("[data-content-modal]").forEach(function (element) {
      if (element.hasAttribute("data-content-template")) {
        return; /* o molde não vira linha */
      }
      self.register(element);
    });

    if (this.addButton) {
      this.addButton.addEventListener("click", function () {
        self.add(self.addButton);
      });
    }
    this.refreshEmptyNote();
  };

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-content-inline]").forEach(function (root) {
      new ContentTable(root).start();
    });
  });
})();
