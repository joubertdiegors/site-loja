"""O menu do Admin, organizado como a loja é vista — e não por app Python.

## O problema

O Django agrupa os models por **app**, e app é uma divisão de código: quem
trabalha na loja não sabe (nem deve saber) que "Países de entrega" mora em
`core` e "Transportadoras" em `shipping`, nem por que as "Pílulas" apareciam
longe do bloco "Sobre a loja" de que fazem parte, ou o "Carrossel" longe dos
banners que ele gira.

## A saída

`AdminSite.get_app_list()` é o ponto de extensão que o próprio Django oferece
para isto, e a troca do site padrão é feita pelo caminho oficial —
`AdminConfig.default_site`, em `config/apps.py`, apontado no `INSTALLED_APPS`.

Cada **seção** (uma caixa do índice) responde a uma pergunta de quem opera a
loja — "estou cuidando de pedidos", "estou montando a Home" — e dentro dela
os itens vêm na ordem em que a coisa acontece ou aparece na tela. A seção
HOME segue a página de cima para baixo: faixa do topo, banner, blocos,
rodapé. Onde ajuda, há **subtítulos** ("2 · Banner principal") e itens
**recuados**, que fazem parte do item logo acima: as pílulas do "Sobre a
loja", os passos da chamada final, o carrossel dos banners.

Os **nomes** na tela são os daqui, escritos para quem administra ("Pílulas
do «Sobre a loja»"), e não o `verbose_name_plural` do model. Mudar um nome
aqui não gera migration nem altera URL: o model continua com o seu
`app_label`, as suas permissões e o seu endereço (`/admin/home/homeabout/`).
Cada item pode levar uma **dica** de uma linha, que o índice mostra em letra
menor e a barra lateral omite.

O que isso **não** faz: não mexe em model nenhum, não registra nada duas
vezes, não cria model para organizar menu e não muda uma URL.

## Como acrescentar um model ao menu

Um `Item("app_label.modelname", "Nome na tela", "dica")` na seção certa.
Model registrado no Admin que não estiver listado **não some**: ele continua
aparecendo, no grupo do próprio app, no fim da página — esquecer de listar um
cadastro novo não pode escondê-lo de quem precisa dele. (Hoje tudo está
listado; `apps/core/tests_admin_sections.py` cobra isso.)
"""

from dataclasses import dataclass

from django.contrib.admin import AdminSite
from django.urls import reverse
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Item:
    """Um model no menu: a chave `app_label.modelname`, o nome na tela e a dica."""

    chave: str
    nome: str = ""  # vazio = o verbose_name_plural do model
    dica: str = ""
    sub: bool = False  # recuado: faz parte do item logo acima
    adicionar: str = ""  # nome de URL do "+ Adicionar", quando não é o padrão


@dataclass(frozen=True)
class Grupo:
    """Um subtítulo dentro da seção. Não é link; só organiza o que vem abaixo."""

    nome: str
    dica: str = ""


