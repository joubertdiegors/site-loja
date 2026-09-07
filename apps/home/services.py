"""Resolução do conteúdo da Home.

A view não sabe de onde vêm os produtos de cada seção — quem sabe é este
módulo. Cada tipo de seção tem um resolvedor; acrescentar "promoções" ou
"lançamentos da marca X" é acrescentar uma função aqui.

## A composição

Desde a etapa 20 a Home é a **lista das seções ativas, na ordem** — todas
elas: as faixas de produtos e também os blocos (categorias em destaque, como
trabalhamos, chamada final, sobre a loja), que antes tinham posição fixa no
template. `get_home_sections` devolve cada seção já resolvida e pronta para o
template, e descarta as que não têm o que mostrar: um título com zero
produtos, uma seção de categorias sem blocos, uma chamada desativada.

A **faixa de fundo** de cada seção é consequência da posição entre as que
sobraram: a primeira depois do banner é branca, a segunda creme, e assim por
diante (`band`). Remover ou mover uma seção reajusta as outras sozinho — não
há cor gravada em lugar nenhum. Os blocos que têm fundo próprio no cadastro
(a chamada final, o "Sobre a loja") e a faixa lilás de "Como trabalhamos"
pintam por cima da faixa; ela continua contando na alternância.

Sem nenhuma seção cadastrada (uma instalação recém-migrada) a Home mostra a
composição padrão: o aviso de vitrine em montagem, os três cards de sempre e
a chamada final de fábrica — o primeiro cadastro a substitui.

Cuidado com consultas: a Home carrega tudo com ``select_related`` /
``prefetch_related`` e resolve categorias em memória. O custo é constante em
relação ao número de produtos e de blocos, e cresce apenas com o número de
seções de produtos ativas (uma consulta por seção dinâmica).
"""

from dataclasses import dataclass, field

from django.db.models import Count, Prefetch

from apps.catalog.models import Product, ProductStatus, ProductVariant
from apps.categories.models import Category
from apps.categories.tree import CategoryTree
from apps.home.models import (
    HomeBanner,
    HomeCard,
    HomeCategoryCard,
    HomeSection,
    HomeSectionProduct,
    HomeSectionType,
)


# ---------------------------------------------------------------------------
# Querysets base
# ---------------------------------------------------------------------------


def product_card_queryset():
    """Produtos prontos para virar card, sem N+1.

    ``media`` vem por prefetch porque o card usa ``display_media``;
    ``translations`` porque o nome é traduzido; ``category__translations``
    porque o card mostra o nome da categoria; ``variants`` — com cor e material
    — porque desde a etapa 8 é dela que saem preço, disponibilidade e as
    amostras de cor. Sem qualquer um deles, cada card dispara uma consulta.

    ``sellable()`` porque produto sem variante ativa não tem preço nem estoque:
    não é um card, é um cadastro pela metade.
    """
    return (
        Product.objects.sellable()
        .select_related("category")
        .prefetch_related(
            "translations",
            "media",
            "category__translations",
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.select_related("color", "material")
                .prefetch_related("color__translations", "material__translations")
                .order_by(
                    "sort_order", "id"
                ),
            ),
        )
    )


# ---------------------------------------------------------------------------
# Seção resolvida
# ---------------------------------------------------------------------------

#: As duas faixas de fundo que se alternam. A primeira seção depois do banner
#: é branca — é o que a separa do creme em que o banner está.
BANDS = ("white", "cream")


@dataclass
class ResolvedSection:
    """Uma seção pronta para o template: configuração + conteúdo já carregado.

    `kind` diz ao template qual componente desenhar; `band` é a faixa de fundo
    desta posição. As seções padrão da instalação vazia não têm `section`.
    """

    section: HomeSection | None
    kind: str
    products: list = field(default_factory=list)
    blocks: list = field(default_factory=list)  # HomeCategoryCard
    cards: list = field(default_factory=list)  # HomeCard
    callout: object = None  # HomeCallout
    about: object = None  # HomeAbout
    use_defaults: bool = False  # cards/chamada de fábrica
    band: str = BANDS[0]

    KIND_PRODUCTS = "products"
    KIND_CATEGORY_CARDS = "category_cards"
    KIND_HOW_WE_WORK = "how_we_work"
    KIND_CALLOUT = "callout"
    KIND_ABOUT = "about"

    @property
    def is_renderable(self) -> bool:
        if self.kind == self.KIND_PRODUCTS:
            return bool(self.products)
        if self.kind == self.KIND_CATEGORY_CARDS:
            return bool(self.blocks)
        if self.kind == self.KIND_HOW_WE_WORK:
            return bool(self.cards) or self.use_defaults
        if self.kind == self.KIND_CALLOUT:
            return self.callout is not None or self.use_defaults
        if self.kind == self.KIND_ABOUT:
            return self.about is not None
        return False

    # Atalhos usados pelo template (evitam lógica no HTML).
    @property
    def title(self) -> str:
        return self.section.title if self.section else ""

    @property
    def subtitle(self) -> str:
        return self.section.subtitle if self.section else ""

    @property
    def layout(self) -> str:
        return self.section.layout if self.section else ""

    @property
    def has_cta(self) -> bool:
        return bool(self.section and self.section.has_cta)

    @property
    def cta_link(self) -> str:
        return self.section.cta_link if self.section else ""

    @property
    def cta_label(self) -> str:
        return self.section.cta_label if self.section else ""

    @property
    def anchor(self) -> str:
        """Um id único por seção — duas do mesmo tipo não podem dividir um id."""
        if self.section is None:
            return f"secao-{self.kind}"
        return f"secao-{self.section.pk}"

    @property
    def is_white(self) -> bool:
        return self.band == "white"


