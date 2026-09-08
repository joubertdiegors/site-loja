/* Variantes no cadastro do produto: tabela compacta + modal de edição.

   A tabela mostra só o que identifica e compara uma variante. Os campos de
   edição ficam num modal sobre a página — nunca abaixo da tabela, que era o
   que deixava a página comprida a ponto de ser preciso rolar para achar a
   variante com o preço errado.

   ## Onde o "Salvar" grava

   Na tela de EDIÇÃO o modal grava a variante sozinha, num POST para
   `admin:catalog_product_variant_save`. O servidor valida com o mesmo
   `ProductVariant.clean()` de sempre, recalcula preço/margem com Decimal e
   devolve o que gravou — e é esse valor que volta para os campos e para a
   linha da tabela.

   Na tela de CADASTRO o produto ainda não tem PK: não há a que prender uma
   variante. Lá o modal continua alimentando o formset (os campos são os do
   inline) e o botão diz que a gravação acontece junto com o produto. É a única
   diferença de comportamento, e ela está escrita na tela.

   ## Criar e excluir recarregam a página

   Depois de CRIAR ou EXCLUIR pelo modal, a página é recarregada. Não é
   preguiça: o formset do Django numera os formulários e conta quantos vieram
   do banco (`INITIAL_FORMS`). Uma variante criada por fora passaria a existir
   duas vezes — uma no banco, outra como formulário "novo" — e o próximo
   "Salvar" do produto tentaria inseri-la de novo. Recarregar devolve um
   formset coerente. Editar não recarrega, porque aí o formulário já é o da
   variante certa e os valores no DOM passam a ser os que o servidor gravou.

   ## Cálculo de preço

   Inalterado desde a etapa 9. `pricing_mode` declara qual campo é a entrada e
   só o outro é reescrito — por isso não existe ciclo margem → preço → margem.
   O número da tela é PRÉ-VISUALIZAÇÃO; o que vale é o que o servidor grava.

     custo_total = filamento + energia
     margem (%)  = ((preco - custo) / preco) * 100      <- margem, não markup
     preco       = custo / (1 - margem/100)
*/
(function () {
  "use strict";

  /* Limites iguais aos de pricing.py: 100% seria divisão por zero. */
  var MAX_MARGIN = 99.99;
  var MIN_MARGIN = 0;

  var MOEDA = "€";

  /* Arredondamento comercial (half-up) em duas casas, feito em centavos para
     não herdar o erro de representação do ponto flutuante em `x * 100`. */
  function money(value) {
    if (!isFinite(value)) {
      return null;
    }
    var cents = Math.round(Math.abs(value) * 100 + 1e-6);
    return (value < 0 ? -cents : cents) / 100;
  }

  function parseNumber(raw) {
    if (raw === null || raw === undefined) {
      return null;
    }
    /* O admin aceita vírgula decimal conforme o locale. */
    var texto = String(raw).trim().replace(",", ".");
    if (texto === "") {
      return null;
    }
    var valor = Number(texto);
    return isFinite(valor) ? valor : null;
  }

  function formatMoney(value) {
    if (value === null || !isFinite(value)) {
      return "—";
    }
    return MOEDA + " " + value.toFixed(2).replace(".", ",");
  }

  function marginFromPrice(cost, price) {
    if (price === null || price <= 0 || cost === null) {
      return null; /* margem indefinida sem preço positivo */
    }
    return money(((price - cost) / price) * 100);
  }

  function priceFromMargin(cost, margin) {
    if (cost === null || margin === null) {
      return null;
    }
    if (margin < MIN_MARGIN || margin > MAX_MARGIN) {
      return null; /* fora dos limites: 100% dividiria por zero */
    }
    return money(cost / (1 - margin / 100));
  }

  /* ---- Um modal de variante ---------------------------------------------- */

  var CAMPOS = [
    "sku", "sort_order", "is_active",
    "color", "size", "material",
    "filament_cost", "energy_cost",
    "pricing_mode", "sale_price", "profit_margin",
    "stock_quantity", "allow_backorder",
    "made_to_order", "production_lead_time_days",
    "weight_grams", "print_time",
    "width", "height", "depth", "dimension_unit"
  ];

  function Modal(element, table) {
    this.element = element;
    this.table = table;
    this.row = null;
    this.fields = {};

    var self = this;
    CAMPOS.concat(["id", "DELETE"]).forEach(function (nome) {
      self.fields[nome] = element.querySelector('[name$="-' + nome + '"]');
    });
    /* Etapa 3C: os <select> das opções adicionais (`opt_<id>`), um por opção
       do produto. Entram no mesmo dicionário: Cancelar desfaz, o servidor
       reescreve e o POST leva todos — sem lista fixa, porque a lista é do
       produto, não do código. */
    this.optionFields = [];
    element.querySelectorAll("[data-variant-option]").forEach(function (select) {
      var nome = select.getAttribute("data-variant-option");
      self.fields[nome] = select;
      self.optionFields.push(nome);
    });

    /* A casca compartilhada (jd_modal.js) cuida de abrir, fechar, ESC, foco,
       trava de rolagem, desfazer no Cancelar e pintar erros por campo. Aqui
       fica só o que é de variante. */
    this.shell = new window.JDModal(element, {
      onOpen: function () {
        if (self.removeButton) {
          /* Excluir no servidor só faz sentido para variante que já existe. */
          self.removeButton.hidden = !(self.variantId() && self.table.deleteUrl);
        }
        self.recalculate();
      },
      onClose: function () {
        self.syncRow();
      }
    });
    this.shell.fields = this.fields;

    this.dialog = element.querySelector(".jd-modal-dialog");
    this.titleNode = element.querySelector("[data-variant-modal-title]");
    this.kindNode = element.querySelector("[data-variant-modal-kind]");
    this.activeLabelNode = element.querySelector("[data-variant-active-label]");
    this.saveStayButton = element.querySelector("[data-variant-save-stay]");
    this.totalCostNode = element.querySelector("[data-variant-total-cost]");
    this.profitNode = element.querySelector("[data-variant-profit]");
    this.saveButton = element.querySelector("[data-variant-save]");
    this.removeButton = element.querySelector("[data-variant-remove]");
  }

  Modal.prototype.value = function (nome) {
    var campo = this.fields[nome];
    return campo ? campo.value : "";
  };

  Modal.prototype.checked = function (nome) {
    var campo = this.fields[nome];
    return !!(campo && campo.checked);
  };

  Modal.prototype.label = function (nome) {
    var campo = this.fields[nome];
    if (!campo || campo.tagName !== "SELECT") {
      return campo ? campo.value : "";
    }
    var opcao = campo.options[campo.selectedIndex];
    if (!opcao || !opcao.value) {
      return "";
    }
    return opcao.textContent.trim();
  };

  Modal.prototype.variantId = function () {
    return this.value("id");
  };

  /* ---- cálculo ----------------------------------------------------------- */

  Modal.prototype.totalCost = function () {
    var filamento = parseNumber(this.value("filament_cost")) || 0;
    var energia = parseNumber(this.value("energy_cost")) || 0;
    return money(filamento + energia);
  };

  Modal.prototype.recalculate = function () {
    var custo = this.totalCost();
    var modo = this.value("pricing_mode");

    if (modo === "margin") {
      var margem = parseNumber(this.value("profit_margin"));
      this.write("sale_price", priceFromMargin(custo, margem));
    } else {
      var precoDigitado = parseNumber(this.value("sale_price"));
      this.write("profit_margin", marginFromPrice(custo, precoDigitado));
    }

    if (this.totalCostNode) {
      this.totalCostNode.textContent = formatMoney(custo);
    }
    if (this.profitNode) {
      var venda = parseNumber(this.value("sale_price"));
      this.profitNode.textContent =
        venda === null ? "—" : formatMoney(money(venda - custo));
    }
    this.syncRow();
  };

  Modal.prototype.write = function (nome, valor) {
    var campo = this.fields[nome];
    if (!campo) {
      return;
    }
    campo.value = valor === null ? "" : valor.toFixed(2);
    campo.classList.add("jd-variant-derived-field");
  };

  /* ---- resumo ------------------------------------------------------------ */

  Modal.prototype.status = function () {
    if (this.checked("DELETE")) {
      return { texto: "Será excluída", classe: "jd-badge-danger" };
    }
    if (!this.checked("is_active")) {
      return { texto: "Inativa", classe: "jd-badge-muted" };
    }
    if (this.checked("made_to_order")) {
      return { texto: "Sob encomenda", classe: "jd-badge-warn" };
    }
    if (this.checked("allow_backorder")) {
      return { texto: "Sem estoque OK", classe: "jd-badge-ok" };
    }
    var estoque = parseNumber(this.value("stock_quantity"));
    if (estoque === null || estoque <= 0) {
      return { texto: "Esgotada", classe: "jd-badge-danger" };
    }
    return { texto: "Ativa", classe: "jd-badge-ok" };
  };

  Modal.prototype.summary = function () {
    var estoque = parseNumber(this.value("stock_quantity"));
    var peso = parseNumber(this.value("weight_grams"));
    var dias = parseNumber(this.value("production_lead_time_days"));
    var self = this;
    var opcoes = this.optionFields
      .map(function (nome) { return self.label(nome); })
      .filter(function (texto) { return !!texto; });
    return {
      sku: this.value("sku") || "(sem SKU)",
      color: this.label("color") || "—",
      size: this.value("size") || "—",
      material: this.label("material") || "—",
      options: opcoes.length ? opcoes.join(" · ") : "—",
      weight: peso === null ? "—" : peso.toFixed(0) + " g",
      stock: estoque === null ? "—" : String(estoque),
      price: formatMoney(parseNumber(this.value("sale_price"))),
      production: dias === null || dias === 0 ? "—" : dias + " d",
      status: this.status()
    };
  };

  Modal.prototype.syncRow = function () {
    if (!this.row) {
      return;
    }
    var dados = this.summary();
    this.row.querySelectorAll("[data-cell]").forEach(function (celula) {
      var chave = celula.getAttribute("data-cell");
      if (chave === "status") {
        celula.innerHTML = "";
        var selo = document.createElement("span");
        selo.className = "jd-badge " + dados.status.classe;
        selo.textContent = dados.status.texto;
        celula.appendChild(selo);
        return;
      }
      celula.textContent = dados[chave];
    });
    this.row.classList.toggle("jd-row-deleted", this.checked("DELETE"));
    this.row.classList.toggle("jd-row-error", this.hasErrors());
    /* O cabeçalho do modal: «Editar variante» (ou «Nova variante») em cima,
       o SKU embaixo — é o que diz «estou editando ESTA variante». */
    if (this.kindNode) {
      this.kindNode.textContent = this.variantId() ? "Editar variante" : "Nova variante";
    }
    if (this.titleNode) {
      this.titleNode.textContent = dados.sku;
    }
    if (this.activeLabelNode) {
      this.activeLabelNode.textContent = this.checked("is_active") ? "Ativa" : "Inativa";
    }
  };

  Modal.prototype.hasErrors = function () {
    return !!this.element.querySelector(".errorlist");
  };

  /* ---- abrir e fechar (a casca faz o trabalho) ---------------------------- */

  Modal.prototype.open = function (opener) {
    this.shell.open(opener);
  };

  Modal.prototype.close = function () {
    this.shell.close();
  };

  Modal.prototype.cancel = function () {
    this.shell.cancel();
  };

  Modal.prototype.showErrors = function (errors, detail) {
    this.shell.showErrors(errors, detail);
  };

  /* ---- gravar ------------------------------------------------------------ */

  Modal.prototype.formData = function () {
    var dados = new FormData();
    if (this.variantId()) {
      dados.append("variant_id", this.variantId());
    }
    var self = this;
    CAMPOS.concat(this.optionFields).forEach(function (nome) {
      var campo = self.fields[nome];
      if (!campo) {
        return;
      }
      if (campo.type === "checkbox") {
        if (campo.checked) {
          dados.append(nome, "on");
        }
        return;
      }
      dados.append(nome, campo.value);
    });
    return dados;
  };

  Modal.prototype.applyServerValues = function (payload) {
    var self = this;
    Object.keys(payload.fields || {}).forEach(function (nome) {
      var campo = self.fields[nome];
      if (!campo) {
        return;
      }
      if (campo.type === "checkbox") {
        campo.checked = !!payload.fields[nome];
      } else {
        campo.value = payload.fields[nome];
      }
    });
    if (self.fields.id) {
      self.fields.id.value = payload.id;
    }
  };

  /* `stay`: «Salvar e continuar editando» — grava e mantém o modal aberto.
     Uma variante NOVA precisa recarregar a página de qualquer jeito (o
     formset precisa ficar coerente); nesse caso o modal reabre sozinho
     depois do reload, pelo id que o servidor devolveu (`reopenKey`). */
  Modal.prototype.save = function (stay) {
    if (!this.table.saveUrl) {
      /* Cadastro: o produto ainda não existe. O modal só confirma; quem grava
         é o "Salvar" do produto, com o formset. */
      this.shell.snapshot = null;
      if (!stay) {
        this.close();
      }
      this.table.flash("Variante preparada. Ela será gravada ao salvar o produto.", true);
      return;
    }

    var self = this;
    var botao = stay && this.saveStayButton ? this.saveStayButton : this.saveButton;
    var rotulo = botao.textContent;
    this.saveButton.disabled = true;
    if (this.saveStayButton) {
      this.saveStayButton.disabled = true;
    }
    botao.textContent = "Salvando…";

    this.shell
      .post(this.table.saveUrl, this.formData())
      .then(function (r) {
        if (!r.dados.ok) {
          self.showErrors(r.dados.errors, r.dados.detail);
          return;
        }
        self.applyServerValues(r.dados);
        self.shell.snapshot = null;

        if (r.dados.created) {
          /* Criou por fora do formset: recarregar devolve um formset coerente
             (ver o comentário no topo do arquivo). */
          if (stay) {
            self.table.rememberReopen(r.dados.id);
          }
          self.close();
          self.table.flash(r.dados.message, true);
          self.table.reload();
          return;
        }
        self.recalculate();
        if (stay) {
          self.shell.snapshot = self.shell.capture();
          self.shell.clearErrors();
        } else {
          self.close();
        }
        self.table.flash(r.dados.message, true);
      })
      .catch(function () {
        self.showErrors(null, "Não foi possível falar com o servidor. Tente de novo.");
      })
      .then(function () {
        self.saveButton.disabled = false;
        if (self.saveStayButton) {
          self.saveStayButton.disabled = false;
        }
        botao.textContent = rotulo;
      });
  };

  Modal.prototype.remove = function () {
    if (!this.table.deleteUrl || !this.variantId()) {
      return;
    }
    var sku = this.value("sku") || "esta variante";
    if (!window.confirm("Excluir " + sku + "? Esta ação não pode ser desfeita.")) {
      return;
    }

    var self = this;
    var url = this.table.deleteUrl.replace(/0\/excluir\/$/, this.variantId() + "/excluir/");

    this.removeButton.disabled = true;
    this.shell
      .post(url, new FormData())
      .then(function (envelope) {
        var r = envelope.dados;
        if (!r.ok) {
          self.showErrors(null, r.detail || "Não foi possível excluir.");
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

  var COLUNAS = [
    "sku", "color", "size", "material", "options",
    "weight", "stock", "price", "production", "status"
  ];
  var NUMERICAS = ["weight", "stock", "price", "production"];

  function VariantTable(root) {
    this.root = root;
    this.tbody = root.querySelector("[data-variant-rows]");
    this.emptyNote = root.querySelector("[data-variant-empty]");
    this.flashNode = root.querySelector("[data-variant-flash]");
    this.addButton = root.querySelector("[data-variant-add]");
    this.template = root.querySelector("[data-variant-template]");
    this.prefix = this.root.id.replace(/-group$/, "");
    this.totalForms = document.getElementById("id_" + this.prefix + "-TOTAL_FORMS");
    this.saveUrl = root.getAttribute("data-variant-save-url") || "";
    this.deleteUrl = root.getAttribute("data-variant-delete-url") || "";
    this.countNode = root.querySelector("[data-variant-count]");
    this.modals = [];
    this.reopenKey = "jd-variant-reopen:" + window.location.pathname;
  }

  /* Qual variante reabrir depois do reload («Salvar e continuar editando»
     numa variante nova). Fica na sessão do navegador — só nesta aba. */
  VariantTable.prototype.rememberReopen = function (id) {
    try { window.sessionStorage.setItem(this.reopenKey, String(id)); } catch (erro) { /* modo privado */ }
  };

  VariantTable.prototype.takeReopen = function () {
    try {
      var id = window.sessionStorage.getItem(this.reopenKey);
      window.sessionStorage.removeItem(this.reopenKey);
      return id;
    } catch (erro) {
      return null;
    }
  };

  VariantTable.prototype.flash = function (mensagem, ok) {
    if (!this.flashNode) {
      return;
    }
    this.flashNode.textContent = mensagem;
    this.flashNode.className = "jd-variant-flash " + (ok ? "jd-flash-ok" : "jd-flash-erro");
    this.flashNode.hidden = false;
  };

  VariantTable.prototype.reload = function () {
    /* Um instante para a mensagem ser lida antes de a página trocar. */
    window.setTimeout(function () {
      window.location.reload();
    }, 700);
  };

  VariantTable.prototype.makeRow = function (modal) {
    var linha = document.createElement("tr");
    linha.className = "jd-variant-row";

    COLUNAS.forEach(function (chave) {
      var celula = document.createElement("td");
      celula.setAttribute("data-cell", chave);
      if (NUMERICAS.indexOf(chave) !== -1) {
        celula.className = "jd-num";
      }
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

  VariantTable.prototype.register = function (element) {
    var modal = new Modal(element, this);
    modal.row = this.makeRow(modal);
    this.tbody.appendChild(modal.row);
    this.modals.push(modal);

    var self = this;
    element.addEventListener("input", function () {
      modal.recalculate();
    });
    element.addEventListener("change", function (evento) {
      modal.recalculate();
      if (evento.target === modal.fields.DELETE) {
        self.refreshEmptyNote();
      }
    });

    /* Digitar num dos dois declara qual deles é a entrada. É isto que impede
       o ciclo: o campo que o administrador está editando nunca é reescrito. */
    ["sale_price", "profit_margin"].forEach(function (nome) {
      var campo = modal.fields[nome];
      if (!campo || !modal.fields.pricing_mode) {
        return;
      }
      campo.addEventListener("input", function () {
        var modo = nome === "sale_price" ? "price" : "margin";
        if (modal.fields.pricing_mode.value !== modo) {
          modal.fields.pricing_mode.value = modo;
        }
        campo.classList.remove("jd-variant-derived-field");
      });
    });

    /* Só o Cancelar e o X. O fundo escuro não fecha — ver `jd_modal.js`. */
    element.querySelectorAll("[data-variant-cancel]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.cancel();
      });
    });
    element.querySelectorAll("[data-variant-save]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.save(false);
      });
    });
    element.querySelectorAll("[data-variant-save-stay]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.save(true);
      });
    });
    element.querySelectorAll("[data-variant-remove]").forEach(function (botao) {
      botao.addEventListener("click", function () {
        modal.remove();
      });
    });

    modal.recalculate();

    /* Modal com erro de validação do servidor abre sozinho: o administrador
       não pode ter que caçar em qual das vinte variantes o servidor reclamou. */
    if (modal.hasErrors()) {
      modal.open(null);
    }
    return modal;
  };

  VariantTable.prototype.refreshEmptyNote = function () {
    var vivas = this.modals.filter(function (modal) {
      return !modal.checked("DELETE");
    });
    if (this.emptyNote) {
      this.emptyNote.hidden = vivas.length > 0;
    }
    /* «3 variantes cadastradas», no cabeçalho da seção. */
    if (this.countNode) {
      this.countNode.textContent =
        vivas.length === 1 ? "1 variante cadastrada" : vivas.length + " variantes cadastradas";
    }
  };

  VariantTable.prototype.add = function (opener) {
    if (!this.template || !this.totalForms) {
      return;
    }
    var indice = parseInt(this.totalForms.value, 10) || 0;
    var novo = this.template.cloneNode(true);
    novo.removeAttribute("data-variant-template");

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
    if (modal.fields.sku && !modal.fields.sku.value) {
      modal.fields.sku.value = this.nextSku();
      modal.syncRow();
    }
    this.refreshEmptyNote();
    modal.open(opener || this.addButton);
  };

  /* PRODUTO-V01, V02…: o servidor manda a próxima sequência livre no banco
     (`data-variant-sku-next`); as variantes ainda não gravadas desta tela
     contam também, para dois cliques em "Adicionar" não sugerirem o mesmo.
     No cadastro (produto sem PK) a base é o SKU digitado no formulário. */
  VariantTable.prototype.nextSku = function () {
    var servidor = this.root.getAttribute("data-variant-sku-next") || "";
    var campoSku = document.getElementById("id_sku");
    var base = servidor.replace(/\d+$/, "");
    if (!base) {
      var produto = campoSku ? campoSku.value.trim().toUpperCase() : "";
      if (!produto) {
        return "";
      }
      base = produto + "-V";
    }
    var maior = 0;
    var m = servidor.match(/(\d+)$/);
    if (m) {
      maior = parseInt(m[1], 10) - 1;
    }
    this.modals.forEach(function (modal) {
      var valor = modal.fields.sku ? modal.fields.sku.value.trim().toUpperCase() : "";
      if (valor.indexOf(base) === 0) {
        var n = parseInt(valor.slice(base.length), 10);
        if (!isNaN(n) && n > maior) {
          maior = n;
        }
      }
    });
    var proximo = String(maior + 1);
    return base + (proximo.length < 2 ? "0" + proximo : proximo);
  };

  VariantTable.prototype.start = function () {
    var self = this;
    this.root.querySelectorAll("[data-variant-modal]").forEach(function (element) {
      if (element.hasAttribute("data-variant-template")) {
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

    /* Voltou do reload de um «Salvar e continuar editando»: reabre a variante. */
    var reabrir = this.takeReopen();
    if (reabrir) {
      var alvo = this.modals.filter(function (modal) {
        return modal.variantId() === String(reabrir);
      })[0];
      if (alvo) {
        alvo.open(null);
      }
    }
  };

  /* O ESC e a prisão do Tab vivem em `jd_modal.js`, para o CONTEÚDO e as
     VARIANTES não terem duas respostas para a mesma tecla. */
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-variant-inline]").forEach(function (root) {
      new VariantTable(root).start();
    });
  });
})();
