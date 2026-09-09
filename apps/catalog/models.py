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
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import connection, models, transaction
from django.db.models.functions import Coalesce
from django.utils.text import get_valid_filename, slugify
from django.utils.translation import gettext as _

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


class Material(TranslatableMixin, TimeStampedModel):
    """Material de fabricação (PLA, PETG, Resina, Madeira...).

    Tabela própria em vez de texto livre no produto: permite filtrar por
    material na loja, associar propriedades (densidade, custo por kg) depois e
    evita 'PLA', 'pla' e 'P.L.A.' convivendo no banco.

    ``name`` é o nome **interno** — o que o administrador digita, procura e vê
    nas listagens, e o que gera o slug e garante unicidade. O que o cliente lê
    é ``display_name``, que vem de ``MaterialTranslation``. Um só registro de
    material serve os quatro idiomas: a variante não é duplicada por idioma,
    e continua apontando para um único ``Material``.

    "PLA" e "PETG" são nomes próprios e ficam iguais em toda parte; "Resina" e
    "Madeira" não — e era isso que aparecia em português numa loja francesa.
    """

    translatable_fields = ("name", "description")

    name = models.CharField(
        "nome interno",
        max_length=80,
        unique=True,
        help_text="Como o material aparece no Admin. O nome que o cliente lê vem das traduções.",
    )
    slug = models.SlugField("slug", max_length=100, unique=True, blank=True)
    description = models.CharField("descrição", max_length=255, blank=True)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "material"
        verbose_name_plural = "materiais"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        """O nome que o cliente lê, no idioma de conteúdo atual.

        Sem tradução no idioma pedido, cai no português e depois em qualquer
        uma que exista — o mesmo fallback de produto, categoria e cor. Sem
        tradução nenhuma, devolve o nome interno: melhor "Resina" em francês do
        que um espaço em branco na ficha técnica.
        """
        return self.tr("name", default=self.name)

    @property
    def display_description(self) -> str:
        """A descrição rica no idioma atual — ou em português, ou nada.

        O fallback é mais estreito de propósito do que o de `display_name`.
        Um nome sem tradução vale em qualquer idioma («PLA» é PLA), e por isso
        `tr()` vasculha todos até achar algum. Um **texto** não: mostrar dois
        parágrafos em francês a um cliente holandês não é um fallback, é o
        idioma errado na tela. Então são duas tentativas — o idioma pedido e o
        padrão da loja — e, se nenhuma existir, a seção simplesmente não
        aparece.

        O HTML volta limpo: o que está no banco foi sanitizado ao gravar, e
        `{% rich_html %}` sanitiza de novo ao desenhar.
        """
        atual = self.tr("description", fallback=False)
        if atual:
            return atual
        return self.tr("description", language=DEFAULT_LANGUAGE.value, fallback=False)

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


class Color(TranslatableMixin, TimeStampedModel):
    """Cor reutilizável, apresentada no idioma do cliente.

    A variante aponta para cá com uma FK: a cor é um eixo da unidade vendável,
    não uma etiqueta do produto.

    ``name`` é o nome **interno** — o que o administrador digita, procura e vê
    nas listagens, e o que gera o slug e garante unicidade. O que o cliente lê é
    ``display_name``, que vem de ``ColorTranslation``. Um só registro de cor
    serve os quatro idiomas: a variante não é duplicada por idioma.

    RGB não é armazenado: é derivado do HEX (ver ``rgb``), evitando dois
    campos que podem divergir.
    """

    translatable_fields = ("name",)

    name = models.CharField(
        "nome interno",
        max_length=60,
        unique=True,
        help_text="Como a cor aparece no Admin. O nome que o cliente lê vem das traduções.",
    )
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

    @property
    def display_name(self) -> str:
        """O nome que o cliente lê, no idioma de conteúdo atual.

        Sem tradução no idioma pedido, cai no português e depois em qualquer
        uma que exista — o mesmo fallback de produto e categoria. Sem tradução
        nenhuma, devolve o nome interno: melhor "Preto" em francês do que um
        espaço em branco no seletor.
        """
        return self.tr("name", default=self.name)

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


