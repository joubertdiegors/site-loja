/* JD PRINT — interações da vitrine.
 *
 * Três comportamentos pequenos, sem biblioteca: menu do celular, fechamento
 * dos menus suspensos e as setas do carrossel. Tudo funciona sem JavaScript
 * (o menu <details> abre nativamente e o carrossel rola com o dedo ou com o
 * teclado); este arquivo só melhora a experiência.
 */
(function () {
  "use strict";

  /* ---- Menu do celular ------------------------------------------------ */
  function setupMobileMenu() {
    var toggle = document.querySelector("[data-menu-toggle]");
    var panel = document.querySelector("[data-menu-panel]");
    if (!toggle || !panel) {
      return;
    }

    toggle.addEventListener("click", function () {
      var isOpen = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!isOpen));
      panel.classList.toggle("hidden", isOpen);

      var openIcon = toggle.querySelector('[data-menu-icon="open"]');
      var closeIcon = toggle.querySelector('[data-menu-icon="close"]');
      if (openIcon && closeIcon) {
        openIcon.classList.toggle("hidden", !isOpen);
        closeIcon.classList.toggle("hidden", isOpen);
      }
    });
  }

  /* ---- Menus suspensos (<details>) ------------------------------------ */
  function setupDropdowns() {
    var dropdowns = document.querySelectorAll("[data-dropdown]");
    if (!dropdowns.length) {
      return;
    }

    document.addEventListener("click", function (event) {
      dropdowns.forEach(function (dropdown) {
        if (dropdown.open && !dropdown.contains(event.target)) {
          dropdown.open = false;
        }
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") {
        return;
      }
      dropdowns.forEach(function (dropdown) {
        if (dropdown.open) {
          dropdown.open = false;
          var summary = dropdown.querySelector("summary");
          if (summary) {
            summary.focus();
          }
        }
      });
    });
  }

  /* ---- Carrossel ------------------------------------------------------ */
  function setupCarousels() {
    document.querySelectorAll("[data-carousel]").forEach(function (carousel) {
      var track = carousel.querySelector("[data-carousel-track]");
      if (!track) {
        return;
      }

      var section = carousel.closest("section");
      var previous = section && section.querySelector("[data-carousel-prev]");
      var next = section && section.querySelector("[data-carousel-next]");

      function scrollBy(direction) {
        var isRtl = document.documentElement.dir === "rtl";
        var amount = track.clientWidth * 0.85 * direction * (isRtl ? -1 : 1);
        track.scrollBy({ left: amount, behavior: "smooth" });
      }

      function refreshControls() {
        if (!previous || !next) {
          return;
        }
        var maxScroll = track.scrollWidth - track.clientWidth - 1;
        var position = Math.abs(track.scrollLeft);
        previous.disabled = position <= 0;
        next.disabled = position >= maxScroll;
        [previous, next].forEach(function (button) {
          button.classList.toggle("opacity-40", button.disabled);
        });
      }

      if (previous) {
        previous.addEventListener("click", function () {
          scrollBy(-1);
        });
      }
      if (next) {
        next.addEventListener("click", function () {
          scrollBy(1);
        });
      }

      track.addEventListener("scroll", refreshControls, { passive: true });
      window.addEventListener("resize", refreshControls);
      refreshControls();
    });
  }

  /* ---- Carrossel de banners --------------------------------------------- */
  /* Uma camada em volta dos heros da Home. Só existe no HTML quando há dois
     ou mais banners ativos; com um, o template nem o desenha. Tudo o que é
     configuração — rotação, intervalo, pausas — vem dos `data-*` que o
     cadastro (HOME › CARROSSEL DE BANNERS) escreveu: nenhum número aqui.

     Anterior/próximo, indicadores, ← → com o foco dentro do carrossel e o
     deslize no celular fazem a mesma coisa: `go()`. A rotação automática só
     roda para quem não pediu menos movimento ao navegador e para na hora em
     que a aba deixa de estar visível.

     Idempotente: `data-carousel-ready` impede um segundo conjunto de
     ouvintes se a Home for trocada pelo HTMX e o setup rodar de novo. */
  function setupBannerCarousels() {
    document.querySelectorAll("[data-banner-carousel]").forEach(function (root) {
      if (root.hasAttribute("data-banner-ready")) {
        return;
      }
      root.setAttribute("data-banner-ready", "");

      var slides = Array.prototype.slice.call(root.querySelectorAll("[data-banner-slide]"));
      if (slides.length < 2) {
        return;
      }
      var dots = Array.prototype.slice.call(root.querySelectorAll("[data-banner-dot]"));
      var track = root.querySelector("[data-banner-track]");
      var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      var autoplay = root.getAttribute("data-autoplay") === "true" && !reduceMotion;
      var interval = Math.max(1000, Number(root.getAttribute("data-interval")) || 5000);
      var pauseOnHover = root.getAttribute("data-pause-hover") === "true";
      var pauseOnInteraction = root.getAttribute("data-pause-interaction") === "true";

      var current = Math.max(0, slides.findIndex(function (slide) {
        return slide.hasAttribute("data-active");
      }));
      var timer = null;
      var resumeTimer = null;
      var hovering = false;
      /* Até quando a rotação fica em pausa depois de uma interação. O mouse
         sair do carrossel não pode atropelar essa espera. */
      var pausedUntil = 0;

      function go(index) {
        var next = (index + slides.length) % slides.length;
        if (next === current) {
          return;
        }
        slides[current].removeAttribute("data-active");
        slides[current].setAttribute("inert", "");
        slides[next].setAttribute("data-active", "");
        slides[next].removeAttribute("inert");
        dots.forEach(function (dot, i) {
          if (i === next) {
            dot.setAttribute("aria-current", "true");
          } else {
            dot.removeAttribute("aria-current");
          }
        });
        current = next;
      }

      /* Enquanto roda sozinho, a região não anuncia cada troca — um leitor de
         tela seria interrompido a cada cinco segundos. Parado, anuncia. */
      function announce(on) {
        if (track) {
          track.setAttribute("aria-live", on ? "polite" : "off");
        }
      }

      function start() {
        if (!autoplay || timer || hovering || document.hidden) {
          return;
        }
        var wait = pausedUntil - Date.now();
        if (wait > 0) {
          window.clearTimeout(resumeTimer);
          resumeTimer = window.setTimeout(start, wait);
          return;
        }
        announce(false);
        timer = window.setInterval(function () {
          go(current + 1);
        }, interval);
      }

      function stop() {
        if (timer) {
          window.clearInterval(timer);
          timer = null;
        }
        announce(true);
      }

      /* Depois de uma interação a rotação espera dois intervalos e volta —
         se o cadastro pediu a pausa; senão só reinicia a contagem. */
      function interacted() {
        if (!autoplay) {
          return;
        }
        stop();
        pausedUntil = Date.now() + (pauseOnInteraction ? interval * 2 : interval);
        window.clearTimeout(resumeTimer);
        resumeTimer = window.setTimeout(start, pausedUntil - Date.now());
      }

      var previous = root.querySelector("[data-banner-prev]");
      var next = root.querySelector("[data-banner-next]");
      if (previous) {
        previous.addEventListener("click", function () { go(current - 1); interacted(); });
      }
      if (next) {
        next.addEventListener("click", function () { go(current + 1); interacted(); });
      }
      dots.forEach(function (dot, i) {
        dot.addEventListener("click", function () { go(i); interacted(); });
      });

      root.addEventListener("keydown", function (event) {
        var rtl = document.documentElement.dir === "rtl";
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          go(current + (rtl ? 1 : -1));
          interacted();
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          go(current + (rtl ? -1 : 1));
          interacted();
        }
      });

      /* Deslize: 40px na horizontal, mais horizontal que vertical, para não
         confundir com a rolagem da página. */
      var startX = 0, startY = 0, tracking = false;
      root.addEventListener("pointerdown", function (event) {
        if (event.pointerType === "mouse") {
          return;
        }
        tracking = true;
        startX = event.clientX;
        startY = event.clientY;
      }, { passive: true });
      root.addEventListener("pointerup", function (event) {
        if (!tracking) {
          return;
        }
        tracking = false;
        var dx = event.clientX - startX;
        var dy = event.clientY - startY;
        if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy)) {
          return;
        }
        go(current + (dx < 0 ? 1 : -1));
        interacted();
      }, { passive: true });
      root.addEventListener("pointercancel", function () { tracking = false; });

      if (pauseOnHover) {
        root.addEventListener("mouseenter", function () { hovering = true; stop(); });
        root.addEventListener("mouseleave", function () { hovering = false; start(); });
        root.addEventListener("focusin", function () { hovering = true; stop(); });
        root.addEventListener("focusout", function (event) {
          if (!root.contains(event.relatedTarget)) {
            hovering = false;
            start();
          }
        });
      }

      document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
          stop();
        } else {
          start();
        }
      });

      announce(true);
      start();
    });
  }

  /* ---- Filtros do catálogo -------------------------------------------- */
  /* A gaveta do celular é um `popover` nativo: abre, fecha, prende o foco e
     responde ao Esc sem uma linha daqui. O que o navegador não faz sozinho é
     fechá-la quando a janela cresce e a lateral volta a ser coluna — ficaria
     uma gaveta aberta por cima de uma coluna que já está na tela. */
  function setupShopFilters() {
    if (!window.matchMedia || !("hidePopover" in HTMLElement.prototype)) {
      return;
    }

    var wide = window.matchMedia("(min-width: 1101px)");
    wide.addEventListener("change", function () {
      if (!wide.matches) {
        return;
      }
      document.querySelectorAll(".catalog-aside:popover-open").forEach(function (aside) {
        aside.hidePopover();
      });
    });
  }

  /* ---- HTMX ----------------------------------------------------------- */
  /* Depois de trocar a grade de produtos, leva a página de volta ao topo da
     lista — senão o visitante fica olhando para o rodapé — e põe o foco no
     título: o link que foi clicado pode ter sido trocado junto (a lateral
     volta na mesma resposta), e um foco perdido volta para o começo da página. */
  function setupHtmx() {
    document.body.addEventListener("htmx:afterSwap", function (event) {
      if (event.target && event.target.id === "shop-results") {
        var heading = document.querySelector("h1");
        if (heading) {
          heading.scrollIntoView({ behavior: "smooth", block: "start" });
          if (!heading.hasAttribute("tabindex")) {
            heading.setAttribute("tabindex", "-1");
          }
          heading.focus({ preventScroll: true });
        }
        setupCarousels();
      }
    });
  }

  /* ---- Gaveta do carrinho --------------------------------------------- */
  /* A gaveta existe em todas as páginas, fechada e `inert` (fora da ordem de
     foco). Sem JavaScript o ícone do carrinho continua sendo um link para
     /carrinho/ — aqui só interceptamos o clique. */
  function setupCartDrawer() {
    var drawer = document.querySelector("[data-cart-drawer]");
    if (!drawer) {
      return;
    }

    var panel = drawer.querySelector("[data-cart-panel]");
    var overlay = drawer.querySelector("[data-cart-overlay]");
    var opener = null;

    function focusable() {
      return Array.prototype.filter.call(
        drawer.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
        ),
        function (node) {
          return node.offsetParent !== null;
        }
      );
    }

    function open(trigger) {
      opener = trigger || document.querySelector("[data-cart-open]");
      drawer.removeAttribute("inert");
      drawer.classList.remove("pointer-events-none");
      panel.classList.remove("translate-x-full", "rtl:-translate-x-full");
      overlay.classList.remove("opacity-0");
      document.documentElement.classList.add("overflow-hidden");

      document.querySelectorAll("[data-cart-open]").forEach(function (node) {
        node.setAttribute("aria-expanded", "true");
      });

      var first = focusable()[0];
      if (first) {
        first.focus();
      }
    }

    function close() {
      panel.classList.add("translate-x-full", "rtl:-translate-x-full");
      overlay.classList.add("opacity-0");
      drawer.classList.add("pointer-events-none");
      drawer.setAttribute("inert", "");
      document.documentElement.classList.remove("overflow-hidden");

      document.querySelectorAll("[data-cart-open]").forEach(function (node) {
        node.setAttribute("aria-expanded", "false");
      });

      if (opener && document.contains(opener)) {
        opener.focus();
      }
    }

    function isOpen() {
      return !drawer.hasAttribute("inert");
    }

    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-cart-open]");
      if (trigger) {
        event.preventDefault();
        open(trigger);
        return;
      }
      if (event.target.closest("[data-cart-close]") && isOpen()) {
        var link = event.target.closest("a[href]");
        if (!link) {
          event.preventDefault();
        }
        close();
      }
    });

    overlay.addEventListener("click", close);

    document.addEventListener("keydown", function (event) {
      if (!isOpen()) {
        return;
      }
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      /* Prende o Tab dentro da gaveta enquanto ela está aberta. */
      var nodes = focusable();
      if (!nodes.length) {
        return;
      }
      var first = nodes[0];
      var last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    /* O servidor pede a abertura pelo cabeçalho HX-Trigger depois de adicionar. */
    document.body.addEventListener("jd:cart-open", function () {
      open(document.querySelector("[data-cart-open]"));
    });
  }

  /* ---- Galeria do produto ------------------------------------------------
     Uma imagem principal, miniaturas e uma visualização ampliada.

     A galeria expõe `window.jdGallery` porque o seletor de variantes também
     precisa trocar a imagem principal (quando a variante tem foto vinculada).
     Dois lugares escrevendo no mesmo `innerHTML` sem se falarem acabariam com
     a foto de uma variante sobrevivendo à troca para outra.

     `object-contain` e não `object-cover`: a foto do produto não pode ser
     cortada. A área mantém proporção quadrada para a grade não pular de
     altura, e a imagem se ajusta dentro dela. */
  function setupGallery() {
    var gallery = document.querySelector("[data-gallery]");
    if (!gallery) {
      window.jdGallery = null;
      return;
    }

    var main = gallery.querySelector("[data-gallery-main]");
    var thumbs = gallery.querySelectorAll("[data-gallery-thumb]");
    if (!main) {
      window.jdGallery = null;
      return;
    }

    /* A foto com que a página abriu. É para cá que se volta quando a variante
       escolhida não tem foto própria. */
    var padrao = {
      url: main.getAttribute("data-default-url") || "",
      alt: main.getAttribute("data-default-alt") || "",
      type: main.getAttribute("data-default-type") || "IMAGE"
    };

    /* As mídias da galeria, na ordem — a navegação da lupa anda por elas. */
    var itens = [];
    thumbs.forEach(function (thumb) {
      itens.push({
        url: thumb.getAttribute("href"),
        alt: thumb.getAttribute("data-alt") || "",
        type: thumb.getAttribute("data-media-type") || "IMAGE"
      });
    });
    if (!itens.length && padrao.url) {
      itens.push(padrao);
    }

    function render(item) {
      if (!item || !item.url) {
        return;
      }
      if (item.type === "VIDEO") {
        main.innerHTML =
          '<video src="' + item.url + '" controls playsinline' +
          ' class="h-full w-full object-contain"></video>';
      } else {
        main.innerHTML =
          '<img src="' + item.url + '" alt="' + String(item.alt).replace(/"/g, "&quot;") +
          '" class="h-full w-full object-contain">';
      }
      main.setAttribute("data-current-url", item.url);
      main.setAttribute("data-current-type", item.type);
      main.setAttribute("data-current-alt", item.alt || "");
    }

    function highlight(url) {
      thumbs.forEach(function (other) {
        other.classList.toggle("product-thumb-on", other.getAttribute("href") === url);
      });
    }

    thumbs.forEach(function (thumb) {
      thumb.addEventListener("click", function (event) {
        event.preventDefault();
        render({
          url: thumb.getAttribute("href"),
          alt: thumb.getAttribute("data-alt") || "",
          type: thumb.getAttribute("data-media-type") || "IMAGE"
        });
        highlight(thumb.getAttribute("href"));
      });
    });

    /* ---- lupa ------------------------------------------------------------
       Abre a imagem inteira sobre a página. `max-w`/`max-h` na viewport, não
       tamanho fixo: uma foto de 4000px não pode empurrar a tela, e uma de
       300px não pode ser esticada. */
    var lightbox = document.querySelector("[data-lightbox]");
    var indice = 0;

    function abrirLupa(url) {
      if (!lightbox) {
        return;
      }
      indice = Math.max(0, itens.findIndex(function (item) {
        return item.url === url;
      }));
      mostrarNaLupa();
      lightbox.hidden = false;
      document.body.classList.add("jd-lightbox-lock");
      var fechar = lightbox.querySelector("[data-lightbox-close]");
      if (fechar) {
        fechar.focus();
      }
    }

    function fecharLupa() {
      if (!lightbox) {
        return;
      }
      lightbox.hidden = true;
      document.body.classList.remove("jd-lightbox-lock");
      var atual = main.querySelector("img, video");
      if (atual) {
        main.focus ? main.focus() : null;
      }
    }

    function mostrarNaLupa() {
      var alvo = lightbox.querySelector("[data-lightbox-media]");
      var contador = lightbox.querySelector("[data-lightbox-counter]");
      var item = itens[indice];
      if (!alvo || !item) {
        return;
      }
      if (item.type === "VIDEO") {
        alvo.innerHTML =
          '<video src="' + item.url + '" controls playsinline autoplay' +
          ' class="jd-lightbox-media"></video>';
      } else {
        alvo.innerHTML =
          '<img src="' + item.url + '" alt="' + String(item.alt).replace(/"/g, "&quot;") +
          '" class="jd-lightbox-media">';
      }
      if (contador) {
        contador.textContent = itens.length > 1 ? indice + 1 + " / " + itens.length : "";
        contador.hidden = itens.length < 2;
      }
      lightbox.querySelectorAll("[data-lightbox-prev], [data-lightbox-next]").forEach(
        function (botao) {
          botao.hidden = itens.length < 2;
        }
      );
    }

    function andar(passo) {
      if (itens.length < 2) {
        return;
      }
      indice = (indice + passo + itens.length) % itens.length;
      mostrarNaLupa();
    }

    if (lightbox) {
      main.addEventListener("click", function () {
        abrirLupa(main.getAttribute("data-current-url") || padrao.url);
      });
      main.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          abrirLupa(main.getAttribute("data-current-url") || padrao.url);
        }
      });

      lightbox.querySelectorAll("[data-lightbox-close], [data-lightbox-backdrop]").forEach(
        function (node) {
          node.addEventListener("click", fecharLupa);
        }
      );
      var anterior = lightbox.querySelector("[data-lightbox-prev]");
      var proximo = lightbox.querySelector("[data-lightbox-next]");
      if (anterior) {
        anterior.addEventListener("click", function () {
          andar(-1);
        });
      }
      if (proximo) {
        proximo.addEventListener("click", function () {
          andar(1);
        });
      }

      document.addEventListener("keydown", function (event) {
        if (lightbox.hidden) {
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          fecharLupa();
        } else if (event.key === "ArrowLeft") {
          andar(-1);
        } else if (event.key === "ArrowRight") {
          andar(1);
        }
      });
    }

    /* O contrato com o seletor de variantes. */
    window.jdGallery = {
      /* Foto da variante escolhida; sem `url`, volta para a foto de abertura. */
      showVariantMedia: function (url, alt) {
        if (url) {
          render({ url: url, alt: alt, type: "IMAGE" });
          highlight(url);
        } else {
          render(padrao);
          highlight(padrao.url);
        }
      }
    };

    render(padrao);
    highlight(padrao.url);
  }

  /* ---- Seletor de variantes --------------------------------------------- */
  /* Uma matriz de combinações, não três listas independentes.

     ## A regra

     O eixo em que o cliente **acabou de clicar tem prioridade**. O sistema
     procura, entre as variantes que existem de verdade, a que melhor atende
     essa escolha — preservando o que der dos outros eixos — e passa a seleção
     inteira para ela.

         Preto + 25 cm, clica em "30 cm"
             -> só existe Branco + 30 cm
             -> a seleção vira Branco + 30 cm

     Uma opção incompatível com a seleção atual fica **riscada, e clicável**.
     Riscada porque a combinação atual + ela não existe; clicável porque ela
     existe no catálogo, só com outra cor — e é o clique que descobre qual.

     Antes ela era `disabled`, e isso criava um beco: a partir de Preto+25 não
     havia clique nenhum que levasse a Branco+30, porque 30 cm estava desligado
     e um radio não se desmarca. A saída da vez foi um "eixo livre" que ficava
     inteiro; a regra abaixo dispensa esse remendo, porque nada trava.

     ## Nada de combinação inventada

     A seleção nunca passa por um estado intermediário inválido: o clique não
     "marca 30 cm e depois conserta", ele **calcula a variante final** e marca
     todos os eixos de uma vez. Preto+30 não existe nem por um instante.

     ## Genérico por construção

     Nada aqui sabe o que é cor ou tamanho. Os eixos vêm do DOM e as
     combinações, do catálogo — acrescentar "acabamento" não muda uma linha.

     Nada disto é autoridade: o `<select>` é quem manda o `variant_id`, e o
     servidor confere a combinação de novo (ver `AddToCartForm.clean`). */
  function setupVariants() {
    var form = document.querySelector("[data-add-to-cart]");
    if (!form) {
      return;
    }

    /* Produto de opção única não tem seletor nem payload: a variante já vai
       num campo oculto e não há nada para trocar na tela. */
    var select = form.querySelector("[data-variant-select]");
    var dataNode = document.getElementById("variant-data");
    if (!select || !dataNode) {
      return;
    }

    var variants = [];
    try {
      variants = JSON.parse(dataNode.textContent);
    } catch (error) {
      return;
    }
    if (!variants.length) {
      return;
    }

    var wrapper = form.querySelector("[data-variant-select-wrapper]");
    var groupsBox = form.querySelector("[data-variant-groups]");
    var groups = form.querySelectorAll("[data-variant-group]");
    var message = form.querySelector("[data-variant-message]");

    /* Os botões só entram em cena aqui: sem JavaScript o <select> é a
       interface inteira, e é ele que garante uma combinação existente. */
    if (groups.length && groupsBox) {
      groupsBox.hidden = false;
      wrapper.classList.add("sr-only");
    }

    var price = document.querySelector("[data-price]");
    /* Na página, e não no formulário: a linha de compra (quantidade, botão,
       coração) fica fora dele e aponta para ele por `form="add-to-cart"`. */
    var addButton = document.querySelector("[data-add-button]");
    var quantityInput = document.querySelector("[data-quantity-input]");
    var stockState = document.querySelector("[data-stock-state]");

    /* Mesmos selos que o template usa no primeiro desenho. */
    var STOCK_BADGE = {
      made_to_order: "product-tag product-tag-warning",
      out: "product-tag product-tag-danger",
      low: "product-tag product-tag-warning",
      in: "product-tag product-tag-stock"
    };

    var axes = [];
    groups.forEach(function (group) {
      axes.push(group.getAttribute("data-variant-group"));
    });

    function inputsOf(axis) {
      var group = form.querySelector('[data-variant-group="' + axis + '"]');
      return group ? group.querySelectorAll("[data-variant-option]") : [];
    }

    function selection() {
      var chosen = {};
      axes.forEach(function (axis) {
        var group = form.querySelector('[data-variant-group="' + axis + '"]');
        var checked = group ? group.querySelector("input:checked") : null;
        chosen[axis] = checked ? checked.value : "";
      });
      return chosen;
    }

    /* ---- a escolha -------------------------------------------------------

       A variante que melhor atende "este eixo com este valor", dado o que já
       estava escolhido nos outros.

       Critério, nesta ordem:
         1. tem que ter o valor clicado no eixo clicado — é a prioridade;
         2. concorda com o maior número possível dos outros eixos;
         3. no empate, a que dá para comprar;
         4. persistindo o empate, a ordem do catálogo.

       O peso `* 2` garante que concordar com um eixo a mais sempre vence estar
       disponível: preservar a escolha do cliente importa mais do que oferecer
       algo em estoque que ele não pediu. */
    function bestVariant(priorityAxis, priorityValue, chosen) {
      var melhor = null;
      var melhorNota = -1;

      variants.forEach(function (variant) {
        if (String(variant[priorityAxis]) !== priorityValue) {
          return;
        }
        var acordo = 0;
        axes.forEach(function (axis) {
          if (axis === priorityAxis) {
            return;
          }
          if (chosen[axis] && String(variant[axis]) === chosen[axis]) {
            acordo += 1;
          }
        });
        var nota = acordo * 2 + (variant.available ? 1 : 0);
        if (nota > melhorNota) {
          melhorNota = nota;
          melhor = variant;
        }
      });

      return melhor;
    }

    /* Existe variante com este valor neste eixo **mais** o que está escolhido
       nos outros? É isto que decide o risco. */
    function fitsCurrent(axis, value, chosen) {
      return variants.some(function (variant) {
        if (String(variant[axis]) !== value) {
          return false;
        }
        return axes.every(function (outro) {
          if (outro === axis || !chosen[outro]) {
            return true;
          }
          return String(variant[outro]) === chosen[outro];
        });
      });
    }

    /* ---- a tela ---------------------------------------------------------- */

    /* Risca o que não combina com a seleção atual — **sem desabilitar**.

       Desabilitar diria "esta opção não existe"; o que é verdade é "esta
       opção não existe *nesta cor*". Riscada e clicável diz isso, e o clique
       resolve. */
    function paintAvailability(chosen) {
      axes.forEach(function (axis) {
        inputsOf(axis).forEach(function (input) {
          var combina = fitsCurrent(axis, input.value, chosen);
          input.disabled = false;
          input.setAttribute("aria-disabled", combina ? "false" : "true");
          /* A pílula OU a bolinha de cor: as duas são o `<label>` do radio. */
          var label = input.closest("label");
          if (label) {
            label.classList.toggle("variant-option-unavailable", !combina);
            label.title = combina
              ? ""
              : select.getAttribute("data-switch-label") || "";
          }
        });
      });
    }

    function setMessage(texto) {
      if (!message) {
        return;
      }
      message.textContent = texto || "";
      message.hidden = !texto;
    }

    /* Marca nos botões a combinação desta variante. */
    function mark(variant) {
      axes.forEach(function (axis) {
        var alvo = String(variant[axis]);
        inputsOf(axis).forEach(function (input) {
          input.checked = input.value === alvo;
        });
      });
    }

    /* Tudo o que é da variante muda junto: preço, disponibilidade, peso,
       prazo e ficha técnica. Trocar de cor não pode deixar na tela o peso da
       opção anterior. */
    function show(variant) {
      if (price && variant.priceDisplay) {
        price.textContent = variant.priceDisplay;
      }
      if (quantityInput) {
        quantityInput.max = variant.maxQuantity;
        if (Number(quantityInput.value) > variant.maxQuantity) {
          quantityInput.value = Math.max(1, variant.maxQuantity);
        }
      }
      if (addButton) {
        addButton.disabled = !variant.available;
      }
      if (stockState) {
        stockState.innerHTML = "";
        var badge = document.createElement("span");
        badge.className = STOCK_BADGE[variant.stockState] || "product-tag product-tag-stock";
        badge.textContent = variant.stockLabel;
        stockState.appendChild(badge);
      }
      /* A ficha inteira, e nao so metade dela: cor, tamanho e material sao
         tao da variante quanto o peso. Ate a etapa 18 estas tres ficavam
         com o valor da variante com que a pagina abriu. */
      updateSpec("cor", variant.colorLabel);
      updateSpec("tamanho", variant.sizeLabel);
      updateSpec("material", variant.materialLabel);
      updateSpec("peso", variant.weightGrams ? variant.weightGrams + " g" : "");
      updateSpec("dimensoes", variant.dimensions);
      updateSpec("impressao", variant.printTime);
      updateSpec("referencia", variant.sku);
      /* O nome da cor escolhida no rótulo do grupo ("Cor: Roxo"): as
         bolinhas não têm texto, e é aqui que ele aparece. */
      updateChoice("color", variant.colorLabel);
      updateChoice("size", variant.sizeLabel);
      updateChoice("material", variant.materialLabel);

      /* A foto da variante, quando alguem vinculou uma. Sem foto propria,
         `mediaUrl` vem vazio e a galeria volta para a foto de abertura --
         nao fica a foto da variante anterior na tela. */
      if (window.jdGallery) {
        window.jdGallery.showVariantMedia(variant.mediaUrl || "", variant.mediaAlt || "");
      }
    }

    /* A ficha técnica acompanha a variante; linha sem valor some.

       TODAS as ocorrências da chave, e não só a primeira: a mesma referência
       aparece ao lado do preço e na ficha, o mesmo material no cartão de
       vantagens e na ficha. Um lugar esquecido seria uma tela falando de
       duas variantes ao mesmo tempo. */
    function updateSpec(key, value) {
      document.querySelectorAll('[data-spec="' + key + '"]').forEach(function (row) {
        var target = row.querySelector("[data-spec-value]");
        if (target) {
          target.textContent = value;
        }
        row.hidden = !value;
      });
    }

    function updateChoice(key, value) {
      document.querySelectorAll('[data-variant-choice="' + key + '"]').forEach(function (node) {
        node.textContent = value || "";
      });
    }

    /* Passa a seleção inteira para esta variante: botões, `<select>`, preço,
       ficha e o risco dos outros eixos. Um lugar só, para não existir estado
       em que metade da tela fala de uma variante e metade de outra. */
    function selectVariant(variant) {
      setMessage("");
      mark(variant);
      select.value = String(variant.id);
      paintAvailability(selection());
      show(variant);
    }

    /* ---- eventos ---------------------------------------------------------- */

    form.querySelectorAll("[data-variant-option]").forEach(function (input) {
      input.addEventListener("change", function () {
        var group = input.closest("[data-variant-group]");
        var axis = group ? group.getAttribute("data-variant-group") : null;
        if (!axis) {
          return;
        }

        /* A escolha anterior, ANTES de o radio ter mudado a marcação — é ela
           que o cálculo tenta preservar nos outros eixos. */
        var chosen = selection();
        var variant = bestVariant(axis, input.value, chosen);

        if (!variant) {
          /* Defensivo. Toda opção desenhada vem de alguma variante ativa
             (ver `ProductDetailView.variant_options`), então isto não deveria
             acontecer — mas deixar a tela falando de uma variante que não
             existe seria pior do que dizer que não deu. */
          setMessage(select.getAttribute("data-unavailable-label") || "");
          return;
        }

        selectVariant(variant);
      });
    });

    /* O seletor nativo fica escondido (sr-only) quando há botões, mas continua
       acessível por teclado e por leitor de tela — e é ele que manda o
       `variant_id` no POST. Sem este ouvinte, quem escolhesse por ele levava
       uma variante e via na tela o preço da anterior. */
    select.addEventListener("change", function () {
      var variant = variants.find(function (candidate) {
        return String(candidate.id) === select.value;
      });
      if (variant) {
        selectVariant(variant);
      }
    });

    /* A variante que abre selecionada: a do `<select>` (que o servidor já
       escolheu), com as reservas de sempre. */
    var initial = variants.find(function (variant) {
      return String(variant.id) === select.value;
    }) || variants.find(function (variant) {
      return variant.available;
    }) || variants[0];

    selectVariant(initial);
  }

  /* ---- Personalização --------------------------------------------------- */
  function setupPersonalization() {
    var block = document.querySelector("[data-personalization]");
    if (!block) {
      return;
    }

    var photo = block.querySelector("[data-personalization-photo]");
    var text = block.querySelector("[data-personalization-text]");
    var modes = block.querySelectorAll("[data-personalization-mode]");

    function apply() {
      var chosen = block.querySelector("[data-personalization-mode]:checked");
      var mode = chosen ? chosen.value : "";

      if (photo) {
        photo.hidden = mode !== "photo";
        var file = photo.querySelector("input[type=file]");
        if (file) {
          file.required = mode === "photo";
        }
      }
      if (text) {
        text.hidden = mode !== "text";
        var area = text.querySelector("textarea");
        if (area) {
          area.required = mode === "text";
        }
      }
    }

    modes.forEach(function (input) {
      input.addEventListener("change", apply);
    });
    if (modes.length) {
      apply();
    }

    /* Contador de caracteres do texto. */
    var area = block.querySelector("textarea[data-counter-target]");
    if (area) {
      var counter = document.getElementById(area.getAttribute("data-counter-target"));
      if (counter) {
        var update = function () {
          counter.textContent = String(area.value.length);
        };
        area.addEventListener("input", update);
        update();
      }
    }
  }

  /* ---- Quantidade (− 1 +) ---------------------------------------------- */
  function setupQuantitySteppers() {
    document.querySelectorAll("[data-quantity]").forEach(function (widget) {
      var input = widget.querySelector("[data-quantity-input]");
      if (!input) {
        return;
      }

      /* Um limite so, para os botoes e para o que for digitado. Os valores
         vem do proprio campo (`min`/`max`), que o servidor preencheu e que a
         troca de variante atualiza -- nenhuma regra de estoque escrita aqui. */
      function clamp(valor) {
        var min = Number(input.min || 1);
        var max = Number(input.max || 99);
        var numero = Math.floor(Number(valor));
        if (!isFinite(numero) || numero < min) {
          numero = min;
        }
        input.value = String(Math.min(max, numero));
      }

      widget.querySelectorAll("[data-quantity-step]").forEach(function (button) {
        button.addEventListener("click", function () {
          var step = Number(button.getAttribute("data-quantity-step"));
          clamp(Number(input.value || 1) + step);
        });
      });

      /* Digitar 99 num produto com 5 em estoque deixava o formulario invalido:
         o HTMX nao mandava a requisicao e o cliente ficava sem resposta.
         Corrigir o valor faz o pedido sair com o numero certo. */
      input.addEventListener("change", function () {
        clamp(input.value);
      });
    });
  }


  /* ---- Checkout: endereço de faturamento -------------------------------
     Sem JavaScript o bloco fica visível e o formulário continua correto (o
     "mesmo endereço" vence no servidor). Com JavaScript, ele só aparece quando
     o cliente realmente quer outro endereço. */
  function setupCheckout() {
    var toggle = document.querySelector("[data-billing-toggle]");
    var block = document.querySelector("[data-billing-block]");
    if (!toggle || !block) {
      return;
    }
    var sync = function () {
      block.hidden = toggle.checked;
    };
    toggle.addEventListener("change", sync);
    sync();
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupMobileMenu();
    setupDropdowns();
    setupCarousels();
    setupBannerCarousels();
    setupShopFilters();
    setupHtmx();
    setupCartDrawer();
    setupGallery();
    setupVariants();
    setupPersonalization();
    setupQuantitySteppers();
    setupCheckout();

    // O bloco de entrega é remontado pelo HTMX a cada troca de endereço.
    document.body.addEventListener("htmx:afterSwap", function (event) {
      if (event.target && event.target.id === "checkout-delivery") {
        setupCheckout();
      }
      // Se um carrossel de banners chegar por troca, ele nasce sem ouvintes;
      // os que já existiam ficam como estão (`data-carousel-ready`).
      setupBannerCarousels();
    });
  });
})();
