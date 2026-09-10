"""Duplicar: o Admin abre a tela de criação preenchida — e nada nasce antes do Salvar.

Um arquivo só para os sete cadastros, e não um por app, porque o que está sob
teste é **um** mecanismo (``DuplicateAdminMixin``) visto de sete ângulos.
Espalhá-lo por quatro arquivos esconderia que a regra é a mesma.

O formulário é lido da própria página renderizada, por
``campos_do_formulario``, em vez de ser escrito à mão. Isso não é comodidade:
"clicar em Salvar sem modificar nada" só quer dizer alguma coisa se o que se
envia for exatamente o que a tela mostrava.
"""

from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import (
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
from apps.categories.models import Category, CategoryTranslation
from apps.core.admin_mixins import DUPLICATE_PARAM
from apps.core.testing import (
    make_category,
    make_country,
    make_method,
    make_product,
    make_rate,
    make_variant,
    translate_category,
    translate_product,
)
from apps.shipping.models import ShippingMethod, ShippingRate

# ---------------------------------------------------------------------------
# Ler a tela como o navegador a leria
# ---------------------------------------------------------------------------


class _LeitorDeFormulario(HTMLParser):
    """O que o navegador enviaria ao apertar Salvar, sem ninguém tocar em nada.

    Só o formulário pedido: a página do Admin também tem o de sair da conta, e
    a lista tem o de busca.

    As regras são as do navegador, não as do Django: caixa desmarcada não é
    enviada; ``<select>`` sem nada marcado manda a primeira opção; botão e
    arquivo não entram.
    """

    IGNORADOS = {"submit", "button", "image", "reset", "file"}

    #: A linha-molde dos inlines, de onde o JavaScript clona uma linha nova. O
    #: navegador até a envia, e o Django a ignora (o índice não entra em
    #: `TOTAL_FORMS`) — mas ela não é dado de ninguém, e deixá-la aqui faria
    #: um "preço 0,00" fantasma aparecer em toda asserção sobre os filhos.
    MOLDE = "__prefix__"

    def __init__(self, form_id):
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.dados = {}
        self._dentro = False
        self._select = None
        self._primeira_opcao = None
        self._opcao_marcada = None
        self._textarea = None
        self._texto = []

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)

        if tag == "form":
            self._dentro = atributos.get("id") == self.form_id
            return
        if not self._dentro:
            return
        if self.MOLDE in (atributos.get("name") or ""):
            return

        if tag == "input":
            nome = atributos.get("name")
            tipo = (atributos.get("type") or "text").lower()
            if not nome or tipo in self.IGNORADOS:
                return
            if tipo in ("checkbox", "radio"):
                if "checked" in atributos:
                    self.dados[nome] = atributos.get("value", "on")
            else:
                self.dados[nome] = atributos.get("value", "")

        elif tag == "select":
            self._select = atributos.get("name")
            self._primeira_opcao = None
            self._opcao_marcada = None

        elif tag == "option" and self._select is not None:
            valor = atributos.get("value", "")
            if self._primeira_opcao is None:
                self._primeira_opcao = valor
            if "selected" in atributos:
                self._opcao_marcada = valor

        elif tag == "textarea":
            self._textarea = atributos.get("name")
            self._texto = []

    def handle_endtag(self, tag):
        if tag == "form":
            self._dentro = False
        elif tag == "select" and self._select is not None:
            escolhida = self._opcao_marcada
            if escolhida is None:
                escolhida = self._primeira_opcao or ""
            self.dados[self._select] = escolhida
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            self.dados[self._textarea] = "".join(self._texto)
            self._textarea = None

    def handle_data(self, dado):
        if self._textarea is not None:
            self._texto.append(dado)


def campos_do_formulario(html, form_id):
    leitor = _LeitorDeFormulario(form_id)
    leitor.feed(html)
    return leitor.dados


# ---------------------------------------------------------------------------
# O caminho, para qualquer cadastro
# ---------------------------------------------------------------------------


class DuplicarBase(TestCase):
    """Os dois passos do fluxo, num lugar só: pedir a cópia e enviá-la."""

    SENHA = "senha-de-teste-77"

    @classmethod
    def setUpTestData(cls):
        cls.chefe = User.objects.create_superuser(
            "chefe", "chefe@jdprint.test", cls.SENHA
        )

    def setUp(self):
        self.client.force_login(self.chefe)

    # -- endereços ---------------------------------------------------------

    @staticmethod
    def url(obj_ou_model, nome):
        opts = obj_ou_model._meta
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_{nome}")

    # -- passo 1: clicar em "Duplicar" -------------------------------------

    def pedir_duplicacao(self, obj, selecionados=None):
        """A ação, a partir da lista. Devolve a resposta crua."""
        return self.client.post(
            self.url(obj, "changelist"),
            {
                "action": "duplicate_action",
                "index": "0",
                "_selected_action": selecionados or [str(obj.pk)],
            },
        )

    def tela_de_criacao(self, obj):
        """Clica em "Duplicar" e devolve (URL da tela, campos preenchidos)."""
        resposta = self.pedir_duplicacao(obj)
        self.assertEqual(resposta.status_code, 302, "a ação deveria levar a algum lugar")

        destino = resposta["Location"]
        self.assertIn(self.url(obj, "add"), destino)
        self.assertIn(f"{DUPLICATE_PARAM}={obj.pk}", destino)

        pagina = self.client.get(destino)
        self.assertEqual(pagina.status_code, 200)

        html = pagina.content.decode()
        campos = campos_do_formulario(html, f"{obj._meta.model_name}_form")
        self.assertTrue(campos, "nenhum campo lido da tela de criação")

        self.html = html
        self.pagina = pagina
        return destino, campos

    # -- passo 2: salvar ---------------------------------------------------

    def salvar(self, destino, campos, **alteracoes):
        envio = dict(campos)
        envio.update(alteracoes)
        envio["_save"] = "Salvar"
        return self.client.post(destino, envio)

    # -- leitura -----------------------------------------------------------

    def assertCriou(self, resposta):
        self.assertEqual(
            resposta.status_code,
            302,
            f"o registro não foi criado; erros: {self.erros(resposta)}",
        )

    def assertRecusou(self, resposta, trecho):
        self.assertEqual(resposta.status_code, 200, "deveria voltar ao formulário")
        self.assertIn(trecho, resposta.content.decode())

    @staticmethod
    def erros(resposta):
        """Os erros do formulário e dos formsets, para a mensagem de falha."""
        contexto = getattr(resposta, "context", None) or {}
        try:
            partes = [repr(contexto["adminform"].form.errors)]
            partes += [repr(fs.errors) + repr(fs.non_form_errors()) for fs in contexto["inline_admin_formsets"]]
            return " | ".join(partes)
        except Exception:  # pragma: no cover - só melhora a mensagem
            return "(sem contexto de formulário)"


