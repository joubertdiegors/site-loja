# Operação da JD PRINT

O que fazer para manter a loja de pé: configurar o e-mail, salvar o que não
pode ser perdido e recuperar quando algo der errado.

> **Nenhum segredo neste documento.** Onde aparece `SUAUSUARIO`, `<senha>` ou
> `smtp.provedor.com`, é placeholder. Os valores reais existem em dois lugares
> só: no painel de quem os emitiu e no `.env` do servidor — que não é
> versionado e nunca é colado em documentação, chat ou issue.

---

## 1. Configuração de e-mail

### De onde sai o e-mail

Duas fontes possíveis, uma regra de prioridade:

```text
Admin › Core › configuração de e-mail
    ativa (“usar esta configuração”) e com servidor SMTP preenchido
        ↓  se não houver
variáveis do .env  (EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, ...)
```

A decisão acontece **a cada envio**, não na inicialização: mudar a configuração
no Admin passa a valer no próximo e-mail, sem reiniciar o servidor.

Enquanto ninguém cadastrar a configuração no Admin, o `.env` manda — que é como
o projeto sempre funcionou. Não existe estado em que as duas valham ao mesmo
tempo nem metade de cada.

Quem implementa: `apps/core/mailer.py`. A tela do Admin mostra, no topo, qual
fonte está em uso agora.

### Para onde vão as mensagens de contato

Toda mensagem enviada em `/contato/` é **gravada no banco antes** de qualquer
e-mail sair, e fica no Admin, em *Loja › mensagens de contato*.

A tela é de leitura — ninguém edita o que um cliente escreveu — e tem a
marcação de **tratado / a tratar**, que é como a equipe divide o que já foi
respondido. A ação em lote está no seletor de ações da lista.

A página de revenda não tem formulário próprio: ela manda o interessado para
este mesmo contato. Uma caixa de entrada só.

Isso quer dizer que **um problema no e-mail nunca perde uma mensagem**. Se o
aviso não chegar na caixa de entrada, a mensagem está lá.

O aviso é enviado para:

```text
Admin › Core › configuração de e-mail › “quem recebe contato e revenda”
    preenchido (um endereço por linha, ou separados por vírgula)
        ↓  se estiver em branco
quem já recebe os pedidos (a mesma cadeia: Admin ativo, senão ORDER_ADMIN_EMAILS do .env)
```

Repare que é uma regra **própria**, separada da de cima: o destinatário do
contato vale mesmo quando o SMTP vem do `.env` e a linha do Admin está lá só
para guardar os endereços.

O `Reply-To` do aviso é o e-mail de quem escreveu — responder na caixa de
entrada já responde ao cliente.

### A senha do SMTP

Ela é cifrada antes de entrar no banco (`apps/core/secrets.py`), com uma chave
derivada do `DJANGO_SECRET_KEY` — que vive no `.env`, **não** no banco. Quem
tiver só o dump não tem a chave; quem tiver só o `.env` não tem o dado.

Consequências práticas:

* a senha **nunca** é exibida de volta no Admin. O formulário mostra um campo
  vazio; deixá-lo vazio conserva a senha gravada;
* **trocar o `DJANGO_SECRET_KEY` invalida a senha gravada.** Ela volta como
  vazia e precisa ser digitada de novo. Isso é deliberado: melhor um campo
  visivelmente vazio do que um valor silenciosamente errado;
* o que isto **não** protege: quem tiver o servidor inteiro (banco + `.env` +
  código) lê a senha, como leria o `.env` direto. Não há como guardar uma
  credencial reutilizável que resista a isso.

### Testar o envio

*Admin › Core › configuração de e-mail › **Enviar e-mail de teste***

Usa **a mesma conexão** dos e-mails de pedido. Se este teste chega, o e-mail do
cliente chega. O resultado fica gravado na própria tela (data, sucesso ou falha
e a mensagem de erro já limpa de credenciais).

### Se um e-mail de pedido falhar

A tela do pedido (*Admin › Pedidos*) mostra, no bloco **AVISOS ENVIADOS**, os
três e-mails com data:

| Estado | Significa |
|---|---|
| ✓ enviado em … | saiu, e quando |
| ⚠ NÃO enviado — reenvie | já deveria ter saído e não saiu |
| — ainda não enviado | ainda não era hora (ex.: aviso de envio de um pedido que não saiu) |

Para reenviar: selecione o pedido na **lista** e escolha a ação —
*Reenviar confirmação ao cliente*, *Reenviar ordem de produção* ou
*Reenviar aviso de envio*. Cada uma é separada, para não mandar de novo o que o
cliente já recebeu.

Reenviar **não** altera pagamento, estoque, situação, valores nem snapshot, e
fica registrado no histórico do pedido com autor e data.

---

## 1b. Pagamento por transferência (provisório)

Enquanto `PAYMENT_PROVIDER=transfer` estiver no `.env`, o fluxo é este:

1. o cliente fecha o pedido e vê "Pedido recebido";
2. a loja recebe **[JD PRINT] Pedido … aguardando transferência**, com o total,
   o contato do cliente, os endereços e o IBAN cadastrado;
3. alguém responde ao cliente com os dados bancários;
4. quando o dinheiro entra, alguém abre o pedido no Admin e confirma o
   pagamento. **Só aí** o pedido vira pago, o estoque baixa e saem a
   confirmação do cliente e a ordem de produção.

Cadastre a conta em *Admin › Pedidos › dados para transferência* — sem ela o
aviso avisa que ela falta, mas o pedido é registrado do mesmo jeito.

