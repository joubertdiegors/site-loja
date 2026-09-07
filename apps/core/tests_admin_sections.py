"""O menu do Admin agrupado por assunto (`config/admin.py`).

Reagrupar o menu é uma mudança de **apresentação**, e o risco de uma mudança
dessas é justamente parecer inofensiva: um model que some da tela é um cadastro
que ninguém mais encontra, e um link que quebra só aparece no dia em que
alguém clica.

Por isso os testes daqui não checam estética. Checam três coisas que a
reorganização não pode ter mexido: **nada sumiu**, **nada mudou de endereço** e
**ninguém ganhou acesso** — e, desde que o menu passou a seguir a loja como
ela é vista, que a HOME vem na ordem da página e que o que faz parte de um
bloco (as pílulas do "Sobre a loja", o carrossel dos banners) aparece junto
dele.
"""

from django.contrib import admin as dj_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.core.testing import LanguageResetMixin
from config.admin import SECOES, Grupo, Item, itens_da_secao, modelos_da_secao, modelos_listados

SENHA = "senha-de-teste-77"


def registrados() -> set[str]:
    """`{"orders.order", ...}` — tudo que está registrado no Admin agora."""
    return {
        f"{model._meta.app_label}.{model._meta.model_name}"
        for model in dj_admin.site._registry
    }


def secoes_da_pagina(response) -> list[tuple[str, list[str]]]:
    """As seções do menu, na ordem, com o nome dos models de cada uma (sem os subtítulos)."""
    return [
        (str(app["name"]), [str(model["name"]) for model in app["models"] if not model.get("heading")])
        for app in response.context["app_list"]
    ]


def linhas_da_secao(response, titulo) -> list[str]:
    """Tudo o que a seção desenha, na ordem: subtítulos entre colchetes, itens recuados com ↳."""
    for app in response.context["app_list"]:
        if str(app["name"]) == titulo:
            return [
                f"[{model['name']}]" if model.get("heading") else ("↳ " if model.get("sub") else "") + str(model["name"])
                for model in app["models"]
            ]
    raise KeyError(titulo)


class AdminBase(LanguageResetMixin, TestCase):
    def equipe(self, username, permissoes=(), **extra):
        pessoa = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@jdprint.test",
            password=SENHA,
            is_staff=True,
            **extra,
        )
        if permissoes:
            pessoa.user_permissions.set(
                Permission.objects.filter(
                    codename__in=[p.split(".")[1] for p in permissoes],
                    content_type__app_label__in=[p.split(".")[0] for p in permissoes],
                )
            )
        return pessoa

    def indice(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("admin:index"))


# ---------------------------------------------------------------------------
# 1. Nada sumiu
# ---------------------------------------------------------------------------


class NadaSumiuTests(AdminBase):
    def setUp(self):
        super().setUp()
        self.chefe = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", SENHA
        )

    def test_the_menu_shows_every_registered_model(self):
        """O que está registrado aparece — inclusive o que ninguém listou."""
        na_tela = set()
        for app in self.indice(self.chefe).context["app_list"]:
            for model in app["models"]:
                if not model.get("heading"):
                    na_tela.add(model["object_name"].lower())

        faltando = {
            chave for chave in registrados() if chave.split(".")[1] not in na_tela
        }
        self.assertEqual(faltando, set(), f"sumiram do menu: {sorted(faltando)}")

    def test_no_model_appears_twice(self):
        """Aparecer duas vezes é pior que não aparecer: dois lugares para procurar."""
        vistos = []
        for app in self.indice(self.chefe).context["app_list"]:
            vistos.extend(model["object_name"] for model in app["models"] if not model.get("heading"))

        repetidos = {nome for nome in vistos if vistos.count(nome) > 1}
        self.assertEqual(repetidos, set(), f"duplicados no menu: {sorted(repetidos)}")

    def test_the_listed_models_all_exist(self):
        """`SECOES` não pode citar um model que não está registrado.

        Uma linha órfã não quebra a tela — o `get_app_list` a ignora —, mas
        mente sobre onde a coisa está. Quem lê a lista para saber onde mexer
        precisa que ela corresponda ao Admin de verdade.
        """
        self.assertEqual(
            modelos_listados() - registrados(), set(), "SECOES cita model não registrado"
        )

    def test_every_registered_model_has_a_place_in_the_sections(self):
        """Nada fica no "resto": todo cadastro tem uma seção pensada para ele.

        O `get_app_list` continua mostrando o que não estiver listado (no grupo
        do próprio app), mas isso é rede de segurança — hoje a lista é completa,
        e um model novo sem lugar é o que este teste acusa.
        """
        self.assertEqual(registrados() - modelos_listados(), set())

    def test_no_model_is_listed_twice_in_the_sections(self):
        chaves = [item.chave for _t, entradas in SECOES for item in itens_da_secao(entradas)]

        self.assertEqual(len(chaves), len(set(chaves)))

    def test_the_important_models_are_all_placed(self):
        """Os cadastros do dia a dia têm de estar numa seção, não no resto."""
        essenciais = {
            "orders.order",
            "orders.payment",
            "orders.paymentproof",
            "catalog.product",
            "catalog.productvariant",
            "categories.category",
            "accounts.customer",
            "accounts.customeraddress",
            "accounts.favorite",
            "shipping.shippingcarrier",
            "shipping.shippingmethod",
            "shipping.shippingrate",
            "core.deliverycountry",
            "core.emailsettings",
            "core.sitelanguage",
            "orders.bankaccount",
            "accounts.user",
            "auth.group",
        }
        self.assertEqual(essenciais - modelos_listados(), set())


