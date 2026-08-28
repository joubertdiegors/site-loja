from django.urls import path
from django.views.generic import RedirectView

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    # Vitrine de Modelos. A view é genérica: outras vitrines (filamentos,
    # impressoras) entram como rotas novas apontando para outra categoria raiz.
    path("modelos/", views.ShopView.as_view(), name="models_shop"),
    # Rota antiga da etapa 2, mantida para não quebrar links já salvos.
    path(
        "produtos/",
        RedirectView.as_view(pattern_name="catalog:models_shop", permanent=False),
        name="product_list",
    ),
    # Busca. Sem raiz de categoria: varre a loja inteira.
    path("buscar/", views.SearchView.as_view(), name="search"),
    path("produtos/<slug:slug>/", views.ProductDetailView.as_view(), name="product_detail"),
    # A pagina de uma categoria. A URL nao mudou -- o que mudou e que agora ela
    # mostra os produtos em vez de um aviso de "em construcao".
    path("categorias/<slug:slug>/", views.CategoryDetailView.as_view(), name="category_detail"),
]
