"""Telas de conta: cadastro, login, confirmação de e-mail, senha e dados.

Duas ideias atravessam o arquivo:

* **não revelar quem tem conta** — recuperação de senha e reenvio de
  confirmação respondem sempre a mesma coisa, exista ou não o endereço;
* **nada de estado por GET** — sair, cadastrar, reenviar e redefinir são POST
  com CSRF. O único GET que muda estado é o link de confirmação de e-mail, que
  é a natureza do mecanismo: quem o abre carrega um token assinado, de uso
  único e com prazo.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.http import Http404
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, FormView, TemplateView, UpdateView

from apps.accounts.emails import send_verification_email
from apps.accounts.forms import (
    AddressForm,
    CustomerForm,
    EmailChangeForm,
    LoginForm,
    PasswordResetRequestForm,
    RegistrationForm,
    ResendVerificationForm,
    StyledPasswordChangeForm,
)
from apps.accounts.models import Customer, CustomerAddress, User
from apps.accounts.tokens import decode_uid, email_verification_token
from apps.core.languages import is_language_available
from apps.core.security import (
    ip_is_throttled,
    login_is_blocked,
    register_login_failure,
)

#: Resposta única dos fluxos que não podem revelar se a conta existe.
GENERIC_EMAIL_RESPONSE = _(
    "Se existir uma conta associada a este e-mail, enviaremos as instruções em instantes."
)

AUTH_BACKEND = "apps.accounts.backends.UsernameOrEmailBackend"


def current_language() -> str:
    """Idioma da loja nesta requisição, se ele estiver disponível."""
    language = get_language()
    return language if is_language_available(language) else settings.LANGUAGE_CODE


class RegisterView(FormView):
    """Cadastro público: conta + cliente + login + e-mail de confirmação."""

    template_name = "accounts/register.html"
    form_class = RegistrationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Quem se cadastra em /fr/ recebe os e-mails em francês.
        kwargs["language"] = current_language()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Criar conta — JD PRINT")
        return context

    def post(self, request, *args, **kwargs):
        """Cadastro em série é uma torneira de e-mail com o nosso domínio.

        Cada cadastro dispara um e-mail de confirmação para o endereço que o
        visitante escolheu. Em laço, isso vira envio em massa saindo do
        remetente da loja — e o estrago não é o nosso servidor, é a reputação
        do domínio: quem recebe marca como spam e, depois, o e-mail de pedido
        de um cliente de verdade para na caixa de lixo.

        A trava é a mesma do reenvio de confirmação e do reset de senha
        (`ip_is_throttled`), com a mesma cota por janela — várias pessoas
        dividem um IP num escritório, e a segunda delas não pode ficar sem
        criar conta por causa da primeira.
        """
        if ip_is_throttled(request, "register"):
            messages.warning(
                request,
                _("Muitas contas criadas a partir deste endereço. Aguarde alguns instantes."),
            )
            return self.form_invalid(self.get_form())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()

        # O carrinho da sessão é migrado pelo sinal `user_logged_in`
        # (apps/cart/signals.py): entrar e cadastrar seguem o mesmo caminho.
        auth_login(self.request, user, backend=AUTH_BACKEND)

        send_verification_email(user)
        messages.success(
            self.request,
            _("Conta criada. Enviamos um e-mail para %(email)s — confirme para concluir.")
            % {"email": user.email},
        )
        return redirect(self.get_success_url())

    def get_success_url(self):
        """Volta para onde o visitante estava, se o destino for do próprio site."""
        redirect_to = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if redirect_to and url_has_allowed_host_and_scheme(
            url=redirect_to,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return redirect_to
        return reverse("accounts:dashboard")


class LoginView(auth_views.LoginView):
    """Entrada do cliente, com freio para tentativa em série.

    Sem o freio, a tela aceitava senha errada indefinidamente — o suficiente
    para testar uma lista de e-mail+senha vazada de outro site. A trava é a
    mesma que o reenvio de confirmação e o reset de senha já usavam.

    A mensagem é a mesma para IP bloqueado e para senha errada de propósito:
    dizer "você foi bloqueado" contaria ao atacante que ele chegou ao limite
    e como calibrar a próxima rodada.
    """

    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if login_is_blocked(request):
            form = self.get_form()
            form.is_valid()  # popula cleaned_data e os erros de campo
            form.add_error(
                None,
                _(
                    "Muitas tentativas a partir deste endereço. "
                    "Aguarde alguns minutos e tente de novo."
                ),
            )
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        """Só a falha conta. Acertar a senha não aproxima ninguém do limite."""
        register_login_failure(self.request)
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Entrar — JD PRINT")
        return context


class LogoutView(auth_views.LogoutView):
    """Sair. Só POST, com CSRF — é o comportamento nativo do Django."""

    next_page = reverse_lazy("home:index")


def _safe_next_url(request) -> str:
    """Destino de volta, validado contra host externo (o ``?next=`` do checkout)."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return ""


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Minha conta — JD PRINT")
        context["account_page"] = "dashboard"
        context["customer"] = getattr(self.request.user, "customer", None)
        context["seconds_until_resend"] = self.request.user.seconds_until_resend()

        from apps.orders.models import Order

        context["order_count"] = Order.objects.for_user(self.request.user).count()
        return context


