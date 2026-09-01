"""Peças reutilizáveis do Django Admin."""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import ActionLocation
from django.core.exceptions import NON_FIELD_ERRORS, ObjectDoesNotExist, ValidationError
from django.db import models
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import urlencode

from apps.core.constants import DEFAULT_LANGUAGE
from apps.core.utils import unique_slugify


class TranslatedSlugAdminMixin(admin.ModelAdmin):
    """Preenche o slug a partir do nome em português quando ele fica em branco.

    O nome vive na tabela de traduções, que só é gravada depois do objeto
    principal — por isso a geração acontece em ``save_related`` e não em
    ``save_model``. O ``save()`` do modelo já gravou um slug provisório
    (a partir do SKU) para satisfazer as restrições de banco.
    """

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        if form.cleaned_data.get("slug"):
            return

        instance = form.instance
        instance.refresh_translations()
        name = instance.tr("name", language=DEFAULT_LANGUAGE.value, default="")
        if not name:
            return

        new_slug = unique_slugify(instance, name)
        if new_slug != instance.slug:
            instance.slug = new_slug
            instance.save(update_fields=["slug"])


class AuditUserAdminMixin(admin.ModelAdmin):
    """Registra quem criou e quem alterou o objeto."""

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class RequiredDefaultLanguageInlineFormSet(forms.BaseInlineFormSet):
    """Exige a tradução no idioma padrão e proíbe idiomas repetidos.

    Usado pelos inlines de tradução cujo conteúdo é sempre obrigatório
    (título de seção, título de banner). O banco já garante a unicidade por
    idioma; aqui a mensagem chega antes, no formulário.
    """

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

        if DEFAULT_LANGUAGE.value not in languages:
            raise ValidationError("A tradução em português é obrigatória.")


class UniqueLanguageInlineFormSet(forms.BaseInlineFormSet):
    """Proíbe idiomas repetidos, mas **não** exige o português.

    Para conteúdo que pode legitimamente não existir: um banner que é só arte
    não tem título em idioma nenhum, e obrigá-lo a uma linha de tradução vazia
    seria burocracia sem leitor.

    A unicidade o banco já garante; aqui a mensagem chega antes, no formulário,
    em vez de virar um erro de constraint.
    """

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


class PartialSafeModelForm(forms.ModelForm):
    """ModelForm tolerante a erros de ``clean()`` em campos ausentes do form.

    O ``clean()`` do modelo valida o objeto inteiro, mas formulários parciais
    (a edição em lote da changelist) só têm alguns campos. Sem isto, um erro
    apontando para um campo fora do formulário derruba a página com
    ``ValueError``. Aqui o erro é preservado e mostrado como erro geral da
    linha — nunca engolido.
    """

    def _update_errors(self, errors):
        if hasattr(errors, "error_dict"):
            remapped: dict[str, list] = {}
            for field, messages in errors.error_dict.items():
                key = field if (field in self.fields or field == NON_FIELD_ERRORS) else NON_FIELD_ERRORS
                remapped.setdefault(key, []).extend(messages)
            errors = ValidationError(remapped)
        super()._update_errors(errors)


# ---------------------------------------------------------------------------
# Duplicação de cadastros
# ---------------------------------------------------------------------------

#: O que a tela de criação recebe para saber de quem está copiando.
#:
#: Vai só a chave, e nada além dela: os valores são lidos do banco, no
#: servidor. Acrescentar `&sale_price=1` à mão na barra de endereços não muda
#: o formulário, e a permissão é conferida de novo aqui — nunca deduzida do
#: que o navegador mandou.
DUPLICATE_PARAM = "_duplicar"


