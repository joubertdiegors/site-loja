from django.contrib import admin
from django.db.models import Count

from apps.categories.models import Category, CategoryTranslation
from apps.core.admin_mixins import TranslatedSlugAdminMixin
from apps.core.constants import DEFAULT_LANGUAGE


class CategoryTranslationInline(admin.TabularInline):
    model = CategoryTranslation
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "name", "description")
    verbose_name = "tradução"
    verbose_name_plural = "traduções (o nome em português é obrigatório)"


@admin.register(Category)
class CategoryAdmin(TranslatedSlugAdminMixin):
    inlines = [CategoryTranslationInline]
    list_display = ("indented_name", "slug", "product_count", "sort_order", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("slug", "translations__name")
    ordering = ("sort_order", "slug")
    autocomplete_fields = ("parent",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("IDENTIFICAÇÃO", {"fields": ("parent", "slug", "sort_order", "is_active")}),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("parent")
            .prefetch_related("translations", "parent__translations")
            .annotate(total_products=Count("products"))
        )

    @admin.display(description="categoria", ordering="slug")
    def indented_name(self, obj):
        return f"{'— ' * obj.depth}{obj.name_in(DEFAULT_LANGUAGE.value)}"

    @admin.display(description="produtos", ordering="total_products")
    def product_count(self, obj):
        return obj.total_products
