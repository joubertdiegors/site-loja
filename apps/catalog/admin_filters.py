"""Os filtros do painel da lista de produtos.

Todos são filtros do próprio Admin (`ListFilter`), registrados em
`ProductAdmin.list_filter`. É o `ChangeList` que os encadeia, e é por isso que
eles **combinam entre si, com a busca, com a ordenação, com a hierarquia de
datas e com a paginação** sem que nada aqui precise saber dos outros.

Duas diferenças em relação ao `SimpleListFilter` de fábrica:

* **Multi-seleção.** O parâmetro pode vir repetido (`?material=1&material=3`)
  e a repetição vale **OU**. Entre grupos continua valendo **E**, porque cada
  grupo estreita o queryset que o anterior devolveu.

* **Contagem por opção.** Cada opção mostra quantos produtos ela traria
  *considerando os outros filtros ativos, mas não o próprio grupo* — senão a
  contagem de "PLA" seria sempre 0 depois de escolher "PETG". É a mesma
  semântica das facetas do Django (`exclude_parameters`), com uma consulta por
  grupo em vez de uma por opção.
"""

from decimal import Decimal, InvalidOperation

from django.contrib.admin.filters import ListFilter
from django.db.models import Count, Q

from apps.catalog.models import (
    LOW_STOCK_THRESHOLD,
    Brand,
    Color,
    Material,
    PersonalizationType,
    ProductColor,
    ProductMaterialComposition,
    ProductStatus,
    ProductVariant,
)
from apps.categories.models import Category
from apps.categories.tree import CategoryTree
from apps.core.constants import DEFAULT_LANGUAGE


def arvore_de_categorias(request):
    """A árvore de categorias inteira, carregada uma vez por requisição.

    Uma consulta só, guardada no `request`: o filtro de categoria, a contagem
    de cada opção e a busca por categoria usam todos a mesma — e a montagem
    da lista pede o queryset ao `ChangeList` uma vez por grupo de filtro.

    Carrega também as inativas: no Admin elas continuam existindo e continuam
    tendo produtos.
    """
    memoria = getattr(request, "_jd_arvore_de_categorias", None)
    if memoria is None:
        memoria = CategoryTree(list(Category.objects.all().prefetch_related("translations")))
        request._jd_arvore_de_categorias = memoria
    return memoria


def caminho_de_categoria(arvore, categoria_id) -> str:
    """"Religiosos › Santos › Nossa Senhora", lido da árvore em memória.

    `Category.__str__` monta esse mesmo texto subindo por `self.parent`, o que
    custa uma consulta por ancestral — e, numa lista de 24 produtos em
    categorias de terceiro nível, isso é uma dúzia de consultas por página.
    A árvore já está carregada; ela responde de graça.

    O `visto` guarda contra dado inconsistente: uma categoria que fosse
    ancestral de si mesma travaria o laço.
    """
    partes = []
    visto = set()
    atual = arvore.by_id.get(categoria_id)
    while atual is not None and atual.pk not in visto:
        visto.add(atual.pk)
        partes.append(atual.name_in(DEFAULT_LANGUAGE.value))
        atual = arvore.by_id.get(atual.parent_id)
    return " › ".join(reversed(partes))