# ---------------------------------------------------------------------------
# 1. A ação
# ---------------------------------------------------------------------------


class AcaoDuplicarTests(DuplicarBase):
    """O botão existe, leva à tela certa e não grava nada."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.preto = Color.objects.create(name="Preto", hex_code="#000000")

    def test_a_acao_aparece_na_lista(self):
        resposta = self.client.get(self.url(Color, "changelist"))

        self.assertIn("duplicate_action", resposta.content.decode())

    def test_a_acao_aparece_na_tela_do_registro(self):
        """Duplicar a partir do registro que se está olhando é o caminho natural."""
        url = reverse("admin:catalog_color_change", args=[self.preto.pk])

        resposta = self.client.get(url)

        escolhas = dict(resposta.context["action_form"].fields["action"].choices)
        self.assertIn("duplicate_action", escolhas)

    def test_duplicar_pela_tela_do_registro_tambem_leva_a_criacao(self):
        url = reverse("admin:catalog_color_change", args=[self.preto.pk])

        resposta = self.client.post(
            url,
            {"CHANGE_FORM-action": "duplicate_action", "_selected_action": [str(self.preto.pk)]},
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(f"{DUPLICATE_PARAM}={self.preto.pk}", resposta["Location"])

    def test_o_clique_em_duplicar_nao_cria_nada(self):
        """O ponto inteiro da etapa: o registro novo não existe ainda."""
        antes = Color.objects.count()

        self.tela_de_criacao(self.preto)

        self.assertEqual(Color.objects.count(), antes)

    def test_a_tela_aberta_e_de_criacao_e_nao_a_edicao_do_original(self):
        destino, _campos = self.tela_de_criacao(self.preto)

        self.assertIn("/add/", destino)
        self.assertNotIn(f"/{self.preto.pk}/change/", destino)
        # `original` é a variável que o Admin usa para dizer "isto já existe".
        self.assertIsNone(self.pagina.context.get("original"))
        self.assertTrue(self.pagina.context["add"])
        self.assertFalse(self.pagina.context["change"])

    def test_cancelar_deixa_o_banco_como_estava(self):
        """Abandonar a tela é sair dela. Nada foi escrito para desfazer."""
        antes = list(Color.objects.values_list("pk", flat=True))

        self.tela_de_criacao(self.preto)
        self.client.get(self.url(Color, "changelist"))

        self.assertEqual(list(Color.objects.values_list("pk", flat=True)), antes)

    def test_dois_registros_selecionados_nao_duplicam_nada(self):
        """Duplicar é sobre **um** registro: dois não têm resposta certa."""
        outra = Color.objects.create(name="Branco", hex_code="#FFFFFF")
        antes = Color.objects.count()

        resposta = self.pedir_duplicacao(self.preto, [str(self.preto.pk), str(outra.pk)])

        # A lista responde 302 para ela mesma quando a ação não faz nada; o que
        # importa é que ninguém foi levado à tela de criação.
        self.assertNotIn("/add/", resposta.get("Location", ""))
        self.assertEqual(Color.objects.count(), antes)

    def test_uma_chave_que_nao_existe_abre_a_tela_de_criacao_vazia(self):
        """Nem erro 500, nem dados de outro registro: um cadastro em branco."""
        resposta = self.client.get(f"{self.url(Color, 'add')}?{DUPLICATE_PARAM}=99999")

        self.assertEqual(resposta.status_code, 200)
        # O nome do outro registro não pode vir preenchido. («Preto» ainda
        # aparece na página como opção do `<select>` de componentes da cor
        # composta — isso é a lista de cores, não dado copiado.)
        self.assertNotIn('value="Preto"', resposta.content.decode())

    def test_uma_chave_que_nao_e_numero_nao_derruba_a_tela(self):
        resposta = self.client.get(f"{self.url(Color, 'add')}?{DUPLICATE_PARAM}=nada")

        self.assertEqual(resposta.status_code, 200)

    def test_o_parametro_nao_vira_campo_do_formulario(self):
        """`_duplicar` é endereço, não valor: o Django copia a query string inteira."""
        _destino, campos = self.tela_de_criacao(self.preto)

        self.assertNotIn(DUPLICATE_PARAM, campos)


# ---------------------------------------------------------------------------
# 2. Permissões
# ---------------------------------------------------------------------------


class PermissaoTests(DuplicarBase):
    """Duplicar é criar. Quem não pode criar não duplica — nem vê o caminho."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.preto = Color.objects.create(name="Preto", hex_code="#000000")

    @staticmethod
    def _staff(username, permissoes):
        pessoa = User.objects.create_user(
            username, f"{username}@jdprint.test", DuplicarBase.SENHA, is_staff=True
        )
        pessoa.user_permissions.set(
            Permission.objects.filter(
                codename__in=permissoes, content_type__app_label="catalog"
            )
        )
        return pessoa

    def test_sem_permissao_de_adicionar_a_acao_nao_e_oferecida(self):
        self.client.force_login(self._staff("olheiro", ["view_color", "change_color"]))

        resposta = self.client.get(self.url(Color, "changelist"))

        self.assertNotIn("duplicate_action", resposta.content.decode())

    def test_sem_permissao_de_adicionar_a_acao_e_recusada_mesmo_forcada(self):
        """A lista some da tela, mas a decisão não pode depender disso."""
        self.client.force_login(self._staff("olheiro", ["view_color", "change_color"]))
        antes = Color.objects.count()

        resposta = self.pedir_duplicacao(self.preto)

        self.assertNotEqual(resposta.status_code, 302)
        self.assertEqual(Color.objects.count(), antes)

    def test_sem_permissao_de_adicionar_a_tela_de_criacao_e_negada(self):
        """A URL montada à mão bate na mesma porta do "Adicionar" de sempre."""
        self.client.force_login(self._staff("olheiro", ["view_color", "change_color"]))

        resposta = self.client.get(f"{self.url(Color, 'add')}?{DUPLICATE_PARAM}={self.preto.pk}")

        self.assertEqual(resposta.status_code, 403)

    def test_quem_pode_criar_mas_nao_pode_ver_nao_recebe_os_dados(self):
        """A chave vem do navegador; a permissão de ler, não.

        Sem permissão de ver o model, a tela de criação abre — mas em branco.
        O registro de origem não é lido, e nada diz se aquela chave existe.
        """
        self.client.force_login(self._staff("cadastrador", ["add_color"]))

        resposta = self.client.get(f"{self.url(Color, 'add')}?{DUPLICATE_PARAM}={self.preto.pk}")

        self.assertEqual(resposta.status_code, 200)
        # Nos campos, e não no HTML inteiro: "#000000" também é o exemplo no
        # texto de ajuda do campo HEX, e aparece com o formulário vazio.
        campos = campos_do_formulario(resposta.content.decode(), "color_form")
        self.assertEqual(campos["name"], "")
        self.assertEqual(campos["hex_code"], "")


