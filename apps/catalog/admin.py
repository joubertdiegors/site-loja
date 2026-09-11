"""Configuração do Django Admin do catálogo.

O formulário de produto é organizado em seções (``fieldsets``) e usa três
inlines: traduções, variantes e mídias.
"""

from decimal import Decimal

from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.main import ORDER_VAR, PAGE_VAR, SEARCH_VAR, ChangeList
from django.contrib.admin.widgets import AutocompleteSelect, RelatedFieldWidgetWrapper
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, RestrictedError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.catalog import sku as sku_rules
from apps.catalog.admin_filters import (
    FILTROS_DE_PRODUTO,
    PrecoFiltro,
    arvore_de_categorias,
    caminho_de_categoria,
)

from apps.catalog.models import (
    Brand,
    Color,
    ColorComponent,
    ColorMode,
    ColorTranslation,
    Material,
    MaterialTranslation,
    MediaType,
    PricingMode,
    Product,
    ProductColor,
    ProductMaterialComposition,
    ProductMedia,
    ProductOption,
    ProductOptionTranslation,
    ProductOptionValue,
    ProductOptionValueTranslation,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
    apply_product_template,
    copy_product_content,
    product_content_copy_plan,
    color_prefetches,
    validate_composition,
    variant_option_prefetches,
)
from apps.categories.models import Category
from apps.core.admin_mixins import (
    DUPLICATE_PARAM,
    AuditUserAdminMixin,
    DuplicateAdminMixin,
    TranslatedSlugAdminMixin,
)
from apps.core.constants import DEFAULT_LANGUAGE, Language
from apps.core.languages import active_languages
from apps.core.richtext import sanitize_rich_text
from apps.core.widgets import RichTextWidget
from apps.core.i18n import normalize_language
from apps.core.models import SiteLanguage
from apps.core.utils import unique_slugify

# ---------------------------------------------------------------------------
# Atributos
# ---------------------------------------------------------------------------


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "website", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class NameTranslationInline(admin.TabularInline):
    """O nome do atributo em cada idioma, um por linha.

    Mesmo padrão do produto e da categoria: uma linha por idioma, nunca um
    campo ``nome_fr`` no modelo. Acrescentar um idioma é inserir linha.

    Serve cor e material — a mecânica é a mesma, e duas cópias dela seriam duas
    chances de divergir.
    """

    extra = 0
    fields = ("language", "name")
    verbose_name = "tradução"
    verbose_name_plural = (
        "TRADUÇÕES — o nome que o cliente lê. Sem o idioma cadastrado, "
        "a loja mostra o português."
    )

    def get_extra(self, request, obj=None, **kwargs):
        """Cadastro novo já abre com os idiomas todos para preencher."""
        if obj is None:
            return len(Language.choices)
        faltando = len(Language.choices) - obj.translations.count()
        return max(faltando, 0)

    def get_formset(self, request, obj=None, **kwargs):
        """Pré-seleciona o idioma que falta em cada linha vazia."""
        formset = super().get_formset(request, obj, **kwargs)
        existentes = set(obj.translations.values_list("language", flat=True)) if obj else set()
        faltando = [code for code, _label in Language.choices if code not in existentes]

        class Formset(formset):
            def _construct_form(self, index, **form_kwargs):
                form = super()._construct_form(index, **form_kwargs)
                vazio = index - self.initial_form_count()
                if 0 <= vazio < len(faltando):
                    form.fields["language"].initial = faltando[vazio]
                return form

        return Formset


def translations_column(obj):
    """Quais idiomas já têm nome — e quais faltam, em vermelho."""
    por_idioma = {t.language: t.name for t in obj.translations.all()}
    partes = []
    for code, _label in Language.choices:
        nome = por_idioma.get(code)
        partes.append((code.upper(), nome or "—", "#1a7f37" if nome else "#b42318"))
    return format_html_join(
        " · ",
        '<span style="color:{}"><b>{}</b> {}</span>',
        ((cor, code, nome) for code, nome, cor in partes),
    )


class MaterialTranslationForm(forms.ModelForm):
    """Nome e descrição do material em um idioma.

    A descrição é HTML: ela chega do editor e sai daqui **limpa**, pela lista
    de permissões de `apps.core.richtext`. Gravar já sanitizado é o que
    permite a página do produto desenhar o conteúdo sem confiar em quem o
    escreveu — e a limpeza acontece de novo na hora de exibir, porque o banco
    também recebe conteúdo de importação e de `shell`.
    """

    class Meta:
        model = MaterialTranslation
        fields = ("language", "name", "description")
        widgets = {"description": RichTextWidget()}

    def clean_description(self):
        return sanitize_rich_text(self.cleaned_data.get("description") or "")


class MaterialTranslationInline(admin.StackedInline):
    """As traduções do material, uma por idioma — com a descrição que o cliente lê.

    Empilhado, e não em tabela como o de cor: o editor de texto rico não cabe
    numa célula. A mecânica é a mesma de sempre — uma linha por idioma, nenhum
    campo `descricao_fr` no modelo — e as linhas em branco seguem os idiomas
    que a loja oferece **hoje** (`SiteLanguage`), então ligar um idioma novo o
    faz aparecer aqui sem tocar em código.
    """

    model = MaterialTranslation
    form = MaterialTranslationForm
    extra = 0
    fields = ("language", "name", "description")
    verbose_name = "tradução"
    verbose_name_plural = (
        "TRADUÇÕES — o nome e a descrição que o cliente lê na página do produto. "
        "Sem o idioma cadastrado, a loja mostra o português."
    )

    def _missing_languages(self, obj) -> list:
        """Idiomas ativos da loja que este material ainda não tem."""
        existentes = set(obj.translations.values_list("language", flat=True)) if obj else set()
        codigos = []
        for language in active_languages():
            codigo = normalize_language(language.code)
            if codigo not in existentes and codigo not in codigos:
                codigos.append(codigo)
        return codigos

    def get_extra(self, request, obj=None, **kwargs):
        return len(self._missing_languages(obj))

    def get_formset(self, request, obj=None, **kwargs):
        """Pré-seleciona, em cada linha vazia, um idioma que falta."""
        formset = super().get_formset(request, obj, **kwargs)
        faltando = self._missing_languages(obj)

        class Formset(formset):
            def _construct_form(self, index, **form_kwargs):
                form = super()._construct_form(index, **form_kwargs)
                vazio = index - self.initial_form_count()
                if 0 <= vazio < len(faltando):
                    form.fields["language"].initial = faltando[vazio]
                return form

        return Formset


@admin.register(Material)
class MaterialAdmin(DuplicateAdminMixin):
    """O material é um só; o que muda por idioma é o nome que o cliente lê."""

    #: O `name` é copiado de propósito: ele é a identidade (é `unique`), e é o
    #: que faz "Duplicar" + "Salvar" sem mexer em nada voltar com "Material com
    #: este nome interno já existe" em vez de criar um segundo PLA. O `slug` sai
    #: vazio porque o `save()` o gera a partir do nome.
    duplicate_exclude = ("slug",)

    #: As traduções são o motivo de duplicar um material: são elas que custam
    #: caro de digitar. Cada uma vira uma linha nova, do material novo.
    duplicate_inlines = {MaterialTranslation: ()}

    inlines = [MaterialTranslationInline]
    list_display = ("name", "translations_display", "descriptions_display", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "translations__name")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "description", "is_active")

    class Media:
        css = {"all": ("admin/css/jdprint_admin.css",)}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="traduções")
    def translations_display(self, obj):
        return translations_column(obj)

    @admin.display(description="descrição")
    def descriptions_display(self, obj):
        """Em quais idiomas a descrição já foi escrita.

        O nome é obrigatório e quase sempre está lá; a descrição é o trabalho
        de verdade, e é ela que falta. A coluna mostra onde.
        """
        com_texto = sorted(
            t.language.upper() for t in obj.translations.all() if (t.description or "").strip()
        )
        if not com_texto:
            # `format_html` exige ao menos um argumento no Django 6; o traço
            # vai como valor, e não embutido na string.
            return format_html('<span style="color:#b42318">{}</span>', "—")
        return format_html('<span style="color:#1a7f37">{}</span>', " · ".join(com_texto))


class ColorTranslationInline(NameTranslationInline):
    model = ColorTranslation


class ColorComponentInlineFormSet(forms.BaseInlineFormSet):
    """As regras de conjunto da cor composta, com a mensagem legível.

    Linha a linha o modelo já confere (`ColorComponent.clean`); aqui entra o
    que só se vê olhando a lista inteira: duas ou mais componentes, nenhuma
    repetida, e nenhuma outra cor com a mesma composição.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        vivas = [
            form.cleaned_data["component"].pk
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("component")
        ]
        validate_composition(self.instance, vivas)


class ColorComponentInline(admin.TabularInline):
    """COMPONENTES: vazio para a cor simples; duas ou mais para a composta."""

    model = ColorComponent
    fk_name = "color"
    formset = ColorComponentInlineFormSet
    extra = 0
    fields = ("component", "sort_order")
    verbose_name = "componente"
    verbose_name_plural = (
        "COMPONENTES — deixe vazio para uma cor simples; duas ou mais cores simples, "
        "em ordem, fazem uma cor composta («Branco + Azul»)"
    )

    def get_formset(self, request, obj=None, **kwargs):
        self._parent_color = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "component":
            # Só cores simples, e nunca a própria: é a regra do modelo, oferecida
            # antes de ser cobrada. O `ColorSelect` traz o hex para a bolinha.
            queryset = Color.objects.filter(is_composite=False).prefetch_related("translations")
            pai = getattr(self, "_parent_color", None)
            if pai is not None and pai.pk:
                queryset = queryset.exclude(pk=pai.pk)
            kwargs["queryset"] = queryset
            kwargs["widget"] = ColorSelect
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Color)
class ColorAdmin(DuplicateAdminMixin):
    """A cor é uma só; o que muda por idioma é o nome que o cliente lê.

    Uma cor composta («Branco + Azul») cadastra-se aqui mesmo: é uma cor com
    componentes, na ordem em que se lê. Ela aparece em todo `<select>` de
    cor como qualquer outra — a variante, a paleta e o filtro não precisam
    saber que é composta.
    """

    #: Mesma decisão do material: `name` copiado (é a identidade `unique`),
    #: `slug` vazio (o `save()` o gera). O HEX acompanha — duas cores próximas
    #: partem do mesmo tom e é justamente isso que se quer ajustar. As
    #: componentes não vão junto: a cópia nasce simples, e uma composição
    #: idêntica seria recusada de todo modo.
    duplicate_exclude = ("slug",)
    duplicate_inlines = {ColorTranslation: ()}

    inlines = [ColorTranslationInline, ColorComponentInline]
    list_display = (
        "name", "swatch", "composition_display", "translations_display", "hex_code", "slug", "is_active",
    )
    list_filter = ("is_active", "is_composite")
    search_fields = ("name", "slug", "hex_code", "translations__name")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "hex_code", "is_active")

    class Media:
        css = {"all": ("admin/css/jdprint_admin.css",)}

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related(*color_prefetches(""))
        )

    def save_related(self, request, form, formsets, change):
        """Depois das componentes gravadas, o espelho `is_composite` é acertado."""
        super().save_related(request, form, formsets, change)
        form.instance.refresh_composite_flag()

    @admin.display(description="amostra")
    def swatch(self, obj):
        fundo = obj.swatch_background
        if not fundo:
            return "—"
        return format_html(
            '<span style="display:inline-block;width:22px;height:22px;border-radius:4px;'
            'border:1px solid #bbb;background:{}"></span>',
            fundo,
        )

    @admin.display(description="composição", ordering="is_composite")
    def composition_display(self, obj):
        if not obj.is_composite:
            return format_html('<span class="jd-muted">{}</span>', "simples")
        return " + ".join(componente.name for componente in obj.component_list)

    @admin.display(description="traduções")
    def translations_display(self, obj):
        return translations_column(obj)


# ---------------------------------------------------------------------------
# Inlines do produto
# ---------------------------------------------------------------------------


class ProductTranslationInlineFormSet(forms.BaseInlineFormSet):
    """Garante o conteúdo mínimo por idioma."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        languages = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            languages.append(form.cleaned_data.get("language"))

        if len(set(languages)) != len(languages):
            raise ValidationError("Há mais de uma tradução para o mesmo idioma.")

        product_is_active = self.instance.status == ProductStatus.ACTIVE
        if product_is_active and DEFAULT_LANGUAGE.value not in languages:
            raise ValidationError(
                "Um produto ativo precisa da tradução em português (nome do produto)."
            )


#: Os campos de conteúdo, num lugar só — usados pelo inline e pelo modal.
#: Com duas listas, um campo acrescentado numa delas apareceria na tela e
#: sumiria ao gravar pelo modal, sem ninguém perceber.
CONTENT_FIELDS = (
    "language", "name", "short_description", "description", "extra_information", "color_choice_label",
)


class ProductTranslationModalForm(forms.ModelForm):
    """O que o modal de conteúdo envia — os mesmos campos do inline."""

    class Meta:
        model = ProductTranslation
        fields = CONTENT_FIELDS


class ProductTranslationInline(admin.StackedInline):
    """O conteúdo traduzível, como tabela + modal.

    Antes, os cinco campos de cada idioma ficavam abertos na página: com
    quatro idiomas eram vinte campos empilhados antes de chegar às variantes.
    Agora é uma linha por idioma e um modal de cada vez — o mesmo padrão das
    variantes, com a mesma casca (`jd_modal.js`).
    """

    model = ProductTranslation
    formset = ProductTranslationInlineFormSet
    template = "admin/catalog/edit_inline/content_table.html"
    classes = ("collapse",)
    extra = 0
    min_num = 1
    validate_min = True
    fields = CONTENT_FIELDS
    verbose_name = "conteúdo por idioma"
    verbose_name_plural = "CONTEÚDO — nome e descrições por idioma"


#: Os campos da variante e como eles se agrupam em linhas no formulário.
#:
#: Uma lista só, usada pelo inline **e** pelo formulário do modal: com duas
#: listas, um campo acrescentado numa delas apareceria na tela e sumiria ao
#: gravar pelo modal — e o administrador não teria como perceber.
#:
#: A ordem segue a decisão de quem cadastra: identificação, eixos, **custo**,
#: depois preço/margem — o preço só faz sentido depois de saber o custo.
VARIANT_FIELD_ROWS = (
    ("sku", "sort_order", "is_active"),
    ("color", "size", "material"),
    ("filament_cost", "energy_cost"),
    ("pricing_mode", "sale_price", "profit_margin"),
    ("stock_quantity", "allow_backorder"),
    ("made_to_order", "production_lead_time_days"),
    ("weight_grams", "print_time"),
    ("width", "height", "depth", "dimension_unit"),
)

VARIANT_FIELDS = tuple(campo for linha in VARIANT_FIELD_ROWS for campo in linha)

#: Como o MODAL da variante agrupa os campos — a anatomia da tela, não do
#: formulário. `VARIANT_FIELD_ROWS` continua sendo a lista do formset; aqui é
#: só a ordem em que o administrador os vê: quem é a variante, o que a
#: distingue, o que ela custa e vale, quanto pesa e mede.
#:
#: `is_active` não está em grupo nenhum: vira o interruptor do cabeçalho.
#: A afirmação no fim garante que os dois lugares falam dos mesmos campos —
#: um campo acrescentado no formset e esquecido aqui sumiria do modal.
VARIANT_MODAL_GROUPS = (
    {
        "slug": "identificacao",
        "title": "Identificação",
        "hint": "",
        "fields": ("sku", "sort_order"),
    },
    {
        "slug": "opcoes",
        "title": "Opções da variante",
        "hint": (
            "Só o que distingue esta variante das outras do mesmo produto e "
            "muda oferta, preço, estoque ou SKU. Eixo que não se aplica fica em "
            "branco — a descrição das cores e a composição da peça são do produto."
        ),
        "fields": ("color", "size", "material"),
    },
    {
        "slug": "estoque",
        "title": "Estoque e produção",
        "hint": "",
        "fields": ("stock_quantity", "production_lead_time_days", "allow_backorder", "made_to_order"),
    },
    {
        "slug": "fisico",
        "title": "Peso, tempo e dimensões",
        "hint": "",
        "fields": ("weight_grams", "print_time", "width", "height", "depth", "dimension_unit"),
    },
    {
        "slug": "preco",
        "title": "Custos e preço",
        "hint": (
            "Escolha em «definir preço por» qual valor você digita; o outro é "
            "calculado ao salvar."
        ),
        "fields": ("filament_cost", "energy_cost", "pricing_mode", "sale_price", "profit_margin"),
    },
)