# ---------------------------------------------------------------------------
# 2. As seções
# ---------------------------------------------------------------------------


class OrdemDasSecoesTests(AdminBase):
    def setUp(self):
        super().setUp()
        self.chefe = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", SENHA
        )
        self.secoes = secoes_da_pagina(self.indice(self.chefe))

    def test_the_sections_come_in_the_declared_order(self):
        titulos = [nome for nome, _models in self.secoes]
        esperados = [str(titulo) for titulo, _chaves in SECOES]

        self.assertEqual(titulos[: len(esperados)], esperados)

    def test_nothing_is_left_over_after_the_sections(self):
        """Tudo está numa seção: não sobra caixa de app no fim da página."""
        titulos = [nome for nome, _models in self.secoes]

        self.assertEqual(titulos[len(SECOES) :], [])

    def test_orders_leads_the_menu(self):
        """É a tela aberta todo dia; nenhuma outra disputa o primeiro lugar."""
        self.assertEqual(self.secoes[0][0], "PEDIDOS")

    def test_each_model_sits_in_its_declared_section(self):
        por_titulo = {nome: models for nome, models in self.secoes}

        for titulo, entradas in SECOES:
            with self.subTest(secao=str(titulo)):
                self.assertEqual(len(por_titulo[str(titulo)]), len(itens_da_secao(entradas)))

    def test_the_customer_registry_does_not_carry_the_login_account(self):
        """"Usuários" é da equipe; quem compra é "Clientes".

        Misturar os dois é o que fazia a tela antiga: um cadastro de acesso
        interno aparecendo junto de quem comprou, com o mesmo peso visual.
        """
        clientes = dict(self.secoes)["CLIENTES"]

        self.assertNotIn("Usuários", clientes)

    def test_the_team_section_holds_the_login_accounts(self):
        equipe = dict(self.secoes)["EQUIPE"]

        self.assertIn("Usuários", equipe)
        self.assertIn("Grupos de permissão", equipe)

    def test_the_menu_is_not_a_wall_of_boxes(self):
        """Sete caixas; a maior (HOME) tem 13 itens, divididos por subtítulos."""
        self.assertLessEqual(len(self.secoes), 8)
        for nome, models in self.secoes:
            with self.subTest(secao=nome):
                self.assertLessEqual(len(models), 14)

    def test_home_follows_the_page_from_top_to_bottom(self):
        """Faixa do topo, banner, blocos na ordem da página, rodapé.

        Desde a composição livre (etapa 20) a ordem dos blocos é a de «Seções
        da Home»; no menu, os cadastros de conteúdo de cada bloco vêm recuados
        abaixo dela.
        """
        self.assertEqual(
            linhas_da_secao(self.indice(self.chefe), "HOME"),
            [
                "[1 · Faixa do topo]",
                "Frases da faixa do topo",
                "[2 · Banner principal]",
                "Banners",
                "↳ Carrossel",
                "[3 · Seções da Home]",
                "Seções da Home — a ordem da página",
                "↳ Blocos de categorias",
                "↳ Cards de «Como trabalhamos»",
                "↳ Textos da chamada final",
                "↳ Passos da chamada final",
                "↳ Blocos «Sobre a loja»",
                "↳ Pílulas do «Sobre a loja»",
                "[4 · Rodapé]",
                "Textos e contato do rodapé",
                "Colunas do rodapé",
                "↳ Links do rodapé",
                "Páginas da loja",
            ],
        )

    def test_maintenance_and_launch_are_grouped_in_the_settings(self):
        linhas = linhas_da_secao(self.indice(self.chefe), "CONFIGURAÇÕES DA LOJA")
        inicio = linhas.index("[Manutenção e lançamento]")

        self.assertEqual(
            linhas[inicio : inicio + 4],
            ["[Manutenção e lançamento]", "Páginas especiais", "↳ Benefícios das páginas", "↳ Inscritos do lançamento"],
        )

    def test_the_names_on_screen_are_for_the_shopkeeper_not_the_model(self):
        """O nome técnico do model fica no código; a tela fala a língua da loja."""
        html = self.indice(self.chefe).content.decode()

        self.assertNotIn("PÍLULAS — as etiquetas coloridas", html)
        self.assertNotIn("CARDS — como trabalhamos", html)
        self.assertIn("Pílulas do «Sobre a loja»", html)
        self.assertIn("Como trabalhamos", html)
        # ...mas cada nome continua levando ao mesmo endereço de sempre.
        self.assertIn('href="/admin/home/homeaboutbadge/"', html)
        self.assertIn('href="/admin/home/homecard/"', html)

    def test_hints_show_on_the_index_and_not_in_the_sidebar(self):
        indice = self.indice(self.chefe).content.decode()
        self.assertIn('<small class="jd-dica">O comportamento: rotação automática, setas e indicadores.</small>', indice)

        lateral = self.client.get(reverse("admin:orders_order_changelist")).content.decode()
        self.assertNotIn("O comportamento: rotação automática", lateral)
        self.assertIn(">Carrossel</a>", lateral)

    def test_sections_declare_no_unknown_kind_of_entry(self):
        for _titulo, entradas in SECOES:
            for entrada in entradas:
                self.assertIsInstance(entrada, (Item, Grupo))


