"""Páginas públicas do catálogo.

Três telas, e uma view só por baixo das três:

* ``ShopView`` — a vitrine de uma árvore de categorias (``/modelos/``): filtro
  por categoria e por material, ordenação, contagem e paginação;
* ``CategoryDetailView`` — a página de uma categoria, que é a vitrine recortada
  nela (``/categorias/filamentos/``);
* ``SearchView`` — os resultados da busca (``/buscar/?q=...``), que é a vitrine
  recortada por texto.

Escrever três consultas para "o que o cliente pode ver" seria três lugares para
corrigir quando a regra mudar. A regra está em ``ShopView.base_queryset()``.

* ``ProductDetailView`` — a página do produto.
"""

from django.conf import settings
from collections import defaultdict

from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce, Lower
from django.http import HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.utils import formats
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import DetailView, ListView

from apps.cart.cart import max_quantity_for
from apps.catalog.models import (
    product_color_prefetches,
    product_description_prefetches,
    Material,
    Product,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.categories.models import Category
from apps.categories.tree import CategoryTree
from apps.core.constants import DEFAULT_LANGUAGE
from apps.core.i18n import get_content_language

# ---------------------------------------------------------------------------
# Ordenação
# ---------------------------------------------------------------------------

#: chave usada na URL -> (rótulo, expressões de ordenação)
#
#: ``sort_price`` é anotado no queryset (ver ``ShopView.get_queryset``): é o
#: menor preço entre as variantes ativas. O produto não tem preço para ordenar.
SORT_OPTIONS = {
    "recentes": (_("Mais recentes"), ("-created_at",)),
    "nome-az": (_("Nome A-Z"), ("sort_name_lower", "-created_at")),
    "nome-za": (_("Nome Z-A"), ("-sort_name_lower", "-created_at")),
    "preco-asc": (_("Preço menor → maior"), (F("sort_price").asc(nulls_last=True), "-created_at")),
    "preco-desc": (_("Preço maior → menor"), (F("sort_price").desc(nulls_last=True), "-created_at")),
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

    O **título e o subtítulo vêm da categoria**, que já é traduzível e já é
    editável no Admin. Trocar "Modelos" por outra palavra é editar a categoria;
    a URL ``/modelos/`` não muda, porque quem manda nela é o slug.
    """

    context_object_name = "products"
    template_name = "catalog/shop.html"
    #: devolvido quando o pedido vem do HTMX (só a grade muda)
    partial_template_name = "catalog/_shop_results.html"
    root_category_slug = None
    #: O que aparece quando a categoria raiz ainda não foi cadastrada. Não é o
    #: nome público da vitrine: esse vem da própria categoria (ver
    #: `page_title`), que já é traduzível e já é administrável.
    fallback_title = _("Modelos")
    fallback_subtitle = _("Descubra nossos modelos impressos em 3D")

    # -- preparação --------------------------------------------------------

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.tree = CategoryTree.load()
        self.root_category = self.tree.get(self.get_root_slug())
        self.selected_category = self._resolve_selected_category()
        self.selected_material = self._resolve_selected_material()
        self.sort_key = self._resolve_sort_key()
        self.page_size = self._resolve_page_size()

    def get_root_slug(self) -> str:
        return self.root_category_slug or settings.SHOP_MODELS_CATEGORY_SLUG

    @property
    def page_title(self) -> str:
        """O nome público da vitrine — o da categoria, não um texto no código.

        A vitrine de `/modelos/` é montada sobre a categoria `modelos`, que já
        tem nome traduzível em PT/FR/NL/EN e já é editável no Admin. Um segundo
        lugar para o mesmo texto seria dois lugares para corrigir — e o do
        código exigiria recompilar as traduções para trocar uma palavra.

        A **URL não muda**: quem manda nela é o slug, e o slug não é isto.
        """
        if self.root_category is not None:
            return self.root_category.name
        return str(self.fallback_title)

    @property
    def page_subtitle(self) -> str:
        """A descrição da categoria, também traduzível e também do Admin."""
        if self.root_category is not None:
            descricao = self.root_category.tr("description", default="")
            if descricao:
                return descricao
        return str(self.fallback_subtitle)

    @property
    def banner_link(self) -> Category | None:
        """Para onde o banner convida: a primeira vitrine que não é esta.

        A direção visual fecha o banner com "Ver filamentos →" — um convite
        para outra vitrine, não uma promoção escrita à mão. A outra vitrine
        sai da árvore que já está carregada (as raízes, na ordem do menu), e
        quem manda na URL continua sendo `Category.get_absolute_url()`.

        Numa página de categoria aninhada (`/modelos/?categoria=gatos` ou
        `/categorias/gatos/`) "esta vitrine" é a raiz da categoria, não ela:
        convidar de Gatos para Modelos seria convidar para onde já se está.
        """
        if self.root_category is None:
            return None
        current = self.tree.root_of(self.root_category.pk) or self.root_category
        for root in self.tree.roots():
            if root.pk != current.pk:
                return root
        return None

    def _resolve_selected_category(self) -> Category | None:
        """Categoria do filtro, aceita só se pertencer à árvore desta vitrine."""
        slug = self.request.GET.get("categoria")
        if not slug or self.root_category is None:
            return None
        category = self.tree.get(slug)
        if category is None or not self.tree.is_inside(category.pk, self.root_category.pk):
            return None
        return category

    def _resolve_selected_material(self) -> Material | None:
        """Material do filtro, pelo slug. Slug desconhecido é ignorado.

        Ignorado, e não 404: o filtro é um refinamento, e uma URL antiga com um
        material que foi apagado deve mostrar a vitrine inteira em vez de uma
        página de erro.
        """
        slug = (self.request.GET.get("material") or "").strip()
        if not slug:
            return None
        return Material.objects.filter(slug=slug, is_active=True).first()

    #: Quantas opções de material justificam desenhar a lista.
    #:
    #: Duas, na vitrine: ela mostra a loja inteira, e "Todos + PLA" seria uma
    #: escolha que não escolhe nada — a lista inteira já é PLA.
    #:
    #: A busca sobrescreve para **uma** (ver `SearchView`): lá o recorte é o
    #: resultado, e "Todos + PLA" diz algo de verdade — que tudo o que apareceu
    #: é PLA. Some só quando nenhum resultado tem material.
    MIN_MATERIAL_OPTIONS = 2

    def available_materials(self):
        """Materiais que **existem** no recorte atual, com quantos produtos.

        Só os que têm produto: um filtro que leva a zero resultado é um beco
        sem saída, e a lista fica curta o bastante para caber na barra lateral.

        A contagem ignora o material já escolhido — senão, escolher PLA deixaria
        a lista com uma linha só e o cliente não teria como trocar para PETG
        sem voltar. É o mesmo comportamento das lojas em que ele já comprou.
        """
        products = self.materials_scope()
        if products is None:
            return []

        # Etapa 2B: um material conta se é opção comercial de alguma variante
        # ativa OU está na composição do produto. Os pares (material, produto)
        # das duas origens são unidos em memória para um produto que tem PLA
        # nas duas não contar duas vezes.
        pares = defaultdict(set)
        por_variante = Material.objects.filter(
            is_active=True, variants__is_active=True, variants__product__in=products
        ).values_list("pk", "variants__product_id")
        por_composicao = Material.objects.filter(
            is_active=True, compositions__product__in=products
        ).values_list("pk", "compositions__product_id")
        for material_id, product_id in list(por_variante) + list(por_composicao):
            pares[material_id].add(product_id)

        materials = list(
            Material.objects.filter(pk__in=pares).order_by("name").prefetch_related("translations")
        )
        for material in materials:
            material.total = len(pares[material.pk])
        # Quantas opções justificam mostrar a lista — ver `MIN_MATERIAL_OPTIONS`.
        # Devolver `[]` aqui é o que faz o bloco inteiro sumir da tela: a
        # decisão fica num lugar, e não numa condição repetida em cada template
        # que inclui o filtro.
        if len(materials) < self.MIN_MATERIAL_OPTIONS:
            return []

        return [
            {
                "material": material,
                "name": material.display_name,
                "slug": material.slug,
                "count": material.total,
                "is_selected": (
                    self.selected_material is not None
                    and self.selected_material.pk == material.pk
                ),
            }
            for material in materials
        ]

    def materials_scope(self):
        """Os produtos sobre os quais a lista de materiais é contada.

        O mesmo recorte da tela, **menos** o filtro de material — ver
        `available_materials`.
        """
        if self.root_category is None:
            return None
        scope = self.selected_category or self.root_category
        return Product.objects.filter(
            status=ProductStatus.ACTIVE,
            category_id__in=self.tree.subtree_ids(scope.pk),
            variants__is_active=True,
        )

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

    def _variant_condition(self):
        """A condição que a variante tem que satisfazer — numa `Q` só.

        Numa `Q` só, e não em dois `.filter()`, porque as duas coisas falam da
        **mesma** variante. Separadas, o Django faria dois JOINs e um produto
        com uma variante ativa de PLA mais uma variante desativada de PETG
        apareceria no filtro "PETG" — vendendo o que não está à venda.
        """
        condition = Q(variants__is_active=True)
        if self.selected_material is not None:
            # Opção comercial de uma variante ativa OU material da composição do
            # produto (etapa 2B): «PLA + PETG» aparece nos dois filtros.
            condition &= Q(variants__material_id=self.selected_material.pk) | Q(
                material_composition__material_id=self.selected_material.pk
            )
        return condition

    def base_queryset(self):
        """Produtos vendáveis, anotados para ordenar e desenhar o card.

        Não sabe de categoria nem de busca: é a base de que a vitrine, a página
        de categoria e a busca partem. Foi extraída daqui para as três não
        terem três versões de "o que o cliente pode ver".
        """
        language = get_content_language()
        translated_name = ProductTranslation.objects.filter(
            master=OuterRef("pk"), language=language
        ).values("name")[:1]
        default_name = ProductTranslation.objects.filter(
            master=OuterRef("pk"), language=DEFAULT_LANGUAGE.value
        ).values("name")[:1]

        # Menor preço entre as variantes ativas — é por ele que a vitrine
        # ordena, porque é ele que o card mostra. Subconsulta e não JOIN: um
        # JOIN multiplicaria a linha do produto por variante e quebraria a
        # paginação.
        cheapest_variant = (
            ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
            .order_by("sale_price")
            .values("sale_price")[:1]
        )

        return (
            Product.objects.filter(status=ProductStatus.ACTIVE)
            # Produto sem variante ativa não é comprável: não tem preço, peso
            # nem estoque em lugar nenhum. Fora da vitrine. É a mesma regra de
            # `Product.objects.sellable()`, escrita aqui porque o filtro de
            # material entra na mesma condição.
            .filter(self._variant_condition())
            .distinct()
            .select_related("category")
            .prefetch_related(
                "translations",
                "media",
                "category__translations",
                *product_color_prefetches(),
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .select_related("color", "material")
                    .prefetch_related("color__translations", "material__translations")
                    .order_by("sort_order", "id"),
                ),
            )
            # Ordenar por nome traduzido: o nome está na tabela de traduções,
            # então vem por subconsulta, com o português como reserva. Feito no
            # banco para não quebrar a paginação.
            .annotate(
                sort_name_lower=Lower(
                    Coalesce(Subquery(translated_name), Subquery(default_name))
                ),
                sort_price=Subquery(cheapest_variant),
            )
        )

    def get_queryset(self):
        if self.root_category is None:
            return Product.objects.none()

        scope = self.selected_category or self.root_category
        category_ids = self.tree.subtree_ids(scope.pk)
        queryset = self.base_queryset().filter(category_id__in=category_ids)
        return queryset.order_by(*SORT_OPTIONS[self.sort_key][1])

    # -- contexto ----------------------------------------------------------

    def _product_counts(self) -> dict[int, int]:
        """Produtos ativos por categoria, já somados na subárvore."""
        direct = {
            row["category_id"]: row["total"]
            for row in Product.objects.filter(
                status=ProductStatus.ACTIVE, category__isnull=False, variants__is_active=True
            )
            .values("category_id")
            .annotate(total=Count("id", distinct=True))
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
                "selected_material": self.selected_material,
                "material_nodes": self.available_materials(),
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
                "banner_link": self.banner_link,
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
# Produto
# ---------------------------------------------------------------------------


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
                "category__translations",
                *product_description_prefetches(),
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .select_related("color", "material")
                    .prefetch_related(
                        "color__translations", "material__translations", "media"
                    )
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

        Um eixo só vira botões quando cumpre **duas** condições:

        1. varia — um seletor de cor com uma opção só é ruído;
        2. está preenchido em **todas** as variantes ativas.

        A segunda é o que impede uma variante de ficar inalcançável. Com duas
        variantes coloridas e uma sem cor, os botões de cor não teriam botão
        para a terceira, e o ``<select>`` que a alcançaria está ``sr-only``:
        ela existiria no catálogo e não haveria clique que a vendesse. Sem o
        grupo, o ``<select>`` volta a ser a interface — e ele lista todas.
        """
        groups = []

        def preenchido_em_todas(atributo) -> bool:
            return all(getattr(variant, atributo) for variant in variants)

        colors = []
        for variant in variants:
            if variant.color and variant.color not in colors:
                colors.append(variant.color)
        if len(colors) > 1 and preenchido_em_todas("color_id"):
            groups.append(
                {
                    "key": "color",
                    "label": _("Cor"),
                    "options": [
                        {
                            "value": str(color.pk),
                            "label": color.display_name,
                            "hex": color.hex_code,
                        }
                        for color in colors
                    ],
                }
            )

        sizes = []
        for variant in variants:
            if variant.size and variant.size not in sizes:
                sizes.append(variant.size)
        if len(sizes) > 1 and preenchido_em_todas("size"):
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
        if len(materials) > 1 and preenchido_em_todas("material_id"):
            groups.append(
                {
                    "key": "material",
                    "label": _("Material"),
                    "options": [
                        {"value": str(material.pk), "label": material.display_name, "hex": ""}
                        for material in materials
                    ],
                }
            )

        return groups

    def variant_payload(self, product, variants):
        """Mapa que o JavaScript usa para casar a escolha com a variante.

        Leva tudo o que muda ao trocar de opção: preço, disponibilidade, peso,
        prazo de produção e ficha técnica. Trocar de cor no seletor não pode
        deixar na tela o peso da outra.
        """
        return [
            {
                "id": variant.pk,
                "color": str(variant.color_id) if variant.color_id else "",
                "size": variant.size,
                "material": str(variant.material_id) if variant.material_id else "",
                "label": variant.label,
                # Os nomes, para a ficha técnica. `color`/`material` acima
                # são chaves primárias: servem para casar a combinação, não
                # para escrever na tela.
                "colorLabel": variant.color.display_name if variant.color_id else "",
                "sizeLabel": variant.size or "",
                "materialLabel": (
                    variant.material.display_name if variant.material_id else ""
                ),
                "sku": variant.sku,
                "price": str(variant.sale_price or ""),
                "priceDisplay": (
                    f"{product.currency_symbol} {formats.number_format(variant.sale_price, 2, use_l10n=True)}"
                    if variant.sale_price is not None
                    else ""
                ),
                "stock": variant.stock_quantity,
                "available": variant.is_available,
                "stockState": variant.stock_state,
                "stockLabel": self.stock_label(variant),
                "productionDays": variant.production_lead_time_days or 0,
                "weightGrams": (
                    f"{variant.weight_grams.normalize():f}"
                    if variant.weight_grams is not None
                    else ""
                ),
                "dimensions": variant.dimensions_display(),
                "printTime": variant.print_time_display(),
                "maxQuantity": max_quantity_for(product, variant),
                # A foto desta variante, quando alguém vinculou uma. Vazio quer
                # dizer "use as fotos gerais" — não é falta de dado.
                "mediaUrl": self.variant_media_url(variant),
                "mediaAlt": self.variant_media_alt(variant, product),
            }
            for variant in variants
        ]

    def variant_media_url(self, variant) -> str:
        """URL da foto vinculada a esta variante, ou vazio."""
        media = variant.display_media
        return media.file.url if media and media.file else ""

    def variant_media_alt(self, variant, product) -> str:
        media = variant.display_media
        if media is None:
            return ""
        return media.alt_text or f"{product.display_name} — {variant.display_label}"

    def stock_label(self, variant) -> str:
        """A frase de disponibilidade desta variante, pronta para a tela."""
        if variant.made_to_order:
            days = variant.production_lead_time_days
            if days:
                return ngettext(
                    "Pronto em %(days)s dia", "Pronto em %(days)s dias", days
                ) % {"days": days}
            return str(_("Sob encomenda"))
        if not variant.is_available:
            return str(_("Esgotado"))
        if variant.stock_state == "low":
            return str(_("Últimas unidades"))
        return str(_("Em estoque"))

    # -- ficha técnica -----------------------------------------------------

    def specifications(self, product, variant, variants=()):
        """Ficha técnica **da variante selecionada**.

        Peso, dimensões e tempo de impressão são da unidade que o cliente vai
        receber — a mesma peça em 25 cm não pesa o que pesa em 10 cm.

        Cada linha leva uma chave: é por ela que o JavaScript acha a linha para
        atualizar quando o cliente troca de opção.

        Por isso a linha de um eixo entra quando **qualquer** variante do
        produto tem aquele eixo, e não só a que abre a página: se a primeira
        variante não tem tamanho, a linha precisa existir mesmo assim para
        receber o tamanho da próxima. Vazia, ela nasce escondida — como
        peso, dimensões e tempo de impressão sempre fizeram.
        """
        rows = []
        if variant is None:
            rows.append(("referencia", _("Referência"), product.sku))
            return rows

        eixos = (
            (
                "material",
                _("Material"),
                variant.material.display_name if variant.material_id else "",
                any(outra.material_id for outra in variants),
            ),
            (
                "cor",
                _("Cor"),
                variant.color.display_name if variant.color_id else "",
                any(outra.color_id for outra in variants),
            ),
            ("tamanho", _("Tamanho"), variant.size or "", any(outra.size for outra in variants)),
        )
        for chave, rotulo, valor, alguma_variante_tem in eixos:
            if valor or alguma_variante_tem:
                rows.append((chave, rotulo, valor))

        # Etapa 2B: a descrição da PEÇA — cores e composição — vem do produto e
        # não muda ao trocar de opção. A composição de um material só, igual ao
        # material da variante, não é repetida.
        if product.colors_text:
            rows.append(("cores", _("Cores"), product.colors_text))
        composicao = product.composition
        repete_a_variante = (
            len(composicao) == 1
            and composicao[0].percentage is None
            and composicao[0].material_id == variant.material_id
        )
        if composicao and not repete_a_variante:
            rows.append(("materiais", _("Materiais"), product.materials_text))

        # Estas quatro sempre entram, mesmo vazias: a linha nasce escondida e o
        # JavaScript a mostra quando a variante escolhida tiver o dado.
        rows.append(("dimensoes", _("Dimensões"), variant.dimensions_display()))
        rows.append((
            "peso",
            _("Peso"),
            f"{variant.weight_grams.normalize():f} g" if variant.weight_grams is not None else "",
        ))
        rows.append(("impressao", _("Tempo de impressão"), variant.print_time_display()))
        if product.brand_id:
            rows.append(("marca", _("Marca"), product.brand.name))
        rows.append(("referencia", _("Referência"), variant.sku))
        return rows

    # -- sugestões ---------------------------------------------------------

    #: Quantos cards a seção mostra. Quatro fecha a grade do desktop e as duas
    #: colunas do celular sem sobrar linha pela metade.
    RECOMMENDED_LIMIT = 4

    def recommended(self, product):
        """Outros produtos que o cliente pode querer ver.

        Três passadas, nesta ordem, sem repetir:

        1. **mesma categoria** — é a relação que o catálogo já tem e a que o
           cliente entende;
        2. **destaques** — o que a loja escolheu empurrar;
        3. **o resto**, do mais recente para o mais antigo, só para a seção não
           aparecer vazia num catálogo pequeno.

        `sellable()` em todas: produto sem variante ativa não tem preço, peso
        nem estoque, e um card sem preço não convida ninguém a clicar.

        Determinística de propósito. Recomendação de verdade — quem viu isto
        viu aquilo — depende de dados de navegação que a loja ainda não coleta;
        inventar um critério agora só faria parecer inteligente.
        """
        base = (
            Product.objects.sellable()
            .exclude(pk=product.pk)
            .select_related("category")
            .prefetch_related(
                "translations",
                "media",
                "category__translations",
                *product_color_prefetches(),
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .select_related("color", "material")
                    .prefetch_related("color__translations")
                    .order_by("sort_order", "id"),
                ),
            )
        )

        escolhidos = []
        vistos = {product.pk}

        passadas = []
        if product.category_id:
            passadas.append(base.filter(category_id=product.category_id).order_by("-created_at"))
        passadas.append(base.filter(is_featured=True).order_by("featured_order", "-created_at"))
        passadas.append(base.order_by("-created_at"))

        for passada in passadas:
            if len(escolhidos) >= self.RECOMMENDED_LIMIT:
                break
            for candidato in passada[: self.RECOMMENDED_LIMIT * 2]:
                if candidato.pk in vistos:
                    continue
                vistos.add(candidato.pk)
                escolhidos.append(candidato)
                if len(escolhidos) >= self.RECOMMENDED_LIMIT:
                    break

        return escolhidos

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = context["product"]
        variants = product.active_variants()
        selected = product.default_variant

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
                # A variante que abre selecionada: a primeira disponível; se
                # todas estiverem esgotadas, a primeira, para a página ainda
                # ter preço e ficha ao anunciar que acabou.
                "selected_variant": selected,
                "variant_options": self.variant_options(variants),
                # Objeto puro: o template usa |json_script, que escapa com
                # segurança. Passar a string pronta faria o Django escapar as
                # aspas e o JSON chegaria quebrado no navegador.
                "variant_payload": self.variant_payload(product, variants),
                "specifications": self.specifications(product, selected, variants),
                "breadcrumb": breadcrumb,
                "recommended": self.recommended(product),
                "max_quantity": max_quantity_for(product, selected),
                "notes_limit": settings.CUSTOMIZATION_NOTES_MAX_LENGTH,
                "photo_extensions": ", ".join(settings.CUSTOMIZATION_IMAGE_EXTENSIONS).upper(),
                "photo_max_mb": settings.CUSTOMIZATION_MAX_UPLOAD_SIZE // (1024 * 1024),
                "meta_title": f"{product.display_name} | JD PRINT",
                "meta_description": product.display_short_description or product.display_name,
            }
        )
        return context