def duplicable_values(obj, exclude=()):
    """Os valores de ``obj`` que servem de ponto de partida para um cadastro novo.

    Ficam de fora, sempre:

    * a chave primária — o registro novo terá a sua;
    * o que o Django já marca como não editável, que neste projeto é
      exatamente a auditoria: ``created_at``, ``updated_at``, ``created_by``,
      ``updated_by``;
    * arquivos — dois registros apontando para o mesmo caminho é um vínculo
      entre eles, não uma cópia. Quem duplica envia o arquivo novo.

    E mais o que a chamada listar em ``exclude``.

    Relações muitos-para-muitos ficam de fora por omissão: nenhum dos
    cadastros que hoje têm "Duplicar" tem uma editável, e copiar uma lista de
    vínculos sem decidir caso a caso é o oposto do que se quer aqui.
    """
    valores = {}
    for field in obj._meta.concrete_fields:
        if field.primary_key or not field.editable:
            continue
        if isinstance(field, models.FileField):
            continue
        if field.name in exclude or field.attname in exclude:
            continue
        # Chave: o nome do campo (`category`). Valor: a chave estrangeira crua
        # (`category_id`) — é o que o `ModelChoiceField` espera como inicial, e
        # poupa uma consulta por relação.
        valores[field.name] = getattr(obj, field.attname)
    return valores


