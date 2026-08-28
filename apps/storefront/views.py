"""As páginas institucionais e o formulário de contato.

Uma view só serve as quatro páginas: o que muda entre elas é o conteúdo
cadastrado (`InstitutionalPage`) e, em uma delas, o formulário. Quatro views
com o mesmo corpo seriam quatro lugares para corrigir quando o layout mudasse.

## A página existe mesmo sem cadastro

Antes de alguém escrever a política de trocas, `/trocas-e-devolucoes/` continua
respondendo 200 — com o título que a rota conhece e um aviso curto de que o
texto está sendo preparado. Um 404 numa página que o rodapé linka seria pior:
quebraria o link em vez de dizer que ainda não há o que ler.

A de contato funciona mesmo sem nenhum texto: o formulário é o conteúdo.

## O envio não se perde

A mensagem é gravada **antes** de o e-mail sair. Provedor fora do ar não pode
apagar o pedido de um cliente — e é o que aconteceria se ela só existisse na
caixa de entrada de alguém. A falha vai para o log; o cliente vê sucesso,
porque para ele a mensagem foi entregue: está no banco.
"""

import logging

from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import Http404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.generic import FormView, TemplateView

from apps.storefront.forms import ContactForm
from apps.storefront.models import InstitutionalPage, PageSlug, page_cta_url

logger = logging.getLogger(__name__)


def _page(slug: str):
    """O conteúdo cadastrado desta página, ou ``None``."""
    return (
        InstitutionalPage.objects.filter(slug=slug)
        .prefetch_related("translations")
        .first()
    )


class InstitutionalPageMixin:
    """O contexto comum às quatro páginas."""

    #: Preenchido por cada rota (ver ``urls.py``).
    page_slug: str = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = _page(self.page_slug)
        rotulo = PageSlug(self.page_slug).label

        titulo = page.title if page else rotulo
        context.update(
            {
                "page": page,
                # Título de reserva: é rótulo de rota, não conteúdo
                # institucional — serve para a página nunca abrir sem `<h1>`.
                "page_title": titulo,
                "page_slug": self.page_slug,
                "meta_title": f"{titulo} | JD PRINT",
                "meta_description": page.meta_description if page else "",
                # A revenda termina mandando o leitor para o contato. Quem
                # define isso é `PAGE_CTA`, não o template: a chamada tem de
                # aparecer com ou sem texto cadastrado.
                "page_cta_url": page_cta_url(self.page_slug),
            }
        )
        return context


class InstitutionalPageView(InstitutionalPageMixin, TemplateView):
    """Envios e prazos, trocas e devoluções: só texto."""

    template_name = "storefront/page.html"

    def get(self, request, *args, **kwargs):
        """Despublicada e sem formulário, a página não é uma página."""
        page = _page(self.page_slug)
        if page is not None and not page.is_active:
            raise Http404("Página não publicada.")
        return super().get(request, *args, **kwargs)


class InstitutionalFormView(InstitutionalPageMixin, FormView):
    """Contato: o mesmo texto cadastrado, mais o formulário.

    Continua sendo uma classe separada de `InstitutionalPageView` porque só
    ela precisa de POST, validação e redirecionamento — e genérica o bastante
    (form, model e assunto são atributos) para uma segunda página com
    formulário não virar uma segunda view.
    """

    template_name = "storefront/page.html"
    #: Preenchidos pelas subclasses.
    model = None
    email_template = ""
    success_message = ""

    def form_valid(self, form):
        registro = self.model.objects.create(
            language=get_language() or "",
            **{campo: valor for campo, valor in form.cleaned_data.items() if campo != "website"},
        )
        self._notify(registro)
        messages.success(self.request, self.success_message)
        return redirect(self.request.path)

    def _notify(self, registro):
        """Avisa a equipe. Falhar aqui não desfaz o que já foi gravado."""
        from apps.core.mailer import contact_recipients

        destinatarios = [endereco for endereco in contact_recipients() if endereco]
        if not destinatarios:
            logger.warning(
                "Nenhum destinatário configurado: %s #%s ficou só no banco.",
                self.model.__name__,
                registro.pk,
            )
            return

        contexto = {"registro": registro, "site_name": "JD PRINT"}
        corpo = render_to_string(f"emails/{self.email_template}.txt", contexto)

        mensagem = EmailMultiAlternatives(
            subject="".join(self.get_email_subject(registro).splitlines()),
            body=corpo,
            to=destinatarios,
            # Responder vai direto para quem escreveu, sem copiar o endereço à
            # mão. O remetente continua sendo o da loja: usar o e-mail do
            # visitante como `From` faria a mensagem cair em spam.
            reply_to=[registro.email],
        )
        try:
            mensagem.send()
        except Exception:  # provedor fora do ar não pode perder a mensagem
            logger.exception(
                "Falha ao avisar a equipe sobre %s #%s", self.model.__name__, registro.pk
            )

    def get_email_subject(self, registro) -> str:
        raise NotImplementedError


class ContactView(InstitutionalFormView):
    page_slug = PageSlug.CONTACT
    form_class = ContactForm

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        from apps.storefront.models import ContactMessage

        self.model = ContactMessage
        self.email_template = "contact_message"
        self.success_message = _(
            "Mensagem enviada. Respondemos assim que possível."
        )

    def get_email_subject(self, registro) -> str:
        return f"[JD PRINT] Contato: {registro.subject}"
