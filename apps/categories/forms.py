"""O formulário por trás do modal de categorias.

Uma categoria é duas tabelas: `Category` (pai, slug, ordem, ativa) e
`CategoryTranslation` (nome e descrição por idioma). O modal grava as duas de
uma vez, e é aqui que as regras moram — as mesmas que o inline do Admin já
aplicava, para não existirem duas opiniões sobre o que é uma categoria válida:

* **nome em português obrigatório.** É a identidade: o menu, o slug e o
  fallback dos outros idiomas saem dele;
* **dois nomes iguais no mesmo nível não podem existir.** O cliente escolhe
  pelo nome, dentro do lugar onde ele está; duas "Gatos" dentro de "Animais"
  seriam duas entradas idênticas no menu com endereços diferentes. A regra
  não cabe numa `UniqueConstraint` porque o nome está numa tabela e o nível
  na outra;
* **hierarquia.** Pai igual a si mesma, ciclo e profundidade acima do limite
  são recusados pelo `clean()` do modelo, que este formulário chama;
* **slug.** Em branco, é gerado a partir do nome em português — a mesma
  função (`unique_slugify`) que o `TranslatedSlugAdminMixin` usa na ficha.

Os idiomas não são uma lista fixa: vêm de `SiteLanguage` (os que a loja
oferece hoje), como no resto do Admin.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.categories.models import Category, CategoryTranslation
from apps.core.constants import DEFAULT_LANGUAGE, Language
from apps.core.i18n import normalize_language
from apps.core.models import SiteLanguage
from apps.core.utils import unique_slugify


def modal_languages() -> list[str]:
    """Os idiomas do modal: os que a loja oferece hoje, o português à frente.

    Os códigos vêm de `SiteLanguage` (que fala «pt-br», «fr») e são
    normalizados para idioma de **conteúdo** («pt», «fr»), que é o que a
    tabela de traduções guarda — a mesma conversão do modal de opções do
    produto.

    Uma categoria já traduzida num idioma que a loja desligou continua com a
    tradução no banco: ela não é apagada aqui, só não aparece para edição.
    """
    codes = [DEFAULT_LANGUAGE.value]
    for code in SiteLanguage.objects.active().values_list("code", flat=True):
        normalizado = normalize_language(code)
        if normalizado not in codes:
            codes.append(normalizado)
    return codes


def language_label(code: str) -> str:
    """«Português», «Francês» — o rótulo que o Admin já usa para o idioma."""
    try:
        return Language(code).label
    except ValueError:  # idioma fora da lista de conteúdo: mostra o código
        return code.upper()


class CategoryModalForm(forms.ModelForm):
    """Cria e edita uma categoria inteira — identificação e traduções."""

    name = forms.CharField(
        label="Nome (português)",
        max_length=200,
        error_messages={"required": "O nome em português é obrigatório."},
    )

    class Meta:
        model = Category
        fields = ("parent", "slug", "sort_order", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.languages = modal_languages()
        self.fields["parent"].required = False
        self.fields["slug"].required = False
        self.fields["sort_order"].required = False

        # Um par de campos por idioma. O nome em português vem do campo `name`
        # acima (a identidade da categoria); aqui ficam os outros idiomas e as
        # descrições de todos, inclusive a portuguesa.
        traducoes = self.instance.translations_by_language() if self.instance.pk else {}
        for code in self.languages:
            atual = traducoes.get(code)
            if code != DEFAULT_LANGUAGE.value:
                self.fields[f"name_{code}"] = forms.CharField(
                    label=f"Nome ({code})",
                    max_length=200,
                    required=False,
                    initial=getattr(atual, "name", ""),
                )
            self.fields[f"description_{code}"] = forms.CharField(
                label=f"Descrição ({code})",
                required=False,
                widget=forms.Textarea,
                initial=getattr(atual, "description", ""),
            )
        if self.instance.pk and not self.initial.get("name"):
            self.initial["name"] = self.instance.name_in(DEFAULT_LANGUAGE.value)

    # -- regras ------------------------------------------------------------

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean_slug(self):
        return (self.cleaned_data.get("slug") or "").strip()

    def clean_sort_order(self):
        valor = self.cleaned_data.get("sort_order")
        return 0 if valor in (None, "") else valor

    def clean_parent(self):
        """O pai não pode ser a própria categoria nem um descendente dela.

        O `clean()` do modelo recusa o ciclo, mas só enxerga a corrente para
        cima. Escolher um **filho** como pai fecha o ciclo pelo outro lado, e é
        o engano fácil de cometer numa lista de categorias.
        """
        parent = self.cleaned_data.get("parent")
        if parent is None or not self.instance.pk:
            return parent
        if parent.pk == self.instance.pk:
            raise ValidationError("Uma categoria não pode ser pai dela mesma.")
        node = parent
        vistos = set()
        while node is not None and node.pk not in vistos:
            vistos.add(node.pk)
            if node.parent_id == self.instance.pk:
                raise ValidationError(
                    "Esta categoria está acima da escolhida: mover para lá criaria um ciclo."
                )
            node = node.parent
        return parent

    def clean(self):
        dados = super().clean()
        nome = dados.get("name") or ""
        if not nome:
            return dados

        irmas = Category.objects.filter(
            parent=dados.get("parent"),
            translations__language=DEFAULT_LANGUAGE.value,
            translations__name__iexact=nome,
        )
        if self.instance.pk:
            irmas = irmas.exclude(pk=self.instance.pk)
        if irmas.exists():
            self.add_error(
                "name",
                f"Já existe uma categoria com o nome “{nome}” neste nível. "
                "Duas iguais apareceriam duas vezes no menu, com endereços diferentes.",
            )
        return dados

    def _post_clean(self):
        # O nome ainda não está no banco quando o modelo valida, e o `save()`
        # do modelo precisa dele para gerar o slug de uma categoria nova.
        self.instance._pending_name = self.cleaned_data.get("name", "")
        super()._post_clean()

    # -- gravação ----------------------------------------------------------

    @transaction.atomic
    def save(self, commit=True):
        nova = self.instance.pk is None
        categoria = super().save(commit=False)
        categoria.sort_order = self.cleaned_data.get("sort_order") or 0
        pedido = self.cleaned_data.get("slug") or ""
        nome = self.cleaned_data["name"]
        categoria.slug = pedido or (categoria.slug if not nova else "")
        categoria.save()

        self._save_translations(categoria, nome)
        categoria.refresh_translations()

        # Slug em branco: gerado a partir do nome, como na ficha completa. Só
        # depois das traduções, que é de onde o nome sai.
        if not pedido and (nova or not categoria.slug):
            novo = unique_slugify(categoria, nome)
            if novo != categoria.slug:
                categoria.slug = novo
                categoria.save(update_fields=["slug"])
        return categoria

    def _save_translations(self, categoria, nome_pt):
        for code in self.languages:
            if code == DEFAULT_LANGUAGE.value:
                nome = nome_pt
            else:
                nome = (self.cleaned_data.get(f"name_{code}") or "").strip()
            descricao = (self.cleaned_data.get(f"description_{code}") or "").strip()

            if not nome:
                # Sem nome não há tradução: o idioma volta a usar o português.
                # A descrição sozinha não se sustenta — ela é daquele nome.
                CategoryTranslation.objects.filter(master=categoria, language=code).delete()
                continue

            CategoryTranslation.objects.update_or_create(
                master=categoria,
                language=code,
                defaults={"name": nome, "description": descricao},
            )
