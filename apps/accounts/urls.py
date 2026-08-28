"""Rotas da conta.

Ficam dentro do ``i18n_patterns`` como o resto da loja: ``/conta/entrar/`` em
português, ``/fr/conta/entrar/`` em francês. Os caminhos em si não são
traduzidos — é o mesmo critério já usado em ``/modelos/`` e ``/carrinho/``.
"""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("conta/", views.DashboardView.as_view(), name="dashboard"),
    path("conta/entrar/", views.LoginView.as_view(), name="login"),
    path("conta/sair/", views.LogoutView.as_view(), name="logout"),
    path("conta/criar/", views.RegisterView.as_view(), name="register"),
    path("conta/dados/", views.ProfileView.as_view(), name="profile"),
    path("conta/seguranca/", views.SecurityView.as_view(), name="security"),
    path("conta/enderecos/", views.AddressesView.as_view(), name="addresses"),
    path("conta/enderecos/novo/", views.AddressCreateView.as_view(), name="address_create"),
    path("conta/enderecos/<int:pk>/", views.AddressUpdateView.as_view(), name="address_edit"),
    path("conta/enderecos/<int:pk>/remover/", views.AddressDeleteView.as_view(), name="address_delete"),
    path("conta/enderecos/<int:pk>/padrao/", views.set_default_address, name="address_default"),
    path("conta/pedidos/", views.OrdersView.as_view(), name="orders"),
    # Favoritos. `/favoritos/` é curto porque é para onde o coração do
    # cabeçalho leva; a tela em si é da área da conta, e o menu lateral a
    # mostra ao lado de "Meus pedidos".
    path("favoritos/", views.FavoritesView.as_view(), name="favorites"),
    path("favoritos/alternar/", views.favorite_toggle, name="favorite_toggle"),
    # Confirmação de e-mail
    path(
        "conta/confirmar/<uidb64>/<token>/",
        views.VerifyEmailView.as_view(),
        name="verify_email",
    ),
    path(
        "conta/confirmar/reenviar/",
        views.resend_verification,
        name="resend_verification",
    ),
    path(
        "conta/confirmar/novo-envio/",
        views.resend_verification_page,
        name="resend_verification_page",
    ),
    # Recuperação de senha
    path("conta/senha/", views.PasswordResetView.as_view(), name="password_reset"),
    path("conta/senha/enviado/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "conta/senha/nova/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "conta/senha/pronto/",
        views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]