class ColorMode(models.TextChoices):
    """Como a cor funciona num produto — é o produto quem diz.

    Cor pode ser três coisas diferentes, e a arquitetura até a etapa 2A só
    sabia uma delas:

    * **descrição visual** — a peça é preta, ou preta e branca, ou de seis
      cores. Vem de ``ProductColor`` (uma lista ordenada) e não cria variante;
    * **à escolha do cliente** — a peça é produzida na cor que ele pedir.
      Também não cria variante; nesta etapa é só informação;
    * **opção comercial** — o cliente escolhe entre preto e branco e cada um
      tem estoque e SKU próprios. Aí a cor continua sendo o eixo
      ``ProductVariant.color``, como sempre foi.

    Os valores são chaves estáveis, nunca texto traduzido.
    """

    NONE = "none", "Não se aplica"
    SINGLE = "single", "Uma cor"
    MULTI = "multi", "Multicolorido"
    CUSTOM = "custom", "Cores à escolha do cliente"
    VARIANT = "variant", "Opção comercial (a cor é escolhida na variante)"


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

    def sellable(self):
        """Ativo **e** com pelo menos uma variante ativa.

        É o filtro da loja. Produto sem variante existe no admin enquanto está
        sendo cadastrado, mas não é um produto comprável — e o cliente não
        deve tropeçar nele.
        """
        return self.active().filter(variants__is_active=True).distinct()

    def featured(self):
        return self.sellable().filter(is_featured=True).order_by("featured_order", "-created_at")

    def with_translations(self):
        return self.prefetch_related("translations")

    def for_listing(self):
        """Listagens (Admin incluso), sem uma consulta por linha.

        ``category__translations`` entra porque a coluna de categoria imprime o
        nome traduzido: sem ele, cada produto da lista custa uma consulta a
        mais. ``variants`` porque preço, estoque e a contagem de variantes vêm
        delas.
        """
        return self.select_related("category", "brand").prefetch_related(
            "translations", "media", "category__translations", "variants"
        )

    def with_admin_annotations(self):
        """As anotações da lista do Admin, sem o `prefetch` da listagem.

        Separada de `for_admin_list` de propósito: as contagens de cada opção
        de filtro precisam destas colunas, mas não precisam carregar
        traduções, mídia nem variantes — e carregá-las para depois só contar
        seria trabalho jogado fora.

        Preço, estoque e contagem de variantes são propriedades Python
        derivadas de ``active_variants()`` — e propriedade Python não filtra
        nem ordena no banco. Aqui cada uma ganha uma anotação equivalente.

        Por **subquery**, e não ``Sum``/``Count`` sobre o join: a busca do
        Admin junta ``translations``, e um agregado sobre join é multiplicado
        pelo número de linhas juntadas — o produto com quatro idiomas
        mostraria quatro vezes o estoque. A subquery agrupa dentro de si, e é
        indiferente ao que acontece fora.

        ``Coalesce`` porque a subquery não devolve linha nenhuma para o
        produto sem variante: soma e contagem precisam valer zero, não nulo,
        para "estoque zerado" e "sem variantes" filtrarem esse produto.
        """
        ativas = ProductVariant.objects.filter(product=models.OuterRef("pk"), is_active=True)
        todas = ProductVariant.objects.filter(product=models.OuterRef("pk"))

        def agregado(consulta, expressao, tipo):
            return models.Subquery(
                consulta.order_by().values("product").annotate(valor=expressao).values("valor")[:1],
                output_field=tipo,
            )

        dinheiro = models.DecimalField(max_digits=10, decimal_places=2)
        inteiro = models.IntegerField()
        return self.annotate(
            # O nome em português, para a coluna "produto" poder ser ordenada:
            # ele mora em `ProductTranslation`, e `display_name` é Python. Sem
            # tradução cai no SKU — que é exatamente o que a coluna imprime.
            _nome_pt=Coalesce(
                models.Subquery(
                    ProductTranslation.objects.filter(
                        master=models.OuterRef("pk"), language=DEFAULT_LANGUAGE.value
                    ).values("name")[:1],
                    output_field=models.CharField(),
                ),
                models.F("sku"),
            ),
            _preco_min=agregado(ativas, models.Min("sale_price"), dinheiro),
            _preco_max=agregado(ativas, models.Max("sale_price"), dinheiro),
            _estoque=Coalesce(agregado(ativas, models.Sum("stock_quantity"), inteiro), 0),
            _variantes_ativas=Coalesce(agregado(ativas, models.Count("pk"), inteiro), 0),
            _variantes_total=Coalesce(agregado(todas, models.Count("pk"), inteiro), 0),
            # Chave de ordenação por preço. Separada de `_preco_min` porque
            # aquele **precisa** ser nulo (produto sem preço fica fora de
            # qualquer faixa) e este não pode ser: SQLite ordena nulo primeiro
            # e o PostgreSQL ordena por último, e a lista mudaria de ordem
            # entre o desenvolvimento e a produção. Com -1 os sem preço ficam
            # sempre no mesmo lugar: antes do mais barato.
            _preco_ordem=Coalesce(
                agregado(ativas, models.Min("sale_price"), dinheiro), Decimal("-1")
            ),
            _sob_encomenda=models.Exists(ativas.filter(made_to_order=True)),
            _disponivel=models.Exists(
                ativas.filter(
                    models.Q(made_to_order=True)
                    | models.Q(allow_backorder=True)
                    | models.Q(stock_quantity__gt=0)
                )
            ),
        )

    def for_admin_list(self):
        """A lista do Admin: as relações da listagem mais as anotações."""
        return self.for_listing().with_admin_annotations()


