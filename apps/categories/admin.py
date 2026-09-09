"""O Admin de categorias: uma lista em árvore e um modal por categoria.

## Por que uma listagem própria

Uma categoria não se lê em linha: ela se lê **dentro** de onde está. A lista
padrão do Django mostra "Gatos" e "Gatos" sem dizer que uma é de Animais e a
outra de Brinquedos, e obriga a abrir uma página inteira para trocar a ordem
ou desligar uma. Aqui a árvore aparece indentada, com filhos que recolhem, e
tudo o que se faz com frequência — buscar, filtrar, ativar, reordenar, criar —
acontece na mesma tela.

## O que continua sendo o Admin de sempre

* a ficha completa (`add`/`change`) continua existindo, com os inlines de
  tradução e a ação "Duplicar". O modal é o caminho curto, não um segundo
  cadastro: quem grava é o mesmo formulário do domínio (`CategoryModalForm`),
  com as mesmas regras;
* as permissões são as do Django (`add`, `change`, `delete`) e valem em cada
  endpoint, não só nos botões;
* CSRF em todo POST, e cada objeto é buscado no `get_queryset` do usuário —
  um id de outra tela não alcança nada que a pessoa não veja;
* excluir continua esbarrando no `PROTECT` de produtos e subcategorias: a
  recusa vira mensagem, nunca erro 500.

## Onde grava

Os endpoints ficam sob a URL do próprio model
(`admin:categories_category_...`), passam por `admin_view` (sessão de staff) e
respondem JSON. A tela recarrega depois de gravar: a árvore muda de forma —
uma categoria muda de pai, some de um galho e aparece em outro — e redesenhar
metade dela no navegador seria uma segunda verdade sobre o que está no banco.
"""

import copy

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.admin.options import get_content_type_for_model
from django.contrib.admin.utils import quote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, RestrictedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.formats import date_format
from django.utils.timezone import localtime
from django.views.decorators.http import require_POST

from apps.categories.forms import CategoryModalForm, language_label, modal_languages
from apps.categories.models import Category, CategoryTranslation
from apps.core.admin_mixins import DuplicateAdminMixin, TranslatedSlugAdminMixin
from apps.core.constants import DEFAULT_LANGUAGE

#: As colunas da lista, na ordem do desenho. A chave é o que vai na URL (`o=`).
COLUMNS = (
    ("name", "CATEGORIA", "flex-start"),
    ("slug", "SLUG", "flex-start"),
    ("products", "PRODUTOS", "flex-end"),
    ("order", "ORDEM", "flex-end"),
    ("active", "ATIVA", "center"),
)


