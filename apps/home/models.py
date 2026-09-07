"""Conteúdo gerenciável da Home.

Três blocos:

1. ``CtaMixin``      — botão que aponta para categoria, produto ou URL.
2. ``HomeBanner``    — área grande do topo (hero).
3. ``HomeSection``   — faixas de produtos, ordenadas e configuráveis.

Nenhuma seção é escrita no template: a Home percorre as seções ativas e
renderiza cada uma conforme a sua configuração.
"""

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import get_valid_filename

from apps.catalog.models import Product
from apps.categories.models import Category
from apps.core.colors import check_contrast, resolve, validate_color
from apps.core.models import TimeStampedModel, TranslatableMixin, TranslationBase

#: A frase que acompanha todo campo de cor no Admin.
COLOR_HELP = (
    "Uma cor da marca (creme, navy, purple, yellow, mint, coral, lavender…) "
    "ou um hexadecimal como <code>#4A1A8C</code>."
)


def css_vars(pares: dict) -> str:
    """Um `style` com variáveis CSS, para o bloco herdar as cores escolhidas.

    É assim que a cor sai do banco e chega ao desenho sem nenhum hex escrito
    em template: o CSS lê `var(--card-bg)` e quem define é esta linha.
    """
    return ";".join("--%s:%s" % (nome, valor) for nome, valor in pares.items() if valor)

#: Quantidade máxima de produtos que uma seção pode exibir.
MAX_PRODUCT_LIMIT = 24


class BannerLayout(models.TextChoices):
    """Os quatro desenhos que o topo da Home aceita.

    ``FULL_IMAGE`` é o que existia: uma arte ocupando a faixa inteira, com
    título opcional por cima. ``EDITORIAL`` é a composição da direção visual —
    texto à esquerda e, à direita, a placa roxa inclinada, o quadro da foto e
    os três selos soltos. ``POSTER_POP`` é o poster roxo de conteúdo centrado,
    com a palavra do título marcada em amarelo, dois selos soltos e três
    quadros de foto na base. ``BENTO`` é a grade de cartões: o principal
    (branco) com o texto, o da foto (menta listrado) e, embaixo dele, o das
    cores (roxo) e o da avaliação (amarelo).
    """

    EDITORIAL = "editorial", "Hero editorial (texto + composição gráfica)"
    FULL_IMAGE = "full_image", "Imagem completa (a arte ocupa o banner)"
    POSTER_POP = "poster_pop", "Poster Pop (poster roxo, conteúdo centrado)"
    BENTO = "bento_criativo", "Bento Criativo (grade de cartões)"


#: As bolinhas do cartão de cores do Bento Criativo: a paleta da marca mais os
#: tons de filamento do desenho. Decoração fixa, como os marcadores das
#: promessas — não é uma lista de cores à venda, então não vem do cadastro.
BENTO_DOTS = (
    "#f5d547", "#7edcd8", "#f5b5a3", "#ffffff",
    "#1b1530", "#9b6bdf", "#3fa8a3", "#e45b5b",
)


#: As cores que a composição aceita, e só elas.
#:
#: São escolhas fechadas, e não um seletor livre: o hero é a primeira coisa
#: que se vê da marca, e um `#ff00aa` digitado numa tarde apressada fica no ar
#: até alguém reclamar. Cada entrada é ``(valor, rótulo, hex)`` — o hex sai
#: daqui para o template como variável CSS.
PLATE_COLORS = (
    ("purple", "Roxo", "#4a1a8c"),
    ("navy", "Navy", "#1b1530"),
    ("mint", "Menta", "#7edcd8"),
    ("coral", "Coral", "#f5b5a3"),
)

#: O quadro da foto é listrado: duas faixas do mesmo tom, uma um pouco mais
#: escura. ``(valor, rótulo, faixa clara, faixa escura, tracejado)``.
FRAME_COLORS = (
    ("lavender", "Lavanda", "#efe8fa", "#e4daf6", "#c9b8ea"),
    ("mint", "Menta", "#e3f7f6", "#d3f0ee", "#93d8d4"),
    ("yellow", "Amarelo", "#fff3d6", "#fbe7b8", "#e3c76a"),
    ("coral", "Coral", "#fde6de", "#f8d6ca", "#eeae98"),
)

SURFACE_COLORS = (
    ("cream", "Creme", "#faf8f4"),
    ("lavender", "Lavanda", "#efe8fa"),
    ("white", "Branco", "#ffffff"),
)


# ---------------------------------------------------------------------------
# 1. CTA
# ---------------------------------------------------------------------------


class CtaTarget(models.TextChoices):
    NONE = "none", "Sem botão"
    CATEGORY = "category", "Categoria"
    PRODUCT = "product", "Produto"
    URL = "url", "Endereço livre"


