/* Mostra só o que o tipo de banner escolhido usa.

   Quatro desenhos, quatro conjuntos de campos: o hero editorial tem
   composição (cores), selos e promessas; a imagem completa não tem nada
   disso e ganha um segundo arquivo (a versão para o celular); o Poster Pop
   tem os dois quadros laterais e os selos menta e branco; o Bento Criativo
   tem o selo coral e os dois cartões pequenos (cores e avaliação). Deixar
   tudo na tela ao mesmo tempo faria o formulário parecer o triplo do que é,
   e quem cadastra preencheria campos que o desenho escolhido ignora.

   É só apresentação: nada é apagado, e trocar o tipo de volta traz o que
   estava preenchido. */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var layout = document.getElementById("id_layout");
    if (!layout) {
      return;
    }

    /* Campo -> desenhos que o usam. As chaves batem com `BannerLayout`; o
       que não está aqui aparece em todos os desenhos. Os campos de tradução
       vivem no inline, um conjunto por idioma — por isso a busca é por
       classe e não por id. */
    var EDITORIAL = "editorial", CHEIA = "full_image", POSTER = "poster_pop", BENTO = "bento_criativo";
    var COMPOSTOS = [EDITORIAL, POSTER, BENTO];
    var campos = {
      ".field-image_mobile": [CHEIA],
      ".field-cta_secondary_url": COMPOSTOS,
      ".field-plate_color": [EDITORIAL],
      ".field-frame_color": [EDITORIAL],
      ".field-surface_color": [EDITORIAL],
      ".field-image_tile_left": [POSTER],
      ".field-image_tile_right": [POSTER],
      ".field-eyebrow": COMPOSTOS,
      ".field-title_highlight": COMPOSTOS,
      ".field-cta_secondary_label": COMPOSTOS,
      ".field-perk_1": COMPOSTOS,
      ".field-perk_2": COMPOSTOS,
      ".field-perk_3": COMPOSTOS,
      ".field-badge_yellow": [EDITORIAL, BENTO],
      ".field-badge_mint": [EDITORIAL, POSTER, BENTO],
      ".field-badge_white": [EDITORIAL, POSTER],
      ".field-image_tile_left_alt": [POSTER],
      ".field-image_tile_right_alt": [POSTER],
      ".field-badge_coral": [BENTO],
      ".field-colors_note": [BENTO],
      ".field-rating_value": [BENTO],
      ".field-rating_note": [BENTO]
    };

    /* Blocos inteiros do formulário. */
    var blocos = {
      "fieldset.jd-composicao": [EDITORIAL],
      "fieldset.jd-poster": [POSTER]
    };

    function aplicar() {
      var atual = layout.value;
      Object.keys(campos).forEach(function (seletor) {
        var visivel = campos[seletor].indexOf(atual) !== -1;
        document.querySelectorAll(seletor).forEach(function (node) {
          node.style.display = visivel ? "" : "none";
        });
      });
      Object.keys(blocos).forEach(function (seletor) {
        var visivel = blocos[seletor].indexOf(atual) !== -1;
        document.querySelectorAll(seletor).forEach(function (node) {
          node.style.display = visivel ? "" : "none";
        });
      });
    }

    layout.addEventListener("change", aplicar);

    /* O Django cria as linhas de tradução por JavaScript ao clicar em
       "adicionar"; sem observar, uma linha nova nasceria com os campos do
       outro desenho à mostra. */
    var observador = new MutationObserver(function () {
      aplicar();
    });
    observador.observe(document.body, { childList: true, subtree: true });

    aplicar();
  });
})();
