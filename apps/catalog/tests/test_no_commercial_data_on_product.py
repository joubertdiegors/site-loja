"""A trave da etapa 8, cobrada no código-fonte.

O ``Product`` deixou de ter preço, estoque, peso e prazo de produção — quem os
tem é a ``ProductVariant``. Os testes de modelo já garantem que os campos não
existem mais; estes aqui garantem que **ninguém volta a escrevê-los**, nem numa
propriedade derivada, nem num template, nem num serviço.

Por que um teste que lê arquivos: o dia em que alguém acrescentar de novo um
``price`` ao Product para "facilitar", o modelo volta a compilar e os testes de
comportamento continuam passando — porque o valor estaria certo *naquele*
momento. O que quebra é depois, quando as duas fontes divergem e o cliente paga
um preço e recebe outro. A única forma de pegar isso é proibir a leitura.

Alcance: a varredura casa pelos nomes por que o produto é chamado no código
(`product`, `produto`, `line.product`, `item.product`). Um apelido exótico
escapa dela — por isso a metade decisiva desta trave é o teste de modelo em
`test_product.py`, que prova que os campos não existem mais e portanto não há
o que ler, com nome nenhum.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Onde o código comercial mora: carrinho, checkout, frete, pedidos e catálogo.
APPS = ("cart", "catalog", "orders", "shipping", "home")

#: Leituras proibidas. ``price`` cobre ``sale_price`` e ``price``; os demais são
#: os nomes exatos que a etapa 8 moveu para a variante.
PROIBIDOS = (
    "sale_price",
    "price",
    "stock_quantity",
    "weight_grams",
    "production_lead_time_days",
    "allow_backorder",
    "filament_cost",
    "energy_cost",
    "total_cost",
    "profit_margin",
    "pricing_mode",
)

#: ``product.<campo>`` / ``produto.<campo>`` / ``line.product.<campo>``…
PADRAO_PY = re.compile(
    r"\b(?:product|produto|self\.product|line\.product|item\.product)\.(" +
    "|".join(PROIBIDOS) + r")\b"
)

#: Em template: ``{{ product.price }}``, ``{% if line.product.stock_quantity %}``.
PADRAO_HTML = re.compile(
    r"\b(?:product|produto|line\.product|item\.product)\.(" + "|".join(PROIBIDOS) + r")\b"
)

#: Onde a leitura é legítima.
#:
#: - ``models.py`` do catálogo: é lá que as propriedades derivadas moram, e
#:   elas leem ``self.default_variant.<campo>``, nunca um campo do produto;
#: - migrations: a 0004 move os dados justamente lendo o produto antigo;
#: - os próprios testes, que precisam nomear o que está proibido.
ISENTOS = ("migrations", "tests", "test_")


def arquivos_comerciais():
    """Todo .py e .html de código comercial, fora das isenções."""
    raiz = Path(settings.BASE_DIR)
    alvos = [raiz / "apps" / nome for nome in APPS]
    alvos.append(raiz / "templates")

    for base in alvos:
        if not base.exists():
            continue
        for caminho in list(base.rglob("*.py")) + list(base.rglob("*.html")):
            partes = caminho.parts
            if any(isento in parte for parte in partes for isento in ISENTOS):
                continue
            yield caminho


class ProductIsNotACommercialSourceTests(SimpleTestCase):
    def ocorrencias(self):
        achados = []
        for caminho in arquivos_comerciais():
            padrao = PADRAO_PY if caminho.suffix == ".py" else PADRAO_HTML
            texto = caminho.read_text(encoding="utf-8")
            for numero, linha in enumerate(texto.splitlines(), 1):
                if "etapa-8-ok" in linha:  # escape explícito e revisado
                    continue
                achado = padrao.search(linha)
                if achado:
                    achados.append(f"{caminho.name}:{numero}: {linha.strip()}")
        return achados

    def test_no_commercial_code_reads_price_or_stock_from_the_product(self):
        achados = self.ocorrencias()
        self.assertEqual(
            achados,
            [],
            "Código comercial voltou a ler dado de variante no Product:\n"
            + "\n".join(achados)
            + "\n\nEsses valores são da ProductVariant. Use `line.variant`, "
            "`selected_variant` ou `product.default_variant`.",
        )

    def test_the_guard_actually_matches_something(self):
        """Se o padrão parasse de casar, o teste acima passaria por engano."""
        self.assertTrue(PADRAO_PY.search("total = product.sale_price * quantity"))
        self.assertTrue(PADRAO_PY.search("if self.product.stock_quantity > 0:"))
        self.assertTrue(PADRAO_HTML.search("{{ line.product.weight_grams }}"))
        self.assertIsNone(PADRAO_PY.search("peso = variant.weight_grams"))
        self.assertIsNone(PADRAO_HTML.search("{{ selected_variant.sale_price }}"))

    def test_the_guard_looks_at_real_files(self):
        """Uma isenção larga demais esvaziaria a varredura sem avisar."""
        arquivos = list(arquivos_comerciais())
        nomes = {caminho.name for caminho in arquivos}

        self.assertGreater(len(arquivos), 30)
        for esperado in ("cart.py", "services.py", "views.py", "product_card.html"):
            self.assertIn(esperado, nomes)
