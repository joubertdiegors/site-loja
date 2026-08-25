"""O carrinho segue o cliente quando ele entra.

Ligado ao sinal ``user_logged_in`` do Django, e não à view de login: assim
cadastro, login e qualquer autenticação futura (um "entrar com link", por
exemplo) passam pelo mesmo caminho, sem repetição.

``django.contrib.auth.login`` troca o identificador da sessão (``cycle_key``)
mas **preserva o conteúdo** — por isso o carrinho do visitante ainda está lá
quando este código roda.
"""

from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from apps.cart.merge import merge_session_cart


@receiver(user_logged_in, dispatch_uid="cart.merge_session_cart_on_login")
def merge_cart_on_login(sender, request, user, **kwargs):
    if request is None:
        return  # login programático (comando, teste de unidade)

    report = merge_session_cart(request, user)
    if report.message:
        messages.info(request, report.message)