# ---------------------------------------------------------------------------
# 3. Cor e material — o mesmo desenho, duas tabelas
# ---------------------------------------------------------------------------


class CorTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.preto = Color.objects.create(name="Preto", hex_code="#000000")
        ColorTranslation.objects.create(master=cls.preto, language="pt", name="Preto")
        ColorTranslation.objects.create(master=cls.preto, language="fr", name="Noir")
        ColorTranslation.objects.create(master=cls.preto, language="nl", name="Zwart")

    def test_a_tela_abre_com_os_dados_do_original(self):
        _destino, campos = self.tela_de_criacao(self.preto)

        self.assertEqual(campos["name"], "Preto")
        self.assertEqual(campos["hex_code"], "#000000")

    def test_o_slug_abre_vazio_por_ser_gerado_no_salvamento(self):
        """Copiá-lo daria "preto-2" sem ninguém pedir — o `save()` faz melhor."""
        _destino, campos = self.tela_de_criacao(self.preto)

        self.assertEqual(campos["slug"], "")

    def test_as_traducoes_vem_preenchidas(self):
        _destino, campos = self.tela_de_criacao(self.preto)

        nomes = {v for k, v in campos.items() if k.endswith("-name") and v}
        self.assertEqual(nomes, {"Preto", "Noir", "Zwart"})

    def test_salvar_sem_mudar_nada_e_recusado(self):
        destino, campos = self.tela_de_criacao(self.preto)
        antes = Color.objects.count()

        resposta = self.salvar(destino, campos)

        self.assertRecusou(resposta, "já existe")
        self.assertEqual(Color.objects.count(), antes)

    def test_mudar_o_nome_cria_a_cor_nova(self):
        destino, campos = self.tela_de_criacao(self.preto)

        resposta = self.salvar(destino, campos, name="Preto Fosco")
        self.assertCriou(resposta)

        nova = Color.objects.get(name="Preto Fosco")
        self.assertNotEqual(nova.pk, self.preto.pk)
        self.assertEqual(nova.hex_code, "#000000")
        self.assertEqual(nova.slug, "preto-fosco")

    def test_as_traducoes_copiadas_sao_linhas_proprias(self):
        """Editar a cópia não pode mexer no original — nem ao contrário."""
        destino, campos = self.tela_de_criacao(self.preto)
        self.assertCriou(self.salvar(destino, campos, name="Preto Fosco"))

        nova = Color.objects.get(name="Preto Fosco")
        originais = set(self.preto.translations.values_list("pk", flat=True))
        copias = set(nova.translations.values_list("pk", flat=True))

        self.assertEqual(len(copias), 3)
        self.assertFalse(originais & copias, "as duas cores compartilham uma tradução")
        self.assertEqual(
            sorted(nova.translations.values_list("language", "name")),
            [("fr", "Noir"), ("nl", "Zwart"), ("pt", "Preto")],
        )

    def test_o_original_fica_exatamente_como_estava(self):
        antes = Color.objects.filter(pk=self.preto.pk).values().first()
        traducoes_antes = sorted(self.preto.translations.values_list("pk", "language", "name"))

        destino, campos = self.tela_de_criacao(self.preto)
        self.assertCriou(self.salvar(destino, campos, name="Preto Fosco"))

        depois = Color.objects.filter(pk=self.preto.pk).values().first()
        self.assertEqual(depois, antes)
        self.assertEqual(
            sorted(self.preto.translations.values_list("pk", "language", "name")),
            traducoes_antes,
        )


class MaterialTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pla = Material.objects.create(name="PLA", description="Rígido e fosco")
        MaterialTranslation.objects.create(master=cls.pla, language="pt", name="PLA")
        MaterialTranslation.objects.create(master=cls.pla, language="fr", name="PLA")

    def test_a_tela_abre_preenchida_e_com_o_slug_vazio(self):
        _destino, campos = self.tela_de_criacao(self.pla)

        self.assertEqual(campos["name"], "PLA")
        self.assertEqual(campos["description"], "Rígido e fosco")
        self.assertEqual(campos["slug"], "")

    def test_salvar_sem_mudar_nada_e_recusado(self):
        destino, campos = self.tela_de_criacao(self.pla)
        antes = Material.objects.count()

        resposta = self.salvar(destino, campos)

        self.assertRecusou(resposta, "já existe")
        self.assertEqual(Material.objects.count(), antes)

    def test_mudar_o_nome_cria_o_material_novo_com_as_traducoes(self):
        destino, campos = self.tela_de_criacao(self.pla)

        self.assertCriou(self.salvar(destino, campos, name="PLA Seda"))

        novo = Material.objects.get(name="PLA Seda")
        self.assertEqual(novo.slug, "pla-seda")
        self.assertEqual(novo.translations.count(), 2)
        self.assertFalse(
            set(novo.translations.values_list("pk", flat=True))
            & set(self.pla.translations.values_list("pk", flat=True))
        )


# ---------------------------------------------------------------------------
# 4. Categoria — a regra de duplicidade que não existia
# ---------------------------------------------------------------------------


class CategoriaTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.raiz = make_category(slug="animais", name="Animais")
        cls.gatos = make_category(slug="gatos", name="Gatos", parent=cls.raiz)
        translate_category(cls.gatos, "fr", "Chats")

    def test_a_tela_abre_com_o_pai_e_os_nomes_do_original(self):
        _destino, campos = self.tela_de_criacao(self.gatos)

        self.assertEqual(campos["parent"], str(self.raiz.pk))
        self.assertEqual(campos["slug"], "")
        nomes = {v for k, v in campos.items() if k.endswith("-name") and v}
        self.assertEqual(nomes, {"Gatos", "Chats"})

    def test_salvar_sem_mudar_nada_e_recusado(self):
        """Sem esta regra o banco aceitava a segunda "Gatos" — com slug "gatos-2"."""
        destino, campos = self.tela_de_criacao(self.gatos)
        antes = Category.objects.count()

        resposta = self.salvar(destino, campos)

        self.assertRecusou(resposta, "Já existe uma categoria com o nome")
        self.assertEqual(Category.objects.count(), antes)

    def test_mudar_o_nome_em_portugues_cria_a_categoria(self):
        destino, campos = self.tela_de_criacao(self.gatos)
        campo_pt = self._campo_do_idioma(campos, "pt")

        self.assertCriou(self.salvar(destino, campos, **{campo_pt: "Cachorros"}))

        nova = Category.objects.get(translations__language="pt", translations__name="Cachorros")
        self.assertEqual(nova.parent_id, self.raiz.pk)
        self.assertEqual(nova.slug, "cachorros")
        self.assertNotEqual(nova.pk, self.gatos.pk)

    def test_o_mesmo_nome_em_outro_nivel_e_permitido(self):
        """A regra é sobre o nível, não sobre o nome: dois "Gatos" em pais
        diferentes são duas categorias distintas para quem navega."""
        outra_raiz = make_category(slug="fantasia", name="Fantasia")
        destino, campos = self.tela_de_criacao(self.gatos)

        self.assertCriou(self.salvar(destino, campos, parent=str(outra_raiz.pk)))

        self.assertEqual(
            Category.objects.filter(translations__name="Gatos").count(), 2
        )

    def test_a_regra_vale_fora_da_duplicacao(self):
        """Não é uma checagem do botão: é uma regra do cadastro."""
        resposta = self.client.post(
            self.url(Category, "add"),
            {
                "parent": str(self.raiz.pk),
                "slug": "",
                "sort_order": "0",
                "is_active": "on",
                "translations-TOTAL_FORMS": "1",
                "translations-INITIAL_FORMS": "0",
                "translations-MIN_NUM_FORMS": "1",
                "translations-MAX_NUM_FORMS": "1000",
                "translations-0-id": "",
                "translations-0-master": "",
                "translations-0-language": "pt",
                "translations-0-name": "  gatos  ",
                "translations-0-description": "",
                "_save": "Salvar",
            },
        )

        self.assertRecusou(resposta, "Já existe uma categoria com o nome")

    def test_as_traducoes_copiadas_sao_independentes(self):
        destino, campos = self.tela_de_criacao(self.gatos)
        campo_pt = self._campo_do_idioma(campos, "pt")
        self.assertCriou(self.salvar(destino, campos, **{campo_pt: "Cachorros"}))

        nova = Category.objects.get(translations__language="pt", translations__name="Cachorros")

        self.assertFalse(
            set(nova.translations.values_list("pk", flat=True))
            & set(self.gatos.translations.values_list("pk", flat=True))
        )
        self.assertEqual(nova.tr("name", language="fr"), "Chats")
        self.assertEqual(self.gatos.name_in("pt"), "Gatos")

    @staticmethod
    def _campo_do_idioma(campos, idioma):
        """O `translations-N-name` cuja linha está no idioma pedido."""
        for chave, valor in campos.items():
            if chave.endswith("-language") and valor == idioma:
                return chave.replace("-language", "-name")
        raise AssertionError(f"nenhuma linha no idioma {idioma}: {campos}")