# ---------------------------------------------------------------------------
# 3. Nada mudou de endereço
# ---------------------------------------------------------------------------


class EnderecosIntactosTests(AdminBase):
    def setUp(self):
        super().setUp()
        self.chefe = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", SENHA
        )
        self.client.force_login(self.chefe)

    def test_every_changelist_still_opens(self):
        """Um link do menu que dá 404 é um cadastro perdido.

        Com `follow=True` porque alguns cadastros de linha única — o rodapé, o
        destaque da home — mandam a lista direto para a própria linha. Isso é
        de propósito e já era assim; o que o teste procura é a tela que **não**
        abre.
        """
        quebrados = []
        for chave in sorted(registrados()):
            app, model = chave.split(".")
            try:
                url = reverse(f"admin:{app}_{model}_changelist")
            except NoReverseMatch:
                quebrados.append(f"{chave}: sem URL")
                continue
            resposta = self.client.get(url, follow=True)
            if resposta.status_code != 200 or "/admin/login/" in resposta.request["PATH_INFO"]:
                quebrados.append(f"{chave}: {url} -> {resposta.status_code}")

        self.assertEqual(quebrados, [], f"changelists quebradas: {quebrados}")

    def test_the_urls_keep_the_app_name_and_not_the_section(self):
        """A seção é rótulo de tela; o endereço continua sendo o do app.

        É o que garante que link salvo, favorito do navegador e URL colada num
        e-mail antigo continuam abrindo.
        """
        self.assertEqual(
            reverse("admin:orders_order_changelist"), "/admin/orders/order/"
        )
        self.assertEqual(
            reverse("admin:accounts_customer_changelist"), "/admin/accounts/customer/"
        )

    def test_the_per_app_page_still_lists_its_own_app(self):
        """`/admin/orders/` responde sobre `orders`, não sobre "PEDIDOS".

        Ali a pergunta é "o que existe neste app": reagrupar seria responder
        outra coisa — e a página não teria como listar o que veio de fora.
        """
        resposta = self.client.get("/admin/orders/")

        titulos = [str(app["name"]) for app in resposta.context["app_list"]]
        self.assertEqual(titulos, ["Pedidos"])

    def test_the_order_page_still_opens(self):
        """A tela mais usada da loja, aberta de ponta a ponta."""
        self.assertEqual(
            self.client.get(reverse("admin:orders_order_changelist")).status_code, 200
        )


# ---------------------------------------------------------------------------
# 4. O título da seção não finge ser um link
# ---------------------------------------------------------------------------