class GrupoDeFiltro(ListFilter):
    """Base dos grupos do painel: opções com caixa de seleção e contagem.

    O desenho fica no template da lista, não em `admin/filter.html`: o painel
    é uma grade de dropdowns, não a barra lateral de fábrica. Por isso
    `choices()` devolve vazio — quem lê `opcoes` é
    `ProductAdmin._contexto_do_painel`.
    """

    #: Nome do parâmetro na querystring. Não é campo do model, e não precisa
    #: ser: `ModelAdmin.lookup_allowed` libera nomes que não são campos, e o
    #: `ChangeList` só repassa ao `filter()` o que nenhum filtro consumiu.
    parametro = ""
    #: Texto do botão quando nada está escolhido.
    vazio = "Todos"
    title = ""

    def __init__(self, request, params, model, model_admin):
        super().__init__(request, params, model, model_admin)
        self.model_admin = model_admin
        self.opcoes = self._opcoes(request, model_admin)

        conhecidos = {opcao["valor"] for opcao in self.opcoes}
        self.escolhidos = []
        for bruto in params.pop(self.parametro, []):
            bruto = str(bruto)
            # Valor desconhecido é ignorado, não é erro: uma URL antiga (a
            # categoria que foi apagada) deve abrir a lista, não um 500.
            if bruto in conhecidos and bruto not in self.escolhidos:
                self.escolhidos.append(bruto)
        if self.escolhidos:
            self.used_parameters[self.parametro] = self.escolhidos

    def _opcoes(self, request, model_admin):
        """As opções do grupo, uma vez por requisição.

        A contagem de facetas reconstrói o queryset do `ChangeList` uma vez
        por grupo, e cada reconstrução instancia **todos** os filtros de novo.
        Sem esta memória seriam nove leituras de opções vezes nove grupos.
        """
        memoria = getattr(request, "_jd_opcoes_de_filtro", None)
        if memoria is None:
            memoria = {}
            request._jd_opcoes_de_filtro = memoria
        if self.parametro not in memoria:
            memoria[self.parametro] = list(self.carregar_opcoes(request, model_admin))
        return memoria[self.parametro]

    # -- interface do Django -----------------------------------------------

    def has_output(self):
        return bool(self.opcoes)

    def expected_parameters(self):
        return [self.parametro]

    def choices(self, changelist):
        return ()

    def queryset(self, request, queryset):
        if not self.escolhidos:
            return queryset
        return queryset.filter(self.condicao(self.escolhidos))

    # -- interface das subclasses ------------------------------------------

    def carregar_opcoes(self, request, model_admin) -> list:
        """`[{"valor": str, "rotulo": str, "nivel": int}, ...]`, na ordem da tela."""
        raise NotImplementedError

    def condicao(self, valores) -> Q:
        """O `Q` que traz os produtos de **qualquer** um dos valores."""
        raise NotImplementedError

    def contar(self, base) -> dict:
        """Quantos produtos por opção, em **uma** consulta.

        `base` já vem estreitada pelos outros filtros e pela busca — e sem as
        escolhas deste grupo.
        """
        pares = [(opcao["valor"], self.condicao([opcao["valor"]])) for opcao in self.opcoes]
        return self._contar_condicoes(base, pares)

    @staticmethod
    def _contar_condicoes(base, pares) -> dict:
        """Um `Count` condicional por opção, tudo num `aggregate` só.

        `distinct=True` porque uma condição pode atravessar relação de muitos
        (a cor que vem da variante): sem ele o produto de cinco variantes
        contaria cinco vezes.
        """
        if not pares:
            return {}
        bruto = base.aggregate(
            **{
                f"opcao_{indice}": Count("pk", filter=condicao, distinct=True)
                for indice, (_valor, condicao) in enumerate(pares)
            }
        )
        return {
            valor: bruto.get(f"opcao_{indice}") or 0
            for indice, (valor, _condicao) in enumerate(pares)
        }


class EscolhasFiltro(GrupoDeFiltro):
    """Grupo cujas opções são um `TextChoices` fixo e o campo é do produto."""

    escolhas = ()
    campo = ""

    def carregar_opcoes(self, request, model_admin):
        return [
            {"valor": str(valor), "rotulo": str(rotulo), "nivel": 0}
            for valor, rotulo in self.escolhas
        ]

    def condicao(self, valores):
        return Q(**{f"{self.campo}__in": valores})


# ---------------------------------------------------------------------------
# Categoria — o único com hierarquia
# ---------------------------------------------------------------------------