class CategoryDetailView(ShopView):
    """A página de uma categoria — que é a vitrine, recortada nela.

    Filamentos e Acessórios caíam numa página "em construção" mesmo tendo
    produtos cadastrados: eram raízes sem vitrine, e só a árvore de Modelos
    tinha uma. Agora a vitrine é de quem a pedir, porque ela nunca dependeu de
    qual árvore era — só da raiz que recebe.

    Categorias **dentro** da árvore que já tem rota própria continuam
    redirecionando para lá (`/modelos/?categoria=gatos`): duas URLs mostrando a
    mesma grade seriam duas páginas para o Google indexar e uma para o cliente
    entender. Quem decide é `Category.get_absolute_url()`, que não mudou.
    """

    def setup(self, request, *args, **kwargs):
        self.category = get_object_or_404(
            Category.objects.select_related("parent").prefetch_related("translations"),
            slug=kwargs.get("slug"),
            is_active=True,
        )
        super().setup(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        canonical = self.category.get_absolute_url()
        if canonical != request.path:
            return HttpResponsePermanentRedirect(canonical)
        return super().get(request, *args, **kwargs)

    def get_root_slug(self) -> str:
        """A própria categoria é a raiz desta vitrine.

        Assim a barra lateral mostra as subcategorias **dela**, e não a árvore
        inteira da loja — que numa categoria folha não teria o que oferecer.
        """
        return self.category.slug

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        descricao = self.category.tr("description", default="") or str(
            _("Produtos da categoria %(name)s.") % {"name": self.category.name}
        )
        context.update(
            {
                "category": self.category,
                "page_title": self.category.name,
                "page_subtitle": descricao,
                "meta_title": f"{self.category.name} | JD PRINT",
                "meta_description": descricao,
                "breadcrumb": self.category.ancestors(),
            }
        )
        return context


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------

#: Quantas palavras da consulta são levadas em conta. Cada palavra vira um
#: `.filter()` com uma dezena de JOINs; sem limite, uma URL com duzentas
#: palavras seria um jeito barato de derrubar o banco.
MAX_SEARCH_TERMS = 6

#: Consulta menor que isto não busca. Uma letra devolveria meio catálogo e
#: custaria a varredura inteira para não ajudar ninguém.
MIN_SEARCH_LENGTH = 2


class SearchView(ShopView):
    """Resultados da busca — a vitrine recortada por texto em vez de categoria.

    ## O que ela procura

    Por palavra, em nome, descrição curta, descrição, SKU do produto, SKU da
    variante, nome da categoria e nome do material. Várias palavras funcionam
    como "e": "gato preto" pede as duas, mas cada uma pode aparecer num campo
    diferente — o nome tem "gato" e a variante tem "preto".

    ## Idioma

    **Procura em todos os idiomas cadastrados; mostra no idioma do cliente.**

    São duas coisas diferentes, e misturá-las era o defeito: até aqui a busca
    só olhava o idioma da tela mais o português, e o mesmo produto existia ou
    não conforme a bandeirinha escolhida. Numa loja belga isso é errado — o
    cliente lê o rótulo em francês, ouve falar do produto em neerlandês e vê o
    nome em inglês numa rede social.

    Achar o produto por "Dinosaurus" não muda nada na tela: o card continua
    escrevendo o nome no idioma do cliente, com o fallback de sempre.

    ## O que ela não faz

    Sem SQL cru, sem `SearchVector`: `icontains` no ORM. É o que este catálogo
    pede — a busca por prefixo/similaridade do PostgreSQL entra quando o número
    de produtos justificar, e será uma troca dentro de `search_filter()`.
    """

    template_name = "catalog/search.html"
    partial_template_name = "catalog/_shop_results.html"

    def setup(self, request, *args, **kwargs):
        # Depois do `super()`, e nao antes: e ele quem poe `self.request`, de
        # onde a consulta e lida. Nada do que ele faz depende de `self.query`.
        super().setup(request, *args, **kwargs)
        self.query = self._resolve_query()

    def _resolve_query(self) -> str:
        """O texto pedido, limpo. Espaço repetido não é palavra."""
        return " ".join((self.request.GET.get("q") or "").split())

    @property
    def terms(self) -> list[str]:
        return self.query.split()[:MAX_SEARCH_TERMS]

    @property
    def has_query(self) -> bool:
        return len(self.query) >= MIN_SEARCH_LENGTH

    def get_root_slug(self) -> str:
        """A busca não tem raiz: ela varre a loja inteira."""
        return ""

    def search_filter(self, term: str):
        """Onde uma palavra pode aparecer. Trocar isto troca a busca inteira.

        Em **todas** as traduções, sem filtrar por idioma. A loja é belga: o
        mesmo cliente lê o rótulo em francês, ouve falar do produto em
        neerlandês e vê o nome em inglês numa rede social. Procurar só no
        idioma da tela transformava cada troca de idioma numa loja diferente —
        o mesmo produto existia ou não existia conforme a bandeirinha.

        Procurar em todos os idiomas não custa uma consulta por idioma: é o
        mesmo JOIN de sempre em `translations`, só sem a condição de idioma. O
        que pode repetir a linha do produto (uma vez por tradução que casou)
        resolve-se com o `distinct()` que `get_queryset` já faz.

        Isto é sobre **encontrar**. O que aparece na tela continua sendo o
        idioma do cliente, com o fallback de sempre — quem escreve o card é o
        `display_name`, e ele não sabe por qual tradução o produto foi achado.
        """
        texto = (
            Q(translations__name__icontains=term)
            | Q(translations__short_description__icontains=term)
            | Q(translations__description__icontains=term)
        )
        categoria = Q(category__translations__name__icontains=term)
        # `variants__is_active` junto na mesma `Q`: é a mesma variante. Sem
        # isso, o SKU de uma variante desativada traria o produto de volta.
        variante = Q(variants__is_active=True) & (
            Q(variants__sku__icontains=term)
            | Q(variants__material__name__icontains=term)
            | Q(variants__material__translations__name__icontains=term)
        )
        return texto | Q(sku__icontains=term) | categoria | variante

    def get_queryset(self):
        if not self.has_query:
            return Product.objects.none()

        queryset = self.base_queryset()
        # Um `.filter()` por palavra: assim cada palavra pode casar numa linha
        # relacionada diferente. Num `.filter()` só, "gato preto" exigiria que a
        # MESMA tradução tivesse as duas palavras.
        for term in self.terms:
            queryset = queryset.filter(self.search_filter(term))
        # `distinct()` de novo: cada JOIN acrescentado acima pode repetir a
        # linha do produto, e o cliente veria o mesmo card três vezes.
        return queryset.distinct().order_by(*SORT_OPTIONS[self.sort_key][1])

    #: Um material já é informação aqui, ao contrário da vitrine: o recorte é
    #: o resultado da busca, e "Todos + PLA" conta ao cliente que tudo o que
    #: ele encontrou é PLA. Com o mínimo da vitrine (dois), o filtro sumia em
    #: praticamente toda busca — de oito termos medidos, sete alcançavam
    #: exatamente um material.
    MIN_MATERIAL_OPTIONS = 1

    def materials_scope(self):
        """A lista de materiais conta sobre o resultado, não sobre a loja."""
        if not self.has_query:
            return None
        products = Product.objects.filter(
            status=ProductStatus.ACTIVE, variants__is_active=True
        )
        for term in self.terms:
            products = products.filter(self.search_filter(term))
        return products.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        total = context["paginator"].count if context.get("paginator") else 0
        titulo = (
            _("Resultados para “%(query)s”") % {"query": self.query}
            if self.has_query
            else _("Buscar produtos")
        )
        context.update(
            {
                "query": self.query,
                "has_query": self.has_query,
                "is_search": True,
                "result_count": total,
                # Sem raiz não há barra de categorias: a busca atravessa a loja
                # inteira, e uma lateral marcando "Todos" não filtraria nada.
                "sidebar_nodes": [],
                "total_count": total,
                "page_title": titulo,
                "page_subtitle": "",
                "meta_title": f"{titulo} | JD PRINT",
                "meta_description": str(_("Resultados da busca na loja JD PRINT.")),
            }
        )
        return context
