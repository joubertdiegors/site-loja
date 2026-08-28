"""De onde saem os e-mails da loja.

Existiam duas fontes possíveis de configuração e nenhuma regra dizendo qual
vale. Agora existe, e é uma só:

.. code-block:: text

    EmailSettings (Admin), ativa e com servidor preenchido
        ↓  se não houver
    variáveis do .env / settings (EMAIL_HOST, EMAIL_PORT, ...)

O Admin só entra em cena quando alguém de fato cadastrar a configuração **e**
marcar "ativa". Enquanto isso, o ``.env`` manda — que é como o projeto sempre
funcionou e como o PythonAnywhere está configurado hoje.

A escolha acontece **a cada envio**, não na inicialização: mudar a configuração
no Admin passa a valer no próximo e-mail, sem reiniciar o servidor.

Um detalhe que este módulo conserta de passagem: o ``settings.py`` tinha um
dicionário ``MAILERS`` com a chave ``BACKEND`` que **nenhum código lia** — o
nome certo para o Django é ``EMAIL_BACKEND``. Na prática, a variável
``DJANGO_EMAIL_BACKEND`` do ``.env`` não fazia nada e o Django usava o backend
SMTP padrão, sem servidor configurado. Agora ela é lida de verdade
(``EMAIL_FALLBACK_BACKEND``).
"""

import logging
import re

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class EffectiveConfig:
    """A configuração que vai ser usada agora, e de onde ela veio.

    Existe para o Admin poder mostrar a origem sem repetir a regra de
    prioridade em outro lugar.
    """

    def __init__(self, source: str, **valores):
        self.source = source  # "admin" ou "env"
        self.__dict__.update(valores)

    def __repr__(self) -> str:  # pragma: no cover - diagnóstico
        return f"<EffectiveConfig source={self.source} host={self.host!r}>"


def resolve() -> EffectiveConfig:
    """Qual configuração vale neste momento."""
    from apps.core.models import EmailSettings

    configurada = EmailSettings.active()
    if configurada is not None:
        return EffectiveConfig(
            "admin",
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=configurada.host,
            port=configurada.port,
            username=configurada.username,
            password=configurada.password,
            use_tls=configurada.use_tls,
            use_ssl=configurada.use_ssl,
            timeout=configurada.timeout,
            from_email=configurada.sender or settings.DEFAULT_FROM_EMAIL,
            reply_to=configurada.reply_to,
            admin_recipients=(
                configurada.recipient_list() or list(settings.ORDER_ADMIN_EMAILS)
            ),
        )

    return EffectiveConfig(
        "env",
        backend=_env_backend(),
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        username=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS,
        use_ssl=settings.EMAIL_USE_SSL,
        timeout=settings.EMAIL_TIMEOUT,
        from_email=settings.DEFAULT_FROM_EMAIL,
        reply_to="",
        admin_recipients=list(settings.ORDER_ADMIN_EMAILS),
    )


#: O nosso próprio backend, para não entrarmos em recursão ao delegar.
_NOSSO_BACKEND = "apps.core.mailer.ConfiguredEmailBackend"


def _env_backend() -> str:
    """O backend real quando a configuração vem do ``.env``.

    Normalmente é ``EMAIL_FALLBACK_BACKEND``. Mas se alguém trocou o
    ``EMAIL_BACKEND`` por fora — o runner de teste põe ``locmem``, um
    ``override_settings`` põe o que quiser, um comando pode forçar arquivo —,
    essa troca tem que valer. Ignorá-la faria os e-mails escaparem para o
    console no meio de um teste que os esperava na caixa de saída.
    """
    atual = getattr(settings, "EMAIL_BACKEND", "")
    if atual and atual != _NOSSO_BACKEND:
        return atual
    return settings.EMAIL_FALLBACK_BACKEND


def build_connection(config: EffectiveConfig | None = None, *, fail_silently=False):
    """A conexão real, montada a partir da configuração que vale agora."""
    config = config or resolve()

    if config.backend.endswith("smtp.EmailBackend"):
        return get_connection(
            backend=config.backend,
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            use_tls=config.use_tls,
            use_ssl=config.use_ssl,
            timeout=config.timeout,
            fail_silently=fail_silently,
        )

    # Console, locmem, arquivo: não têm host nem credencial.
    return get_connection(backend=config.backend, fail_silently=fail_silently)


def admin_recipients() -> list[str]:
    """Para onde vai a ordem de produção de cada pedido novo."""
    return resolve().admin_recipients


def contact_recipients() -> list[str]:
    """Para onde vão as mensagens de contato e os pedidos de revenda.

    Regra própria, e diferente da dos pedidos de propósito::

        EmailSettings.contact_recipients preenchido
            ↓  se estiver em branco
        quem recebe os pedidos (a cadeia normal: Admin ativo, senão .env)

    O `resolve()` decide a configuração de **envio** inteira — servidor,
    credencial, remetente — e ali a regra da etapa 10 é "nunca metade de cada",
    porque credencial pela metade não conecta. Destinatário não é credencial:
    quem usa o SMTP do `.env` (o caso do PythonAnywhere hoje) precisa poder
    dizer no Admin quem recebe os contatos sem recadastrar o servidor.

    A linha é lida direto, sem passar por `active()`: `is_active` fala do
    servidor, não de quem lê a mensagem.
    """
    from apps.core.models import EmailSettings

    linha = EmailSettings.objects.filter(pk=1).first()
    if linha is not None:
        escolhidos = linha._split(linha.contact_recipients)
        if escolhidos:
            return escolhidos

    return resolve().admin_recipients