class ProfileView(LoginRequiredMixin, UpdateView):
    """"Meus dados": só o ``Customer``. Senha e e-mail não passam por aqui."""

    template_name = "accounts/profile.html"
    form_class = CustomerForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        customer, _created = Customer.objects.get_or_create(user=self.request.user)
        return customer

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Meus dados — JD PRINT")
        context["account_page"] = "profile"
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Dados atualizados."))
        return response


class SecurityView(LoginRequiredMixin, TemplateView):
    """"Minha conta › Segurança": senha, e-mail e estado da confirmação.

    Nada de autenticação nova: a senha usa o ``PasswordChangeForm`` do Django e
    a troca de e-mail usa o ``User.set_email`` da etapa 5, que derruba a
    confirmação e dispara um novo e-mail.
    """

    template_name = "accounts/security.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Segurança — JD PRINT")
        context["account_page"] = "security"
        context.setdefault("password_form", StyledPasswordChangeForm(user=self.request.user))
        context.setdefault("email_form", EmailChangeForm(user=self.request.user))
        context["seconds_until_resend"] = self.request.user.seconds_until_resend()
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("form") == "email":
            return self.change_email(request)
        return self.change_password(request)

    def change_password(self, request):
        form = StyledPasswordChangeForm(user=request.user, data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(password_form=form))

        form.save()
        # Sem isto o próprio autor da troca seria deslogado na requisição
        # seguinte: o hash da sessão deixa de bater com a senha nova.
        update_session_auth_hash(request, form.user)
        messages.success(request, _("Senha alterada."))
        return redirect("accounts:security")

    def change_email(self, request):
        form = EmailChangeForm(user=request.user, data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(email_form=form))

        if form.save():
            send_verification_email(request.user)
            messages.success(
                request,
                _("E-mail alterado para %(email)s. Enviamos um link de confirmação.")
                % {"email": request.user.email},
            )
        return redirect("accounts:security")


class CustomerMixin(LoginRequiredMixin):
    """Quem está logado, do lado comercial.

    O ``Customer`` nasce com a conta, mas contas criadas pelo terminal
    (``createsuperuser``) não passam pelo formulário de cadastro. Em vez de
    quebrar com ``RelatedObjectDoesNotExist`` na primeira compra, criamos o
    registro vazio aqui.
    """

    @property
    def customer(self):
        customer, _created = Customer.objects.get_or_create(user=self.request.user)
        return customer


