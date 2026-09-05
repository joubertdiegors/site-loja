"""Rotas das páginas institucionais.

Caminhos de primeiro nível, como `/carrinho/`, `/modelos/` e `/favoritos/` — o
padrão da loja. Nada de prefixo `/paginas/`: são quatro páginas conhecidas, não
um CMS de slug livre.

Os nomes seguem `page_<slug>` porque é assim que `InstitutionalPage.get_absolute_url`
os monta a partir de `PageSlug`: acrescentar uma quinta página é acrescentar a
escolha e a rota, e o link do rodapé passa a existir sozinho.
"""

from django.urls import path

from apps.storefront import views

app_name = "storefront"

urlpatterns = [
    path(
        "envios-e-prazos/",
        views.InstitutionalPageView.as_view(page_slug="envios-e-prazos"),
        name="page_shipping",
    ),
    path(
        "trocas-e-devolucoes/",
        views.InstitutionalPageView.as_view(page_slug="trocas-e-devolucoes"),
        name="page_returns",
    ),
    path("contato/", views.ContactView.as_view(), name="page_contact"),
    path(
        "revenda/",
        views.InstitutionalPageView.as_view(page_slug="revenda"),
        name="page_reseller",
    ),
    # O formulário da página de lançamento. É a única rota pública que o
    # middleware da página especial deixa passar (ver storefront/middleware.py).
    path("lancamento/aviso/", views.launch_notify, name="launch_notify"),
]