class CategoriaFiltro(GrupoDeFiltro):
    """A árvore inteira, indentada, com **profundidade ilimitada**.

    Escolher "Religiosos" traz os produtos de "Religiosos", de
    "Religiosos › Santos" e de "Religiosos › Santos › Nossa Senhora" — de
    todos os descendentes, em qualquer nível. Quem sabe quem desce de quem é
    a `CategoryTree`, que carrega a árvore em **uma** consulta; não há aqui
    nenhum limite de dois níveis nem consulta recursiva.

    Carrega também as categorias inativas: no Admin elas continuam existindo
    e continuam tendo produtos.
    """

    title = "Categoria"
    parametro = "categoria"
    vazio = "Todas as categorias"

    def carregar_opcoes(self, request, model_admin):
        arvore = arvore_de_categorias(request)
        opcoes = []

        def descer(pai_id, nivel):
            for categoria in arvore.children.get(pai_id, []):
                opcoes.append(
                    {
                        "valor": str(categoria.pk),
                        "rotulo": categoria.name_in(DEFAULT_LANGUAGE.value),
                        "nivel": nivel,
                    }
                )
                descer(categoria.pk, nivel + 1)

        descer(None, 0)
        return opcoes

    def _subarvore(self, valores):
        arvore = arvore_de_categorias(self.request)
        ids = set()
        for valor in valores:
            ids.update(arvore.subtree_ids(int(valor)))
        return ids

    def condicao(self, valores):
        return Q(category_id__in=self._subarvore(valores))

    def contar(self, base):
        """Uma consulta, e a soma sobe pela árvore em memória.

        Um `Count` condicional por nó custaria um `IN (...)` por categoria
        dentro do mesmo SQL — e a árvore não tem profundidade máxima. Aqui o
        banco devolve o total **direto** de cada categoria e os pais somam os
        filhos onde a árvore já está: na memória.
        """
        arvore = arvore_de_categorias(self.request)
        diretos = {
            categoria_id: total
            for categoria_id, total in base.order_by()
            .values_list("category_id")
            .annotate(total=Count("pk"))
        }
        return {
            opcao["valor"]: sum(
                diretos.get(descendente, 0)
                for descendente in arvore.subtree_ids(int(opcao["valor"]))
            )
            for opcao in self.opcoes
        }


# ---------------------------------------------------------------------------
# Classificação do produto
# ---------------------------------------------------------------------------


class StatusFiltro(EscolhasFiltro):
    title = "Status"
    parametro = "status"
    vazio = "Todos"
    escolhas = ProductStatus.choices
    campo = "status"


class PersonalizacaoFiltro(EscolhasFiltro):
    title = "Personalização"
    parametro = "personalizacao"
    vazio = "Todas"
    escolhas = PersonalizationType.choices
    campo = "personalization_type"


class MarcaFiltro(GrupoDeFiltro):
    """Só as marcas que têm produto — o painel não é o cadastro de marcas."""

    title = "Marca"
    parametro = "marca"
    vazio = "Todas"

    def carregar_opcoes(self, request, model_admin):
        marcas = Brand.objects.filter(products__isnull=False).distinct().order_by("name")
        return [{"valor": str(marca.pk), "rotulo": marca.name, "nivel": 0} for marca in marcas]

    def condicao(self, valores):
        return Q(brand_id__in=valores)


class DestaqueFiltro(GrupoDeFiltro):
    title = "Destaque"
    parametro = "destaque"
    vazio = "Todos"

    def carregar_opcoes(self, request, model_admin):
        return [
            {"valor": "sim", "rotulo": "Em destaque", "nivel": 0},
            {"valor": "nao", "rotulo": "Fora do destaque", "nivel": 0},
        ]

    def condicao(self, valores):
        condicao = Q(pk__in=[])
        if "sim" in valores:
            condicao |= Q(is_featured=True)
        if "nao" in valores:
            condicao |= Q(is_featured=False)
        return condicao


# ---------------------------------------------------------------------------
# Cor e material — moram em dois lugares
# ---------------------------------------------------------------------------


class PorRelacaoFiltro(GrupoDeFiltro):
    """Grupo cujo valor pode vir da ficha do produto **ou** da variante.

    A condição é escrita com `pk__in` de subconsultas, e não com um `join`:
    `join` traria o produto repetido (uma linha por variante) e obrigaria a um
    `distinct()` que estragaria as anotações de soma da lista.
    """

    def condicao(self, valores):
        return Q(pk__in=self._da_ficha(valores)) | Q(pk__in=self._da_variante(valores))

    def _da_ficha(self, valores):
        raise NotImplementedError

    def _da_variante(self, valores):
        raise NotImplementedError