class AddressesView(CustomerMixin, TemplateView):
    """Lista de endereços do cliente."""

    template_name = "accounts/addresses.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Endereços — JD PRINT")
        context["account_page"] = "addresses"
        context["addresses"] = (
            self.customer.addresses.select_related("country")
            .prefetch_related("country__translations")
            .all()
        )
        return context


class AddressCreateView(CustomerMixin, CreateView):
    template_name = "accounts/address_form.html"
    form_class = AddressForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "customer": self.customer}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Novo endereço — JD PRINT")
        context["account_page"] = "addresses"
        context["is_new"] = True
        context["next_url"] = _safe_next_url(self.request)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Endereço salvo."))
        return response

    def get_success_url(self):
        # Voltar para o checkout quando foi de lá que o cliente veio: ele está
        # no meio de uma compra, não organizando o cadastro.
        return _safe_next_url(self.request) or reverse("accounts:addresses")


class AddressUpdateView(CustomerMixin, UpdateView):
    template_name = "accounts/address_form.html"
    form_class = AddressForm

    def get_queryset(self):
        # Sempre pelo dono. O id vem da URL e id de URL não prova nada.
        return CustomerAddress.objects.filter(customer=self.customer)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "customer": self.customer}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Editar endereço — JD PRINT")
        context["account_page"] = "addresses"
        context["next_url"] = _safe_next_url(self.request)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Endereço atualizado."))
        return response

    def get_success_url(self):
        return _safe_next_url(self.request) or reverse("accounts:addresses")


class AddressDeleteView(CustomerMixin, DeleteView):
    template_name = "accounts/address_confirm_delete.html"
    success_url = reverse_lazy("accounts:addresses")

    def get_queryset(self):
        return CustomerAddress.objects.filter(customer=self.customer)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Remover endereço — JD PRINT")
        context["account_page"] = "addresses"
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Endereço removido."))
        return super().form_valid(form)


@require_POST
@login_required
def set_default_address(request, pk):
    """Define o padrão de entrega e/ou de faturamento. POST com CSRF."""
    customer, _created = Customer.objects.get_or_create(user=request.user)
    address = get_object_or_404(CustomerAddress, pk=pk, customer=customer)

    address.make_default(
        shipping=request.POST.get("kind") in {"shipping", "both"},
        billing=request.POST.get("kind") in {"billing", "both"},
    )
    messages.success(request, _("Endereço padrão atualizado."))
    return redirect("accounts:addresses")


class OrdersView(CustomerMixin, TemplateView):
    """Lista de pedidos do cliente."""

    template_name = "accounts/orders.html"

    def get_context_data(self, **kwargs):
        from apps.orders.models import Order

        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Meus pedidos — JD PRINT")
        context["account_page"] = "orders"
        context["orders"] = (
            Order.objects.for_user(self.request.user)
            .with_details()
            .order_by("-created_at", "-pk")
        )
        return context


class VerifyEmailView(TemplateView):
    """Confirmação de e-mail.

    Quatro desfechos, cada um com a sua mensagem:

    * ``ok``       — confirmado agora;
    * ``already``  — link já usado (ou conta já confirmada);
    * ``expired``  — assinatura boa, prazo vencido → oferece novo envio;
    * ``invalid``  — link adulterado, de outra conta ou incompleto.
    """

    template_name = "accounts/verify_email.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Confirmação de e-mail — JD PRINT")
        context["status"] = self.check_link()
        return context

    def check_link(self) -> str:
        user = decode_uid(self.kwargs.get("uidb64", ""))
        token = self.kwargs.get("token", "")

        if user is None:
            return "invalid"

        if email_verification_token.check_token(user, token):
            user.mark_email_verified()
            return "ok"

        # Assinatura válida mas fora do prazo: dá para oferecer novo envio.
        if email_verification_token.check_token(
            user, token, ignore_timeout=True
        ) and email_verification_token.is_expired(token):
            return "expired"

        # A conta já está confirmada: o token perdeu a validade justamente
        # porque foi usado (``email_verified`` entra no valor assinado).
        if user.email_verified:
            return "already"

        return "invalid"


