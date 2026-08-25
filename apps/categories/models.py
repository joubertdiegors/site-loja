"""Árvore de categorias.

Decisão: uma única tabela com auto-relacionamento (``parent``) em vez de
``categoria``/``subcategoria``. Isso permite qualquer profundidade
(Modelos > Animais > Gatos) sem migração adicional, e uma categoria "folha"
não é estruturalmente diferente de uma categoria raiz.
"""

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel, TranslatableMixin, TranslationBase
from apps.core.utils import unique_slugify

#: Profundidade máxima aceita (proteção contra árvores absurdas em menus).
MAX_CATEGORY_DEPTH = 5


class CategoryQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def with_translations(self):
        return self.prefetch_related("translations")

    def roots(self):
        return self.filter(parent__isnull=True)


class Category(TranslatableMixin, TimeStampedModel):
    translatable_fields = ("name", "description")

    parent = models.ForeignKey(
        "self",
        verbose_name="categoria pai",
        related_name="children",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Deixe vazio para uma categoria de primeiro nível.",
    )
    slug = models.SlugField(
        "slug",
        max_length=220,
        unique=True,
        blank=True,
        help_text="Identificador para URL. Gerado a partir do nome quando deixado em branco.",
    )
    sort_order = models.PositiveIntegerField(
        "ordem",
        default=0,
        help_text="Menor valor aparece primeiro dentro do mesmo nível.",
    )
    is_active = models.BooleanField("ativa", default=True)

    objects = CategoryQuerySet.as_manager()

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ("sort_order", "slug")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name="category_parent_is_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["parent", "sort_order"], name="category_parent_order_idx"),
        ]

    def __str__(self) -> str:
        return self.full_path()

    # -- nome / tradução ---------------------------------------------------

    @property
    def name(self) -> str:
        """Nome no idioma ativo, com fallback para português e depois o slug."""
        return self.tr("name", default=self.slug)

    def name_in(self, language: str) -> str:
        return self.tr("name", language=language, default=self.slug)

    # -- hierarquia --------------------------------------------------------

    def ancestors(self) -> list["Category"]:
        """Da raiz até o pai imediato."""
        chain: list[Category] = []
        node = self.parent
        while node is not None and len(chain) <= MAX_CATEGORY_DEPTH:
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    @property
    def depth(self) -> int:
        return len(self.ancestors())

    def full_path(self, separator: str = " › ") -> str:
        return separator.join([*(node.name for node in self.ancestors()), self.name])

    def get_absolute_url(self) -> str:
        """URL pública da categoria.

        Categorias dentro da árvore que tem vitrine própria (hoje só
        "modelos") apontam para o Shop já filtrado; as demais continuam na
        página provisória até ganharem a sua vitrine.

        A subida até a raiz usa ``self.parent``, que vem do cache quando o
        queryset fez ``select_related``. Categorias raiz (menu, rodapé, cards
        da Home) não sobem nenhum nível.
        """
        from django.conf import settings
        from django.urls import reverse

        root = self
        seen = {self.pk}
        while root.parent_id is not None:
            root = root.parent
            if root.pk in seen:  # dados inconsistentes: não travar a página
                break
            seen.add(root.pk)

        if root.slug == settings.SHOP_MODELS_CATEGORY_SLUG:
            shop_url = reverse("catalog:models_shop")
            if root.pk == self.pk:
                return shop_url
            return f"{shop_url}?categoria={self.slug}"

        return reverse("catalog:category_detail", kwargs={"slug": self.slug})

    # -- regras ------------------------------------------------------------

    def clean(self):
        super().clean()
        self._validate_parent()

    def _validate_parent(self):
        if self.parent_id is None:
            return

        if self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "Uma categoria não pode ser pai dela mesma."})

        seen = {self.pk} if self.pk else set()
        node = self.parent
        depth = 0
        while node is not None:
            if node.pk in seen:
                raise ValidationError({"parent": "Hierarquia circular de categorias."})
            seen.add(node.pk)
            depth += 1
            if depth >= MAX_CATEGORY_DEPTH:
                raise ValidationError(
                    {"parent": f"Profundidade máxima de {MAX_CATEGORY_DEPTH} níveis atingida."}
                )
            node = node.parent

    def save(self, *args, **kwargs):
        if not self.slug:
            source = getattr(self, "_pending_name", "") or "categoria"
            self.slug = unique_slugify(self, source)
        super().save(*args, **kwargs)


class CategoryTranslation(TranslationBase):
    master = models.ForeignKey(
        Category,
        verbose_name="categoria",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField("nome", max_length=200)
    description = models.TextField("descrição", blank=True)

    class Meta:
        verbose_name = "tradução da categoria"
        verbose_name_plural = "traduções da categoria"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"],
                name="category_translation_unique_language",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="category_translation_name_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_language_display()})"