class MaterialFiltro(PorRelacaoFiltro):
    title = "Material"
    parametro = "material"
    vazio = "Todos"

    def carregar_opcoes(self, request, model_admin):
        materiais = (
            Material.objects.filter(Q(compositions__isnull=False) | Q(variants__isnull=False))
            .distinct()
            .order_by("name")
        )
        return [{"valor": str(item.pk), "rotulo": item.name, "nivel": 0} for item in materiais]

    def _da_ficha(self, valores):
        return ProductMaterialComposition.objects.filter(material_id__in=valores).values("product_id")

    def _da_variante(self, valores):
        return ProductVariant.objects.filter(material_id__in=valores).values("product_id")


class CorFiltro(PorRelacaoFiltro):
    """Cor da paleta ou da variante — e, para a cor composta, cada componente.

    Filtrar por «Azul» traz também o produto cuja variante é «Branco + Azul»:
    a cor composta é feita de azul. E a lista de opções inclui as componentes
    das compostas em uso, para «Azul» aparecer no painel mesmo quando nenhum
    produto usa o azul sozinho.
    """

    title = "Cor"
    parametro = "cor"
    vazio = "Todas"

    def carregar_opcoes(self, request, model_admin):
        em_uso = Q(product_uses__isnull=False) | Q(variants__isnull=False)
        componente_de_uma_em_uso = Q(composed_in__color__product_uses__isnull=False) | Q(
            composed_in__color__variants__isnull=False
        )
        cores = (
            Color.objects.filter(em_uso | componente_de_uma_em_uso)
            .distinct()
            .order_by("name")
        )
        return [{"valor": str(item.pk), "rotulo": item.name, "nivel": 0} for item in cores]

    @staticmethod
    def _propria_ou_componente(valores):
        """A cor pedida, ou uma composta que a tenha como componente."""
        return Q(color_id__in=valores) | Q(color__component_links__component_id__in=valores)

    def _da_ficha(self, valores):
        return ProductColor.objects.filter(self._propria_ou_componente(valores)).values("product_id")

    def _da_variante(self, valores):
        return ProductVariant.objects.filter(self._propria_ou_componente(valores)).values("product_id")


# ---------------------------------------------------------------------------
# Estado comercial — lê as anotações de `with_admin_annotations`
# ---------------------------------------------------------------------------


class EstoqueFiltro(GrupoDeFiltro):
    """Estoque somado das variantes ativas, com as regras da loja.

    "Sob encomenda" e "venda sem estoque" não dependem do saldo — é o que
    `ProductVariant.is_available` diz, e é o que a anotação `_disponivel`
    reproduz em SQL. As quatro opções são um recorte de conjunto ("tem ao
    menos uma variante assim"), não a leitura de `Product.stock_state`, que
    olha só a variante em exibição: num produto que mistura pronta-entrega e
    sob encomenda, aqui ele aparece em "Sob encomenda".
    """

    title = "Estoque"
    parametro = "estoque"
    vazio = "Todos"

    def carregar_opcoes(self, request, model_admin):
        return [
            {"valor": "encomenda", "rotulo": "Sob encomenda", "nivel": 0},
            {"valor": "esgotado", "rotulo": "Esgotado", "nivel": 0},
            {"valor": "baixo", "rotulo": f"Baixo (1–{LOW_STOCK_THRESHOLD})", "nivel": 0},
            {"valor": "ok", "rotulo": f"Em estoque ({LOW_STOCK_THRESHOLD + 1}+)", "nivel": 0},
        ]

    @staticmethod
    def _pedacos():
        vendavel = Q(_disponivel=True, _sob_encomenda=False)
        pouco = Q(_estoque__gt=0, _estoque__lte=LOW_STOCK_THRESHOLD)
        return {
            "encomenda": Q(_sob_encomenda=True),
            "esgotado": Q(_disponivel=False),
            "baixo": vendavel & pouco,
            "ok": vendavel & ~pouco,
        }

    def condicao(self, valores):
        pedacos = self._pedacos()
        condicao = Q(pk__in=[])
        for valor in valores:
            condicao |= pedacos[valor]
        return condicao

    def contar(self, base):
        return self._contar_condicoes(
            base.with_admin_annotations(),
            [(opcao["valor"], self._pedacos()[opcao["valor"]]) for opcao in self.opcoes],
        )


