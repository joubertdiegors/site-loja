"""Conteúdo gerenciável da Home.

Três blocos:

1. ``CtaMixin``      — botão que aponta para categoria, produto ou URL.
2. ``HomeBanner``    — área grande do topo (hero).
3. ``HomeSection``   — faixas de produtos, ordenadas e configuráveis.

Nenhuma seção é escrita no template: a Home percorre as seções ativas e
renderiza cada uma conforme a sua configuração.
"""

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.text import get_valid_filename

from apps.catalog.models import Product
from apps.categories.models import Category
from apps.core.models import TimeStampedModel, TranslatableMixin, TranslationBase

#: Quantidade máxima de produtos que uma seção pode exibir.
MAX_PRODUCT_LIMIT = 24


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

    translatable_fields = ("title", "subtitle", "cta_label", "image_alt")

    internal_name = models.CharField(
        "nome interno",
        max_length=120,
        help_text="Identificação administrativa. Não aparece para o cliente.",
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
# 3. Seções
# ---------------------------------------------------------------------------


class HomeSectionType(models.TextChoices):
    MANUAL_PRODUCTS = "manual", "Produtos escolhidos manualmente"
    CATEGORY_PRODUCTS = "category", "Produtos de uma categoria"
    FEATURED_PRODUCTS = "featured", "Produtos em destaque"
    NEWEST_PRODUCTS = "newest", "Novidades (produtos mais recentes)"
    BEST_SELLERS = "best_sellers", "Mais vendidos (aguarda o módulo de pedidos)"


class HomeSectionLayout(models.TextChoices):
    GRID = "grid", "Grade"
    CAROUSEL = "carousel", "Carrossel"


class HomeSectionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def ordered(self):
        return self.order_by("sort_order", "id")


class HomeSection(TranslatableMixin, CtaMixin, TimeStampedModel):
    """Uma faixa de produtos da Home.

    O tipo (``section_type``) decide de onde vêm os produtos; o service em
    ``apps/home/services.py`` faz a resolução. Acrescentar um tipo novo
    (promoções, lançamentos de uma marca) é acrescentar uma opção aqui e um
    resolvedor lá — o template não muda.
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

#: As três cores que os cards já usavam. Um seletor de cor livre deixaria a
#: Home sair do padrão da marca no primeiro cadastro distraído.
CARD_ACCENTS = (
    ("brand", "Roxo"),
    ("cyan", "Ciano"),
    ("magenta", "Magenta"),
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
    """A faixa escura no fim da Home — sobretítulo, título, texto e botão.

    **Uma linha só** (`pk=1`): é *a* chamada final, não uma lista delas. O
    botão reaproveita o `CtaMixin`, o mesmo do banner e das seções, então pode
    apontar para uma categoria ou um produto e continuar válido se o slug
    mudar.

    Tudo é opcional. Sem título, sem texto e sem botão o bloco inteiro some da
    Home em vez de virar uma faixa escura vazia.
    """

    translatable_fields = ("eyebrow", "title", "text", "cta_label")

    is_active = models.BooleanField("exibir na Home", default=True)

    class Meta:
        verbose_name = "chamada final da Home"
        verbose_name_plural = "CHAMADA FINAL — faixa do fim da Home"

    def __str__(self) -> str:
        return "Chamada final da Home"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "HomeCallout":
        obj, _criado = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def current(cls) -> "HomeCallout | None":
        """A chamada em uso, ou `None` para o template usar o texto padrão."""
        return cls.objects.filter(pk=1, is_active=True).prefetch_related("translations").first()

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
    def has_content(self) -> bool:
        """Há algo para mostrar? Sem isto o bloco viraria uma faixa vazia."""
        return bool(self.eyebrow or self.title or self.text or self.has_cta)

    def clean(self):
        super().clean()
        errors = self.clean_cta()
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