class CtaMixin(models.Model):
    """Botão de chamada para ação.

    Referências internas (categoria/produto) são preferidas a URLs digitadas:
    elas continuam válidas se o slug mudar e não quebram quando a loja ganhar
    rotas por idioma. A URL livre existe para campanhas e páginas externas.
    """

    cta_target = models.CharField(
        "destino do botão", max_length=10, choices=CtaTarget.choices, default=CtaTarget.NONE
    )
    cta_category = models.ForeignKey(
        Category,
        verbose_name="categoria de destino",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    cta_product = models.ForeignKey(
        Product,
        verbose_name="produto de destino",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    cta_url = models.CharField(
        "endereço do botão",
        max_length=500,
        blank=True,
        help_text="Use apenas quando o destino não for uma categoria ou produto.",
    )

    class Meta:
        abstract = True

    @property
    def has_cta(self) -> bool:
        return self.cta_target != CtaTarget.NONE and bool(self.cta_link)

    @property
    def cta_link(self) -> str:
        """URL final do botão, ou string vazia quando não houver destino."""
        if self.cta_target == CtaTarget.CATEGORY and self.cta_category_id:
            return self.cta_category.get_absolute_url()
        if self.cta_target == CtaTarget.PRODUCT and self.cta_product_id:
            return self.cta_product.get_absolute_url()
        if self.cta_target == CtaTarget.URL:
            return self.cta_url
        return ""

    def clean_cta(self) -> dict:
        errors = {}
        if self.cta_target == CtaTarget.CATEGORY and self.cta_category_id is None:
            errors["cta_category"] = "Escolha a categoria de destino do botão."
        if self.cta_target == CtaTarget.PRODUCT and self.cta_product_id is None:
            errors["cta_product"] = "Escolha o produto de destino do botão."
        if self.cta_target == CtaTarget.URL and not self.cta_url.strip():
            errors["cta_url"] = "Informe o endereço de destino do botão."
        if self.cta_target == CtaTarget.URL and not self._url_is_safe(self.cta_url):
            errors["cta_url"] = "Use um caminho interno (/algo) ou um endereço http/https."
        return errors

    @staticmethod
    def _url_is_safe(url: str) -> bool:
        """Bloqueia esquemas perigosos (``javascript:``, ``data:``) no CTA."""
        value = (url or "").strip().lower()
        if not value:
            return True
        return value.startswith(("/", "http://", "https://"))


# ---------------------------------------------------------------------------
# 2. Banner
# ---------------------------------------------------------------------------


BANNER_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp", "avif", "gif")


def banner_upload_to(instance, filename: str) -> str:
    return f"banners/{get_valid_filename(filename)}"


class HomeBanner(TranslatableMixin, CtaMixin, TimeStampedModel):
    """Banner do topo da Home.

    Nesta etapa não existe rotação nem agendamento: a Home usa o primeiro
    banner ativo na ordem definida. Imagem é opcional — sem ela, a Home mostra
    uma área de destaque tipográfica, nunca um espaço quebrado.
    """

    translatable_fields = (
        "title", "subtitle", "cta_label", "image_alt",
        # Os desenhos compostos (editorial, Poster Pop e Bento) usam os de baixo.
        "eyebrow", "title_highlight", "cta_secondary_label",
        "perk_1", "perk_2", "perk_3",
        "badge_yellow", "badge_mint", "badge_white",
        # Só o Poster Pop (os quadros laterais) e o Bento (selo coral e os dois
        # cartões pequenos).
        "image_tile_left_alt", "image_tile_right_alt",
        "badge_coral", "colors_note", "rating_value", "rating_note",
    )

    internal_name = models.CharField(
        "nome interno",
        max_length=120,
        help_text="Identificação administrativa. Não aparece para o cliente.",
    )
    layout = models.CharField(
        "tipo de banner",
        max_length=20,
        choices=BannerLayout.choices,
        default=BannerLayout.EDITORIAL,
        help_text=(
            "<b>Hero editorial</b>: o texto fica à esquerda e a foto entra no "
            "quadro da composição, com os selos por cima.<br>"
            "<b>Imagem completa</b>: a arte ocupa o banner inteiro.<br>"
            "<b>Poster Pop</b>: poster roxo com o texto centrado, a palavra "
            "destacada em amarelo, os selos menta e branco e três quadros de "
            "foto na base.<br>"
            "<b>Bento Criativo</b>: grade de cartões — o texto no cartão branco, "
            "a foto no cartão menta, e embaixo os cartões de cores (roxo) e de "
            "avaliação (amarelo)."
        ),
    )
    cta_secondary_url = models.CharField(
        "endereço do segundo botão",
        max_length=500,
        blank=True,
        help_text="Só no hero editorial. Em branco, o segundo botão não aparece.",
    )
    plate_color = models.CharField(
        "cor da placa de trás",
        max_length=10,
        choices=[(v, r) for v, r, _h in PLATE_COLORS],
        default="purple",
    )
    frame_color = models.CharField(
        "cor do quadro da foto",
        max_length=10,
        choices=[(v, r) for v, r, _c, _e, _t in FRAME_COLORS],
        default="lavender",
    )
    surface_color = models.CharField(
        "fundo do bloco",
        max_length=10,
        choices=[(v, r) for v, r, _h in SURFACE_COLORS],
        default="cream",
    )
    image_desktop = models.FileField(
        "imagem (desktop)",
        upload_to=banner_upload_to,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=list(BANNER_IMAGE_EXTENSIONS))],
        help_text=(
            "1920 × 700 px — proporção 2,74:1. Fora dessa medida a imagem é "
            "cortada pelo centro para caber na faixa."
        ),
    )
    image_mobile = models.FileField(
        "imagem (mobile)",
        upload_to=banner_upload_to,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=list(BANNER_IMAGE_EXTENSIONS))],
        help_text="Opcional. Proporção recomendada: 4:5 (ex.: 900×1125).",
    )
    # Os quadros laterais do Poster Pop. O do meio é a `image_desktop`, que
    # todo desenho já tem: um campo a mais para a mesma foto seria dois lugares
    # para trocar a peça em destaque.
    image_tile_left = models.FileField(
        "quadro da esquerda (Poster Pop)",
        upload_to=banner_upload_to,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=list(BANNER_IMAGE_EXTENSIONS))],
        help_text="Quadrada (ex.: 600 × 600 px). Só no Poster Pop.",
    )
    image_tile_right = models.FileField(
        "quadro da direita (Poster Pop)",
        upload_to=banner_upload_to,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=list(BANNER_IMAGE_EXTENSIONS))],
        help_text="Quadrada (ex.: 600 × 600 px). Só no Poster Pop.",
    )
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "banner da Home"
        verbose_name_plural = "banners da Home"
        ordering = ("sort_order", "-created_at")
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="home_banner_active_idx"),
        ]

    def __str__(self) -> str:
        return self.internal_name

    @property
    def title(self) -> str:
        return self.tr("title")

    @property
    def subtitle(self) -> str:
        return self.tr("subtitle")

    @property
    def cta_label(self) -> str:
        return self.tr("cta_label")

    @property
    def image_alt(self) -> str:
        return self.tr("image_alt", default=self.tr("title"))

    @property
    def has_text(self) -> bool:
        """Há algo a escrever sobre a imagem?

        Sem isto o hero desenhava a faixa de gradiente e um `<h1>` vazio por
        cima de um banner que era só arte — cobrindo justamente a parte da
        imagem que o cliente deveria ver.
        """
        return bool(self.title or self.subtitle or self.has_cta)

    # -- hero editorial ----------------------------------------------------

    @property
    def is_editorial(self) -> bool:
        return self.layout == BannerLayout.EDITORIAL

    # -- Poster Pop e Bento Criativo -----------------------------------------

    @property
    def is_poster(self) -> bool:
        return self.layout == BannerLayout.POSTER_POP

    @property
    def is_bento(self) -> bool:
        return self.layout == BannerLayout.BENTO

    @property
    def tiles(self) -> list[dict]:
        """Os três quadros da base do Poster Pop, na ordem do desenho.

        Sempre três: sem foto o quadro fica vazio (tracejado), porque a faixa
        de três é a composição — um quadro a menos deixaria a base torta. O
        do meio é a foto principal do banner.
        """
        return [
            {"image": self.image_tile_left, "alt": self.tr("image_tile_left_alt")},
            {"image": self.image_desktop, "alt": self.image_alt},
            {"image": self.image_tile_right, "alt": self.tr("image_tile_right_alt")},
        ]

    @property
    def badge_coral(self) -> str:
        return self.tr("badge_coral")

    @property
    def badge_yellow(self) -> str:
        return self.tr("badge_yellow")

    @property
    def badge_mint(self) -> str:
        return self.tr("badge_mint")

    @property
    def badge_white(self) -> str:
        return self.tr("badge_white")

    @property
    def colors_note(self) -> str:
        return self.tr("colors_note")

    @property
    def rating_value(self) -> str:
        return self.tr("rating_value")

    @property
    def rating_note(self) -> str:
        return self.tr("rating_note")

    @property
    def has_colors_card(self) -> bool:
        """O cartão roxo do Bento existe quando há o texto grande ("+120 cores")."""
        return bool(self.badge_mint)

    @property
    def has_rating_card(self) -> bool:
        """O cartão amarelo do Bento existe quando há a nota ("4.9")."""
        return bool(self.rating_value)

    @property
    def bento_dots(self) -> tuple[str, ...]:
        return BENTO_DOTS

    @property
    def eyebrow(self) -> str:
        return self.tr("eyebrow")

    @property
    def title_highlight(self) -> str:
        return self.tr("title_highlight")

    @property
    def title_html(self):
        """O título com o trecho destacado em roxo.

        O cadastro guarda o título inteiro e, à parte, o pedaço que deve sair
        colorido — em vez de pedir HTML a quem escreve. Aqui o pedaço é
        localizado no título e embrulhado num `<em>`.

        Tudo é escapado ANTES de a marcação entrar: o único HTML que sai daqui
        é o `<em>` que este método escreve. Se o trecho não estiver no título
        (erro de digitação, tradução divergente), o título sai inteiro e sem
        destaque — nunca quebrado.
        """
        from django.utils.html import escape
        from django.utils.safestring import mark_safe

        titulo, trecho = self.title, self.title_highlight
        if not trecho or trecho not in titulo:
            return mark_safe(escape(titulo))
        antes, _, depois = titulo.partition(trecho)
        return mark_safe(f"{escape(antes)}<em>{escape(trecho)}</em>{escape(depois)}")

    @property
    def cta_secondary_label(self) -> str:
        return self.tr("cta_secondary_label")

    @property
    def has_secondary_cta(self) -> bool:
        return bool(self.cta_secondary_label and self.cta_secondary_url)

    @property
    def perks(self) -> list[str]:
        """As três promessas curtas, sem os buracos.

        Cadastrar só a primeira e a terceira não pode deixar um marcador
        colorido solto no meio.
        """
        return [t for t in (self.tr("perk_1"), self.tr("perk_2"), self.tr("perk_3")) if t]

    @property
    def badges(self) -> list[dict]:
        """Os três selos soltos sobre a composição.

        A ordem é a do desenho — amarelo, menta e branco — e cada um só existe
        se tiver texto. A cor não é escolha do cadastro: os selos SÃO o amarelo,
        o menta e o branco da marca, e é o contraste entre os três que faz a
        composição funcionar.
        """
        escritos = (
            ("yellow", self.tr("badge_yellow")),
            ("mint", self.tr("badge_mint")),
            ("white", self.tr("badge_white")),
        )
        return [{"tone": tom, "text": texto} for tom, texto in escritos if texto]

    def _preset(self, tabela, valor, campo):
        for linha in tabela:
            if linha[0] == valor:
                return linha[campo]
        return tabela[0][campo]

    @property
    def hero_style(self) -> str:
        """As cores escolhidas, como variáveis CSS para o bloco do hero.

        Vai para um atributo `style` no `<section>`. Os valores saem de uma
        lista fechada no código — nunca de texto digitado —, então não há hex
        de origem duvidosa chegando ao HTML.
        """
        claro = self._preset(FRAME_COLORS, self.frame_color, 2)
        escuro = self._preset(FRAME_COLORS, self.frame_color, 3)
        tracejado = self._preset(FRAME_COLORS, self.frame_color, 4)
        return (
            f"--hero-surface:{self._preset(SURFACE_COLORS, self.surface_color, 2)};"
            f"--hero-plate:{self._preset(PLATE_COLORS, self.plate_color, 2)};"
            f"--hero-frame-a:{claro};--hero-frame-b:{escuro};--hero-frame-line:{tracejado}"
        )

    def clean(self):
        super().clean()
        errors = self.clean_cta()
        if errors:
            raise ValidationError(errors)


class HomeBannerTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeBanner, verbose_name="banner", related_name="translations", on_delete=models.CASCADE
    )
    title = models.CharField(
        "título",
        max_length=200,
        blank=True,
        help_text=(
            "Opcional. Em branco, a imagem aparece sozinha — sem faixa escura "
            "nem título vazio por cima dela."
        ),
    )
    subtitle = models.CharField("subtítulo", max_length=300, blank=True)
    cta_label = models.CharField("texto do botão", max_length=80, blank=True)
    image_alt = models.CharField(
        "texto alternativo da imagem",
        max_length=200,
        blank=True,
        help_text="Descrição da imagem para leitores de tela.",
    )

    # -- só o hero editorial usa os campos abaixo --------------------------

    eyebrow = models.CharField(
        "tarja acima do título",
        max_length=60,
        blank=True,
        help_text='A pílula amarela inclinada. Ex.: "Impressão 3D criativa".',
    )
    title_highlight = models.CharField(
        "palavra destacada no título",
        max_length=80,
        blank=True,
        help_text=(
            "Um trecho que já exista no título; ele sai em roxo. Ex.: com o "
            'título "Ideias que ganham forma", escreva <b>forma</b> aqui.'
        ),
    )
    cta_secondary_label = models.CharField(
        "texto do segundo botão", max_length=80, blank=True
    )
    perk_1 = models.CharField("promessa 1", max_length=60, blank=True)
    perk_2 = models.CharField("promessa 2", max_length=60, blank=True)
    perk_3 = models.CharField("promessa 3", max_length=60, blank=True)
    badge_yellow = models.CharField(
        "selo amarelo", max_length=40, blank=True, help_text='Ex.: "PLA · 0.12 mm".'
    )
    badge_mint = models.CharField(
        "selo menta", max_length=40, blank=True, help_text='Ex.: "+120 cores".'
    )
    badge_white = models.CharField(
        "selo branco", max_length=40, blank=True, help_text='Ex.: "★ 4.9 · 300+ pedidos".'
    )

    # -- só o Poster Pop ---------------------------------------------------

    image_tile_left_alt = models.CharField(
        "texto alternativo do quadro da esquerda",
        max_length=200,
        blank=True,
        help_text="Poster Pop. Descrição da foto do quadro da esquerda para leitores de tela.",
    )
    image_tile_right_alt = models.CharField(
        "texto alternativo do quadro da direita",
        max_length=200,
        blank=True,
        help_text="Poster Pop. Descrição da foto do quadro da direita para leitores de tela.",
    )

    # -- só o Bento Criativo -----------------------------------------------

    badge_coral = models.CharField(
        "selo coral",
        max_length=40,
        blank=True,
        help_text='Bento Criativo: o selo inclinado no canto do cartão branco. Ex.: "Feito na Bélgica".',
    )
    colors_note = models.CharField(
        "nota do cartão de cores",
        max_length=60,
        blank=True,
        help_text=(
            'Bento Criativo: a linha pequena abaixo do texto grande do cartão roxo. '
            'Ex.: "PLA · PETG · TPU". O texto grande é o <b>selo menta</b> ("+120 cores").'
        ),
    )
    rating_value = models.CharField(
        "nota da avaliação",
        max_length=10,
        blank=True,
        help_text='Bento Criativo: o número grande do cartão amarelo. Ex.: "4.9". Em branco, o cartão não aparece.',
    )
    rating_note = models.CharField(
        "nota do cartão de avaliação",
        max_length=60,
        blank=True,
        help_text='Bento Criativo: a linha pequena do cartão amarelo. Ex.: "300+ pedidos entregues".',
    )

    class Meta:
        verbose_name = "tradução do banner"
        verbose_name_plural = "traduções do banner"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_banner_translation_unique_language"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_language_display()})"


