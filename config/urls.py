from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.templatetags.static import static as static_url
from django.urls import include, path
from django.views.generic import RedirectView

from apps.core.views import favicon, set_language
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
    # O navegador pede /favicon.ico sozinho, em toda visita, mesmo com o <link>
    # do <head> apontando para outro arquivo. Sem esta rota é um 404 por
    # visitante no log — ruído que esconde os 404 que importam.
    #
    # A view (e não um `RedirectView` com URL fixa) porque o ícone agora vem do
    # Admin: ver `apps.core.views.favicon`.
    path("favicon.ico", favicon, name="favicon"),
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
    path("", include("apps.storefront.urls")),
    prefix_default_language=False,
)

if settings.DEBUG:
    # Uma rota por pasta pública, e só por elas: `customizations/` e
    # `payment-proofs/` ficam de fora de propósito — são a foto e o comprovante
    # que o cliente mandou, e quem os entrega é uma view que confere quem está
    # pedindo. Servir `media/` inteiro aqui faria o desenvolvimento não bater
    # com a produção justamente no ponto que a etapa de segurança fechou.
    #
    # A lista vive em `settings.PUBLIC_MEDIA_DIRS`, e não aqui, porque ela tem
    # um segundo leitor: em produção o Django não serve mídia nenhuma, e o
    # mapeamento do painel da hospedagem precisa ter as mesmas pastas. Enquanto
    # a lista morava dentro deste `if`, acrescentar uma pasta não lembrava
    # ninguém do passo no servidor — e foi assim que `brand/` chegou à produção
    # sem mapeamento, com a imagem padrão dos produtos dando 404.
    for _publica in settings.PUBLIC_MEDIA_DIRS:
        urlpatterns += static(
            f"{settings.MEDIA_URL}{_publica}/",
            document_root=settings.MEDIA_ROOT / _publica,
        )