#: As seções, na ordem em que aparecem — e o que cada uma lista, na ordem em
#: que aparece dentro dela.
#:
#: A ordem das seções segue a frequência de uso de quem opera a loja: pedidos
#: todo dia, catálogo e Home toda semana, entrega e configurações de vez em
#: quando. "Equipe" fica por último porque mexer em quem tem acesso é raro —
#: e ficar longe de "Clientes" é o ponto: são duas coisas diferentes que a
#: tela antiga misturava.
SECOES = (
    (
        _("PEDIDOS"),
        (
            Item("orders.order", "Pedidos", "Tudo começa aqui: pagamento, produção, envio e cancelamento."),
            Item("orders.payment", "Pagamentos", "Um registro por tentativa de pagamento."),
            Item("orders.paymentproof", "Comprovantes de transferência", "Enviados pelo cliente; a confirmação é feita no pedido."),
            Item("cart.customizationupload", "Arquivos de personalização", "Fotos e arquivos que o cliente anexou aos itens."),
            Item("orders.orderstatushistory", "Histórico do pedido", "Quem mudou o quê, e quando."),
            Item("orders.ordernote", "Notas internas", "Anotações da equipe; o cliente não vê."),
            Item("cart.cart", "Carrinhos", "Carrinhos em aberto de visitantes e clientes."),
            Item("orders.webhookevent", "Eventos do provedor de pagamento", "Registro técnico das notificações da Stripe."),
        ),
    ),
    (
        _("CATÁLOGO"),
        (
            Item("catalog.product", "Produtos", adicionar="admin:catalog_product_quick_add"),
            Item("catalog.productvariant", "Variantes", "Cor, material e tamanho de cada produto: é aqui que ficam preço e estoque."),
            Item("categories.category", "Categorias", "A árvore do catálogo; também monta o menu do site."),
            Item("catalog.color", "Cores"),
            Item("catalog.material", "Materiais"),
            Item("catalog.brand", "Marcas"),
        ),
    ),
    (
        _("CLIENTES"),
        (
            Item("accounts.customer", "Clientes", "Quem compra. O acesso da equipe fica em EQUIPE."),
            Item("accounts.customeraddress", "Endereços"),
            Item("accounts.favorite", "Favoritos"),
            Item("storefront.contactmessage", "Mensagens de contato", "Recebidas pelo formulário da página Contato."),
        ),
    ),
    (
        _("ENTREGA"),
        (
            Item("core.deliverycountry", "Países de entrega", "Para onde a loja entrega, com a alíquota de TVA de cada país."),
            Item("shipping.shippingcarrier", "Transportadoras"),
            Item("shipping.shippingmethod", "Métodos de entrega"),
            Item("shipping.shippingrate", "Tarifas de entrega", "Preço por método, país e faixa de peso."),
        ),
    ),
    (
        _("HOME"),
        (
            Grupo("1 · Faixa do topo"),
            Item("storefront.topbaritem", "Frases da faixa do topo", "Frases curtas na faixa acima do cabeçalho, em todas as páginas."),
            Grupo("2 · Banner principal"),
            Item("home.homebanner", "Banners", "O conteúdo: cada banner, o seu desenho e os textos por idioma."),
            Item("home.homebannercarousel", "Carrossel", "O comportamento: rotação automática, setas e indicadores.", sub=True),
            Grupo("3 · Seções da Home", "A ordem da página: o que aparece, quantas vezes e em que ordem."),
            Item("home.homesection", "Seções da Home — a ordem da página", "Cada linha é uma seção: categorias em destaque, produtos, como trabalhamos, chamada final ou sobre a loja. Numeradas na ordem em que aparecem."),
            Item("home.homecategorycard", "Blocos de categorias", "Os blocos coloridos de cada seção «Categorias em destaque».", sub=True),
            Item("home.homecard", "Cards de «Como trabalhamos»", "Os cards com ícone de cada seção «Como trabalhamos».", sub=True),
            Item("home.homecallout", "Textos da chamada final", "O texto, o botão e as cores; a posição é uma seção «Chamada final».", sub=True),
            Item("home.homecalloutstep", "Passos da chamada final", "Os blocos numerados de cada texto de chamada.", sub=True),
            Item("home.homeabout", "Blocos «Sobre a loja»", "O quadro de imagem e o texto; a posição é uma seção «Sobre a loja».", sub=True),
            Item("home.homeaboutbadge", "Pílulas do «Sobre a loja»", "As etiquetas coloridas de cada bloco.", sub=True),
            Grupo("4 · Rodapé", "Em todas as páginas."),
            Item("storefront.footersettings", "Textos e contato do rodapé"),
            Item("storefront.footercolumn", "Colunas do rodapé"),
            Item("storefront.footerlink", "Links do rodapé", "Cada link, dentro da sua coluna.", sub=True),
            Item("storefront.institutionalpage", "Páginas da loja", "Envios e prazos, trocas e devoluções, contato e revenda — linkadas no rodapé."),
        ),
    ),
    (
        _("CONFIGURAÇÕES DA LOJA"),
        (
            Grupo("Identidade"),
            Item("core.brandassets", "Logos e imagens"),
            Item("core.sitelanguage", "Idiomas da loja"),
            Grupo("Comunicação"),
            Item("core.emailsettings", "Configuração de e-mail", "Servidor de envio, remetente e quem recebe os avisos."),
            Grupo("Pagamento e pedidos"),
            Item("orders.bankaccount", "Contas bancárias", "Para o pagamento por transferência."),
            Item("orders.cancellationsettings", "Texto de cancelamento", "O que o cliente recebe ao pedir o cancelamento."),
            Item("orders.ordernumbersequence", "Numeração dos pedidos"),
            Grupo("Manutenção e lançamento", "Uma página ativa fecha a loja para o visitante; o Admin continua aberto."),
            Item("storefront.specialpage", "Páginas especiais", "Manutenção ou lançamento; só uma fica ativa por vez."),
            Item("storefront.specialpagebenefit", "Benefícios das páginas", "As promessas curtas de cada página.", sub=True),
            Item("storefront.launchsubscriber", "Inscritos do lançamento", "Quem deixou o e-mail para ser avisado.", sub=True),
        ),
    ),
    (
        _("EQUIPE"),
        (
            Item("accounts.user", "Usuários", "Quem acessa este Admin. Quem compra está em CLIENTES."),
            Item("auth.group", "Grupos de permissão"),
        ),
    ),
)


