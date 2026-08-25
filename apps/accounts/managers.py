"""Manager do usuário.

Duas responsabilidades além do padrão do Django:

* **normalizar** o e-mail antes de gravar (minúsculas), para que a unicidade
  não dependa de como o cliente digitou;
* **buscar sem diferenciar maiúsculas** em ``get_by_natural_key``, que é o que
  o backend de autenticação e o ``createsuperuser`` usam.
"""

from django.contrib.auth.base_user import BaseUserManager
from django.db.models import Q


class UserManager(BaseUserManager):
    use_in_migrations = True

    @staticmethod
    def normalize_username(username):
        return (username or "").strip()

    def normalize_email(self, email):
        """Minúsculas no endereço inteiro, não só no domínio.

        O ``normalize_email`` do Django só normaliza o domínio, porque o RFC
        permite que a parte local diferencie maiúsculas. Na prática, nenhum
        provedor sério trata ``Diego@`` e ``diego@`` como caixas diferentes, e
        tratá-las como iguais é o que impede duas contas para a mesma pessoa.
        """
        return super().normalize_email(email or "").strip().lower()

    def _create_user(self, username, email, password, **extra_fields):
        if not username:
            raise ValueError("O nome de usuário é obrigatório.")
        if not email:
            raise ValueError("O e-mail é obrigatório.")

        user = self.model(
            username=self.normalize_username(username),
            email=self.normalize_email(email),
            **extra_fields,
        )
        user.set_password(password)  # hashing do Django, nunca o nosso
        user.save(using=self._db)
        return user

    def create_user(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # Quem cria pelo terminal já provou ser dono da instalação; não faz
        # sentido deixar o superusuário preso atrás da confirmação de e-mail.
        extra_fields.setdefault("email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Um superusuário precisa de is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Um superusuário precisa de is_superuser=True.")

        return self._create_user(username, email, password, **extra_fields)

    # -- buscas ------------------------------------------------------------

    def get_by_natural_key(self, username):
        """Login por username sem diferenciar maiúsculas de minúsculas."""
        return self.get(username__iexact=self.normalize_username(username))

    def find_by_identifier(self, identifier):
        """Usuário por username **ou** e-mail, sem diferenciar maiúsculas.

        É o que permite o campo único "Usuário ou e-mail" da tela de login.
        Devolve ``None`` quando não encontra — quem chama decide a mensagem
        (que nunca revela se a conta existe).
        """
        identifier = (identifier or "").strip()
        if not identifier:
            return None
        return (
            self.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
            .order_by("pk")
            .first()
        )
