/* Mostra só o que o tipo de banner escolhido usa.

   Quatro desenhos, quatro conjuntos de campos: o hero editorial tem
   composição (cores), selos e promessas; a imagem completa não tem nada
   disso e ganha um segundo arquivo (a versão para o celular); o Poster Pop
   tem os dois quadros laterais e os selos menta e branco; o Bento Criativo
   tem o selo coral e os dois cartões pequenos (cores e avaliação). Deixar
   tudo na tela ao mesmo tempo faria o formulário parecer o triplo do que é,
   e quem cadastra preencheria campos que o desenho escolhido ignora.

   É só apresentação: nada é apagado, e trocar o tipo de volta traz o que
   estava preenchido. Os campos dividem linhas (`jdShowField` esconde a caixa
   de cada um, e a linha só quando fica vazia). */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var layout = document.getElementById("id_layout");
    if (!layout) {
      return;
    }

    var EDITORIAL = "editorial", CHEIA = "full_image", POSTER = "poster_pop", BENTO = "bento_criativo";
    var COMPOSTOS = [EDITORIAL, POSTER, BENTO];
    var campos = {
      image_mobile: [CHEIA],
      cta_secondary_url: COMPOSTOS,
      plate_color: [EDITORIAL],
      frame_color: [EDITORIAL],
      surface_color: [EDITORIAL],
      image_tile_left: [POSTER],
      image_tile_right: [POSTER],
      eyebrow: COMPOSTOS,
      title_highlight: COMPOSTOS,
      cta_secondary_label: COMPOSTOS,
      perk_1: COMPOSTOS,
      perk_2: COMPOSTOS,
      perk_3: COMPOSTOS,
      badge_yellow: [EDITORIAL, BENTO],
      badge_mint: [EDITORIAL, POSTER, BENTO],
      badge_white: [EDITORIAL, POSTER],
      image_tile_left_alt: [POSTER],
      image_tile_right_alt: [POSTER],
      badge_coral: [BENTO],
      colors_note: [BENTO],
      rating_value: [BENTO],
      rating_note: [BENTO]
    };

    /* Blocos inteiros do formulário. */
    var blocos = {
      "fieldset.jd-composicao": [EDITORIAL],
      "fieldset.jd-poster": [POSTER]
    };

    function aplicar() {
      var atual = layout.value;
      Object.keys(campos).forEach(function (nome) {
        window.jdShowField(nome, campos[nome].indexOf(atual) !== -1);
      });
      Object.keys(blocos).forEach(function (seletor) {
        window.jdShowBlock(seletor, blocos[seletor].indexOf(atual) !== -1);
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
