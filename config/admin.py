"""O menu do Admin, organizado por assunto em vez de por app Python.

## O problema

O Django agrupa os models por **app**, e app é uma divisão de código: quem
trabalha na loja não sabe (nem deve saber) que "Países de entrega" mora em
`core` e "Transportadoras" em `shipping`, nem por que "Clientes" e "Usuários"
aparecem lado a lado quando um é quem compra e o outro é quem atende.

O resultado era uma lista de nove caixas em que achar uma coisa dependia de
lembrar em qual app ela foi escrita.

## A saída

`AdminSite.get_app_list()` é o ponto de extensão que o próprio Django oferece
para isto, e a troca do site padrão é feita pelo caminho oficial —
`AdminConfig.default_site`, em `config/apps.py`, apontado no `INSTALLED_APPS`.

O que isso **não** faz: não mexe em model nenhum, não registra nada duas vezes,
não cria model para organizar menu e não muda uma URL. Cada model continua no
seu app, com o seu `app_label`, as suas permissões e o seu endereço
(`/admin/orders/order/`). O que muda é só a caixa em que o link aparece.

## Como acrescentar um model ao menu

Uma linha em `SECOES`, com o rótulo `app_label.modelname`. Model que não estiver
listado **não some**: ele continua aparecendo, no grupo do próprio app, no fim
da página. É de propósito — esquecer de listar um cadastro novo não pode
escondê-lo de quem precisa dele.
"""

from django.contrib.admin import AdminSite
from django.utils.translation import gettext_lazy as _

#: As seções, na ordem em que aparecem — e os models de cada uma, na ordem em
#: que aparecem dentro dela.
#:
#: A ordem das seções segue a frequência de uso de quem opera a loja: pedidos
#: todo dia, catálogo toda semana, entrega e configurações de vez em quando.
#: "Equipe" fica perto do fim porque mexer em quem tem acesso é raro — e ficar
#: longe de "Cadastro de clientes" é o ponto: são duas coisas diferentes que a
#: tela antiga misturava.
SECOES = (
    (
        _("PEDIDOS"),
        (
            "orders.order",
            "orders.payment",
            "orders.paymentproof",
            "orders.orderstatushistory",
            "orders.ordernote",
            "cart.customizationupload",
            "cart.cart",
            "orders.webhookevent",
        ),
    ),
    (
        _("CATÁLOGO"),
        (
            "catalog.product",
            "catalog.productvariant",
            "categories.category",
            "catalog.color",
            "catalog.material",
            "catalog.brand",
        ),
    ),
    (
        _("CADASTRO DE CLIENTES"),
        (
            "accounts.customer",
            "accounts.customeraddress",
            "accounts.favorite",
        ),
    ),
    (
        _("ENTREGA"),
        (
            "shipping.shippingcarrier",
            "shipping.shippingmethod",
            "shipping.shippingrate",
            "core.deliverycountry",
        ),
    ),
    (
        _("CONFIGURAÇÕES DA LOJA"),
        (
            "core.brandassets",
            "core.emailsettings",
            "core.sitelanguage",
            "orders.bankaccount",
            "orders.cancellationsettings",
            "orders.ordernumbersequence",
        ),
    ),
    (
        _("EQUIPE"),
        (
            "accounts.user",
            "auth.group",
        ),
    ),
)


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
        não pode escondê-lo de quem precisa dele.
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

        for titulo, chaves in SECOES:
            models = []
            for chave in chaves:
                item = disponiveis.get(chave)
                if item is None:
                    # O model não existe, não está registrado, ou este usuário
                    # não tem permissão nenhuma sobre ele. Nos três casos a
                    # resposta é a mesma: não desenhar a linha.
                    continue
                models.append(item[0])
                usados.add(chave)

            if models:
                lista.append(
                    {
                        "name": titulo,
                        "app_label": _slug(titulo),
                        # Sem `app_url`: o título da seção não é um app e não
                        # tem página própria. Um link que leva a lugar nenhum é
                        # pior que texto.
                        "app_url": "",
                        "has_module_perms": True,
                        "models": models,
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


def _slug(titulo) -> str:
    """Um identificador estável para a seção, usado como `app_label` na tela.

    O template do índice o usa em `id` e em `class`; ele não resolve URL
    nenhuma, e é por isso que não precisa corresponder a um app de verdade.
    """
    return str(titulo).lower().replace(" ", "-").replace("ç", "c").replace("õ", "o").replace("á", "a")
