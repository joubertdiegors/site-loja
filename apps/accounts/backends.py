"""Autenticação por username **ou** e-mail no mesmo campo.

Continua sendo o mecanismo do Django: ``ModelBackend`` (hashing, permissões,
bloqueio de conta inativa) com uma única mudança — como o usuário é encontrado.
Nada de sessão própria nem de verificação de senha escrita à mão.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()

        identifier = username or kwargs.get(UserModel.USERNAME_FIELD) or kwargs.get("email")
        if identifier is None or password is None:
            return None

        user = UserModel._default_manager.find_by_identifier(identifier)
        if user is None:
            # Mesmo custo de CPU de um login válido: sem isto, dá para
            # descobrir quais contas existem cronometrando a resposta.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
