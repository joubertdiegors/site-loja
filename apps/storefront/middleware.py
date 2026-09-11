"""O middleware que fecha a loja quando há uma página especial ativa.

Com uma `SpecialPage` ativa (manutenção ou lançamento), **toda** rota pública
responde com ela — `/`, `/modelos/`, `/produtos/x/`, `/carrinho/`, `/conta/`,
em qualquer idioma e com qualquer método. A resposta é renderizada no lugar,
sem redirecionamento: não existe URL "de manutenção" para onde mandar o
visitante, então não existe loop possível.

## Quando o lançamento chega

Um lançamento com data deixa de responder na hora marcada, sozinho: a
página continua ativa no Admin, mas `SpecialPage.objects.current()` não a
devolve mais (ver `SpecialPage.launch_is_over`), e cada rota volta a
entregar o site — a Home em `/`, o produto no endereço do produto. É a
proteção de verdade: vale num refresh, num link aberto depois da hora e sem
JavaScript. O redirecionamento do contador para a Home é só a experiência
de quem estava olhando a contagem.

## O que continua aberto

* `/admin/` — inclusive o login administrativo. É por ali que a página é
  desativada; bloquear seria trancar a chave dentro de casa.
* `/i18n/`, `/static/`, `/media/`, `/favicon.ico` — infraestrutura da própria
  página especial (ela usa o CSS, as fontes e o ícone do site).
* O webhook da Stripe — pagamentos em andamento não podem parar de ser
  confirmados porque a vitrine está fechada.
* A rota de inscrição do lançamento — o formulário da própria página.
* Quem está logado como **equipe** (`is_staff`) vê a loja normal, para testar
  o que está por trás da cortina. É a sessão do Admin, não um segredo na URL:
  cliente comum logado continua vendo a página especial.

## Ordem no `MIDDLEWARE`

Depois do `AuthenticationMiddleware` (precisa de `request.user`) e do
`LocaleMiddleware` (a página sai no idioma da URL). Fica por último.

## Cache

A página especial sai com `Cache-Control: no-store` e o site não usa cache de
página: desativar no Admin vale na requisição seguinte. Nenhuma página normal
ganha cabeçalho de cache por causa deste middleware.
"""

from django.urls import Resolver404, resolve

from apps.storefront.models import SpecialPage

#: Prefixos que nunca são cobertos pela página especial.
EXEMPT_PREFIXES = (
    "/admin/",
    "/i18n/",
    "/static/",
    "/media/",
    "/favicon.ico",
    "/pagamento/stripe/webhook/",
)

#: Views (pelo nome completo) que a própria página especial usa.
EXEMPT_VIEWS = frozenset({"storefront:launch_notify"})


class SpecialPageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        page = self.page_for(request)
        if page is None:
            return self.get_response(request)
        # Import tardio: `views` importa `models`, e este módulo é importado
        # na inicialização, antes de o registro de apps terminar.
        from apps.storefront.views import render_special_page

        return render_special_page(request, page)

    @staticmethod
    def page_for(request):
        """A página que deve responder a esta requisição — ou ``None``."""
        path = request.path_info
        if path.startswith(EXEMPT_PREFIXES):
            return None

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return None

        page = SpecialPage.objects.current()
        if page is None:
            return None

        try:
            match = resolve(path)
        except Resolver404:
            return page
        if match.view_name in EXEMPT_VIEWS:
            return None
        return page