# ---------------------------------------------------------------------------
# Resolvedores por tipo
# ---------------------------------------------------------------------------


def _manual_products(section: HomeSection, tree: CategoryTree) -> list[Product]:
    """Produtos escolhidos a dedo, na ordem definida pelo administrador.

    Usa o prefetch de ``items`` feito em ``get_home_sections`` — nenhuma
    consulta adicional aqui.
    """
    products = []
    for item in section.items.all():
        product = item.product
        # Escolhido a dedo, mas sem variante ativa não há o que vender: fica
        # fora da vitrine em vez de virar um card sem preço.
        if product.status == ProductStatus.ACTIVE and product.has_variants:
            products.append(product)
    return products[: section.product_limit]


def _category_products(section: HomeSection, tree: CategoryTree) -> list[Product]:
    if section.category_id is None:
        return []
    if section.include_subcategories:
        category_ids = tree.subtree_ids(section.category_id)
    else:
        category_ids = [section.category_id]
    queryset = product_card_queryset().filter(category_id__in=category_ids)
    return list(queryset.order_by("-created_at")[: section.product_limit])


def _featured_products(section: HomeSection, tree: CategoryTree) -> list[Product]:
    queryset = product_card_queryset().filter(is_featured=True)
    return list(queryset.order_by("featured_order", "-created_at")[: section.product_limit])


def _newest_products(section: HomeSection, tree: CategoryTree) -> list[Product]:
    return list(product_card_queryset().order_by("-created_at")[: section.product_limit])


def _best_sellers(section: HomeSection, tree: CategoryTree) -> list[Product]:
    """Mais vendidos — sem fonte de dados nesta etapa.

    Não existe módulo de pedidos, então não existe número de vendas. Inventar
    um critério (mais recentes, destaques, aleatório) e chamá-lo de "mais
    vendidos" seria mentir para o cliente e para o administrador.

    A seção fica registrada, aparece no admin com o aviso de que depende do
    módulo de pedidos e simplesmente não é renderizada. Quando existir
    ``orders``, este resolvedor passa a somar itens vendidos por produto e
    nada mais no sistema precisa mudar.
    """
    return []


RESOLVERS = {
    HomeSectionType.MANUAL_PRODUCTS: _manual_products,
    HomeSectionType.CATEGORY_PRODUCTS: _category_products,
    HomeSectionType.FEATURED_PRODUCTS: _featured_products,
    HomeSectionType.NEWEST_PRODUCTS: _newest_products,
    HomeSectionType.BEST_SELLERS: _best_sellers,
}


# ---------------------------------------------------------------------------
# Os blocos
# ---------------------------------------------------------------------------


def _category_cards_section(section: HomeSection) -> ResolvedSection:
    """Os blocos coloridos desta seção (prefetch de ``category_cards``)."""
    blocks = [b for b in section.category_cards.all() if b.is_active]
    return ResolvedSection(section=section, kind=ResolvedSection.KIND_CATEGORY_CARDS, blocks=blocks)


def _how_we_work_section(section: HomeSection) -> ResolvedSection:
    """Os cards desta seção — e, sem nenhum cadastrado, os três de fábrica.

    Dois estados diferentes de propósito: seção sem card nenhum mostra os
    padrões (a seção acabou de ser criada e a Home não abre com um buraco);
    seção com cards, todos desativados, não mostra nada — desativar é uma
    decisão do administrador, e ela vale.
    """
    todos = list(section.cards.all())
    ativos = [card for card in todos if card.is_active]
    return ResolvedSection(
        section=section,
        kind=ResolvedSection.KIND_HOW_WE_WORK,
        cards=ativos,
        use_defaults=not todos,
    )