class DuplicateAdminMixin(admin.ModelAdmin):
    """"Duplicar": abre a tela de **criação** já preenchida com outro registro.

    O clique não grava nada. O que existe depois dele é um formulário de
    cadastro com valores sugeridos: o registro novo só nasce no "Salvar", e
    fechar a página não deixa rastro. É o caminho do "Adicionar" de sempre,
    com campos pré-preenchidos — sem tela paralela, sem rota de gravação
    própria, sem uma segunda ideia do que é um cadastro válido.

    ## Por que não o "Salvar como novo" do Django

    ``ModelAdmin.save_as`` existe e faz quase isto. Mas parte da tela de
    **edição** do original e grava no clique — as duas coisas que esta etapa
    não quer: a tela precisa dizer "Adicionar", e o botão não pode criar nada.

    ## Onde o botão aparece

    Em lugar nenhum novo. É uma **ação** do Admin, declarada nos dois lugares
    que o Django oferece (``ActionLocation``): a lista, onde já moram
    "Ativar"/"Desativar", e a própria tela do registro. Nenhum template é
    trocado, e a permissão vem de ``permissions=["add"]`` — o Django esconde a
    ação de quem não pode criar, e a tela de criação recusa de novo quem
    montar a URL à mão. Duplicar é criar.

    ## O que é copiado

    Todo campo editável do modelo, menos o que ``duplicable_values`` já deixa
    de fora e o que cada Admin listar em ``duplicate_exclude``.

    Os filhos vêm de ``duplicate_inlines`` — e só os declarados ali. Cada linha
    de origem vira um formulário **novo** do formset, sem ``id`` e sem vínculo
    com a linha copiada: salvar cria linhas próprias, e o original não é lido
    nem tocado depois que a tela foi montada.

    ## O que impede a cópia idêntica

    Nada escrito aqui: quem recusa é a regra que o modelo já tem — ``unique``,
    ``UniqueConstraint`` ou o ``clean()``. É de propósito. Uma segunda checagem
    neste arquivo seria uma segunda opinião sobre o que é "o mesmo registro", e
    valeria só dentro do Admin.

    Por isso os campos de identidade (SKU, código, nome único) **são**
    copiados: é o que faz "Duplicar" seguido de "Salvar", sem mexer em nada,
    esbarrar na regra e voltar com a mensagem certa. Só o ``slug`` sai vazio —
    o projeto o gera no ``save()``, e copiá-lo renderia um "-2" silencioso,
    que é exatamente o que não se quer.
    """

    #: Campos que não acompanham a cópia, além dos que já ficam de fora.
    duplicate_exclude: tuple[str, ...] = ()

    #: ``{model do inline: campos daquele inline que não são copiados}``.
    #: Inline que não estiver aqui não é copiado.
    duplicate_inlines: dict = {}

    actions = ["duplicate_action"]

    # -- a ação -------------------------------------------------------------

    @admin.action(
        description="Duplicar — abre a tela de criação preenchida",
        permissions=["add"],
        location=[ActionLocation.CHANGE_LIST, ActionLocation.CHANGE_FORM],
    )
    def duplicate_action(self, request, queryset):
        """Manda para a tela de criação. Não grava.

        ``queryset`` já vem recortado pelo ``get_queryset`` do usuário — o
        Admin não deixa uma ação alcançar o que a pessoa não enxerga.
        """
        escolhidos = list(queryset[:2])
        if len(escolhidos) != 1:
            self.message_user(
                request,
                "Escolha um registro por vez para duplicar.",
                messages.WARNING,
            )
            return None
        return HttpResponseRedirect(self.duplicate_url(escolhidos[0]))

    def duplicate_url(self, obj) -> str:
        rota = f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_add"
        return f"{reverse(rota)}?{urlencode({DUPLICATE_PARAM: obj.pk})}"

    # -- de quem se está copiando -------------------------------------------

    def duplicate_source(self, request):
        """O registro de origem desta tela, ou ``None``.

        A chave vem da URL; todo o resto vem do banco. Fora do ``get_queryset``
        do usuário, ou sem permissão de ver o model, a tela de criação abre
        vazia — nunca com os dados, e sem dizer se a chave existe.

        O resultado fica na requisição, e não em ``self``: a instância do
        ModelAdmin é uma só, compartilhada por todas as requisições do
        processo.
        """
        cache = getattr(request, "_jd_duplicate_source", None)
        if cache is None:
            cache = request._jd_duplicate_source = {}
        if self.model not in cache:
            cache[self.model] = self._load_duplicate_source(request)
        return cache[self.model]

    def _load_duplicate_source(self, request):
        chave = request.GET.get(DUPLICATE_PARAM)
        if not chave:
            return None
        try:
            origem = self.get_queryset(request).get(pk=chave)
        except (ObjectDoesNotExist, ValidationError, ValueError, TypeError):
            return None
        if not self.has_view_permission(request, origem):
            return None
        return origem

    # -- o formulário preenchido --------------------------------------------

    def get_changeform_initial_data(self, request):
        inicial = super().get_changeform_initial_data(request)
        # O Django copia a query string inteira para o inicial; este parâmetro
        # é endereço, não valor de campo.
        inicial.pop(DUPLICATE_PARAM, None)

        origem = self.duplicate_source(request)
        if origem is not None:
            inicial.update(duplicable_values(origem, self.duplicate_exclude))
        return inicial

    # -- os filhos ----------------------------------------------------------

    def get_formsets_with_inlines(self, request, obj=None):
        for FormSet, inline in super().get_formsets_with_inlines(request, obj):
            linhas = self._duplicate_inline_initial(request, inline, FormSet)
            if linhas:
                # `extra` decide quantos formulários em branco o formset
                # desenha, e o `initial` só alcança os que existem. O `min_num`
                # já garante os primeiros: descontá-lo evita uma linha vazia
                # sobrando no fim. O `max` preserva a generosidade de quem já
                # abre com um formulário por idioma.
                FormSet.extra = max(FormSet.extra, len(linhas) - FormSet.min_num)
                self._duplicate_rows(request)[inline.model] = linhas
            yield FormSet, inline

    def get_formset_kwargs(self, request, obj, inline, prefix):
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        linhas = self._duplicate_rows(request).get(inline.model)

        # Só fora do POST. Enviado o formulário, o formset é preenchido pelo
        # que veio da tela; um `initial` aqui faria `has_changed()` devolver
        # False para a linha que o usuário não tocou, e o Django pula essas em
        # `save_new_objects` — a cópia sumiria sem erro nenhum.
        if linhas and request.method != "POST":
            kwargs["initial"] = linhas
        return kwargs

    def _duplicate_rows(self, request) -> dict:
        cache = getattr(request, "_jd_duplicate_rows", None)
        if cache is None:
            cache = request._jd_duplicate_rows = {}
        return cache

    def _duplicate_inline_initial(self, request, inline, formset_class) -> list:
        origem = self.duplicate_source(request)
        if origem is None:
            return []

        excluidos = self.duplicate_inlines.get(inline.model)
        if excluidos is None:  # inline não declarado: não acompanha a cópia
            return []
        if not inline.has_add_permission(request, None):
            return []

        fk = formset_class.fk
        filhos = inline.get_queryset(request).filter(**{fk.name: origem})
        fora = tuple(excluidos) + (fk.name, fk.attname)
        return [duplicable_values(filho, fora) for filho in filhos]
