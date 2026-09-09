"""Configuração do Django Admin do catálogo.

O formulário de produto é organizado em seções (``fieldsets``) e usa três
inlines: traduções, variantes e mídias.
"""

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Q, RestrictedError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.catalog import sku as sku_rules

from apps.catalog.models import (
    Brand,
    Color,
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
    copy_product_content,
    copy_product_options,
    product_content_copy_plan,
    variant_option_prefetches,
)
from apps.categories.models import Category
from apps.core.admin_mixins import (
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


@admin.register(Color)
class ColorAdmin(DuplicateAdminMixin):
    """A cor é uma só; o que muda por idioma é o nome que o cliente lê."""

    #: Mesma decisão do material: `name` copiado (é a identidade `unique`),
    #: `slug` vazio (o `save()` o gera). O HEX acompanha — duas cores próximas
    #: partem do mesmo tom e é justamente isso que se quer ajustar.
    duplicate_exclude = ("slug",)
    duplicate_inlines = {ColorTranslation: ()}

    inlines = [ColorTranslationInline]
    list_display = ("name", "swatch", "translations_display", "hex_code", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "hex_code", "translations__name")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "hex_code", "is_active")

    class Media:
        css = {"all": ("admin/css/jdprint_admin.css",)}

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="amostra")
    def swatch(self, obj):
        if not obj.hex_code:
            return "—"
        return format_html(
            '<span style="display:inline-block;width:22px;height:22px;border-radius:4px;'
            'border:1px solid #bbb;background:{}"></span>',
            obj.hex_code,
        )

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
CONTENT_FIELDS = ("language", "name", "short_description", "description", "extra_information")


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
        # (e para o molde do «Adicionar variante»). Produto novo não tem —
        # salvo na duplicação (etapa 3F), em que o `ProductAdmin` passa as
        # opções do produto de origem para as linhas copiadas já virem com
        # as escolhas de cada variante.
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
            .prefetch_related("color__translations", "material__translations", *variant_option_prefetches())
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
        if modo == ColorMode.SINGLE and len(vivas) > 1:
            raise ValidationError(
                "No modo «Uma cor» cadastre uma cor só — para várias, use «Multicolorido»."
            )
        if modo in (ColorMode.SINGLE, ColorMode.MULTI) and not vivas:
            raise ValidationError(
                "Cadastre ao menos uma cor na paleta — ou mude o modo de cores para "
                "«Não se aplica», «Cores à escolha» ou «Opção comercial»."
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
        if cor is not None and getattr(cor, "hex_code", ""):
            option["attrs"]["data-hex"] = cor.hex_code
        return option


class ProductColorInline(admin.TabularInline):
    """PALETA DE CORES: a descrição visual do produto. Não cria variantes."""

    model = ProductColor
    formset = ProductColorInlineFormSet
    template = "admin/catalog/edit_inline/media_tabular.html"
    #: Linhas compactas (cor, ordem, remover) no desktop; um card por linha
    #: nas telas estreitas (`jd-cards`, jdprint_forms.css).
    classes = ("collapse", "jd-cards", "jd-compact-rows")
    extra = 0
    fields = ("color", "sort_order")
    verbose_name = "cor"
    verbose_name_plural = "PALETA DE CORES — as cores do produto (não cria variantes)"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "color":
            kwargs["queryset"] = Color.objects.filter(is_active=True).prefetch_related("translations")
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categorias = Category.objects.prefetch_related("translations").order_by("sort_order", "slug")
        self.fields["category"].queryset = categorias
        self.fields["category"].label_from_instance = lambda c: c.full_path()

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        if sku and Product.objects.filter(sku=sku).exists():
            raise ValidationError("Já existe um produto com este SKU.")
        return sku

    def clean(self):
        dados = super().clean()
        if dados.get("status") == ProductStatus.ACTIVE:
            if dados.get("category") is None:
                self.add_error("category", "Um produto ativo precisa de categoria.")
            if dados.get("sale_price") is None:
                self.add_error(
                    "status",
                    "Um produto ativo precisa de uma variante com preço: informe o "
                    "preço ou crie como rascunho e ative depois.",
                )
        if dados.get("stock_quantity") is not None and dados.get("sale_price") is None:
            self.add_error("sale_price", "Informe o preço para criar a primeira variante.")
        return dados

    def suggested_sku(self, reserved=()) -> str:
        return sku_rules.suggest_product_sku(
            self.cleaned_data.get("category"), self.cleaned_data.get("name", ""), reserved
        )

    def save(self, user):
        """Produto + nome em português + slug (+ a primeira variante).

        O SKU digitado é respeitado; em branco, a sugestão entra e, se o banco
        recusar (outro cadastro gravou o mesmo número no meio do caminho), a
        sequência seguinte é tentada — ver `sku_rules.com_sku_livre`.
        """
        dados = self.cleaned_data
        manual = dados.get("sku") or ""

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
                if dados.get("sale_price") is not None:
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
    #: `sku` é copiado: é a identidade do produto (`unique`), e é o que faz
    #: "Duplicar" + "Salvar" sem alterar nada parar em "Produto com este SKU já
    #: existe" em vez de criar um gêmeo. `slug` sai vazio — quem o gera é o
    #: `TranslatedSlugAdminMixin`, a partir do nome em português.
    #:
    #: A auditoria (`created_at`, `created_by`, ...) nunca entra: o Django a
    #: marca `editable=False`, e `duplicable_values` respeita isso.
    duplicate_exclude = ("slug",)

    #: Dos três inlines, dois acompanham a cópia:
    #:
    #: **CONTEÚDO** — é o motivo de duplicar um produto. Quatro idiomas de
    #: nome, descrição curta, descrição e informações extras é o trabalho que
    #: se quer reaproveitar.
    #:
    #: **VARIANTES** — preço, custo, peso, dimensões e tempo de impressão são a
    #: base de uma peça parecida. O SKU vai junto (mesma razão do produto: sem
    #: ele a cópia idêntica não seria recusada), mas o **estoque não**: é
    #: quantidade de peça física, do produto original. Copiar "10 em estoque"
    #: para um produto que ainda não existe faria a loja vender dez unidades
    #: que ninguém imprimiu — e o campo aparece na tela zerado, para quem
    #: cadastra informar o número certo.
    #:
    #: **MÍDIA** fica de fora: são arquivos. Duas linhas apontando para a mesma
    #: foto é um vínculo entre os dois produtos, não uma cópia.
    #: **CORES** e **MATERIAIS** (etapa 2B) acompanham: são descrição da peça,
    #: e a peça parecida costuma ter as mesmas. Apontam para as mesmas `Color`
    #: e `Material` — nada é copiado nesses cadastros.
    duplicate_inlines = {
        ProductTranslation: (),
        ProductVariant: ("stock_quantity",),
        ProductColor: (),
        ProductMaterialComposition: (),
    }

    #: A tabela operacional: o que identifica, o que vende e o que fazer.
    list_display = (
        "id",
        "sku",
        "display_name",
        "category",
        "status_badge",
        "price_display",
        "stock_display",
        "variant_count",
        "acoes",
    )
    list_display_links = ("sku", "display_name")
    list_filter = (
        "status",
        "personalization_type",
        "is_featured",
        "category",
        "brand",
        "currency",
        "variants__material",
    )
    search_fields = (
        "sku",
        "slug",
        "translations__name",
        "translations__short_description",
        "variants__sku",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 30
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
                    "abaixo; «Opção comercial» mantém a cor como eixo de cada VARIANTE."
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
        css = {"all": ("admin/css/jdprint_admin.css",)}
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
        """Nome, categoria, SKU (sugerido) e status. Cadastrar primeiro."""
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = QuickProductForm(request.POST or None)
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
            "title": "Novo produto",
            "form": form,
            "variant_sku_preview": "SKU-V01",
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

    # -- duplicar: sem colisão de SKU --------------------------------------------

    def get_changeform_initial_data(self, request):
        """A cópia nasce com o SKU seguinte livre (VASO-01 -> VASO-02).

        O original não é tocado; só a sugestão muda, e ela continua editável.
        As variantes copiadas acompanham a nova identidade (VASO-02-V01…), em
        `_duplicate_inline_initial`.
        """
        inicial = super().get_changeform_initial_data(request)
        origem = self.duplicate_source(request)
        if origem is not None:
            inicial["sku"] = sku_rules.next_product_sku_after(origem.sku)
            request._jd_duplicate_sku = inicial["sku"]
        return inicial

    def _duplicate_inline_initial(self, request, inline, formset_class) -> list:
        linhas = super()._duplicate_inline_initial(request, inline, formset_class)
        if inline.model is ProductVariant and linhas:
            origem = self.duplicate_source(request)
            novo_sku = getattr(request, "_jd_duplicate_sku", None) or sku_rules.next_product_sku_after(
                origem.sku
            )
            reservados: set[str] = set()
            # Etapa 3F: cada linha copiada traz as escolhas da variante de
            # origem nas opções adicionais (`opt_<id da opção de origem>`).
            # A mesma consulta — e a mesma ordem — que montou `linhas`, já com
            # as escolhas no prefetch: nenhuma consulta por variante.
            variantes = list(inline.get_queryset(request).filter(product=origem))
            for linha, variante in zip(linhas, variantes):
                linha["sku"] = sku_rules.suggest_variant_sku(novo_sku, reserved=reservados)
                reservados.add(linha["sku"])
                for link in variante.option_values.all():
                    linha[f"{OPTION_FIELD_PREFIX}{link.option_id}"] = link.value_id
        return linhas

    def _duplicate_options(self, request) -> list:
        """As opções do produto de origem, lidas uma vez por requisição (3F)."""
        cache = getattr(request, "_jd_duplicate_options", None)
        if cache is None:
            origem = self.duplicate_source(request)
            cache = request._jd_duplicate_options = (
                list(origem.options.prefetch_related("values").order_by("sort_order", "id"))
                if origem is not None
                else []
            )
        return cache

    def get_formset_kwargs(self, request, obj, inline, prefix):
        """Na duplicação, o formset das variantes nasce com as opções de origem.

        É o que faz os `<select>` das opções adicionais aparecerem nas linhas
        copiadas — na tela (GET) e na leitura do envio (POST). Fora da
        duplicação, e na edição, nada muda.
        """
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        # Na criação o Django passa o produto ainda não gravado (pk vazio), e
        # não `None`: é o pk que diz se esta é a tela de criação.
        if (
            getattr(obj, "pk", None) is None
            and inline.model is ProductVariant
            and self.duplicate_source(request) is not None
        ):
            kwargs["product_options"] = self._duplicate_options(request)
        return kwargs

    def save_formset(self, request, form, formset, change):
        """Ao gravar a cópia, as opções nascem no produto novo antes das variantes.

        Etapa 3F. O produto acabou de ser gravado (`save_model`) e as variantes
        ainda não: aqui as opções, os valores e as traduções da origem são
        copiados para o produto novo (`copy_product_options`) e os mapas
        entram nas linhas, para cada escolha ser religada à opção e ao valor
        novos — nunca aos da origem. Tudo dentro da transação da tela do
        admin: se qualquer coisa falhar, não sobra produto, opção nem variante.
        """
        if (
            not change
            and isinstance(formset, ProductVariantInlineFormSet)
            and formset.product_options
            and self.duplicate_source(request) is not None
        ):
            origem = self.duplicate_source(request)
            mapas = copy_product_options(origem, form.instance)
            for linha in formset.forms:
                linha.option_maps = mapas
            self.message_user(
                request,
                f"Opções adicionais copiadas de {origem.sku}: {len(mapas[0])} opção(ões) e "
                f"{len(mapas[1])} valor(es), com as traduções e as escolhas de cada variante.",
                messages.INFO,
            )
        super().save_formset(request, form, formset, change)

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
        if add:
            # Etapa 3F: na duplicação, a seção OPÇÕES ADICIONAIS diz o que vai
            # acontecer ao salvar, em vez de «salve o produto primeiro».
            origem = self.duplicate_source(request)
            if origem is not None:
                opcoes = self._duplicate_options(request)
                context["jd_duplicate_source"] = origem
                context["jd_duplicate_options"] = opcoes
                context["jd_duplicate_values"] = sum(len(o.values.all()) for o in opcoes)
        if editando:
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
        return super().get_queryset(request).for_listing().prefetch_related("variants")

    # -- colunas calculadas ------------------------------------------------

    @admin.display(description="produto")
    def display_name(self, obj):
        return obj.name_in(DEFAULT_LANGUAGE.value)

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

    @admin.display(description="preço")
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

    @admin.display(description="variantes")
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

    @admin.display(description="estoque")
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
            "product__translations", *variant_option_prefetches()
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