class ConfiguredEmailBackend(BaseEmailBackend):
    """O backend que o Django usa. Ele só decide qual backend de verdade usar.

    Fica no meio do caminho de propósito: assim **todo** envio do projeto —
    pedido, confirmação de conta, redefinição de senha, e-mail de teste —
    passa pela mesma regra de prioridade, sem cada módulo ter de lembrar de
    pedir a conexão certa.

    Também completa o remetente: quando a mensagem não pediu um remetente
    específico (ou seja, veio com o ``DEFAULT_FROM_EMAIL``) e o Admin tem um
    cadastrado, é o do Admin que vai. Uma mensagem que escolheu o próprio
    remetente continua com o dela.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        config = resolve()
        for message in email_messages:
            self._apply_sender(message, config)

        connection = build_connection(config, fail_silently=self.fail_silently)
        return connection.send_messages(email_messages)

    @staticmethod
    def _apply_sender(message, config):
        padrao = settings.DEFAULT_FROM_EMAIL
        if config.from_email and message.from_email in ("", None, padrao):
            message.from_email = config.from_email
        if config.reply_to and not message.reply_to:
            message.reply_to = [config.reply_to]


# ---------------------------------------------------------------------------
# Teste de envio
# ---------------------------------------------------------------------------

#: Trechos que um erro de SMTP costuma carregar e que não podem chegar à tela.
#: A mensagem crua de um servidor recusando login pode repetir o usuário, e
#: alguns servidores devolvem a linha do comando AUTH inteira.
_SENSIVEL = re.compile(
    r"(AUTH\s+\S+|password[=:\s]+\S+|senha[=:\s]+\S+|\b[A-Za-z0-9+/]{24,}={0,2})",
    re.IGNORECASE,
)


def sanitize_error(erro: Exception, config: "EffectiveConfig | None" = None) -> str:
    """A mensagem de erro que pode ser mostrada ao administrador.

    O erro cru do ``smtplib`` é útil ("Authentication failed", "Connection
    refused") e é isso que interessa. O que não pode passar é credencial: há
    servidores que ecoam o comando ``AUTH`` na resposta, e o Django inclui a
    representação da conexão em alguns erros.

    A limpeza é feita em duas passadas, e a **primeira é a que importa**: como
    conhecemos a senha e o usuário em uso, eles são apagados por valor. Confiar
    só em reconhecer formato (``password=``, ``AUTH ...``, base64 longo) é
    frágil — basta um servidor devolver a senha solta no meio de uma frase e
    ela passaria. Os padrões ficam como rede para o que vier de outra
    configuração que não a atual.
    """
    texto = f"{type(erro).__name__}: {erro}"

    config = config or resolve()
    for segredo in (getattr(config, "password", ""), getattr(config, "username", "")):
        if segredo and len(segredo) >= 4:
            texto = texto.replace(segredo, "[oculto]")

    texto = _SENSIVEL.sub("[oculto]", texto)
    texto = " ".join(texto.split())
    return texto[:280]


def send_test_email(recipient: str) -> tuple[bool, str]:
    """Manda um e-mail de teste com a configuração atual.

    Devolve ``(deu_certo, mensagem)``. A mensagem já vem própria para a tela:
    nada de senha, token ou string de conexão.
    """
    from django.core.mail import EmailMultiAlternatives

    config = resolve()
    # O artigo entra junto com o nome: "usando a configuração" / "usando as
    # variáveis" — sem isso a frase sai com concordância errada.
    origem = (
        "a configuração do Admin" if config.source == "admin" else "as variáveis do .env"
    )

    mensagem = EmailMultiAlternatives(
        subject="[JD PRINT] E-mail de teste",
        body=(
            "Este é um e-mail de teste da loja JD PRINT.\n\n"
            f"Se você recebeu esta mensagem, o envio está funcionando por {origem}.\n"
            f"Servidor: {config.host or '(backend local)'}\n"
        ),
        to=[recipient],
    )

    try:
        connection = build_connection(config, fail_silently=False)
        ConfiguredEmailBackend._apply_sender(mensagem, config)
        enviados = connection.send_messages([mensagem])
    except Exception as erro:  # noqa: BLE001 - o erro vira texto para a tela
        logger.warning("Falha no e-mail de teste para %s", recipient, exc_info=True)
        return False, sanitize_error(erro, config)

    if not enviados:
        return False, "O servidor aceitou a conexão mas não entregou a mensagem."
    return True, f"E-mail enviado para {recipient} usando {origem}."