# ---------------------------------------------------------------------------
# 5. Produto — o cadastro mais pesado, e o que mais se ganha em duplicar
# ---------------------------------------------------------------------------


class ProdutoTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.categoria = make_category(slug="vasos", name="Vasos")
        cls.vaso = make_product(
            sku="VASO-01",
            name="Vaso Facetado",
            category=cls.categoria,
            price=Decimal("27.90"),
            stock_quantity=7,
            weight_grams=Decimal("300.00"),
            status=ProductStatus.ACTIVE,
            variant_sku="VASO-01-PRETO",
        )
        translate_product(cls.vaso, "fr", "Vase à facettes", "Un vase imprimé en 3D")
        make_variant(cls.vaso, sku="VASO-01-BRANCO", price=Decimal("29.90"), stock=4)
        cls.midia = ProductMedia.objects.create(
            product=cls.vaso,
            media_type=MediaType.IMAGE,
            file=SimpleUploadedFile("vaso.jpg", b"conteudo-falso", content_type="image/jpeg"),
            alt_text="Vaso preto",
        )

    # -- o formulário ------------------------------------------------------

    def test_a_tela_abre_com_a_identificacao_e_a_classificacao(self):
        _destino, campos = self.tela_de_criacao(self.vaso)

        # A cópia nasce com a identidade seguinte livre: não há colisão de SKU
        # ao salvar, e o original fica como está.
        self.assertEqual(campos["sku"], "VASO-02")
        self.assertEqual(campos["slug"], "")
        self.assertEqual(campos["category"], str(self.categoria.pk))
        self.assertEqual(campos["status"], ProductStatus.ACTIVE)

    def test_o_conteudo_dos_dois_idiomas_vem_junto(self):
        """É o trabalho que se quer reaproveitar: nome e descrições por idioma."""
        _destino, campos = self.tela_de_criacao(self.vaso)

        nomes = {v for k, v in campos.items() if k.startswith("translations-") and k.endswith("-name") and v}
        self.assertEqual(nomes, {"Vaso Facetado", "Vase à facettes"})
        self.assertIn("Un vase imprimé en 3D", campos.values())

    def test_as_variantes_vem_junto_menos_o_estoque(self):
        """Preço e peso são a base da peça nova; estoque é peça física da antiga."""
        _destino, campos = self.tela_de_criacao(self.vaso)

        skus = {v for k, v in campos.items() if k.startswith("variants-") and k.endswith("-sku") and v}
        self.assertEqual(skus, {"VASO-02-V01", "VASO-02-V02"})

        precos = {v for k, v in campos.items() if k.startswith("variants-") and k.endswith("-sale_price")}
        self.assertEqual(precos, {"27.90", "29.90"})

        estoques = {
            v for k, v in campos.items()
            if k.startswith("variants-") and k.endswith("-stock_quantity")
        }
        self.assertEqual(estoques, {"0"}, "o estoque do original não pode vir junto")

    def test_a_midia_nao_vem_junto(self):
        """Duas linhas apontando para o mesmo arquivo é vínculo, não cópia."""
        _destino, campos = self.tela_de_criacao(self.vaso)

        self.assertEqual(campos["media-TOTAL_FORMS"], "1")  # a linha em branco de sempre
        self.assertNotIn("vaso.jpg", self.html)

    # -- salvar ------------------------------------------------------------

    def test_salvar_sem_mudar_nada_cria_a_copia_com_o_sku_seguinte(self):
        """A sugestão de SKU já é livre: a cópia nasce sem colidir com o original."""
        destino, campos = self.tela_de_criacao(self.vaso)
        antes = Product.objects.count()

        self.assertCriou(self.salvar(destino, campos))

        self.assertEqual(Product.objects.count(), antes + 1)
        copia = Product.objects.get(sku="VASO-02")
        self.assertEqual(
            sorted(copia.variants.values_list("sku", flat=True)), ["VASO-02-V01", "VASO-02-V02"]
        )
        original = Product.objects.get(pk=self.vaso.pk)
        self.assertEqual(original.sku, "VASO-01")
        self.assertEqual(
            sorted(original.variants.values_list("sku", flat=True)), ["VASO-01-BRANCO", "VASO-01-PRETO"]
        )

    def test_salvar_com_o_sku_do_original_e_recusado(self):
        destino, campos = self.tela_de_criacao(self.vaso)
        antes = Product.objects.count()

        resposta = self.salvar(destino, campos, sku="VASO-01")

        self.assertRecusou(resposta, "já existe")
        self.assertEqual(Product.objects.count(), antes)

    def test_mudar_os_skus_cria_o_produto_novo_inteiro(self):
        destino, campos = self.tela_de_criacao(self.vaso)
        alteracoes = {"sku": "VASO-02"}
        for chave, valor in campos.items():
            if chave.endswith("-sku") and valor.startswith("VASO-01-"):
                alteracoes[chave] = valor.replace("VASO-01-", "VASO-02-")

        self.assertCriou(self.salvar(destino, campos, **alteracoes))

        novo = Product.objects.get(sku="VASO-02")
        self.assertNotEqual(novo.pk, self.vaso.pk)
        self.assertEqual(novo.slug, "vaso-facetado-2")
        self.assertEqual(novo.category_id, self.categoria.pk)
        self.assertEqual(novo.translations.count(), 2)
        self.assertEqual(novo.variants.count(), 2)
        self.assertEqual(novo.media.count(), 0)

    def test_nada_e_reaproveitado_do_original(self):
        """Nenhum id, nenhuma linha filha, nenhum arquivo em comum."""
        destino, campos = self.tela_de_criacao(self.vaso)
        alteracoes = {"sku": "VASO-02"}
        for chave, valor in campos.items():
            if chave.endswith("-sku") and valor.startswith("VASO-01-"):
                alteracoes[chave] = valor.replace("VASO-01-", "VASO-02-")
        self.assertCriou(self.salvar(destino, campos, **alteracoes))

        novo = Product.objects.get(sku="VASO-02")

        self.assertFalse(
            set(novo.translations.values_list("pk", flat=True))
            & set(self.vaso.translations.values_list("pk", flat=True))
        )
        self.assertFalse(
            set(novo.variants.values_list("pk", flat=True))
            & set(self.vaso.variants.values_list("pk", flat=True))
        )
        for variante in novo.variants.all():
            self.assertEqual(variante.product_id, novo.pk)

    def test_o_produto_original_fica_intacto(self):
        antes = Product.objects.filter(pk=self.vaso.pk).values().first()
        variantes_antes = sorted(
            self.vaso.variants.values_list("pk", "sku", "sale_price", "stock_quantity")
        )
        traducoes_antes = sorted(self.vaso.translations.values_list("pk", "language", "name"))
        midias_antes = sorted(self.vaso.media.values_list("pk", "file"))

        destino, campos = self.tela_de_criacao(self.vaso)
        alteracoes = {"sku": "VASO-02"}
        for chave, valor in campos.items():
            if chave.endswith("-sku") and valor.startswith("VASO-01-"):
                alteracoes[chave] = valor.replace("VASO-01-", "VASO-02-")
        self.assertCriou(self.salvar(destino, campos, **alteracoes))

        self.assertEqual(Product.objects.filter(pk=self.vaso.pk).values().first(), antes)
        self.assertEqual(
            sorted(self.vaso.variants.values_list("pk", "sku", "sale_price", "stock_quantity")),
            variantes_antes,
        )
        self.assertEqual(
            sorted(self.vaso.translations.values_list("pk", "language", "name")),
            traducoes_antes,
        )
        self.assertEqual(sorted(self.vaso.media.values_list("pk", "file")), midias_antes)

    def test_o_estoque_do_produto_novo_comeca_zerado(self):
        destino, campos = self.tela_de_criacao(self.vaso)
        alteracoes = {"sku": "VASO-02"}
        for chave, valor in campos.items():
            if chave.endswith("-sku") and valor.startswith("VASO-01-"):
                alteracoes[chave] = valor.replace("VASO-01-", "VASO-02-")
        self.assertCriou(self.salvar(destino, campos, **alteracoes))

        novo = Product.objects.get(sku="VASO-02")

        self.assertEqual(list(novo.variants.values_list("stock_quantity", flat=True)), [0, 0])
        self.assertEqual(
            sorted(self.vaso.variants.values_list("stock_quantity", flat=True)), [4, 7]
        )