class Product(TranslatableMixin, AuditableModel):
    """A definição **genérica** do produto: o que ele é.

    O que se vende não é o produto — é a variante. "Vaso Facetado" é um
    conceito; "Vaso Facetado, preto, 25 cm, PLA, €27,90, 300 g, pronto em 2
    dias" é a coisa que entra no carrinho, é pesada no frete e é produzida.

    Por isso este model guarda só o que vale para **todas** as variantes:
    identificação, categoria, marca, moeda, textos traduzidos, mídia,
    personalização e destaque. Preço, custo, estoque, peso, dimensões, prazo de
    produção, cor e material vivem em ``ProductVariant``.

    **Todo produto vendável tem pelo menos uma variante.** Um produto sem
    variantes pode existir enquanto o administrador o está cadastrando, mas não
    aparece na loja, não entra no carrinho e não fecha pedido — ver
    ``ProductQuerySet.sellable`` e ``is_sellable``.
    """

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

    # -- moeda -------------------------------------------------------------
    #
    # Fica no produto: é a moeda em que ele é anunciado, igual para todas as
    # variantes. Preço, custo, peso, estoque e prazo são da VARIANTE — ver o
    # docstring de ``ProductVariant``.
    currency = models.CharField(
        "moeda", max_length=3, choices=Currency.choices, default=DEFAULT_CURRENCY
    )

    # -- cores -------------------------------------------------------------
    #
    # ``none`` é o padrão seguro: um produto novo não diz nada sobre cor até
    # alguém dizer. Os produtos que já existiam recebem o modo derivado das
    # variantes na migration de dados (``0012``).
    color_mode = models.CharField(
        "modo de cores",
        max_length=8,
        choices=ColorMode.choices,
        default=ColorMode.NONE,
        help_text=(
            "Como a cor funciona neste produto. «Uma cor» e «Multicolorido» "
            "descrevem a peça (lista abaixo, não cria variantes); «Cores à "
            "escolha» é informativo; «Opção comercial» mantém a cor como eixo "
            "de cada variante."
        ),
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

    # -- moeda -------------------------------------------------------------

    @property
    def currency_symbol(self) -> str:
        return CURRENCY_SYMBOLS.get(self.currency, self.currency)

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
    #
    # Tudo o que é comercial passa por aqui. Nenhuma destas propriedades lê
    # preço, estoque, peso ou prazo do próprio produto: eles não existem mais
    # nesta tabela.

    def active_variants(self) -> list:
        """Variantes ativas, lidas do prefetch quando houver.

        Percorre ``self.variants.all()`` em memória de propósito: com
        ``prefetch_related("variants")`` a listagem inteira não faz uma
        consulta a mais por produto.
        """
        return [variant for variant in self.variants.all() if variant.is_active]

    @property
    def has_variants(self) -> bool:
        return bool(self.active_variants())

    @property
    def has_multiple_variants(self) -> bool:
        """Há mais de uma opção para o cliente escolher?

        Um produto de opção única entra no carrinho direto do card; com duas ou
        mais, quem escolhe é o cliente, na página do produto.
        """
        return len(self.active_variants()) > 1

    @property
    def is_sellable(self) -> bool:
        """O produto pode ser comprado?

        Ativo **e** com pelo menos uma variante ativa. Sem variante não há o
        que vender — não há preço, peso nem estoque em lugar nenhum.
        """
        return self.status == ProductStatus.ACTIVE and self.has_variants

    @property
    def default_variant(self):
        """A variante que a loja mostra primeiro.

        A primeira **disponível**; se todas estiverem esgotadas, a primeira
        ativa, para a página ainda ter preço e ficha técnica ao anunciar que o
        produto está esgotado. ``None`` só quando não há variante nenhuma.
        """
        variants = self.active_variants()
        for variant in variants:
            if variant.is_available:
                return variant
        return variants[0] if variants else None

    @property
    def price_range(self):
        """(menor, maior) preço entre as variantes ativas."""
        prices = [
            variant.sale_price for variant in self.active_variants()
            if variant.sale_price is not None
        ]
        if not prices:
            return (None, None)
        return (min(prices), max(prices))

    @property
    def display_price(self):
        """O preço que o card mostra: o da variante padrão."""
        variant = self.default_variant
        return variant.sale_price if variant is not None else None

    @property
    def has_price_range(self) -> bool:
        low, high = self.price_range
        return low is not None and high is not None and low != high

    @property
    def available_colors(self) -> list:
        """Cores oferecidas — derivadas das variantes, nunca cadastradas duas vezes."""
        cores = []
        for variant in self.active_variants():
            if variant.color_id and variant.color not in cores:
                cores.append(variant.color)
        return cores

    @property
    def available_materials(self) -> list:
        materiais = []
        for variant in self.active_variants():
            if variant.material_id and variant.material not in materiais:
                materiais.append(variant.material)
        return materiais

    # -- cores e composição ------------------------------------------------
    #
    # Lidas do prefetch (``product_color_prefetches`` no card,
    # ``product_description_prefetches`` na página e no carrinho):
    # ``product_colors.all()`` e ``material_composition.all()`` são percorridos
    # em memória, como as variantes — a listagem inteira não custa uma
    # consulta por produto.

    @property
    def configured_colors(self) -> list:
        """As cores cadastradas em ``ProductColor``, na ordem."""
        linhas = sorted(self.product_colors.all(), key=lambda pc: (pc.sort_order, pc.pk or 0))
        return [linha.color for linha in linhas]

    @property
    def display_colors(self) -> list:
        """As cores que o card mostra, conforme o modo.

        Descrição visual (uma cor, multicolorido): a lista cadastrada. Opção
        comercial: as cores das variantes ativas, como antes da etapa 2B.
        Não se aplica e à escolha: nenhuma bolinha.
        """
        if self.color_mode in (ColorMode.SINGLE, ColorMode.MULTI):
            return self.configured_colors
        if self.color_mode == ColorMode.VARIANT:
            return self.available_colors
        return []

    @property
    def has_custom_colors(self) -> bool:
        return self.color_mode == ColorMode.CUSTOM

    @property
    def colors_text(self) -> str:
        """«Preto + Branco + Dourado», ou «Cores à escolha», ou vazio.

        No modo «opção comercial» é vazio de propósito: a cor que importa é a
        da variante escolhida, e ela já tem o seu lugar (``variant.color``,
        ``OrderItem.color_name``).
        """
        if self.color_mode in (ColorMode.SINGLE, ColorMode.MULTI):
            return " + ".join(color.display_name for color in self.configured_colors)
        if self.color_mode == ColorMode.CUSTOM:
            return _("Cores à escolha")
        return ""

    @property
    def composition(self) -> list:
        """As linhas de ``ProductMaterialComposition``, na ordem."""
        return sorted(self.material_composition.all(), key=lambda c: (c.sort_order, c.pk or 0))

    @property
    def related_materials(self) -> list:
        """Os materiais que este produto usa, sem repetir, na ordem de leitura.

        Duas origens, porque o material entra no produto por dois caminhos: a
        **composição** (do que a peça é feita: «PLA 80% + PETG 20%») e a
        **variante** (o material comercial daquela unidade vendável). Quem
        cadastrou só um dos dois não deve ficar sem a descrição por causa da
        escolha de cadastro.

        Percorre o que já veio no `prefetch_related` da página: nenhuma
        consulta a mais, seja com um material ou com cinco. O produto continua
        apenas **apontando** para o material — a descrição mora lá, e alterá-la
        muda todos os produtos de uma vez.
        """
        encontrados = {}
        for linha in self.composition:
            encontrados.setdefault(linha.material_id, linha.material)
        for variant in self.active_variants():
            if variant.material_id and variant.material_id not in encontrados:
                encontrados[variant.material_id] = variant.material
        return list(encontrados.values())

    @property
    def material_notes(self) -> list:
        """Os materiais que têm o que dizer no idioma atual — os outros ficam de fora.

        É o que a página desenha: material sem descrição no idioma pedido nem
        em português não vira seção vazia.
        """
        return [material for material in self.related_materials if material.display_description]

    @property
    def materials_text(self) -> str:
        """«PLA 80% + PETG 20%», «PLA», ou vazio quando não há composição."""
        return " + ".join(linha.display for linha in self.composition)

    # -- estoque -----------------------------------------------------------

    @property
    def available_stock(self) -> int:
        """Estoque somado das variantes ativas."""
        return sum(variant.stock_quantity for variant in self.active_variants())

    @property
    def is_available(self) -> bool:
        """Basta uma variante disponível. Sem variante, nada é vendável."""
        return any(variant.is_available for variant in self.active_variants())

    @property
    def made_to_order(self) -> bool:
        """O produto é anunciado como sob encomenda?

        Derivado: verdadeiro quando a variante em exibição é sob encomenda.
        Não é campo — é a variante que decide, e a variante pode diferir.
        """
        variant = self.default_variant
        return bool(variant and variant.made_to_order)

    @property
    def production_lead_time_days(self):
        """Prazo anunciado na vitrine: o da variante em exibição."""
        variant = self.default_variant
        return variant.production_lead_time_days if variant else None

    @property
    def stock_state(self) -> str:
        """Rótulo do estado de estoque: made_to_order/out/low/in."""
        variant = self.default_variant
        if variant is None:
            return "out"
        if variant.made_to_order:
            return "made_to_order"
        if not self.is_available:
            return "out"
        if variant.allow_backorder:
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

        errors.update(self._validate_activation())

        if errors:
            raise ValidationError(errors)

    def _validate_activation(self):
        """Informações mínimas exigidas de um produto ativo.

        Preço, estoque e prazo saíram daqui: quem os valida é a variante. O que
        resta é o que o produto precisa ter por si — categoria e nome.

        A exigência de "pelo menos uma variante" **não** entra neste método: no
        cadastro pelo admin o produto é gravado antes do formset de variantes,
        e a checagem daria erro num formulário correto. Quem a faz é o formset
        (``ProductVariantInlineFormSet``) e, do lado da loja,
        ``ProductQuerySet.sellable`` — que é o filtro por onde a vitrine, o
        carrinho e o checkout passam.
        """
        if self.status != ProductStatus.ACTIVE:
            return {}

        errors = {}

        if self.category_id is None:
            errors["category"] = "Um produto ativo precisa de categoria."

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
        super().save(*args, **kwargs)


#: Separa as opções em ``ProductVariant.options_text`` e no snapshot do pedido.
OPTIONS_TEXT_SEPARATOR = " · "


class ProductVariant(TimeStampedModel):
    """A unidade **vendável**: o que entra no carrinho e sai na caixa.

    Enquanto ``Product`` responde "o que é isto", a variante responde "qual
    exatamente estou comprando" — e carrega **tudo** que a venda precisa:

        Caneca personalizada                       (Product: nome, categoria)
        ├── CANECA-PRETA-11  Preto  · 11 oz · PLA  €19,90 · 10 un · 180 g · 2 d
        ├── CANECA-BRANCA-11 Branco · 11 oz · PLA  €19,90 ·  5 un · 185 g · 2 d
        └── CANECA-PRETA-15  Preto  · 15 oz · PLA  €24,90 ·  3 un · 260 g · 4 d

    Não existe mais herança de valores do produto: **preço, custo, estoque,
    peso, dimensões, prazo de produção e sob-encomenda são da variante e só
    dela**. Um campo vazio aqui é um campo vazio, não um "pergunte ao produto"
    — que era exatamente o que produzia carrinho com preço de um lugar e frete
    de outro.

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

    # -- eixos -------------------------------------------------------------
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

    # -- preço -------------------------------------------------------------
    pricing_mode = models.CharField(
        "definir preço por",
        max_length=8,
        choices=PricingMode.choices,
        default=PricingMode.PRICE,
    )
    sale_price = models.DecimalField(
        "preço de venda",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="O preço desta variante. É ele que o cliente paga.",
    )
    profit_margin = models.DecimalField(
        "margem de lucro (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Margem sobre o preço de venda: (preço - custo) / preço × 100.",
    )

    # -- custos ------------------------------------------------------------
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

    # -- estoque -----------------------------------------------------------
    stock_quantity = models.PositiveIntegerField("quantidade em estoque", default=0)
    allow_backorder = models.BooleanField(
        "permitir venda sem estoque",
        default=False,
        help_text="Permite vender mesmo com estoque zerado.",
    )
    made_to_order = models.BooleanField("produzida sob encomenda", default=False)
    production_lead_time_days = models.PositiveIntegerField(
        "prazo de produção (dias)",
        null=True,
        blank=True,
        help_text="Obrigatório sob encomenda. Entra no prazo mostrado ao cliente.",
    )

    # -- físico ------------------------------------------------------------
    weight_grams = models.DecimalField(
        "peso (g)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Sempre em gramas. É este peso que calcula o frete.",
    )
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
    print_time = models.DurationField(
        "tempo de impressão",
        null=True,
        blank=True,
        help_text="Formato HH:MM:SS. Ex.: 02:35:00 para 2 horas e 35 minutos.",
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
            models.CheckConstraint(
                condition=models.Q(filament_cost__gte=0),
                name="variant_filament_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(energy_cost__gte=0),
                name="variant_energy_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_cost__gte=0),
                name="variant_total_cost_not_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(profit_margin__isnull=True)
                    | models.Q(
                        profit_margin__gte=pricing.MIN_STORED_MARGIN,
                        profit_margin__lte=pricing.MAX_STORED_MARGIN,
                    )
                ),
                name="variant_profit_margin_within_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(width__isnull=True) | models.Q(width__gte=0),
                name="variant_width_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(height__isnull=True) | models.Q(height__gte=0),
                name="variant_height_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(depth__isnull=True) | models.Q(depth__gte=0),
                name="variant_depth_not_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(print_time__isnull=True) | models.Q(print_time__gte=timedelta(0))
                ),
                name="variant_print_time_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sku} — {self.label or self.product.display_name}"

    # -- apresentação ------------------------------------------------------

    @property
    def label(self) -> str:
        """Como a variante aparece para o cliente: "Preto · 15 cm".

        A cor sai traduzida (``display_name``); tamanho e material são texto do
        catálogo. Vazio é legítimo: produto de opção única não precisa de
        rótulo, e a página mostra só o nome do produto.
        """
        parts = [
            self.color.display_name if self.color_id else "",
            self.size,
            self.material.display_name if self.material_id else "",
            # Etapa 3B: as opções adicionais, na ordem das opções do produto.
            *self.option_labels,
        ]
        return " · ".join(part for part in parts if part)

    # -- opções adicionais (etapa 3B) ----------------------------------------
    #
    # Lidas de ``option_values.all()`` — com ``variant_option_prefetches()``
    # nas listagens, percorrer em memória não custa uma consulta por variante.

    #: Escolhas ainda não gravadas, para ``clean()`` comparar combinações antes
    #: de a variante existir: ``{option_id: value_id}``. ``None`` = ler do banco.
    _pending_option_choices = None

    @property
    def option_links(self) -> list:
        """As escolhas gravadas, na ordem das opções do produto."""
        if self.pk is None:
            return []
        return sorted(
            self.option_values.all(), key=lambda link: (link.option.sort_order, link.option_id)
        )

    @property
    def option_labels(self) -> list[str]:
        """«Parede», «Fosco» — os valores, traduzidos, na ordem das opções."""
        return [link.value.display_name for link in self.option_links]

    @property
    def options_text(self) -> str:
        """«Instalação: Parede · Acabamento: Fosco», no idioma atual; vazio sem opções.

        É este texto que o pedido congela em ``OrderItem.options_snapshot``
        (etapas 3B/3E) — separado por ``OPTIONS_TEXT_SEPARATOR``, para que o
        pedido consiga desmontá-lo em linhas sem voltar ao produto.
        """
        return OPTIONS_TEXT_SEPARATOR.join(
            f"{link.option.display_name}: {link.value.display_name}" for link in self.option_links
        )

    def option_signature(self, choices: dict | None = None) -> frozenset:
        """O conjunto ``{(option_id, value_id)}`` que identifica as escolhas.

        ``choices`` (``{option_id: value_id}``) substitui o que está no banco:
        é como o formulário pergunta «esta combinação já existe?» antes de
        gravar. Sem escolhas, o conjunto é vazio — o caso de toda variante de
        antes desta etapa.
        """
        if choices is None:
            choices = self._pending_option_choices
        if choices is not None:
            return frozenset(
                (int(option_id), int(value_id)) for option_id, value_id in choices.items() if value_id
            )
        if self.pk is None:
            return frozenset()
        # Só os ids: não precisa da opção carregada, ao contrário de `option_links`.
        return frozenset((link.option_id, link.value_id) for link in self.option_values.all())

    def set_option_values(self, choices: dict) -> None:
        """Grava as escolhas da variante: ``{opção: valor ou None}``.

        Opção e valor podem ser instâncias ou ids. Recusa opção de outro
        produto, valor de outra opção e a combinação repetida com outra
        variante do produto; ``None`` apaga a escolha daquela opção. A
        variante precisa existir (ter pk). Tudo numa transação.
        """
        if self.pk is None:
            raise ValidationError("Grave a variante antes de escolher as opções.")

        def pk_de(obj):
            return getattr(obj, "pk", obj)

        normalizadas = {int(pk_de(opcao)): (int(pk_de(valor)) if valor else None) for opcao, valor in choices.items()}
        opcoes = {o.pk: o for o in ProductOption.objects.filter(pk__in=normalizadas)}
        erros = {}
        for option_id in normalizadas:
            opcao = opcoes.get(option_id)
            if opcao is None or opcao.product_id != self.product_id:
                erros[str(option_id)] = "A opção não pertence ao produto desta variante."
        valores = {
            v.pk: v
            for v in ProductOptionValue.objects.filter(pk__in=[v for v in normalizadas.values() if v])
        }
        for option_id, value_id in normalizadas.items():
            if value_id is None or str(option_id) in erros:
                continue
            valor = valores.get(value_id)
            if valor is None or valor.option_id != option_id:
                erros[str(option_id)] = "O valor não pertence a esta opção."
        if erros:
            raise ValidationError(erros)

        # A combinação inteira (eixos fixos + escolhas) não pode repetir outra.
        atuais = {link.option_id: link.value_id for link in self.option_links}
        atuais.update(normalizadas)
        self._check_duplicate_combination(self.option_signature(atuais))

        with transaction.atomic():
            for option_id, value_id in normalizadas.items():
                if value_id is None:
                    ProductVariantOptionValue.objects.filter(variant=self, option_id=option_id).delete()
                    continue
                link, criado = ProductVariantOptionValue.objects.get_or_create(
                    variant=self, option_id=option_id, defaults={"value_id": value_id}
                )
                if not criado and link.value_id != value_id:
                    link.value_id = value_id
                    link.save(update_fields=["value", "updated_at"])
        # O cache do prefetch, se houver, ficou velho.
        self._pending_option_choices = None
        if hasattr(self, "_prefetched_objects_cache"):
            self._prefetched_objects_cache.pop("option_values", None)

    def _check_duplicate_combination(self, signature: frozenset) -> None:
        """Recusa outra variante do produto com os mesmos eixos E as mesmas escolhas."""
        if not self.product_id:
            return
        candidatas = (
            ProductVariant.objects.filter(
                product_id=self.product_id,
                color_id=self.color_id,
                material_id=self.material_id,
                size=self.size,
            )
            .exclude(pk=self.pk)
            .prefetch_related("option_values")
        )
        for outra in candidatas:
            if outra.option_signature() == signature:
                campo = "size" if (self.size or not self.color_id) else "color"
                raise ValidationError({campo: "Já existe uma variante com esta combinação neste produto."})

    @property
    def display_label(self) -> str:
        """Rótulo que nunca vem vazio — para seletores e listas."""
        return self.label or self.product.display_name

    @property
    def currency_symbol(self) -> str:
        return self.product.currency_symbol

    # -- valores comerciais ------------------------------------------------
    #
    # Não há apelido para preço, peso ou dimensões: o campo da variante **é** o
    # valor. Até a etapa 7 existiam `effective_price`/`effective_weight`, que
    # caíam no produto quando a variante não tinha o dado — com a herança
    # extinta eles viraram sinônimos exatos dos campos, e dois nomes para a
    # mesma coisa fazem supor que existe diferença entre eles.

    @property
    def dimensions(self):
        """(largura, altura, profundidade) desta variante."""
        return (self.width, self.height, self.depth)

    @property
    def weight_kg(self):
        if self.weight_grams is None:
            return None
        return (self.weight_grams / Decimal("1000")).quantize(Decimal("0.001"))

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

    # -- custos e margem ---------------------------------------------------

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

    # -- disponibilidade ---------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Sob encomenda e venda sem estoque não dependem do saldo."""
        if not self.is_active:
            return False
        if self.made_to_order or self.allow_backorder:
            return True
        return self.stock_quantity > 0

    @property
    def display_media(self):
        """A foto desta variante, se alguém vinculou uma.

        ``None`` quer dizer "use as fotos gerais do produto" — não é falta de
        dado, é o caso comum. Percorre em memória para a página do produto não
        pagar uma consulta por variante (ver o `prefetch_related` da view).
        """
        for item in self.media.all():
            if item.media_type in {MediaType.IMAGE, MediaType.GIF}:
                return item
        return None

    @property
    def stock_state(self) -> str:
        """made_to_order / out / low / in — para esta variante."""
        if self.made_to_order:
            return "made_to_order"
        if not self.is_available:
            return "out"
        if self.allow_backorder:
            return "in"
        if 0 < self.stock_quantity <= LOW_STOCK_THRESHOLD:
            return "low"
        return "in"

    # -- validação ---------------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}

        if self.sku:
            self.sku = self.sku.strip().upper()
        else:
            errors["sku"] = "Informe o SKU da variante."

        for field in ("sale_price", "weight_grams", "width", "height", "depth",
                      "filament_cost", "energy_cost"):
            value = getattr(self, field)
            if value is not None and value < 0:
                errors[field] = "O valor não pode ser negativo."

        # A margem só é entrada de dados no modo MARGIN; no modo PRICE ela é
        # derivada do preço e pode legitimamente ser 100% (sem custo) ou
        # negativa (venda abaixo do custo).
        if self.pricing_mode == PricingMode.MARGIN:
            if self.profit_margin is None:
                errors["profit_margin"] = (
                    "Informe a margem desejada ou volte o modo para preço de venda."
                )
            elif not (pricing.MIN_MARGIN <= self.profit_margin <= pricing.MAX_MARGIN):
                errors["profit_margin"] = (
                    f"A margem deve estar entre {pricing.MIN_MARGIN}% e {pricing.MAX_MARGIN}%."
                )

        if self.print_time is not None and self.print_time < timedelta(0):
            errors["print_time"] = "O tempo de impressão não pode ser negativo."

        if self.made_to_order and self.production_lead_time_days is None:
            errors["production_lead_time_days"] = (
                "Informe o prazo de produção para variantes sob encomenda."
            )

        if self.is_active:
            price = self.sale_price
            # No modo MARGIN o preço ainda não foi calculado (isso acontece no
            # save), então derive-o aqui — mas só com uma margem que já passou
            # pela validação acima, senão o cálculo estoura em vez de reportar.
            if (
                price is None
                and self.pricing_mode == PricingMode.MARGIN
                and self.profit_margin is not None
                and "profit_margin" not in errors
            ):
                price = pricing.price_from_margin(
                    pricing.total_cost(self.cost_components()), self.profit_margin
                )
            if price is None or price <= 0:
                errors.setdefault(
                    "sale_price", "Uma variante ativa precisa de preço maior que zero."
                )

        if self.pk and self.product_id and not errors:
            # Auditoria 3G: uma variante com escolhas nas opções adicionais
            # não muda de produto — os vínculos apontariam para opções de
            # OUTRO produto, exatamente o que `set_option_values` recusa.
            # Quem precisa mover a variante tira as escolhas antes.
            gravado = ProductVariant.objects.filter(pk=self.pk).values_list("product_id", flat=True).first()
            if gravado is not None and gravado != self.product_id and self.option_values.exists():
                errors["product"] = (
                    "Esta variante tem escolhas nas opções adicionais do produto atual. "
                    "Remova as escolhas antes de movê-la para outro produto."
                )

        if self.product_id and not errors:
            # Dois eixos idênticos no mesmo produto seriam duas linhas que o
            # cliente não consegue distinguir. Produto de opção única (todos os
            # eixos vazios) é legítimo — mas só pode haver um. Desde a etapa 3B
            # a comparação inclui as opções adicionais: «Branco + Parede» e
            # «Branco + Mesa» são duas variantes diferentes.
            try:
                self._check_duplicate_combination(self.option_signature())
            except ValidationError as erro:
                errors.update(erro.message_dict)

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.sku = (self.sku or "").strip().upper()
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


class MaterialTranslation(TranslationBase):
    """O que o cliente lê sobre o material, em um idioma: nome e descrição.

    Slug, nome interno e estado não mudam com o idioma e ficam em `Material`.
    Acrescentar um idioma continua sendo inserir uma linha — nenhum campo
    `descricao_fr` existe, nem existirá.
    """

    master = models.ForeignKey(
        Material,
        verbose_name="material",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField("nome", max_length=80)
    #: Etapa 4C.3: a descrição que o cliente lê na página do produto.
    #:
    #: Um campo só, e HTML: o administrador estrutura ali dentro o que aquele
    #: material precisa dizer — limpeza, calor, cuidados — com subtítulos,
    #: listas e destaque. Campos fixos por assunto envelheceriam mal (o
    #: próximo material pede um assunto que os outros não têm) e não seriam
    #: traduzíveis sem multiplicar colunas.
    #:
    #: O conteúdo é limpo por `apps.core.richtext.sanitize_rich_text` ao
    #: gravar e de novo ao exibir; nada aqui é renderizado sem passar por lá.
    description = models.TextField("descrição", blank=True)

    class Meta:
        verbose_name = "tradução do material"
        verbose_name_plural = "traduções do material"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"],
                name="material_translation_unique_language",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="material_translation_name_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_language_display()}: {self.name}"


class ColorTranslation(TranslationBase):
    """O nome da cor em um idioma.

    Só o nome: HEX, slug e estado não mudam com o idioma.
    """

    master = models.ForeignKey(
        Color,
        verbose_name="cor",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField("nome", max_length=60)

    class Meta:
        verbose_name = "tradução da cor"
        verbose_name_plural = "traduções da cor"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"],
                name="color_translation_unique_language",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="color_translation_name_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_language_display()}: {self.name}"


class ProductColor(TimeStampedModel):
    """Uma cor da descrição visual do produto — na ordem em que aparece.

    É a lista que «Uma cor» e «Multicolorido» usam. Aponta para a ``Color`` de
    sempre (com HEX e traduções); não cria variante, não tem preço nem
    estoque. Quantas cores forem: «Preto + Branco», ou seis.
    """

    product = models.ForeignKey(
        Product, verbose_name="produto", related_name="product_colors", on_delete=models.CASCADE
    )
    color = models.ForeignKey(
        Color, verbose_name="cor", related_name="product_uses", on_delete=models.PROTECT
    )
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "cor do produto"
        verbose_name_plural = "cores do produto"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["product", "color"], name="product_color_unique"),
        ]
        indexes = [
            models.Index(fields=["product", "sort_order"], name="product_color_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} · {self.color.name}"


class ProductMaterialComposition(TimeStampedModel):
    """Um material da fabricação do produto, com percentual opcional.

    «PLA 80% + PETG 20%», ou só «PLA + TPU + PETG». É a composição física da
    peça — o que ela é feita —, não uma opção que o cliente escolhe: para isso
    existe ``ProductVariant.material``. Não exige que os percentuais somem 100
    (suportes, tinta e acabamento também pesam).
    """

    product = models.ForeignKey(
        Product,
        verbose_name="produto",
        related_name="material_composition",
        on_delete=models.CASCADE,
    )
    material = models.ForeignKey(
        Material, verbose_name="material", related_name="compositions", on_delete=models.PROTECT
    )
    percentage = models.DecimalField(
        "percentual",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Opcional, de 0 a 100. Em branco, só o material é mostrado.",
    )
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "material da composição"
        verbose_name_plural = "composição de materiais"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "material"], name="product_material_composition_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(percentage__isnull=True)
                | (models.Q(percentage__gte=0) & models.Q(percentage__lte=100)),
                name="product_material_percentage_range",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "sort_order"], name="product_material_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} · {self.display}"

    @property
    def display(self) -> str:
        """«PLA 80%» ou «PLA» — no idioma do cliente."""
        nome = self.material.display_name
        if self.percentage is None:
            return nome
        return f"{nome} {self.percentage.normalize():f}%"


def product_color_prefetches():
    """A paleta do produto, para o card: duas consultas por listagem.

    ``select_related("color")`` traz a cor na mesma consulta das linhas da
    paleta; só as traduções da cor custam a segunda. Vazia (o produto não tem
    paleta), custa uma.
    """
    return (
        models.Prefetch(
            "product_colors",
            queryset=ProductColor.objects.select_related("color")
            .prefetch_related("color__translations")
            .order_by("sort_order", "id"),
        ),
    )


def product_description_prefetches():
    """Paleta e composição: para a página do produto e o carrinho."""
    return (
        *product_color_prefetches(),
        models.Prefetch(
            "material_composition",
            queryset=ProductMaterialComposition.objects.select_related("material")
            .prefetch_related("material__translations")
            .order_by("sort_order", "id"),
        ),
    )


# ---------------------------------------------------------------------------
# Opções adicionais (etapa 3B)
#
# Cor, tamanho e material continuam sendo os três eixos fixos da variante. O
# que uma peça precise a mais — «Instalação: Mesa / Parede», «Acabamento:
# Fosco / Brilhante» — é uma OPÇÃO do produto, com VALORES, e cada variante
# escolhe no máximo um valor por opção. Estrutura relacional, com traduções
# pelo mesmo mecanismo de ``Color``/``Material``; nada de JSON.
# ---------------------------------------------------------------------------


class ProductOption(TranslatableMixin, TimeStampedModel):
    """Um eixo adicional de um produto: «Instalação», «Acabamento», «Modelo».

    Pertence ao produto — dois produtos com «Instalação» têm duas opções, cada
    uma com os seus valores. ``name`` é o nome interno em português; o que o
    cliente lê vem de ``ProductOptionTranslation``, com o fallback de sempre.
    """

    translatable_fields = ("name",)

    product = models.ForeignKey(
        Product, verbose_name="produto", related_name="options", on_delete=models.CASCADE
    )
    name = models.CharField("nome interno", max_length=60)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "opção do produto"
        verbose_name_plural = "opções do produto"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["product", "name"], name="product_option_unique"),
            models.CheckConstraint(condition=~models.Q(name=""), name="product_option_name_not_empty"),
        ]
        indexes = [
            models.Index(fields=["product", "sort_order"], name="product_option_order_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def display_name(self) -> str:
        """No idioma atual; sem tradução, o nome interno — que já é o português.

        Sem o fallback «qualquer idioma que exista» da cor: aqui o nome
        interno é o texto em português, e um cliente em português não pode
        ler «Installation» só porque alguém traduziu para inglês antes.
        """
        return self.tr("name", fallback=False, default=self.name)

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "O nome da opção é obrigatório."})


