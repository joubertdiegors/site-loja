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
from apps.core.security import ip_is_throttled
from apps.core.uploads import private_file_response
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


def cart_page_context(cart: Cart) -> dict:
    """O que a página do carrinho mostra além das linhas.

    Vale para a página inteira e para a resposta parcial do HTMX que redesenha
    o painel — um lugar só, senão o resumo trocado por um clique divergiria do
    resumo com que a página abriu.

    Nada aqui é conta nova: o prazo é o do checkout (`production_days`, o
    maior entre as linhas), as formas de pagamento são as do checkout e os
    dois cartões são as páginas de política publicadas no Admin.
    """
    from apps.orders.payments import available_checkout_methods
    from apps.shipping.services import production_days
    from apps.storefront.models import InstitutionalPage, PageSlug

    lines = cart.lines()
    if not lines:
        return {"production_days": 0, "payment_methods": (), "policy_pages": [], "recommended": []}

    ordem = [PageSlug.SHIPPING, PageSlug.RETURNS]
    paginas = {
        page.slug: page
        for page in InstitutionalPage.objects.for_display()
        .filter(slug__in=ordem)
        .prefetch_related("translations")
    }
    return {
        "production_days": production_days(lines),
        "payment_methods": available_checkout_methods(),
        "policy_pages": [paginas[slug] for slug in ordem if slug in paginas],
        "recommended": recommended_for(lines),
    }


#: Quantos cards a faixa "Você também pode gostar" mostra. Quatro fecha a
#: grade do desktop e as duas colunas do tablet sem sobrar linha pela metade.
RECOMMENDED_LIMIT = 4


def recommended_for(lines, limit: int = RECOMMENDED_LIMIT) -> list:
    """Outros produtos para quem já tem estes no carrinho.

    O mesmo critério da página do produto, sobre o carrinho inteiro: primeiro
    as categorias do que está no carrinho, depois os destaques, depois o mais
    recente — sem repetir e sem o que já está lá. `sellable()` em todas: um
    card sem preço não convida ninguém a clicar.
    """
    from django.db.models import Prefetch

    from apps.catalog.models import ProductVariant

    no_carrinho = {line.product.pk for line in lines}
    categorias = {line.product.category_id for line in lines if line.product.category_id}

    base = (
        Product.objects.sellable()
        .exclude(pk__in=no_carrinho)
        .select_related("category")
        .prefetch_related(
            "translations",
            "media",
            "category__translations",
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(is_active=True)
                .select_related("color", "material")
                .prefetch_related("color__translations")
                .order_by("sort_order", "id"),
            ),
        )
    )

    passadas = []
    if categorias:
        passadas.append(base.filter(category_id__in=categorias).order_by("-created_at"))
    passadas.append(base.filter(is_featured=True).order_by("featured_order", "-created_at"))
    passadas.append(base.order_by("-created_at"))

    escolhidos = []
    vistos = set(no_carrinho)
    for passada in passadas:
        if len(escolhidos) >= limit:
            break
        for candidato in passada[: limit * 2]:
            if candidato.pk in vistos:
                continue
            vistos.add(candidato.pk)
            escolhidos.append(candidato)
            if len(escolhidos) >= limit:
                break
    return escolhidos


def _respond(request, cart: Cart, result, *, render_panel: bool = False, open_drawer: bool = False):
    """Resposta HTMX (pedaços) ou redirecionamento com mensagem."""
    if _is_htmx(request):
        contexto = {
            "cart": cart,
            "toast_message": result.message,
            "toast_level": result.level,
            "render_panel": render_panel,
        }
        if render_panel:
            contexto.update(cart_page_context(cart))
        response = render(request, "cart/_update.html", contexto)
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

    # A foto de personalização é aceita de visitante anônimo, e cada uma pode
    # ter megabytes. Sem freio, um laço enche o disco da hospedagem: é o único
    # ponto da loja em que alguém sem conta grava arquivo no servidor. A trava
    # só conta quem realmente manda arquivo — quem só adiciona ao carrinho não
    # é afetado.
    if request.FILES.get("personalization_photo"):
        if ip_is_throttled(request, "upload"):
            return _respond(
                request,
                cart,
                CartResult(
                    False,
                    _("Muitos envios a partir deste endereço. Aguarde alguns instantes."),
                    "error",
                ),
            )

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


def customization_file(request, pk: int):
    """Entrega a foto de personalização — só a quem tem o que ver com ela.

    O arquivo não é mais público. Antes bastava ter a URL: ela vai no e-mail
    interno de cada pedido, e-mail se reencaminha, e a foto de um cliente
    ficava aberta na internet para sempre.

    Quem passa:

    * **a equipe** — staff com permissão de ver o arquivo ou de ver pedidos.
      É quem precisa dele para produzir a peça;
    * **o dono** — o cliente cujo pedido tem um item apontando para este
      arquivo;
    * **quem acabou de enviar** — a sessão que gravou o upload, antes de o
      pedido existir. É o carrinho ainda aberto.

    Quem não passa recebe **404**, não 403: um 403 confirmaria que o arquivo
    existe, e a lista de ids é curta de percorrer.

    O caminho em disco nunca sai daqui — o cliente recebe bytes, não um lugar.
    """
    from apps.cart.models import CustomizationUpload

    upload = CustomizationUpload.objects.filter(pk=pk).first()
    if upload is None or not upload.file:
        raise Http404("Arquivo não encontrado.")

    if not _may_read_customization(request, upload):
        raise Http404("Arquivo não encontrado.")

    # A entrega em si (bytes, sem caminho, sem cache compartilhado, 404 quando
    # o arquivo sumiu do disco) mora em `apps.core.uploads`. A **permissão**
    # continua sendo decidida acima, e só aqui.
    return private_file_response(
        upload.file,
        content_type=upload.content_type,
        filename=upload.original_name or upload.file.name,
    )


def _may_read_customization(request, upload) -> bool:
    """As três portas de entrada do arquivo. Nenhuma delas é "ter o link"."""
    user = request.user

    if user.is_authenticated and user.is_staff:
        if user.has_perm("cart.view_customizationupload") or user.has_perm(
            "orders.view_order"
        ):
            return True

    if user.is_authenticated and upload.order_items.filter(
        order__customer__user=user
    ).exists():
        return True

    # O carrinho ainda aberto: quem enviou continua na mesma sessão. A chave
    # muda quando a pessoa entra na conta — a partir daí vale a regra do dono.
    sessao = request.session.session_key
    return bool(sessao and upload.session_key and upload.session_key == sessao)


class CartDetailView(TemplateView):
    template_name = "cart/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Meu carrinho — JD PRINT")
        context["meta_description"] = _("Produtos selecionados no seu carrinho.")
        # A instância vai para o contexto da view (e não só para o context
        # processor, que só entra na hora de renderizar): as linhas são lidas
        # uma vez aqui e ficam em cache para o template.
        cart = Cart(self.request)
        context["cart"] = cart
        context.update(cart_page_context(cart))
        return context
