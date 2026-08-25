"""Páginas públicas do catálogo.

* ``ShopView`` — a vitrine de Modelos (``/modelos/``): filtro por categoria,
  ordenação, contagem e paginação.
* ``ProductDetailView`` / ``CategoryDetailView`` — páginas provisórias, para os
  links não quebrarem enquanto a página de produto não existe.
"""

from django.conf import settings
from django.db.models import Count, F, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce, Lower
from django.http import HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import formats
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView, TemplateView

from apps.cart.cart import max_quantity_for
from apps.catalog.models import Product, ProductStatus, ProductTranslation, ProductVariant
from apps.categories.models import Category
from apps.categories.tree import CategoryTree
from apps.core.constants import DEFAULT_LANGUAGE
from apps.core.i18n import get_content_language

# ---------------------------------------------------------------------------
# Ordenação
# ---------------------------------------------------------------------------

#: chave usada na URL -> (rótulo, expressões de ordenação)
SORT_OPTIONS = {
    "recentes": (_("Mais recentes"), ("-created_at",)),
    "nome-az": (_("Nome A-Z"), ("sort_name_lower", "-created_at")),
    "nome-za": (_("Nome Z-A"), ("-sort_name_lower", "-created_at")),
    "preco-asc": (_("Preço menor → maior"), (F("sale_price").asc(nulls_last=True), "-created_at")),
    "preco-desc": (_("Preço maior → menor"), (F("sale_price").desc(nulls_last=True), "-created_at")),
}

DEFAULT_SORT = "recentes"


# ---------------------------------------------------------------------------
# Shop
# ---------------------------------------------------------------------------


class ShopView(ListView):
    """Vitrine de uma árvore de categorias.

    Hoje só existe a de Modelos, montada sobre a categoria raiz definida em
    ``settings.SHOP_MODELS_CATEGORY_SLUG``. A view é genérica: para abrir a
    vitrine de Filamentos basta uma rota nova apontando para outra raiz —
    nenhum campo novo no banco, nenhuma duplicação de template.
    """

    context_object_name = "products"
    template_name = "catalog/shop.html"
    #: devolvido quando o pedido vem do HTMX (só a grade muda)
    partial_template_name = "catalog/_shop_results.html"
    root_category_slug = None
    page_title = _("Modelos")
    page_subtitle = _("Descubra nossos modelos impressos em 3D")

    # -- preparação --------------------------------------------------------

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.tree = CategoryTree.load()
        self.root_category = self.tree.get(self.get_root_slug())
        self.selected_category = self._resolve_selected_category()
        self.sort_key = self._resolve_sort_key()
        self.page_size = self._resolve_page_size()

    def get_root_slug(self) -> str:
        return self.root_category_slug or settings.SHOP_MODELS_CATEGORY_SLUG

    def _resolve_selected_category(self) -> Category | None:
        """Categoria do filtro, aceita só se pertencer à árvore desta vitrine."""
        slug = self.request.GET.get("categoria")
        if not slug or self.root_category is None:
            return None
        category = self.tree.get(slug)
        if category is None or not self.tree.is_inside(category.pk, self.root_category.pk):
            return None
        return category

    def _resolve_sort_key(self) -> str:
        key = self.request.GET.get("ordenar", DEFAULT_SORT)
        return key if key in SORT_OPTIONS else DEFAULT_SORT

    def _resolve_page_size(self) -> int:
        """Quantos produtos por página. Só valores da lista são aceitos.

        Número arbitrário na URL viraria um jeito fácil de pedir a base
        inteira em uma requisição.
        """
        raw = self.request.GET.get("per_page")
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            return settings.SHOP_PAGE_SIZE
        if requested in settings.SHOP_PAGE_SIZE_OPTIONS:
            return requested
        return settings.SHOP_PAGE_SIZE

    def get_paginate_by(self, queryset):
        return self.page_size

    # -- consulta ----------------------------------------------------------

    def get_queryset(self):
        if self.root_category is None:
            return Product.objects.none()

        scope = self.selected_category or self.root_category
        category_ids = self.tree.subtree_ids(scope.pk)

        language = get_content_language()
        translated_name = ProductTranslation.objects.filter(
            master=OuterRef("pk"), language=language
        ).values("name")[:1]
        default_name = ProductTranslation.objects.filter(
            master=OuterRef("pk"), language=DEFAULT_LANGUAGE.value
        ).values("name")[:1]

        queryset = (
            Product.objects.filter(status=ProductStatus.ACTIVE, category_id__in=category_ids)
            .select_related("category")
            .prefetch_related(
                "translations", "media", "colors", "category__translations", "variants"
            )
            # Ordenar por nome traduzido: o nome está na tabela de traduções,
            # então vem por subconsulta, com o português como reserva. Feito no
            # banco para não quebrar a paginação.
            .annotate(
                sort_name_lower=Lower(
                    Coalesce(Subquery(translated_name), Subquery(default_name))
                )
            )
        )
        return queryset.order_by(*SORT_OPTIONS[self.sort_key][1])

    # -- contexto ----------------------------------------------------------

    def _product_counts(self) -> dict[int, int]:
        """Produtos ativos por categoria, já somados na subárvore."""
        direct = {
            row["category_id"]: row["total"]
            for row in Product.objects.filter(
                status=ProductStatus.ACTIVE, category__isnull=False
            )
            .values("category_id")
            .annotate(total=Count("id"))
        }
        return {
            category_id: sum(direct.get(pk, 0) for pk in self.tree.subtree_ids(category_id))
            for category_id in self.tree.by_id
        }

    def _sidebar_nodes(self, counts) -> list[dict]:
        """Categorias da vitrine, achatadas com nível e link prontos."""
        if self.root_category is None:
            return []

        base_url = self.request.path
        nodes = []
        for category, depth in self.tree.descendants(self.root_category.pk):
            nodes.append(
                {
                    "category": category,
                    "name": category.name,
                    "slug": category.slug,
                    "depth": depth,
                    "count": counts.get(category.pk, 0),
                    "url": f"{base_url}?categoria={category.slug}",
                    "is_selected": (
                        self.selected_category is not None
                        and self.selected_category.pk == category.pk
                    ),
                }
            )
        return nodes

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = self._product_counts() if self.root_category else {}

        context.update(
            {
                "root_category": self.root_category,
                "expected_slug": self.get_root_slug(),
                "selected_category": self.selected_category,
                "sidebar_nodes": self._sidebar_nodes(counts),
                "total_count": counts.get(self.root_category.pk, 0) if self.root_category else 0,
                "result_count": context["paginator"].count if context.get("paginator") else 0,
                "sort_key": self.sort_key,
                "page_size": self.page_size,
                "page_size_options": settings.SHOP_PAGE_SIZE_OPTIONS,
                # Resposta parcial: o template devolve também a barra de
                # categorias, marcada com hx-swap-oob (ver _shop_sidebar.html).
                "is_partial": self.request.headers.get("HX-Request") == "true",
                "sort_options": [(key, label) for key, (label, _expr) in SORT_OPTIONS.items()],
                "page_title": self.page_title,
                "page_subtitle": self.page_subtitle,
                "meta_title": f"{self.page_title} | JD PRINT",
                "meta_description": self.page_subtitle,
                "results_url": self.request.path,
            }
        )
        return context

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return [self.partial_template_name]
        return [self.template_name]