VARIANT_HEADER_FIELDS = ("is_active",)

#: Unidade ao lado do campo, no modal: quem digita 20 no peso vê que é «g».
VARIANT_FIELD_AFFIX = {
    "filament_cost": ("moeda", ""),
    "energy_cost": ("moeda", ""),
    "sale_price": ("moeda", ""),
    "profit_margin": ("", "%"),
    "weight_grams": ("", "g"),
    "production_lead_time_days": ("", "dias"),
}

_no_modal = {campo for grupo in VARIANT_MODAL_GROUPS for campo in grupo["fields"]} | set(VARIANT_HEADER_FIELDS)
assert _no_modal == set(VARIANT_FIELDS), sorted(_no_modal ^ set(VARIANT_FIELDS))


#: O prefixo dos campos dinâmicos das opções adicionais no formulário da
#: variante: ``opt_<id da opção>``. Um campo por opção do produto, criado em
#: `VariantSkuAutoMixin.__init__` — nunca um campo fixo por tipo de opção.
OPTION_FIELD_PREFIX = "opt_"


def option_field_name(option) -> str:
    return f"{OPTION_FIELD_PREFIX}{option.pk}"


class VariantSkuAutoMixin:
    """SKU da variante em branco = a próxima sequência do produto (V01, V02…).

    O campo continua sendo o SKU oficial e continua editável: a sugestão só
    entra quando ninguém digitou nada. Variantes que já existem nunca são
    renomeadas — a sugestão só preenche o que está vazio.

    ``reserved_skus`` é compartilhado pelo formset: duas variantes novas no
    mesmo cadastro recebem V01 e V02, e não duas vezes V01.
    """

    reserved_skus: set | None = None

    #: Campos que podem ficar em branco no cadastro e recebem o padrão do
    #: modelo (zero): custo e estoque se detalham depois.
    optional_with_default = ("filament_cost", "energy_cost", "stock_quantity")

    def __init__(self, *args, product_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        if "sku" in self.fields:
            self.fields["sku"].required = False
            self.fields["sku"].help_text = (
                "Em branco, o sistema sugere PRODUTO-V01, V02… Você pode alterar."
            )
        for nome in self.optional_with_default:
            if nome in self.fields:
                self.fields[nome].required = False
        # -- opções adicionais (etapa 3C) ----------------------------------
        # Um `<select>` por opção do produto, com os valores daquela opção e
        # «Não definido» como vazio. Só entram quando quem instancia o
        # formulário passa as opções (o formset da ficha e o modal): a tela
        # própria da variante não as conhece e, por isso, não as toca.
        self.product_options = list(product_options or ())
        escolhidos = {}
        if self.instance.pk:
            escolhidos = {link.option_id: link.value_id for link in self.instance.option_values.all()}
        for option in self.product_options:
            nome = option_field_name(option)
            # `TypedChoiceField` a partir dos valores JÁ carregados (o formset
            # faz o prefetch uma vez): um `ModelChoiceField` refaria a consulta
            # em cada linha do inline. Só os ids dos valores desta opção são
            # escolhas válidas — valor de outra opção é recusado aqui mesmo, e
            # `set_option_values` confere de novo ao gravar.
            campo = forms.TypedChoiceField(
                choices=[("", "Não definido")] + [(valor.pk, valor.name) for valor in option.values.all()],
                coerce=int,
                empty_value=None,
                required=False,
                label=option.name,
                initial=escolhidos.get(option.pk),
            )
            campo.widget.attrs["data-variant-option"] = nome
            self.fields[nome] = campo

    @property
    def option_fields(self) -> list:
        """Os campos dinâmicos das opções adicionais, na ordem das opções."""
        return [self[option_field_name(option)] for option in self.product_options]

    #: Etapa 3F — ``(option_map, value_map)`` de ``copy_product_options``: na
    #: duplicação, os `<select>` da tela foram montados com as opções do produto
    #: **de origem**; ao gravar, cada escolha é traduzida para a opção e o valor
    #: recém-criados no produto novo. Fora da duplicação fica ``None``.
    option_maps = None

    def option_choices(self) -> dict:
        """``{id da opção: id do valor ou None}`` — o que foi escolhido na tela."""
        escolhas = {
            option.pk: self.cleaned_data.get(option_field_name(option))
            for option in self.product_options
        }
        if self.option_maps:
            option_map, value_map = self.option_maps
            escolhas = {
                option_map[option_id].pk: (value_map[value_id].pk if value_id else None)
                for option_id, value_id in escolhas.items()
            }
        return escolhas

    def _variant_product(self):
        produto = self.cleaned_data.get("product") if "product" in self.fields else None
        if produto is None:
            produto = getattr(self.instance, "product", None) if self.instance.product_id or hasattr(self.instance, "product") else None
        return produto

    def clean(self):
        dados = super().clean()
        for nome in self.optional_with_default:
            if nome in self.fields and dados.get(nome) is None:
                dados[nome] = ProductVariant._meta.get_field(nome).get_default()
        if (dados.get("sku") or "").strip():
            return dados
        try:
            produto = self._variant_product()
        except ProductVariant.product.RelatedObjectDoesNotExist:
            produto = None
        sku_produto = getattr(produto, "sku", "") if produto is not None else ""
        if not sku_produto:
            return dados  # o modelo pede o SKU, com a mensagem de sempre
        reservados = self.reserved_skus if self.reserved_skus is not None else set()
        dados["sku"] = sku_rules.suggest_variant_sku(sku_produto, reserved=reservados)
        reservados.add(dados["sku"])
        return dados

    def _post_clean(self):
        # Antes de o modelo validar (`full_clean` em `_post_clean`): as escolhas
        # ainda não gravadas, para a combinação repetida ser recusada já aqui.
        if self.product_options:
            self.instance._pending_option_choices = self.option_choices()
        super()._post_clean()

    def save(self, commit=True):
        """Grava a variante e, por `set_option_values`, as escolhas — uma só API.

        Com ``commit=False`` (o formset do Django), as escolhas ficam para o
        ``save_m2m`` que o Django chama logo depois: o vínculo precisa da pk.
        """
        instance = super().save(commit=commit)
        if not self.product_options:
            return instance
        escolhas = self.option_choices()
        if commit:
            instance.set_option_values(escolhas)
        else:
            original = self.save_m2m

            def save_m2m():
                original()
                instance.set_option_values(escolhas)

            self.save_m2m = save_m2m
        return instance


class ProductVariantModalForm(VariantSkuAutoMixin, forms.ModelForm):
    """O formulário que o modal envia — os mesmos campos do inline.

    Existe separado do inline porque o inline é um formset (vem com `id`,
    `DELETE` e prefixo indexado) e aqui chega **uma** variante, com os nomes
    limpos. A validação é a do modelo: `ProductVariant.clean()` continua sendo
    quem decide o que é uma variante válida.
    """

    class Meta:
        model = ProductVariant
        fields = VARIANT_FIELDS


class ProductVariantInlineForm(VariantSkuAutoMixin, forms.ModelForm):
    """O formulário de cada linha do inline — com o SKU sugerido."""

    class Meta:
        model = ProductVariant
        fields = VARIANT_FIELDS


class ProductVariantAdminForm(VariantSkuAutoMixin, forms.ModelForm):
    """A tela própria da variante — com o SKU sugerido."""

    class Meta:
        model = ProductVariant
        fields = "__all__"


class ProductVariantInlineFormSet(forms.BaseInlineFormSet):
    """Um produto ativo precisa de pelo menos uma variante ativa.

    A checagem mora aqui e não no ``clean()`` do produto porque o admin grava
    o produto **antes** dos inlines: no cadastro, o modelo ainda não enxerga a
    variante que está sendo criada na mesma tela. O formset enxerga.
    """

    def __init__(self, *args, product_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Compartilhado pelas linhas: V01, V02… sem repetir dentro do cadastro.
        self.reserved_skus: set[str] = set()
        # As opções adicionais do produto, lidas UMA vez para todas as linhas
        # (e para o molde do «Adicionar variante»). Produto novo não tem.
        if product_options is not None:
            self.product_options = list(product_options)
        else:
            self.product_options = (
                list(self.instance.options.prefetch_related("values"))
                if getattr(self.instance, "pk", None)
                else []
            )

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["product_options"] = self.product_options
        return kwargs

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        form.reserved_skus = self.reserved_skus
        return form

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        ativas = 0
        for form in self.forms:
            dados = form.cleaned_data
            if not dados or dados.get("DELETE"):
                continue
            if dados.get("is_active", True):
                ativas += 1

        if self.instance.status == ProductStatus.ACTIVE and ativas == 0:
            raise ValidationError(
                "Um produto ativo precisa de pelo menos uma variante ativa — é a "
                "variante que tem preço, estoque, peso e prazo. Cadastre uma "
                "variante ou volte o status para rascunho."
            )


class ProductVariantInline(admin.StackedInline):
    """As unidades vendáveis do produto, como tabela.

    Cada linha da tabela é uma coisa que o cliente compra: tem SKU, preço,
    estoque, peso, prazo e dimensões próprios. Nada é herdado do produto — o
    produto guarda apenas o que vale para todas.

    A apresentação é um resumo em tabela; clicar numa linha abre o painel com
    todos os campos daquela variante. Por baixo continua sendo o formset padrão
    do Django: os campos do painel **são** os campos do inline, e gravar o
    produto grava as variantes na mesma transação. Sem endpoint próprio, sem
    gravação parcial, sem uma segunda fonte de verdade sobre o que foi salvo.

    Um cadastro com vinte variantes tinha vinte formulários abertos ao mesmo
    tempo; agora tem vinte linhas e um painel de cada vez.

    A ordem dos campos no painel segue a ordem de decisão de quem cadastra:
    identificação, eixos, **custo**, depois preço/margem — o preço só faz
    sentido depois de saber quanto a peça custa.
    """

    model = ProductVariant
    form = ProductVariantInlineForm
    formset = ProductVariantInlineFormSet
    template = "admin/catalog/edit_inline/variant_table.html"
    #: `collapse` torna a secao recolhivel; o template a abre expandida.
    classes = ("collapse",)
    extra = 0
    fields = VARIANT_FIELD_ROWS
    #: `autocomplete_fields` traria o select2 do admin, que não sobrevive a ser
    #: clonado para uma variante nova sem reinicialização. Um `<select>` comum
    #: clona sem drama — e a lista de cores e materiais de uma gráfica 3D cabe
    #: num select.
    ordering = ("sort_order", "id")
    verbose_name = "variante"
    verbose_name_plural = "VARIANTES — cada uma é uma unidade vendável (preço, estoque, peso, prazo)"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("color", "material")
            .prefetch_related(*color_prefetches(), "material__translations", *variant_option_prefetches())
        )


class ProductMediaInline(admin.TabularInline):
    """As fotos do produto — e, opcionalmente, a variante de cada uma.

    Vincular a foto a uma variante faz dela a imagem principal quando o cliente
    escolhe aquela opção. Em branco, é foto geral: continua na galeria e vale
    para todas as variantes. Nenhum arquivo é duplicado — é a mesma linha.
    """

    model = ProductMedia
    template = "admin/catalog/edit_inline/media_tabular.html"
    #: `jd-cards` + `jd-media-grid`: cada linha da tabela vira um card com a
    #: miniatura em cima (jdprint_admin.css), em qualquer largura.
    classes = ("collapse", "jd-cards", "jd-media-grid")
    extra = 1
    fields = ("preview", "file", "media_type", "variant", "alt_text", "sort_order", "is_primary")
    readonly_fields = ("preview",)
    verbose_name = "foto"
    verbose_name_plural = "FOTOS — fotos, vídeos e GIFs"

    def get_formset(self, request, obj=None, **kwargs):
        """Guarda o produto para limitar a lista de variantes a ele.

        Sem isto, o `<select>` traria as variantes de TODOS os produtos, e o
        administrador poderia vincular a foto de um copo à variante de um vaso.
        O `clean()` do modelo recusaria, mas depois de o erro já ter sido
        cometido — melhor não oferecer.
        """
        self._parent_product = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name != "variant":
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        produto = getattr(self, "_parent_product", None)
        kwargs["queryset"] = (
            ProductVariant.objects.filter(product=produto)
            .select_related("color", "material")
            # O rótulo de cada `<option>` é `str(variante)`, que lê as opções
            # adicionais (etapa 3C): sem o prefetch, uma consulta por variante
            # em cada linha de foto.
            .prefetch_related(*variant_option_prefetches())
            .order_by("sort_order", "id")
            if produto is not None
            # Produto novo ainda não tem variante gravada: a lista fica vazia,
            # e o vínculo é feito depois de salvar.
            else ProductVariant.objects.none()
        )

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Tira do `<select>` de variante os atalhos de popup do Admin.

        Ao lado de toda FK o Django desenha quatro links (+ / lápis / lixeira /
        olho) que abrem `ProductVariant` num popup — e por ali dá para alcançar
        a variante de **outro** produto, justamente o que a associação não pode
        fazer. Criar variante a partir da linha de uma foto também não faz
        sentido: preço, estoque, peso e prazo são da seção VARIANTES.

        A desembrulhada é aqui, e não em `formfield_for_foreignkey`, porque é
        `formfield_for_dbfield` quem põe o `RelatedFieldWidgetWrapper` — depois
        de o outro método já ter devolvido o campo.

        O `clean()` do modelo continua sendo a rede: o widget é a porta
        principal, não a única.
        """
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if (
            db_field.name == "variant"
            and field is not None
            and isinstance(field.widget, RelatedFieldWidgetWrapper)
        ):
            field.widget = field.widget.widget
        return field

    @admin.display(description="prévia")
    def preview(self, obj):
        if not obj.pk or not obj.file:
            return mark_safe('<span class="jd-media-empty" aria-hidden="true">+</span>')
        if obj.media_type in {MediaType.IMAGE, MediaType.GIF}:
            selo = '<span class="jd-badge jd-badge-ok jd-media-primary">principal</span>' if obj.is_primary else ""
            return format_html('<img src="{}" class="jd-media-thumb" alt="" />{}', obj.file.url, mark_safe(selo))
        return format_html('<a href="{}" target="_blank">abrir vídeo</a>', obj.file.url)


class ProductColorInlineFormSet(forms.BaseInlineFormSet):
    """A paleta obedece ao modo de cores do produto.

    «Uma cor» com duas linhas é contradição; «Multicolorido» sem nenhuma é uma
    lista vazia que o card não tem como desenhar. A mesma cor duas vezes o
    banco já recusa — aqui a mensagem chega antes, legível.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        vivas = [
            form.cleaned_data["color"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("color")
        ]
        if len({cor.pk for cor in vivas}) != len(vivas):
            raise ValidationError("A mesma cor aparece duas vezes na paleta.")
        modo = getattr(self.instance, "color_mode", ColorMode.NONE)
        if modo == ColorMode.CUSTOM:
            self._refuse_discounts_that_zero_the_price()
        # «Cores à escolha do cliente» aceita paleta vazia de propósito: sem
        # cor cadastrada não há o que escolher, e a compra segue como sempre
        # (ver `Product.customer_color_rows`). As regras abaixo são as dos
        # modos descritivos, e não mudaram.
        if modo == ColorMode.SINGLE and len(vivas) > 1:
            raise ValidationError(
                "No modo «Uma cor» cadastre uma cor só — para várias, use «Multicolorido»."
            )
        if modo in (ColorMode.SINGLE, ColorMode.MULTI) and not vivas:
            raise ValidationError(
                "Cadastre ao menos uma cor na paleta — ou mude o modo de cores para "
                "«Não se aplica», «Cores à escolha» ou «Opção comercial»."
            )

    def _refuse_discounts_that_zero_the_price(self):
        """Desconto é permitido; preço final zero ou negativo, não.

        Confere cada adicional negativo contra a variante ativa mais barata
        do produto — a que o desconto atinge primeiro. Produto ainda sem
        variante com preço não tem contra o que conferir; o carrinho recusa
        a compra de todo modo (`Cart.add`).
        """
        if not self.instance.pk:
            return
        mais_barata = (
            self.instance.variants.filter(is_active=True, sale_price__isnull=False)
            .order_by("sale_price", "id")
            .first()
        )
        if mais_barata is None:
            return
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            adicional = form.cleaned_data.get("price_delta") or Decimal("0.00")
            if adicional < 0 and mais_barata.sale_price + adicional <= 0:
                form.add_error(
                    "price_delta",
                    "Este desconto deixaria «%(variante)s» (€ %(preco)s) com preço zero ou "
                    "negativo. Reduza o desconto."
                    % {"variante": mais_barata.display_label, "preco": mais_barata.sale_price},
                )


class ColorSelect(forms.Select):
    """Um `<select>` de cores em que cada opção leva o hex (`data-hex`).

    É o que permite à ficha desenhar a bolinha da cor ao lado do campo
    (`product_form_admin.js`) sem outra consulta. Só apresentação: o valor
    enviado continua sendo o `id` da cor.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        cor = getattr(value, "instance", None)
        if cor is None:
            return option
        # A composta pinta com todas as componentes (`swatch_background`) —
        # mesmo sem hex próprio; a simples leva o próprio hex, como sempre.
        fundo = cor.swatch_background
        if fundo:
            option["attrs"]["data-swatch"] = fundo
        if getattr(cor, "hex_code", ""):
            option["attrs"]["data-hex"] = cor.hex_code
        return option


class ProductColorInlineForm(forms.ModelForm):
    """Uma linha da paleta. Adicional em branco vale zero.

    O campo é opcional no formulário (a coluna some nos modos descritivos),
    mas a coluna do banco não aceita nulo: quem não digitou nada não quer
    adicional nenhum.
    """

    class Meta:
        model = ProductColor
        fields = ("color", "price_delta", "sort_order")

    def clean_price_delta(self):
        valor = self.cleaned_data.get("price_delta")
        return Decimal("0.00") if valor is None else valor


class ProductColorInline(admin.TabularInline):
    """PALETA DE CORES: a descrição visual do produto — e, em «Cores à escolha
    do cliente», as cores que o cliente escolhe na compra, com o adicional de
    cada uma. Não cria variantes em modo nenhum.
    """

    model = ProductColor
    form = ProductColorInlineForm
    formset = ProductColorInlineFormSet
    template = "admin/catalog/edit_inline/media_tabular.html"
    #: Linhas compactas (cor, adicional, ordem, remover) no desktop; um card
    #: por linha nas telas estreitas (`jd-cards`, jdprint_forms.css). A coluna
    #: do adicional só aparece em «Cores à escolha» (`product_colors_admin.js`):
    #: nos outros modos ela não teria efeito nenhum.
    classes = ("collapse", "jd-cards", "jd-compact-rows")
    extra = 0
    fields = ("color", "price_delta", "sort_order")
    verbose_name = "cor"
    verbose_name_plural = "PALETA DE CORES — as cores do produto (não cria variantes)"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "color":
            # As componentes vêm junto: a bolinha da composta (`data-swatch`)
            # não pode custar consultas por cor a cada linha da paleta.
            kwargs["queryset"] = Color.objects.filter(is_active=True).prefetch_related(
                *color_prefetches("")
            )
            kwargs["widget"] = ColorSelect
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ProductMaterialCompositionInlineFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        vivos = [
            form.cleaned_data["material"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE") and form.cleaned_data.get("material")
        ]
        if len({m.pk for m in vivos}) != len(vivos):
            raise ValidationError("O mesmo material aparece duas vezes na composição.")


class ProductMaterialCompositionInline(admin.TabularInline):
    """MATERIAIS: do que a peça é feita. Não cria variantes."""

    model = ProductMaterialComposition
    formset = ProductMaterialCompositionInlineFormSet
    template = "admin/catalog/edit_inline/media_tabular.html"
    classes = ("collapse", "jd-cards", "jd-compact-rows")
    extra = 0
    fields = ("material", "percentage", "sort_order")
    verbose_name = "material"
    verbose_name_plural = "MATERIAIS — composição de fabricação (não cria variantes)"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "material":
            kwargs["queryset"] = Material.objects.filter(is_active=True).prefetch_related("translations")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------------------------------------------------------------------------
# Produto
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Opções adicionais (etapa 3C): os formulários dos modais
# ---------------------------------------------------------------------------


def option_translation_languages() -> list[str]:
    """Os idiomas que a loja oferece além do português: FR, NL, EN.

    Vêm dos idiomas ativos da loja (`SiteLanguage`), não da lista inteira de
    `Language` — nove caixas por opção seriam seis a mais do que a loja usa.
    Sem idioma ativo cadastrado, os três da loja. O português é o nome interno
    da opção: é o fallback.
    """
    idiomas = []
    for code in SiteLanguage.objects.active().values_list("code", flat=True):
        normalizado = normalize_language(code)
        if normalizado != DEFAULT_LANGUAGE.value and normalizado not in idiomas:
            idiomas.append(normalizado)
    return idiomas or ["fr", "nl", "en"]


def _erros_por_campo(erros) -> dict:
    """`form.errors` ou `ValidationError` → `{campo: [mensagens]}` para o modal."""
    if isinstance(erros, ValidationError):
        if hasattr(erros, "error_dict"):
            return {campo: [str(m) for m in lista] for campo, lista in erros.message_dict.items()}
        return {"__all__": [str(m) for m in erros.messages]}
    return {campo: [str(m) for m in lista] for campo, lista in erros.items()}


class TranslatedNameModalForm(forms.ModelForm):
    """Nome interno (PT), ordem e uma caixa por idioma (FR, NL, EN).

    O nome interno é o texto em português — não existe uma linha PT na tabela
    de tradução, ao contrário da cor: `display_name` cai nele quando falta o
    idioma. As outras caixas gravam, atualizam ou apagam a linha do idioma.
    """

    translation_model = None  # a tabela de tradução (master, language, name)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Nome (português)"
        existentes = self.instance.translations_by_language() if self.instance.pk else {}
        for code in option_translation_languages():
            self.fields[f"name_{code}"] = forms.CharField(
                label=Language(code).label,
                max_length=60,
                required=False,
                initial=existentes[code].name if code in existentes else "",
            )

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def _post_clean(self):
        # Já recusado pelo formulário (nome repetido): não pedir ao modelo para
        # validar uma instância sem o nome, que o Django tirou de `cleaned_data`.
        if self.errors:
            return
        super()._post_clean()

    def save(self, commit=True):
        obj = super().save(commit=commit)
        for code in option_translation_languages():
            texto = (self.cleaned_data.get(f"name_{code}") or "").strip()
            linha = self.translation_model.objects.filter(master=obj, language=code).first()
            if texto:
                if linha is None:
                    self.translation_model.objects.create(master=obj, language=code, name=texto)
                elif linha.name != texto:
                    linha.name = texto
                    linha.save(update_fields=["name"])
            elif linha is not None:
                linha.delete()
        obj.refresh_translations()
        return obj


class ProductOptionModalForm(TranslatedNameModalForm):
    translation_model = ProductOptionTranslation

    class Meta:
        model = ProductOption
        fields = ("name", "sort_order")

    def __init__(self, *args, product, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.product = product

    def clean(self):
        dados = super().clean()
        nome = dados.get("name")
        if nome and ProductOption.objects.filter(product=self.instance.product, name=nome).exclude(pk=self.instance.pk).exists():
            self.add_error("name", "Já existe uma opção com este nome neste produto.")
        return dados


class ProductOptionValueModalForm(TranslatedNameModalForm):
    translation_model = ProductOptionValueTranslation

    class Meta:
        model = ProductOptionValue
        fields = ("name", "sort_order")

    def __init__(self, *args, option, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.option = option

    def clean(self):
        dados = super().clean()
        nome = dados.get("name")
        if nome and ProductOptionValue.objects.filter(option=self.instance.option, name=nome).exclude(pk=self.instance.pk).exists():
            self.add_error("name", "Já existe um valor com este nome nesta opção.")
        return dados


#: A seção das opções adicionais em `SECTION_ORDER`: não é fieldset nem inline.
OPTIONS_SECTION = "OPÇÕES ADICIONAIS"


#: A ordem das seções na tela do produto, de cima para baixo. Fieldsets entram
#: pelo nome; inlines, pelo modelo. É a única lista que precisa mudar quando a
#: ordem mudar — nem o template nem os `fieldsets` sabem dela.
#:
#: AUDITORIA não está aqui de propósito: ela é a última da página e é desenhada
#: depois dos inlines, em `after_related_objects`.
SECTION_ORDER = (
    "INFORMAÇÕES BÁSICAS",
    ProductTranslation,  # CONTEÚDO
    ProductMedia,  # FOTOS
    "CORES",  # o modo de cores
    ProductColor,  # PALETA DE CORES
    ProductMaterialComposition,  # MATERIAIS
    "PERSONALIZAÇÃO",
    OPTIONS_SECTION,  # OPÇÕES ADICIONAIS (etapa 3C): nem fieldset nem inline
    ProductVariant,  # VARIANTES
    "OUTRAS INFORMAÇÕES",
)

#: O que cada seção diz de si na ficha: a âncora e o rótulo curto da
#: navegação rápida, o subtítulo do cabeçalho e uma explicação para o corpo.
#: `attached` marca a seção que se apoia na anterior — a PALETA DE CORES é
#: parte de CORES: não ganha número nem entrada no índice, mas continua
#: recolhível e com o próprio título, como as outras.
#:
#: Os títulos NÃO estão aqui: vêm do nome do fieldset ou do
#: `verbose_name_plural` do inline, como sempre. Só o que é da ficha mora
#: nesta tabela.
SECTION_META = {
    "INFORMAÇÕES BÁSICAS": {
        "slug": "basico",
        "nav": "Básico",
        "subtitle": "SKU, status, categoria, marca e slug",
    },
    ProductTranslation: {
        "slug": "conteudo",
        "nav": "Conteúdo",
        "help": (
            "Uma linha por idioma. O português é o padrão: é dele que a loja "
            "tira o nome quando falta tradução."
        ),
    },
    ProductMedia: {
        "slug": "fotos",
        "nav": "Fotos",
        "help": (
            "A imagem marcada como <b>principal</b> é a capa do produto. "
            "Vincule a uma variante apenas quando a mídia mostrar aquela opção "
            "específica."
        ),
    },
    "CORES": {
        "slug": "cores",
        "nav": "Cores",
        "subtitle": "modo e paleta — descreve a peça, não cria variantes",
    },
    ProductColor: {"slug": "paleta", "attached": True},
    ProductMaterialComposition: {
        "slug": "materiais",
        "nav": "Materiais",
        "help": (
            "Do que a peça é feita — a composição física, com o percentual "
            "quando fizer sentido. Não confundir com o <b>material comercial</b> "
            "de uma VARIANTE: lá o material é uma opção que o cliente escolhe e "
            "que muda oferta, preço ou SKU."
        ),
    },
    "PERSONALIZAÇÃO": {
        "slug": "personalizacao",
        "nav": "Personalização",
        "subtitle": "o que o cliente fornece antes de comprar",
    },
    "OPÇÕES ADICIONAIS": {
        "slug": "opcoes",
        "nav": "Opções",
        "subtitle": "eixos a mais da variante — Instalação, Acabamento, Modelo…",
        "help": (
            "Opções que o cliente escolhe além de cor, tamanho e material. Cada "
            "opção tem os seus valores («Instalação: Mesa / Parede»); cada VARIANTE "
            "escolhe um valor por opção, na seção seguinte. Isto não cria variantes."
        ),
    },
    ProductVariant: {"slug": "variantes", "nav": "Variantes"},
    "OUTRAS INFORMAÇÕES": {
        "slug": "outras",
        "nav": "Outras",
        "subtitle": "moeda e destaque na Home",
    },
}

#: A AUDITORIA não passa por `SECTION_ORDER` (é desenhada depois dos inlines),
#: mas na ficha é a última seção numerada, como as outras.
AUDIT_META = {"slug": "auditoria", "nav": "Auditoria", "subtitle": "somente leitura"}


def split_title(nome: str) -> tuple[str, str]:
    """«CONTEÚDO — nome e descrições por idioma» → («CONTEÚDO», «nome e…»).

    O título das seções sempre foi escrito assim, com o travessão separando
    o nome do resumo. A ficha só passa a desenhar as duas partes em pesos
    diferentes.
    """
    titulo, sep, resto = nome.partition(" — ")
    return (titulo.strip(), resto.strip()) if sep else (nome.strip(), "")


def activation_problems(product) -> list[str]:
    """O que impede um produto de ser ativado — em frases para a tela.

    As mesmas três regras que já existem: categoria e nome em português
    (`Product._validate_activation`) e pelo menos uma variante ativa
    (`ProductVariantInlineFormSet`). Aqui elas valem para o botão da lista.
    """
    problemas = []
    if product.category_id is None:
        problemas.append("Um produto ativo precisa de categoria.")
    if not product.has_default_translation():
        problemas.append("Um produto ativo precisa do nome em português.")
    if not product.active_variants():
        problemas.append(
            "Um produto ativo precisa de pelo menos uma variante ativa — é a "
            "variante que tem preço, estoque, peso e prazo."
        )
    return problemas


def money(value, symbol: str) -> str:
    """€29,90 — o formato da lista."""
    return f"{symbol}{Decimal(value):.2f}".replace(".", ",")


class QuickProductForm(forms.Form):
    """O cadastro rápido: nome, categoria, SKU (sugerido) e status.

    Não é um `ModelForm` de propósito: o nome mora em `ProductTranslation`, o
    SKU pode vir em branco (o sistema sugere) e a primeira variante é
    opcional. `save()` grava tudo na ordem certa e numa transação só.

    ## Com um produto-modelo (duplicar)

    Este mesmo formulário é a tela de «Duplicar» (ver
    `ProductAdmin.duplicate_url`). Com `template=<produto>` ele abre
    pré-preenchido com o nome, a categoria, o status, o SKU e a marca do
    modelo, e quem cadastra decide ali o que o produto novo é: manter o nome
    e criar uma variação, ou trocar tudo e aproveitar só a estrutura. Ao
    gravar, `apply_product_template` traz o resto do modelo.

    A primeira variante não é perguntada nesse caso: as variantes vêm do
    modelo, com preço e tudo.
    """

    name = forms.CharField(
        label="Nome",
        max_length=200,
        help_text="Em português. Os outros idiomas ficam para a ficha.",
        widget=forms.TextInput(attrs={"autofocus": True, "class": "vTextField"}),
    )
    category = forms.ModelChoiceField(
        label="Categoria",
        queryset=Category.objects.none(),
        required=False,
        help_text="Opcional no rascunho; obrigatória para ativar.",
    )
    sku = forms.CharField(
        label="SKU",
        max_length=64,
        required=False,
        help_text="Em branco, é gerado a partir da categoria e do nome. Você pode alterar.",
        widget=forms.TextInput(attrs={"class": "vTextField", "autocomplete": "off"}),
    )
    status = forms.ChoiceField(
        label="Status",
        choices=ProductStatus.choices,
        initial=ProductStatus.DRAFT,
        help_text="Rascunho não aparece na loja. Para ativar é preciso preço (variante).",
    )
    brand = forms.ModelChoiceField(
        label="Marca", queryset=Brand.objects.filter(is_active=True), required=False
    )
    sale_price = forms.DecimalField(
        label="Preço de venda",
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=10,
        help_text="Cria a primeira variante com este preço.",
    )
    stock_quantity = forms.IntegerField(
        label="Estoque", required=False, min_value=0, help_text="Da primeira variante."
    )

    def __init__(self, *args, template=None, **kwargs):
        #: O produto usado como modelo, ou ``None`` no cadastro comum.
        self.template = template
        #: Ficará verdadeiro quando o SKU enviado for o que a tela sugeriu —
        #: e, portanto, puder ceder a vez numa colisão (ver `clean_sku`).
        self.sku_is_auto = True
        super().__init__(*args, **kwargs)
        categorias = Category.objects.prefetch_related("translations").order_by("sort_order", "slug")
        self.fields["category"].queryset = categorias
        self.fields["category"].label_from_instance = lambda c: c.full_path()
        # A MESMA busca da ficha completa (`ProductAdmin.autocomplete_fields`):
        # o widget do próprio Admin, o endpoint do próprio Admin e a busca de
        # `CategoryAdmin.search_fields`. Com dezenas de categorias, o `<select>`
        # vira uma lista longa demais para achar «Dinossauros» — aqui se
        # digita o nome. Cada resultado vem com o caminho inteiro
        # («Impressões 3D › Brinquedos › Dinossauros»), que é o que desfaz a
        # ambiguidade entre nomes parecidos em ramos diferentes.
        self.fields["category"].widget = AutocompleteSelect(
            Product._meta.get_field("category"), admin.site
        )
        self.fields["category"].widget.choices = self.fields["category"].choices
        if template is not None:
            # As variantes vêm do modelo, com preço, peso e prazo: perguntar
            # de novo criaria uma variante a mais, sem eixos.
            self.fields.pop("sale_price", None)
            self.fields.pop("stock_quantity", None)

    @staticmethod
    def initial_from(produto) -> dict:
        """Os cinco campos, a partir de um produto-modelo.

        O SKU é o que a regra de sempre sugeriria para este nome e esta
        categoria (`sku_rules.suggest_product_sku`) — o mesmo que a tela
        passa a sugerir se o nome mudar. Nada de «SKU do modelo + 1»: o SKU
        é do produto novo.
        """
        nome = produto.name_in(DEFAULT_LANGUAGE.value) or ""
        return {
            "name": nome,
            "category": produto.category_id,
            "status": produto.status,
            "brand": produto.brand_id,
            "sku": sku_rules.suggest_product_sku(produto.category, nome),
        }

    def clean_sku(self):
        """O SKU digitado; em branco, o sistema sugere.

        Com um modelo, o SKU que a tela ofereceu é **automático**: se ele
        ainda é o que a regra sugere, continua sendo sugestão (e cede a vez a
        quem gravar primeiro, em `save`); se alguém já o levou entre a tela e
        o envio, a sugestão seguinte entra no lugar. Um SKU digitado fora da
        família daquele nome é da pessoa — e, colidindo, é erro para ela ver.
        """
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        self.sku_is_auto = not sku
        if not sku:
            return sku
        ocupado = Product.objects.filter(sku=sku).exists()
        # O SKU do próprio modelo nunca é uma sugestão velha — é a identidade
        # de outro produto, e digitá-lo de volta é erro para a pessoa ver.
        do_modelo = self.template is not None and sku == (self.template.sku or "").strip().upper()
        if self.template is not None and not do_modelo:
            categoria = self.cleaned_data.get("category")
            nome = self.cleaned_data.get("name", "")
            if sku == sku_rules.suggest_product_sku(categoria, nome):
                self.sku_is_auto = True
            elif ocupado and sku_rules.matches_suggested_base(sku, categoria, nome):
                # A sugestão que a tela ofereceu foi levada no meio do
                # caminho: a seguinte entra no lugar.
                self.sku_is_auto = True
                return self.suggested_sku()
        if ocupado:
            raise ValidationError("Já existe um produto com este SKU.")
        return sku

    def clean(self):
        dados = super().clean()
        do_modelo = self.template_has_priced_variant()
        if dados.get("status") == ProductStatus.ACTIVE:
            if dados.get("category") is None:
                self.add_error("category", "Um produto ativo precisa de categoria.")
            if self.template is None and dados.get("sale_price") is None:
                self.add_error(
                    "status",
                    "Um produto ativo precisa de uma variante com preço: informe o "
                    "preço ou crie como rascunho e ative depois.",
                )
            if self.template is not None and not do_modelo:
                self.add_error(
                    "status",
                    f"«{self.template.sku}» não tem variante ativa com preço para copiar: "
                    "crie como rascunho e ative depois de cadastrar o preço.",
                )
        if dados.get("stock_quantity") is not None and dados.get("sale_price") is None:
            self.add_error("sale_price", "Informe o preço para criar a primeira variante.")
        return dados

    def template_has_priced_variant(self) -> bool:
        """O modelo tem uma variante ativa com preço — a que fará o produto vender?"""
        if self.template is None:
            return False
        return self.template.variants.filter(is_active=True, sale_price__isnull=False).exists()

    def suggested_sku(self, reserved=()) -> str:
        return sku_rules.suggest_product_sku(
            self.cleaned_data.get("category"), self.cleaned_data.get("name", ""), reserved
        )

    def save(self, user):
        """Produto + nome em português + slug (+ a primeira variante).

        O SKU digitado é respeitado; em branco — ou sendo o que a tela
        sugeriu, na duplicação —, a sugestão entra e, se o banco recusar
        (outro cadastro gravou o mesmo número no meio do caminho), a sequência
        seguinte é tentada — ver `sku_rules.com_sku_livre`.

        Com um produto-modelo, `apply_product_template` roda **dentro** da
        mesma transação: ou o produto novo nasce completo, ou não nasce.
        """
        dados = self.cleaned_data
        manual = "" if self.sku_is_auto else (dados.get("sku") or "")
        #: O resumo do que o modelo trouxe, para a mensagem de quem cadastra.
        self.template_report = None

        def criar(sku):
            with transaction.atomic():
                produto = Product(
                    sku=sku,
                    category=dados.get("category"),
                    brand=dados.get("brand"),
                    status=dados["status"],
                    created_by=user,
                    updated_by=user,
                )
                # A unicidade do SKU é do banco: se ele recusar, a sequência
                # seguinte é tentada (`com_sku_livre`). Validar aqui de novo
                # transformaria a colisão em erro em vez de em nova tentativa.
                produto.full_clean(validate_unique=False)
                produto.save()
                ProductTranslation.objects.create(
                    master=produto, language=DEFAULT_LANGUAGE.value, name=dados["name"].strip()
                )
                produto.refresh_translations()
                # O slug vem do nome, como no cadastro completo
                # (`TranslatedSlugAdminMixin`); nunca do SKU.
                produto.slug = unique_slugify(produto, dados["name"].strip())
                produto.save(update_fields=["slug"])
                if self.template is not None:
                    self.template_report = apply_product_template(self.template, produto)
                elif dados.get("sale_price") is not None:
                    variante = ProductVariant(
                        product=produto,
                        sku=sku_rules.suggest_variant_sku(produto.sku),
                        pricing_mode=PricingMode.PRICE,
                        sale_price=dados["sale_price"],
                        stock_quantity=dados.get("stock_quantity") or 0,
                        is_active=True,
                    )
                    variante.full_clean()
                    variante.save()
                    produto.refresh_from_db()
                return produto

        if manual:
            return criar(manual)
        return sku_rules.com_sku_livre(self.suggested_sku, criar)


class ProductAdminForm(forms.ModelForm):
    """O formulário completo — com o SKU sugerido para produto NOVO.

    A mesma regra do cadastro rápido (`sku_rules.suggest_product_sku`): em
    branco, o SKU nasce da categoria e do nome em português — que aqui vem da
    linha em português do inline CONTEÚDO, enviada no mesmo POST. Num produto
    que já existe o campo continua obrigatório e nunca é reescrito: mudar o
    nome ou a categoria depois não mexe no SKU.
    """

    class Meta:
        model = Product
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sku_auto = False
        # Omitido no POST (formulários que não o conhecem), o modo de cores
        # fica como está no registro — o padrão «não se aplica» num produto novo.
        if "color_mode" in self.fields:
            self.fields["color_mode"].required = False
        # A pergunta da seção PERSONALIZAÇÃO, no lugar do nome do campo. Só o
        # rótulo do formulário: o modelo continua «tipo de personalização».
        if "personalization_type" in self.fields:
            self.fields["personalization_type"].label = "Este produto pode ser personalizado?"
        if "sku" in self.fields and not self.instance.pk:
            self.fields["sku"].required = False
            self.fields["sku"].help_text = (
                "Em branco, é gerado a partir da categoria e do nome em português "
                "(ex.: REL-LEAO-001). Você pode alterar."
            )

    def portuguese_name(self) -> str:
        """O nome em português como está no inline CONTEÚDO deste POST."""
        prefixo = "translations"
        try:
            total = int(self.data.get(f"{prefixo}-TOTAL_FORMS", 0))
        except (TypeError, ValueError):
            total = 0
        for indice in range(total):
            chave = f"{prefixo}-{indice}-"
            if self.data.get(chave + "DELETE"):
                continue
            if self.data.get(chave + "language") == DEFAULT_LANGUAGE.value:
                return (self.data.get(chave + "name") or "").strip()
        return ""

    def clean(self):
        dados = super().clean()
        if self.instance.pk or (dados.get("sku") or "").strip():
            return dados
        nome = self.portuguese_name()
        if not nome:
            self.add_error(
                "sku",
                "Informe o SKU — ou o nome em português em CONTEÚDO, para ele ser gerado.",
            )
            return dados
        dados["sku"] = sku_rules.suggest_product_sku(dados.get("category"), nome)
        self.sku_auto = True
        return dados


class ProdutoChangeList(ChangeList):
    """O `ChangeList` da lista de produtos: acrescenta "itens por página".

    `get_results` é o gancho certo para isso. Quando ele roda, `__init__` já
    gravou `self.list_per_page`, e é dessa atribuição que sai o paginador —
    trocar o valor na `ModelAdmin` não serviria: a instância dela é
    compartilhada por todas as requisições do processo.
    """

    #: As três opções da tela. Um número fora daqui é ignorado: `?por_pagina=`
    #: não é um jeito de pedir dez mil linhas ao banco.
    POR_PAGINA = (12, 24, 48)
    POR_PAGINA_PADRAO = 24
    PARAMETRO_POR_PAGINA = "por_pagina"

    def escolha_por_pagina(self) -> int:
        for bruto in reversed(self.filter_params.get(self.PARAMETRO_POR_PAGINA, [])):
            try:
                valor = int(bruto)
            except (TypeError, ValueError):
                continue
            if valor in self.POR_PAGINA:
                return valor
        return self.POR_PAGINA_PADRAO

    def get_filters_params(self, params=None):
        """`por_pagina` não é filtro.

        Sem esta remoção ele sobraria em `remaining_lookup_params` e o
        `ChangeList` tentaria `filter(por_pagina=24)` — que é um `FieldError`
        virando "Please correct the error below" na tela. Sai daqui, mas
        continua em `filter_params`: é por isso que ele sobrevive à paginação,
        aos chips e ao "Limpar tudo".
        """
        limpos = super().get_filters_params(params)
        limpos.pop(self.PARAMETRO_POR_PAGINA, None)
        return limpos

    def get_results(self, request):
        self.list_per_page = self.escolha_por_pagina()
        super().get_results(request)
        # O caminho completo da categoria, carimbado nas linhas desta página a
        # partir da árvore que a requisição já carregou. A coluna não pode
        # buscá-lo sozinha: ela recebe só o objeto, e `str(categoria)` sobe
        # pelos pais com uma consulta por ancestral.
        arvore = arvore_de_categorias(request)
        for produto in self.result_list:
            produto.jd_caminho = caminho_de_categoria(arvore, produto.category_id)


#: As pílulas de visão: atalhos para combinações de filtro que se usa todo
#: dia. Não são um mecanismo à parte — cada uma só escreve na URL os mesmos
#: parâmetros que o painel escreveria, e por isso aparecem como chip, saem no
#: "Limpar tudo" e combinam com o resto.
VISOES_DA_LISTA = (
    ("todos", "Todos", {}),
    ("rascunhos", "Rascunhos", {"status": [ProductStatus.DRAFT]}),
    ("estoque", "Estoque baixo", {"estoque": ["baixo"]}),
    ("pendentes", "Sem configuração", {"variantes": ["sem"]}),
    ("destaques", "Destaques", {"destaque": ["sim"]}),
)


@admin.register(Product)
class ProductAdmin(DuplicateAdminMixin, TranslatedSlugAdminMixin, AuditUserAdminMixin):
    form = ProductAdminForm

    #: Quem manda na ordem da tela é `SECTION_ORDER`, não esta lista — ela só
    #: diz quais inlines existem. Mesmo assim vão na ordem final, para quem
    #: ler o arquivo não precisar cruzar os dois lugares.
    inlines = [
        ProductTranslationInline,
        ProductVariantInline,
        ProductMediaInline,
        ProductColorInline,
        ProductMaterialCompositionInline,
    ]
    save_on_top = True

    #: -- Duplicar ---------------------------------------------------------
    #:
    #: Duplicar um produto é **criar um produto novo usando outro como
    #: modelo**, e não copiar um registro campo a campo. Por isso o botão leva
    #: ao mesmo cadastro rápido de sempre (`quick_add_view`), pré-preenchido
    #: com nome, categoria, status, SKU e marca do modelo: quem cadastra
    #: decide ali se aquilo é uma variação do mesmo produto ou uma peça
    #: diferente, e o SKU acompanha o nome enquanto for automático.
    #:
    #: O resto — conteúdo, variantes, opções adicionais, paleta e composição —
    #: entra depois que o produto novo existe, por `apply_product_template`.
    #: Fotos, SKU (do produto e das variantes) e slug nunca acompanham.
    #:
    #: `duplicate_inlines` fica vazio de propósito: a ficha completa não é a
    #: tela de duplicação (ver `get_changeform_initial_data`).
    duplicate_exclude = ("slug",)

    #: A tabela operacional: o que identifica, o que vende e o que fazer.
    list_display = (
        "id",
        "sku",
        "display_name",
        "categoria",
        "status_badge",
        "price_display",
        "stock_display",
        "variant_count",
        "acoes",
    )
    list_display_links = ("sku", "display_name")
    #: O painel da lista (ver `admin_filters.py`). São filtros do Admin de
    #: verdade: quem os combina, preserva na querystring e leva para a próxima
    #: página é o `ChangeList`, não código nosso.
    list_filter = FILTROS_DE_PRODUTO
    #: `get_search_results` faz o trabalho de verdade; esta lista fica porque
    #: `ChangeList` só monta o campo de busca quando ela existe.
    search_fields = (
        "sku",
        "slug",
        "translations__name",
        "translations__short_description",
        "variants__sku",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = ProdutoChangeList.POR_PAGINA_PADRAO
    autocomplete_fields = ("category", "brand")
    #: `DuplicateAdminMixin.actions` entra explicitamente porque o Django monta
    #: a lista a partir de `self.actions` e só dela: declarar ações aqui
    #: **substitui** as da mixin em vez de somar, e o "Duplicar" sumiria da
    #: tela sem nenhum aviso. `AcaoPorCadastroTests` guarda essa armadilha.
    actions = (
        *DuplicateAdminMixin.actions,
        "action_activate",
        "action_deactivate",
        "action_feature",
        "action_unfeature",
    )

    readonly_fields = (
        "nome_pt",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )

    #: Todas as seções recolhem (`collapse`); `start-open` diz quais abrem
    #: expandidas — ver `templates/admin/includes/fieldset.html`. Só a
    #: AUDITORIA começa fechada: ela é consulta, não trabalho.
    fieldsets = (
        (
            "INFORMAÇÕES BÁSICAS",
            {
                "classes": ("collapse", "start-open"),
                "fields": (("sku", "status"), ("category", "brand"), "nome_pt", "slug"),
                "description": (
                    "Este cadastro é a definição <b>genérica</b> do produto. "
                    "Preço, estoque, peso, dimensões e prazo de produção ficam em "
                    "<b>VARIANTES</b> — cada variante é uma unidade vendável. "
                    "O nome e as descrições, por idioma, ficam em <b>CONTEÚDO</b>."
                ),
            },
        ),
        (
            "CORES",
            {
                "classes": ("collapse", "start-open"),
                "fields": ("color_mode",),
                "description": (
                    "Use esta seção para descrever as cores do produto. Isso não cria "
                    "variantes. «Uma cor» e «Multicolorido» usam a PALETA DE CORES logo "
                    "abaixo; «Cores à escolha do cliente» oferece as cores da PALETA na "
                    "compra — cada uma com o seu adicional de preço, somado ao preço da "
                    "VARIANTE escolhida —; «Opção comercial» mantém a cor como eixo de "
                    "cada VARIANTE."
                ),
            },
        ),
        (
            "PERSONALIZAÇÃO",
            {
                "classes": ("collapse", "start-open"),
                "fields": (("personalization_type", "personalization_text_limit"),),
                "description": (
                    "Define se o cliente deverá fornecer uma foto, um texto ou escolher "
                    "entre os dois antes de adicionar o produto ao carrinho. "
                    "É característica do produto — não crie categoria para isso."
                ),
            },
        ),
        (
            "OUTRAS INFORMAÇÕES",
            {
                "classes": ("collapse",),
                # `is_featured` e `featured_order` vão juntos: são a mesma
                # funcionalidade — se o produto está em destaque, e em que
                # posição. Deixar a ordem para trás a tornaria ineditável, e é
                # ela que ordena a prateleira da home e a primeira passada das
                # sugestões.
                "fields": (("currency", "is_featured", "featured_order"),),
                "description": (
                    "Moeda e destaque na Home. Custos, margem, estoque, peso, "
                    "dimensões e produção são de cada <b>VARIANTE</b>."
                ),
            },
        ),
    )

    #: A AUDITORIA é o último bloco da página. Ela sai dos `fieldsets` porque o
    #: Django desenha **todos** os fieldsets antes de **todos** os inlines: se
    #: ficasse lá, apareceria antes de CONTEÚDO, VARIANTES e MÍDIA. Como
    #: `readonly_fields` já a torna somente leitura, um inline vazio seria uma
    #: gambiarra pior — então ela é renderizada por
    #: `templates/admin/catalog/product/change_form.html`, no fim da página.
    audit_fields = ("created_at", "created_by", "updated_at", "updated_by")

    class Media:
        css = {
            "all": (
                "admin/css/jdprint_admin.css",
                # A lista de produtos. Fica em arquivo próprio porque é a
                # única tela do Admin com o painel de filtros.
                "admin/css/jdprint_product_list.css",
            )
        }
        # `jd_modal.js` primeiro: e a casca que os outros dois usam.
        js = (
            "admin/js/jd_modal.js",
            "admin/js/variant_admin.js",
            "admin/js/content_admin.js",
            "admin/js/jd_tabular_cards.js",
            "admin/js/product_list.js",
            "admin/js/product_sku_suggest.js",
            "admin/js/product_colors_admin.js",
            "admin/js/jd_fields.js",
            "admin/js/product_form_admin.js",
            "admin/js/product_options_admin.js",
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # O aviso depois de salvar vai para a lista; com «Salvar e continuar»
        # a própria ficha o mostra ao reabrir (ver `render_change_form`).
        if "_continue" not in request.POST:
            self._avisar_cor_repetida(request, form.instance)

    def _avisar_cor_repetida(self, request, produto):
        """«Cores à escolha»: a mesma cor na paleta e numa variante ativa.

        Não é erro — corpo Preto com pompom Preto é legítimo —, mas quase
        sempre é um produto que oferece a mesma parte duas vezes. Fica o aviso
        para o administrador conferir, e o salvamento segue.
        """
        if produto is None or not produto.pk or produto.color_mode != ColorMode.CUSTOM:
            return
        nas_variantes = set(
            produto.variants.filter(is_active=True, color__isnull=False).values_list("color_id", flat=True)
        )
        if not nas_variantes:
            return
        repetidas = list(
            Color.objects.filter(pk__in=nas_variantes, product_uses__product=produto)
            .order_by("name")
            .values_list("name", flat=True)
        )
        if not repetidas:
            return
        nomes = ", ".join(f"«{nome}»" for nome in repetidas)
        if len(repetidas) == 1:
            frase = f"{nomes}: esta cor também está sendo usada nas variantes deste produto."
        else:
            frase = f"{nomes}: estas cores também estão sendo usadas nas variantes deste produto."
        self.message_user(
            request,
            f"⚠️ {frase} Verifique se as duas cores representam partes diferentes do produto.",
            messages.WARNING,
        )

    def save_model(self, request, obj, form, change):
        """Grava — e, se o SKU foi sugerido, tenta a sequência seguinte numa colisão.

        Entre a sugestão (na validação) e a gravação outro cadastro pode ter
        levado o mesmo número; o `unique` do banco recusa, e a mesma rotina do
        cadastro rápido (`com_sku_livre`) pede a próxima sequência. Um SKU
        digitado não passa por isto: colidiu, é erro para a pessoa ver.
        """
        if change or not getattr(form, "sku_auto", False):
            return super().save_model(request, obj, form, change)

        categoria = form.cleaned_data.get("category")
        nome = form.portuguese_name()

        def sugerir(reservados):
            return sku_rules.suggest_product_sku(categoria, nome, reservados)

        def gravar(sku):
            obj.sku = sku
            super(ProductAdmin, self).save_model(request, obj, form, change)
            return obj

        sku_rules.com_sku_livre(sugerir, gravar)

    # -- os modais (variantes e conteúdo) -----------------------------------

    def get_urls(self):
        """Duas rotas para o modal: gravar e excluir uma variante.

        Ficam sob a URL do produto de propósito — quem pode editar o produto é
        quem pode mexer nas variantes dele, e `admin_view` já exige sessão de
        equipe. A permissão fina é conferida em cada uma.
        """
        extra = [
            path(
                "novo/",
                self.admin_site.admin_view(self.quick_add_view),
                name="catalog_product_quick_add",
            ),
            path(
                "sku-sugestao/",
                self.admin_site.admin_view(self.sku_suggestion_view),
                name="catalog_product_sku_suggestion",
            ),
            path(
                "<int:product_id>/status/",
                self.admin_site.admin_view(self.toggle_status_view),
                name="catalog_product_toggle_status",
            ),
            path(
                "<int:product_id>/variante/gravar/",
                self.admin_site.admin_view(self.variant_save_view),
                name="catalog_product_variant_save",
            ),
            path(
                "<int:product_id>/variante/<int:variant_id>/excluir/",
                self.admin_site.admin_view(self.variant_delete_view),
                name="catalog_product_variant_delete",
            ),
            path(
                "<int:product_id>/conteudo/gravar/",
                self.admin_site.admin_view(self.content_save_view),
                name="catalog_product_content_save",
            ),
            path(
                "<int:product_id>/conteudo/<int:translation_id>/excluir/",
                self.admin_site.admin_view(self.content_delete_view),
                name="catalog_product_content_delete",
            ),
            # «Copiar de…» (etapa 4C.2): procurar a origem, ver o plano, copiar.
            path(
                "<int:product_id>/conteudo/copiar/buscar/",
                self.admin_site.admin_view(self.content_copy_search_view),
                name="catalog_product_content_copy_search",
            ),
            path(
                "<int:product_id>/conteudo/copiar/<int:source_id>/plano/",
                self.admin_site.admin_view(self.content_copy_plan_view),
                name="catalog_product_content_copy_plan",
            ),
            path(
                "<int:product_id>/conteudo/copiar/",
                self.admin_site.admin_view(self.content_copy_view),
                name="catalog_product_content_copy",
            ),
            # Opções adicionais (etapa 3C): opção e valor, gravar e excluir.
            path(
                "<int:product_id>/opcao/gravar/",
                self.admin_site.admin_view(self.option_save_view),
                name="catalog_product_option_save",
            ),
            path(
                "<int:product_id>/opcao/<int:option_id>/excluir/",
                self.admin_site.admin_view(self.option_delete_view),
                name="catalog_product_option_delete",
            ),
            path(
                "<int:product_id>/opcao/<int:option_id>/valor/gravar/",
                self.admin_site.admin_view(self.option_value_save_view),
                name="catalog_product_option_value_save",
            ),
            path(
                "<int:product_id>/opcao/<int:option_id>/valor/<int:value_id>/excluir/",
                self.admin_site.admin_view(self.option_value_delete_view),
                name="catalog_product_option_value_delete",
            ),
        ]
        return extra + super().get_urls()

    # -- cadastro rápido ------------------------------------------------------

    def quick_add_view(self, request):
        """Nome, categoria, SKU (sugerido) e status. Cadastrar primeiro.

        É também a tela de **duplicar**: com `?_duplicar=<pk>` os cinco campos
        abrem preenchidos com os do produto-modelo e, ao gravar, o resto dele
        acompanha (`apply_product_template`). O modelo passa pelo
        `get_queryset` e pela permissão de ver, como em qualquer duplicação.
        """
        if not self.has_add_permission(request):
            raise PermissionDenied

        modelo = self.duplicate_source(request)
        form = QuickProductForm(
            request.POST or None,
            template=modelo,
            initial=QuickProductForm.initial_from(modelo) if modelo is not None else None,
        )
        if request.method == "POST" and form.is_valid():
            produto = form.save(request.user)
            self.log_addition(request, produto, [{"added": {}}])
            self.message_user(
                request,
                format_html(
                    'Produto <b>{}</b> criado como {} com o SKU <b>{}</b>. Complete a ficha quando quiser.',
                    produto.display_name,
                    produto.get_status_display().lower(),
                    produto.sku,
                ),
                messages.SUCCESS,
            )
            if form.template_report:
                r = form.template_report
                self.message_user(
                    request,
                    format_html(
                        "Copiado de <b>{}</b>: {} idioma(s) de conteúdo, {} variante(s) "
                        "com SKU novo e estoque zerado, {} opção(ões) adicionais com "
                        "{} valor(es), {} cor(es) na paleta e {} material(is). "
                        "Fotos não acompanham.",
                        modelo.sku,
                        r["idiomas"], r["variantes"], r["opcoes"], r["valores"],
                        r["cores"], r["materiais"],
                    ),
                    messages.INFO,
                )
            if "_continue" in request.POST:
                return HttpResponseRedirect(
                    reverse("admin:catalog_product_change", args=[produto.pk])
                )
            if "_addanother" in request.POST:
                return HttpResponseRedirect(reverse("admin:catalog_product_quick_add"))
            return HttpResponseRedirect(reverse("admin:catalog_product_changelist"))

        contexto = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": f"Novo produto a partir de {modelo.sku}" if modelo else "Novo produto",
            "form": form,
            "variant_sku_preview": "SKU-V01",
            "jd_template": modelo,
        }
        return render(request, "admin/catalog/product/quick_add.html", contexto)

    def sku_suggestion_view(self, request):
        """A sugestão de SKU para o cadastro rápido, enquanto se digita."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        nome = (request.GET.get("name") or "").strip()
        categoria = None
        chave = request.GET.get("category") or ""
        if chave.isdigit():
            categoria = Category.objects.filter(pk=int(chave)).first()
        if not nome:
            return JsonResponse({"sku": ""})
        return JsonResponse({"sku": sku_rules.suggest_product_sku(categoria, nome)})

    # -- ativar / desativar pela lista --------------------------------------------

    def _safe_next(self, request):
        destino = request.POST.get("next") or request.GET.get("next") or ""
        if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
            return destino
        return reverse("admin:catalog_product_changelist")

    def toggle_status_view(self, request, product_id):
        """Ativa um produto inativo/rascunho, ou desativa um ativo.

        GET mostra a confirmação (o caminho sem JavaScript); POST executa. As
        regras de ativação são as de sempre — `activation_problems`.
        """
        produto = get_object_or_404(self.get_queryset(request), pk=product_id)
        if not self.has_change_permission(request, produto):
            raise PermissionDenied

        ativar = produto.status != ProductStatus.ACTIVE
        problemas = activation_problems(produto) if ativar else []
        destino = self._safe_next(request)

        if request.method == "POST":
            if problemas:
                for problema in problemas:
                    self.message_user(request, f"{produto.sku}: {problema}", messages.ERROR)
            else:
                produto.status = ProductStatus.ACTIVE if ativar else ProductStatus.INACTIVE
                produto.updated_by = request.user
                produto.save(update_fields=["status", "updated_by", "updated_at"])
                self.log_change(request, produto, "Status alterado pela lista.")
                self.message_user(
                    request,
                    f"{produto.sku} {'ativado' if ativar else 'desativado'}.",
                    messages.SUCCESS,
                )
            return HttpResponseRedirect(destino)

        contexto = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "product": produto,
            "titulo": "Ativar produto" if ativar else "Desativar produto",
            "problemas": problemas,
            "next": destino,
        }
        return render(request, "admin/catalog/product/toggle_status.html", contexto)

    # -- duplicar: o cadastro rápido, com o produto como modelo ------------------

    def duplicate_url(self, obj) -> str:
        """O «Duplicar» abre o CADASTRO RÁPIDO com o produto como modelo.

        Duplicar é criar um produto novo: quem cadastra vê as mesmas cinco
        perguntas de sempre (nome, categoria, status, SKU, marca), já
        preenchidas, e decide ali o que este produto é. O resto do modelo
        entra depois de o produto existir (`apply_product_template`).
        """
        return f"{reverse('admin:catalog_product_quick_add')}?{urlencode({DUPLICATE_PARAM: obj.pk})}"

    def get_changeform_initial_data(self, request):
        """A ficha completa não é a tela de duplicação.

        Um `_duplicar` na URL dela não pré-preenche nada — quem duplica é o
        cadastro rápido (ver `duplicate_url`). Sem isto haveria duas telas
        fazendo a mesma coisa de jeitos diferentes.
        """
        inicial = admin.ModelAdmin.get_changeform_initial_data(self, request)
        inicial.pop(DUPLICATE_PARAM, None)
        return inicial

    # -- conteúdo -----------------------------------------------------------

    def _content_payload(self, translation):
        """O que o modal reescreve na tela depois de gravar."""
        return {
            "id": translation.pk,
            "fields": {
                "language": translation.language,
                "name": translation.name,
                "short_description": translation.short_description,
                "description": translation.description,
                "extra_information": translation.extra_information,
            },
        }

    @method_decorator(require_POST)
    def content_save_view(self, request, product_id):
        """Grava **um** idioma. Cria quando não vem `translation_id`."""
        product = get_object_or_404(Product, pk=product_id)
        if not self.has_change_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        translation_id = request.POST.get("translation_id") or ""
        instance = None
        if translation_id:
            instance = get_object_or_404(
                ProductTranslation, pk=translation_id, master=product
            )

        form = ProductTranslationModalForm(request.POST, instance=instance)
        form.instance.master = product

        if form.is_valid():
            # Um idioma por produto. A constraint do banco também recusa, mas
            # com um erro que não diz ao administrador o que fazer.
            duplicada = (
                ProductTranslation.objects.filter(
                    master=product, language=form.cleaned_data["language"]
                )
                .exclude(pk=instance.pk if instance else None)
                .exists()
            )
            if duplicada:
                form.add_error("language", "Este produto já tem conteúdo neste idioma.")

        if not form.is_valid():
            return JsonResponse(
                {
                    "ok": False,
                    "errors": {campo: list(msgs) for campo, msgs in form.errors.items()},
                },
                status=400,
            )

        criada = instance is None
        translation = form.save()
        # O produto guarda as traduções em cache; sem isto, o nome que a
        # mensagem devolve seria o de antes. O **slug** não é mexido de
        # propósito: ele é estável depois de criado (é o que
        # `TranslatedSlugAdminMixin` também faz), e renomear um produto em
        # cartaz derrubaria o link que o cliente já tem.
        product.refresh_translations()

        return JsonResponse(
            {
                "ok": True,
                "created": criada,
                "message": (
                    f"Conteúdo em {translation.get_language_display()} "
                    + ("criado." if criada else "atualizado.")
                ),
                **self._content_payload(translation),
            }
        )

    # -- «Copiar de…» (etapa 4C.2) -----------------------------------------
    #
    # Três endpoints pequenos em vez de um grande: procurar, ver o que vai
    # acontecer, e só então copiar. A confirmação precisa do plano, e o plano
    # precisa da origem — separá-los deixa cada resposta com uma pergunta só.

    def _copy_source(self, request, product, source_id):
        """O produto de origem, ou 404.

        Procurado dentro do `get_queryset` do usuário: um id de produto que
        ele não enxerga não vira origem, mesmo digitado na URL. E a origem
        nunca é o próprio destino — copiar de si mesmo não é uma operação.
        """
        if str(source_id) == str(product.pk):
            raise Http404("A origem não pode ser o próprio produto.")
        return get_object_or_404(self.get_queryset(request), pk=source_id)

    def _copy_permission(self, request, product) -> bool:
        """Quem pode **alterar o destino** pode copiar para ele."""
        return self.has_change_permission(request, product)

    def content_copy_search_view(self, request, product_id):
        """Procura o produto de origem por nome ou SKU.

        Lista curta e do servidor: o Admin de uma loja com milhares de
        produtos não pode mandar todos para o navegador só para preencher um
        `<select>`. Sem busca, devolve os últimos alterados — é a lista mais
        provável de conter o irmão que acabou de ser cadastrado.
        """
        product = get_object_or_404(Product, pk=product_id)
        if not self._copy_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        termo = (request.GET.get("q") or "").strip()
        produtos = (
            self.get_queryset(request)
            .exclude(pk=product.pk)
            .select_related("category")
            .prefetch_related("translations", "category__translations")
        )
        if termo:
            produtos = produtos.filter(
                Q(sku__icontains=termo) | Q(translations__name__icontains=termo)
            ).distinct()
        produtos = produtos.order_by("-updated_at")[:20]

        return JsonResponse(
            {
                "ok": True,
                "query": termo,
                "results": [
                    {
                        "id": item.pk,
                        "name": item.display_name,
                        "sku": item.sku,
                        "category": item.category.name if item.category_id else "",
                        "languages": sorted(
                            t.language.upper() for t in item.translations.all()
                        ),
                    }
                    for item in produtos
                ],
            }
        )

    def content_copy_plan_view(self, request, product_id, source_id):
        """O que a cópia faria, idioma a idioma — para a tela confirmar."""
        product = get_object_or_404(Product, pk=product_id)
        if not self._copy_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)
        origem = self._copy_source(request, product, source_id)

        plano = product_content_copy_plan(origem, product)
        rotulo = dict(Language.choices)
        return JsonResponse(
            {
                "ok": True,
                "source": {"id": origem.pk, "name": origem.display_name, "sku": origem.sku},
                "target": {"id": product.pk, "name": product.display_name, "sku": product.sku},
                "plan": {
                    chave: [
                        {"code": code, "label": rotulo.get(code, code.upper())}
                        for code in plano[chave]
                    ]
                    for chave in ("replace", "create", "keep")
                },
                "empty": not (plano["replace"] or plano["create"]),
            }
        )

    @method_decorator(require_POST)
    def content_copy_view(self, request, product_id):
        """Executa a cópia. Só aqui alguma coisa é gravada."""
        product = get_object_or_404(Product, pk=product_id)
        if not self._copy_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        source_id = (request.POST.get("source_id") or "").strip()
        if not source_id.isdigit():
            return JsonResponse(
                {"ok": False, "detail": "Escolha o produto de origem."}, status=400
            )
        origem = self._copy_source(request, product, source_id)

        plano = copy_product_content(origem, product)
        copiadas = len(plano["replace"]) + len(plano["create"])
        if not copiadas:
            return JsonResponse(
                {
                    "ok": False,
                    "detail": (
                        f"«{origem.display_name}» não tem conteúdo cadastrado para copiar."
                    ),
                },
                status=400,
            )

        # O mesmo histórico que o resto do Admin usa (`LogEntry`), e não um
        # registro paralelo: a operação aparece em "Histórico" do produto.
        mensagem = (
            f"Descrições copiadas de {origem.sku} — {origem.display_name}: "
            f"{copiadas} tradução(ões)."
        )
        if plano["keep"]:
            mensagem += f" {len(plano['keep'])} preservada(s)."
        self.log_change(request, product, mensagem)

        aviso = (
            f"Descrições copiadas com sucesso de «{origem.display_name}». "
            f"{copiadas} tradução(ões) copiada(s)."
        )
        if plano["keep"]:
            aviso += (
                f" {len(plano['keep'])} tradução(ões) existente(s) preservada(s), "
                "porque a origem não tem esse idioma."
            )
        return JsonResponse({"ok": True, "message": aviso, "plan": plano, "copied": copiadas})

    @method_decorator(require_POST)
    def content_delete_view(self, request, product_id, translation_id):
        """Remove um idioma — menos o português de um produto ativo.

        Sem o português a loja fica sem fallback: um produto ativo passaria a
        aparecer com o SKU no lugar do nome em qualquer idioma sem tradução.
        É a mesma regra que o formset já cobra ao gravar o produto.
        """
        product = get_object_or_404(Product, pk=product_id)
        if not self.has_delete_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        translation = get_object_or_404(
            ProductTranslation, pk=translation_id, master=product
        )

        e_o_padrao = translation.language == DEFAULT_LANGUAGE.value
        if e_o_padrao and product.status == ProductStatus.ACTIVE:
            return JsonResponse(
                {
                    "ok": False,
                    "detail": (
                        "O conteúdo em português é o fallback da loja e não pode "
                        "ser removido de um produto ativo. Volte o produto para "
                        "rascunho, ou cadastre outro idioma antes."
                    ),
                },
                status=400,
            )

        if product.translations.count() <= 1:
            return JsonResponse(
                {
                    "ok": False,
                    "detail": "Este é o único conteúdo do produto. Cadastre outro antes.",
                },
                status=400,
            )

        idioma = translation.get_language_display()
        translation.delete()
        return JsonResponse({"ok": True, "message": f"Conteúdo em {idioma} removido."})

    def _variant_payload(self, variant):
        """O que o modal precisa reescrever na tela depois de gravar.

        Vem do **servidor**, não do que foi digitado: `save()` recalcula custo
        total, preço e margem com `Decimal`, e o número da tela era só uma
        pré-visualização. Devolver o valor gravado é o que faz a tabela contar
        a verdade.
        """
        def texto(valor):
            return "" if valor is None else str(valor)

        # As escolhas nas opções adicionais: vazio para a opção sem valor.
        escolhas = {
            option_field_name(option): ""
            for option in ProductOption.objects.filter(product_id=variant.product_id)
        }
        for link in variant.option_values.all():
            escolhas[f"{OPTION_FIELD_PREFIX}{link.option_id}"] = texto(link.value_id)

        return {
            "id": variant.pk,
            "fields": {
                **escolhas,
                "sku": variant.sku,
                "sort_order": texto(variant.sort_order),
                "is_active": variant.is_active,
                "color": texto(variant.color_id),
                "size": variant.size,
                "material": texto(variant.material_id),
                "filament_cost": texto(variant.filament_cost),
                "energy_cost": texto(variant.energy_cost),
                "pricing_mode": variant.pricing_mode,
                "sale_price": texto(variant.sale_price),
                "profit_margin": texto(variant.profit_margin),
                "stock_quantity": texto(variant.stock_quantity),
                "allow_backorder": variant.allow_backorder,
                "made_to_order": variant.made_to_order,
                "production_lead_time_days": texto(variant.production_lead_time_days),
                "weight_grams": texto(variant.weight_grams),
                "print_time": texto(variant.print_time),
                "width": texto(variant.width),
                "height": texto(variant.height),
                "depth": texto(variant.depth),
                "dimension_unit": variant.dimension_unit,
            },
        }

    @method_decorator(require_POST)
    def variant_save_view(self, request, product_id):
        """Grava **uma** variante. Cria quando não vem `variant_id`.

        Devolve JSON: `{ok: true, ...}` ou `{ok: false, errors: {campo: [...]}}`.
        Os erros voltam por campo justamente para o modal poder ficar aberto
        com o que já foi digitado — recarregar a página perderia tudo.
        """
        product = get_object_or_404(Product, pk=product_id)
        if not self.has_change_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        variant_id = request.POST.get("variant_id") or ""
        instance = None
        if variant_id:
            instance = get_object_or_404(ProductVariant, pk=variant_id, product=product)

        form = ProductVariantModalForm(
            request.POST,
            instance=instance,
            product_options=list(product.options.prefetch_related("values")),
        )
        form.instance.product = product

        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "errors": {campo: list(msgs) for campo, msgs in form.errors.items()}},
                status=400,
            )

        criada = instance is None
        try:
            with transaction.atomic():
                variant = form.save()
        except ValidationError as erro:
            # `set_option_values` recusou (combinação repetida com as escolhas):
            # nada foi gravado, e o modal mostra o motivo.
            return JsonResponse({"ok": False, "errors": _erros_por_campo(erro)}, status=400)

        variant = (
            ProductVariant.objects.prefetch_related(*variant_option_prefetches()).get(pk=variant.pk)
        )
        return JsonResponse(
            {
                "ok": True,
                "created": criada,
                "message": (
                    f"Variante {variant.sku} criada."
                    if criada
                    else f"Variante {variant.sku} atualizada."
                ),
                **self._variant_payload(variant),
            }
        )

    @method_decorator(require_POST)
    def variant_delete_view(self, request, product_id, variant_id):
        """Exclui uma variante — recusando deixar um produto ativo sem nenhuma.

        A mesma regra que o formset já cobra ao gravar o produto (etapa 8): sem
        variante ativa não há preço, peso nem estoque, e o produto viraria uma
        página que não vende. Recusar aqui evita chegar lá.
        """
        product = get_object_or_404(Product, pk=product_id)
        if not self.has_delete_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)

        restantes = (
            ProductVariant.objects.filter(product=product, is_active=True)
            .exclude(pk=variant.pk)
            .count()
        )
        if product.status == ProductStatus.ACTIVE and variant.is_active and restantes == 0:
            return JsonResponse(
                {
                    "ok": False,
                    "detail": (
                        "Esta é a última variante ativa de um produto ativo. "
                        "Cadastre outra antes, ou volte o produto para rascunho."
                    ),
                },
                status=400,
            )

        sku = variant.sku
        variant.delete()
        return JsonResponse({"ok": True, "message": f"Variante {sku} excluída."})

    # -- opções adicionais (etapa 3C) -----------------------------------------

    def _options_for_sheet(self, product) -> list:
        """As opções do produto para a seção da ficha: opção, valores e traduções.

        Três consultas fixas (opções, traduções, valores com traduções), e o
        template só percorre listas. As traduções vão como dicionário por
        idioma, para os modais abrirem preenchidos sem outra consulta.
        """
        idiomas = option_translation_languages()

        def traducoes(obj):
            tabela = obj.translations_by_language()
            return {code: (tabela[code].name if code in tabela else "") for code in idiomas}

        opcoes = (
            product.options.prefetch_related("translations", "values__translations")
            .order_by("sort_order", "id")
        )
        return [
            {
                "option": option,
                "translations": traducoes(option),
                "values": [{"value": value, "translations": traducoes(value)} for value in option.values.all()],
            }
            for option in opcoes
        ]

    def _option_permission(self, request, product):
        """Quem pode alterar o produto pode mexer nas opções dele."""
        return self.has_change_permission(request, product)

    @method_decorator(require_POST)
    def option_save_view(self, request, product_id):
        """Grava **uma** opção (e as traduções). Cria quando não vem `option_id`."""
        product = get_object_or_404(Product, pk=product_id)
        if not self._option_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        option_id = request.POST.get("option_id") or ""
        instance = get_object_or_404(ProductOption, pk=option_id, product=product) if option_id else None
        form = ProductOptionModalForm(request.POST, instance=instance, product=product)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": _erros_por_campo(form.errors)}, status=400)

        criada = instance is None
        with transaction.atomic():
            option = form.save()
        return JsonResponse(
            {
                "ok": True,
                "created": criada,
                "id": option.pk,
                "message": f"Opção «{option.name}» " + ("criada." if criada else "atualizada."),
            }
        )

    @method_decorator(require_POST)
    def option_delete_view(self, request, product_id, option_id):
        """Exclui uma opção — recusando, com mensagem, a que alguma variante usa."""
        product = get_object_or_404(Product, pk=product_id)
        if not self._option_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)
        option = get_object_or_404(ProductOption, pk=option_id, product=product)
        nome = option.name
        try:
            with transaction.atomic():
                option.delete()
        except (RestrictedError, ProtectedError):
            return JsonResponse(
                {
                    "ok": False,
                    "detail": (
                        f"A opção «{nome}» está sendo usada por uma ou mais variantes e não "
                        "pode ser removida. Tire-a das variantes primeiro."
                    ),
                },
                status=400,
            )
        return JsonResponse({"ok": True, "message": f"Opção «{nome}» excluída."})

    @method_decorator(require_POST)
    def option_value_save_view(self, request, product_id, option_id):
        """Grava **um** valor de uma opção do produto. Cria quando não vem `value_id`."""
        product = get_object_or_404(Product, pk=product_id)
        if not self._option_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)
        option = get_object_or_404(ProductOption, pk=option_id, product=product)

        value_id = request.POST.get("value_id") or ""
        instance = get_object_or_404(ProductOptionValue, pk=value_id, option=option) if value_id else None
        form = ProductOptionValueModalForm(request.POST, instance=instance, option=option)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": _erros_por_campo(form.errors)}, status=400)

        criado = instance is None
        with transaction.atomic():
            value = form.save()
        return JsonResponse(
            {
                "ok": True,
                "created": criado,
                "id": value.pk,
                "message": f"Valor «{value.name}» " + ("criado." if criado else "atualizado."),
            }
        )

    @method_decorator(require_POST)
    def option_value_delete_view(self, request, product_id, option_id, value_id):
        product = get_object_or_404(Product, pk=product_id)
        if not self._option_permission(request, product):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)
        option = get_object_or_404(ProductOption, pk=option_id, product=product)
        value = get_object_or_404(ProductOptionValue, pk=value_id, option=option)
        nome = value.name
        try:
            with transaction.atomic():
                value.delete()
        except (RestrictedError, ProtectedError):
            return JsonResponse(
                {
                    "ok": False,
                    "detail": (
                        f"O valor «{nome}» está sendo usado por uma ou mais variantes e não "
                        "pode ser removido. Troque o valor nas variantes primeiro."
                    ),
                },
                status=400,
            )
        return JsonResponse({"ok": True, "message": f"Valor «{nome}» excluído."})

    def _page_layout(self, context):
        """As seções da página na ordem de `SECTION_ORDER`, numa lista só.

        O Django desenha **todos** os fieldsets e só depois **todos** os
        inlines. A ordem de hoje por acaso coincide com essa — os três
        fieldsets, depois os três inlines — mas na etapa 13 não coincidia
        (CONTEÚDO ficava entre CLASSIFICAÇÃO e PERSONALIZAÇÃO) e pode voltar a
        não coincidir na próxima vez que a ordem mudar.

        Por isso a página monta aqui uma lista única, e o template caminha por
        ela numa volta só: a ordem passa a ser a de `SECTION_ORDER`, e não a
        que o Django dá de brinde. Trocar duas linhas naquela tupla basta —
        sem tocar em `fieldsets`, em `inlines` nem no template.

        Cada item é ``{"kind", "fieldset"|"formset", "index"}``. O `index`
        continua sendo a posição real do fieldset no formulário: é dele que sai
        o `id` de cada bloco, e dois blocos com o mesmo `id` quebrariam o
        `aria-labelledby`.

        Uma seção que não estiver em `SECTION_ORDER` não é engolida: entra no
        fim, na ordem em que o Django a entregou. Assim um fieldset novo
        aparece na tela mesmo que alguém esqueça de listá-lo aqui.
        """
        adminform = context.get("adminform")
        formsets = list(context.get("inline_admin_formsets") or ())
        if adminform is None:
            return None

        fieldsets = list(adminform)
        por_nome = {}
        for indice, fieldset in enumerate(fieldsets):
            por_nome[fieldset.name] = {
                "kind": "fieldset",
                "fieldset": fieldset,
                "index": indice,
            }

        por_modelo = {}
        for formset in formsets:
            por_modelo[formset.opts.model] = {"kind": "formset", "formset": formset}

        layout = []
        opcoes_desenhadas = False
        for chave in SECTION_ORDER:
            if chave == OPTIONS_SECTION:
                # A seção das opções adicionais: nem fieldset nem inline —
                # cards e modais próprios (`product/_options_section.html`).
                layout.append({"kind": "options", "meta": SECTION_META.get(chave, {}), "index": None})
                opcoes_desenhadas = True
                continue
            item = por_nome.pop(chave, None) if isinstance(chave, str) else por_modelo.pop(chave, None)
            if item is not None:
                item["meta"] = SECTION_META.get(chave, {})
                layout.append(item)

        # O que sobrou (fieldset ou inline não listado) vai para o fim, na
        # ordem original — melhor no lugar errado do que invisível.
        layout.extend(item for item in por_nome.values())
        layout.extend(item for item in por_modelo.values())
        if not opcoes_desenhadas:
            layout.append({"kind": "options", "meta": SECTION_META.get(OPTIONS_SECTION, {}), "index": None})

        # A ficha: número, âncora e subtítulo de cada seção. A seção «apoiada»
        # (PALETA, dentro de CORES) não conta no número nem entra no índice.
        numero = 0
        for item in layout:
            meta = item.get("meta") or {}
            if item["kind"] == "fieldset":
                titulo, resto = split_title(item["fieldset"].name or "")
            elif item["kind"] == "options":
                titulo, resto = OPTIONS_SECTION, ""
            else:
                titulo, resto = split_title(str(item["formset"].opts.verbose_name_plural))
            item["title"] = titulo
            item["subtitle"] = meta.get("subtitle") or resto
            item["help"] = meta.get("help", "")
            item["attached"] = bool(meta.get("attached"))
            item["slug"] = meta.get("slug") or f"secao-{len(layout)}"
            item["nav"] = meta.get("nav", "")
            # O id do <h2>, para o `aria-labelledby` do fieldset — o mesmo que
            # o include do Django montaria (`fieldset-0-<índice>-heading`).
            if item["kind"] == "fieldset":
                item["heading_id"] = f"fieldset-0-{item['index']}-heading"
            elif item["kind"] == "options":
                item["heading_id"] = "options-heading"
            else:
                item["heading_id"] = f"{item['formset'].formset.prefix}-heading"
            if item["attached"]:
                item["number"] = ""
            else:
                numero += 1
                item["number"] = numero
        return layout

    def _section_nav(self, layout, with_audit):
        """Os itens da navegação rápida: «1 · Básico», «2 · Conteúdo»…"""
        itens = [
            {"number": item["number"], "label": item["nav"], "slug": item["slug"]}
            for item in layout or ()
            if item.get("nav")
        ]
        if with_audit:
            itens.append({"number": len(itens) + 1, "label": AUDIT_META["nav"], "slug": AUDIT_META["slug"]})
        return itens

    def render_change_form(self, request, context, add=False, change=False, **kwargs):
        """Entrega a ordem das seções e a AUDITORIA para o template.

        A AUDITORIA sai dos `fieldsets` e é desenhada no bloco
        `after_related_objects` do próprio Django, no fim da página. Só existe
        na edição: um produto que ainda não foi criado não tem histórico.
        """
        context["product_layout"] = self._page_layout(context)
        context["variant_modal_groups"] = VARIANT_MODAL_GROUPS
        context["variant_field_affix"] = VARIANT_FIELD_AFFIX
        context["option_translation_languages"] = option_translation_languages()
        context["product_options"] = []
        # A sugestão ao vivo só em produto NOVO (`product_sku_suggest.js`).
        if add:
            context["jd_sku_suggest_url"] = reverse("admin:catalog_product_sku_suggestion")

        original = context.get("original")
        editando = original is not None and original.pk
        context["jd_section_nav"] = self._section_nav(context["product_layout"], with_audit=bool(editando))
        if editando:
            if request.method == "GET":
                # «Cores à escolha»: a mesma cor na paleta e numa variante —
                # aviso ao abrir a ficha, nunca bloqueio.
                self._avisar_cor_repetida(request, original)
            context["audit_rows"] = [
                ("Criado em", original.created_at),
                ("Criado por", original.created_by),
                ("Atualizado em", original.updated_at),
                ("Atualizado por", original.updated_by),
            ]
            numeradas = [item for item in context["product_layout"] or () if not item.get("attached")]
            context["audit_section"] = {**AUDIT_META, "number": len(numeradas) + 1, "title": "AUDITORIA"}
            # As opções adicionais, com valores e traduções, em três consultas.
            context["product_options"] = self._options_for_sheet(original)
            # O cabeçalho da ficha e os atalhos: duplicar e ver na loja.
            context["variant_sku_next"] = sku_rules.suggest_variant_sku(original.sku)
            context["jd_view_url"] = original.get_absolute_url()
            if self.has_add_permission(request):
                context["jd_duplicate_url"] = self.duplicate_url(original)
            if self.has_change_permission(request, original):
                context["jd_toggle_url"] = reverse("admin:catalog_product_toggle_status", args=[original.pk])
                context["jd_toggle_label"] = "Desativar" if original.status == ProductStatus.ACTIVE else "Ativar"
        return super().render_change_form(request, context, add=add, change=change, **kwargs)

    def get_queryset(self, request):
        return super().get_queryset(request).for_admin_list()

    def get_changelist(self, request, **kwargs):
        return ProdutoChangeList

    def get_search_results(self, request, queryset, search_term):
        """A busca da lista: nome, SKU, categoria (com os pais) e marca.

        Escrita com `pk__in` de subconsultas em vez de `join`, ao contrário da
        busca de fábrica: `join` traria o produto repetido (uma linha por
        tradução, uma por variante) e obrigaria a um `distinct()` — que é
        justamente o que estragaria a soma de estoque anotada na lista. Por
        isso a segunda posição da tupla devolvida é `False`.

        A categoria entra com os **ancestrais**: procurar "Religiosos" acha o
        produto que está em "Religiosos › Santos › Nossa Senhora", que é o que
        quem procura espera — a categoria do produto é sempre a folha.
        """
        termo = (search_term or "").strip()
        if not termo:
            return queryset, False

        procura = (
            Q(sku__icontains=termo)
            | Q(slug__icontains=termo)
            | Q(pk__in=ProductTranslation.objects.filter(
                Q(name__icontains=termo) | Q(short_description__icontains=termo)
            ).values("master_id"))
            | Q(pk__in=ProductVariant.objects.filter(sku__icontains=termo).values("product_id"))
            | Q(brand__name__icontains=termo)
        )

        categorias = self._categorias_da_busca(request, termo)
        if categorias:
            procura |= Q(category_id__in=categorias)
        return queryset.filter(procura), False

    @staticmethod
    def _categorias_da_busca(request, termo) -> set:
        """Categorias cujo nome (ou o de um ancestral) casa com o termo."""
        arvore = arvore_de_categorias(request)
        encontradas = {
            categoria.pk
            for categoria in arvore.by_id.values()
            if termo.lower() in categoria.name_in(DEFAULT_LANGUAGE.value).lower()
            or termo.lower() in categoria.slug.lower()
        }
        alcance = set()
        for categoria_id in encontradas:
            alcance.update(arvore.subtree_ids(categoria_id))
        return alcance

    # -- o painel da lista -------------------------------------------------
    #
    # Nada aqui filtra nem ordena: quem faz isso são os filtros do Admin e o
    # `ChangeList`. O que estes métodos montam é só o que a tela precisa
    # desenhar — rótulos, contagens e URLs.

    def changelist_view(self, request, extra_context=None):
        """Acrescenta o contexto do painel à resposta que o Django já montou.

        Pós-processar a `TemplateResponse` em vez de reimplementar a view:
        o `cl` só existe depois que `super()` roda, e é dele que sai tudo —
        inclusive o queryset já recortado por permissão.
        """
        resposta = super().changelist_view(request, extra_context)
        dados = getattr(resposta, "context_data", None)
        if not dados or "cl" not in dados:
            # POST de ação, redirecionamento após "Ir", `?e=1`: nada a fazer.
            return resposta
        dados.update(self._contexto_da_lista(request, dados["cl"]))
        return resposta

    def _contexto_da_lista(self, request, cl) -> dict:
        # `cl.get_queryset()` reatribui `cl.filter_specs` a cada chamada (é
        # como as facetas do Django funcionam), então a lista é copiada antes
        # de contar — senão as instâncias mudariam debaixo do laço.
        especificacoes = list(cl.filter_specs)
        preco = next((e for e in especificacoes if isinstance(e, PrecoFiltro)), None)
        grupos = [e for e in especificacoes if e is not preco]

        inicio = (cl.page_num - 1) * cl.list_per_page + 1 if cl.result_count else 0
        return {
            "jd_total": cl.full_result_count if cl.full_result_count is not None else cl.result_count,
            "jd_resultados": cl.result_count,
            "jd_busca": cl.query,
            "jd_visoes": self._visoes(request, cl),
            "jd_grupos": [self._grupo(request, cl, grupo) for grupo in grupos],
            "jd_preco": {
                "minimo": "" if preco is None or preco.minimo is None else preco.minimo,
                "maximo": "" if preco is None or preco.maximo is None else preco.maximo,
                "campo_minimo": PrecoFiltro.MINIMO,
                "campo_maximo": PrecoFiltro.MAXIMO,
            },
            "jd_chips": self._chips(cl, especificacoes),
            "jd_url_limpar_tudo": self._url_limpar_tudo(cl),
            "jd_ocultos": self._ocultos(cl, especificacoes),
            "jd_ordenacoes": self._ordenacoes(cl),
            "jd_por_pagina": self._por_pagina(cl),
            "jd_paginas": self._paginas(cl),
            "jd_faixa": (
                f"Mostrando {inicio}–{inicio + len(cl.result_list) - 1} de {cl.result_count}"
                if cl.result_count
                else ""
            ),
            "jd_parametro_por_pagina": ProdutoChangeList.PARAMETRO_POR_PAGINA,
            "jd_parametro_busca": SEARCH_VAR,
            "jd_parametro_ordem": ORDER_VAR,
            "jd_ordem_atual": cl.params.get(ORDER_VAR, ""),
        }

    # -- URLs --------------------------------------------------------------

    @staticmethod
    def _querystring(parametros) -> str:
        limpos = {chave: valores for chave, valores in parametros.items() if valores}
        return "?" + urlencode(sorted(limpos.items()), doseq=True) if limpos else "?"

    @staticmethod
    def _parametros(cl) -> dict:
        """Cópia mutável do que está na URL, sem a página.

        Sem a página de propósito: mudar um filtro e continuar na página 7 é
        um jeito confiável de cair numa lista vazia.
        """
        parametros = {chave: list(valores) for chave, valores in cl.filter_params.items()}
        parametros.pop(PAGE_VAR, None)
        return parametros

    def _url_sem(self, cl, nome, valor=None) -> str:
        """A URL de agora menos **um** valor — é o que o × do chip faz.

        Um valor, e não o parâmetro inteiro: os grupos são multi-seleção, e
        tirar "PLA" não pode levar "PETG" junto.
        """
        parametros = self._parametros(cl)
        if valor is None:
            parametros.pop(nome, None)
        else:
            restantes = [item for item in parametros.get(nome, []) if str(item) != str(valor)]
            if restantes:
                parametros[nome] = restantes
            else:
                parametros.pop(nome, None)
        return self._querystring(parametros)

    def _url_limpar_tudo(self, cl) -> str:
        """Zera busca e filtros; ordenação e itens por página ficam.

        São preferências de leitura da tela, não um recorte do catálogo.
        """
        guardados = {
            chave: cl.filter_params[chave]
            for chave in (ORDER_VAR, ProdutoChangeList.PARAMETRO_POR_PAGINA)
            if chave in cl.filter_params
        }
        return self._querystring(guardados)

    def _ocultos(self, cl, especificacoes) -> list:
        """O que está na URL e não tem campo no formulário do painel.

        A hierarquia de datas, por exemplo: sem estes `hidden` ela seria
        apagada assim que alguém digitasse na busca.
        """
        donos = {SEARCH_VAR, ORDER_VAR, ProdutoChangeList.PARAMETRO_POR_PAGINA, PAGE_VAR}
        for especificacao in especificacoes:
            donos.update(especificacao.expected_parameters())
        return [
            {"nome": nome, "valor": valor}
            for nome, valores in sorted(cl.filter_params.items())
            if nome not in donos
            for valor in valores
        ]

    # -- pedaços do painel -------------------------------------------------

    def _visoes(self, request, cl) -> list:
        """As pílulas, com quantos produtos existem em cada estado.

        A contagem é do catálogo inteiro (recortado por permissão), não do
        resultado atual: a pílula responde "quantos existem assim", que é o
        que faz dela um atalho útil mesmo com filtros ligados.
        """
        from apps.catalog.admin_filters import EstoqueFiltro

        base = self.model.objects.filter(
            pk__in=cl.root_queryset.order_by().values("pk")
        ).with_admin_annotations()
        contagens = base.aggregate(
            todos=Count("pk", distinct=True),
            rascunhos=Count("pk", filter=Q(status=ProductStatus.DRAFT), distinct=True),
            estoque=Count("pk", filter=EstoqueFiltro._pedacos()["baixo"], distinct=True),
            pendentes=Count("pk", filter=Q(_variantes_ativas=0), distinct=True),
            destaques=Count("pk", filter=Q(is_featured=True), distinct=True),
        )

        donos = set()
        for _chave, _rotulo, parametros in VISOES_DA_LISTA:
            donos.update(parametros)

        atuais = self._parametros(cl)
        visoes = []
        for chave, rotulo, parametros in VISOES_DA_LISTA:
            alvo = {nome: valores for nome, valores in atuais.items() if nome not in donos}
            alvo.update({nome: list(valores) for nome, valores in parametros.items()})
            ativa = all(
                [str(item) for item in atuais.get(nome, [])] == [str(item) for item in parametros.get(nome, [])]
                for nome in donos
            )
            visoes.append(
                {
                    "chave": chave,
                    "rotulo": rotulo,
                    "contagem": contagens.get(chave) or 0,
                    "url": self._querystring(alvo),
                    "ativa": ativa,
                }
            )
        return visoes

    def _grupo(self, request, cl, especificacao) -> dict:
        """Um grupo do painel, com a contagem de cada opção.

        A contagem sai do queryset filtrado por **todos os outros** grupos e
        pela busca, mas não por este — `exclude_parameters` é exatamente isso.
        Sem essa exclusão, marcar "PLA" zeraria a contagem de "PETG" e a tela
        diria que não existe PETG nenhum no catálogo.
        """
        pool = cl.get_queryset(request, exclude_parameters=especificacao.expected_parameters())
        base = self.model.objects.filter(pk__in=pool.order_by().values("pk"))
        contagens = especificacao.contar(base)

        escolhidos = list(especificacao.escolhidos)
        rotulos = [
            opcao["rotulo"] for opcao in especificacao.opcoes if opcao["valor"] in escolhidos
        ]
        if not rotulos:
            texto = especificacao.vazio
        elif len(rotulos) == 1:
            texto = rotulos[0]
        else:
            texto = ", ".join(rotulos[:2])

        return {
            "titulo": especificacao.title,
            "parametro": especificacao.parametro,
            "texto": texto,
            "quantidade": len(escolhidos),
            "ativo": bool(escolhidos),
            "url_limpar": self._url_sem(cl, especificacao.parametro),
            "opcoes": [
                {
                    "valor": opcao["valor"],
                    "rotulo": opcao["rotulo"],
                    "recuo": opcao["nivel"] * 14,
                    "marcada": opcao["valor"] in escolhidos,
                    "contagem": contagens.get(opcao["valor"], 0),
                }
                for opcao in especificacao.opcoes
            ],
        }

    def _chips(self, cl, especificacoes) -> list:
        chips = []
        if cl.query:
            chips.append(
                {"rotulo": f"Busca: “{cl.query}”", "url": self._url_sem(cl, SEARCH_VAR)}
            )
        for especificacao in especificacoes:
            if isinstance(especificacao, PrecoFiltro):
                if especificacao.minimo is not None:
                    chips.append(
                        {
                            "rotulo": f"Preço ≥ €{especificacao.minimo}",
                            "url": self._url_sem(cl, PrecoFiltro.MINIMO),
                        }
                    )
                if especificacao.maximo is not None:
                    chips.append(
                        {
                            "rotulo": f"Preço ≤ €{especificacao.maximo}",
                            "url": self._url_sem(cl, PrecoFiltro.MAXIMO),
                        }
                    )
                continue
            rotulos = {opcao["valor"]: opcao["rotulo"] for opcao in especificacao.opcoes}
            for valor in especificacao.escolhidos:
                chips.append(
                    {
                        "rotulo": f"{especificacao.title}: {rotulos.get(valor, valor)}",
                        "url": self._url_sem(cl, especificacao.parametro, valor),
                    }
                )
        return chips

    def _ordenacoes(self, cl) -> list:
        """O `<select>` de ordenação fala a mesma língua do cabeçalho da tabela.

        Ele escreve o mesmo `?o=` que o clique no `<th>` escreve, e o índice
        sai do `cl.list_display` de verdade — assim mexer em `list_display`
        não deixa o select apontando para a coluna errada.
        """
        colunas = list(cl.list_display)

        def indice(nome):
            return colunas.index(nome) if nome in colunas else None

        atual = cl.params.get(ORDER_VAR, "")
        opcoes = [{"rotulo": "Mais recentes", "valor": "", "ativa": not atual}]
        for rotulo, coluna, decrescente in (
            ("Mais antigos", "id", False),
            ("Nome (A–Z)", "display_name", False),
            ("Preço ↑", "price_display", False),
            ("Preço ↓", "price_display", True),
            ("Estoque ↑", "stock_display", False),
            ("Estoque ↓", "stock_display", True),
        ):
            posicao = indice(coluna)
            if posicao is None:
                continue
            valor = f"-{posicao}" if decrescente else str(posicao)
            opcoes.append({"rotulo": rotulo, "valor": valor, "ativa": atual == valor})
        if atual and not any(opcao["ativa"] for opcao in opcoes):
            # Ordenação vinda de um clique no cabeçalho da tabela (que aceita
            # combinações que o select não lista). Sem esta entrada o select
            # cairia na primeira opção e o próximo envio apagaria a escolha.
            opcoes.append({"rotulo": "Ordenação da tabela", "valor": atual, "ativa": True})
        return opcoes

    def _por_pagina(self, cl) -> list:
        return [
            {
                "valor": str(quantidade),
                "rotulo": f"{quantidade} por página",
                "ativa": cl.list_per_page == quantidade,
            }
            for quantidade in ProdutoChangeList.POR_PAGINA
        ]

    def _paginas(self, cl) -> list:
        if cl.paginator.num_pages <= 1:
            return []
        paginas = []
        for numero in cl.paginator.get_elided_page_range(cl.page_num, on_each_side=2, on_ends=1):
            if numero == cl.paginator.ELLIPSIS:
                paginas.append({"reticencias": True, "rotulo": str(numero)})
                continue
            paginas.append(
                {
                    "reticencias": False,
                    "rotulo": str(numero),
                    "url": cl.get_query_string({PAGE_VAR: numero}),
                    "atual": numero == cl.page_num,
                }
            )
        return paginas

    # -- colunas calculadas ------------------------------------------------

    @admin.display(description="categoria", ordering="category")
    def categoria(self, obj):
        """O caminho inteiro, como no modelo: «Religiosos › Santos › Aparecida».

        O texto vem carimbado por `ProdutoChangeList.get_results`. Sem ele
        (numa listagem montada fora do changelist) cai no `str` de sempre, que
        dá o mesmo resultado pagando consultas.
        """
        caminho = getattr(obj, "jd_caminho", None)
        if caminho is None:
            caminho = str(obj.category) if obj.category_id else ""
        return caminho or format_html('<span class="jd-muted">{}</span>', "—")

    #: Os tipos de mídia que servem de miniatura na lista.
    MIDIA_VISIVEL = (MediaType.IMAGE, MediaType.GIF)

    @admin.display(description="produto", ordering="_nome_pt")
    def display_name(self, obj):
        """Miniatura e nome, como na linha do modelo.

        A foto sai do `prefetch_related("media")` que a listagem já faz — não
        custa uma consulta por linha. Sem foto entra um retângulo listrado do
        mesmo tamanho: assim a coluna não muda de altura de linha para linha,
        e a falta da foto fica visível em vez de invisível.
        """
        nome = obj.name_in(DEFAULT_LANGUAGE.value)
        endereco = self._miniatura(obj)
        if endereco is None:
            return format_html(
                '<span class="jd-produto"><span class="jd-thumb jd-thumb-vazio"></span>'
                '<span class="jd-produto-nome">{}</span></span>',
                nome,
            )
        return format_html(
            '<span class="jd-produto">'
            '<img class="jd-thumb" src="{}" alt="" loading="lazy" width="34" height="34">'
            '<span class="jd-produto-nome">{}</span></span>',
            endereco,
            nome,
        )

    def _miniatura(self, obj):
        """A foto principal do produto, ou a primeira que existir."""
        imagens = [item for item in obj.media.all() if item.media_type in self.MIDIA_VISIVEL]
        escolhida = next((item for item in imagens if item.is_primary), None) or next(
            iter(imagens), None
        )
        if escolhida is None or not escolhida.file:
            return None
        try:
            return escolhida.file.url
        except ValueError:  # arquivo sem storage configurado
            return None

    @admin.display(description="nome (português)")
    def nome_pt(self, obj):
        """O nome, na ficha — cadastrado e traduzido em CONTEÚDO."""
        if obj is None or not obj.pk:
            return "Cadastre o nome em CONTEÚDO, abaixo."
        return obj.tr("name", language=DEFAULT_LANGUAGE.value, fallback=False) or mark_safe(
            '<span class="jd-badge jd-badge-warn">sem nome em português — cadastre em CONTEÚDO</span>'
        )

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        classes = {
            ProductStatus.DRAFT: "jd-badge-muted",
            ProductStatus.ACTIVE: "jd-badge-ok",
            ProductStatus.INACTIVE: "jd-badge-danger",
        }
        return format_html(
            '<span class="jd-badge {}">{}</span>',
            classes.get(obj.status, "jd-badge-muted"),
            obj.get_status_display(),
        )

    @admin.display(description="preço", ordering="_preco_ordem")
    def price_display(self, obj):
        """Preço das variantes ativas: um valor, ou a faixa quando diferem.

        Derivado das variantes, sempre — não há preço no produto.
        """
        low, high = obj.price_range
        if low is None:
            return format_html('<span class="jd-muted" title="{}">—</span>', "sem variante ativa com preço")
        if low == high:
            return money(low, obj.currency_symbol)
        return f"{money(low, obj.currency_symbol)} – {money(high, obj.currency_symbol)}"

    @admin.display(description="variantes", ordering="_variantes_ativas")
    def variant_count(self, obj):
        """«1 opção», «3 opções» (link para a seção), ou o alerta sem variante."""
        total = len(obj.variants.all())
        ativas = len(obj.active_variants())
        if total == 0:
            return format_html(
                '<a class="jd-badge jd-badge-warn" href="{}#variants-group" title="{}">⚠ Sem configuração</a>',
                reverse("admin:catalog_product_change", args=[obj.pk]),
                "Solicitar informações: nenhuma variante cadastrada (sem preço, estoque nem peso).",
            )
        texto = "1 opção" if total == 1 else f"{total} opções"
        if ativas != total:
            texto += f" ({ativas} ativa{'s' if ativas != 1 else ''})"
        return format_html(
            '<a href="{}#variants-group">{}</a>',
            reverse("admin:catalog_product_change", args=[obj.pk]),
            texto,
        )

    @admin.display(description="ações")
    def acoes(self, obj):
        """Editar · Duplicar · ⋮ (ver produto, ativar/desativar)."""
        editar = reverse("admin:catalog_product_change", args=[obj.pk])
        alternar = reverse("admin:catalog_product_toggle_status", args=[obj.pk])
        rotulo = "Desativar" if obj.status == ProductStatus.ACTIVE else "Ativar"
        return format_html(
            '<div class="jd-row-actions">'
            '<a href="{}">Editar</a>'
            '<a href="{}">Duplicar</a>'
            '<details class="jd-row-menu"><summary aria-label="Mais ações">⋮</summary>'
            '<div class="jd-row-menu-list">'
            '<a href="{}" target="_blank" rel="noopener">Ver produto</a>'
            '<a href="{}" data-toggle-status>{}</a>'
            '</div></details></div>',
            editar,
            self.duplicate_url(obj),
            obj.get_absolute_url(),
            alternar,
            rotulo,
        )

    @admin.display(description="personalização", ordering="personalization_type")
    def personalization_badge(self, obj):
        if not obj.needs_personalization:
            return "—"
        return obj.get_personalization_type_display()

    @admin.display(description="estoque", ordering="_estoque")
    def stock_display(self, obj):
        """Somado das variantes, ou o rótulo de sob encomenda."""
        if not obj.has_variants:
            return "—"
        if obj.made_to_order:
            days = obj.production_lead_time_days
            return f"sob encomenda ({days} dias)" if days else "sob encomenda"
        return obj.available_stock

    # -- ações -------------------------------------------------------------

    @admin.action(permissions=["change"], description="Ativar produtos selecionados")
    def action_activate(self, request, queryset):
        activated = 0
        for product in queryset:
            problemas = activation_problems(product)
            if problemas:
                self.message_user(request, f"{product.sku}: {' '.join(problemas)}", level=messages.ERROR)
                continue
            product.status = ProductStatus.ACTIVE
            try:
                product.full_clean()
            except ValidationError as error:
                self.message_user(
                    request,
                    f"{product.sku}: {'; '.join(m for msgs in error.message_dict.values() for m in msgs)}",
                    level=messages.ERROR,
                )
                continue
            product.save()
            activated += 1
        if activated:
            self.message_user(request, f"{activated} produto(s) ativado(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Desativar produtos selecionados")
    def action_deactivate(self, request, queryset):
        updated = queryset.update(status=ProductStatus.INACTIVE)
        self.message_user(request, f"{updated} produto(s) desativado(s).", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Marcar como destaque")
    def action_feature(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"{updated} produto(s) em destaque.", messages.SUCCESS)

    @admin.action(permissions=["change"], description="Remover do destaque")
    def action_unfeature(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"{updated} produto(s) fora do destaque.", messages.SUCCESS)


# ---------------------------------------------------------------------------
# Variante
# ---------------------------------------------------------------------------


@admin.register(ProductVariant)
class ProductVariantAdmin(DuplicateAdminMixin):
    """A variante também tem tela própria.

    O inline dentro do produto serve para cadastrar; esta tela serve para
    operar — procurar por SKU, conferir estoque, corrigir preço de uma opção
    sem abrir o produto inteiro.
    """

    #: Duplicar uma variante é o caminho mais curto para a variante seguinte do
    #: mesmo produto: muda a cor, ou o tamanho, e o resto (preço, custo, peso,
    #: dimensões, prazo) já está certo.
    #:
    #: A cópia chega com o **mesmo produto e os mesmos eixos**, e é isso que faz
    #: "Salvar" sem alterar nada esbarrar nas duas regras que o modelo já tem:
    #: o SKU `unique` e o `clean()`, que recusa dois eixos iguais no mesmo
    #: produto ("Já existe uma variante com esta combinação neste produto").
    #:
    #: Só o estoque não vem: é a contagem física da variante original.
    duplicate_exclude = ("stock_quantity",)
    form = ProductVariantAdminForm

    list_display = (
        "sku",
        "product",
        "label_display",
        "price_display",
        "stock_display",
        "weight_display",
        "production_display",
        "is_active",
    )
    list_filter = ("is_active", "made_to_order", "allow_backorder", "color", "material",
                   "product__status")
    search_fields = ("sku", "product__sku", "product__translations__name", "size")
    autocomplete_fields = ("product", "color", "material")
    ordering = ("product", "sort_order", "id")
    list_select_related = ("product", "color", "material")
    list_per_page = 40
    readonly_fields = ("total_cost_display", "effective_margin_display", "profit_display",
                       "created_at", "updated_at")

    fieldsets = (
        ("IDENTIFICAÇÃO", {"fields": ("product", "sku", ("is_active", "sort_order"))}),
        (
            "EIXOS",
            {
                "fields": (("color", "size", "material"),),
                "description": (
                    "É o que distingue esta variante das outras do mesmo produto. "
                    "Produto de opção única pode ter os três vazios."
                ),
            },
        ),
        (
            "PREÇO",
            {
                "fields": ("pricing_mode", "sale_price", "profit_margin",
                           "effective_margin_display", "profit_display"),
                "description": (
                    "Escolha em <b>definir preço por</b> qual valor você digita. "
                    "O outro é calculado automaticamente ao salvar."
                ),
            },
        ),
        ("CUSTO", {"fields": ("filament_cost", "energy_cost", "total_cost_display")}),
        (
            "ESTOQUE E PRAZO",
            {
                "fields": ("stock_quantity", "allow_backorder",
                           "made_to_order", "production_lead_time_days"),
            },
        ),
        (
            "FÍSICO",
            {
                "fields": ("weight_grams", ("width", "height", "depth"), "dimension_unit",
                           "print_time"),
                "description": "O <b>peso</b> desta variante é o que calcula o frete.",
            },
        ),
        ("AUDITORIA", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        """As traduções do produto vêm juntas.

        A coluna do produto imprime o nome traduzido; sem este prefetch, cada
        linha da lista custa uma consulta a mais.
        """
        return super().get_queryset(request).prefetch_related(
            "product__translations", *color_prefetches(), *variant_option_prefetches()
        )

    @admin.display(description="opção")
    def label_display(self, obj):
        return obj.label or "—"

    @admin.display(description="preço", ordering="sale_price")
    def price_display(self, obj):
        if obj.sale_price is None:
            return "—"
        return f"{obj.currency_symbol} {obj.sale_price}"

    @admin.display(description="estoque", ordering="stock_quantity")
    def stock_display(self, obj):
        if obj.made_to_order:
            return "sob encomenda"
        return obj.stock_quantity

    @admin.display(description="peso", ordering="weight_grams")
    def weight_display(self, obj):
        return "—" if obj.weight_grams is None else f"{obj.weight_grams.normalize():f} g"

    @admin.display(description="produção", ordering="production_lead_time_days")
    def production_display(self, obj):
        days = obj.production_lead_time_days
        return "—" if not days else f"{days} d"

    @admin.display(description="custo")
    def total_cost_display(self, obj):
        return f"{obj.currency_symbol} {obj.total_cost}"

    @admin.display(description="margem real do preço gravado")
    def effective_margin_display(self, obj):
        margin = obj.effective_margin
        return "—" if margin is None else f"{margin}%"

    @admin.display(description="lucro por unidade")
    def profit_display(self, obj):
        value = obj.profit
        return "—" if value is None else f"{obj.currency_symbol} {value}"