class VariantesFiltro(GrupoDeFiltro):
    """Quantas unidades vendáveis o produto oferece — inclusive nenhuma."""

    title = "Variantes"
    parametro = "variantes"
    vazio = "Todas"

    def carregar_opcoes(self, request, model_admin):
        return [
            {"valor": "multiplas", "rotulo": "Com variantes (2+)", "nivel": 0},
            {"valor": "unica", "rotulo": "Uma única opção", "nivel": 0},
            {"valor": "sem", "rotulo": "Sem configuração", "nivel": 0},
        ]

    @staticmethod
    def _pedacos():
        return {
            "multiplas": Q(_variantes_ativas__gt=1),
            "unica": Q(_variantes_ativas=1),
            "sem": Q(_variantes_ativas=0),
        }

    def condicao(self, valores):
        condicao = Q(pk__in=[])
        pedacos = self._pedacos()
        for valor in valores:
            condicao |= pedacos[valor]
        return condicao

    def contar(self, base):
        return self._contar_condicoes(
            base.with_admin_annotations(),
            [(opcao["valor"], self._pedacos()[opcao["valor"]]) for opcao in self.opcoes],
        )


# ---------------------------------------------------------------------------
# Preço — dois campos, não uma lista
# ---------------------------------------------------------------------------


class PrecoFiltro(ListFilter):
    """Faixa de preço, em dois parâmetros.

    Um produto entra quando a **faixa dele encosta na faixa pedida**
    (`_preco_max >= mínimo` e `_preco_min <= máximo`): quem procura "entre €10
    e €20" quer o produto que tem alguma variante nesse intervalo, e não só o
    produto cuja variante mais barata está lá dentro.

    Produto sem variante com preço tem as duas anotações nulas e fica de fora
    de qualquer faixa — que é o comportamento certo: ele não tem preço.
    """

    title = "Preço"
    MINIMO = "preco_min"
    MAXIMO = "preco_max"

    def __init__(self, request, params, model, model_admin):
        super().__init__(request, params, model, model_admin)
        self.minimo = self._numero(params.pop(self.MINIMO, []))
        self.maximo = self._numero(params.pop(self.MAXIMO, []))
        if self.minimo is not None:
            self.used_parameters[self.MINIMO] = self.minimo
        if self.maximo is not None:
            self.used_parameters[self.MAXIMO] = self.maximo

    @staticmethod
    def _numero(brutos):
        """O último valor legível, ou nada. Texto inválido é ignorado, não é erro."""
        for bruto in reversed(list(brutos)):
            texto = str(bruto).strip().replace("€", "").replace(",", ".")
            if not texto:
                continue
            try:
                valor = Decimal(texto)
            except (InvalidOperation, ValueError):
                continue
            if valor >= 0:
                return valor
        return None

    def has_output(self):
        return True

    def expected_parameters(self):
        return [self.MINIMO, self.MAXIMO]

    def choices(self, changelist):
        return ()

    def queryset(self, request, queryset):
        if self.minimo is not None:
            queryset = queryset.filter(_preco_max__gte=self.minimo)
        if self.maximo is not None:
            queryset = queryset.filter(_preco_min__lte=self.maximo)
        return queryset


#: A ordem aqui é a ordem do painel na tela.
FILTROS_DE_PRODUTO = (
    CategoriaFiltro,
    StatusFiltro,
    MarcaFiltro,
    MaterialFiltro,
    CorFiltro,
    PersonalizacaoFiltro,
    EstoqueFiltro,
    VariantesFiltro,
    DestaqueFiltro,
    PrecoFiltro,
)
