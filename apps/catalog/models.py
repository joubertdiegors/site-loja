"""Modelos do catálogo.

Organização do arquivo:

1. Atributos reutilizáveis: ``Brand``, ``Material``, ``Color``
2. Produto: ``Product`` + ``ProductTranslation``
3. Mídia: ``ProductMedia``

Decisão de arquitetura (variantes)
----------------------------------
``Product`` é o produto base. Os campos comerciais (SKU, preço, estoque, cor)
vivem hoje no produto porque ainda não existem variantes. Quando
``ProductVariant`` for criado, ele receberá esses mesmos campos e os do produto
passarão a ser *valores padrão* herdados pela variante. Nada aqui impede essa
migração: cor e material são relações M2M (nunca colunas de texto), e o preço
é calculado por funções puras em ``pricing.py``, reaproveitáveis pela variante.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models, transaction
from django.utils.text import get_valid_filename, slugify

from apps.catalog import pricing
from apps.categories.models import Category
from apps.core.constants import (
    CURRENCY_SYMBOLS,
    DEFAULT_CURRENCY,
    DEFAULT_LANGUAGE,
    LENGTH_UNIT_TO_MM,
    Currency,
    LengthUnit,
)
from apps.core.models import AuditableModel, TimeStampedModel, TranslatableMixin, TranslationBase
from apps.core.utils import unique_slugify

# ---------------------------------------------------------------------------
# 1. Atributos reutilizáveis
# ---------------------------------------------------------------------------


class Brand(TimeStampedModel):
    """Marca/fabricante.

    Opcional para os produtos impressos pela própria JD PRINT, essencial
    quando o catálogo incluir impressoras, filamentos e ferramentas.
    """

    name = models.CharField("nome", max_length=120, unique=True)
    slug = models.SlugField("slug", max_length=140, unique=True, blank=True)
    website = models.URLField("site", blank=True)
    is_active = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "marca"
        verbose_name_plural = "marcas"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)


class Material(TimeStampedModel):
    """Material de fabricação (PLA, PETG, Resina, Madeira...).

    Tabela própria em vez de texto livre no produto: permite filtrar por
    material na loja, associar propriedades (densidade, custo por kg) depois e
    evita 'PLA', 'pla' e 'P.L.A.' convivendo no banco.
    """

    name = models.CharField("nome", max_length=80, unique=True)
    slug = models.SlugField("slug", max_length=100, unique=True, blank=True)
    description = models.CharField("descrição", max_length=255, blank=True)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "material"
        verbose_name_plural = "materiais"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)


#: A partir de quantas unidades o produto deixa de ser "últimas unidades".
LOW_STOCK_THRESHOLD = 3

HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    message="Informe uma cor hexadecimal válida, por exemplo #FF0000.",
)


class Color(TimeStampedModel):
    """Cor reutilizável.

    Hoje o produto base referencia cores (M2M). Quando existirem variantes, a
    variante passará a ter uma FK para ``Color`` — por isso a cor vive em
    tabela própria e não como campo do produto.

    RGB não é armazenado: é derivado do HEX (ver ``rgb``), evitando dois
    campos que podem divergir.
    """

    name = models.CharField("nome", max_length=60, unique=True)
    slug = models.SlugField("slug", max_length=80, unique=True, blank=True)
    hex_code = models.CharField(
        "código HEX",
        max_length=7,
        blank=True,
        validators=[HEX_COLOR_VALIDATOR],
        help_text="Ex.: #000000",
    )
    is_active = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "cor"
        verbose_name_plural = "cores"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def rgb(self):
        """Tupla (r, g, b) derivada do HEX, ou ``None`` se não houver HEX."""
        value = self.hex_code.lstrip("#")
        if len(value) == 3:
            value = "".join(char * 2 for char in value)
        if len(value) != 6:
            return None
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))

    def save(self, *args, **kwargs):
        if self.hex_code:
            self.hex_code = self.hex_code.strip().upper()
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# 2. Produto
# ---------------------------------------------------------------------------


class PersonalizationType(models.TextChoices):
    """O que o cliente precisa fornecer antes de comprar.

    É característica do produto, não categoria: "Gato personalizado" continua
    na categoria Gatos e só ganha o pedido de foto.
    """

    NONE = "none", "Nenhuma"
    PHOTO = "photo", "Foto (obrigatória)"
    TEXT = "text", "Texto (obrigatório)"
    PHOTO_OR_TEXT = "photo_or_text", "Foto ou texto (o cliente escolhe)"


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Rascunho"
    ACTIVE = "active", "Ativo"
    INACTIVE = "inactive", "Inativo"


class PricingMode(models.TextChoices):
    """Qual valor o administrador digita — o outro é sempre derivado.

    Sem esse campo, alterar o preço recalcularia a margem e alterar a margem
    recalcularia o preço, criando ida e volta com deriva de arredondamento.
    Assim existe sempre uma única fonte de verdade.
    """

    PRICE = "price", "Informar preço de venda (margem calculada)"
    MARGIN = "margin", "Informar margem de lucro (preço calculado)"


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=ProductStatus.ACTIVE)

    def featured(self):
        return self.active().filter(is_featured=True).order_by("featured_order", "-created_at")

    def with_translations(self):
        return self.prefetch_related("translations")

    def for_listing(self):
        return self.select_related("category", "brand").prefetch_related("translations", "media")


class Product(TranslatableMixin, AuditableModel):
    translatable_fields = ("name", "short_description", "description", "extra_information")

    # -- identificação -----------------------------------------------------
    sku = models.CharField(
        "SKU",
        max_length=64,
        unique=True,
        help_text="Código interno único. Ex.: GATO-POMPOM-01",
    )
    slug = models.SlugField(
        "slug",
        max_length=220,
        unique=True,
        blank=True,
        help_text=(
            "Identificador para URL. Se deixado em branco, é gerado a partir do "
            "nome em português. URLs por idioma poderão ser adicionadas depois "
            "com um slug por tradução."
        ),
    )
    status = models.CharField(
        "status",
        max_length=16,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
        db_index=True,
    )

    # -- classificação -----------------------------------------------------
    category = models.ForeignKey(
        Category,
        verbose_name="categoria",
        related_name="products",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text=(
            "Obrigatória para ativar o produto. Subcategoria é apenas uma "
            "categoria que possui pai."
        ),
    )
    brand = models.ForeignKey(
        Brand,
        verbose_name="marca",
        related_name="products",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    materials = models.ManyToManyField(
        Material,
        verbose_name="materiais",
        related_name="products",
        blank=True,
    )
    colors = models.ManyToManyField(
        Color,
        verbose_name="cores",
        related_name="products",
        blank=True,
        help_text="Cores do produto base. Migrará para a variante no futuro.",
    )

    # -- características físicas -------------------------------------------
    width = models.DecimalField("largura", max_digits=10, decimal_places=2, null=True, blank=True)
    height = models.DecimalField("altura", max_digits=10, decimal_places=2, null=True, blank=True)
    depth = models.DecimalField(
        "profundidade", max_digits=10, decimal_places=2, null=True, blank=True
    )
    dimension_unit = models.CharField(
        "unidade das dimensões",
        max_length=4,
        choices=LengthUnit.choices,
        default=LengthUnit.MM,
    )
    weight_grams = models.DecimalField(
        "peso (g)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Sempre em gramas. A unidade é fixa para permitir cálculo de frete.",
    )

    # -- produção ----------------------------------------------------------
    print_time = models.DurationField(
        "tempo de impressão",
        null=True,
        blank=True,
        help_text="Formato HH:MM:SS. Ex.: 02:35:00 para 2 horas e 35 minutos.",
    )

    # -- custos ------------------------------------------------------------
    currency = models.CharField(
        "moeda", max_length=3, choices=Currency.choices, default=DEFAULT_CURRENCY
    )
    filament_cost = models.DecimalField(
        "custo de filamento", max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    energy_cost = models.DecimalField(
        "custo de energia", max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    total_cost = models.DecimalField(
        "custo total",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
        help_text="Calculado automaticamente a partir dos componentes de custo.",
    )

    # -- preço -------------------------------------------------------------
    pricing_mode = models.CharField(
        "definir preço por",
        max_length=8,
        choices=PricingMode.choices,
        default=PricingMode.PRICE,
    )
    sale_price = models.DecimalField(
        "preço de venda", max_digits=10, decimal_places=2, null=True, blank=True
    )
    profit_margin = models.DecimalField(
        "margem de lucro (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Margem sobre o preço de venda: (preço - custo) / preço × 100.",
    )

    # -- estoque -----------------------------------------------------------
    # Etapa 1: um contador simples. O controle completo (entradas, saídas,
    # reservas, ajustes, estoque por variante e por localização) virá em um app
    # próprio com um modelo de movimentações; este campo passará a ser o saldo
    # consolidado calculado a partir delas.
    stock_quantity = models.PositiveIntegerField("quantidade em estoque", default=0)
    allow_backorder = models.BooleanField(
        "permitir venda sem estoque",
        default=False,
        help_text="Permite vender mesmo com estoque zerado.",
    )
    made_to_order = models.BooleanField("produto sob encomenda", default=False)
    production_lead_time_days = models.PositiveIntegerField(
        "prazo médio de produção (dias)",
        null=True,
        blank=True,
        help_text="Obrigatório para produtos sob encomenda.",
    )

    # -- personalização ----------------------------------------------------
    personalization_type = models.CharField(
        "tipo de personalização",
        max_length=16,
        choices=PersonalizationType.choices,
        default=PersonalizationType.NONE,
        help_text=(
            "Define se o cliente deverá fornecer uma foto, um texto ou escolher "
            "entre os dois antes de adicionar o produto ao carrinho."
        ),
    )
    personalization_text_limit = models.PositiveIntegerField(
        "limite de caracteres do texto",
        default=200,
        help_text="Usado quando a personalização aceita texto.",
    )

    # -- destaque ----------------------------------------------------------
    is_featured = models.BooleanField("produto em destaque", default=False)
    featured_order = models.PositiveIntegerField(
        "ordem no destaque",
        default=0,
        help_text="Menor valor aparece primeiro na home.",
    )

    objects = ProductQuerySet.as_manager()

    class Meta:
        verbose_name = "produto"
        verbose_name_plural = "produtos"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "-created_at"], name="product_status_created_idx"),
            models.Index(fields=["is_featured", "featured_order"], name="product_featured_idx"),
            models.Index(fields=["category", "status"], name="product_category_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(filament_cost__gte=0),
                name="product_filament_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(energy_cost__gte=0),
                name="product_energy_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_cost__gte=0),
                name="product_total_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(sale_price__isnull=True) | models.Q(sale_price__gte=0),
                name="product_sale_price_not_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(profit_margin__isnull=True)
                    | models.Q(
                        profit_margin__gte=pricing.MIN_STORED_MARGIN,
                        profit_margin__lte=pricing.MAX_STORED_MARGIN,
                    )
                ),
                name="product_profit_margin_within_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(weight_grams__isnull=True) | models.Q(weight_grams__gte=0),
                name="product_weight_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(width__isnull=True) | models.Q(width__gte=0),
                name="product_width_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(height__isnull=True) | models.Q(height__gte=0),
                name="product_height_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(depth__isnull=True) | models.Q(depth__gte=0),
                name="product_depth_not_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(print_time__isnull=True) | models.Q(print_time__gte=timedelta(0))
                ),
                name="product_print_time_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sku} — {self.display_name}"

    # -- conteúdo traduzido ------------------------------------------------

    @property
    def display_name(self) -> str:
        return self.tr("name", default=self.sku)

    def name_in(self, language: str) -> str:
        return self.tr("name", language=language, default=self.sku)

    def has_default_translation(self) -> bool:
        translation = self.translations_by_language().get(DEFAULT_LANGUAGE.value)
        return bool(translation and translation.name.strip())

    # -- físico ------------------------------------------------------------

    @property
    def currency_symbol(self) -> str:
        return CURRENCY_SYMBOLS.get(self.currency, self.currency)

    def dimensions_display(self) -> str:
        if self.width is None and self.height is None and self.depth is None:
            return ""
        parts = [
            "?" if value is None else f"{value.normalize():f}"
            for value in (self.width, self.height, self.depth)
        ]
        return f"{' × '.join(parts)} {self.dimension_unit}"

    def dimensions_in_mm(self):
        """Dimensões normalizadas em mm (útil para frete e planejamento)."""
        factor = LENGTH_UNIT_TO_MM[LengthUnit(self.dimension_unit)]
        return tuple(
            None if value is None else value * factor
            for value in (self.width, self.height, self.depth)
        )

    @property
    def weight_kg(self):
        if self.weight_grams is None:
            return None
        return (self.weight_grams / Decimal("1000")).quantize(Decimal("0.001"))

    def print_time_display(self) -> str:
        if not self.print_time:
            return ""
        total_minutes = int(self.print_time.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes}min"
        if hours:
            return f"{hours}h"
        return f"{minutes}min"

    # -- custos e preço ----------------------------------------------------

    def cost_components(self):
        """Componentes que formam o custo total.

        Ponto de extensão: mão de obra, embalagem, manutenção, desperdício e
        taxas entram como novas chaves aqui — o cálculo de custo total, de
        margem e de preço continua o mesmo.
        """
        return {
            "filament": self.filament_cost or Decimal("0.00"),
            "energy": self.energy_cost or Decimal("0.00"),
        }

    def recalculate_pricing(self) -> None:
        """Recalcula o custo total e o lado derivado do par preço/margem."""
        self.total_cost = pricing.total_cost(self.cost_components())

        if self.pricing_mode == PricingMode.MARGIN:
            if self.profit_margin is not None:
                self.profit_margin = pricing.percent(self.profit_margin)
                self.sale_price = pricing.price_from_margin(self.total_cost, self.profit_margin)
        elif self.sale_price is not None:
            self.sale_price = pricing.money(self.sale_price)
            self.profit_margin = pricing.clamp_stored_margin(
                pricing.margin_from_price(self.total_cost, self.sale_price)
            )
        else:
            # Sem preço não existe margem a exibir.
            self.profit_margin = None

    @property
    def effective_margin(self):
        """Margem real do preço gravado.

        No modo MARGIN o preço é arredondado para 2 casas, então a margem real
        pode diferir da desejada em centésimos. Este valor mostra a margem que
        de fato será obtida.
        """
        if self.sale_price is None:
            return None
        return pricing.margin_from_price(self.total_cost, self.sale_price)

    @property
    def profit(self):
        if self.sale_price is None:
            return None
        return pricing.profit(self.total_cost, self.sale_price)

    # -- personalização ----------------------------------------------------

    @property
    def needs_personalization(self) -> bool:
        return self.personalization_type != PersonalizationType.NONE

    @property
    def accepts_personalization_photo(self) -> bool:
        return self.personalization_type in {
            PersonalizationType.PHOTO,
            PersonalizationType.PHOTO_OR_TEXT,
        }

    @property
    def accepts_personalization_text(self) -> bool:
        return self.personalization_type in {
            PersonalizationType.TEXT,
            PersonalizationType.PHOTO_OR_TEXT,
        }

    # -- variantes ---------------------------------------------------------

    def active_variants(self) -> list:
        """Variantes ativas, lidas do prefetch quando houver."""
        return [variant for variant in self.variants.all() if variant.is_active]

    @property
    def has_variants(self) -> bool:
        return bool(self.active_variants())

    @property
    def price_range(self):
        """(menor, maior) preço entre as variantes, ou (preço, preço)."""
        prices = [variant.effective_price for variant in self.active_variants()]
        prices = [price for price in prices if price is not None]
        if not prices:
            return (self.sale_price, self.sale_price)
        return (min(prices), max(prices))

    @property
    def has_price_range(self) -> bool:
        low, high = self.price_range
        return low is not None and high is not None and low != high

    # -- estoque -----------------------------------------------------------

    @property
    def available_stock(self) -> int:
        """Estoque somado das variantes, ou o do próprio produto."""
        variants = self.active_variants()
        if variants:
            return sum(variant.stock_quantity for variant in variants)
        return self.stock_quantity

    @property
    def is_available(self) -> bool:
        """Produto sob encomenda nunca depende de estoque.

        Com variantes, basta uma delas estar disponível.
        """
        if self.made_to_order or self.allow_backorder:
            return True
        variants = self.active_variants()
        if variants:
            return any(variant.is_available for variant in variants)
        return self.stock_quantity > 0

    @property
    def stock_state(self) -> str:
        """Rótulo do estado de estoque: made_to_order/out/low/in."""
        if self.made_to_order:
            return "made_to_order"
        if not self.is_available:
            return "out"
        if self.allow_backorder:
            return "in"
        if 0 < self.available_stock <= LOW_STOCK_THRESHOLD:
            return "low"
        return "in"

    # -- mídia -------------------------------------------------------------

    @property
    def primary_media(self):
        for item in self.media.all():
            if item.is_primary:
                return item
        return None

    @property
    def display_short_description(self) -> str:
        return self.tr("short_description")

    @property
    def display_description(self) -> str:
        return self.tr("description")

    @property
    def display_extra_information(self) -> str:
        return self.tr("extra_information")

    @property
    def display_media(self):
        """Imagem a usar no card: principal -> primeira imagem/GIF -> nada.

        Vídeo não serve como capa de card, então é ignorado aqui mesmo quando
        está marcado como principal. Percorre ``self.media.all()`` em memória:
        nenhuma consulta extra quando a listagem usa
        ``prefetch_related("media")``. Devolver ``None`` é um caso normal — o
        template mostra um espaço reservado.
        """
        pictures = [
            item for item in self.media.all() if item.media_type in {MediaType.IMAGE, MediaType.GIF}
        ]
        for item in pictures:
            if item.is_primary:
                return item
        return pictures[0] if pictures else None

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("catalog:product_detail", kwargs={"slug": self.slug})

    # -- validação ---------------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}

        if self.sku:
            self.sku = self.sku.strip().upper()
        else:
            errors["sku"] = "Informe o SKU."

        negative_check_fields = (
            "filament_cost",
            "energy_cost",
            "sale_price",
            "weight_grams",
            "width",
            "height",
            "depth",
        )
        for field in negative_check_fields:
            value = getattr(self, field)
            if value is not None and value < 0:
                errors[field] = "O valor não pode ser negativo."

        # A margem só é entrada de dados no modo MARGIN; no modo PRICE ela é
        # derivada do preço e pode legitimamente ser 100% (sem custo) ou
        # negativa (venda abaixo do custo).
        if self.pricing_mode == PricingMode.MARGIN and self.profit_margin is not None:
            if not (pricing.MIN_MARGIN <= self.profit_margin <= pricing.MAX_MARGIN):
                errors["profit_margin"] = (
                    f"A margem deve estar entre {pricing.MIN_MARGIN}% e {pricing.MAX_MARGIN}%."
                )

        if self.print_time is not None and self.print_time < timedelta(0):
            errors["print_time"] = "O tempo de impressão não pode ser negativo."

        if self.pricing_mode == PricingMode.MARGIN and self.profit_margin is None:
            errors["profit_margin"] = "Informe a margem desejada ou volte o modo para preço de venda."

        if self.made_to_order and self.production_lead_time_days is None:
            errors["production_lead_time_days"] = (
                "Informe o prazo médio de produção para produtos sob encomenda."
            )

        errors.update(self._validate_activation())

        if errors:
            raise ValidationError(errors)

    def _validate_activation(self):
        """Informações mínimas exigidas de um produto ativo."""
        if self.status != ProductStatus.ACTIVE:
            return {}

        errors = {}

        if self.category_id is None:
            errors["category"] = "Um produto ativo precisa de categoria."

        price = self.sale_price
        if (
            price is None
            and self.pricing_mode == PricingMode.MARGIN
            and self.profit_margin is not None
        ):
            price = pricing.price_from_margin(
                pricing.total_cost(self.cost_components()), self.profit_margin
            )
        if price is None or price <= 0:
            errors["sale_price"] = "Um produto ativo precisa de preço de venda maior que zero."

        # Só é possível checar traduções depois que o produto existe; na
        # criação pelo admin, o formset de traduções faz essa validação.
        if self.pk and not self.has_default_translation():
            errors["status"] = "Um produto ativo precisa do nome em português."

        return errors

    # -- persistência ------------------------------------------------------

    def _slug_source(self) -> str:
        return self.tr("name", language=DEFAULT_LANGUAGE.value, default="") or self.sku

    def save(self, *args, **kwargs):
        self.sku = (self.sku or "").strip().upper()
        if not self.slug:
            self.slug = unique_slugify(self, self._slug_source())
        self.recalculate_pricing()

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "total_cost",
                "sale_price",
                "profit_margin",
                "updated_at",
            }

        super().save(*args, **kwargs)


class ProductVariant(TimeStampedModel):
    """Uma configuração comercial concreta do produto.

    O produto base responde "o que é"; a variante responde "qual exatamente
    estou comprando" — e é ela que carrega preço, estoque e SKU quando a loja
    precisa diferenciá-los:

        Caneca personalizada
        ├── Preto  / 300 ml → €15,00 → estoque 10
        ├── Branco / 300 ml → €15,00 → estoque 5
        └── Preto  / 500 ml → €18,00 → estoque 3

    Regras de convivência com o produto base:

    * produto **sem** variantes continua funcionando exatamente como antes —
      variantes são aditivas, nada foi movido para fora de ``Product``;
    * campo nulo na variante **herda** o valor do produto (preço, peso,
      dimensões). Só se preenche o que difere;
    * ``stock_quantity`` é sempre da variante: é justamente o que não pode ser
      compartilhado.

    Os eixos são três (cor, tamanho, material) porque são os que a operação
    usa. Um sistema genérico de atributos pode ser acrescentado depois sem
    recriar esta tabela — a variante continua sendo a linha vendável.
    """

    product = models.ForeignKey(
        Product, verbose_name="produto", related_name="variants", on_delete=models.CASCADE
    )
    sku = models.CharField(
        "SKU",
        max_length=64,
        unique=True,
        help_text="Código próprio desta variante. Ex.: CANECA-PRETO-300",
    )
    color = models.ForeignKey(
        Color,
        verbose_name="cor",
        related_name="variants",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    material = models.ForeignKey(
        Material,
        verbose_name="material",
        related_name="variants",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    size = models.CharField(
        "tamanho",
        max_length=60,
        blank=True,
        help_text='Como o cliente vê. Ex.: "15 cm", "300 ml".',
    )

    sale_price = models.DecimalField(
        "preço de venda",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Deixe vazio para usar o preço do produto.",
    )
    stock_quantity = models.PositiveIntegerField("quantidade em estoque", default=0)

    weight_grams = models.DecimalField(
        "peso (g)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Deixe vazio para usar o peso do produto.",
    )
    width = models.DecimalField("largura", max_digits=10, decimal_places=2, null=True, blank=True)
    height = models.DecimalField("altura", max_digits=10, decimal_places=2, null=True, blank=True)
    depth = models.DecimalField(
        "profundidade", max_digits=10, decimal_places=2, null=True, blank=True
    )

    is_active = models.BooleanField("ativa", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "variante"
        verbose_name_plural = "variantes"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["product", "is_active"], name="variant_product_active_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sale_price__isnull=True) | models.Q(sale_price__gte=0),
                name="variant_sale_price_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(weight_grams__isnull=True) | models.Q(weight_grams__gte=0),
                name="variant_weight_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sku} — {self.label or self.product.display_name}"

    # -- apresentação ------------------------------------------------------

    @property
    def label(self) -> str:
        """Como a variante aparece para o cliente: "Preto · 15 cm"."""
        parts = [
            self.color.name if self.color_id else "",
            self.size,
            self.material.name if self.material_id else "",
        ]
        return " · ".join(part for part in parts if part)

    # -- valores herdados --------------------------------------------------

    @property
    def effective_price(self):
        return self.sale_price if self.sale_price is not None else self.product.sale_price

    @property
    def effective_weight(self):
        return self.weight_grams if self.weight_grams is not None else self.product.weight_grams

    @property
    def effective_dimensions(self):
        """(largura, altura, profundidade) — cada uma herda se estiver vazia."""
        return (
            self.width if self.width is not None else self.product.width,
            self.height if self.height is not None else self.product.height,
            self.depth if self.depth is not None else self.product.depth,
        )

    # -- disponibilidade ---------------------------------------------------

    @property
    def is_available(self) -> bool:
        if not self.is_active:
            return False
        if self.product.made_to_order or self.product.allow_backorder:
            return True
        return self.stock_quantity > 0

    # -- validação ---------------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}

        if self.sku:
            self.sku = self.sku.strip().upper()
        else:
            errors["sku"] = "Informe o SKU da variante."

        if not (self.color_id or self.material_id or self.size.strip()):
            errors["size"] = "Uma variante precisa de pelo menos cor, tamanho ou material."

        for field in ("sale_price", "weight_grams", "width", "height", "depth"):
            value = getattr(self, field)
            if value is not None and value < 0:
                errors[field] = "O valor não pode ser negativo."

        if self.product_id and not errors:
            duplicates = ProductVariant.objects.filter(
                product_id=self.product_id,
                color_id=self.color_id,
                material_id=self.material_id,
                size=self.size,
            ).exclude(pk=self.pk)
            if duplicates.exists():
                errors["size"] = "Já existe uma variante com esta combinação neste produto."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.sku = (self.sku or "").strip().upper()
        super().save(*args, **kwargs)


class ProductTranslation(TranslationBase):
    """Conteúdo textual do produto, uma linha por idioma.

    Campos técnicos (peso, dimensões, custo, SKU, preço, estoque) NÃO ficam
    aqui: eles não mudam com o idioma.
    """

    master = models.ForeignKey(
        Product,
        verbose_name="produto",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField("nome", max_length=200)
    short_description = models.CharField(
        "descrição curta",
        max_length=300,
        blank=True,
        help_text="Usada em cards, resultados de busca e listagens.",
    )
    description = models.TextField("descrição", blank=True)
    extra_information = models.TextField(
        "informações adicionais",
        blank=True,
        help_text="Cuidados, instruções de uso, avisos.",
    )

    class Meta:
        verbose_name = "tradução do produto"
        verbose_name_plural = "traduções do produto"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"],
                name="product_translation_unique_language",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="product_translation_name_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_language_display()})"

    def clean(self):
        super().clean()
        if not (self.name or "").strip():
            raise ValidationError({"name": "O nome é obrigatório."})


# ---------------------------------------------------------------------------
# 3. Mídia
# ---------------------------------------------------------------------------


class MediaType(models.TextChoices):
    IMAGE = "IMAGE", "Imagem"
    VIDEO = "VIDEO", "Vídeo"
    GIF = "GIF", "GIF"


MEDIA_TYPE_EXTENSIONS = {
    MediaType.IMAGE: ("jpg", "jpeg", "png", "webp", "avif"),
    MediaType.VIDEO: ("mp4", "webm", "mov", "m4v"),
    MediaType.GIF: ("gif",),
}

ALLOWED_MEDIA_EXTENSIONS = sorted(
    {extension for group in MEDIA_TYPE_EXTENSIONS.values() for extension in group}
)


def product_media_upload_to(instance, filename: str) -> str:
    """Arquivos agrupados por SKU: media/products/<sku>/<arquivo>."""
    sku = slugify(getattr(instance.product, "sku", "") or "sem-sku")
    return f"products/{sku}/{get_valid_filename(filename)}"


def validate_media_file_size(file) -> None:
    from django.conf import settings

    limit = getattr(settings, "PRODUCT_MEDIA_MAX_UPLOAD_SIZE", 50 * 1024 * 1024)
    if file.size and file.size > limit:
        raise ValidationError(f"Arquivo maior que o limite de {limit // (1024 * 1024)} MB.")


class ProductMedia(TimeStampedModel):
    """Fotos, vídeos e GIFs de um produto.

    O alt text fica aqui, no idioma padrão. Traduzi-lo seguiria exatamente o
    mesmo padrão de ``ProductTranslation`` (tabela filha com ``language``);
    não foi feito agora porque o Django Admin não suporta inline aninhado —
    exigiria uma tela dedicada, sem ganho nesta etapa.
    """

    product = models.ForeignKey(
        Product,
        verbose_name="produto",
        related_name="media",
        on_delete=models.CASCADE,
    )
    media_type = models.CharField(
        "tipo", max_length=8, choices=MediaType.choices, default=MediaType.IMAGE
    )
    file = models.FileField(
        "arquivo",
        upload_to=product_media_upload_to,
        validators=[
            FileExtensionValidator(allowed_extensions=ALLOWED_MEDIA_EXTENSIONS),
            validate_media_file_size,
        ],
    )
    alt_text = models.CharField(
        "texto alternativo",
        max_length=200,
        blank=True,
        help_text="Descrição da mídia para acessibilidade e SEO.",
    )
    sort_order = models.PositiveIntegerField("ordem", default=0)
    is_primary = models.BooleanField(
        "imagem principal",
        default=False,
        help_text="Apenas uma mídia por produto pode ser a principal.",
    )

    class Meta:
        verbose_name = "mídia do produto"
        verbose_name_plural = "mídias do produto"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_primary=True),
                name="product_media_single_primary",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "sort_order"], name="product_media_order_idx"),
        ]

    def __str__(self) -> str:
        name = self.file.name.rsplit("/", 1)[-1] if self.file else "sem arquivo"
        return f"{self.get_media_type_display()} — {name}"

    @property
    def extension(self) -> str:
        if not self.file:
            return ""
        return self.file.name.rsplit(".", 1)[-1].lower()

    def clean(self):
        super().clean()
        extension = self.extension
        allowed = MEDIA_TYPE_EXTENSIONS[MediaType(self.media_type)]
        if extension and extension not in allowed:
            raise ValidationError(
                {
                    "media_type": (
                        f"Arquivo .{extension} não corresponde ao tipo "
                        f"{self.get_media_type_display()} (aceitos: {', '.join(allowed)})."
                    )
                }
            )

    @transaction.atomic
    def save(self, *args, **kwargs):
        siblings = ProductMedia.objects.filter(product_id=self.product_id).exclude(pk=self.pk)

        if self.is_primary:
            # Zera a principal anterior ANTES de gravar, para não violar a
            # constraint de unicidade parcial.
            siblings.filter(is_primary=True).update(is_primary=False)
        elif not siblings.filter(is_primary=True).exists():
            # A primeira mídia do produto vira a principal automaticamente.
            self.is_primary = True

        super().save(*args, **kwargs)