# ---------------------------------------------------------------------------
# 2b. O carrossel dos banners
# ---------------------------------------------------------------------------


#: O intervalo aceito entre trocas, em segundos. Menos que dois é um pisca-pisca
#: que ninguém lê; mais que trinta é um banner parado com controles à toa.
CAROUSEL_INTERVAL_MIN = 2
CAROUSEL_INTERVAL_MAX = 30


class HomeBannerCarousel(TimeStampedModel):
    """Como os banners ativos rodam no topo da Home.

    **Uma linha só** (``pk=1``), como a chamada final e o rodapé: é *a*
    configuração do carrossel, global — não uma escolha por banner. Com um
    banner ativo só, nada disto entra em cena: ele aparece como sempre, sem
    setas nem indicadores. Com dois ou mais, os banners viram slides na ordem
    do cadastro e estas opções dizem como se navega entre eles.

    Sem linha cadastrada valem os padrões dos campos: rotação automática
    desligada, cinco segundos, setas e indicadores à mostra, pausa ao passar o
    cursor e ao interagir.
    """

    autoplay = models.BooleanField(
        "rotação automática",
        default=False,
        help_text=(
            "Ligada, os banners trocam sozinhos no intervalo abaixo. Quem pediu "
            "ao navegador menos movimento (prefers-reduced-motion) não recebe a "
            "rotação, só a navegação manual."
        ),
    )
    interval_seconds = models.PositiveSmallIntegerField(
        "intervalo (segundos)",
        default=5,
        validators=[
            MinValueValidator(CAROUSEL_INTERVAL_MIN),
            MaxValueValidator(CAROUSEL_INTERVAL_MAX),
        ],
        help_text=(
            f"Quanto tempo cada banner fica na tela antes de trocar. Entre "
            f"{CAROUSEL_INTERVAL_MIN} e {CAROUSEL_INTERVAL_MAX} segundos."
        ),
    )
    show_arrows = models.BooleanField(
        "mostrar setas",
        default=True,
        help_text="Os botões de anterior e próximo, nas bordas do banner.",
    )
    show_dots = models.BooleanField(
        "mostrar indicadores",
        default=True,
        help_text="As bolinhas abaixo do banner, uma por slide; a do slide atual fica cheia.",
    )
    pause_on_hover = models.BooleanField(
        "pausar ao passar o mouse",
        default=True,
        help_text="A rotação automática para enquanto o cursor (ou o foco do teclado) estiver sobre o banner.",
    )
    pause_on_interaction = models.BooleanField(
        "pausar após interação",
        default=True,
        help_text=(
            "Depois de um clique numa seta ou indicador, de uma tecla ou de um "
            "deslize no celular, a rotação espera dois intervalos antes de voltar."
        ),
    )

    class Meta:
        verbose_name = "carrossel de banners"
        verbose_name_plural = "CARROSSEL DE BANNERS — rotação e controles"

    def __str__(self) -> str:
        return "Carrossel de banners"

    def save(self, *args, **kwargs):
        """Uma linha só: qualquer gravação assume o mesmo PK."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "HomeBannerCarousel":
        obj, _criada = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def current(cls) -> "HomeBannerCarousel":
        """A configuração em uso — a gravada ou, sem linha, os padrões.

        Não grava nada: a Home é lida por visitantes, e uma visita não pode
        criar linha no banco.
        """
        return cls.objects.filter(pk=1).first() or cls()

    @property
    def interval_ms(self) -> int:
        return self.interval_seconds * 1000


# ---------------------------------------------------------------------------
# 3. Seções
# ---------------------------------------------------------------------------


class HomeSectionType(models.TextChoices):
    """O que uma seção da Home mostra.

    Os cinco primeiros são faixas de **produtos** (o que a seção sempre foi);
    os quatro últimos são os **blocos** da Home — categorias em destaque, como
    trabalhamos, chamada final e sobre a loja —, que até a etapa 20 tinham
    posição fixa na página e agora são seções como as outras: entram na
    composição, na ordem que o Admin escolher, quantas vezes quiser.
    """

    MANUAL_PRODUCTS = "manual", "Produtos escolhidos manualmente"
    CATEGORY_PRODUCTS = "category", "Produtos de uma categoria"
    FEATURED_PRODUCTS = "featured", "Produtos em destaque"
    NEWEST_PRODUCTS = "newest", "Novidades (produtos mais recentes)"
    BEST_SELLERS = "best_sellers", "Mais vendidos (aguarda o módulo de pedidos)"
    CATEGORY_CARDS = "category_cards", "Categorias em destaque (blocos coloridos)"
    HOW_WE_WORK = "how_we_work", "Como trabalhamos (cards com ícone)"
    CALLOUT = "callout", "Chamada final (faixa com botão e passos)"
    ABOUT = "about", "Sobre a loja (quadro de imagem e texto)"


#: Os tipos que são faixas de produtos — os que têm layout, limite e resolvedor.
PRODUCT_SECTION_TYPES = frozenset({
    HomeSectionType.MANUAL_PRODUCTS,
    HomeSectionType.CATEGORY_PRODUCTS,
    HomeSectionType.FEATURED_PRODUCTS,
    HomeSectionType.NEWEST_PRODUCTS,
    HomeSectionType.BEST_SELLERS,
})

#: Os tipos que são blocos da Home.
BLOCK_SECTION_TYPES = frozenset({
    HomeSectionType.CATEGORY_CARDS,
    HomeSectionType.HOW_WE_WORK,
    HomeSectionType.CALLOUT,
    HomeSectionType.ABOUT,
})


class HomeSectionLayout(models.TextChoices):
    GRID = "grid", "Grade"
    CAROUSEL = "carousel", "Carrossel"


class HomeSectionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def ordered(self):
        return self.order_by("sort_order", "id")


class HomeSection(TranslatableMixin, CtaMixin, TimeStampedModel):
    """Uma seção da Home — a unidade da **composição** da página.

    A Home é a lista das seções ativas, na ordem de ``sort_order``: o que
    aparece, quantas vezes e em que ordem é decisão do Admin, e nada no
    template fixa posição nenhuma. Cada linha é uma **instância**: duas seções
    de categorias, cada uma com os seus blocos; duas chamadas finais; o "Sobre
    a loja" antes ou depois dos produtos.

    O tipo (``section_type``) decide o que a seção mostra:

    * **faixas de produtos** (manual, categoria, destaques, novidades, mais
      vendidos) — o service em ``apps/home/services.py`` resolve os produtos;
    * **categorias em destaque** — os `HomeCategoryCard` desta seção;
    * **como trabalhamos** — os `HomeCard` desta seção;
    * **chamada final** — o `HomeCallout` escolhido em ``callout`` (com os
      passos dele); duas seções podem apontar para o mesmo texto;
    * **sobre a loja** — o `HomeAbout` escolhido em ``about`` (com as pílulas).

    O nome interno é o nome da **instância** ("Categorias — Coleções"); o tipo
    é o nome do **modelo**. Na lista do Admin os dois aparecem lado a lado, e é
    assim que se distingue duas seções iguais.
    """

    translatable_fields = ("title", "subtitle", "cta_label")

    internal_name = models.CharField(
        "nome interno",
        max_length=120,
        help_text="Identificação administrativa (ex.: 'Destaques de Modelos'). "
        "Não aparece para o cliente.",
    )
    section_type = models.CharField(
        "tipo", max_length=20, choices=HomeSectionType.choices, default=HomeSectionType.FEATURED_PRODUCTS
    )
    layout = models.CharField(
        "layout", max_length=10, choices=HomeSectionLayout.choices, default=HomeSectionLayout.GRID
    )
    is_active = models.BooleanField(
        "ativa", default=True, help_text="Desmarque para esconder sem apagar (campanhas sazonais)."
    )
    sort_order = models.PositiveIntegerField(
        "ordem", default=0, help_text="Menor valor aparece primeiro na Home."
    )
    product_limit = models.PositiveIntegerField(
        "quantidade de produtos",
        default=4,
        help_text=f"Máximo de produtos exibidos (1 a {MAX_PRODUCT_LIMIT}).",
    )
    category = models.ForeignKey(
        Category,
        verbose_name="categoria",
        related_name="home_sections",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Usada apenas no tipo 'Produtos de uma categoria'.",
    )
    include_subcategories = models.BooleanField(
        "incluir subcategorias",
        default=True,
        help_text="Traz também os produtos das categorias filhas.",
    )
    products = models.ManyToManyField(
        Product,
        verbose_name="produtos",
        related_name="home_sections",
        through="HomeSectionProduct",
        blank=True,
    )
    # Os blocos que têm um registro de conteúdo próprio, reaproveitável.
    callout = models.ForeignKey(
        "HomeCallout",
        verbose_name="chamada final (conteúdo)",
        related_name="sections",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Usada só no tipo 'Chamada final'. Sem escolher, a seção mostra o texto padrão.",
    )
    about = models.ForeignKey(
        "HomeAbout",
        verbose_name="sobre a loja (conteúdo)",
        related_name="sections",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Usado só no tipo 'Sobre a loja'. Sem escolher, a seção não é desenhada.",
    )

    objects = HomeSectionQuerySet.as_manager()

    class Meta:
        verbose_name = "seção da Home"
        verbose_name_plural = "seções da Home"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="home_section_active_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(product_limit__gte=1, product_limit__lte=MAX_PRODUCT_LIMIT),
                name="home_section_product_limit_range",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(section_type=HomeSectionType.CATEGORY_PRODUCTS)
                    | models.Q(category__isnull=False)
                ),
                name="home_section_category_required_for_category_type",
            ),
        ]

    def __str__(self) -> str:
        return self.internal_name

    # -- conteúdo traduzido ------------------------------------------------

    @property
    def title(self) -> str:
        return self.tr("title", default=self.internal_name)

    @property
    def subtitle(self) -> str:
        return self.tr("subtitle")

    @property
    def cta_label(self) -> str:
        return self.tr("cta_label")

    # -- características do tipo -------------------------------------------

    @property
    def is_products_section(self) -> bool:
        """Faixa de produtos (com layout, limite e botão) — e não um bloco."""
        return self.section_type in PRODUCT_SECTION_TYPES

    @property
    def is_block_section(self) -> bool:
        return self.section_type in BLOCK_SECTION_TYPES

    @property
    def uses_callout(self) -> bool:
        return self.section_type == HomeSectionType.CALLOUT

    @property
    def uses_about(self) -> bool:
        return self.section_type == HomeSectionType.ABOUT

    @property
    def uses_manual_products(self) -> bool:
        return self.section_type == HomeSectionType.MANUAL_PRODUCTS

    @property
    def uses_category(self) -> bool:
        return self.section_type == HomeSectionType.CATEGORY_PRODUCTS

    @property
    def has_data_source(self) -> bool:
        """``False`` para tipos que dependem de módulos ainda não implementados."""
        return self.section_type != HomeSectionType.BEST_SELLERS

    # -- validação ---------------------------------------------------------

    def clean(self):
        super().clean()
        errors = self.clean_cta()

        if not (1 <= (self.product_limit or 0) <= MAX_PRODUCT_LIMIT):
            errors["product_limit"] = f"Informe um valor entre 1 e {MAX_PRODUCT_LIMIT}."

        if self.uses_category and self.category_id is None:
            errors["category"] = "Escolha a categoria desta seção."

        if errors:
            raise ValidationError(errors)


class HomeSectionTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeSection, verbose_name="seção", related_name="translations", on_delete=models.CASCADE
    )
    title = models.CharField("título", max_length=200)
    subtitle = models.CharField("subtítulo", max_length=300, blank=True)
    cta_label = models.CharField("texto do botão", max_length=80, blank=True)

    class Meta:
        verbose_name = "tradução da seção"
        verbose_name_plural = "traduções da seção"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_section_translation_unique_language"
            ),
            models.CheckConstraint(
                condition=~models.Q(title=""), name="home_section_translation_title_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_language_display()})"


class HomeSectionProduct(models.Model):
    """Produto escolhido manualmente para uma seção, com ordem própria.

    Um ManyToMany simples não guardaria a ordem definida pelo administrador —
    por isso a tabela intermediária explícita.
    """

    section = models.ForeignKey(
        HomeSection, verbose_name="seção", related_name="items", on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        Product, verbose_name="produto", related_name="home_section_items", on_delete=models.CASCADE
    )
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "produto da seção"
        verbose_name_plural = "produtos da seção"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["section", "product"], name="home_section_product_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["section", "sort_order"], name="home_section_item_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sort_order}. {self.product}"


# ---------------------------------------------------------------------------
# 4. Cards "como trabalhamos"
# ---------------------------------------------------------------------------


#: Os ícones que o `icon.html` já sabe desenhar. Uma lista fechada, e não um
#: campo de texto livre: nome errado renderizaria um espaço em branco, e o
#: administrador não teria como descobrir por quê.
CARD_ICONS = (
    ("cube", "Cubo (produção)"),
    ("palette", "Paleta (cores e materiais)"),
    ("sparkles", "Brilho (personalização)"),
    ("truck", "Caminhão (envio)"),
    ("shield", "Escudo (segurança)"),
    ("clock", "Relógio (prazo)"),
    ("package", "Caixa (embalagem)"),
    ("heart", "Coração"),
    ("check", "Confirmação"),
    ("globe", "Globo"),
)

#: Os três acentos da marca. Um seletor de cor livre deixaria a Home sair do
#: padrão no primeiro cadastro distraído.
#:
#: Chamavam-se "cyan" e "magenta" — os nomes de antes do redesenho, quando eram
#: mesmo ciano e magenta. A migração 0004 os renomeou para o que já pintavam.
CARD_ACCENTS = (
    ("brand", "Roxo"),
    ("mint", "Menta"),
    ("coral", "Coral"),
)


class HomeCardQuerySet(models.QuerySet):
    def for_display(self):
        return (
            self.filter(is_active=True)
            .order_by("sort_order", "id")
            .prefetch_related("translations")
        )


class HomeCard(TranslatableMixin, TimeStampedModel):
    """Um dos cards com ícone abaixo das faixas de produtos.

    Eram três, escritos no HTML. Nada no template fixa a quantidade: a grade é
    `sm:grid-cols-3`, e cadastrar quatro dá duas linhas — o que é decisão do
    administrador, não um erro.
    """

    translatable_fields = ("title", "text")

    section = models.ForeignKey(
        HomeSection,
        verbose_name="seção",
        related_name="cards",
        null=True,
        on_delete=models.CASCADE,
        help_text="A seção 'Como trabalhamos' em que este card aparece.",
    )
    internal_name = models.CharField(
        "nome interno",
        max_length=120,
        help_text="Identificação administrativa. Não aparece para o cliente.",
    )
    icon = models.CharField("ícone", max_length=20, choices=CARD_ICONS, default="cube")
    accent = models.CharField("cor do ícone", max_length=10, choices=CARD_ACCENTS, default="brand")
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    objects = HomeCardQuerySet.as_manager()

    class Meta:
        verbose_name = "card da Home"
        verbose_name_plural = "CARDS — como trabalhamos"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="home_card_active_idx"),
        ]

    def __str__(self) -> str:
        return self.internal_name

    @property
    def title(self) -> str:
        return self.tr("title")

    @property
    def text(self) -> str:
        return self.tr("text")


class HomeCardTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeCard, verbose_name="card", related_name="translations", on_delete=models.CASCADE
    )
    title = models.CharField("título", max_length=120)
    text = models.CharField("texto", max_length=300, blank=True)

    class Meta:
        verbose_name = "tradução do card"
        verbose_name_plural = "traduções do card"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_card_translation_unique_language"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_language_display()})"


# ---------------------------------------------------------------------------
# 5. Chamada final
# ---------------------------------------------------------------------------


class HomeCallout(TranslatableMixin, CtaMixin, TimeStampedModel):
    """A faixa escura com botão e passos — sobretítulo, título, texto e botão.

    É o **conteúdo** de uma seção do tipo "Chamada final": a posição na Home
    (e quantas vezes aparece) é da `HomeSection` que aponta para cá. Pode
    haver várias — uma por campanha —, e duas seções podem usar a mesma. O
    botão reaproveita o `CtaMixin`, o mesmo do banner e das seções, então pode
    apontar para uma categoria ou um produto e continuar válido se o slug
    mudar.

    Tudo é opcional. Sem título, sem texto e sem botão o bloco inteiro some da
    Home em vez de virar uma faixa escura vazia.
    """

    translatable_fields = ("eyebrow", "title", "text", "cta_label")

    internal_name = models.CharField(
        "nome interno", max_length=120, blank=True, default="",
        help_text="Para reconhecer o texto na lista (ex.: 'Chamada — Natal'). Não aparece para o cliente.",
    )
    is_active = models.BooleanField(
        "exibir na Home", default=True,
        help_text="Desmarcado, as seções que usam este texto não são desenhadas.",
    )

    surface_color = models.CharField(
        "fundo da faixa", max_length=20, default="navy",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    eyebrow_color = models.CharField(
        "cor do sobretítulo", max_length=20, default="yellow",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    title_color = models.CharField(
        "cor do título", max_length=20, default="white",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    text_color = models.CharField(
        "cor do texto", max_length=20, default="ink-on-deep",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    cta_bg_color = models.CharField(
        "cor do botão", max_length=20, default="yellow",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    cta_text_color = models.CharField(
        "cor do texto do botão", max_length=20, default="navy",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    step_bg_color = models.CharField(
        "fundo dos blocos dos passos", max_length=20, default="navy-soft",
        validators=[validate_color], help_text=COLOR_HELP,
    )

    class Meta:
        verbose_name = "chamada final da Home"
        verbose_name_plural = "CHAMADA FINAL — faixa do fim da Home"
        ordering = ("id",)

    def __str__(self) -> str:
        return self.internal_name or f"Chamada final #{self.pk}"

    @classmethod
    def load(cls) -> "HomeCallout":
        """A chamada padrão (a primeira, `pk=1`), criada se não existir.

        Era o registro único; hoje é só o primeiro de uma lista — quem sabe
        qual chamada uma seção mostra é a `HomeSection.callout`.
        """
        obj, _criado = cls.objects.get_or_create(pk=1, defaults={"internal_name": "Chamada final"})
        return obj

    @property
    def eyebrow(self) -> str:
        return self.tr("eyebrow")

    @property
    def title(self) -> str:
        return self.tr("title")

    @property
    def text(self) -> str:
        return self.tr("text")

    @property
    def cta_label(self) -> str:
        return self.tr("cta_label")

    @property
    def visible_steps(self) -> list:
        """Os passos ativos e com título. Um passo vazio não vira bloco."""
        return [p for p in self.steps.all() if p.is_active and p.title]

    @property
    def has_content(self) -> bool:
        """Há algo para mostrar? Sem isto o bloco viraria uma faixa vazia."""
        return bool(
            self.eyebrow or self.title or self.text or self.has_cta or self.visible_steps
        )

    @property
    def style(self) -> str:
        return css_vars({
            "callout-surface": resolve(self.surface_color, "navy"),
            "callout-eyebrow": resolve(self.eyebrow_color, "yellow"),
            "callout-title": resolve(self.title_color, "white"),
            "callout-text": resolve(self.text_color, "ink-on-deep"),
            "callout-cta-bg": resolve(self.cta_bg_color, "yellow"),
            "callout-cta-fg": resolve(self.cta_text_color, "navy"),
            "callout-step-bg": resolve(self.step_bg_color, "navy-soft"),
        })

    def clean(self):
        super().clean()
        errors = self.clean_cta() or {}
        # Todo texto da faixa precisa ser legível sobre o fundo escolhido.
        for tinta, campo in (
            (self.eyebrow_color, "eyebrow_color"),
            (self.title_color, "title_color"),
            (self.text_color, "text_color"),
        ):
            erro = check_contrast(tinta, self.surface_color, campo=campo)
            if erro:
                errors.update(erro)
        erro = check_contrast(self.cta_text_color, self.cta_bg_color, campo="cta_text_color")
        if erro:
            errors.update(erro)
        if errors:
            raise ValidationError(errors)


class HomeCalloutTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeCallout, verbose_name="chamada", related_name="translations", on_delete=models.CASCADE
    )
    eyebrow = models.CharField("sobretítulo", max_length=80, blank=True)
    title = models.CharField("título", max_length=200, blank=True)
    text = models.TextField("texto", blank=True)
    cta_label = models.CharField("texto do botão", max_length=80, blank=True)

    class Meta:
        verbose_name = "tradução da chamada"
        verbose_name_plural = "traduções da chamada"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_callout_translation_unique_language"
            ),
        ]


# ---------------------------------------------------------------------------
# 4. Cards de categoria — os blocos coloridos abaixo do hero
# ---------------------------------------------------------------------------


class HomeCategoryCardQuerySet(models.QuerySet):
    def for_display(self):
        # `category__translations` não é zelo excessivo: o título do bloco cai
        # no nome da categoria quando não há um próprio, e esse nome é
        # traduzido — sem o prefetch seria uma consulta por bloco.
        return (
            self.filter(is_active=True)
            .select_related("category")
            .order_by("sort_order", "id")
            .prefetch_related("translations", "category__translations")
        )


class HomeCategoryCard(TranslatableMixin, TimeStampedModel):
    """Um bloco colorido que leva a uma categoria.

    É a peça de identidade mais colorida da Home: fundo cheio, título grande
    em Baloo e uma seta. Diferente de `HomeCard` (aquele é "como
    trabalhamos" — ícone, título e um parágrafo sobre um card branco); aqui o
    bloco é a cor.

    O destino é uma **categoria do catálogo**, por chave estrangeira: o link
    continua válido se o slug mudar, e não há uma segunda lista de categorias
    para sair de sincronia. O título é livre porque a Home às vezes quer
    chamar "Modelos 3D" o que no catálogo é "Impressões 3D".
    """

    translatable_fields = ("eyebrow", "title", "text")

    section = models.ForeignKey(
        HomeSection,
        verbose_name="seção",
        related_name="category_cards",
        null=True,
        on_delete=models.CASCADE,
        help_text="A seção 'Categorias em destaque' em que este bloco aparece.",
    )
    internal_name = models.CharField(
        "nome interno",
        max_length=120,
        help_text="Identificação administrativa. Não aparece para o cliente.",
    )
    category = models.ForeignKey(
        Category,
        verbose_name="categoria de destino",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Sem categoria, o bloco aparece sem link.",
    )
    icon = models.CharField("ícone", max_length=20, choices=CARD_ICONS, blank=True, default="")
    image = models.FileField(
        "imagem",
        upload_to=banner_upload_to,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=list(BANNER_IMAGE_EXTENSIONS))],
        help_text="Opcional, no lugar do ícone. Proporção quadrada (ex.: 400 × 400 px).",
    )

    bg_color = models.CharField(
        "cor de fundo", max_length=20, default="purple",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    text_color = models.CharField(
        "cor do texto", max_length=20, default="white",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    accent_color = models.CharField(
        "cor do ícone e da seta", max_length=20, blank=True, default="",
        validators=[validate_color],
        help_text="Em branco, acompanha a cor do texto. " + COLOR_HELP,
    )

    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    objects = HomeCategoryCardQuerySet.as_manager()

    class Meta:
        verbose_name = "card de categoria"
        verbose_name_plural = "CATEGORIAS — blocos coloridos da Home"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="home_catcard_active_idx"),
        ]

    def __str__(self) -> str:
        return self.internal_name

    @property
    def eyebrow(self) -> str:
        return self.tr("eyebrow")

    @property
    def title(self) -> str:
        """O título cadastrado, ou o nome da categoria.

        O nome da categoria é lido SÓ quando não há título próprio: passá-lo
        como `default=` avaliava a expressão sempre, e como `Category.name` é
        traduzido isso custava uma consulta por bloco mesmo quando o título
        estava preenchido.
        """
        proprio = self.tr("title")
        if proprio:
            return proprio
        return self.category.name if self.category_id else ""

    @property
    def text(self) -> str:
        return self.tr("text")

    @property
    def link(self) -> str:
        return self.category.get_absolute_url() if self.category_id else ""

    @property
    def style(self) -> str:
        return css_vars({
            "card-bg": resolve(self.bg_color, "purple"),
            "card-fg": resolve(self.text_color, "white"),
            "card-accent": resolve(self.accent_color) or resolve(self.text_color, "white"),
        })

    def clean(self):
        super().clean()
        erro = check_contrast(self.text_color, self.bg_color, campo="text_color")
        if erro:
            raise ValidationError(erro)


class HomeCategoryCardTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeCategoryCard, verbose_name="card", related_name="translations",
        on_delete=models.CASCADE,
    )
    eyebrow = models.CharField(
        "linha de cima", max_length=60, blank=True,
        help_text='O texto pequeno em maiúsculas. Ex.: "48 modelos".',
    )
    title = models.CharField(
        "título", max_length=80, blank=True,
        help_text="Em branco, usa o nome da categoria.",
    )
    text = models.CharField("descrição curta", max_length=160, blank=True)

    class Meta:
        verbose_name = "tradução do card"
        verbose_name_plural = "traduções do card"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_catcard_translation_unique_language"
            ),
        ]


# ---------------------------------------------------------------------------
# 5. Os passos da chamada final
# ---------------------------------------------------------------------------


class HomeCalloutStep(TranslatableMixin, TimeStampedModel):
    """Um dos passos numerados ao lado da chamada final.

    O número não é cadastrado: é a posição na ordem. Cadastrar "1, 2, 3" à mão
    e depois desativar o segundo deixaria "1, 3" na tela.
    """

    translatable_fields = ("title", "text")

    callout = models.ForeignKey(
        HomeCallout, verbose_name="chamada", related_name="steps", on_delete=models.CASCADE
    )
    internal_name = models.CharField("nome interno", max_length=120)
    icon = models.CharField("ícone", max_length=20, choices=CARD_ICONS, blank=True, default="")
    accent_color = models.CharField(
        "cor do bloco do número", max_length=20, default="mint",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "passo da chamada"
        verbose_name_plural = "PASSOS — os blocos numerados"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.internal_name

    @property
    def title(self) -> str:
        return self.tr("title")

    @property
    def text(self) -> str:
        return self.tr("text")

    @property
    def style(self) -> str:
        return css_vars({"step-accent": resolve(self.accent_color, "mint")})

    def clean(self):
        super().clean()
        # O número é escrito em navy sobre o bloco colorido.
        erro = check_contrast("navy", self.accent_color, campo="accent_color")
        if erro:
            raise ValidationError(erro)


class HomeCalloutStepTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeCalloutStep, verbose_name="passo", related_name="translations",
        on_delete=models.CASCADE,
    )
    title = models.CharField("título", max_length=120, blank=True)
    text = models.CharField("descrição", max_length=200, blank=True)

    class Meta:
        verbose_name = "tradução do passo"
        verbose_name_plural = "traduções do passo"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_step_translation_unique_language"
            ),
        ]


# ---------------------------------------------------------------------------
# 6. "Feito na Bélgica" — o bloco institucional
# ---------------------------------------------------------------------------


class HomeAbout(TranslatableMixin, TimeStampedModel):
    """O bloco sobre a loja: quadro de imagem à esquerda, texto à direita.

    É o **conteúdo** de uma seção do tipo "Sobre a loja": a posição na Home é
    da `HomeSection` que aponta para cá, e pode haver mais de um texto (a
    seção escolhe qual). Desativado, ele não desenha nada — nem uma faixa
    vazia. As pílulas (`HomeAboutBadge`) continuam sendo dele.

    O quadro da imagem segue o mesmo conceito do hero: a moldura listrada faz
    parte do desenho e continua visível em volta da foto.
    """

    translatable_fields = ("eyebrow", "title", "text")

    internal_name = models.CharField(
        "nome interno", max_length=120, blank=True, default="",
        help_text="Para reconhecer o bloco na lista. Não aparece para o cliente.",
    )
    is_active = models.BooleanField(
        "exibir na Home", default=True,
        help_text="Desmarcado, as seções que usam este bloco não são desenhadas.",
    )
    image = models.FileField(
        "imagem",
        upload_to=banner_upload_to,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=list(BANNER_IMAGE_EXTENSIONS))],
        help_text="Entra dentro do quadro. Proporção 4:3 (ex.: 1000 × 750 px).",
    )
    frame_color = models.CharField(
        "cor do quadro", max_length=10,
        choices=[(v, r) for v, r, _c, _e, _t in FRAME_COLORS], default="yellow",
    )
    surface_color = models.CharField(
        "fundo do bloco", max_length=20, default="cream",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    text_color = models.CharField(
        "cor do texto", max_length=20, default="ink-soft",
        validators=[validate_color], help_text=COLOR_HELP,
    )

    class Meta:
        verbose_name = "bloco Sobre a loja"
        verbose_name_plural = "SOBRE A LOJA — quadro de imagem e texto"
        ordering = ("id",)

    def __str__(self) -> str:
        return self.internal_name or f"Sobre a loja #{self.pk}"

    @classmethod
    def load(cls) -> "HomeAbout":
        """O bloco padrão (o primeiro, `pk=1`), criado se não existir."""
        obj, _criado = cls.objects.get_or_create(pk=1, defaults={"internal_name": "Sobre a loja"})
        return obj

    @property
    def eyebrow(self) -> str:
        return self.tr("eyebrow")

    @property
    def title(self) -> str:
        return self.tr("title")

    @property
    def text(self) -> str:
        return self.tr("text")

    @property
    def image_alt(self) -> str:
        return self.tr("image_alt", default=self.tr("title"))

    @property
    def visible_badges(self) -> list["HomeAboutBadge"]:
        return [b for b in self.badges.all() if b.is_active and b.text]

    @property
    def has_content(self) -> bool:
        return bool(self.title or self.text or self.image or self.visible_badges)

    def _frame(self, campo):
        for linha in FRAME_COLORS:
            if linha[0] == self.frame_color:
                return linha[campo]
        return FRAME_COLORS[0][campo]

    @property
    def style(self) -> str:
        return css_vars({
            "about-surface": resolve(self.surface_color, "cream"),
            "about-fg": resolve(self.text_color, "ink-soft"),
            "about-frame-a": self._frame(2),
            "about-frame-b": self._frame(3),
            "about-frame-line": self._frame(4),
        })


class HomeAboutTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeAbout, verbose_name="bloco", related_name="translations", on_delete=models.CASCADE
    )
    eyebrow = models.CharField("sobretítulo", max_length=80, blank=True)
    title = models.CharField("título", max_length=200, blank=True)
    text = models.TextField("texto", blank=True)
    image_alt = models.CharField(
        "texto alternativo da imagem", max_length=200, blank=True,
        help_text="Descrição da imagem para leitores de tela.",
    )

    class Meta:
        verbose_name = "tradução do bloco"
        verbose_name_plural = "traduções do bloco"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_about_translation_unique_language"
            ),
        ]


class HomeAboutBadge(TranslatableMixin, TimeStampedModel):
    """Uma das pílulas coloridas abaixo do texto institucional."""

    translatable_fields = ("text",)

    about = models.ForeignKey(
        HomeAbout, verbose_name="bloco", related_name="badges", on_delete=models.CASCADE
    )
    internal_name = models.CharField("nome interno", max_length=120)
    bg_color = models.CharField(
        "cor de fundo", max_length=20, default="lavender",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    text_color = models.CharField(
        "cor do texto", max_length=20, default="purple",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "pílula"
        verbose_name_plural = "PÍLULAS — as etiquetas coloridas"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.internal_name

    @property
    def text(self) -> str:
        return self.tr("text")

    @property
    def style(self) -> str:
        return css_vars({
            "pill-bg": resolve(self.bg_color, "lavender"),
            "pill-fg": resolve(self.text_color, "purple"),
        })

    def clean(self):
        super().clean()
        erro = check_contrast(self.text_color, self.bg_color, campo="text_color")
        if erro:
            raise ValidationError(erro)


class HomeAboutBadgeTranslation(TranslationBase):
    master = models.ForeignKey(
        HomeAboutBadge, verbose_name="pílula", related_name="translations",
        on_delete=models.CASCADE,
    )
    text = models.CharField("texto", max_length=80, blank=True)

    class Meta:
        verbose_name = "tradução da pílula"
        verbose_name_plural = "traduções da pílula"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="home_badge_translation_unique_language"
            ),
        ]
