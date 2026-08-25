from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.views import set_language
from apps.orders.views import StripeWebhookView

# Fora do i18n_patterns: o admin tem o próprio seletor de idioma do Django e a
# rota de troca de idioma não pode depender de prefixo.
#
# A troca de idioma usa a view do projeto (apps.core.views.set_language) e não
# a do Django: a do Django não consegue traduzir uma URL que já vem com
# prefixo de idioma diferente do ativo. O nome da rota continua "set_language",
# então {% url 'set_language' %} nos templates não muda.
urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/setlang/", set_language, name="set_language"),
    # O webhook da Stripe não é um navegador: não tem idioma, não tem sessão e
    # não pode ganhar prefixo /fr/. Ele se autentica pela assinatura do corpo
    # (ver apps/orders/views.py::StripeWebhookView).
    path(
        "pagamento/stripe/webhook/",
        StripeWebhookView.as_view(),
        name="stripe_webhook",
    ),
]

# Com prefix_default_language=False, o português (idioma padrão) fica em "/" e
# os demais idiomas ganham prefixo: /fr/, /nl/, /en/...
urlpatterns += i18n_patterns(
    path("", include("apps.home.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.cart.urls")),
    path("", include("apps.orders.urls")),
    prefix_default_language=False,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
