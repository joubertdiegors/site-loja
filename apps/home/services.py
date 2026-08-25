"""Resolução do conteúdo da Home.

A view não sabe de onde vêm os produtos de cada seção — quem sabe é este
módulo. Cada tipo de seção tem um resolvedor; acrescentar "promoções" ou
"lançamentos da marca X" é acrescentar uma função aqui.

Cuidado com consultas: a Home carrega tudo com ``select_related`` /
``prefetch_related`` e resolve categorias em memória. O custo é constante em
relação ao número de produtos e cresce apenas com o número de seções ativas
(uma consulta por seção dinâmica).
"""

from dataclasses import dataclass, field

from django.db.models import Count, Prefetch

from apps.catalog.models import Product, ProductStatus
from apps.categories.models import Category
from apps.categories.tree import CategoryTree
from apps.home.models import HomeBanner, HomeSection, HomeSectionProduct, HomeSectionType


# ---------------------------------------------------------------------------
# Querysets base
# ---------------------------------------------------------------------------


def product_card_queryset():
    """Produtos prontos para virar card, sem N+1.

    ``media`` e ``colors`` vêm por prefetch porque o card usa
    ``display_media`` e as amostras de cor; ``translations`` porque o nome é
    traduzido; ``category__translations`` porque o card mostra o nome da
    categoria; ``variants`` porque a disponibilidade do card olha as variantes.
    Sem qualquer um deles, cada card dispara uma consulta.
    """
    return (
        Product.objects.filter(status=ProductStatus.ACTIVE)
        .select_related("category")
        .prefetch_related(
            "translations", "media", "colors", "category__translations", "variants"
        )
    )


# ---------------------------------------------------------------------------
# Seção resolvida
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSection:
    """Uma seção pronta para o template: configuração + produtos já carregados."""

    section: HomeSection
    products: list = field(default_factory=list)

    @property
    def is_renderable(self) -> bool:
        return bool(self.products)

    # Atalhos usados pelo template (evitam lógica no HTML).
    @property
    def title(self) -> str:
        return self.section.title

    @property
    def subtitle(self) -> str:
        return self.section.subtitle

    @property
    def layout(self) -> str:
        return self.section.layout

    @property
    def has_cta(self) -> bool:
        return self.section.has_cta

    @property
    def cta_link(self) -> str:
        return self.section.cta_link

    @property
    def cta_label(self) -> str:
        return self.section.cta_label

    @property
    def anchor(self) -> str:
        return f"secao-{self.section.pk}"


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
        if product.status == ProductStatus.ACTIVE:
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
# API pública
# ---------------------------------------------------------------------------


def active_sections_queryset():
    """Seções ativas, ordenadas, com tudo o que o template vai precisar."""
    manual_items = (
        HomeSectionProduct.objects.select_related("product", "product__category")
        .prefetch_related(
            "product__translations",
            "product__media",
            "product__colors",
            "product__category__translations",
            "product__variants",
        )
        .order_by("sort_order", "id")
    )
    return (
        HomeSection.objects.active()
        .ordered()
        .select_related("category", "cta_category", "cta_product")
        .prefetch_related(
            "translations",
            "category__translations",
            "cta_category__translations",
            "cta_product__translations",
            Prefetch("items", queryset=manual_items),
        )
    )


def get_home_sections(tree: CategoryTree | None = None) -> list[ResolvedSection]:
    """Seções ativas já resolvidas, sem as vazias.

    Uma seção ativa cujo conteúdo não existe (categoria sem produtos, nenhum
    produto em destaque, mais vendidos sem módulo de pedidos) é descartada
    aqui — a Home nunca mostra um título com zero produtos.
    """
    tree = tree or CategoryTree.load()
    resolved = []

    for section in active_sections_queryset():
        resolver = RESOLVERS.get(section.section_type)
        if resolver is None:
            continue
        products = resolver(section, tree)
        candidate = ResolvedSection(section=section, products=products)
        if candidate.is_renderable:
            resolved.append(candidate)

    return resolved


def get_active_banner() -> HomeBanner | None:
    """Primeiro banner ativo, ou ``None`` (a Home mostra o destaque tipográfico)."""
    return (
        HomeBanner.objects.filter(is_active=True)
        .select_related("cta_category", "cta_product")
        .prefetch_related("translations", "cta_category__translations", "cta_product__translations")
        .order_by("sort_order", "-created_at")
        .first()
    )


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
    return {
        "banner": get_active_banner(),
        "sections": get_home_sections(tree),
        "category_cards": get_category_cards(tree),
    }
