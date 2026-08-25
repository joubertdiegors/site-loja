# Logo oficial da JD PRINT

Coloque aqui o arquivo da logo enviado pelo proprietário, com um destes nomes
(a ordem é a de preferência):

```text
static/images/logo/jdprint-logo.svg     <- preferido (vetor, nítido em qualquer tela)
static/images/logo/jdprint-logo.png
static/images/logo/jdprint-logo.webp
static/images/logo/logo.svg
static/images/logo/logo.png
```

Opcionalmente, um favicon:

```text
static/images/logo/favicon.svg   (ou .png / .ico)
```

Assim que o arquivo existir, o header e o rodapé passam a usá-lo
automaticamente — nenhuma alteração de código é necessária. Enquanto não
existir, o site mostra uma marca tipográfica provisória ("JD PRINT").

A logo é usada como está: sem recorte, sem filtro, sem redesenho.

## Cor da marca

A paleta roxa fica em `static/src/input.css`, no bloco `@theme`
(`--color-brand-50` … `--color-brand-900`). Substitua `--color-brand-600` pelo
roxo exato da logo e rode `npm run build:css`.
