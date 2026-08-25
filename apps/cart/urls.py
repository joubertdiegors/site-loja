from django.urls import path

from apps.cart import views
from apps.orders.views import CheckoutView

app_name = "cart"

urlpatterns = [
    path("carrinho/", views.CartDetailView.as_view(), name="detail"),
    path("carrinho/adicionar/", views.add, name="add"),
    path("carrinho/atualizar/", views.update, name="update"),
    path("carrinho/remover/", views.remove, name="remove"),
    path("carrinho/painel/", views.drawer, name="drawer"),
    # A URL é da etapa 3 e continua a mesma: quem tinha ela salva não perde
    # nada. O que mudou foi o que há do outro lado — agora é o checkout real
    # (apps.orders), não mais a página de "em breve".
    path("carrinho/finalizar/", CheckoutView.as_view(), name="checkout"),
]
