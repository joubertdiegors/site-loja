"""Peças reutilizáveis do Django Admin."""

from django import forms
from django.contrib import admin
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

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
