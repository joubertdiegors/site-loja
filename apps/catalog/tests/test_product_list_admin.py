"""A lista de produtos do Admin: busca, painel de filtros, ordenação e paginação.

O que estes testes guardam é o contrato da tela, não o desenho dela:

* a hierarquia de categorias **não tem profundidade máxima** — filtrar pela
  raiz tem de trazer o bisneto;
* dentro de um grupo os valores somam (**OU**), entre grupos eles estreitam
  (**E**);
* a contagem de cada opção considera os outros filtros, mas não o próprio;
* busca, filtros, ordenação e itens por página sobrevivem à paginação e à
  remoção de um chip;
* o custo em consultas não cresce com o número de produtos;
* permissões, CSRF e as ações em massa continuam onde estavam.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.catalog.models import (
    Brand,
    Color,
    Material,
    PersonalizationType,
    Product,
    ProductColor,
    ProductMaterialComposition,
    ProductStatus,
)
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_variant

URL = reverse("admin:catalog_product_changelist")


class ListaBase(LanguageResetMixin, TestCase):
    """Catálogo pequeno, mas com uma árvore de quatro níveis e um pouco de tudo."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="adm", email="adm@jdprint.test", password="senha-de-teste-77"
        )

        # Religiosos › Santos › Nossa Senhora › Aparecida  (quatro níveis)
        cls.raiz = make_category(slug="religiosos", name="Religiosos")
        cls.filho = make_category(slug="santos", name="Santos", parent=cls.raiz)
        cls.neto = make_category(slug="nossa-senhora", name="Nossa Senhora", parent=cls.filho)
        cls.bisneto = make_category(slug="aparecida", name="Aparecida", parent=cls.neto)
        cls.outra = make_category(slug="decoracao", name="Decoração")

        cls.marca = Brand.objects.create(name="Prusament")
        cls.outra_marca = Brand.objects.create(name="Bambu Lab")
        cls.pla = Material.objects.create(name="PLA")
        cls.petg = Material.objects.create(name="PETG")
        cls.preto = Color.objects.create(name="Preto", hex_code="#000000")
        cls.branco = Color.objects.create(name="Branco", hex_code="#ffffff")

        # Um produto em cada nível da árvore.
        cls.p_raiz = make_product(
            sku="REL-001", name="Cruz", category=cls.raiz, price=Decimal("10.00"),
            brand=cls.marca, variant_sku="REL-001-V1",
        )
        cls.p_filho = make_product(
            sku="SAN-001", name="São Jorge", category=cls.filho, price=Decimal("20.00"),
            brand=cls.marca, variant_sku="SAN-001-V1",
        )
        cls.p_neto = make_product(
            sku="NSR-001", name="Nossa Senhora de Fátima", category=cls.neto,
            price=Decimal("30.00"), brand=cls.outra_marca, variant_sku="NSR-001-V1",
        )
        cls.p_bisneto = make_product(
            sku="APA-001", name="Aparecida grande", category=cls.bisneto,
            price=Decimal("40.00"), brand=cls.outra_marca, variant_sku="APA-001-V1",
        )
        cls.p_outra = make_product(
            sku="DEC-001", name="Vaso", category=cls.outra, price=Decimal("50.00"),
            variant_sku="DEC-001-V1", personalization_type=PersonalizationType.TEXT,
            is_featured=True,
        )
        # Rascunho sem variante nenhuma: "sem configuração" e "esgotado".
        cls.p_rascunho = make_product(
            sku="RAS-001", name="Rascunho", category=cls.outra,
            status=ProductStatus.DRAFT, with_variant=False,
        )

        # Estoque: o da raiz fica baixo, o do filho fica cheio, o do neto zera.
        cls.p_raiz.variants.update(stock_quantity=2)
        cls.p_filho.variants.update(stock_quantity=50)
        cls.p_neto.variants.update(stock_quantity=0)
        cls.p_bisneto.variants.update(stock_quantity=0, made_to_order=True)
        cls.p_outra.variants.update(stock_quantity=9)

        # Material e cor: um pela ficha do produto, outro pela variante.
        ProductMaterialComposition.objects.create(product=cls.p_raiz, material=cls.pla)
        ProductColor.objects.create(product=cls.p_raiz, color=cls.preto)
        cls.p_filho.variants.update(material=cls.petg, color=cls.branco)

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.admin)

    # -- ajudantes ---------------------------------------------------------

    def pagina(self, **parametros):
        resposta = self.client.get(URL, parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def skus(self, resposta):
        return sorted(produto.sku for produto in resposta.context["cl"].result_list)

    def grupo(self, resposta, parametro):
        for grupo in resposta.context["jd_grupos"]:
            if grupo["parametro"] == parametro:
                return grupo
        self.fail(f"grupo «{parametro}» não está no painel")

    def contagens(self, resposta, parametro):
        return {
            opcao["rotulo"]: opcao["contagem"]
            for opcao in self.grupo(resposta, parametro)["opcoes"]
        }


# ---------------------------------------------------------------------------
# 1. Hierarquia de categorias
# ---------------------------------------------------------------------------


class HierarquiaTests(ListaBase):
    def test_a_raiz_traz_todos_os_descendentes(self):
        """O requisito central: filtrar «Religiosos» tem de trazer o bisneto."""
        resposta = self.pagina(categoria=str(self.raiz.pk))
        self.assertEqual(self.skus(resposta), ["APA-001", "NSR-001", "REL-001", "SAN-001"])

    def test_cada_nivel_traz_a_sua_sub_arvore(self):
        for categoria, esperado in (
            (self.filho, ["APA-001", "NSR-001", "SAN-001"]),
            (self.neto, ["APA-001", "NSR-001"]),
            (self.bisneto, ["APA-001"]),
        ):
            with self.subTest(categoria=categoria.slug):
                resposta = self.pagina(categoria=str(categoria.pk))
                self.assertEqual(self.skus(resposta), esperado)

    def test_um_quinto_nivel_continua_funcionando(self):
        """Nada no filtro conta níveis: quem sabe descer é a árvore."""
        tataraneto = make_category(slug="coroada", name="Coroada", parent=self.bisneto)
        make_product(sku="COR-001", name="Coroada", category=tataraneto,
                     price=Decimal("60.00"), variant_sku="COR-001-V1")

        resposta = self.pagina(categoria=str(self.raiz.pk))
        self.assertIn("COR-001", self.skus(resposta))

    def test_as_opcoes_vem_indentadas_na_ordem_da_arvore(self):
        opcoes = self.grupo(self.pagina(), "categoria")["opcoes"]
        por_rotulo = {opcao["rotulo"]: opcao["recuo"] for opcao in opcoes}
        self.assertEqual(por_rotulo["Religiosos"], 0)
        self.assertEqual(por_rotulo["Santos"], 14)
        self.assertEqual(por_rotulo["Nossa Senhora"], 28)
        self.assertEqual(por_rotulo["Aparecida"], 42)

    def test_a_contagem_da_raiz_soma_a_sub_arvore(self):
        contagens = self.contagens(self.pagina(), "categoria")
        self.assertEqual(contagens["Religiosos"], 4)
        self.assertEqual(contagens["Santos"], 3)
        self.assertEqual(contagens["Nossa Senhora"], 2)
        self.assertEqual(contagens["Aparecida"], 1)

    def test_categoria_inativa_continua_no_painel(self):
        self.filho.is_active = False
        self.filho.save()
        rotulos = [opcao["rotulo"] for opcao in self.grupo(self.pagina(), "categoria")["opcoes"]]
        self.assertIn("Santos", rotulos)

    def test_categoria_inexistente_nao_derruba_a_tela(self):
        resposta = self.pagina(categoria="99999")
        self.assertEqual(len(self.skus(resposta)), 6)


# ---------------------------------------------------------------------------
# 2. Combinação: OU dentro do grupo, E entre grupos
# ---------------------------------------------------------------------------


class CombinacaoTests(ListaBase):
    def test_dois_valores_no_mesmo_grupo_somam(self):
        resposta = self.pagina(categoria=[str(self.neto.pk), str(self.outra.pk)])
        self.assertEqual(self.skus(resposta), ["APA-001", "DEC-001", "NSR-001", "RAS-001"])

    def test_grupos_diferentes_estreitam(self):
        resposta = self.pagina(categoria=str(self.raiz.pk), marca=str(self.marca.pk))
        self.assertEqual(self.skus(resposta), ["REL-001", "SAN-001"])

    def test_a_busca_entra_na_conta_com_os_filtros(self):
        resposta = self.pagina(categoria=str(self.raiz.pk), q="Fátima")
        self.assertEqual(self.skus(resposta), ["NSR-001"])

    def test_seis_grupos_ao_mesmo_tempo(self):
        resposta = self.pagina(
            categoria=str(self.raiz.pk),
            status=ProductStatus.ACTIVE,
            marca=str(self.marca.pk),
            material=str(self.petg.pk),
            cor=str(self.branco.pk),
            variantes="unica",
        )
        self.assertEqual(self.skus(resposta), ["SAN-001"])

    def test_combinacao_sem_resultado_mostra_o_estado_vazio(self):
        resposta = self.pagina(categoria=str(self.outra.pk), marca=str(self.marca.pk))
        self.assertEqual(self.skus(resposta), [])
        self.assertContains(resposta, "Nenhum produto encontrado")


# ---------------------------------------------------------------------------
# 3. Cada grupo do painel
# ---------------------------------------------------------------------------


class GruposTests(ListaBase):
    def test_status(self):
        resposta = self.pagina(status=ProductStatus.DRAFT)
        self.assertEqual(self.skus(resposta), ["RAS-001"])

    def test_marca(self):
        resposta = self.pagina(marca=str(self.outra_marca.pk))
        self.assertEqual(self.skus(resposta), ["APA-001", "NSR-001"])

    def test_marca_lista_so_as_que_tem_produto(self):
        Brand.objects.create(name="Sem produto")
        rotulos = [opcao["rotulo"] for opcao in self.grupo(self.pagina(), "marca")["opcoes"]]
        self.assertEqual(rotulos, ["Bambu Lab", "Prusament"])

    def test_material_da_ficha_e_material_da_variante(self):
        """Um veio da composição, o outro da variante — os dois filtram."""
        self.assertEqual(self.skus(self.pagina(material=str(self.pla.pk))), ["REL-001"])
        self.assertEqual(self.skus(self.pagina(material=str(self.petg.pk))), ["SAN-001"])

    def test_cor_da_ficha_e_cor_da_variante(self):
        self.assertEqual(self.skus(self.pagina(cor=str(self.preto.pk))), ["REL-001"])
        self.assertEqual(self.skus(self.pagina(cor=str(self.branco.pk))), ["SAN-001"])

    def test_personalizacao(self):
        resposta = self.pagina(personalizacao=PersonalizationType.TEXT)
        self.assertEqual(self.skus(resposta), ["DEC-001"])

    def test_destaque(self):
        self.assertEqual(self.skus(self.pagina(destaque="sim")), ["DEC-001"])
        self.assertNotIn("DEC-001", self.skus(self.pagina(destaque="nao")))

    def test_variantes(self):
        self.assertEqual(self.skus(self.pagina(variantes="sem")), ["RAS-001"])
        make_variant(self.p_outra, sku="DEC-001-V2", price=Decimal("55.00"), size="G")
        self.assertEqual(self.skus(self.pagina(variantes="multiplas")), ["DEC-001"])

    def test_estoque_segue_as_regras_da_loja(self):
        for valor, esperado in (
            ("baixo", ["REL-001"]),          # 2 unidades
            ("ok", ["DEC-001", "SAN-001"]),  # 9 e 50
            ("esgotado", ["NSR-001", "RAS-001"]),
            ("encomenda", ["APA-001"]),      # sob encomenda, apesar do saldo 0
        ):
            with self.subTest(estoque=valor):
                self.assertEqual(self.skus(self.pagina(estoque=valor)), esperado)

    def test_estoque_cobre_todo_o_catalogo(self):
        """As quatro opções somadas não deixam nenhum produto de fora."""
        vistos = set()
        for valor in ("baixo", "ok", "esgotado", "encomenda"):
            vistos.update(self.skus(self.pagina(estoque=valor)))
        self.assertEqual(vistos, set(Product.objects.values_list("sku", flat=True)))

    def test_venda_sem_estoque_conta_como_disponivel(self):
        self.p_neto.variants.update(allow_backorder=True)
        self.assertIn("NSR-001", self.skus(self.pagina(estoque="ok")))
        self.assertNotIn("NSR-001", self.skus(self.pagina(estoque="esgotado")))

    def test_preco_minimo_e_maximo(self):
        self.assertEqual(self.skus(self.pagina(preco_min="30")), ["APA-001", "DEC-001", "NSR-001"])
        self.assertEqual(self.skus(self.pagina(preco_max="20")), ["REL-001", "SAN-001"])
        self.assertEqual(self.skus(self.pagina(preco_min="20", preco_max="30")), ["NSR-001", "SAN-001"])

    def test_preco_alcanca_a_faixa_inteira_da_variante(self):
        """Produto de €10 a €40 aparece em «entre 30 e 35»: ele tem preço ali."""
        make_variant(self.p_raiz, sku="REL-001-V2", price=Decimal("40.00"), size="G")
        self.assertEqual(self.skus(self.pagina(preco_min="31", preco_max="39")), ["REL-001"])

    def test_preco_invalido_e_ignorado_sem_erro(self):
        resposta = self.pagina(preco_min="dez euros")
        self.assertEqual(len(self.skus(resposta)), 6)

    def test_produto_sem_preco_fica_fora_de_qualquer_faixa(self):
        self.assertNotIn("RAS-001", self.skus(self.pagina(preco_min="0")))


# ---------------------------------------------------------------------------
# 4. Contagens facetadas
# ---------------------------------------------------------------------------


class ContagemTests(ListaBase):
    def test_a_contagem_do_grupo_ignora_o_proprio_grupo(self):
        """Escolher «Prusament» não pode zerar a contagem de «Bambu Lab»."""
        resposta = self.pagina(marca=str(self.marca.pk))
        contagens = self.contagens(resposta, "marca")
        self.assertEqual(contagens["Prusament"], 2)
        self.assertEqual(contagens["Bambu Lab"], 2)

    def test_a_contagem_do_grupo_respeita_os_outros_grupos(self):
        resposta = self.pagina(marca=str(self.marca.pk))
        contagens = self.contagens(resposta, "categoria")
        self.assertEqual(contagens["Religiosos"], 2)
        self.assertEqual(contagens["Nossa Senhora"], 0)

    def test_a_contagem_respeita_a_busca(self):
        contagens = self.contagens(self.pagina(q="Fátima"), "categoria")
        self.assertEqual(contagens["Religiosos"], 1)
        self.assertEqual(contagens["Decoração"], 0)

    def test_a_contagem_nao_e_inflada_por_traducoes_nem_variantes(self):
        """Anotação por subquery: juntar quatro idiomas não multiplica nada."""
        from apps.core.testing import translate_product

        for idioma in ("fr", "nl", "en"):
            translate_product(self.p_raiz, idioma, f"Cruz {idioma}")
        make_variant(self.p_raiz, sku="REL-001-V3", price=Decimal("11.00"), size="M")
        make_variant(self.p_raiz, sku="REL-001-V4", price=Decimal("12.00"), size="G")

        contagens = self.contagens(self.pagina(q="Cruz"), "categoria")
        self.assertEqual(contagens["Religiosos"], 1)


# ---------------------------------------------------------------------------
# 5. Busca
# ---------------------------------------------------------------------------


class BuscaTests(ListaBase):
    def test_por_nome_sku_e_slug(self):
        self.assertEqual(self.skus(self.pagina(q="Vaso")), ["DEC-001"])
        self.assertEqual(self.skus(self.pagina(q="APA-001")), ["APA-001"])
        self.assertEqual(self.skus(self.pagina(q="aparecida-grande")), ["APA-001"])

    def test_por_sku_da_variante(self):
        self.assertEqual(self.skus(self.pagina(q="SAN-001-V1")), ["SAN-001"])

    def test_por_marca(self):
        self.assertEqual(self.skus(self.pagina(q="Prusament")), ["REL-001", "SAN-001"])

    def test_por_traducao_em_outro_idioma(self):
        from apps.core.testing import translate_product

        translate_product(self.p_outra, "fr", "Pot de fleurs")
        self.assertEqual(self.skus(self.pagina(q="fleurs")), ["DEC-001"])

    def test_por_categoria(self):
        self.assertEqual(self.skus(self.pagina(q="Decoração")), ["DEC-001", "RAS-001"])

    def test_por_categoria_ancestral(self):
        """Procurar «Religiosos» acha quem está três níveis abaixo dela."""
        self.assertEqual(
            self.skus(self.pagina(q="Religiosos")),
            ["APA-001", "NSR-001", "REL-001", "SAN-001"],
        )

    def test_busca_sem_resultado(self):
        resposta = self.pagina(q="jamais-existiu")
        self.assertEqual(self.skus(resposta), [])
        self.assertContains(resposta, "Nenhum produto encontrado")


# ---------------------------------------------------------------------------
# 6. Ordenação
# ---------------------------------------------------------------------------


class OrdenacaoTests(ListaBase):
    def coluna(self, resposta, nome):
        return list(resposta.context["cl"].list_display).index(nome)

    def test_por_preco(self):
        resposta = self.pagina()
        indice = self.coluna(resposta, "price_display")
        crescente = self.pagina(o=str(indice), status=ProductStatus.ACTIVE)
        self.assertEqual(
            [p.sku for p in crescente.context["cl"].result_list],
            ["REL-001", "SAN-001", "NSR-001", "APA-001", "DEC-001"],
        )
        decrescente = self.pagina(o=f"-{indice}", status=ProductStatus.ACTIVE)
        self.assertEqual(
            [p.sku for p in decrescente.context["cl"].result_list],
            ["DEC-001", "APA-001", "NSR-001", "SAN-001", "REL-001"],
        )

    def test_por_estoque(self):
        resposta = self.pagina()
        indice = self.coluna(resposta, "stock_display")
        crescente = self.pagina(o=f"-{indice}")
        self.assertEqual(crescente.context["cl"].result_list[0].sku, "SAN-001")  # 50

    def test_por_nome(self):
        resposta = self.pagina()
        indice = self.coluna(resposta, "display_name")
        nomes = [
            p.name_in("pt") for p in self.pagina(o=str(indice)).context["cl"].result_list
        ]
        self.assertEqual(nomes, sorted(nomes))

    def test_produto_sem_preco_tem_lugar_fixo_na_ordenacao(self):
        """A chave de preço nunca é nula: SQLite e PostgreSQL ordenam igual."""
        indice = self.coluna(self.pagina(), "price_display")
        crescente = [p.sku for p in self.pagina(o=str(indice)).context["cl"].result_list]
        decrescente = [p.sku for p in self.pagina(o=f"-{indice}").context["cl"].result_list]
        self.assertEqual(crescente[0], "RAS-001")
        self.assertEqual(decrescente[-1], "RAS-001")
        self.assertEqual(crescente, list(reversed(decrescente)))

    def test_produto_sem_nome_em_portugues_ordena_pelo_sku(self):
        sem_nome = make_product(sku="ZZZ-999", name="", category=self.outra,
                                price=Decimal("1.00"), variant_sku="ZZZ-999-V1")
        indice = self.coluna(self.pagina(), "display_name")
        ordem = [p.sku for p in self.pagina(o=str(indice)).context["cl"].result_list]
        self.assertEqual(ordem[-1], sem_nome.sku)

    def test_o_select_fala_a_mesma_lingua_do_cabecalho(self):
        resposta = self.pagina()
        valores = {opcao["rotulo"]: opcao["valor"] for opcao in resposta.context["jd_ordenacoes"]}
        self.assertEqual(valores["Preço ↑"], str(self.coluna(resposta, "price_display")))
        self.assertEqual(valores["Estoque ↓"], f"-{self.coluna(resposta, 'stock_display')}")

    def test_ordenacao_do_cabecalho_nao_e_perdida_pelo_select(self):
        resposta = self.pagina(o="1.-5")
        ativas = [o for o in resposta.context["jd_ordenacoes"] if o["ativa"]]
        self.assertEqual([o["valor"] for o in ativas], ["1.-5"])


# ---------------------------------------------------------------------------
# 7. Chips, limpar e visões
# ---------------------------------------------------------------------------


class ChipsTests(ListaBase):
    def test_um_chip_por_valor_escolhido(self):
        resposta = self.pagina(categoria=[str(self.raiz.pk), str(self.outra.pk)], q="a")
        rotulos = [chip["rotulo"] for chip in resposta.context["jd_chips"]]
        self.assertIn("Busca: “a”", rotulos)
        self.assertIn("Categoria: Religiosos", rotulos)
        self.assertIn("Categoria: Decoração", rotulos)

    def test_o_chip_remove_so_o_seu_valor(self):
        resposta = self.pagina(categoria=[str(self.raiz.pk), str(self.outra.pk)])
        chip = next(
            c for c in resposta.context["jd_chips"] if c["rotulo"] == "Categoria: Decoração"
        )
        seguinte = self.client.get(URL + chip["url"])
        self.assertEqual(
            seguinte.context["cl"].filter_params["categoria"], [str(self.raiz.pk)]
        )

    def test_o_chip_preserva_busca_ordenacao_e_itens_por_pagina(self):
        resposta = self.pagina(
            categoria=str(self.raiz.pk), marca=str(self.marca.pk), q="a", o="2", por_pagina="12"
        )
        chip = next(c for c in resposta.context["jd_chips"] if c["rotulo"].startswith("Marca"))
        seguinte = self.client.get(URL + chip["url"])
        parametros = seguinte.context["cl"].filter_params
        self.assertEqual(parametros["q"], ["a"])
        self.assertEqual(parametros["o"], ["2"])
        self.assertEqual(parametros["por_pagina"], ["12"])
        self.assertNotIn("marca", parametros)

    def test_chips_de_preco(self):
        resposta = self.pagina(preco_min="10", preco_max="30")
        rotulos = [chip["rotulo"] for chip in resposta.context["jd_chips"]]
        self.assertIn("Preço ≥ €10", rotulos)
        self.assertIn("Preço ≤ €30", rotulos)

    def test_limpar_tudo_zera_filtros_e_busca_mas_guarda_a_leitura(self):
        resposta = self.pagina(categoria=str(self.raiz.pk), q="a", o="2", por_pagina="48")
        seguinte = self.client.get(URL + resposta.context["jd_url_limpar_tudo"])
        parametros = seguinte.context["cl"].filter_params
        self.assertEqual(sorted(parametros), ["o", "por_pagina"])
        self.assertEqual(len(self.skus(seguinte)), 6)

    def test_limpar_de_um_grupo_nao_toca_nos_outros(self):
        resposta = self.pagina(categoria=str(self.raiz.pk), marca=str(self.marca.pk))
        url = self.grupo(resposta, "categoria")["url_limpar"]
        seguinte = self.client.get(URL + url)
        self.assertNotIn("categoria", seguinte.context["cl"].filter_params)
        self.assertEqual(seguinte.context["cl"].filter_params["marca"], [str(self.marca.pk)])


class VisoesTests(ListaBase):
    def visao(self, resposta, rotulo):
        return next(v for v in resposta.context["jd_visoes"] if v["rotulo"] == rotulo)

    def test_as_contagens_das_pilulas(self):
        resposta = self.pagina()
        esperado = {
            "Todos": 6,
            "Rascunhos": 1,
            "Estoque baixo": 1,
            "Sem configuração": 1,
            "Destaques": 1,
        }
        for rotulo, total in esperado.items():
            with self.subTest(visao=rotulo):
                self.assertEqual(self.visao(resposta, rotulo)["contagem"], total)

    def test_a_pilula_e_um_atalho_para_os_mesmos_filtros(self):
        resposta = self.pagina()
        seguinte = self.client.get(URL + self.visao(resposta, "Rascunhos")["url"])
        self.assertEqual(self.skus(seguinte), ["RAS-001"])
        self.assertTrue(self.visao(seguinte, "Rascunhos")["ativa"])
        self.assertIn(
            "Status: Rascunho", [chip["rotulo"] for chip in seguinte.context["jd_chips"]]
        )

    def test_a_pilula_preserva_a_busca(self):
        resposta = self.pagina(q="Aparecida")
        seguinte = self.client.get(URL + self.visao(resposta, "Destaques")["url"])
        self.assertEqual(seguinte.context["cl"].filter_params["q"], ["Aparecida"])

    def test_todos_esta_ativa_quando_nao_ha_recorte(self):
        self.assertTrue(self.visao(self.pagina(), "Todos")["ativa"])


# ---------------------------------------------------------------------------
# 8. Paginação e itens por página
# ---------------------------------------------------------------------------


class PaginacaoTests(ListaBase):
    def povoar(self, quantos=20):
        for indice in range(quantos):
            make_product(
                sku=f"LOT-{indice:03d}", name=f"Lote {indice}", category=self.raiz,
                price=Decimal("5.00"), variant_sku=f"LOT-{indice:03d}-V1",
            )

    def test_o_padrao_e_vinte_e_quatro_por_pagina(self):
        self.povoar()
        resposta = self.pagina()
        self.assertEqual(resposta.context["cl"].list_per_page, 24)
        self.assertEqual(len(resposta.context["cl"].result_list), 24)

    def test_itens_por_pagina_pela_url(self):
        self.povoar()
        resposta = self.pagina(por_pagina="12")
        self.assertEqual(len(resposta.context["cl"].result_list), 12)

    def test_valor_fora_das_opcoes_cai_no_padrao(self):
        self.povoar()
        for bruto in ("9999", "abc", "-3"):
            with self.subTest(por_pagina=bruto):
                self.assertEqual(self.pagina(por_pagina=bruto).context["cl"].list_per_page, 24)

    def test_a_faixa_mostrada(self):
        self.povoar()
        self.assertEqual(self.pagina(por_pagina="12").context["jd_faixa"], "Mostrando 1–12 de 26")
        self.assertEqual(
            self.pagina(por_pagina="12", p="3").context["jd_faixa"], "Mostrando 25–26 de 26"
        )

    def test_a_pagina_seguinte_leva_filtros_busca_e_ordenacao(self):
        self.povoar()
        resposta = self.pagina(categoria=str(self.raiz.pk), q="Lote", o="2", por_pagina="12")
        pagina_dois = next(p for p in resposta.context["jd_paginas"] if p["rotulo"] == "2")
        seguinte = self.client.get(URL + pagina_dois["url"])
        parametros = seguinte.context["cl"].filter_params
        self.assertEqual(parametros["categoria"], [str(self.raiz.pk)])
        self.assertEqual(parametros["q"], ["Lote"])
        self.assertEqual(parametros["o"], ["2"])
        self.assertEqual(parametros["por_pagina"], ["12"])
        self.assertEqual(len(seguinte.context["cl"].result_list), 8)

    def test_mudar_de_filtro_volta_para_a_primeira_pagina(self):
        self.povoar()
        resposta = self.pagina(por_pagina="12", p="2", categoria=str(self.raiz.pk))
        chip = next(c for c in resposta.context["jd_chips"] if c["rotulo"].startswith("Categoria"))
        self.assertNotIn("p=", chip["url"])

    def test_sem_paginacao_quando_cabe_tudo(self):
        self.assertEqual(self.pagina().context["jd_paginas"], [])


# ---------------------------------------------------------------------------
# 9. Desempenho
# ---------------------------------------------------------------------------


class DesempenhoTests(ListaBase):
    def test_o_custo_nao_cresce_com_o_numero_de_produtos(self):
        """O critério de «sem N+1»: mesma consulta com 6 ou com 30 produtos."""
        with CaptureQueriesContext(connection) as poucas:
            self.pagina()

        for indice in range(24):
            produto = make_product(
                sku=f"LOT-{indice:03d}", name=f"Lote {indice}", category=self.neto,
                price=Decimal("7.00"), brand=self.marca, variant_sku=f"LOT-{indice:03d}-V1",
            )
            make_variant(produto, sku=f"LOT-{indice:03d}-V2", price=Decimal("9.00"), size="G")

        with CaptureQueriesContext(connection) as muitas:
            self.pagina()

        self.assertEqual(len(poucas), len(muitas))

    def test_o_custo_nao_cresce_com_os_filtros_ligados(self):
        with CaptureQueriesContext(connection) as simples:
            self.pagina()
        with CaptureQueriesContext(connection) as cheia:
            self.pagina(
                categoria=str(self.raiz.pk), status=ProductStatus.ACTIVE,
                marca=str(self.marca.pk), estoque="ok", q="a", preco_min="1",
            )
        self.assertLessEqual(len(cheia), len(simples) + 2)

    def test_a_arvore_de_categorias_e_lida_uma_vez_por_requisicao(self):
        for indice in range(6):
            make_category(slug=f"extra-{indice}", name=f"Extra {indice}")
        with CaptureQueriesContext(connection) as consultas:
            self.pagina(q="Religiosos")
        arvore = [
            consulta for consulta in consultas.captured_queries
            if 'FROM "categories_category"' in consulta["sql"]
        ]
        self.assertEqual(len(arvore), 1, [c["sql"][:90] for c in arvore])


# ---------------------------------------------------------------------------
# 10. O que já existia continua existindo
# ---------------------------------------------------------------------------


class PreservacaoTests(ListaBase):
    def test_as_seis_acoes_continuam_no_lugar(self):
        escolhas = dict(self.pagina().context["action_form"].fields["action"].choices)
        for acao in (
            "duplicate_action", "action_activate", "action_deactivate",
            "action_feature", "action_unfeature",
        ):
            with self.subTest(acao=acao):
                self.assertIn(acao, escolhas)

    def test_uma_acao_em_massa_roda_a_partir_da_lista(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        pagina = cliente.get(URL)
        csrf = pagina.context["csrf_token"]

        resposta = cliente.post(
            URL,
            {
                "action": "action_deactivate",
                "index": "0",
                "_selected_action": [str(self.p_outra.pk)],
                "csrfmiddlewaretoken": str(csrf),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.p_outra.refresh_from_db()
        self.assertEqual(self.p_outra.status, ProductStatus.INACTIVE)

    def test_a_acao_sem_csrf_e_recusada(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        resposta = cliente.post(
            URL,
            {"action": "action_deactivate", "index": "0", "_selected_action": [str(self.p_outra.pk)]},
        )
        self.assertEqual(resposta.status_code, 403)
        self.p_outra.refresh_from_db()
        self.assertEqual(self.p_outra.status, ProductStatus.ACTIVE)

    def test_as_colunas_e_os_botoes_de_sempre(self):
        html = self.pagina().content.decode()
        for pedaco in ("SKU", "Produto", "Categoria", "Status", "Preço", "Estoque", "Variantes", "Ações"):
            self.assertIn(pedaco, html)
        self.assertIn("+ Novo produto", html)
        self.assertIn("Cadastro completo", html)
        self.assertIn(reverse("admin:catalog_product_quick_add"), html)
        self.assertIn(reverse("admin:catalog_product_add"), html)

    def test_a_hierarquia_de_datas_continua_na_tela(self):
        html = self.pagina().content.decode()
        self.assertIn("jd-datas", html)

    def test_a_hierarquia_de_datas_sobrevive_a_um_filtro(self):
        ano = self.p_raiz.created_at.year
        resposta = self.pagina(created_at__year=str(ano))
        ocultos = {oculto["nome"]: oculto["valor"] for oculto in resposta.context["jd_ocultos"]}
        self.assertEqual(ocultos.get("created_at__year"), str(ano))

    def test_o_filtro_antigo_por_status_continua_valendo(self):
        """URLs guardadas nos favoritos não podem virar erro."""
        resposta = self.pagina(status__exact=ProductStatus.DRAFT)
        self.assertEqual(self.skus(resposta), ["RAS-001"])

    def test_a_lista_pede_login_de_equipe(self):
        cliente = Client()
        resposta = cliente.get(URL)
        self.assertEqual(resposta.status_code, 302)

    def test_quem_nao_pode_ver_produtos_recebe_403(self):
        equipe = get_user_model().objects.create_user(
            username="equipe", email="e@jdprint.test", password="senha-de-teste-77", is_staff=True
        )
        cliente = Client()
        cliente.force_login(equipe)
        self.assertEqual(cliente.get(URL).status_code, 403)


# ---------------------------------------------------------------------------
# 11. Anatomia da tela
# ---------------------------------------------------------------------------


class AnatomiaTests(ListaBase):
    def test_os_pedacos_do_modelo_estao_todos_na_pagina(self):
        html = self.pagina(categoria=str(self.raiz.pk)).content.decode()
        for pedaco in (
            'class="breadcrumbs"',            # trilha
            "jd-lista-contador",              # contador
            "jd-visoes",                      # pílulas
            'id="jd-painel"',                 # painel de filtros
            "jd-busca",                       # busca principal
            "jd-painel-grade",                # grade de grupos
            "jd-grupo-preco",                 # preço mín/máx
            "jd-chips",                       # filtros ativos
            "jd-barra-controles",             # ordenação e itens por página
            'id="result_list"',               # tabela nativa
            "jd-massa",                       # ações em massa
            "jd-paginacao",                   # paginação
        ):
            with self.subTest(pedaco=pedaco):
                self.assertIn(pedaco, html)

    def test_o_contador_diz_o_total_e_o_resultado(self):
        resposta = self.pagina(categoria=str(self.raiz.pk))
        self.assertEqual(resposta.context["jd_total"], 6)
        self.assertEqual(resposta.context["jd_resultados"], 4)
        self.assertContains(resposta, "6 produtos no catálogo")
        self.assertContains(resposta, "4 resultados com os filtros atuais")

    def test_sao_dois_formularios_irmaos_e_nao_aninhados(self):
        """O painel é GET, a seleção é POST — um dentro do outro não existe."""
        html = self.pagina().content.decode()
        painel = html.index('id="jd-painel"')
        fim_do_painel = html.index("</form>", painel)
        self.assertLess(fim_do_painel, html.index('id="changelist-form"'))

    def test_o_painel_traz_os_dez_grupos_do_modelo(self):
        resposta = self.pagina()
        titulos = [grupo["titulo"] for grupo in resposta.context["jd_grupos"]]
        self.assertEqual(
            titulos,
            ["Categoria", "Status", "Marca", "Material", "Cor",
             "Personalização", "Estoque", "Variantes", "Destaque"],
        )
        self.assertIn("Preço", resposta.content.decode())

    def test_o_botao_do_grupo_resume_o_que_esta_escolhido(self):
        vazio = self.grupo(self.pagina(), "categoria")
        self.assertEqual(vazio["texto"], "Todas as categorias")
        self.assertEqual(vazio["quantidade"], 0)

        um = self.grupo(self.pagina(categoria=str(self.raiz.pk)), "categoria")
        self.assertEqual(um["texto"], "Religiosos")

        dois = self.grupo(
            self.pagina(categoria=[str(self.raiz.pk), str(self.outra.pk)]), "categoria"
        )
        self.assertEqual(dois["quantidade"], 2)
        self.assertIn("Religiosos", dois["texto"])

    def test_as_opcoes_marcadas_voltam_marcadas(self):
        grupo = self.grupo(self.pagina(marca=str(self.marca.pk)), "marca")
        marcadas = [opcao["rotulo"] for opcao in grupo["opcoes"] if opcao["marcada"]]
        self.assertEqual(marcadas, ["Prusament"])