# ---------------------------------------------------------------------------
# 6. Variante — a tela própria
# ---------------------------------------------------------------------------


class VarianteTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.vaso = make_product(sku="VASO-01", name="Vaso", price=Decimal("27.90"))
        cls.variante = cls.vaso.variants.get()
        ProductVariant.objects.filter(pk=cls.variante.pk).update(
            stock_quantity=9, size="15 cm", weight_grams=Decimal("300.00")
        )
        cls.variante.refresh_from_db()

    def test_a_tela_abre_com_o_produto_e_os_eixos_do_original(self):
        _destino, campos = self.tela_de_criacao(self.variante)

        self.assertEqual(campos["product"], str(self.vaso.pk))
        self.assertEqual(campos["sku"], "VASO-01")
        self.assertEqual(campos["size"], "15 cm")
        self.assertEqual(campos["sale_price"], "27.90")
        self.assertEqual(campos["weight_grams"], "300.00")

    def test_o_estoque_abre_zerado(self):
        _destino, campos = self.tela_de_criacao(self.variante)

        self.assertEqual(campos["stock_quantity"], "0")

    def test_salvar_sem_mudar_nada_e_recusado(self):
        destino, campos = self.tela_de_criacao(self.variante)
        antes = ProductVariant.objects.count()

        resposta = self.salvar(destino, campos)

        self.assertRecusou(resposta, "já existe")
        self.assertEqual(ProductVariant.objects.count(), antes)

    def test_repetir_os_eixos_do_mesmo_produto_e_recusado_mesmo_com_sku_novo(self):
        """Regra do modelo, não do botão: dois eixos iguais no mesmo produto."""
        destino, campos = self.tela_de_criacao(self.variante)

        resposta = self.salvar(destino, campos, sku="VASO-01-B")

        self.assertRecusou(resposta, "Já existe uma variante com esta combinação")

    def test_mudar_sku_e_tamanho_cria_a_variante_nova(self):
        destino, campos = self.tela_de_criacao(self.variante)

        self.assertCriou(self.salvar(destino, campos, sku="VASO-01-20", size="20 cm"))

        nova = ProductVariant.objects.get(sku="VASO-01-20")
        self.assertNotEqual(nova.pk, self.variante.pk)
        self.assertEqual(nova.product_id, self.vaso.pk)
        self.assertEqual(nova.sale_price, Decimal("27.90"))
        self.assertEqual(nova.weight_grams, Decimal("300.00"))
        self.assertEqual(nova.stock_quantity, 0)

    def test_a_variante_original_fica_intacta(self):
        antes = ProductVariant.objects.filter(pk=self.variante.pk).values().first()

        destino, campos = self.tela_de_criacao(self.variante)
        self.assertCriou(self.salvar(destino, campos, sku="VASO-01-20", size="20 cm"))

        self.assertEqual(
            ProductVariant.objects.filter(pk=self.variante.pk).values().first(), antes
        )