Nada confirma pagamento sozinho: não há webhook, e não deve haver. Para voltar
ao cartão, troque `PAYMENT_PROVIDER=stripe` e recarregue a aplicação.

---

## 2. Backup

Três coisas precisam sair do servidor. Elas são independentes e se perdem de
formas diferentes.

| O quê | Onde | Se perder |
|---|---|---|
| **Banco** | PostgreSQL do PythonAnywhere | perde pedidos, clientes, catálogo. Irrecuperável |
| **`media/`** | disco do servidor | perde as fotos que os clientes enviaram para personalização e as imagens dos produtos |
| **`.env`** | disco do servidor | perde as credenciais (Stripe, banco, SMTP) e a `SECRET_KEY` — e sem ela a senha do SMTP gravada no banco não abre mais |

O código não entra nesta lista: ele está no Git.

### Banco

```bash
# No console do PythonAnywhere (Bash), com as credenciais do .env:
cd ~
pg_dump --host=$POSTGRES_HOST --port=$POSTGRES_PORT \
        --username=$POSTGRES_USER --no-owner --no-privileges \
        --format=custom "$POSTGRES_DB" \
        > backup-jdprint-$(date +%Y%m%d-%H%M).dump
```

`--format=custom` (e não SQL puro) porque ele comprime e permite restaurar
tabelas isoladas. A senha vem do `PGPASSWORD` ou do prompt — **nunca** na linha
de comando, que fica no histórico do shell.

### `media/`

```bash
cd ~/jd-print
tar -czf ~/backup-media-$(date +%Y%m%d-%H%M).tar.gz media/
```

### `.env`

Copie para o seu gerenciador de senhas, não para um arquivo ao lado do backup.
É o único item da lista que é puro segredo.

### Para onde levar

Baixe os arquivos para fora do PythonAnywhere (*Files › Download*). Um backup
que só existe no mesmo servidor não é backup.

> **Nunca** coloque um dump no Git. O `.gitignore` já recusa `*.dump`, `*.sql`,
> `*.bak*` e `*.sqlite3*`, mas a regra é sua, não dele.

### Com que frequência

Enquanto a loja é de teste, antes de cada etapa que mexa em migrations. Quando
começar a vender, diariamente — e antes de todo deploy.

---

## 3. Restore

### Banco

```bash
# ATENÇÃO: apaga o conteúdo atual do banco antes de restaurar.
pg_restore --host=$POSTGRES_HOST --port=$POSTGRES_PORT \
           --username=$POSTGRES_USER --dbname="$POSTGRES_DB" \
           --clean --if-exists --no-owner --no-privileges \
           backup-jdprint-AAAAMMDD-HHMM.dump
```

Depois:

```bash
python manage.py migrate --check   # o dump está na versão de migrations atual?
python manage.py check
```

Se `migrate --check` acusar migrations pendentes, o dump é mais antigo que o
código: rode `python manage.py migrate` para completar.

### `media/`

```bash
cd ~/jd-print
tar -xzf ~/backup-media-AAAAMMDD-HHMM.tar.gz
```

O projeto funciona com `media/` vazia — nenhuma página quebra por falta de
imagem, os espaços reservados entram no lugar. O que se perde é o conteúdo.

### `.env`

Recoloque o arquivo e reinicie a aplicação (*Web › Reload*). Se a
`DJANGO_SECRET_KEY` for **outra**, a senha do SMTP gravada no Admin precisa ser
digitada de novo (ver seção 1).

### Ensaie antes de precisar

Um restore nunca testado não é um plano. Faça um dump e restaure num banco
descartável ao menos uma vez, antes de a loja ter cliente de verdade.

---

## 4. Log

Com `DEBUG=False` não existe página de erro: sem log, uma falha de e-mail, de
webhook ou de pagamento desaparece sem deixar rastro.

Os logs da loja vão para `logs/jdprint.log` (rotacionado, 5 MB × 5 arquivos).
O diretório está no `.gitignore` — log de aplicação carrega e-mail e IP.

```bash
tail -f logs/jdprint.log
grep -i "webhook\|Falha ao enviar" logs/jdprint.log
```

No PythonAnywhere há também o log do servidor em *Web › Log files* (error log).

---

## 5. Antes de publicar de verdade

Lista curta do que ainda precisa de decisão humana:

- [ ] trocar a senha do superusuário `admin` (foi definida como
      `etapa13-teste-local` durante a validação em navegador da etapa 13);
- [ ] gerar uma `DJANGO_SECRET_KEY` nova, longa e aleatória, e guardá-la;
- [ ] cadastrar as tarifas de frete dos países ativos, ou desativar os que não
      têm (o Admin avisa quais são — *Core › países de entrega*);
- [ ] configurar o webhook da Stripe (fora do escopo da etapa 10);
- [ ] rodar `python manage.py check --deploy` no servidor e conferir que só
      resta o aviso `security.W021` (preload de HSTS), que é opt-in;
- [ ] remover os dados de teste (ver a lista no relatório da etapa 10);
- [ ] revisar o texto das quatro páginas (Admin › Loja › PÁGINAS). Elas já
      vêm escritas nos quatro idiomas, **sem prazo em dias nem regra legal**:
      esses números são decisão comercial e precisam ser acrescentados à mão;
- [ ] preencher “quem recebe contato e revenda” na configuração de e-mail;
- [ ] fazer um backup e **restaurá-lo** num banco descartável.