# ---------------------------------------------------------------------------
# Páginas provisórias
# ---------------------------------------------------------------------------


class ComingSoonView(TemplateView):
    """Página de "em construção" para rotas que ainda não têm página real."""

    template_name = "catalog/coming_soon.html"
    page_kind = ""

    def get_object(self):
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_kind"] = self.page_kind
        context["object"] = self.get_object()
        return context


class ProductDetailView(DetailView):
    """Página do produto.

    Carrega tudo o que a página usa em poucas consultas: traduções, mídia,
    cores, materiais e variantes (com cor e material das variantes). A trilha
    de categorias vem da árvore em memória, que já é uma consulta só.
    """

    template_name = "catalog/product_detail.html"
    context_object_name = "product"

    def get_queryset(self):
        return (
            Product.objects.filter(status=ProductStatus.ACTIVE)
            .select_related("category", "brand")
            .prefetch_related(
                "translations",
                "media",
                "colors",
                "materials",
                "category__translations",
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .select_related("color", "material")
                    .order_by("sort_order", "id"),
                ),
            )
        )

    # -- galeria -----------------------------------------------------------

    def gallery(self, product):
        """Mídias na ordem de exibição, a principal primeiro."""
        items = list(product.media.all())
        items.sort(key=lambda item: (not item.is_primary, item.sort_order, item.pk))
        return items

    # -- variantes ---------------------------------------------------------

    def variant_options(self, variants):
        """Agrupa as variantes nos eixos que existirem: cor, tamanho, material.

        Só aparece o eixo que realmente varia — um produto que só muda de
        tamanho não mostra um seletor de cor com uma opção só.
        """
        groups = []

        colors = []
        for variant in variants:
            if variant.color and variant.color not in colors:
                colors.append(variant.color)
        if len(colors) > 1:
            groups.append(
                {
                    "key": "color",
                    "label": _("Cor"),
                    "options": [
                        {"value": str(color.pk), "label": color.name, "hex": color.hex_code}
                        for color in colors
                    ],
                }
            )

        sizes = []
        for variant in variants:
            if variant.size and variant.size not in sizes:
                sizes.append(variant.size)
        if len(sizes) > 1:
            groups.append(
                {
                    "key": "size",
                    "label": _("Tamanho"),
                    "options": [{"value": size, "label": size, "hex": ""} for size in sizes],
                }
            )

        materials = []
        for variant in variants:
            if variant.material and variant.material not in materials:
                materials.append(variant.material)
        if len(materials) > 1:
            groups.append(
                {
                    "key": "material",
                    "label": _("Material"),
                    "options": [
                        {"value": str(material.pk), "label": material.name, "hex": ""}
                        for material in materials
                    ],
                }
            )

        return groups

    def variant_payload(self, product, variants):
        """Mapa que o JavaScript usa para casar a escolha com a variante."""
        return [
            {
                "id": variant.pk,
                "color": str(variant.color_id) if variant.color_id else "",
                "size": variant.size,
                "material": str(variant.material_id) if variant.material_id else "",
                "label": variant.label,
                "price": str(variant.effective_price or ""),
                "priceDisplay": (
                    f"{product.currency_symbol} {formats.number_format(variant.effective_price, 2, use_l10n=True)}"
                    if variant.effective_price is not None
                    else ""
                ),
                "stock": variant.stock_quantity,
                "available": variant.is_available,
                "maxQuantity": max_quantity_for(product, variant),
            }
            for variant in variants
        ]

    # -- ficha técnica -----------------------------------------------------

    def specifications(self, product):
        """Só o que existe: campo vazio não vira linha na tabela."""
        rows = []
        materials = list(product.materials.all())
        if materials:
            rows.append((_("Material"), ", ".join(material.name for material in materials)))
        dimensions = product.dimensions_display()
        if dimensions:
            rows.append((_("Dimensões"), dimensions))
        if product.weight_grams is not None:
            rows.append((_("Peso"), f"{product.weight_grams.normalize():f} g"))
        print_time = product.print_time_display()
        if print_time:
            rows.append((_("Tempo de impressão"), print_time))
        if product.brand_id:
            rows.append((_("Marca"), product.brand.name))
        rows.append((_("Referência"), product.sku))
        return rows

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = context["product"]
        variants = product.active_variants()

        tree = CategoryTree.load()
        breadcrumb = []
        if product.category_id:
            chain = []
            node = tree.by_id.get(product.category_id)
            while node is not None:
                chain.append(node)
                node = tree.by_id.get(node.parent_id)
            breadcrumb = list(reversed(chain))

        context.update(
            {
                "gallery": self.gallery(product),
                "variants": variants,
                "variant_options": self.variant_options(variants),
                # Objeto puro: o template usa |json_script, que escapa com
                # segurança. Passar a string pronta faria o Django escapar as
                # aspas e o JSON chegaria quebrado no navegador.
                "variant_payload": self.variant_payload(product, variants),
                "specifications": self.specifications(product),
                "breadcrumb": breadcrumb,
                "max_quantity": max_quantity_for(product, variants[0] if variants else None),
                "notes_limit": settings.CUSTOMIZATION_NOTES_MAX_LENGTH,
                "photo_extensions": ", ".join(settings.CUSTOMIZATION_IMAGE_EXTENSIONS).upper(),
                "photo_max_mb": settings.CUSTOMIZATION_MAX_UPLOAD_SIZE // (1024 * 1024),
                "meta_title": f"{product.display_name} | JD PRINT",
                "meta_description": product.display_short_description or product.display_name,
            }
        )
        return context


class CategoryDetailView(ComingSoonView):
    """Categoria sem vitrine própria.

    Categorias da árvore de Modelos são redirecionadas para o Shop filtrado:
    a vitrine já existe, então esta página não deve competir com ela.
    """

    page_kind = "category"

    def get(self, request, *args, **kwargs):
        category = self.get_object()
        shop_url = category.get_absolute_url()
        if shop_url != request.path:
            return HttpResponsePermanentRedirect(shop_url)
        return super().get(request, *args, **kwargs)

    def get_object(self):
        if not hasattr(self, "_object"):
            self._object = get_object_or_404(
                Category.objects.select_related("parent").prefetch_related("translations"),
                slug=self.kwargs["slug"],
                is_active=True,
            )
        return self._object