class TituloDaSecaoTests(AdminBase):
    """O `app_list.html` do Django supõe que toda caixa é um app com página.

    Uma seção por assunto não é: ela vem sem `app_url`. Sem o ajuste do
    template (`templates/admin/app_list.html`), o `href=""` vira um título
    clicável que recarrega a página, e o `{% if app.app_url in request.path %}`
    — `""` está contido em qualquer coisa — marca **todas** as caixas como a
    atual.
    """

    def setUp(self):
        super().setUp()
        chefe = get_user_model().objects.create_superuser(
            "chefe", "chefe@jdprint.test", SENHA
        )
        self.html = self.indice(chefe).content.decode()

    def test_no_caption_links_to_nowhere(self):
        self.assertNotIn('<a href="" class="section"', self.html)

    def test_the_section_title_is_plain_text(self):
        self.assertIn('<span class="section">PEDIDOS</span>', self.html)

    def test_a_group_heading_is_not_a_link_either(self):
        """O subtítulo dentro da seção organiza; não leva a lugar nenhum."""
        self.assertIn('<tr class="jd-grupo">', self.html)
        self.assertNotIn('<a href="">', self.html)

    def test_the_per_app_pages_still_exist(self):
        """Sem caixas de app no índice, `/admin/home/` continua abrindo."""
        self.assertEqual(self.client.get("/admin/home/").status_code, 200)

    def test_only_the_open_page_is_the_current_one(self):
        """Oito caixas destacadas ao mesmo tempo é o mesmo que nenhuma."""
        self.assertEqual(self.html.count("current-app"), 0)


# ---------------------------------------------------------------------------
# 5. Ninguém ganhou acesso
# ---------------------------------------------------------------------------


class PermissoesIntactasTests(AdminBase):
    def test_the_menu_only_shows_what_the_person_may_see(self):
        """A seção não é uma porta: ela desenha o que a permissão já permitia."""
        pessoa = self.equipe("consulta", permissoes=["orders.view_order"])

        secoes = secoes_da_pagina(self.indice(pessoa))

        self.assertEqual(secoes, [("PEDIDOS", ["Pedidos"])])

    def test_a_group_heading_without_visible_items_is_not_drawn(self):
        """Quem só vê os banners não recebe os subtítulos dos blocos vazios."""
        pessoa = self.equipe("banners", permissoes=["home.view_homebanner"])

        self.assertEqual(
            linhas_da_secao(self.indice(pessoa), "HOME"),
            ["[2 · Banner principal]", "Banners"],
        )

    def test_an_empty_section_does_not_show_up(self):
        """Caixa vazia é ruído: promete um conteúdo que não existe para quem lê."""
        pessoa = self.equipe("catalogo", permissoes=["catalog.view_product"])

        titulos = [nome for nome, _models in secoes_da_pagina(self.indice(pessoa))]

        self.assertEqual(titulos, ["CATÁLOGO"])

    def test_the_section_does_not_open_a_changelist(self):
        """Ver o nome do model no menu e poder abri-lo continuam sendo coisas
        diferentes — e é o Django que decide a segunda."""
        pessoa = self.equipe("consulta", permissoes=["orders.view_order"])
        self.client.force_login(pessoa)

        self.assertEqual(
            self.client.get(reverse("admin:catalog_product_changelist")).status_code, 403
        )

    def test_staff_without_permissions_sees_an_empty_menu(self):
        pessoa = self.equipe("recem-chegado")

        self.assertEqual(secoes_da_pagina(self.indice(pessoa)), [])

    def test_a_customer_still_cannot_reach_the_admin(self):
        """Quem compra não é da equipe, e o menu novo não muda isso."""
        cliente = get_user_model().objects.create_user(
            username="ana", email="ana@exemplo.test", password=SENHA
        )
        self.client.force_login(cliente)

        resposta = self.client.get(reverse("admin:index"))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/admin/login/", resposta["Location"])

    def test_a_customer_cannot_reach_a_changelist_either(self):
        cliente = get_user_model().objects.create_user(
            username="ana", email="ana@exemplo.test", password=SENHA
        )
        self.client.force_login(cliente)

        resposta = self.client.get(reverse("admin:orders_order_changelist"))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/admin/login/", resposta["Location"])

    def test_a_group_keeps_working_as_the_way_to_give_access(self):
        """As permissões continuam sendo as do Django, por app e por model."""
        grupo = Group.objects.create(name="Atendimento")
        grupo.permissions.set(
            Permission.objects.filter(
                content_type__app_label="orders", codename__in=["view_order", "change_order"]
            )
        )
        pessoa = self.equipe("atendente")
        pessoa.groups.add(grupo)

        self.assertEqual(secoes_da_pagina(self.indice(pessoa)), [("PEDIDOS", ["Pedidos"])])
        self.client.force_login(pessoa)
        self.assertEqual(
            self.client.get(reverse("admin:orders_order_changelist")).status_code, 200
        )