def _callout_section(section: HomeSection) -> ResolvedSection:
    """A chamada escolhida — ou a de fábrica, quando a seção não escolheu.

    Escolhida e **desativada**, ou escolhida e **vazia**, a seção não desenha
    nada: uma faixa escura vazia é pior que faixa nenhuma, e desativar é uma
    decisão. Sem escolha (`callout` vazio) vale o texto padrão, para a seção
    recém-criada não abrir um buraco na página.
    """
    chamada = section.callout
    if chamada is None:
        return ResolvedSection(section=section, kind=ResolvedSection.KIND_CALLOUT, use_defaults=True)
    if not chamada.is_active or not chamada.has_content:
        return ResolvedSection(section=section, kind=ResolvedSection.KIND_CALLOUT)
    return ResolvedSection(section=section, kind=ResolvedSection.KIND_CALLOUT, callout=chamada)


def _about_section(section: HomeSection) -> ResolvedSection:
    sobre = section.about
    if sobre is None or not sobre.is_active or not sobre.has_content:
        return ResolvedSection(section=section, kind=ResolvedSection.KIND_ABOUT)
    return ResolvedSection(section=section, kind=ResolvedSection.KIND_ABOUT, about=sobre)


BLOCK_RESOLVERS = {
    HomeSectionType.CATEGORY_CARDS: _category_cards_section,
    HomeSectionType.HOW_WE_WORK: _how_we_work_section,
    HomeSectionType.CALLOUT: _callout_section,
    HomeSectionType.ABOUT: _about_section,
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def section_prefetches():
    """Os prefetches de uma seção — para a lista da Home e para uma seção só.

    Uma consulta para as seções e um número **fixo** de prefetches — os
    produtos manuais, os blocos de categoria, os cards, a chamada com os
    passos, o "Sobre a loja" com as pílulas, cada um com as suas traduções.
    Cadastrar mais seções, mais blocos ou mais pílulas não acrescenta
    consulta nenhuma; é o que `HomeQueryTests` prova.
    """
    manual_items = (
        HomeSectionProduct.objects.select_related("product", "product__category")
        .prefetch_related(
            "product__translations",
            "product__media",
            "product__category__translations",
            Prefetch(
                "product__variants",
                queryset=ProductVariant.objects.select_related("color", "material")
                .prefetch_related("color__translations", "material__translations")
                .order_by(
                    "sort_order", "id"
                ),
            ),
        )
        .order_by("sort_order", "id")
    )
    category_cards = (
        HomeCategoryCard.objects.select_related("category")
        .prefetch_related("translations", "category__translations")
        .order_by("sort_order", "id")
    )
    cards = HomeCard.objects.prefetch_related("translations").order_by("sort_order", "id")
    return (
        "translations",
        "category__translations",
        "cta_category__translations",
        "cta_product__translations",
        Prefetch("items", queryset=manual_items),
        Prefetch("category_cards", queryset=category_cards),
        Prefetch("cards", queryset=cards),
        "callout__translations",
        "callout__cta_category__translations",
        "callout__cta_product__translations",
        "callout__steps__translations",
        "about__translations",
        "about__badges__translations",
    )


def active_sections_queryset():
    """Seções ativas, ordenadas, com tudo o que o template vai precisar."""
    return (
        HomeSection.objects.active()
        .ordered()
        .select_related(
            "category", "cta_category", "cta_product",
            "callout", "callout__cta_category", "callout__cta_product",
            "about",
        )
        .prefetch_related(*section_prefetches())
    )


def _default_composition() -> list[ResolvedSection]:
    """A Home de uma instalação sem nenhuma seção cadastrada.

    Os três cards de sempre e a chamada final de fábrica — o que impede a loja
    recém-migrada de abrir pela metade. O primeiro cadastro em "Seções da
    Home" substitui tudo isto.
    """
    return [
        ResolvedSection(section=None, kind=ResolvedSection.KIND_HOW_WE_WORK, use_defaults=True),
        ResolvedSection(section=None, kind=ResolvedSection.KIND_CALLOUT, use_defaults=True),
    ]


def _with_bands(entries: list[ResolvedSection]) -> list[ResolvedSection]:
    """A faixa de fundo de cada seção, pela posição entre as que sobraram."""
    for index, entry in enumerate(entries):
        entry.band = BANDS[index % len(BANDS)]
    return entries


def get_home_sections(tree: CategoryTree | None = None) -> list[ResolvedSection]:
    """A composição da Home: as seções ativas já resolvidas, sem as vazias.

    Uma seção ativa cujo conteúdo não existe (categoria sem produtos, nenhum
    produto em destaque, seção de categorias sem blocos, chamada desativada) é
    descartada aqui — a Home nunca mostra um título com zero produtos nem uma
    faixa vazia. As faixas de fundo são numeradas **depois** do descarte: é a
    posição na página que conta, não a do cadastro.
    """
    tree = tree or CategoryTree.load()
    resolved = []

    for section in active_sections_queryset():
        candidate = resolve_section(section, tree)
        if candidate is not None and candidate.is_renderable:
            resolved.append(candidate)

    return _with_bands(resolved)


def resolve_section(section: HomeSection, tree: CategoryTree | None = None) -> ResolvedSection | None:
    """Uma seção resolvida — a mesma rotina da Home, para uma seção só.

    É o que a pré-visualização do Admin usa: a seção como está no formulário,
    resolvida exatamente como a Home a resolveria. `None` para um tipo sem
    resolvedor.
    """
    if section.section_type in BLOCK_RESOLVERS:
        return BLOCK_RESOLVERS[section.section_type](section)
    resolver = RESOLVERS.get(section.section_type)
    if resolver is None:
        return None
    tree = tree or CategoryTree.load()
    return ResolvedSection(
        section=section, kind=ResolvedSection.KIND_PRODUCTS, products=resolver(section, tree)
    )


def get_active_banners() -> list[HomeBanner]:
    """Todos os banners ativos, na ordem do cadastro — os slides do carrossel.

    Uma consulta, com o que cada desenho precisa já carregado. Só os ativos: um
    banner desligado no Admin não pode aparecer nem como segundo slide.
    """
    return list(
        HomeBanner.objects.filter(is_active=True)
        .select_related("cta_category", "cta_product")
        .prefetch_related("translations", "cta_category__translations", "cta_product__translations")
        .order_by("sort_order", "-created_at")
    )


def get_active_banner() -> HomeBanner | None:
    """Primeiro banner ativo, ou ``None`` (a Home mostra o destaque tipográfico)."""
    banners = get_active_banners()
    return banners[0] if banners else None


def get_banner_carousel():
    """Os banners ativos e a configuração do carrossel, para o topo da Home.

    `banner` continua sendo o primeiro: é o que os templates e testes do hero
    sempre leram. `banners` é a lista inteira e `banner_carousel` a
    configuração — com um banner só, o template não desenha carrossel nenhum.
    """
    from apps.home.models import HomeBannerCarousel

    banners = get_active_banners()
    return {
        "banner": banners[0] if banners else None,
        "banners": banners,
        "banner_carousel": HomeBannerCarousel.current(),
    }


@dataclass
class CategoryCard:
    category: Category
    product_count: int

    @property
    def name(self) -> str:
        return self.category.name

    @property
    def url(self) -> str:
        return self.category.get_absolute_url()


def get_category_cards(tree: CategoryTree | None = None, limit: int = 8) -> list[CategoryCard]:
    """Categorias raiz que têm produtos ativos (nelas ou nas filhas).

    Duas consultas no total: a árvore e a contagem agrupada por categoria.
    """
    tree = tree or CategoryTree.load()

    counts_by_category = {
        row["category_id"]: row["total"]
        for row in Product.objects.filter(status=ProductStatus.ACTIVE, category__isnull=False)
        .values("category_id")
        .annotate(total=Count("id"))
    }

    totals_by_root: dict[int, int] = {}
    for category_id, total in counts_by_category.items():
        root = tree.root_of(category_id)
        if root is None:
            continue
        totals_by_root[root.pk] = totals_by_root.get(root.pk, 0) + total

    cards = [
        CategoryCard(category=root, product_count=totals_by_root.get(root.pk, 0))
        for root in tree.roots()
        if totals_by_root.get(root.pk, 0) > 0
    ]
    return cards[:limit]


def get_home_context() -> dict:
    """Tudo que a Home precisa, compartilhando uma única árvore de categorias."""
    tree = CategoryTree.load()
    sections = get_home_sections(tree)
    # "Nenhuma seção cadastrada" é diferente de "nenhuma seção com conteúdo":
    # só a primeira recebe a composição padrão. Um `exists()` a mais, e só
    # quando a lista voltou vazia — na loja em uso não custa nada.
    composition_configured = bool(sections) or HomeSection.objects.exists()
    if not composition_configured:
        sections = _with_bands(_default_composition())
    has_products = any(entry.kind == ResolvedSection.KIND_PRODUCTS for entry in sections)
    contexto = {
        "sections": sections,
        "composition_configured": composition_configured,
        "has_product_sections": has_products,
        "category_cards": get_category_cards(tree),
    }
    contexto.update(get_banner_carousel())
    return contexto
