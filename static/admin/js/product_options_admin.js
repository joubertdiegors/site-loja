/* OPÇÕES ADICIONAIS na ficha do produto: cards, chips e dois modais.

   A casca dos modais é a mesma das variantes e do conteúdo (`jd_modal.js`):
   abrir, fechar, ESC, foco, Cancelar que desfaz, erros por campo. Aqui fica
   só o que é de opção: preencher o modal a partir do card, montar o POST e
   recarregar a página depois de gravar ou excluir.

   ## Por que recarrega

   O modal da variante desenha um `<select>` por opção do produto, com os
   valores daquela opção. Criar, renomear ou apagar uma opção muda esses
   selects em TODAS as variantes; recarregar é o jeito honesto de a tela
   inteira contar a mesma história. A página volta aberta na seção (`#sec-opcoes`).

   ## Exclusão com RESTRICT

   Uma opção ou um valor em uso por alguma variante não é apagado: o servidor
   responde com a explicação e ela aparece no modal. Nada de erro 500. */
(function () {
  "use strict";

  function OptionsSection(root) {
    this.root = root;
    this.flashNode = root.querySelector("[data-options-flash]");
    this.urls = {
      optionSave: root.getAttribute("data-option-save-url") || "",
      optionDelete: root.getAttribute("data-option-delete-url") || "",
      valueSave: root.getAttribute("data-value-save-url") || "",
      valueDelete: root.getAttribute("data-value-delete-url") || ""
    };
  }

  OptionsSection.prototype.flash = function (mensagem, ok) {
    if (!this.flashNode) {
      return;
    }
    this.flashNode.textContent = mensagem;
    this.flashNode.className = "jd-variant-flash " + (ok ? "jd-flash-ok" : "jd-flash-erro");
    this.flashNode.hidden = false;
  };

  OptionsSection.prototype.reload = function () {
    window.setTimeout(function () {
      window.location.hash = "sec-opcoes";
      window.location.reload();
    }, 600);
  };

  /* ---- um modal genérico de «nome + ordem + traduções» ------------------- */

  function NameModal(element, attr, section) {
    this.element = element;
    this.attr = attr; /* "data-option-field" ou "data-value-field" */
    this.section = section;
    this.fields = {};
    var self = this;
    element.querySelectorAll("[" + attr + "]").forEach(function (input) {
      self.fields[input.getAttribute(attr)] = input;
    });
    this.shell = new window.JDModal(element, {});
    this.shell.fields = this.fields;
    this.titleNode = element.querySelector(".jd-modal-title");
    this.removeButton = element.querySelector(".jd-modal-danger");
    this.saveButton = element.querySelector(".jd-modal-save");
  }

  NameModal.prototype.fill = function (valores) {
    var self = this;
    Object.keys(this.fields).forEach(function (nome) {
      self.fields[nome].value = valores[nome] !== undefined && valores[nome] !== null ? valores[nome] : "";
    });
  };

  NameModal.prototype.formData = function () {
    var dados = new FormData();
    var self = this;
    Object.keys(this.fields).forEach(function (nome) {
      dados.append(nome, self.fields[nome].value);
    });
    return dados;
  };

  NameModal.prototype.open = function (titulo, valores, opener, podeExcluir) {
    this.fill(valores);
    if (this.titleNode) {
      this.titleNode.textContent = titulo;
    }
    if (this.removeButton) {
      this.removeButton.hidden = !podeExcluir;
    }
    this.shell.open(opener);
  };

  NameModal.prototype.save = function (url) {
    var self = this;
    this.saveButton.disabled = true;
    this.shell
      .post(url, this.formData())
      .then(function (r) {
        if (!r.dados.ok) {
          self.shell.showErrors(r.dados.errors, r.dados.detail);
          return;
        }
        self.shell.snapshot = null;
        self.shell.close();
        self.section.flash(r.dados.message, true);
        self.section.reload();
      })
      .catch(function () {
        self.shell.showErrors(null, "Não foi possível falar com o servidor. Tente de novo.");
      })
      .then(function () {
        self.saveButton.disabled = false;
      });
  };

  NameModal.prototype.remove = function (url, pergunta) {
    if (!window.confirm(pergunta)) {
      return;
    }
    var self = this;
    this.removeButton.disabled = true;
    this.shell
      .post(url, new FormData())
      .then(function (r) {
        if (!r.dados.ok) {
          /* A opção/valor em uso: a explicação fica no modal, que continua aberto. */
          self.shell.showErrors(null, r.dados.detail || "Não foi possível excluir.");
          return;
        }
        self.shell.snapshot = null;
        self.shell.close();
        self.section.flash(r.dados.message, true);
        self.section.reload();
      })
      .catch(function () {
        self.shell.showErrors(null, "Não foi possível falar com o servidor.");
      })
      .then(function () {
        self.removeButton.disabled = false;
      });
  };

  /* ---- ligar os cards ---------------------------------------------------- */

  function dadosDo(node, prefixo) {
    /* data-option-name-fr -> name_fr; data-option-name -> name; data-option-order -> sort_order */
    var valores = {};
    Array.prototype.forEach.call(node.attributes, function (atributo) {
      if (atributo.name.indexOf("data-" + prefixo + "-") !== 0) {
        return;
      }
      var chave = atributo.name.slice(("data-" + prefixo + "-").length);
      if (chave === "order") {
        chave = "sort_order";
      } else if (chave.indexOf("name-") === 0) {
        chave = "name_" + chave.slice(5);
      } else if (chave === "id") {
        chave = prefixo + "_id";
      }
      valores[chave] = atributo.value;
    });
    return valores;
  }

  function start(root) {
    var section = new OptionsSection(root);
    var optionModalNode = root.querySelector("[data-option-modal]");
    var valueModalNode = root.querySelector("[data-value-modal]");
    if (!optionModalNode || !valueModalNode || !window.JDModal) {
      return;
    }
    var optionModal = new NameModal(optionModalNode, "data-option-field", section);
    var valueModal = new NameModal(valueModalNode, "data-value-field", section);

    optionModalNode.querySelectorAll("[data-option-cancel]").forEach(function (b) {
      b.addEventListener("click", function () { optionModal.shell.cancel(); });
    });
    valueModalNode.querySelectorAll("[data-value-cancel]").forEach(function (b) {
      b.addEventListener("click", function () { valueModal.shell.cancel(); });
    });
    optionModalNode.querySelector("[data-option-save]").addEventListener("click", function () {
      optionModal.save(section.urls.optionSave);
    });
    valueModalNode.querySelector("[data-value-save]").addEventListener("click", function () {
      var optionId = valueModal.fields.option_id.value;
      valueModal.save(section.urls.valueSave.replace(/\/0\/valor\/gravar\/$/, "/" + optionId + "/valor/gravar/"));
    });
    optionModalNode.querySelector("[data-option-remove]").addEventListener("click", function () {
      var id = optionModal.fields.option_id.value;
      optionModal.remove(
        section.urls.optionDelete.replace(/\/0\/excluir\/$/, "/" + id + "/excluir/"),
        "Excluir a opção «" + optionModal.fields.name.value + "» e todos os valores dela?"
      );
    });
    valueModalNode.querySelector("[data-value-remove]").addEventListener("click", function () {
      var optionId = valueModal.fields.option_id.value;
      var id = valueModal.fields.value_id.value;
      valueModal.remove(
        section.urls.valueDelete.replace(/\/0\/valor\/0\/excluir\/$/, "/" + optionId + "/valor/" + id + "/excluir/"),
        "Excluir o valor «" + valueModal.fields.name.value + "»?"
      );
    });

    /* «+ Adicionar opção» */
    var addOption = root.querySelector("[data-option-add]");
    if (addOption) {
      addOption.addEventListener("click", function () {
        optionModal.open("Nova opção", { sort_order: String(root.querySelectorAll("[data-option-card]").length + 1) }, addOption, false);
      });
    }

    root.querySelectorAll("[data-option-card]").forEach(function (card) {
      var opcao = dadosDo(card, "option");
      var edit = card.querySelector("[data-option-edit]");
      if (edit) {
        edit.addEventListener("click", function () {
          optionModal.open(opcao.name, opcao, edit, true);
        });
      }
      var addValue = card.querySelector("[data-value-add]");
      if (addValue) {
        addValue.addEventListener("click", function () {
          var quantos = card.querySelectorAll("[data-value-edit]").length;
          valueModal.open(
            opcao.name + " — novo valor",
            { option_id: opcao.option_id, sort_order: String(quantos + 1) },
            addValue,
            false
          );
        });
      }
      card.querySelectorAll("[data-value-edit]").forEach(function (chip) {
        chip.addEventListener("click", function () {
          var valor = dadosDo(chip, "value");
          valor.option_id = opcao.option_id;
          valueModal.open(opcao.name + " — " + valor.name, valor, chip, true);
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-options-inline]").forEach(start);
  });
})();
