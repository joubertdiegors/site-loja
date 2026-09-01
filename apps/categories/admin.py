from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Count

from apps.categories.models import Category, CategoryTranslation
from apps.core.admin_mixins import DuplicateAdminMixin, TranslatedSlugAdminMixin
from apps.core.constants import DEFAULT_LANGUAGE


class CategoryTranslationInlineFormSet(forms.BaseInlineFormSet):
    """Duas categorias com o mesmo nome no mesmo nível não podem existir.

    É a identidade de uma categoria: o cliente a reconhece pelo nome, dentro do
    lugar onde ela está. "Gatos" dentro de "Animais" é uma; duas seriam duas
    entradas idênticas no menu, com endereços diferentes, e o cliente
    escolhendo entre elas no escuro.

    Hoje nada impedia isso — o `slug` é o único campo `unique`, e ele é gerado
    a partir do nome com um "-2" no fim quando colide. Ou seja: o banco
    aceitava a segunda "Gatos" e ainda inventava um endereço para ela, em
    silêncio. Era exatamente o que "Duplicar" + "Salvar" produziria.

    ## Por que aqui, e não uma constraint de banco

    O nome mora em `CategoryTranslation` e o nível (`parent`) mora em
    `Category`: são duas tabelas, e nenhuma `UniqueConstraint` atravessa
    relação. A checagem tem que ver as duas ao mesmo tempo, e o formset é o
    primeiro lugar onde as duas existem — a categoria (com o pai já preenchido
    pelo formulário principal) e os nomes que estão sendo gravados.

    Comparação sem diferenciar maiúsculas e acentuação de espaços: "Gatos" e
    "gatos " são o mesmo nome para quem lê o menu.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        nome = ""
        for form in self.forms:
            dados = form.cleaned_data
            if not dados or dados.get("DELETE"):
                continue
            if dados.get("language") == DEFAULT_LANGUAGE.value:
                nome = (dados.get("name") or "").strip()

        # Sem nome em português não há o que comparar: o `min_num` do inline já
        # cobra a linha, e é dele a mensagem.
        if not nome:
            return

        irmas = Category.objects.filter(
            parent_id=self.instance.parent_id,
            translations__language=DEFAULT_LANGUAGE.value,
            translations__name__iexact=nome,
        )
        if self.instance.pk:
            irmas = irmas.exclude(pk=self.instance.pk)

        if irmas.exists():
            raise ValidationError(
                f"Já existe uma categoria com o nome “{nome}” neste nível. "
                "Duas iguais apareceriam duas vezes no menu, com endereços "
                "diferentes."
            )


class CategoryTranslationInline(admin.TabularInline):
    model = CategoryTranslation
    formset = CategoryTranslationInlineFormSet
    extra = 0
    min_num = 1
    validate_min = True
    fields = ("language", "name", "description")
    verbose_name = "tradução"
    verbose_name_plural = "traduções (o nome em português é obrigatório)"


@admin.register(Category)
class CategoryAdmin(DuplicateAdminMixin, TranslatedSlugAdminMixin):
    #: `parent`, `sort_order` e `is_active` acompanham a cópia — é o que uma
    #: categoria irmã tem em comum. O `slug` sai vazio: o
    #: `TranslatedSlugAdminMixin` o gera a partir do nome em português, e um
    #: slug copiado viraria "gatos-2" sem ninguém pedir.
    duplicate_exclude = ("slug",)

    #: Os nomes por idioma são o conteúdo da categoria. Vêm preenchidos para
    #: serem editados — e, se ninguém os editar, o formset acima recusa.
    duplicate_inlines = {CategoryTranslation: ()}

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