class ProductOptionTranslation(TranslationBase):
    """O nome da opção em um idioma."""

    master = models.ForeignKey(
        ProductOption, verbose_name="opção", related_name="translations", on_delete=models.CASCADE
    )
    name = models.CharField("nome", max_length=60)

    class Meta:
        verbose_name = "tradução da opção"
        verbose_name_plural = "traduções da opção"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="product_option_translation_unique_language"
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="product_option_translation_name_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_language_display()}: {self.name}"


class ProductOptionValue(TranslatableMixin, TimeStampedModel):
    """Um valor de uma opção: «Mesa», «Parede»; «Fosco», «Brilhante»."""

    translatable_fields = ("name",)

    option = models.ForeignKey(
        ProductOption, verbose_name="opção", related_name="values", on_delete=models.CASCADE
    )
    name = models.CharField("nome interno", max_length=60)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    class Meta:
        verbose_name = "valor da opção"
        verbose_name_plural = "valores da opção"
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(fields=["option", "name"], name="product_option_value_unique"),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="product_option_value_name_not_empty"
            ),
        ]
        indexes = [
            models.Index(fields=["option", "sort_order"], name="product_option_value_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.option.name}: {self.name}"

    @property
    def display_name(self) -> str:
        """No idioma atual; sem tradução, o nome interno (ver `ProductOption`)."""
        return self.tr("name", fallback=False, default=self.name)

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "O nome do valor é obrigatório."})


