# Deploy no PythonAnywhere

Guia da JD PRINT para publicar a loja no PythonAnywhere.

> **Nenhum segredo neste documento.** Onde aparece `SUAUSUARIO`, `sk_live_...`
> ou `<senha-do-painel>`, é placeholder. Os valores reais existem em dois
> lugares só: no painel de quem os emitiu e no `.env` do servidor — que não é
> versionado e nunca é colado em documentação, chat ou issue.

---

## Antes de começar

### O que a hospedagem impõe

| Restrição | Consequência para este projeto |
|---|---|
| Python vai até **3.13** (system image `innit`, Ubuntu 22.04) | o projeto já está em 3.13. Se a conta for anterior a 25/03/2025, ela está numa imagem antiga com teto 3.10 e **o Django 6.1 não roda** — migre em *Account › System image* antes de qualquer coisa |
| **PostgreSQL só em plano Custom**, como add-on pago | o banco oficial do projeto é Postgres. O plano Developer tem MySQL, não Postgres; o gratuito não tem nem MySQL para contas novas |
| Sem Node no fluxo de deploy | por isso `static/css/tailwind.css` é versionado (ver README, seção K) |
| Contas gratuitas só acessam uma allowlist de saída | `api.stripe.com` está nela; SMTP externo **não** |

### Divisão de responsabilidades

Não misture estes canais — é o que mantém o deploy previsível:

| Canal | Transporta | Nunca transporta |
|---|---|---|
| Git | código, templates, migrations, assets de origem | segredo, banco, upload |
| PostgreSQL | dados da loja | código |
| `staticfiles/` | saída do `collectstatic` | dado de cliente |
| `media/` | uploads dos clientes | código |
| `.env` do servidor | segredos | qualquer coisa versionada |

---

## 1. Criar a aplicação web

*Web › Add a new web app*

1. Escolha **Manual configuration** — **não** escolha "Django". A opção Django
   cria um projeto novo do zero; o nosso já existe e seria sobrescrito.
2. Selecione **Python 3.13**.

Anote o domínio atribuído: `SUAUSUARIO.pythonanywhere.com`.

## 2. Clonar o código

No console Bash:

```bash
git clone https://github.com/joubertdiegors/site-loja.git
cd site-loja
```

## 3. Criar a virtualenv

```bash
mkvirtualenv --python=/usr/bin/python3.13 site-loja
```

O prompt passa a mostrar `(site-loja)`. Em *Web › Virtualenv*, informe o
caminho: `/home/SUAUSUARIO/.virtualenvs/site-loja`.

## 4. Instalar as dependências

```bash
workon site-loja
cd ~/site-loja
pip install -r requirements.txt
```

Confira que veio o esperado. As versões são as mesmas validadas pelos 939
testes e não devem ser alteradas aqui:

```bash
python --version
pip freeze | grep -Ei "django|psycopg|stripe|dotenv"
```

## 5. Configurar o PostgreSQL

*Databases › Postgres* — inicie o servidor Postgres (add-on do plano Custom).

O painel mostra **host**, **porta**, **usuário** e **senha**. Copie-os para o
`.env` no passo seguinte; não os anote em arquivo do projeto.

Crie o banco da loja:

```bash
psql -h <host-do-painel> -p <porta-do-painel> -U super postgres
```

```sql
CREATE DATABASE jdprint;
CREATE USER jdprint WITH PASSWORD 'senha-forte-nova-aqui';
ALTER DATABASE jdprint OWNER TO jdprint;
```

> Não importe o `db.sqlite3` de desenvolvimento. Ele contém contas, endereços e
> pedidos de teste com dado pessoal real. Produção começa com banco limpo, e o
> catálogo é cadastrado pelo Admin.

## 6. Configurar as variáveis de ambiente

O `config/settings.py` lê um arquivo `.env` na raiz do projeto
(`load_dotenv(BASE_DIR / ".env")`). Crie-o **no servidor** — ele não vem do Git:

```bash
cd ~/site-loja
cp .env.example .env
nano .env
```

Gere uma `SECRET_KEY` nova, exclusiva da produção:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Os campos que obrigatoriamente mudam em relação ao exemplo:

```ini
DJANGO_SECRET_KEY=a-chave-gerada-acima
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=SUAUSUARIO.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://SUAUSUARIO.pythonanywhere.com

DJANGO_DB_ENGINE=postgres
POSTGRES_DB=jdprint
POSTGRES_USER=jdprint
POSTGRES_PASSWORD=a-senha-criada-no-passo-5
POSTGRES_HOST=host-do-painel
POSTGRES_PORT=porta-do-painel

SITE_URL=https://SUAUSUARIO.pythonanywhere.com
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

Proteja o arquivo:

```bash
chmod 600 .env
```

**`DJANGO_CSRF_TRUSTED_ORIGINS` não é opcional.** Sem ele, todo POST do site
publicado é recusado: login, cadastro, carrinho e checkout param de funcionar.

## 7. Configurar o WSGI

*Web › WSGI configuration file* — apague todo o conteúdo gerado e deixe:

```python
import os
import sys