@require_POST
def resend_verification(request):
    """Reenvia a confirmação.

    Autenticado, usa a própria conta. Anônimo, aceita um e-mail e responde
    sempre a mesma coisa. Nos dois casos há trava de tempo.
    """
    back = reverse("accounts:dashboard") if request.user.is_authenticated else reverse("accounts:login")

    if request.user.is_authenticated:
        user = request.user
        if user.email_verified:
            messages.info(request, _("Seu e-mail já está confirmado."))
            return redirect(back)
        if not user.can_request_verification_email() or ip_is_throttled(request, "resend"):
            messages.warning(
                request,
                _("Aguarde alguns instantes antes de pedir um novo e-mail."),
            )
            return redirect(back)
        send_verification_email(user)
        messages.success(request, _("E-mail de confirmação reenviado para %(email)s.") % {"email": user.email})
        return redirect(back)

    form = ResendVerificationForm(request.POST)
    if form.is_valid() and not ip_is_throttled(request, "resend"):
        user = User.objects.filter(
            email__iexact=form.cleaned_data["email"], is_active=True, email_verified=False
        ).first()
        if user is not None and user.can_request_verification_email():
            send_verification_email(user)

    # Resposta idêntica em todos os caminhos acima — inclusive quando o
    # formulário é inválido ou o IP está travado.
    messages.info(request, GENERIC_EMAIL_RESPONSE)
    return redirect(back)


def resend_verification_page(request):
    """Página com o formulário de reenvio (usada pelo link expirado)."""
    return render(
        request,
        "accounts/resend_verification.html",
        {"form": ResendVerificationForm(), "meta_title": _("Reenviar confirmação — JD PRINT")},
    )


# ---------------------------------------------------------------------------
# Recuperação de senha — mecanismo nativo do Django, telas nossas
# ---------------------------------------------------------------------------


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-input", "autocomplete": "new-password"})


class PasswordResetView(FormView):
    """"Esqueci minha senha".

    Não usamos ``auth_views.PasswordResetView`` porque ela monta o link com o
    host da requisição (``get_current_site``). Aqui o e-mail sai sempre com
    ``settings.SITE_URL`` e no idioma do cliente. O token continua sendo o do
    Django.
    """

    template_name = "accounts/password_reset.html"
    form_class = PasswordResetRequestForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Recuperar senha — JD PRINT")
        return context

    def form_valid(self, form):
        if not ip_is_throttled(self.request, "password-reset"):
            form.save()
        # Sempre o mesmo destino e a mesma mensagem, com ou sem conta.
        return super().form_valid(form)


class PasswordResetDoneView(TemplateView):
    template_name = "accounts/password_reset_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Recuperar senha — JD PRINT")
        context["generic_message"] = GENERIC_EMAIL_RESPONSE
        return context


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta_title"] = _("Nova senha — JD PRINT")
        return context


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---------------------------------------------------------------------------
# Favoritos
# ---------------------------------------------------------------------------


@login_required
@require_POST
def favorite_toggle(request):
    """Liga e desliga o favorito. Sempre POST, sempre do usuário da requisição.

    ## De quem é o favorito

    De `request.user`, e de mais ninguém. O navegador manda `product_id` — diz
    **o que**, nunca **de quem**. Não existe caminho para um `user_id` vindo de
    fora, então não existe o que forjar.

    ## Idempotente dos dois lados

    `get_or_create` e `delete()` sobre a chave única `(user, product)`: dois
    cliques rápidos, um duplo-clique ou um "reenviar" do navegador chegam ao
    mesmo estado final. A `UniqueConstraint` é a rede — mesmo numa corrida, o
    banco recusa a segunda linha.

    ## Só produto que o cliente poderia comprar

    `sellable()`: favoritar um rascunho seria guardar algo que ele não pode
    ver. Produto inexistente ou fora do ar dá 404, não uma linha inútil.
    """
    from apps.accounts.models import Favorite
    from apps.catalog.models import Product

    # `get_object_or_404(pk="abc")` levanta `ValueError`, não devolve 404: sem
    # esta conversão, um POST com lixo no `product_id` derruba a view com 500.
    try:
        product_id = int(request.POST.get("product_id", ""))
    except (TypeError, ValueError):
        raise Http404("Produto inválido.")

    product = get_object_or_404(Product.objects.sellable(), pk=product_id)

    favorito, criado = Favorite.objects.get_or_create(user=request.user, product=product)
    if not criado:
        favorito.delete()

    esta_favoritado = criado
    mensagem = (
        _("Adicionado aos favoritos.") if esta_favoritado else _("Removido dos favoritos.")
    )

    if request.headers.get("HX-Request") == "true":
        return _favorite_htmx_response(request, product, esta_favoritado, mensagem)

    messages.success(request, mensagem)
    return redirect(_favorite_safe_next(request))