# ---------------------------------------------------------------------------
# 7. Entrega — método (com as tarifas) e tarifa
# ---------------------------------------------------------------------------


class MetodoDeEntregaTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.belgica = make_country(iso_code="BE", name="Bélgica")
        cls.franca = make_country(iso_code="FR", name="França")
        cls.metodo = make_method(name="Standard", code="standard")
        make_rate(method=cls.metodo, country=cls.belgica, min_weight=0, max_weight=2000, price="5.90")
        make_rate(method=cls.metodo, country=cls.franca, min_weight=0, max_weight=2000, price="9.90")

    def test_a_tela_abre_com_o_metodo_e_a_grade_de_precos(self):
        """A grade é o que se ganha: sem ela, duplicar pouparia seis campos."""
        _destino, campos = self.tela_de_criacao(self.metodo)

        self.assertEqual(campos["code"], "standard")
        self.assertEqual(campos["name"], "Standard")
        precos = {v for k, v in campos.items() if k.startswith("rates-") and k.endswith("-price")}
        self.assertEqual(precos, {"5.90", "9.90"})

    def test_salvar_sem_mudar_nada_e_recusado(self):
        destino, campos = self.tela_de_criacao(self.metodo)
        antes = ShippingMethod.objects.count()

        resposta = self.salvar(destino, campos)

        self.assertRecusou(resposta, "já existe")
        self.assertEqual(ShippingMethod.objects.count(), antes)

    def test_mudar_o_codigo_cria_o_metodo_com_as_tarifas_proprias(self):
        destino, campos = self.tela_de_criacao(self.metodo)

        self.assertCriou(self.salvar(destino, campos, code="express", name="Express"))

        novo = ShippingMethod.objects.get(code="express")
        self.assertEqual(novo.carrier_id, self.metodo.carrier_id)
        self.assertEqual(novo.rates.count(), 2)
        self.assertFalse(
            set(novo.rates.values_list("pk", flat=True))
            & set(self.metodo.rates.values_list("pk", flat=True))
        )
        for tarifa in novo.rates.all():
            self.assertEqual(tarifa.method_id, novo.pk)

    def test_o_metodo_original_fica_intacto(self):
        antes = ShippingMethod.objects.filter(pk=self.metodo.pk).values().first()
        tarifas_antes = sorted(self.metodo.rates.values_list("pk", "country_id", "price"))

        destino, campos = self.tela_de_criacao(self.metodo)
        self.assertCriou(self.salvar(destino, campos, code="express", name="Express"))

        self.assertEqual(
            ShippingMethod.objects.filter(pk=self.metodo.pk).values().first(), antes
        )
        self.assertEqual(
            sorted(self.metodo.rates.values_list("pk", "country_id", "price")), tarifas_antes
        )