path = "/home/SUAUSUARIO/site-loja"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
```

Não coloque segredo aqui. O `.env` do passo 6 já os fornece.

## 8. Arquivos estáticos

```bash
workon site-loja && cd ~/site-loja
python manage.py collectstatic --noinput
```

*Web › Static files*:

| URL | Directory |
|---|---|
| `/static/` | `/home/SUAUSUARIO/site-loja/staticfiles` |

O mapeamento intercepta o pedido **antes** de ele chegar ao Python — é o que
faz o site ser rápido, e o motivo de o Django não precisar servir estático.

## 9. Media

```bash
mkdir -p ~/site-loja/media
```

*Web › Static files*, uma segunda linha:

| URL | Directory |
|---|---|
| `/media/` | `/home/SUAUSUARIO/site-loja/media` |

Esta pasta **não vem do Git e nunca volta para ele**. É onde ficam as fotos que
os clientes enviam. Ela precisa de backup próprio: um `git pull` não a restaura.

## 10. Domínio e ALLOWED_HOSTS

Enquanto o domínio for o da hospedagem, o passo 6 já resolveu. Ao apontar o
domínio próprio, acrescente-o aos três campos:

```ini
DJANGO_ALLOWED_HOSTS=SUAUSUARIO.pythonanywhere.com,jd-print.com,www.jd-print.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://jd-print.com,https://www.jd-print.com
SITE_URL=https://jd-print.com
```

`SITE_URL` merece atenção: é dele que saem os links dos e-mails de confirmação
e de pedido. O projeto **não** monta esses links a partir do cabeçalho `Host`
justamente para que um `Host` forjado não faça a loja enviar, do próprio
domínio, um link para o site de quem atacou.

## 11. HTTPS

*Web › Force HTTPS* → ligar → **Reload**.

Com domínio próprio, ligue só **depois** de o certificado estar ativo; antes
disso você estaria forçando os visitantes a um protocolo que ainda não responde.

O `config/settings.py` já trata o proxy: `SECURE_PROXY_SSL_HEADER` faz o Django
reconhecer que a requisição chegou por HTTPS. Sem isso o site entra em loop de
redirecionamento, porque o TLS termina no proxy e o Django vê HTTP puro.

Com o *Force HTTPS* ligado, dá para deixar `SECURE_SSL_REDIRECT=False` — o
proxy já redireciona antes do Python. Manter `True` também funciona.

## 12. Webhook da Stripe

Em [dashboard.stripe.com/webhooks](https://dashboard.stripe.com/webhooks), crie
um endpoint apontando para:

```
https://SUAUSUARIO.pythonanywhere.com/pagamento/stripe/webhook/
```

Copie o *signing secret* e as chaves da API para o `.env` do servidor:

```ini
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
```

Comece pelas chaves de **teste**. Só troque para as de produção depois de um
pedido completo ter atravessado o fluxo inteiro.

Sem `STRIPE_SECRET_KEY` a loja continua funcionando por inteiro — apenas o
passo de pagamento avisa que o meio está indisponível.

## 13. Migrations

```bash
workon site-loja && cd ~/site-loja
python manage.py migrate
```

## 14. Superusuário

```bash
python manage.py createsuperuser
```

Use uma senha forte e exclusiva: o Admin dá acesso a pedidos e dados de
clientes. Não reaproveite a senha do PythonAnywhere nem a do GitHub.

## 15. Verificar

```bash
python manage.py check --deploy
python manage.py test
```

Depois, *Web › Reload* e confira no navegador:

- [ ] a Home abre em HTTPS, sem loop de redirecionamento
- [ ] CSS e fontes carregam (se não, revise o passo 8)
- [ ] o Admin abre e aceita login
- [ ] cadastro de conta funciona — é um POST, valida o CSRF do passo 6
- [ ] uma imagem enviada pelo Admin aparece no site, validando o passo 9
- [ ] um pedido de teste chega até a Stripe e o webhook retorna 200

Se algo falhar, o log fica em *Web › Error log*.

---

## Pendências obrigatórias antes de vender de verdade

Estes três pontos foram identificados na auditoria de repositório e
**deliberadamente adiados** para depois de o site estar no ar. Nenhum impede o
teste; todos impedem a operação real.

| # | Pendência | Efeito se ficar como está |
|---|---|---|
| 1 | **SMTP não configurado.** `MAILERS` define só `BACKEND`, sem `OPTIONS` com host, porta, usuário e senha | ao trocar para o backend SMTP, o Django tenta `localhost:25`, que não existe no PythonAnywhere. Confirmação de conta e de pedido **falham em silêncio**: o cliente não recebe nada e nenhum erro aparece |
| 2 | **`DEBUG` tem default `True`.** Se o `.env` sumir ou for renomeado no servidor, o site sobe com DEBUG ligado | traceback completo, caminhos internos e trechos de configuração expostos a qualquer visitante numa página de erro |
| 3 | **`SECRET_KEY` tem fallback inseguro** (`django-insecure-...`) | dá para subir produção sem chave e não perceber. Sessões, CSRF e os tokens de confirmação de e-mail ficam assinados com uma chave pública |

Enquanto o item 1 não for resolvido, mantenha `DJANGO_EMAIL_BACKEND` no backend
de console e trate a loja como vitrine, não como canal de venda: nenhum cliente
consegue confirmar o e-mail.

---

## Atualizações seguintes

```bash
workon site-loja && cd ~/site-loja
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

Depois, *Web › Reload*. O `.env` e a pasta `media/` não são tocados pelo
`git pull` — é exatamente o que se espera deles.
