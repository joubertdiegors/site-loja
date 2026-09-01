"""Configuração do Django Admin do catálogo.

O formulário de produto é organizado em seções (``fieldsets``) e usa três
inlines: traduções, variantes e mídias.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.decorators import method_decorator
from django.utils.html import format_html, format_html_join
from django.views.decorators.http import require_POST

from apps.catalog.models import (
    Brand,
    Color,
    ColorTranslation,
    Material,
    MaterialTranslation,
    MediaType,
    Product,
    ProductMedia,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
)
from apps.core.admin_mixins import (
    AuditUserAdminMixin,
    DuplicateAdminMixin,
    TranslatedSlugAdminMixin,
)
from apps.core.constants import DEFAULT_LANGUAGE, Language

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


class MaterialTranslationInline(NameTranslationInline):
    model = MaterialTranslation


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
    list_display = ("name", "translations_display", "slug", "description", "is_active")
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


class ProductVariantModalForm(forms.ModelForm):
    """O formulário que o modal envia — os mesmos campos do inline.

    Existe separado do inline porque o inline é um formset (vem com `id`,
    `DELETE` e prefixo indexado) e aqui chega **uma** variante, com os nomes
    limpos. A validação é a do modelo: `ProductVariant.clean()` continua sendo
    quem decide o que é uma variante válida.
    """

    class Meta:
        model = ProductVariant
        fields = VARIANT_FIELDS


class ProductVariantInlineFormSet(forms.BaseInlineFormSet):
    """Um produto ativo precisa de pelo menos uma variante ativa.

    A checagem mora aqui e não no ``clean()`` do produto porque o admin grava
    o produto **antes** dos inlines: no cadastro, o modelo ainda não enxerga a
    variante que está sendo criada na mesma tela. O formset enxerga.
    """

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
            .prefetch_related("color__translations", "material__translations")
        )


class ProductMediaInline(admin.TabularInline):
    """As fotos do produto — e, opcionalmente, a variante de cada uma.

    Vincular a foto a uma variante faz dela a imagem principal quando o cliente
    escolhe aquela opção. Em branco, é foto geral: continua na galeria e vale
    para todas as variantes. Nenhum arquivo é duplicado — é a mesma linha.
    """

    model = ProductMedia
    template = "admin/catalog/edit_inline/media_tabular.html"
    classes = ("collapse",)
    extra = 1
    fields = ("preview", "file", "media_type", "variant", "alt_text", "sort_order", "is_primary")
    readonly_fields = ("preview",)
    verbose_name = "mídia"
    verbose_name_plural = "MÍDIA — fotos, vídeos e GIFs"

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
            return "—"
        if obj.media_type in {MediaType.IMAGE, MediaType.GIF}:
            return format_html(
                '<img src="{}" style="max-height:70px;border-radius:4px" />', obj.file.url
            )
        return format_html('<a href="{}" target="_blank">abrir vídeo</a>', obj.file.url)


# ---------------------------------------------------------------------------
# Produto
# ---------------------------------------------------------------------------


#: A ordem das seções na tela do produto, de cima para baixo. Fieldsets entram
#: pelo nome; inlines, pelo modelo. É a única lista que precisa mudar quando a
#: ordem mudar — nem o template nem os `fieldsets` sabem dela.
#:
#: AUDITORIA não está aqui de propósito: ela é a última da página e é desenhada
#: depois dos inlines, em `after_related_objects`.
SECTION_ORDER = (
    "IDENTIFICAÇÃO",
    "CLASSIFICAÇÃO",
    "PERSONALIZAÇÃO",
    ProductTranslation,  # CONTEÚDO
    ProductVariant,  # VARIANTES
    ProductMedia,  # MÍDIA
)


@admin.register(Product)
class ProductAdmin(DuplicateAdminMixin, TranslatedSlugAdminMixin, AuditUserAdminMixin):
    #: Quem manda na ordem da tela é `SECTION_ORDER`, não esta lista — ela só
    #: diz quais inlines existem. Mesmo assim vão na ordem final, para quem
    #: ler o arquivo não precisar cruzar os dois lugares.
    inlines = [ProductTranslationInline, ProductVariantInline, ProductMediaInline]
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
    duplicate_inlines = {
        ProductTranslation: (),
        ProductVariant: ("stock_quantity",),
    }

    list_display = (
        "sku",
        "display_name",
        "category",
        "status_badge",
        "price_display",
        "stock_display",
        "variant_count",
        "personalization_badge",
        "is_featured",
        "updated_at",
    )
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
            "IDENTIFICAÇÃO",
            {
                "classes": ("collapse", "start-open"),
                "fields": ("sku", "status", "slug"),
                "description": (
                    "Este cadastro é a definição <b>genérica</b> do produto. "
                    "Preço, estoque, peso, dimensões e prazo de produção ficam em "
                    "<b>VARIANTES</b> — cada variante é uma unidade vendável. "
                    "O conteúdo traduzível (nome e descrições) fica em <b>CONTEÚDO</b>."
                ),
            },
        ),
        (
            "CLASSIFICAÇÃO",
            {
                "classes": ("collapse", "start-open"),
                # `is_featured` e `featured_order` vieram de CONFIGURAÇÕES, que
                # deixou de existir. Vieram **os dois**: são a mesma
                # funcionalidade — se o produto está em destaque, e em que
                # posição. Deixar a ordem para trás a tornaria ineditável, e é
                # ela que ordena a prateleira da home e a primeira passada das
                # sugestões.
                "fields": ("category", "brand", "currency", "is_featured", "featured_order"),
            },
        ),
        (
            "PERSONALIZAÇÃO",
            {
                "classes": ("collapse", "start-open"),
                "fields": ("personalization_type", "personalization_text_limit"),
                "description": (
                    "Define se o cliente deverá fornecer uma foto, um texto ou escolher "
                    "entre os dois antes de adicionar o produto ao carrinho. "
                    "É característica do produto — não crie categoria para isso."
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
        )

    # -- os modais (variantes e conteúdo) -----------------------------------

    def get_urls(self):
        """Duas rotas para o modal: gravar e excluir uma variante.

        Ficam sob a URL do produto de propósito — quem pode editar o produto é
        quem pode mexer nas variantes dele, e `admin_view` já exige sessão de
        equipe. A permissão fina é conferida em cada uma.
        """
        extra = [
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
        ]
        return extra + super().get_urls()

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

        return {
            "id": variant.pk,
            "fields": {
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

        form = ProductVariantModalForm(request.POST, instance=instance)
        form.instance.product = product

        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "errors": {campo: list(msgs) for campo, msgs in form.errors.items()}},
                status=400,
            )

        criada = instance is None
        variant = form.save()

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
        for chave in SECTION_ORDER:
            item = por_nome.pop(chave, None) if isinstance(chave, str) else por_modelo.pop(chave, None)
            if item is not None:
                layout.append(item)

        # O que sobrou (fieldset ou inline não listado) vai para o fim, na
        # ordem original — melhor no lugar errado do que invisível.
        layout.extend(item for item in por_nome.values())
        layout.extend(item for item in por_modelo.values())
        return layout

    def render_change_form(self, request, context, add=False, change=False, **kwargs):
        """Entrega a ordem das seções e a AUDITORIA para o template.

        A AUDITORIA sai dos `fieldsets` e é desenhada no bloco
        `after_related_objects` do próprio Django, no fim da página. Só existe
        na edição: um produto que ainda não foi criado não tem histórico.
        """
        context["product_layout"] = self._page_layout(context)

        original = context.get("original")
        if original is not None and original.pk:
            context["audit_rows"] = [
                ("Criado em", original.created_at),
                ("Criado por", original.created_by),
                ("Atualizado em", original.updated_at),
                ("Atualizado por", original.updated_by),
            ]
        return super().render_change_form(request, context, add=add, change=change, **kwargs)

    def get_queryset(self, request):
        return super().get_queryset(request).for_listing().prefetch_related("variants")

    # -- colunas calculadas ------------------------------------------------

    @admin.display(description="nome")
    def display_name(self, obj):
        return obj.name_in(DEFAULT_LANGUAGE.value)

    @admin.display(description="status", ordering="status")
    def status_badge(self, obj):
        colors = {
            ProductStatus.DRAFT: "#8a8a8a",
            ProductStatus.ACTIVE: "#1a7f37",
            ProductStatus.INACTIVE: "#b42318",
        }
        return format_html(
            '<span style="color:{};font-weight:600">{}</span>',
            colors.get(obj.status, "#000"),
            obj.get_status_display(),
        )

    @admin.display(description="preço")
    def price_display(self, obj):
        """Preço das variantes: um valor, ou a faixa quando diferem."""
        low, high = obj.price_range
        if low is None:
            return format_html('<span style="color:#b42318">{}</span>', "sem variante")
        if low == high:
            return f"{obj.currency_symbol} {low}"
        return f"{obj.currency_symbol} {low} – {high}"

    @admin.display(description="variantes")
    def variant_count(self, obj):
        """Quantas variantes — e um alerta quando não há nenhuma."""
        total = len(obj.variants.all())
        ativas = len(obj.active_variants())
        if total == 0:
            return format_html(
                '<span style="color:#b42318;font-weight:600">{}</span>', "nenhuma"
            )
        if ativas == total:
            return total
        return f"{ativas}/{total}"

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
        return super().get_queryset(request).prefetch_related("product__translations")

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
