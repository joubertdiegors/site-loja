"""A sugestão de SKU — do produto e da variante.

O SKU continua sendo o campo de sempre, digitado e editável; o que existe aqui
é a **sugestão** que o cadastro rápido, o formulário e o modal de variante
usam quando o campo fica em branco:

    Religioso + "Leão de Judá"          -> REL-LEAO-001
    Religioso + "Jesus Cristo"          -> REL-JESUS-001
    Decoração + "Vaso Decorativo"       -> DEC-VASO-001
    Animais   + "Parasaurolophus"       -> ANI-PARASAUROLOPHUS-001

    produto REL-LEAO-001, variantes     -> REL-LEAO-001-V01, -V02, ...

O prefixo são as três primeiras letras da primeira palavra significativa da
categoria; o miolo é a primeira palavra significativa do nome. Os dois passam
pela mesma normalização: sem acento, sem caractere especial, em maiúsculas.

## A numeração

Não é ``count() + 1``. O número é o **maior já usado** com aquela base, mais
um, lido do banco na hora (``sku__regex``, que funciona no PostgreSQL e no
SQLite). Apagar um produto não faz o número voltar, e dois cadastros ao mesmo
tempo recebem a mesma sugestão só se nenhum dos dois gravou ainda — e aí o
``unique`` do banco recusa o segundo, e ``com_sku_livre`` tenta a sequência
seguinte. É a constraint que garante a unicidade; a sugestão só evita o erro
previsível.
"""

import re
import unicodedata

from django.db import IntegrityError, transaction

#: Palavras que não identificam nada — pulam para a próxima.
STOPWORDS = frozenset(
    "de da do das dos e a o as os um uma uns umas para com em no na nos nas por sem sob "
    "the of and or for with in on at le la les des du et un une van het een en der die das "
    "und mit für".split()
)

#: O tamanho do prefixo da categoria (REL, DEC, ANI).
PREFIX_LENGTH = 3
#: O miolo do nome, no máximo — um SKU cabe numa etiqueta.
STEM_LENGTH = 24
#: Sem categoria, o produto ainda ganha um SKU.
FALLBACK_PREFIX = "PRD"
FALLBACK_STEM = "ITEM"
#: Quantas sequências tentar quando o banco recusa a sugestão.
MAX_ATTEMPTS = 25


def normalize(text: str) -> str:
    """Só letras e dígitos ASCII, em maiúsculas, separados por espaço."""
    sem_acento = unicodedata.normalize("NFKD", text or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    limpo = re.sub(r"[^A-Za-z0-9]+", " ", sem_acento)
    return limpo.upper().strip()


def significant_words(text: str) -> list[str]:
    """As palavras que identificam, na ordem — sem artigos e preposições."""
    palavras = normalize(text).split()
    uteis = [p for p in palavras if p.lower() not in STOPWORDS]
    return uteis or palavras


def category_prefix(category) -> str:
    """REL para Religioso, DEC para Decoração, ANI para Animais."""
    if category is None:
        return FALLBACK_PREFIX
    nome = category.name_in("pt") if hasattr(category, "name_in") else str(category)
    palavras = significant_words(nome)
    if not palavras:
        return FALLBACK_PREFIX
    return palavras[0][:PREFIX_LENGTH]


def name_stem(name: str) -> str:
    """LEAO para "Leão de Judá", DINOSSAURO para "Dinossauro Parasaurolophus"."""
    palavras = significant_words(name)
    if not palavras:
        return FALLBACK_STEM
    return palavras[0][:STEM_LENGTH]


def product_sku_base(category, name: str) -> str:
    return f"{category_prefix(category)}-{name_stem(name)}"


def _highest_sequence(queryset, pattern: str, reserved=()) -> int:
    """O maior número já usado com este padrão, no banco e nos reservados."""
    regex = re.compile(pattern)
    maior = 0
    for sku in queryset.filter(sku__regex=pattern).values_list("sku", flat=True):
        m = regex.match(sku)
        if m:
            maior = max(maior, int(m.group(1)))
    for sku in reserved:
        m = regex.match(sku)
        if m:
            maior = max(maior, int(m.group(1)))
    return maior


def suggest_product_sku(category, name: str, reserved=()) -> str:
    """A próxima sequência livre para categoria + nome: REL-LEAO-001, -002…

    ``reserved`` são SKUs que ainda não foram gravados mas já estão prometidos
    (uma tentativa anterior que o banco recusou, por exemplo).
    """
    from apps.catalog.models import Product

    base = product_sku_base(category, name)
    padrao = rf"^{re.escape(base)}-(\d+)$"
    proximo = _highest_sequence(Product.objects.all(), padrao, reserved) + 1
    return f"{base}-{proximo:03d}"


def suggest_variant_sku(product_sku: str, reserved=()) -> str:
    """PRODUTO-V01, -V02… — a próxima sequência livre entre as variantes."""
    from apps.catalog.models import ProductVariant

    base = f"{(product_sku or '').strip().upper()}-V"
    padrao = rf"^{re.escape(base)}(\d+)$"
    proximo = _highest_sequence(ProductVariant.objects.all(), padrao, reserved) + 1
    return f"{base}{proximo:02d}"


def matches_suggested_base(candidate: str, category, name: str) -> bool:
    """``candidate`` é um SKU da família que a sugestão daria para este nome?

    "REL-LEAO-007" pertence à família de (Religioso, "Leão de Judá"), cuja
    base é "REL-LEAO"; "MEU-CODIGO" não. É o que distingue, ao gravar, o SKU
    que a tela sugeriu — e que pode ceder a vez se alguém o levou no meio do
    caminho — de um SKU digitado, que é da pessoa e, colidindo, é erro para
    ela ver.
    """
    base = product_sku_base(category, name)
    return bool(re.match(rf"^{re.escape(base)}-\d+$", (candidate or "").strip().upper()))


def com_sku_livre(suggest, create, attempts: int = MAX_ATTEMPTS):
    """Grava com a sugestão; se o banco recusar o SKU, tenta a sequência seguinte.

    ``suggest(reserved)`` devolve um SKU; ``create(sku)`` grava e devolve o
    objeto. Cada tentativa roda num savepoint: a recusa (``IntegrityError``
    pelo ``unique`` do SKU) não derruba a transação de fora. Só a colisão de
    SKU é tentada de novo — qualquer outro erro de integridade sobe.
    """
    reservados: set[str] = set()
    for _ in range(attempts):
        sku = suggest(reservados)
        try:
            with transaction.atomic():
                return create(sku)
        except IntegrityError as erro:
            if "sku" not in str(erro).lower():
                raise
            reservados.add(sku)
    raise IntegrityError(f"Não foi possível encontrar um SKU livre após {attempts} tentativas.")
