"""Árvore de categorias carregada em memória.

Estava em ``apps/home/services.py`` na etapa 2; virou módulo próprio quando o
Shop passou a precisar da mesma estrutura. É conhecimento de categoria, não de
vitrine.
"""

from apps.categories.models import Category


class CategoryTree:
    """Mapa pai/filho carregado em uma única consulta.

    Evita consulta recursiva por seção/filtro. A árvore de uma loja cabe
    folgadamente em memória; se um dia não couber, este é o único ponto a
    trocar por uma coluna ``path`` materializada.
    """

    def __init__(self, categories):
        self.by_id = {category.pk: category for category in categories}
        self.by_slug = {category.slug: category for category in categories}
        self.children: dict[int | None, list[Category]] = {}
        for category in self.by_id.values():
            self.children.setdefault(category.parent_id, []).append(category)
        for siblings in self.children.values():
            siblings.sort(key=lambda category: (category.sort_order, category.slug))

    @classmethod
    def load(cls, only_active: bool = True) -> "CategoryTree":
        queryset = Category.objects.all().prefetch_related("translations")
        if only_active:
            queryset = queryset.filter(is_active=True)
        return cls(list(queryset))

    # -- leitura -----------------------------------------------------------

    def get(self, slug: str) -> Category | None:
        return self.by_slug.get(slug)

    def roots(self) -> list[Category]:
        return list(self.children.get(None, []))

    def children_of(self, category_id: int) -> list[Category]:
        return list(self.children.get(category_id, []))

    def subtree_ids(self, category_id: int) -> list[int]:
        """IDs da categoria e de todos os seus descendentes."""
        collected = [category_id]
        queue = [category_id]
        while queue:
            current = queue.pop()
            for child in self.children.get(current, []):
                collected.append(child.pk)
                queue.append(child.pk)
        return collected

    def descendants(self, category_id: int, depth: int = 0) -> list[tuple[Category, int]]:
        """Descendentes em ordem de exibição, com o nível de indentação."""
        result: list[tuple[Category, int]] = []
        for child in self.children.get(category_id, []):
            result.append((child, depth))
            result.extend(self.descendants(child.pk, depth + 1))
        return result

    def root_of(self, category_id: int | None) -> Category | None:
        """Categoria raiz à qual um id pertence (ou ``None`` se desconhecida)."""
        seen = set()
        current = self.by_id.get(category_id)
        while current is not None and current.parent_id is not None:
            if current.pk in seen:  # proteção contra dados inconsistentes
                return None
            seen.add(current.pk)
            current = self.by_id.get(current.parent_id)
        return current

    def is_inside(self, category_id: int | None, root_id: int) -> bool:
        root = self.root_of(category_id)
        return root is not None and root.pk == root_id
