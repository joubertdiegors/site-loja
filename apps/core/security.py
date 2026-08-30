"""Travas por IP — o freio que a loja usa contra tentativa em série.

Vive em `core` porque não pertence a nenhum domínio: quem trava é o login
(`accounts`), o upload de personalização (`cart`) e a nova tentativa de
pagamento (`orders`). Enquanto o código morava em `apps/accounts/views.py`, os
outros dois apps importavam uma *view* de outro app para chegar até aqui.

## O que é uma cota, e por que não é "um por janela"

Cada trava permite N pedidos por janela, não um. Num escritório ou numa casa
várias pessoas dividem o mesmo IP, e a segunda delas não pode ficar sem criar
conta — ou sem receber o e-mail de confirmação — por causa da primeira.

## Onde os números moram

Nas settings, e não aqui: `EMAIL_VERIFICATION_IP_INTERVAL`,
`ACCOUNT_EMAIL_IP_LIMIT`, `LOGIN_FAILURE_LIMIT` e `LOGIN_FAILURE_WINDOW`. Todos
saem do `.env`, para apertar o freio em produção não exigir deploy de código.

## Limite conhecido

O contador vive no cache. Com o `LocMemCache` padrão ele é **por processo**: com
vários workers, o limite efetivo se multiplica, e um reload zera a contagem. A
trava continua valendo — só é mais frouxa do que os números sugerem. Um cache
compartilhado (`DatabaseCache` + `createcachetable`) resolve, e é decisão de
operação, não de código.
"""

from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    """O IP em que dá para confiar para efeito de trava.

    O **último** valor de ``X-Forwarded-For``, não o primeiro. O cabeçalho é uma
    lista que cada proxy vai acrescentando, e o cliente pode mandar a sua
    própria — se lêssemos o primeiro item, bastaria variar o cabeçalho a cada
    requisição para nenhuma trava por IP jamais disparar. O último item é o que
    o proxy da hospedagem escreveu, e depois dele o cliente não escreve.

    E só lemos o cabeçalho quando a instalação **declara** que há proxy
    (``SECURE_PROXY_SSL_HEADER``, ver `config/settings.py`). Sem essa
    declaração, ``X-Forwarded-For`` é apenas um texto que o visitante escreveu:
    ler daria a qualquer um uma trava nova a cada requisição. Nesse caso vale o
    ``REMOTE_ADDR``, que vem da conexão TCP e ninguém forja.
    """
    remoto = request.META.get("REMOTE_ADDR", "")

    if not getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        return remoto

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return remoto


def ip_is_throttled(request, bucket: str) -> bool:
    """Este IP já gastou a cota do ``bucket`` nesta janela?

    **Conta ao perguntar**: cada chamada incrementa. É o que se quer nos casos
    em que a própria ação é o custo (mandar e-mail, gravar arquivo, criar
    conta).

    Buckets em uso: ``resend``, ``password-reset``, ``register``,
    ``order-retry``, ``upload``. Cada um tem contador próprio — quem enviou
    fotos demais não fica sem conseguir tentar pagar.
    """
    interval = int(getattr(settings, "EMAIL_VERIFICATION_IP_INTERVAL", 60))
    limit = int(getattr(settings, "ACCOUNT_EMAIL_IP_LIMIT", 5))
    if interval <= 0 or limit <= 0:
        return False

    key = f"accounts:{bucket}:{client_ip(request)}"
    try:
        count = cache.incr(key)
    except ValueError:  # primeira vez nesta janela
        cache.set(key, 1, interval)
        count = 1
    return count > limit


def login_is_blocked(request) -> bool:
    """Este IP errou a senha vezes demais na janela atual?

    Só **lê**. Quem conta é `register_login_failure`, chamada apenas quando a
    tentativa falha — acertar a senha nunca aproxima ninguém do bloqueio, e num
    IP compartilhado quem está entrando normalmente não paga pelo vizinho que
    errou.
    """
    limit = int(getattr(settings, "LOGIN_FAILURE_LIMIT", 10))
    if limit <= 0:
        return False
    return int(cache.get(f"accounts:login:{client_ip(request)}", 0)) >= limit


def register_login_failure(request) -> None:
    """Conta mais uma senha errada deste IP."""
    window = int(getattr(settings, "LOGIN_FAILURE_WINDOW", 900))
    if window <= 0:
        return
    key = f"accounts:login:{client_ip(request)}"
    try:
        cache.incr(key)
    except ValueError:  # primeira falha nesta janela
        cache.set(key, 1, window)