def _erros_por_campo(erros) -> dict:
    """`{campo: [mensagens]}` — o formato que o modal pinta por campo."""
    return {campo: [str(m) for m in mensagens] for campo, mensagens in erros.items()}


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

    A mesma regra vale no modal, em `CategoryModalForm.clean`.

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

    change_list_template = "admin/categories/category/change_list.html"

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

    class Media:
        #: `jdprint_admin.css` traz a casca dos modais (`.jd-modal*`), a mesma
        #: das variantes e do conteúdo; `jdprint_categories.css` traz o que é
        #: desta tela. A ordem importa: o segundo ajusta o primeiro.
        css = {"all": ("admin/css/jdprint_admin.css", "admin/css/jdprint_categories.css")}
        js = ("admin/js/jd_modal.js", "admin/js/category_admin.js")

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

    # -- rotas ------------------------------------------------------------

    def get_urls(self):
        """Os endpoints do modal e da lista, sob a URL do próprio model.

        `admin_view` exige sessão de staff antes de qualquer coisa; a
        permissão fina (add/change/delete) é conferida dentro de cada um.
        """
        proprias = [
            path(
                "modal/gravar/",
                self.admin_site.admin_view(self.category_save_view),
                name="categories_category_modal_save",
            ),
            path(
                "modal/<int:category_id>/",
                self.admin_site.admin_view(self.category_payload_view),
                name="categories_category_modal_data",
            ),
            path(
                "modal/<int:category_id>/excluir/",
                self.admin_site.admin_view(self.category_delete_view),
                name="categories_category_modal_delete",
            ),
            path(
                "<int:category_id>/alternar/",
                self.admin_site.admin_view(self.category_toggle_view),
                name="categories_category_toggle",
            ),
            path(
                "em-massa/",
                self.admin_site.admin_view(self.category_bulk_view),
                name="categories_category_bulk",
            ),
            path(
                "historico/",
                self.admin_site.admin_view(self.history_all_view),
                name="categories_category_history_all",
            ),
        ]
        return proprias + super().get_urls()

    # -- a lista ----------------------------------------------------------

    #: Os parâmetros desta tela. A `ChangeList` do Django recusa o que não
    #: conhece — e `status`/`o=name.asc` não são filtros dela —, então eles são
    #: lidos aqui e retirados antes de a lista padrão ver a requisição. A lista
    #: padrão continua existindo por baixo: é ela que processa as ações do
    #: Admin (a "Duplicar", por exemplo) enviadas para esta mesma URL.
    OWN_PARAMS = ("q", "status", "o")

    def changelist_view(self, request, extra_context=None):
        """A árvore, já filtrada e ordenada, mais o que o modal precisa saber."""
        contexto = {
            **(extra_context or {}),
            **self._changelist_context(request),
        }
        return super().changelist_view(self._sem_parametros_proprios(request), contexto)

    def _sem_parametros_proprios(self, request):
        """A mesma requisição, sem os parâmetros que só esta tela entende."""
        if not any(nome in request.GET for nome in self.OWN_PARAMS):
            return request
        limpo = copy.copy(request)
        limpo.GET = request.GET.copy()
        for nome in self.OWN_PARAMS:
            limpo.GET.pop(nome, None)
        return limpo

    def _changelist_context(self, request) -> dict:
        busca = (request.GET.get("q") or "").strip()
        status = request.GET.get("status") or "all"
        if status not in {"all", "active", "inactive"}:
            status = "all"
        ordem = request.GET.get("o") or ""
        chave, _, direcao = ordem.partition(".")
        if chave not in {c[0] for c in COLUMNS}:
            chave, direcao = "", ""
        direcao = "desc" if direcao == "desc" else "asc"

        todas = list(self.get_queryset(request))
        linhas = self._tree_rows(todas, busca, status, chave, direcao)
        raizes = sum(1 for c in todas if c.parent_id is None)

        pode_mudar = self.has_change_permission(request)
        return {
            "jd_rows": linhas,
            "jd_columns": self._columns(chave, direcao, busca, status),
            "jd_summary": f"{raizes} categorias · {len(todas) - raizes} subcategorias",
            "jd_footer": f"{len(linhas)} de {len(todas)} registros visíveis",
            "jd_query": busca,
            "jd_status": status,
            "jd_sorted": bool(chave),
            "jd_clear_sort_url": self._list_url(busca, status, "", ""),
            "jd_languages": [
                {"code": code, "label": language_label(code), "is_default": code == DEFAULT_LANGUAGE.value}
                for code in modal_languages()
            ],
            "jd_parents": self._parent_options(todas),
            "jd_can_add": self.has_add_permission(request),
            "jd_can_change": pode_mudar,
            "jd_can_delete": self.has_delete_permission(request),
            "jd_urls": {
                "save": reverse("admin:categories_category_modal_save"),
                "data": reverse("admin:categories_category_modal_data", args=[0]),
                "delete": reverse("admin:categories_category_modal_delete", args=[0]),
                "toggle": reverse("admin:categories_category_toggle", args=[0]),
                "bulk": reverse("admin:categories_category_bulk"),
            },
        }

    def _parent_options(self, todas) -> list:
        """Todas as categorias como pai possível, na ordem da árvore.

        A árvore não tem dois níveis: uma categoria pode ser filha de qualquer
        outra (`Impressões 3D › Animais › Gatos › Raças › Persas`). Oferecer só
        as raízes tornaria o terceiro nível para baixo inalcançável pelo modal
        — e faria "+ Adicionar subcategoria" de um neto abrir sem pai, porque
        o `<select>` não teria a opção para marcar.

        O rótulo leva um traço por nível, como a coluna da lista, para
        distinguir duas "Gatos" que vivem em galhos diferentes. Quem não pode
        ser pai — a própria categoria e os seus descendentes — é recusado no
        `CategoryModalForm`, com a mensagem explicando o ciclo; esconder aqui
        exigiria redesenhar a lista a cada troca de seleção.
        """
        filhos: dict[int | None, list] = {}
        for categoria in todas:
            filhos.setdefault(categoria.parent_id, []).append(categoria)

        opcoes = []

        def descer(pai_id, nivel):
            for categoria in sorted(filhos.get(pai_id, []), key=lambda c: (c.sort_order, c.slug)):
                opcoes.append(
                    {
                        "id": categoria.pk,
                        "name": ("— " * nivel) + categoria.name_in(DEFAULT_LANGUAGE.value),
                        "depth": nivel,
                    }
                )
                descer(categoria.pk, nivel + 1)

        descer(None, 0)
        return opcoes

    def _columns(self, chave, direcao, busca, status) -> list:
        colunas = []
        for key, label, justify in COLUMNS:
            ativa = key == chave
            proxima = "desc" if ativa and direcao == "asc" else "asc"
            colunas.append(
                {
                    "label": label,
                    "justify": justify,
                    "is_sorted": ativa,
                    "caret": ("▲" if direcao == "asc" else "▼") if ativa else "↕",
                    "url": self._list_url(busca, status, key, proxima),
                }
            )
        return colunas

    @staticmethod
    def _list_url(busca, status, chave, direcao) -> str:
        base = reverse("admin:categories_category_changelist")
        partes = []
        if busca:
            partes.append(f"q={busca}")
        if status and status != "all":
            partes.append(f"status={status}")
        if chave:
            partes.append(f"o={chave}.{direcao}")
        return base + ("?" + "&".join(partes) if partes else "")

    def _tree_rows(self, todas, busca, status, chave, direcao) -> list:
        """As linhas visíveis, em árvore, já filtradas e ordenadas.

        Um filho que casa com a busca aparece mesmo quando o pai não casa: sem
        o pai na tela, a linha ficaria sem contexto — e é o contexto que
        distingue "Gatos" de "Gatos". Por isso o pai entra junto, e a contagem
        do rodapé conta o que está à vista.
        """
        filhos: dict[int | None, list] = {}
        for categoria in todas:
            filhos.setdefault(categoria.parent_id, []).append(categoria)

        def casa(categoria) -> bool:
            nome = categoria.name_in(DEFAULT_LANGUAGE.value).lower()
            texto = busca.lower()
            ok_busca = not texto or texto in nome or texto in categoria.slug.lower()
            ok_status = (
                status == "all"
                or (status == "active" and categoria.is_active)
                or (status == "inactive" and not categoria.is_active)
            )
            return ok_busca and ok_status

        def ordenar(lista):
            if not chave:
                return sorted(lista, key=lambda c: (c.sort_order, c.slug))
            reverso = direcao == "desc"
            chaves = {
                "name": lambda c: c.name_in(DEFAULT_LANGUAGE.value).lower(),
                "slug": lambda c: c.slug.lower(),
                "products": lambda c: c.total_products,
                "order": lambda c: c.sort_order,
                "active": lambda c: c.is_active,
            }
            return sorted(lista, key=chaves[chave], reverse=reverso)

        # Quem aparece: quem casa com o filtro **e** os ancestrais de quem casa
        # — um neto encontrado sem os pais na tela seria uma linha sem o
        # contexto que a distingue de outra com o mesmo nome.
        por_id = {c.pk: c for c in todas}
        visiveis = set()
        for categoria in todas:
            if not casa(categoria):
                continue
            visiveis.add(categoria.pk)
            pai = por_id.get(categoria.parent_id)
            while pai is not None and pai.pk not in visiveis:
                visiveis.add(pai.pk)
                pai = por_id.get(pai.parent_id)

        linhas = []

        def percorrer(pai_id, nivel):
            for categoria in ordenar(filhos.get(pai_id, [])):
                if categoria.pk not in visiveis:
                    continue
                linhas.append(
                    {
                        "obj": categoria,
                        "id": categoria.pk,
                        "name": categoria.name_in(DEFAULT_LANGUAGE.value),
                        "slug": categoria.slug,
                        "products": categoria.total_products,
                        "order": categoria.sort_order,
                        "is_active": categoria.is_active,
                        "depth": nivel,
                        "indent": 16 + nivel * 28,
                        "has_children": any(
                            c.pk in visiveis for c in filhos.get(categoria.pk, [])
                        ),
                        "change_url": reverse(
                            "admin:categories_category_change", args=[quote(categoria.pk)]
                        ),
                    }
                )
                percorrer(categoria.pk, nivel + 1)

        percorrer(None, 0)
        return linhas

    def history_all_view(self, request):
        """O que andou mudando nas categorias — o registro do Admin, em uma tela.

        O Django guarda o histórico por objeto (`LogEntry`) e só o mostra numa
        página por vez. Quem cuida da árvore precisa da pergunta inversa: o que
        mexeram nas categorias esta semana? É a mesma tabela, lida de outro
        ângulo — nada é gravado aqui, e quem não pode ver categorias não entra.
        """
        from django.shortcuts import render

        if not self.has_view_permission(request):
            raise PermissionDenied

        entradas = (
            LogEntry.objects.filter(content_type=get_content_type_for_model(self.model))
            .select_related("user")
            .order_by("-action_time")[:200]
        )
        linhas = [
            {
                "when": date_format(localtime(entrada.action_time), "d/m/Y H:i"),
                "who": entrada.user.get_username() if entrada.user else "—",
                "what": entrada.object_repr,
                "detail": entrada.get_change_message(),
                "url": (
                    reverse("admin:categories_category_change", args=[quote(entrada.object_id)])
                    if entrada.object_id and not entrada.is_deletion()
                    else ""
                ),
            }
            for entrada in entradas
        ]
        contexto = {
            **self.admin_site.each_context(request),
            "title": "Histórico das categorias",
            "opts": self.opts,
            "jd_log": linhas,
            "jd_back": reverse("admin:categories_category_changelist"),
        }
        return render(request, "admin/categories/category/history.html", contexto)

    # -- os endpoints do modal --------------------------------------------

    def _categoria(self, request, category_id):
        """A categoria, dentro do que este usuário enxerga. 404 fora disso."""
        return get_object_or_404(self.get_queryset(request), pk=category_id)

    def category_payload_view(self, request, category_id):
        """O que o modal precisa para abrir numa categoria: campos, filhos, auditoria."""
        if not self.has_view_permission(request):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)
        categoria = self._categoria(request, category_id)
        traducoes = categoria.translations_by_language()

        pai = categoria.parent
        return JsonResponse(
            {
                "ok": True,
                "id": categoria.pk,
                "kicker": (pai.name_in(DEFAULT_LANGUAGE.value).upper() + " ›") if pai else "PRIMEIRO NÍVEL",
                "title": categoria.name_in(DEFAULT_LANGUAGE.value),
                "view_url": categoria.get_absolute_url(),
                "change_url": reverse("admin:categories_category_change", args=[quote(categoria.pk)]),
                "fields": {
                    "name": categoria.name_in(DEFAULT_LANGUAGE.value),
                    "parent": str(categoria.parent_id or ""),
                    "sort_order": str(categoria.sort_order),
                    "slug": categoria.slug,
                    "is_active": categoria.is_active,
                    **{
                        f"name_{code}": getattr(traducoes.get(code), "name", "")
                        for code in modal_languages()
                    },
                    **{
                        f"description_{code}": getattr(traducoes.get(code), "description", "")
                        for code in modal_languages()
                    },
                },
                "children": self._children_payload(request, categoria),
                "audit": self._audit_payload(categoria),
            }
        )

    def _children_payload(self, request, categoria) -> list:
        filhos = (
            self.get_queryset(request)
            .filter(parent=categoria)
            .order_by("sort_order", "slug")
        )
        return [
            {
                "id": filho.pk,
                "name": filho.name_in(DEFAULT_LANGUAGE.value),
                "slug": filho.slug,
                "products": f"{filho.total_products} prod.",
                "is_active": filho.is_active,
            }
            for filho in filhos
        ]

    def _audit_payload(self, categoria) -> dict:
        """Datas do próprio registro e o histórico real do Admin (`LogEntry`).

        Nada é inventado: uma categoria criada por uma migration de dados ou
        pelo seed não tem entrada de histórico, e a lista vem vazia — com a
        frase dizendo isso, em vez de linhas de exemplo.
        """
        entradas = (
            LogEntry.objects.filter(
                content_type=get_content_type_for_model(categoria),
                object_id=str(categoria.pk),
            )
            .select_related("user")
            .order_by("-action_time")[:10]
        )
        def quando(valor):
            return date_format(localtime(valor), "d/m/Y H:i") if valor else "—"

        return {
            "created_at": quando(categoria.created_at),
            "updated_at": quando(categoria.updated_at),
            "log": [
                {
                    "when": quando(entrada.action_time),
                    "what": entrada.get_change_message() or entrada.object_repr,
                    "who": entrada.user.get_username() if entrada.user else "",
                }
                for entrada in entradas
            ],
            "history_url": reverse(
                "admin:categories_category_history", args=[quote(categoria.pk)]
            ),
        }

    @method_decorator(require_POST)
    def category_save_view(self, request):
        """Cria ou edita uma categoria inteira — identificação e traduções."""
        category_id = (request.POST.get("category_id") or "").strip()
        instancia = self._categoria(request, category_id) if category_id else None

        permitido = (
            self.has_change_permission(request, instancia)
            if instancia is not None
            else self.has_add_permission(request)
        )
        if not permitido:
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        form = CategoryModalForm(request.POST, instance=instancia)
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "errors": _erros_por_campo(form.errors)}, status=400
            )

        criada = instancia is None
        with transaction.atomic():
            categoria = form.save()

        # O histórico do Admin é o mesmo dos formulários: quem mexeu, quando e
        # no quê. Sem isto a aba Auditoria ficaria cega ao que o modal faz.
        self.log_addition(request, categoria, "Categoria criada pelo modal.") if criada else (
            self.log_change(request, categoria, "Categoria alterada pelo modal.")
        )
        nome = categoria.name_in(DEFAULT_LANGUAGE.value)
        return JsonResponse(
            {
                "ok": True,
                "created": criada,
                "id": categoria.pk,
                "message": f"Categoria «{nome}» " + ("criada." if criada else "atualizada."),
            }
        )

    @method_decorator(require_POST)
    def category_delete_view(self, request, category_id):
        """Exclui — recusando, com o motivo, a que tem produto ou filha."""
        categoria = self._categoria(request, category_id)
        if not self.has_delete_permission(request, categoria):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        nome = categoria.name_in(DEFAULT_LANGUAGE.value)
        try:
            with transaction.atomic():
                self.log_deletions(request, [categoria])
                categoria.delete()
        except (ProtectedError, RestrictedError):
            return JsonResponse(
                {
                    "ok": False,
                    "detail": self._motivo_da_recusa(categoria, nome),
                },
                status=400,
            )
        return JsonResponse({"ok": True, "message": f"Categoria «{nome}» excluída."})

    @staticmethod
    def _motivo_da_recusa(categoria, nome) -> str:
        filhas = categoria.children.count()
        produtos = Category.objects.filter(pk=categoria.pk).aggregate(n=Count("products"))["n"]
        partes = []
        if filhas:
            partes.append(f"{filhas} subcategoria(s)")
        if produtos:
            partes.append(f"{produtos} produto(s)")
        detalhe = " e ".join(partes) if partes else "registros ligados a ela"
        return (
            f"A categoria «{nome}» não pode ser excluída: há {detalhe}. "
            "Mova ou remova o que depende dela primeiro."
        )

    @method_decorator(require_POST)
    def category_toggle_view(self, request, category_id):
        """Liga e desliga a categoria — o clique no selo da lista."""
        categoria = self._categoria(request, category_id)
        if not self.has_change_permission(request, categoria):
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        categoria.is_active = not categoria.is_active
        categoria.save(update_fields=["is_active", "updated_at"])
        self.log_change(
            request,
            categoria,
            "Ativada." if categoria.is_active else "Desativada.",
        )
        return JsonResponse(
            {
                "ok": True,
                "is_active": categoria.is_active,
                "message": (
                    f"«{categoria.name_in(DEFAULT_LANGUAGE.value)}» "
                    + ("ativada." if categoria.is_active else "desativada.")
                ),
            }
        )

    @method_decorator(require_POST)
    def category_bulk_view(self, request):
        """Ativar, desativar ou excluir as selecionadas.

        Excluir é uma permissão diferente de alterar, e é conferida como tal.
        O que o `PROTECT` recusar fica de fora com o motivo: a operação não é
        tudo-ou-nada de propósito — desativar oito e falhar na nona não é
        motivo para desfazer as oito.
        """
        acao = (request.POST.get("action") or "").strip()
        ids = [i for i in request.POST.getlist("ids") if i.isdigit()]
        if acao not in {"activate", "deactivate", "delete"} or not ids:
            return JsonResponse(
                {"ok": False, "detail": "Escolha uma ação e ao menos uma categoria."},
                status=400,
            )

        precisa = self.has_delete_permission(request) if acao == "delete" else self.has_change_permission(request)
        if not precisa:
            return JsonResponse({"ok": False, "detail": "Sem permissão."}, status=403)

        alvos = list(self.get_queryset(request).filter(pk__in=ids))
        feitas, recusadas = 0, []

        for categoria in alvos:
            nome = categoria.name_in(DEFAULT_LANGUAGE.value)
            if acao == "delete":
                try:
                    with transaction.atomic():
                        self.log_deletions(request, [categoria])
                        categoria.delete()
                    feitas += 1
                except (ProtectedError, RestrictedError):
                    recusadas.append(nome)
                continue

            ativo = acao == "activate"
            if categoria.is_active != ativo:
                categoria.is_active = ativo
                categoria.save(update_fields=["is_active", "updated_at"])
                self.log_change(request, categoria, "Ativada." if ativo else "Desativada.")
                feitas += 1

        rotulo = {"activate": "ativada(s)", "deactivate": "desativada(s)", "delete": "excluída(s)"}[acao]
        mensagem = f"{feitas} categoria(s) {rotulo}."
        if recusadas:
            mensagem += (
                " Não foi possível excluir: "
                + ", ".join(recusadas)
                + " — há produtos ou subcategorias ligados."
            )
        self.message_user(request, mensagem, messages.WARNING if recusadas else messages.SUCCESS)
        return JsonResponse({"ok": True, "message": mensagem, "done": feitas})