class TarifaDeEntregaTests(DuplicarBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.belgica = make_country(iso_code="BE", name="Bélgica")
        cls.franca = make_country(iso_code="FR", name="França")
        cls.metodo = make_method()
        cls.tarifa = make_rate(
            method=cls.metodo, country=cls.belgica, min_weight=0, max_weight=2000, price="5.90"
        )

    def test_a_tela_abre_com_a_faixa_e_o_preco_do_original(self):
        _destino, campos = self.tela_de_criacao(self.tarifa)

        self.assertEqual(campos["method"], str(self.metodo.pk))
        self.assertEqual(campos["country"], str(self.belgica.pk))
        self.assertEqual(campos["min_weight_grams"], "0")
        self.assertEqual(campos["max_weight_grams"], "2000")
        self.assertEqual(campos["price"], "5.90")

    def test_salvar_sem_mudar_nada_e_recusado(self):
        """A cópia idêntica se sobrepõe à original — dois preços para o mesmo pedido."""
        destino, campos = self.tela_de_criacao(self.tarifa)
        antes = ShippingRate.objects.count()

        resposta = self.salvar(destino, campos)

        self.assertRecusou(resposta, "sobrepõe")
        self.assertEqual(ShippingRate.objects.count(), antes)

    def test_mudar_o_pais_cria_a_tarifa_nova(self):
        destino, campos = self.tela_de_criacao(self.tarifa)

        self.assertCriou(self.salvar(destino, campos, country=str(self.franca.pk), price="9.90"))

        nova = ShippingRate.objects.get(country=self.franca)
        self.assertNotEqual(nova.pk, self.tarifa.pk)
        self.assertEqual(nova.method_id, self.metodo.pk)
        self.assertEqual(nova.max_weight_grams, 2000)

    def test_mudar_a_faixa_cria_a_tarifa_seguinte(self):
        destino, campos = self.tela_de_criacao(self.tarifa)

        self.assertCriou(
            self.salvar(
                destino, campos, min_weight_grams="2000", max_weight_grams="5000", price="8.90"
            )
        )

        nova = ShippingRate.objects.get(min_weight_grams=2000)
        self.assertEqual(nova.country_id, self.belgica.pk)
        self.assertEqual(nova.price, Decimal("8.90"))

    def test_a_tarifa_original_fica_intacta(self):
        antes = ShippingRate.objects.filter(pk=self.tarifa.pk).values().first()

        destino, campos = self.tela_de_criacao(self.tarifa)
        self.assertCriou(self.salvar(destino, campos, country=str(self.franca.pk)))

        self.assertEqual(ShippingRate.objects.filter(pk=self.tarifa.pk).values().first(), antes)


# ---------------------------------------------------------------------------
# 8. A ação está de pé nos sete
# ---------------------------------------------------------------------------


class AcaoPorCadastroTests(DuplicarBase):
    """Cada um dos sete oferece "Duplicar" — perguntado ao próprio Admin.

    Existe por causa de um defeito real: `ProductAdmin` declara ações próprias,
    e o Django monta a lista a partir de `self.actions` e só dela. A tupla da
    subclasse **substituiu** a da mixin, e o produto — justamente o cadastro
    que mais ganha em ser duplicado — ficou sem a opção. Nada acusou: o `check`
    passava, o import passava, e a tela apenas não mostrava o item.

    Por isso a pergunta aqui é feita ao Admin, e não ao código: não "a mixin
    está aplicada", mas "a ação aparece para quem abre a tela".
    """

    ELEGIVEIS = (
        ("catalog", "product"),
        ("catalog", "productvariant"),
        ("catalog", "color"),
        ("catalog", "material"),
        ("categories", "category"),
        ("shipping", "shippingmethod"),
        ("shipping", "shippingrate"),
    )

    def test_os_sete_oferecem_duplicar_na_lista(self):
        for app, model in self.ELEGIVEIS:
            with self.subTest(cadastro=f"{app}.{model}"):
                resposta = self.client.get(reverse(f"admin:{app}_{model}_changelist"))

                escolhas = dict(resposta.context["action_form"].fields["action"].choices)
                self.assertIn("duplicate_action", escolhas)

    def test_as_acoes_que_ja_existiam_continuam_no_lugar(self):
        """Somar não é substituir: o produto mantém ativar, desativar e destacar."""
        resposta = self.client.get(reverse("admin:catalog_product_changelist"))

        escolhas = dict(resposta.context["action_form"].fields["action"].choices)
        for acao in ("action_activate", "action_deactivate", "action_feature", "action_unfeature"):
            self.assertIn(acao, escolhas)


# ---------------------------------------------------------------------------
# 9. Onde "Duplicar" **não** deve estar
# ---------------------------------------------------------------------------


class CadastrosSemDuplicarTests(DuplicarBase):
    """A ação é uma escolha por cadastro, não um enfeite em toda tela.

    Pedido, venda e mensagem de cliente são acontecimentos: não se duplica um
    pedido. Singletons (rodapé, e-mail, chamada da Home) têm `pk=1` fixo.
    Transportadora, país e idioma são entidades do mundo, uma por nome.
    """

    def test_pedido_nao_tem_duplicar(self):
        from apps.orders.models import Order

        resposta = self.client.get(self.url(Order, "changelist"))

        self.assertNotIn("duplicate_action", resposta.content.decode())

    def test_cliente_nao_tem_duplicar(self):
        from apps.accounts.models import Customer

        resposta = self.client.get(self.url(Customer, "changelist"))

        self.assertNotIn("duplicate_action", resposta.content.decode())

    def test_rodape_singleton_nao_tem_duplicar(self):
        from apps.storefront.models import FooterSettings

        resposta = self.client.get(self.url(FooterSettings, "changelist"))

        self.assertNotIn("duplicate_action", resposta.content.decode())

    def test_transportadora_nao_tem_duplicar(self):
        from apps.shipping.models import ShippingCarrier

        resposta = self.client.get(self.url(ShippingCarrier, "changelist"))

        self.assertNotIn("duplicate_action", resposta.content.decode())

    def test_os_sete_elegiveis_tem_duplicar(self):
        """A lista, num teste: acrescentar ou tirar um cadastro passa por aqui."""
        from django.contrib import admin as django_admin

        from apps.core.admin_mixins import DuplicateAdminMixin

        com_duplicar = {
            model._meta.label
            for model, admin_class in django_admin.site._registry.items()
            if isinstance(admin_class, DuplicateAdminMixin)
        }

        self.assertEqual(
            com_duplicar,
            {
                "catalog.Product",
                "catalog.ProductVariant",
                "catalog.Color",
                "catalog.Material",
                "categories.Category",
                "shipping.ShippingMethod",
                "shipping.ShippingRate",
            },
        )
