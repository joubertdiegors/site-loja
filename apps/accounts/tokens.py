"""Token de confirmação de e-mail.

Nada de criptografia caseira: reaproveitamos o gerador do Django
(``PasswordResetTokenGenerator``), que é HMAC com a ``SECRET_KEY`` do projeto,
carimbo de tempo embutido e comparação em tempo constante.

O que muda em relação ao token de senha:

* **sal próprio** — um token de confirmação nunca serve para trocar senha, e
  vice-versa;
* **estado próprio no hash** — ``email`` e ``email_verified`` entram no valor
  assinado. Consequências diretas dos requisitos:

  - assim que a conta é confirmada, ``email_verified`` muda e **o mesmo link
    para de funcionar** (uso único, sem tabela de token usado);
  - se o cliente trocar o endereço, o link antigo morre junto;

* **sem ``last_login``** — o gerador do Django inclui ``last_login`` para
  invalidar o link depois de um acesso. Aqui isso seria um defeito: quem se
  cadastra é autenticado na hora, e qualquer login posterior mataria um link de
  confirmação ainda dentro da validade.

* **prazo próprio** (``EMAIL_VERIFICATION_TIMEOUT``, 24 h por padrão) em vez de
  ``PASSWORD_RESET_TIMEOUT``, para os dois prazos serem configuráveis
  separadamente.

Manipular ``uid``, ``token`` ou ``email`` na URL não leva a lugar nenhum: o
usuário é procurado pelo ``uid`` e o token é conferido **contra esse usuário**,
com um segredo que só o servidor conhece.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes, force_str
from django.utils.http import base36_to_int, urlsafe_base64_decode, urlsafe_base64_encode


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "apps.accounts.tokens.EmailVerificationTokenGenerator"

    @property
    def timeout(self) -> int:
        return int(getattr(settings, "EMAIL_VERIFICATION_TIMEOUT", 24 * 60 * 60))

    def _make_hash_value(self, user, timestamp: int) -> str:
        return f"{user.pk}{user.email}{user.email_verified}{timestamp}"

    def check_token(self, user, token, *, ignore_timeout: bool = False) -> bool:
        """Assinatura + prazo. ``ignore_timeout`` separa "expirado" de "inválido".

        A checagem é a do Django, reescrita apenas para usar
        ``EMAIL_VERIFICATION_TIMEOUT`` — o método original lê
        ``settings.PASSWORD_RESET_TIMEOUT`` diretamente, e chamar ``super()``
        deixaria o prazo da senha mandando no prazo da confirmação.
        """
        timestamp = self.parse_timestamp(token)
        if timestamp is None or user is None:
            return False

        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(
                self._make_token_with_timestamp(user, timestamp, secret), token
            ):
                break
        else:
            return False

        if ignore_timeout:
            return True
        return self.token_age(token) <= self.timeout

    # -- auxiliares --------------------------------------------------------

    def parse_timestamp(self, token) -> int | None:
        """Carimbo de tempo do token, ou ``None`` se o formato não bate."""
        if not token:
            return None
        try:
            ts_b36, _hash = token.split("-")
            return base36_to_int(ts_b36)
        except ValueError:
            return None

    def token_age(self, token) -> int:
        """Idade do token em segundos (``-1`` quando ilegível)."""
        timestamp = self.parse_timestamp(token)
        if timestamp is None:
            return -1
        return self._num_seconds(self._now()) - timestamp

    def is_expired(self, token) -> bool:
        return self.token_age(token) > self.timeout


email_verification_token = EmailVerificationTokenGenerator()


def encode_uid(user) -> str:
    return urlsafe_base64_encode(force_bytes(user.pk))


def decode_uid(uidb64: str):
    """Usuário do ``uid`` da URL, ou ``None``.

    Nunca confia no que veio na URL: o ``uid`` só aponta *qual* conta conferir;
    quem autoriza é o token, conferido contra essa mesma conta.
    """
    if not uidb64:
        return None
    try:
        pk = force_str(urlsafe_base64_decode(uidb64))
        return get_user_model()._default_manager.get(pk=pk)
    except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
        return None
