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

  var CAMPOS = ["language", "name", "short_description", "description", "extra_information", "color_choice_label"];

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

  /* ---- «Copiar de…» (etapa 4C.2) ----------------------------------------

     Traz os textos de outro produto para este, idioma a idioma. Duas etapas
     na mesma casca de modal: procurar a origem e confirmar o que vai
     acontecer. Nada é gravado antes do segundo passo.

     A busca é do servidor, com espera: uma loja com milhares de produtos não
     manda todos para o navegador, e uma requisição por tecla digitada seria
     uma consulta por tecla no banco.

     Depois de copiar, a página recarrega — a tabela de idiomas e os
     formulários do formset precisam nascer com o que foi gravado. Por isso a
     tela avisa e **impede** a cópia quando há alteração não salva no
     formulário do produto: recarregar perderia o que a pessoa digitou. */

  function CopyPanel(root, table) {
    this.root = root;
    this.table = table;
    this.element = root.querySelector("[data-copy-modal]");
    if (!this.element || !window.JDModal) {
      return;
    }
    this.shell = new window.JDModal(this.element, {});
    this.urls = {
      search: root.getAttribute("data-copy-search-url") || "",
      plan: root.getAttribute("data-copy-plan-url") || "",
      copy: root.getAttribute("data-copy-url") || ""
    };
    this.alvo = root.getAttribute("data-copy-target") || "";
    this.campoBusca = this.element.querySelector("[data-copy-search]");
    this.resultados = this.element.querySelector("[data-copy-results]");
    this.status = this.element.querySelector("[data-copy-status]");
    this.passoBusca = this.element.querySelector('[data-copy-step="search"]');
    this.passoConfirma = this.element.querySelector('[data-copy-step="confirm"]');
    this.resumo = this.element.querySelector("[data-copy-summary]");
    this.plano = this.element.querySelector("[data-copy-plan]");
    this.aviso = this.element.querySelector("[data-copy-dirty]");
    this.botaoVoltar = this.element.querySelector("[data-copy-back]");
    this.botaoConfirmar = this.element.querySelector("[data-copy-confirm]");
    this.escolhido = null;
    this.timer = null;
    /* Fotografia do formulário do produto ao abrir a página: é com ela que se
       sabe, na hora de copiar, se há coisa não salva para não perder. */
    this.form = root.closest("form");
    this.limpo = this.snapshot();
  }

  CopyPanel.prototype.snapshot = function () {
    if (!this.form) {
      return "";
    }
    try {
      return new URLSearchParams(new FormData(this.form)).toString();
    } catch (erro) {
      return "";
    }
  };

  CopyPanel.prototype.sujo = function () {
    return this.form ? this.snapshot() !== this.limpo : false;
  };

  CopyPanel.prototype.mostrarPasso = function (nome) {
    var busca = nome === "search";
    this.passoBusca.hidden = !busca;
    this.passoConfirma.hidden = busca;
    this.botaoVoltar.hidden = busca;
    this.botaoConfirmar.hidden = busca;
  };

  CopyPanel.prototype.open = function (opener) {
    this.escolhido = null;
    this.mostrarPasso("search");
    this.resultados.innerHTML = "";
    if (this.campoBusca) {
      this.campoBusca.value = "";
    }
    this.shell.open(opener);
    this.buscar("");
  };

  CopyPanel.prototype.dizer = function (texto) {
    if (this.status) {
      this.status.textContent = texto || "";
    }
  };

  CopyPanel.prototype.buscar = function (termo) {
    var self = this;
    this.dizer("Procurando…");
    fetch(this.urls.search + "?q=" + encodeURIComponent(termo), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    })
      .then(function (r) { return r.json(); })
      .then(function (dados) {
        if (!dados.ok) {
          self.dizer(dados.detail || "Não foi possível procurar.");
          return;
        }
        self.desenhar(dados.results, termo);
      })
      .catch(function () {
        self.dizer("Não foi possível falar com o servidor.");
      });
  };

  CopyPanel.prototype.desenhar = function (itens, termo) {
    var self = this;
    this.resultados.innerHTML = "";
    if (!itens.length) {
      this.dizer(termo ? "Nenhum produto encontrado." : "Nenhum outro produto cadastrado.");
      return;
    }
    this.dizer(termo ? itens.length + " produto(s) encontrado(s)." : "Alterados recentemente:");

    itens.forEach(function (item) {
      var linha = document.createElement("div");
      linha.className = "jd-copy-row";

      var texto = document.createElement("div");
      texto.className = "jd-copy-info";

      var nome = document.createElement("b");
      nome.textContent = item.name;
      texto.appendChild(nome);

      var meta = document.createElement("span");
      meta.className = "jd-copy-meta";
      var partes = [item.sku];
      if (item.category) {
        partes.push(item.category);
      }
      if (item.languages && item.languages.length) {
        partes.push(item.languages.join(" · "));
      }
      meta.textContent = partes.join(" — ");
      texto.appendChild(meta);

      var botao = document.createElement("button");
      botao.type = "button";
      botao.className = "jd-copy-pick";
      botao.textContent = "Selecionar";
      botao.addEventListener("click", function () {
        self.escolher(item);
      });

      linha.appendChild(texto);
      linha.appendChild(botao);
      self.resultados.appendChild(linha);
    });
  };

  CopyPanel.prototype.escolher = function (item) {
    var self = this;
    this.escolhido = item;
    this.shell.clearErrors();
    fetch(this.urls.plan.replace(/\/0\/plano\/$/, "/" + item.id + "/plano/"), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    })
      .then(function (r) { return r.json(); })
      .then(function (dados) {
        if (!dados.ok) {
          self.shell.showErrors(null, dados.detail || "Não foi possível ler o produto.");
          return;
        }
        self.confirmar(dados);
      })
      .catch(function () {
        self.shell.showErrors(null, "Não foi possível falar com o servidor.");
      });
  };

  CopyPanel.prototype.confirmar = function (dados) {
    this.resumo.innerHTML = "";
    this.resumo.appendChild(document.createTextNode("Você está prestes a copiar as descrições de "));
    var origem = document.createElement("b");
    origem.textContent = dados.source.name;
    this.resumo.appendChild(origem);
    this.resumo.appendChild(document.createTextNode(" para "));
    var destino = document.createElement("b");
    destino.textContent = dados.target.name;
    this.resumo.appendChild(destino);
    this.resumo.appendChild(document.createTextNode(". As traduções são copiadas uma a uma, por idioma."));

    this.plano.innerHTML = "";
    var self = this;
    var linhas = [
      ["replace", "serão substituída(s)"],
      ["create", "será(ão) criada(s)"],
      ["keep", "existente(s) não será(ão) alterada(s), porque a origem não tem esse idioma"]
    ];
    linhas.forEach(function (par) {
      var lista = dados.plan[par[0]] || [];
      if (!lista.length) {
        return;
      }
      var item = document.createElement("p");
      item.className = "jd-copy-plan-line jd-copy-" + par[0];
      var forte = document.createElement("b");
      forte.textContent = lista.length + " tradução(ões) ";
      item.appendChild(forte);
      item.appendChild(document.createTextNode(par[1] + ": " + lista.map(function (l) { return l.label; }).join(", ")));
      self.plano.appendChild(item);
    });

    var sujo = this.sujo();
    this.aviso.hidden = !sujo;
    this.botaoConfirmar.disabled = sujo || dados.empty;
    if (dados.empty) {
      this.shell.showErrors(null, "Este produto de origem não tem conteúdo cadastrado.");
    }
    this.mostrarPasso("confirm");
  };

  CopyPanel.prototype.copiar = function () {
    var self = this;
    if (!this.escolhido || this.botaoConfirmar.disabled) {
      return;
    }
    this.botaoConfirmar.disabled = true;
    var dados = new FormData();
    dados.append("source_id", String(this.escolhido.id));
    this.shell
      .post(this.urls.copy, dados)
      .then(function (r) {
        if (!r.dados.ok) {
          self.shell.showErrors(null, r.dados.detail || "Não foi possível copiar.");
          self.botaoConfirmar.disabled = false;
          return;
        }
        self.shell.snapshot = null;
        self.table.flash(r.dados.message, true);
        self.shell.close();
        /* A tabela e os formulários do formset precisam nascer com o que foi
           gravado — a mesma recarga que criar e remover idioma já fazem. */
        window.setTimeout(function () {
          window.location.reload();
        }, 900);
      })
      .catch(function () {
        self.shell.showErrors(null, "Não foi possível falar com o servidor.");
        self.botaoConfirmar.disabled = false;
      });
  };

  CopyPanel.prototype.start = function () {
    if (!this.element) {
      return;
    }
    var self = this;
    var abrir = this.root.querySelector("[data-copy-open]");
    if (abrir) {
      abrir.addEventListener("click", function () {
        self.open(abrir);
      });
    }
    this.element.querySelectorAll("[data-copy-cancel]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        self.shell.close();
      });
    });
    this.botaoVoltar.addEventListener("click", function () {
      self.shell.clearErrors();
      self.mostrarPasso("search");
    });
    this.botaoConfirmar.addEventListener("click", function () {
      self.copiar();
    });
    if (this.campoBusca) {
      this.campoBusca.addEventListener("input", function () {
        window.clearTimeout(self.timer);
        var termo = self.campoBusca.value;
        self.timer = window.setTimeout(function () {
          self.buscar(termo);
        }, 250);
      });
      this.campoBusca.addEventListener("keydown", function (evento) {
        if (evento.key === "Enter") {
          evento.preventDefault();  /* Enter aqui não envia o produto */
          window.clearTimeout(self.timer);
          self.buscar(self.campoBusca.value);
        }
      });
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-content-inline]").forEach(function (root) {
      var tabela = new ContentTable(root);
      tabela.start();
      new CopyPanel(root, tabela).start();
    });
  });
})();