def itens_da_secao(entradas) -> list[Item]:
    """Só os models de uma seção, sem os subtítulos."""
    return [entrada for entrada in entradas if isinstance(entrada, Item)]


def modelos_da_secao(titulo: str) -> list[str]:
    """As chaves `app_label.modelname` de uma seção, na ordem."""
    for nome, entradas in SECOES:
        if str(nome) == titulo:
            return [item.chave for item in itens_da_secao(entradas)]
    raise KeyError(titulo)


def modelos_listados() -> set[str]:
    """Tudo que `SECOES` cita."""
    return {item.chave for _titulo, entradas in SECOES for item in itens_da_secao(entradas)}


class JDPrintAdminSite(AdminSite):
    """O Admin da JD PRINT, com o menu agrupado por assunto."""

    # A marca (`site_header`, `site_title`, `index_title`) continua onde
    # sempre esteve, em `apps/core/admin.py`: declarar aqui também só criaria
    # duas fontes para o mesmo texto, com esta perdendo no import.

    def get_app_list(self, request, app_label=None):
        """Os models agrupados por `SECOES`, e não por app.

        Chamado pelo índice e pela barra lateral. Quando o Django pede a lista
        de **um** app (`app_label`), a resposta é a dele mesmo: ali a pergunta é
        "o que existe neste app", e reagrupar seria responder outra coisa.

        Cada model aparece **uma vez**. O que não estiver em `SECOES` continua
        no grupo do próprio app, no fim — esquecer de listar um cadastro novo
        não pode escondê-lo de quem precisa dele. Um subtítulo sem nenhum item
        visível abaixo (a pessoa não tem permissão para nenhum deles) não é
        desenhado.
        """
        original = super().get_app_list(request, app_label)
        if app_label is not None:
            return original

        # {app_label.modelname: (model, app)} — só o que este usuário pode ver.
        disponiveis = {}
        for app in original:
            for model in app["models"]:
                chave = f"{app['app_label']}.{model['object_name']}".lower()
                disponiveis[chave] = (model, app)

        lista = []
        usados = set()

        for titulo, entradas in SECOES:
            linhas = []
            grupo_pendente = None
            for entrada in entradas:
                if isinstance(entrada, Grupo):
                    # Só entra quando o primeiro item visível dele chegar.
                    grupo_pendente = entrada
                    continue
                encontrado = disponiveis.get(entrada.chave)
                if encontrado is None:
                    # O model não existe, não está registrado, ou este usuário
                    # não tem permissão nenhuma sobre ele. Nos três casos a
                    # resposta é a mesma: não desenhar a linha.
                    continue
                if grupo_pendente is not None:
                    linhas.append(_linha_de_grupo(grupo_pendente, len(linhas)))
                    grupo_pendente = None
                model = dict(encontrado[0])
                if entrada.nome:
                    model["name"] = entrada.nome
                model["hint"] = entrada.dica
                model["sub"] = entrada.sub
                if entrada.adicionar and model.get("add_url"):
                    model["add_url"] = reverse(entrada.adicionar)
                linhas.append(model)
                usados.add(entrada.chave)

            if linhas:
                lista.append(
                    {
                        "name": titulo,
                        "app_label": _slug(titulo),
                        # Sem `app_url`: o título da seção não é um app e não
                        # tem página própria. Um link que leva a lugar nenhum é
                        # pior que texto.
                        "app_url": "",
                        "has_module_perms": True,
                        "models": linhas,
                    }
                )

        # O que sobrou continua onde estava: no grupo do próprio app.
        for app in original:
            restantes = [
                model
                for model in app["models"]
                if f"{app['app_label']}.{model['object_name']}".lower() not in usados
            ]
            if restantes:
                lista.append({**app, "models": restantes})

        return lista


def _linha_de_grupo(grupo: Grupo, posicao: int) -> dict:
    """Um subtítulo, com a forma de uma linha de model para o template."""
    return {
        "heading": True,
        "name": grupo.nome,
        "hint": grupo.dica,
        "object_name": f"grupo-{posicao}",
        "admin_url": "",
        "add_url": "",
        "view_only": True,
        "perms": {},
    }


def _slug(titulo) -> str:
    """Um identificador estável para a seção, usado como `app_label` na tela.

    O template do índice o usa em `id` e em `class`; ele não resolve URL
    nenhuma, e é por isso que não precisa corresponder a um app de verdade.
    """
    return str(titulo).lower().replace(" ", "-").replace("ç", "c").replace("õ", "o").replace("á", "a")
