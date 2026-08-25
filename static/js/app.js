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

  /* ---- Filtros do Shop ------------------------------------------------ */
  /* O <details> vem aberto no HTML para funcionar sem JavaScript. Com
     JavaScript, fecha no celular (onde ocuparia meia tela) e fica aberto do
     desktop para cima. */
  function setupShopFilters() {
    var filters = document.querySelector("[data-shop-filters]");
    if (!filters || !window.matchMedia) {
      return;
    }

    var wide = window.matchMedia("(min-width: 1024px)");
    function sync() {
      filters.open = wide.matches;
    }

    sync();
    wide.addEventListener("change", sync);
  }

  /* ---- HTMX ----------------------------------------------------------- */
  /* Depois de trocar a grade de produtos, leva a página de volta ao topo da
     lista — senão o visitante fica olhando para o rodapé. */
  function setupHtmx() {
    document.body.addEventListener("htmx:afterSwap", function (event) {
      if (event.target && event.target.id === "shop-results") {
        var heading = document.querySelector("h1");
        if (heading) {
          heading.scrollIntoView({ behavior: "smooth", block: "start" });
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

  /* ---- Galeria do produto ---------------------------------------------- */
  function setupGallery() {
    var gallery = document.querySelector("[data-gallery]");
    if (!gallery) {
      return;
    }

    var main = gallery.querySelector("[data-gallery-main]");
    var thumbs = gallery.querySelectorAll("[data-gallery-thumb]");
    if (!main || !thumbs.length) {
      return;
    }

    thumbs.forEach(function (thumb) {
      thumb.addEventListener("click", function (event) {
        event.preventDefault();
        var url = thumb.getAttribute("href");
        var type = thumb.getAttribute("data-media-type");
        var alt = thumb.getAttribute("data-alt") || "";

        if (type === "VIDEO") {
          main.innerHTML =
            '<video src="' + url + '" controls playsinline class="h-full w-full object-cover"></video>';
        } else {
          main.innerHTML =
            '<img src="' + url + '" alt="' + alt.replace(/"/g, "&quot;") +
            '" class="h-full w-full object-cover">';
        }

        thumbs.forEach(function (other) {
          other.classList.toggle("border-brand-600", other === thumb);
          other.classList.toggle("border-surface-line", other !== thumb);
        });
      });
    });
  }

  /* ---- Seletor de variantes -------------------------------------------- */
  /* Os botões de cor/tamanho/material são a interface; quem guarda a escolha
     continua sendo o <select>, que funciona sem JavaScript. */
  function setupVariants() {
    var form = document.querySelector("[data-add-to-cart]");
    if (!form) {
      return;
    }

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
    var groups = form.querySelectorAll("[data-variant-group]");
    if (groups.length) {
      wrapper.classList.add("sr-only");
    }

    var price = document.querySelector("[data-price]");
    var addButton = form.querySelector("[data-add-button]");
    var quantityInput = form.querySelector("[data-quantity-input]");

    function selection() {
      var chosen = {};
      groups.forEach(function (group) {
        var key = group.getAttribute("data-variant-group");
        var checked = group.querySelector("input:checked");
        chosen[key] = checked ? checked.value : "";
      });
      return chosen;
    }

    function matches(variant, chosen) {
      return Object.keys(chosen).every(function (key) {
        return !chosen[key] || String(variant[key]) === chosen[key];
      });
    }

    function apply() {
      var chosen = selection();
      var candidates = variants.filter(function (variant) {
        return matches(variant, chosen);
      });

      var complete = Object.keys(chosen).every(function (key) {
        return chosen[key];
      });
      if (!complete || !candidates.length) {
        return;
      }

      var variant = candidates[0];
      select.value = String(variant.id);

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
    }

    /* Marca a combinação da primeira variante disponível. */
    var initial = variants.find(function (variant) {
      return variant.available;
    }) || variants[0];
    groups.forEach(function (group) {
      var key = group.getAttribute("data-variant-group");
      var input = group.querySelector('input[value="' + String(initial[key]).replace(/"/g, '\\"') + '"]');
      if (input) {
        input.checked = true;
      }
    });

    form.querySelectorAll("[data-variant-option]").forEach(function (input) {
      input.addEventListener("change", apply);
    });

    apply();
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
      widget.querySelectorAll("[data-quantity-step]").forEach(function (button) {
        button.addEventListener("click", function () {
          var step = Number(button.getAttribute("data-quantity-step"));
          var min = Number(input.min || 1);
          var max = Number(input.max || 99);
          var value = Number(input.value || 1) + step;
          input.value = String(Math.min(max, Math.max(min, value)));
        });
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
    });
  });
})();