class ProductOptionValueTranslation(TranslationBase):
    """O nome do valor em um idioma."""

    master = models.ForeignKey(
        ProductOptionValue, verbose_name="valor", related_name="translations", on_delete=models.CASCADE
    )
    name = models.CharField("nome", max_length=60)

    class Meta:
        verbose_name = "tradução do valor"
        verbose_name_plural = "traduções do valor"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"],
                name="product_option_value_translation_unique_language",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="product_option_value_translation_name_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_language_display()}: {self.name}"


class ProductVariantOptionValue(TimeStampedModel):
    """A escolha de uma variante numa opção: «V01 · Instalação = Parede».

    ``option`` é redundante com ``value.option`` de propósito: é ela que
    permite ao banco garantir «um valor por opção por variante» com uma
    constraint simples ``(variant, option)``. ``clean()`` confere a coerência
    dos dois e que a opção é do produto da variante — e ``save()`` chama
    ``clean()``, para a regra valer também fora de formulários.

    RESTRICT na opção e no valor: apagar o que uma variante usa é recusado,
    nunca silencioso. É RESTRICT e não PROTECT de propósito: PROTECT recusaria
    também apagar o PRODUTO inteiro (o Django recolhe a opção pelo CASCADE do
    produto e esbarra na escolha da variante); RESTRICT só recusa quando a
    escolha NÃO está sendo apagada na mesma operação — e no produto ela está,
    pelo CASCADE variante → escolha. Apagar a variante apaga só as escolhas
    dela.
    """

    variant = models.ForeignKey(
        ProductVariant, verbose_name="variante", related_name="option_values", on_delete=models.CASCADE
    )
    option = models.ForeignKey(
        ProductOption, verbose_name="opção", related_name="variant_links", on_delete=models.RESTRICT
    )
    value = models.ForeignKey(
        ProductOptionValue, verbose_name="valor", related_name="variant_links", on_delete=models.RESTRICT
    )

    class Meta:
        verbose_name = "opção da variante"
        verbose_name_plural = "opções da variante"
        ordering = ("option__sort_order", "option_id")
        constraints = [
            models.UniqueConstraint(fields=["variant", "option"], name="variant_option_unique"),
        ]
        indexes = [
            models.Index(fields=["variant", "value"], name="variant_option_value_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.variant_id} · {self.option_id} = {self.value_id}"

    def clean(self):
        super().clean()
        if self.option_id and self.value_id and self.value.option_id != self.option_id:
            raise ValidationError({"value": "O valor não pertence a esta opção."})
        if self.option_id and self.variant_id and self.option.product_id != self.variant.product_id:
            raise ValidationError({"option": "A opção não pertence ao produto desta variante."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


#: Os textos que «Copiar de…» leva de um produto para outro.
#:
#: O `name` fica **de fora**, e é a decisão que dá sentido à ação: o nome é a
#: identidade do produto («Dinossauros — Kit para Colorir»), não a descrição
#: dele. Copiar o nome renomearia o destino para o nome da origem — o oposto
#: do que quem clica quer. O que se reaproveita é o texto que se repete entre
#: produtos irmãos.
CONTENT_COPY_FIELDS = ("short_description", "description", "extra_information")


def product_content_copy_plan(origem, destino) -> dict:
    """O que aconteceria numa cópia, idioma a idioma — sem gravar nada.

    É o que a confirmação mostra antes de executar, porque a regra tem uma
    assimetria que ninguém adivinha: um idioma que existe no destino e **não**
    existe na origem fica como está. Sobrescrever com vazio seria apagar
    trabalho; ignorar em silêncio seria pior ainda.

    Devolve três listas de códigos de idioma:

    * ``replace`` — existe nos dois: o texto do destino é substituído;
    * ``create``  — só na origem: nasce uma tradução nova no destino;
    * ``keep``    — só no destino: não é tocada.
    """
    da_origem = {t.language: t for t in origem.translations.all()}
    do_destino = {t.language: t for t in destino.translations.all()}
    return {
        "replace": sorted(code for code in da_origem if code in do_destino),
        "create": sorted(code for code in da_origem if code not in do_destino),
        "keep": sorted(code for code in do_destino if code not in da_origem),
    }


def copy_product_content(origem, destino) -> dict:
    """Copia os textos de ``origem`` para ``destino``, um idioma por vez.

    Etapa 4C.2. Nasce de uma repetição real do catálogo: «Pets — Kit para
    Colorir» e «Dinossauros — Kit para Colorir» têm o mesmo texto de
    apresentação em quatro idiomas, e só o assunto muda.

    ## O que isto **não** é

    Não é duplicação de produto (essa já existe, e faz outra coisa), não é
    biblioteca de textos e não é herança: depois da cópia os dois produtos não
    se conhecem. Cada tradução é uma linha própria do destino; editar um lado
    depois não mexe no outro.

    ## Idiomas

    Nenhum código de idioma aparece aqui. O que a origem tiver, o destino
    recebe — quatro idiomas hoje, sete amanhã, sem alterar esta função.

    O nome não é copiado (ver `CONTENT_COPY_FIELDS`). Num idioma que o destino
    ainda não tinha, a tradução nasce com o nome que o destino usa em
    português: é o que a loja já mostraria ali por fallback, e fica visível
    para quem for traduzir depois.

    Tudo numa transação: ou todos os idiomas entram, ou nenhum.
    """
    if origem.pk == destino.pk:
        raise ValueError("Um produto não copia as descrições de si mesmo.")

    plano = product_content_copy_plan(origem, destino)
    nome_padrao = destino.name_in(DEFAULT_LANGUAGE.value) or destino.sku

    with transaction.atomic():
        do_destino = {t.language: t for t in destino.translations.all()}
        for traducao in origem.translations.all():
            campos = {campo: getattr(traducao, campo) for campo in CONTENT_COPY_FIELDS}
            atual = do_destino.get(traducao.language)
            if atual is None:
                ProductTranslation.objects.create(
                    master=destino,
                    language=traducao.language,
                    name=nome_padrao,
                    **campos,
                )
            else:
                for campo, valor in campos.items():
                    setattr(atual, campo, valor)
                atual.save(update_fields=list(campos))

    destino.refresh_translations()
    return plano


def copy_product_options(origem, destino) -> tuple[dict, dict]:
    """Etapa 3F: copia as opções adicionais de ``origem`` para ``destino``.

    Cria registros **novos** — ``ProductOption``, ``ProductOptionValue`` e as
    traduções dos dois — com o mesmo nome, a mesma ordem e os mesmos idiomas,
    e devolve os mapas ``{id da opção original: opção nova}`` e ``{id do valor
    original: valor novo}``: é com eles que quem duplica religa as variantes
    (``ProductVariantOptionValue``) ao produto novo, nunca ao antigo.

    Nada de ``origem`` é reaproveitado ou tocado; nenhum objeto fica em comum.
    Custo fixo: uma leitura com prefetch (quatro consultas) e quatro gravações
    em lote, sejam duas opções ou vinte. Tudo numa transação.
    """
    if destino.pk is None:
        raise ValueError("Grave o produto de destino antes de copiar as opções.")
    if origem.pk == destino.pk:
        raise ValueError("Um produto não copia as próprias opções.")

    opcoes = list(
        origem.options.prefetch_related("translations", "values__translations").order_by("sort_order", "id")
    )
    option_map: dict = {}
    value_map: dict = {}
    if not opcoes:
        return option_map, value_map

    def em_lote(model, objetos):
        """``bulk_create`` devolvendo os pks; um a um onde o banco não os devolve."""
        if not objetos:
            return []
        if connection.features.can_return_rows_from_bulk_insert:
            return model.objects.bulk_create(objetos)
        for obj in objetos:
            obj.save()
        return objetos

    with transaction.atomic():
        novas = em_lote(
            ProductOption,
            [ProductOption(product=destino, name=o.name, sort_order=o.sort_order) for o in opcoes],
        )
        option_map = {o.pk: nova for o, nova in zip(opcoes, novas)}
        em_lote(
            ProductOptionTranslation,
            [
                ProductOptionTranslation(master=option_map[o.pk], language=t.language, name=t.name)
                for o in opcoes
                for t in o.translations.all()
            ],
        )
        valores = [
            v
            for o in opcoes
            for v in sorted(o.values.all(), key=lambda valor: (valor.sort_order, valor.pk))
        ]
        novos = em_lote(
            ProductOptionValue,
            [
                ProductOptionValue(option=option_map[v.option_id], name=v.name, sort_order=v.sort_order)
                for v in valores
            ],
        )
        value_map = {v.pk: novo for v, novo in zip(valores, novos)}
        em_lote(
            ProductOptionValueTranslation,
            [
                ProductOptionValueTranslation(master=value_map[v.pk], language=t.language, name=t.name)
                for v in valores
                for t in v.translations.all()
            ],
        )
    return option_map, value_map


def variant_option_prefetches():
    """As escolhas da variante, para ``label`` e ``options_text``: 3 consultas.

    Opção e valor vêm no mesmo SELECT das escolhas; as traduções dos dois
    custam mais duas. Variante sem escolha custa uma. Usar onde quer que
    ``label`` seja lido em lista — carrinho, página do produto, admin.
    """
    return (
        models.Prefetch(
            "option_values",
            queryset=ProductVariantOptionValue.objects.select_related("option", "value")
            .prefetch_related("option__translations", "value__translations")
            .order_by("option__sort_order", "option_id"),
        ),
    )


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
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        verbose_name="variante",
        related_name="media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=(
            "Opcional. Vinculada a uma variante, esta foto passa a ser a "
            "imagem principal quando o cliente escolher essa opção. "
            "Em branco, é foto geral do produto."
        ),
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
            models.Index(fields=["variant"], name="product_media_variant_idx"),
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
        """As duas regras da mídia, num método só.

        1. O arquivo tem que combinar com o tipo declarado: um ``.mp4`` marcado
           como Imagem apareceria na galeria como foto quebrada.
        2. A variante, quando houver, tem que ser **deste** produto. Sem esta
           checagem o Admin aceitaria vincular a foto de um produto à variante
           de outro — e a vitrine mostraria a foto errada sem nada indicar o
           porquê. O widget do Admin já só oferece as variantes certas, mas ele
           não é a única porta: existe o popup "adicionar", existe o shell e
           existe um POST montado à mão.

        As duas saem juntas, num `ValidationError` por campo: quem errou nos
        dois pontos não descobre um erro por vez.
        """
        super().clean()
        errors = {}

        extension = self.extension
        allowed = MEDIA_TYPE_EXTENSIONS[MediaType(self.media_type)]
        if extension and extension not in allowed:
            errors["media_type"] = (
                f"Arquivo .{extension} não corresponde ao tipo "
                f"{self.get_media_type_display()} (aceitos: {', '.join(allowed)})."
            )

        if self.variant_id and self.product_id and self.variant.product_id != self.product_id:
            errors["variant"] = "Escolha uma variante deste mesmo produto."

        if errors:
            raise ValidationError(errors)

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
