"""Views do carrinho.

Todas as escritas são POST. Cada uma funciona de dois jeitos:

* **sem JavaScript** — formulário normal, mensagem do Django e redirecionamento
  de volta para a página de origem;
* **com HTMX** — devolve só os pedaços que mudaram (contador do header, aviso e
  conteúdo da gaveta lateral, por troca *out of band*), sem recarregar a página.

Isso mantém a loja utilizável sem JavaScript e rápida com ele.
"""

import json

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.cart.cart import Cart, CartResult
from apps.cart.forms import AddToCartForm
from apps.catalog.models import Product, ProductStatus


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _safe_next(request) -> str:
    """Destino do redirecionamento, validado contra host externo."""
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse("cart:detail")


def _get_product(request) -> Product:
    """Produto do POST. Só produtos ativos entram no carrinho.

    O id vem do cliente, então pode não ser número: `pk="abc"` levanta
    `ValueError` no ORM antes de qualquer consulta, e isso é um 500 para
    quem só digitou besteira na requisição. Id inválido e id inexistente
    são a mesma coisa aqui — não existe esse produto.
    """
    try:
        product_id = int(request.POST.get("product_id") or 0)
    except (TypeError, ValueError):
        raise Http404("product_id inválido.")

    return get_object_or_404(
        Product.objects.select_related("category").prefetch_related(
            "translations", "variants", "media"
        ),
        pk=product_id,
        status=ProductStatus.ACTIVE,
    )


def _respond(request, cart: Cart, result, *, render_panel: bool = False, open_drawer: bool = False):
    """Resposta HTMX (pedaços) ou redirecionamento com mensagem."""
    if _is_htmx(request):
        response = render(
            request,
            "cart/_update.html",
            {
                "cart": cart,
                "toast_message": result.message,
                "toast_level": result.level,
                "render_panel": render_panel,
            },
        )
        if open_drawer and result.ok:
            # O JavaScript da gaveta escuta este evento e a abre.
            response["HX-Trigger"] = json.dumps({"jd:cart-open": True})
        return response

    level = {"success": messages.SUCCESS, "warning": messages.WARNING, "error": messages.ERROR}[
        result.level
    ]
    messages.add_message(request, level, result.message)
    return HttpResponseRedirect(_safe_next(request))


@require_POST
def add(request):
    """Adiciona ao carrinho, validando variante e personalização."""
    product = _get_product(request)
    form = AddToCartForm(request.POST, request.FILES, product=product)
    cart = Cart(request)

    if not form.is_valid():
        return _respond(request, cart, CartResult(False, form.error_message, "error"))

    if not request.session.session_key:
        request.session.save()  # precisamos da chave para marcar o upload

    customization = form.build_customization(session_key=request.session.session_key or "")
    result = cart.add(
        product,
        variant=form.variant,
        quantity=form.cleaned_data["quantity"],
        customization=customization,
    )
    return _respond(request, cart, result, open_drawer=True)


@require_POST
def update(request):
    """Define a quantidade da linha (botões − e + ou quantidade direta)."""
    cart = Cart(request)
    key = request.POST.get("line", "")

    action = request.POST.get("action")
    if action == "increment":
        quantity = cart.quantity_of(key) + 1
    elif action == "decrement":
        quantity = cart.quantity_of(key) - 1
    else:
        try:
            quantity = int(request.POST.get("quantity", 1))
        except (TypeError, ValueError):
            quantity = cart.quantity_of(key)

    result = cart.set_quantity(key, quantity)
    return _respond(request, cart, result, render_panel=True)


@require_POST
def remove(request):
    cart = Cart(request)
    result = cart.remove(request.POST.get("line", ""))
    return _respond(request, cart, result, render_panel=True)


def drawer(request):
    """Conteúdo da gaveta lateral, para o HTMX buscar sob demanda."""
    return render(request, "cart/_drawer_body.html", {"cart": Cart(request)})


class CartDetailView(TemplateView):
    template_name = "cart/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Meu carrinho — JD PRINT")
        context["meta_description"] = _("Produtos selecionados no seu carrinho.")
        return context
