"""Rotas de pedido.

``/carrinho/finalizar/`` **não** está aqui: ela já existe em ``apps.cart.urls``
desde a etapa 3 e continua sendo a porta do checkout — só que agora aponta para
a view de verdade. URL que o cliente já pode ter salvo não muda de endereço por
motivo de organização interna.

O webhook fica fora deste arquivo (ver ``config/urls.py``): ele não pertence a
nenhum idioma e não pode ganhar prefixo ``/fr/``.
"""

from django.urls import path

from apps.orders import views

app_name = "orders"

urlpatterns = [
    path("conta/pedidos/<str:number>/", views.OrderDetailView.as_view(), name="detail"),
    path(
        "conta/pedidos/<str:number>/cancelar/",
        views.OrderCancelView.as_view(),
        name="cancel",
    ),
    path(
        "conta/pedidos/<str:number>/pagar/",
        views.OrderRetryPaymentView.as_view(),
        name="retry_payment",
    ),
    # O comprovante fica sob `conta/pedidos/`, junto com as outras telas do
    # pedido, porque é a mesma coisa: uma tela que só o dono do pedido abre.
    # O arquivo em si sai por uma rota própria, por id, e com a mesma
    # conferência — nunca como mídia pública.
    path(
        "conta/pedidos/<str:number>/comprovante/",
        views.PaymentProofView.as_view(),
        name="payment_proof",
    ),
    path(
        "conta/comprovantes/<int:pk>/arquivo/",
        views.payment_proof_file,
        name="payment_proof_file",
    ),
    path(
        "pedido/<str:number>/confirmacao/",
        views.ConfirmationView.as_view(),
        name="confirmation",
    ),
    path(
        "pedido/<str:number>/pagamento-cancelado/",
        views.PaymentCancelledView.as_view(),
        name="payment_cancelled",
    ),
]