def _favorite_safe_next(request) -> str:
    """Para onde voltar sem JavaScript, recusando host de fora."""
    destino = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if destino and url_has_allowed_host_and_scheme(
        url=destino,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destino
    return reverse("accounts:favorites")


def _favorite_htmx_response(request, product, esta_favoritado, mensagem):
    """Só os pedaços que mudaram — o mesmo desenho do carrinho.

    Da lista de favoritos a resposta reescreve a **grade inteira**, e não só o
    botão: desfavoritar ali tem de tirar o card da tela, e a grade precisa
    saber virar o estado vazio quando o último sai. É a lista de um cliente,
    não um catálogo — redesenhá-la é barato e sempre correto.
    """
    from apps.accounts.models import Favorite

    contexto = {
        "product": product,
        "toast_message": mensagem,
        "toast_level": "success",
        # O conjunto do contexto foi montado ANTES da gravação: refazê-lo aqui
        # é o que faz o coração já sair com o estado novo.
        "favorite_ids": frozenset(
            Favorite.objects.for_user(request.user).visible().values_list("product_id", flat=True)
        ),
    }
    if request.POST.get("from_list"):
        contexto["favorites"] = _favorite_products(request.user)
        return render(request, "accounts/_favorites_grid_update.html", contexto)

    return render(request, "accounts/_favorite_button_update.html", contexto)


def _favorite_products(user):
    """Os produtos favoritos, prontos para o card e na ordem do cadastro.

    `sellable()` porque a página é pública ao cliente: produto desativado sai
    da **lista**, não da conta. Se voltar ao ar, reaparece com o favorito
    intacto.

    O `prefetch` é o mesmo da vitrine: sem ele, cada card custaria consultas de
    tradução, mídia e variantes.
    """
    from apps.accounts.models import Favorite
    from apps.catalog.models import Product, ProductVariant

    ordem = {
        favorito.product_id: indice
        for indice, favorito in enumerate(
            Favorite.objects.for_user(user).only("product_id", "created_at", "id")
        )
    }
    if not ordem:
        return []

    produtos = (
        Product.objects.sellable()
        .filter(pk__in=ordem)
        .select_related("category")
        .prefetch_related(
            "translations",
            "media",
            "category__translations",
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(is_active=True)
                .select_related("color", "material")
                .prefetch_related("color__translations", "material__translations")
                .order_by("sort_order", "id"),
            ),
        )
    )
    # A ordem é a dos favoritos (mais recente primeiro), não a do catálogo.
    return sorted(produtos, key=lambda produto: ordem[produto.pk])


class FavoritesView(LoginRequiredMixin, TemplateView):
    """"Minha conta › Favoritos" — a mesma grade e o mesmo card da loja."""

    template_name = "accounts/favorites.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["account_page"] = "favorites"
        context["favorites"] = _favorite_products(self.request.user)
        context["meta_title"] = _("Meus favoritos — JD PRINT")
        context["meta_description"] = _("Os produtos que você guardou na JD PRINT.")
        return context
