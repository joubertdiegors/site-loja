/* A tela de categorias: a árvore e o modal.

   O que é de tela mora aqui — recolher um galho, marcar linhas, trocar de
   aba, abrir e fechar o diálogo. O que é de dado mora no servidor: **toda**
   gravação é um POST para um endpoint do Admin, com CSRF, e a página recarrega
   em seguida.

   ## Por que recarregar

   Mudar o pai de uma categoria muda a forma da árvore: a linha some de um
   galho e nasce em outro, a indentação dos filhos muda, o resumo do cabeçalho
   muda. Redesenhar isso no navegador seria uma segunda opinião sobre o que
   está no banco — e a primeira vez que as duas divergissem ninguém saberia
   qual acreditar. Recarregar custa uma consulta e diz a verdade.

   A casca do modal (abrir, ESC, foco preso, erros por campo) é a mesma dos
   outros modais do Admin: `jd_modal.js`. Aqui fica só o que é de categoria. */
(function () {
  "use strict";

  /* O Admin serve o `Media` no `<head>`: sem esperar o DOM, a raiz da tela
     ainda não existe quando este arquivo roda. */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }

  function iniciar() {
  var raiz = document.querySelector("[data-cat-root]");
  if (!raiz || !window.JDModal) {
    return;
  }

  var urls = {
    save: raiz.getAttribute("data-url-save"),
    data: raiz.getAttribute("data-url-data"),
    del: raiz.getAttribute("data-url-delete"),
    toggle: raiz.getAttribute("data-url-toggle"),
    bulk: raiz.getAttribute("data-url-bulk")
  };
  var podeMudar = raiz.getAttribute("data-can-change") === "1";

  /* Os endpoints com id vêm com um zero no lugar dele (`.../0/excluir/`): a
     URL é construída pelo `reverse` do Django, e não concatenada aqui. */
  function comId(url, id) {
    return url.replace(/\/0\//, "/" + id + "/");
  }

  function csrf() {
    var campo = raiz.querySelector("input[name=csrfmiddlewaretoken]");
    return campo ? campo.value : "";
  }

  function postar(url, dados) {
    dados.append("csrfmiddlewaretoken", csrf());
    return fetch(url, {
      method: "POST",
      body: dados,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    }).then(function (r) {
      return r.json().then(function (corpo) {
        return { status: r.status, dados: corpo };
      });
    });
  }

  function recarregar() {
    window.location.reload();
  }

  /* ---- a árvore ---------------------------------------------------------- */

  var linhas = Array.prototype.slice.call(raiz.querySelectorAll("[data-cat-row]"));

  function filhosDe(id) {
    return linhas.filter(function (linha) {
      return linha.getAttribute("data-parent") === String(id);
    });
  }

  function esconderDescendentes(linha, esconder) {
    filhosDe(linha.getAttribute("data-id")).forEach(function (filho) {
      filho.hidden = esconder;
      var botao = filho.querySelector("[data-cat-toggle]");
      /* Recolher um galho recolhe o que está dentro dele. Reabrir devolve só
         o primeiro nível: o que estava fechado lá dentro continua fechado. */
      if (esconder) {
        esconderDescendentes(filho, true);
      } else if (botao && botao.getAttribute("aria-expanded") === "true") {
        esconderDescendentes(filho, false);
      }
    });
  }

  linhas.forEach(function (linha) {
    var botao = linha.querySelector("[data-cat-toggle]");
    if (botao && botao.tagName === "BUTTON") {
      botao.addEventListener("click", function () {
        var aberto = botao.getAttribute("aria-expanded") === "true";
        botao.setAttribute("aria-expanded", aberto ? "false" : "true");
        botao.textContent = aberto ? "▸" : "▾";
        esconderDescendentes(linha, aberto);
      });
    }
  });

  /* ---- seleção e ações em massa ------------------------------------------ */

  var marcadores = Array.prototype.slice.call(raiz.querySelectorAll("[data-cat-check]"));
  var rotuloSelecao = raiz.querySelector("[data-cat-selected-label]");
  var todos = raiz.querySelector("[data-cat-all]");

  function selecionados() {
    return marcadores.filter(function (c) { return c.checked; });
  }

  function atualizarSelecao() {
    var n = selecionados().length;
    if (rotuloSelecao) {
      rotuloSelecao.textContent = n ? n + " selecionada(s)" : "";
    }
    if (todos) {
      todos.checked = n > 0 && n === marcadores.length;
      todos.indeterminate = n > 0 && n < marcadores.length;
    }
  }

  marcadores.forEach(function (c) {
    c.addEventListener("change", atualizarSelecao);
  });

  if (todos) {
    todos.addEventListener("change", function () {
      marcadores.forEach(function (c) { c.checked = todos.checked; });
      atualizarSelecao();
    });
  }
  atualizarSelecao();

  var executar = raiz.querySelector("[data-cat-bulk-run]");
  var acaoMassa = raiz.querySelector("[data-cat-bulk]");
  if (executar && acaoMassa) {
    executar.addEventListener("click", function () {
      var acao = acaoMassa.value;
      var ids = selecionados().map(function (c) { return c.value; });
      if (!acao || !ids.length) {
        window.alert("Escolha uma ação e ao menos uma categoria.");
        return;
      }
      if (acao === "delete" && !window.confirm("Excluir " + ids.length + " categoria(s)? A ação não pode ser desfeita.")) {
        return;
      }
      var dados = new FormData();
      dados.append("action", acao);
      ids.forEach(function (id) { dados.append("ids", id); });
      executar.disabled = true;
      postar(urls.bulk, dados)
        .then(function (r) {
          if (!r.dados.ok) {
            window.alert(r.dados.detail || "Não foi possível executar a ação.");
            executar.disabled = false;
            return;
          }
          recarregar();
        })
        .catch(function () {
          window.alert("Não foi possível falar com o servidor.");
          executar.disabled = false;
        });
    });
  }

  /* O filtro de situação envia sozinho: um `select` que exige clicar em
     "Filtrar" depois de escolher é um passo a mais para nada. */
  var status = raiz.querySelector("[data-cat-status]");
  var formulario = raiz.querySelector("[data-cat-filters]");
  if (status && formulario) {
    status.addEventListener("change", function () { formulario.submit(); });
  }

  /* ---- ativar/desativar na lista ----------------------------------------- */

  raiz.querySelectorAll("[data-cat-active]").forEach(function (botao) {
    if (!podeMudar) {
      return;
    }
    botao.addEventListener("click", function () {
      var linha = botao.closest("[data-cat-row]");
      botao.disabled = true;
      postar(comId(urls.toggle, linha.getAttribute("data-id")), new FormData())
        .then(function (r) {
          if (!r.dados.ok) {
            window.alert(r.dados.detail || "Não foi possível alterar.");
            botao.disabled = false;
            return;
          }
          botao.classList.toggle("is-on", r.dados.is_active);
          botao.lastChild.textContent = r.dados.is_active ? "Ativa" : "Inativa";
          botao.disabled = false;
        })
        .catch(function () {
          window.alert("Não foi possível falar com o servidor.");
          botao.disabled = false;
        });
    });
  });

  /* ---- o modal ----------------------------------------------------------- */

  var elemento = raiz.querySelector("[data-cat-modal]");
  if (!elemento) {
    return;
  }

  var campos = {};
  elemento.querySelectorAll("[data-cat-field]").forEach(function (input) {
    campos[input.getAttribute("data-cat-field")] = input;
  });

  var modal = new window.JDModal(elemento, {});
  modal.fields = campos;

  var kicker = elemento.querySelector("[data-cat-kicker]");
  var titulo = elemento.querySelector("[data-cat-title]");
  var verNoSite = elemento.querySelector("[data-cat-view]");
  var botaoExcluir = elemento.querySelector("[data-cat-delete]");
  var botaoSalvar = elemento.querySelector("[data-cat-save]");
  var chave = elemento.querySelector("[data-cat-field=category_id]");
  var interruptor = elemento.querySelector("[data-cat-switch]");
  var ativo = campos.is_active;
  var badgeFilhos = elemento.querySelector("[data-cat-children-badge]");
  var listaFilhos = elemento.querySelector("[data-cat-children]");
  var vazioFilhos = elemento.querySelector("[data-cat-children-empty]");
  var dicaFilhos = elemento.querySelector("[data-cat-children-hint]");
  var addFilho = elemento.querySelector("[data-cat-add-child]");
  var criadoEm = elemento.querySelector("[data-cat-created]");
  var atualizadoEm = elemento.querySelector("[data-cat-updated]");
  var registro = elemento.querySelector("[data-cat-log]");
  var linkHistorico = elemento.querySelector("[data-cat-history]");

  /* abas */
  var abas = Array.prototype.slice.call(elemento.querySelectorAll("[data-cat-tab]"));
  var paineis = Array.prototype.slice.call(elemento.querySelectorAll("[data-cat-panel]"));

  function mostrarAba(id) {
    abas.forEach(function (aba) {
      aba.classList.toggle("is-on", aba.getAttribute("data-cat-tab") === id);
    });
    paineis.forEach(function (painel) {
      painel.hidden = painel.getAttribute("data-cat-panel") !== id;
    });
  }

  abas.forEach(function (aba) {
    aba.addEventListener("click", function () {
      mostrarAba(aba.getAttribute("data-cat-tab"));
    });
  });

  /* o interruptor de «ativa» */
  function pintarInterruptor() {
    interruptor.setAttribute("aria-pressed", ativo.checked ? "true" : "false");
  }

  interruptor.addEventListener("click", function () {
    ativo.checked = !ativo.checked;
    pintarInterruptor();
  });

  /* O nome em português aparece em duas abas — Identificação e Traduções.
     São o mesmo dado: escrever num escreve no outro, e só um vai no POST. */
  elemento.querySelectorAll("[data-cat-sync]").forEach(function (input) {
    input.addEventListener("input", function () {
      var espelho = campos[input.getAttribute("data-cat-sync")];
      if (espelho && espelho !== input) {
        espelho.value = input.value;
      }
    });
  });

  elemento.querySelectorAll("[data-cat-clear]").forEach(function (botao) {
    botao.addEventListener("click", function () {
      var code = botao.getAttribute("data-cat-clear");
      ["name_" + code, "description_" + code].forEach(function (nome) {
        if (campos[nome]) {
          campos[nome].value = "";
        }
      });
      var espelho = campos["name_" + code];
      if (espelho && espelho.hasAttribute("data-cat-sync") && campos.name) {
        campos.name.value = "";
      }
    });
  });

  function limpar() {
    Object.keys(campos).forEach(function (nome) {
      var campo = campos[nome];
      if (campo.type === "checkbox") {
        campo.checked = true;
      } else if (campo.tagName === "SELECT") {
        campo.value = "";
      } else if (nome === "sort_order") {
        campo.value = "0";
      } else {
        campo.value = "";
      }
    });
    pintarInterruptor();
  }

  function preencher(dados) {
    Object.keys(dados.fields || {}).forEach(function (nome) {
      var campo = campos[nome];
      if (!campo) {
        return;
      }
      if (campo.type === "checkbox") {
        campo.checked = !!dados.fields[nome];
      } else {
        campo.value = dados.fields[nome];
      }
    });
    pintarInterruptor();
  }

  function desenharFilhos(filhos, temId) {
    listaFilhos.textContent = "";
    (filhos || []).forEach(function (filho) {
      var linha = document.createElement("div");
      linha.className = "jd-cat-child-grid jd-cat-child-row";

      var alca = document.createElement("span");
      alca.className = "jd-cat-child-handle";
      alca.textContent = "⠿";
      alca.title = "A ordem é o campo «Ordem» de cada subcategoria";

      var link = document.createElement("a");
      link.href = "#";
      link.textContent = filho.name;
      link.addEventListener("click", function (evento) {
        evento.preventDefault();
        abrirEdicao(filho.id);
      });

      var slug = document.createElement("span");
      slug.className = "jd-cat-child-slug";
      slug.textContent = filho.slug;

      var produtos = document.createElement("span");
      produtos.className = "jd-cat-num";
      produtos.textContent = filho.products;

      var selo = document.createElement("button");
      selo.type = "button";
      selo.className = "jd-cat-child-status" + (filho.is_active ? " is-on" : "");
      var ponto = document.createElement("span");
      ponto.className = "jd-cat-dot";
      selo.appendChild(ponto);
      selo.appendChild(document.createTextNode(filho.is_active ? "Ativa" : "Inativa"));
      selo.disabled = !podeMudar;
      selo.addEventListener("click", function () {
        selo.disabled = true;
        postar(comId(urls.toggle, filho.id), new FormData()).then(function (r) {
          if (r.dados.ok) {
            filho.is_active = r.dados.is_active;
            selo.classList.toggle("is-on", r.dados.is_active);
            selo.lastChild.textContent = r.dados.is_active ? "Ativa" : "Inativa";
          }
          selo.disabled = !podeMudar;
        });
      });

      linha.appendChild(alca);
      linha.appendChild(link);
      linha.appendChild(slug);
      linha.appendChild(produtos);
      linha.appendChild(selo);
      listaFilhos.appendChild(linha);
    });

    var quantos = (filhos || []).length;
    badgeFilhos.textContent = String(quantos);
    vazioFilhos.hidden = quantos > 0 || !temId;
    dicaFilhos.textContent = temId
      ? quantos + " subcategoria(s) · a ordem é o campo «Ordem» de cada uma"
      : "Salve a categoria para adicionar filhos";
    addFilho.hidden = !temId;
  }

  function desenharAuditoria(audit) {
    criadoEm.textContent = audit ? audit.created_at : "—";
    atualizadoEm.textContent = audit ? audit.updated_at : "—";
    registro.textContent = "";
    var entradas = (audit && audit.log) || [];
    if (!entradas.length) {
      var vazio = document.createElement("div");
      vazio.className = "jd-cat-empty jd-cat-empty-small";
      vazio.textContent = audit
        ? "Nenhuma alteração registrada para esta categoria."
        : "A auditoria aparece depois de salvar.";
      registro.appendChild(vazio);
    } else {
      entradas.forEach(function (entrada) {
        var linha = document.createElement("div");
        linha.className = "jd-cat-log-row";
        var quando = document.createElement("span");
        quando.textContent = entrada.when;
        var oque = document.createElement("span");
        oque.textContent = entrada.what + (entrada.who ? " — " + entrada.who : "");
        linha.appendChild(quando);
        linha.appendChild(oque);
        registro.appendChild(linha);
      });
    }
    if (linkHistorico) {
      linkHistorico.hidden = !audit;
      if (audit) {
        linkHistorico.href = audit.history_url;
        linkHistorico.textContent = "Ver histórico completo";
      }
    }
  }

  function abrirNova(paiId) {
    limpar();
    if (paiId) {
      campos.parent.value = String(paiId);
    }
    chave.value = "";
    kicker.textContent = "NOVA CATEGORIA";
    titulo.textContent = "Nova categoria";
    verNoSite.hidden = true;
    if (botaoExcluir) {
      botaoExcluir.hidden = true;
    }
    desenharFilhos([], false);
    desenharAuditoria(null);
    mostrarAba("id");
    modal.snapshot = null;
    modal.open(document.activeElement);
  }

  function abrirEdicao(id) {
    fetch(comId(urls.data, id), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    })
      .then(function (r) { return r.json(); })
      .then(function (dados) {
        if (!dados.ok) {
          window.alert(dados.detail || "Não foi possível abrir a categoria.");
          return;
        }
        limpar();
        preencher(dados);
        chave.value = dados.id;
        kicker.textContent = dados.kicker;
        titulo.textContent = dados.title;
        verNoSite.hidden = false;
        verNoSite.href = dados.view_url;
        if (botaoExcluir) {
          botaoExcluir.hidden = false;
        }
        desenharFilhos(dados.children, true);
        desenharAuditoria(dados.audit);
        mostrarAba("id");
        modal.snapshot = null;
        modal.open(document.activeElement);
      })
      .catch(function () {
        window.alert("Não foi possível falar com o servidor.");
      });
  }

  /* Abrir: "+ Nova categoria", "Editar" de cada linha e o nome da linha. */
  var novo = raiz.querySelector("[data-cat-new]");
  if (novo) {
    novo.addEventListener("click", function () { abrirNova(null); });
  }

  raiz.querySelectorAll("[data-cat-edit]").forEach(function (link) {
    link.addEventListener("click", function (evento) {
      /* Sem JavaScript o mesmo link leva à ficha completa: o `href` continua
         lá, e é ele que abre com Ctrl+clique ou no botão do meio. */
      if (evento.metaKey || evento.ctrlKey || evento.shiftKey || evento.button !== 0) {
        return;
      }
      evento.preventDefault();
      abrirEdicao(link.closest("[data-cat-row]").getAttribute("data-id"));
    });
  });

  if (addFilho) {
    addFilho.addEventListener("click", function (evento) {
      evento.preventDefault();
      var id = chave.value;
      if (id) {
        abrirNova(id);
      }
    });
  }

  elemento.querySelectorAll("[data-cat-close]").forEach(function (botao) {
    botao.addEventListener("click", function () { modal.cancel(); });
  });

  botaoSalvar.addEventListener("click", function () {
    var dados = new FormData();
    Object.keys(campos).forEach(function (nome) {
      var campo = campos[nome];
      if (campo.type === "checkbox") {
        if (campo.checked) {
          dados.append(nome, "on");
        }
      } else {
        dados.append(nome, campo.value);
      }
    });
    botaoSalvar.disabled = true;
    postar(urls.save, dados)
      .then(function (r) {
        if (!r.dados.ok) {
          modal.showErrors(r.dados.errors, r.dados.detail);
          botaoSalvar.disabled = false;
          return;
        }
        recarregar();
      })
      .catch(function () {
        modal.showErrors(null, "Não foi possível falar com o servidor. Tente de novo.");
        botaoSalvar.disabled = false;
      });
  });

  if (botaoExcluir) {
    botaoExcluir.addEventListener("click", function () {
      var id = chave.value;
      if (!id || !window.confirm("Excluir a categoria «" + titulo.textContent + "»?")) {
        return;
      }
      botaoExcluir.disabled = true;
      postar(comId(urls.del, id), new FormData())
        .then(function (r) {
          if (!r.dados.ok) {
            modal.showErrors(null, r.dados.detail || "Não foi possível excluir.");
            botaoExcluir.disabled = false;
            return;
          }
          recarregar();
        })
        .catch(function () {
          modal.showErrors(null, "Não foi possível falar com o servidor.");
          botaoExcluir.disabled = false;
        });
    });
  }
  }
})();
