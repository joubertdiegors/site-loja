# Decisões de arquitetura — Etapa 1

Registro das decisões que afetam etapas futuras. Cada item diz **o que foi
decidido**, **por quê** e **como estender**.

---

## 1. Traduções em tabela filha, não em colunas por idioma

**Decisão.** `ProductTranslation` e `CategoryTranslation` herdam de
`core.TranslationBase` (campo `language` + unicidade por `(master, language)`).

**Por quê.** Nove idiomas × quatro campos de texto dariam 36 colunas em
`Product`, e cada novo idioma exigiria migração. Com tabela filha, um idioma
novo é `INSERT`.

**Como estender.** Para tornar `Material`, `Color` ou o alt text de mídia
traduzíveis:

```python
class MaterialTranslation(TranslationBase):
    master = models.ForeignKey(Material, related_name="translations", on_delete=models.CASCADE)
    name = models.CharField(max_length=80)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["master", "language"], name="material_translation_unique_language"),
        ]
```

e adicione `TranslatableMixin` ao modelo. Nada nas tabelas existentes muda.

**Limite conhecido.** O alt text de mídia ficou sem tradução porque o Django
Admin não suporta inline dentro de inline; traduzi-lo exigiria uma tela
dedicada, sem ganho nesta etapa.

---

## 2. `pricing_mode` como fonte de verdade do par preço/margem

**Decisão.** O produto declara se o administrador digita o **preço** ou a
**margem**; o outro valor é sempre derivado em `recalculate_pricing()`.

**Por quê.** Recalcular os dois lados a cada gravação cria ida e volta
(`preço → margem → preço`) com perda de centavos por arredondamento. Com um
modo explícito, salvar N vezes não muda nada.

**Como estender.** Promoções e preços por variante devem reutilizar
`apps/catalog/pricing.py` (funções puras, sem dependência de modelo) em vez de
reimplementar as fórmulas.

---

## 3. `total_cost` é calculado, mas armazenado

**Decisão.** Coluna `total_cost` não editável, recalculada em `save()` a
partir de `cost_components()`.

**Por quê.** Uma property pura não pode ser filtrada, ordenada nem agregada em
SQL — e relatórios de custo vão precisar disso. O risco de a coluna divergir é
contido: só `QuerySet.update()` a contorna, e nenhuma parte do projeto usa
`update()` sobre campos de custo.

**Como estender.** Novos custos entram como chaves em `cost_components()`.
Se um dia forem muitos (mão de obra por hora, embalagem por item, taxa por
canal de venda), o dicionário vira um modelo `ProductCostLine` e
`cost_components()` passa a somá-lo — o resto do código não muda.

---

## 4. Produto base preparado para variantes

> **Superada na etapa 8.** O plano descrito aqui — campos comerciais em
> `Product` servindo de valor padrão herdado pela variante — foi executado na
> etapa 4 e **desfeito na etapa 8**: a herança criava duas fontes de verdade
> para o mesmo número. Ver a decisão 71. O registro abaixo fica pelo histórico.

**Decisão.** SKU, preço, estoque, cor e material estão hoje em `Product`, mas
cor e material são **M2M para tabelas próprias**, nunca texto no produto.

**Por quê.** O pedido é cadastrar o produto base agora sem impedir
`Branco / 15 cm`, `Preto / 20 cm` depois.

**Como estender.** Criar `ProductVariant` com:

```python
product = FK(Product, related_name="variants")
sku, sale_price, stock_quantity, color (FK), attributes...
```

Os campos correspondentes em `Product` passam a ser *valores padrão* herdados
pela variante quando ela não define o seu. Nenhuma tabela precisa ser
recriada; a migração é aditiva.

---

## 5. Categorias como árvore, não `categoria` + `subcategoria`

**Decisão.** Auto-relacionamento `parent`, profundidade validada até 5 níveis.

**Por quê.** Dois campos fixos travam o catálogo no segundo nível;
`Modelos › Animais › Gatos` já é um caso real.

**Como estender.** Se a árvore crescer a ponto de as consultas recursivas
pesarem, adicionar uma coluna `path` materializada (ou `django-treebeard`) é
uma migração local em `Category` — os produtos continuam apontando para a
mesma FK.

---

## 6. Estoque simples agora, movimentações depois

**Decisão.** `stock_quantity` + `allow_backorder` + `made_to_order` +
`production_lead_time_days` — em `Product` até a etapa 7, **em
`ProductVariant` desde a etapa 8** (decisão 71).

**Por quê.** A etapa pede o mínimo utilizável sem inventar um subsistema.

**Como estender.** Um app `inventory` com `StockMovement`
(entrada / saída / reserva / ajuste, por variante e por localização) passa a
ser a fonte de verdade, e `stock_quantity` vira o saldo consolidado
recalculado a partir das movimentações. A leitura pública já passa por
`ProductVariant.is_available` (e pelo `Product.is_available` derivado dele),
então o resto do sistema não precisa mudar.

---

## 7. Auditoria mínima

**Decisão.** `created_at`, `updated_at`, `created_by`, `updated_by`
(`core.AuditableModel`), preenchidos pelo admin via `AuditUserAdminMixin`.

**Como estender.** Histórico completo de alterações (quem mudou o preço, de
quanto para quanto) cabe em um app de auditoria com `django-simple-history` ou
uma tabela própria, sem alterar os modelos atuais.

---

## 8. Slug do produto gerado após as traduções

**Decisão.** O nome vive na tabela de traduções, gravada depois do produto.
`Product.save()` grava um slug provisório derivado do SKU; se o administrador
deixou o campo em branco, `TranslatedSlugAdminMixin.save_related()` o
regrava a partir do nome em português.

**Por quê.** É o preço de ter o nome fora da tabela do produto. A alternativa
(duplicar o nome em `Product`) criaria duas fontes de verdade.

**Como estender.** Para URLs por idioma, adicionar `slug` a
`ProductTranslation` (único por idioma) e resolver a URL pelo par
`(idioma, slug)`, mantendo o slug de `Product` como canônico.

---

## 9. Validação em três camadas

1. **Banco** — `CheckConstraint`/`UniqueConstraint`: valem para qualquer via de
   escrita (admin, shell, importação, script).
2. **Modelo** — `clean()`: mensagens claras por campo, inclusive as regras que
   o banco não expressa bem (produto ativo exige categoria, preço e nome em
   português).
3. **Admin** — formset das traduções: exige o português quando o produto é
   ativado, algo que o `clean()` do modelo não enxerga na criação (as
   traduções ainda não existem quando o produto é validado).

A regra de negócio nunca depende apenas do formulário.

---

## 10. O que foi deliberadamente deixado de fora

Carrinho, checkout, pagamento, clientes, login público, wishlist, avaliações,
pedidos, envio, cupons, vitrine, home dinâmica, variantes, personalização,
Stripe, transportadoras e API pública. A arquitetura acomoda todos, mas nada
disso foi implementado nesta etapa.

---

# Decisões de arquitetura — Etapa 2 (Home + seções)

## 11. App `home` separado do `catalog`

**Decisão.** A Home e suas seções vivem em `apps/home`, não em `catalog`.

**Por quê.** São coisas diferentes: `catalog` responde "o que a loja vende";
`home` responde "o que a loja mostra na página inicial". Uma seção da Home não
é um dado de catálogo — é uma decisão de vitrine, que muda com campanha, com
estação e com humor do administrador. Misturar os dois faria o catálogo carregar
regras de apresentação.

**Como estender.** Outras superfícies de vitrine (landing pages de campanha,
blocos da página de categoria) cabem no mesmo app, reaproveitando
`HomeSection` ou um irmão dela.

---

## 12. Um resolvedor por tipo de seção, fora do template

**Decisão.** `apps/home/services.py` tem um dicionário `RESOLVERS`
(tipo → função). A view pede o contexto pronto; o template só itera.

**Por quê.** O template não pode saber se os produtos vieram de escolha manual,
de uma categoria ou dos destaques — senão cada tipo novo vira um `{% if %}` a
mais no HTML. Com resolvedores, o template é um só e a lógica é testável sem
HTTP (22 testes cobrem os resolvedores diretamente).

**Como estender.** "Promoções", "lançamentos da marca X", "mais avaliados":
uma opção em `HomeSectionType` + uma função em `RESOLVERS`. Zero alteração de
template.

---

## 13. Tabela intermediária explícita para os produtos manuais

**Decisão.** `HomeSectionProduct` (seção, produto, `sort_order`) em vez de um
ManyToMany simples.

**Por quê.** Um M2M não guarda a ordem escolhida pelo administrador, e a ordem
é parte da decisão editorial ("o Gato aparece primeiro"). A tabela também é o
lugar natural para acrescentar depois um destaque por item, um texto próprio ou
uma data de validade.

---

## 14. "Mais vendidos" sem dados de vendas

**Decisão.** O tipo `BEST_SELLERS` existe, o resolvedor devolve lista vazia e a
seção some da Home. O admin avisa isso ao salvar.

**Por quê.** Não existe módulo de pedidos. Usar "mais recentes" ou "destaques"
como substituto seria apresentar ao cliente um dado que não é verdade, e ao
administrador uma funcionalidade que ele acha que está funcionando.

**Como estender.** Quando `orders` existir, `_best_sellers()` passa a somar
itens vendidos por produto (com uma janela de tempo configurável). Nada mais no
sistema muda.

---

## 15. CTA por referência interna, não por URL digitada

**Decisão.** `CtaMixin` com `cta_target` (nenhum / categoria / produto / URL) e
FKs para `Category` e `Product`.

**Por quê.** URL digitada à mão quebra quando o slug muda e não sabe traduzir-se
quando existirem rotas por idioma. A referência interna resolve a URL na hora da
renderização, via `get_absolute_url()`.

**Segurança.** A URL livre aceita apenas caminhos internos (`/algo`) ou
`http(s)://` — `javascript:` e `data:` são rejeitados na validação, porque esse
campo é preenchido por um humano e renderizado em um `href`.

---

## 16. Árvore de categorias carregada em memória

**Decisão.** `CategoryTree` carrega todas as categorias ativas em uma consulta
e resolve subárvores e raízes em Python.

**Por quê.** Uma seção "produtos da categoria X, incluindo filhas" precisaria de
consulta recursiva (ou de uma coluna `path`). Uma loja tem dezenas de
categorias, não milhões: a árvore inteira cabe em memória e custa uma consulta,
compartilhada por todas as seções da mesma requisição.

**Como estender.** Se a árvore crescer demais, este é o único ponto a trocar por
uma coluna `path` materializada — o resto do código fala com `CategoryTree`.

---

## 17. Orçamento fixo de consultas

**Decisão.** Dois testes usam `assertNumQueries`: um fixa o número total da
Home, outro garante que ele **não muda** quando o catálogo cresce.

**Por quê.** N+1 não aparece em desenvolvimento com 8 produtos; aparece em
produção com 800. Esse teste já pagou o custo: encontrou uma consulta por card
para a tradução do nome da categoria, corrigida com
`prefetch_related("category__translations")`.

**Consequência.** Alterar o card ou o service pode quebrar o teste. Isso é
intencional — a quebra obriga a olhar o custo antes de subir.

---

## 18. Páginas provisórias de produto e categoria

**Decisão.** `/produtos/<slug>/` e `/categorias/<slug>/` existem desde já, com
uma página de "em construção" que resolve o objeto real (404 correto para slug
inexistente e para produto fora do ar).

**Por quê.** O card precisa de `href`. Link para `#` é ruim para o visitante,
para acessibilidade e para os buscadores. Fixar as URLs agora também evita
redirecionamentos depois.

**Como estender.** A etapa da loja substitui as views mantendo as mesmas rotas
e os mesmos nomes (`catalog:product_detail`, `catalog:category_detail`).

---

## 19. Tailwind compilado, sem CDN

**Decisão.** Tailwind 4 via CLI (`npm run build:css`), com o CSS gerado
versionado em `static/css/tailwind.css`.

**Por quê.** O CDN do Tailwind compila no navegador: lento, dependente de rede e
desaconselhado em produção. Com o CSS versionado, quem só mexe no Python nem
precisa de Node instalado — só quem altera templates ou o tema.

**Componentes.** `static/src/input.css` define o tema (`@theme`) e um punhado de
classes (`.btn-primary`, `.card`, `.product-grid`, `.badge-*`) para os templates
não repetirem vinte utilitários por elemento. É deliberadamente pequeno: não é
um design system.

---

## 20. JavaScript mínimo, HTMX ainda não

**Decisão.** ~100 linhas de JavaScript sem biblioteca (menu do celular,
fechamento de menus suspensos, setas do carrossel). HTMX não foi adicionado.

**Por quê.** A Home é inteiramente renderizada no servidor; nenhuma interação
atual precisa de requisição parcial. Adicionar HTMX agora seria carregar uma
dependência para não usá-la. Ele entra quando houver o caso real: sugestões de
busca, filtros de listagem, adicionar ao carrinho.

**Degradação.** Sem JavaScript a página continua utilizável: o seletor de idioma
é `<details>` + formulário POST, o carrossel rola com o dedo e com o teclado, e
a navegação é feita de links.

---

## 21. Logo: usada, nunca recriada

**Decisão.** A tag `{% brand_logo_url %}` procura o arquivo em
`static/images/logo/` e, se não achar, o template mostra uma marca tipográfica
provisória.

**Por quê.** A logo é do proprietário. Desenhar uma imitação — mesmo "só para
visualizar" — cria um ativo falso que pode acabar em produção. A cor roxa da
paleta é declaradamente um ponto de partida até a cor exata da logo ser
aplicada em `static/src/input.css`.

---

# Decisões de arquitetura — Etapa 3 (idiomas, Shop e carrinho)

## 22. Troca de idioma: view própria em vez da do Django

**O sintoma.** Estando em `/en/` e escolhendo PT ou FR, o idioma não mudava.
Partindo de `/` funcionava.

**A causa.** `django.views.i18n.set_language` traduz a URL de retorno com
`translate_url()`, que faz `resolve(path)` **com o idioma ativo da
requisição**. O POST vai para `/i18n/setlang/`, que está fora do
`i18n_patterns`, então o idioma ativo é o do cookie (ou o padrão). Com
`prefix_default_language=False`, o resolvedor sob `pt-br` espera `/` e não
reconhece `/en/`: `Resolver404` → a URL volta intacta → o navegador é mandado
de novo para `/en/` com o cookie novo → e no `LocaleMiddleware` **o prefixo da
URL vence o cookie**. O cookie mudava; a página, não.

**A correção.** `apps/core/i18n.py::translate_path` resolve a URL sob o idioma
**da própria URL** (`get_language_from_path`) e a remonta sob o idioma de
destino; se a rota não resolver (404, arquivo, rota fora do `i18n_patterns`),
troca o prefixo diretamente. `apps/core/views.py::set_language` usa isso,
mantendo o resto do comportamento do Django (só POST, validação do destino,
cookie) e o mesmo nome de rota.

**Por que não um remendo.** A regra "resolver na origem, remontar no destino" é
a mesma para qualquer par de idiomas e continua valendo quando as URLs forem
traduzidas (`/fr/modeles/` ↔ `/modelos/`). Um `if` para EN→PT teria escondido
o problema.

---

## 23. Cookie de idioma em URLs sem prefixo

**Decisão.** `PreferredLanguageRedirectMiddleware` redireciona uma URL sem
prefixo para a versão prefixada quando o cookie aponta para um idioma
diferente do padrão.

**Por quê.** Com `prefix_default_language=False`, o Django decide que URLs sem
prefixo são sempre do idioma padrão e **ignora o cookie** nesse caso. É bom
para SEO — `/modelos/` é a versão canônica em português, e um buscador sem
cookie sempre a vê assim — mas quem escolheu francês e voltasse pelo endereço
sem prefixo cairia no português, contrariando a escolha registrada.

**Cuidados.** Só `GET`/`HEAD` (redirecionar um `POST` perderia os dados),
nunca em `/admin/`, `/i18n/`, `/static/` e `/media/`, e só para idiomas
configurados.

---

## 24. `/modelos/` a partir da árvore de categorias

**Decisão.** A vitrine é montada sobre a categoria raiz indicada em
`settings.SHOP_MODELS_CATEGORY_SLUG`. Nenhum campo novo em `Product`.

**Por quê.** A pergunta "quais produtos são modelos?" já tinha resposta no
banco: os que estão na árvore de Modelos. Criar um `product_type` duplicaria
essa informação e abriria espaço para os dois discordarem.

**Como estender.** `ShopView` recebe a raiz por atributo:

```python
path("filamentos/", ShopView.as_view(root_category_slug="filamentos"), name="filaments_shop"),
```

Se a categoria configurada não existir, a página explica o que falta em vez de
mostrar uma lista vazia sem contexto.

---

## 25. Ordenar por nome traduzido no banco

**Decisão.** Subconsulta (`Subquery` + `Coalesce`) traz o nome no idioma ativo,
com o português como reserva, e a ordenação acontece em SQL.

**Por quê.** O nome mora na tabela de traduções. Ordenar em Python quebraria a
paginação (só a página atual seria ordenada) e obrigaria a carregar o catálogo
inteiro. É o preço — pequeno — de não ter colunas por idioma, e vale a pena.

---

## 26. Carrinho na sessão, sem tabela

**Decisão.** `apps/cart/cart.py` guarda `{product_id: {"quantity": n}}` na
sessão do Django. Nenhum model foi criado.

**Por quê.** Não existe pedido, cliente nem checkout. Uma tabela `Cart` agora
teria que ser reconciliada com `Order` depois, e carrinhos abandonados de
visitante anônimo viram lixo no banco. A sessão resolve o problema desta etapa
inteiro.

**Preparado para variantes.** A chave é a identidade do item; quando
`ProductVariant` existir, ela passa a identificar o par produto/variante e
`CartLine` ganha o campo. A migração é ler o formato antigo — não há tabela
para converter.

**Regra de estoque.** `max_quantity_for(product)` concentra a decisão: sob
encomenda e venda sem estoque ignoram o saldo; o resto é limitado ao estoque
atual. Sem pedidos não há reserva, então a validação é sobre o estoque de
agora — e terá que ser refeita no checkout. Isso está dito no código.

**Produto que sai do ar.** As linhas são montadas a partir de uma consulta que
só aceita produtos ativos; o que não voltar é removido da sessão em silêncio.
Um carrinho não pode mostrar item que não existe mais.

---

## 27. HTMX: onde entrou e onde não entrou

**Entrou** onde a alternativa era recarregar a página inteira e perder o
contexto do visitante:

* adicionar ao carrinho (contador + aviso, sem sair da listagem);
* alterar quantidade e remover na página do carrinho;
* filtrar, ordenar e paginar no Shop (troca só a grade, com `hx-push-url`).

**Não entrou** na Home, que é conteúdo estático renderizado no servidor.

**Regra seguida.** Todo controle é um link ou formulário real. Sem JavaScript,
a loja inteira continua funcionando com POST + redirecionamento e mensagens do
Django. O HTMX é melhoria progressiva, não requisito.

**CSRF.** O token vai uma vez em `hx-headers` no `<body>`, em vez de ser
repetido em cada `hx-post`.

---

## 28. Um teste para uma classe de erro de template

**Decisão.** `apps/core/tests_templates.py` verifica que nenhum `{#` fica
aberto em uma linha e fechado em outra, e que nenhuma página renderizada
entrega `{{`, `{%` ou `{#` ao cliente.

**Por quê.** `{# ... #}` no Django é comentário **de uma linha só**. Escrito em
duas, não é comentário: o texto aparece na página. Aconteceu duas vezes nesta
etapa, e as duas vezes só foi percebido olhando a captura de tela. Agora o
teste percebe.

---

## 29. `CategoryTree` saiu de `home` para `categories`

**Decisão.** A árvore em memória virou `apps/categories/tree.py`.

**Por quê.** Nasceu para a Home, mas o Shop precisa da mesma coisa. Um app de
vitrine importar estrutura de outro app de vitrine seria acoplamento errado —
o conhecimento é de categoria.


---

# Decisões de arquitetura — Etapa 4 (produto, variantes, personalização, idiomas)

## 30. `ProductVariant`: a linha vendável

**O que o model anterior suportava.** Um SKU, um preço, um estoque, um peso.
`colors` e `materials` eram M2M **informativos**: davam para dizer "existe em
preto e branco", não para dizer nada sobre cada combinação.

**Onde falhava.** `Preto/300ml -> EUR 15,00 -> estoque 10` e
`Branco/500ml -> EUR 18,00 -> estoque 0` eram impossíveis. Vender "Roxo"
enquanto só "Preto" tem estoque seria mentir para o cliente.

**Decisão.** `ProductVariant` com SKU próprio, `color`/`material` (FK para as
tabelas que já existiam), `size` (texto: "15 cm", "300 ml") e
`sale_price`/`stock_quantity`/peso/dimensões — **campo nulo herda do produto**.
Só se preenche o que difere. `stock_quantity` é sempre da variante: é
exatamente o número que não pode ser compartilhado.

**Regra de convivência.** Produto **sem** variantes se comporta exatamente como
antes. Nada saiu de `Product`; as variantes são aditivas. Nenhum teste da etapa
3 mudou de comportamento por causa disso.

**Três eixos, não um sistema genérico.** Cor, tamanho e material cobrem a
operação real. Um sistema de atributos arbitrários (tabela de atributo + tabela
de valor + tabela de ligação) resolveria mais casos ao custo de um admin muito
pior. Quando fizer falta, ele entra **por cima**: a variante continua sendo a
linha vendável, e é ela que o pedido vai referenciar.

**Impacto onde importa:**

| Onde | O que mudou |
|---|---|
| Admin | um inline no produto; o resto da organização ficou igual |
| Carrinho | a linha virou `produto + variante + personalização` |
| Preço | `variant.effective_price` cai no preço do produto quando nulo; `pricing.py` intocado |
| Estoque | `max_quantity_for(produto, variante)` |
| Card | disponibilidade olha as variantes; produto com variante manda para a página |
| Orders (futuro) | `OrderItem` guarda `product_id` + `variant_id` + snapshot de preço |

**Cuidado que custou caro.** `Product.is_available` passou a consultar as
variantes — e isso criou um N+1 imediato em toda listagem. O teste de orçamento
de consultas pegou (36 consultas onde havia 20). A correção foi acrescentar
`variants` ao `prefetch_related` de todo lugar que renderiza card.

---

## 31. Personalização é campo do produto, não categoria

**Decisão.** `Product.personalization_type` (`none` / `photo` / `text` /
`photo_or_text`) e `personalization_text_limit`.

**Por quê não categoria.** "Gato personalizado" continua sendo da categoria
Gatos. Personalização é uma característica que atravessa o catálogo inteiro —
transformá-la em categoria quebraria a navegação e obrigaria o produto a estar
em dois lugares.

**Onde a regra mora.** Em `apps/cart/forms.py::AddToCartForm`. O `required` e o
`accept` do HTML são conveniência; um POST direto encontra a mesma validação —
inclusive o limite de caracteres, que é por produto.

---

## 32. Arquivo do cliente: tabela, não sessão

**Decisão.** `cart.CustomizationUpload` (arquivo, nome original, tipo detectado,
tamanho, sessão) e a sessão guarda **só o id**.

**Por quê.** O binário não pode ir para a sessão — ela é serializada a cada
requisição. E guardar um caminho solto na sessão significaria montar um caminho
de arquivo a partir de um texto depois: é assim que nasce travessia de
diretório. Com uma linha no banco, o futuro `OrderItem` só copia a referência.

**Validação do arquivo:**

* **assinatura**, não extensão — os primeiros bytes precisam ser de JPEG, PNG,
  WEBP ou GIF. Um PDF renomeado para `.png` é recusado (há teste);
* tamanho máximo (`CUSTOMIZATION_MAX_UPLOAD_SIZE`, 10 MB);
* nome gerado por nós (`uuid4` + extensão validada). O nome do cliente é
  guardado só para exibição — `../../etc/passwd.png` não vira caminho;
* vai para `MEDIA_ROOT`, nunca para `static/`.

---

## 33. Identidade da linha do carrinho

**Decisão.** A chave passou de `product_id` para
`produto:variante:impressão-da-personalização`.

**Por quê.** "Caneca com o nome Marie" e "Caneca com o nome Paul" são duas
linhas, não uma com quantidade 2. A mesma identidade é a que `OrderItem` vai
precisar.

**Compatibilidade.** O formato da etapa 3 (`{"12": {"quantity": 2}}`) é lido e
convertido na primeira leitura do carrinho — nenhum carrinho em sessão se perde.

---

## 34. Gaveta do carrinho: HTMX por cima de um link real

**Decisão.** A gaveta é renderizada em todas as páginas (fechada, com `inert`).
O ícone do carrinho continua sendo `<a href="/carrinho/">`; o JavaScript
intercepta o clique e abre a gaveta.

**Por quê `inert`.** Uma gaveta fechada com conteúdo focável é uma armadilha de
acessibilidade: o Tab entra em coisas invisíveis. `inert` tira o conteúdo da
ordem de foco e da árvore de acessibilidade de uma vez, sem gambiarra de
`tabindex`.

**Atualização.** As ações do carrinho devolvem trocas *out of band* (contador,
aviso, corpo da gaveta e, na página do carrinho, o painel). Adicionar responde
com o cabeçalho `HX-Trigger: jd:cart-open` — o servidor pede a abertura, o
JavaScript obedece. Sem JavaScript, tudo continua sendo POST + redirecionamento.

---

## 35. Idiomas: suportados != oferecidos

**Decisão.** `settings.LANGUAGES` continua com os nove idiomas **suportados
pelo sistema**; a tabela `core.SiteLanguage` define os **oferecidos na loja**.

**Por que não tornar `LANGUAGES` dinâmico.** `i18n_patterns` monta os prefixos
de URL na importação do urlconf e `get_supported_language_variant` é cacheado
pelo Django. Uma lista que muda em runtime deixaria URL e resolvedor
discordando — o tipo de bug que a etapa 3 passou horas caçando.

**Sem duplicar o que o Django já sabe.** Nome e nome nativo vêm de
`get_language_info`; a tabela guarda só código, disponibilidade e ordem. Duas
fontes para o mesmo nome só criariam divergência.

**Proteções.** O idioma padrão não pode ser desativado nem apagado (validação
no model, `save()` que o força ativo, admin sem botão de excluir). Tabela vazia
devolve o idioma padrão: a loja nunca fica sem seletor.

**Idioma desligado.** `/de/` responde 302 para a versão em português em vez de
servir uma página que o administrador desligou, e a troca para ele é recusada.

**Sem cache de propósito.** São duas consultas minúsculas por página. Um cache
aqui erraria feio em teste — uma alteração revertida pelo rollback deixaria o
valor antigo em memória. Se pesar, o lugar de resolver é `core/languages.py`.

---

## 36. Quantidade por página vem de uma lista fechada

**Decisão.** `?per_page=` só aceita valores de `SHOP_PAGE_SIZE_OPTIONS`
(4, 8, 12, 16, 20, 24 — todos múltiplos de 4, que fecham a grade de 4 colunas
do desktop e a de 2 do celular).

**Por quê.** Aceitar número arbitrário na URL seria um jeito fácil de pedir a
base inteira em uma requisição. Valor fora da lista cai no padrão, sem erro.

**Persistência.** Pela query string, preservada pelo `{% querystring %}` em
filtros, ordenação e paginação — e pela troca de idioma, que mantém a query.

---

## 37. Página de produto em `/produtos/<slug>/`

**Decisão.** Mantida a URL da etapa 3 (plural), em vez do `/produto/<slug>/`
sugerido no pedido.

**Por quê.** A rota já existia e já era usada pelos cards da Home e do Shop
desde a etapa 3; trocar significaria redirecionamento permanente e links
antigos apontando para o lugar errado, sem ganho. O plural também combina com
`/modelos/`. Se preferir o singular, é uma linha em `apps/catalog/urls.py` mais
um redirecionamento.

**Progressivo.** Sem JavaScript: miniaturas são links, a variante é escolhida
num `<select>` e o formulário é POST comum. Com JavaScript: a miniatura troca a
foto principal, os botões de cor/tamanho alimentam o `<select>` escondido e
atualizam preço, estoque e limite de quantidade, e o "adicionar" abre a gaveta.

**Uma armadilha encontrada na validação visual.** O JSON das variantes estava
sendo escapado pelo Django dentro do `<script>`, o `JSON.parse` falhava em
silêncio e o seletor bonito nunca aparecia. Corrigido com `|json_script`, que é
a forma segura de embutir dados — e com um teste que verifica que as aspas
chegam íntegras.


---

# Decisões de arquitetura — Etapa 5 (contas, clientes, carrinho persistente)

## 38. Custom User Model agora, não depois

**O que existia.** O `django.contrib.auth.models.User` padrão, com um único
superusuário de desenvolvimento e nenhuma conta real.

**Decisão.** Criar `accounts.User` (`AbstractBaseUser` + `PermissionsMixin`) e
apontar `AUTH_USER_MODEL` para ele.

**Por que agora.** Trocar o modelo de usuário depois que existirem pedidos,
endereços e faturas apontando para ele é uma migração de dados delicada, com
tabela renomeada e chaves estrangeiras reapontadas. Antes de existir o primeiro
cliente, é uma linha no `settings` e um banco recriado. Esta etapa é a última
janela barata.

**Custo assumido.** O banco de desenvolvimento foi recriado. O catálogo foi
exportado antes (`dumpdata`) e restaurado depois (`loaddata`) — inclusive o
produto criado à mão no admin; o superusuário anterior teve que ser criado de
novo. Está registrado no README.

**O que o modelo ganhou além do padrão:** `email` obrigatório e único,
`email_verified` + `email_verified_at`, `verification_sent_at` (trava de
reenvio) e `preferred_language`. O que ele **não** ganhou: nada de comercial.

---

## 39. `User` e `Customer` são duas tabelas

**Decisão.** `Customer` em `OneToOne` opcional com `User`.

**Por quê.** São dois ciclos de vida diferentes. A conta existe para entrar; o
cliente existe para faturar. Juntá-los produz o formulário de cadastro gigante
que espanta quem só queria comprar um vaso, e depois um `User` cheio de campos
nulos que o login não usa.

A separação também deixa portas abertas sem custo nenhum agora: uma empresa com
vários acessos (N contas → 1 cliente) e um pedido de balcão sem conta (cliente
sem `User`) cabem sem reescrever nada.

**Campos poucos de propósito.** Nome, sobrenome, telefone, empresa e NIF/VAT.
Endereço não entrou: endereço tem cardinalidade própria (cobrança, entrega,
vários) e nasce como `CustomerAddress` quando o checkout existir. Criar vinte
colunas por antecipação só geraria colunas vazias e migrations extras.

**O cliente nasce junto da conta**, vazio. A partir dele penduram pedidos,
endereços e faturas — e nada disso pode depender de um passo manual que alguém
esqueceu de fazer.

---

## 40. Unicidade sem caixa: três camadas, e a que vale é o banco

**Decisão.** `UniqueConstraint(Lower("username"))` e
`UniqueConstraint(Lower("email"))`, além de `unique=True` em cada campo e de
verificações no formulário e em `validate_unique()`.

**Por que não só `unique=True`.** Ele compara byte a byte:
`Diego@example.com` e `diego@example.com` passariam como contas distintas. Aí a
loja teria dois carrinhos, dois históricos e uma recuperação de senha ambígua
para a mesma pessoa.

**Por que não só no formulário.** Formulário não protege `objects.create()`,
`bulk_create()`, `loaddata`, `shell` nem uma API futura. Há teste para cada um
desses caminhos.

**Por que também normalizar.** O e-mail é gravado em minúsculas: a constraint
impede o duplicado, a normalização evita depender dela no dia a dia.

**Compatível com os dois bancos.** Índice funcional sobre `Lower(...)` é
suportado pelo PostgreSQL e pelo SQLite. Nenhum `CITEXT`, nenhum recurso de um
banco só.

---

## 41. Username: conjunto pequeno de caracteres, e reservados só no cadastro público

**Decisão.** `A-Z a-z 0-9 _ -`, de 3 a 30 caracteres, sem começar nem terminar
com separador, **e nunca com cara de e-mail**.

**Por que tão restrito.** O username vai para URL, template, exportação e API.
Acento traria duas grafias visualmente iguais (`joão`/`joao`); ponto e arroba
trariam ambiguidade com endereço de e-mail — o que é fatal porque o login usa
**um campo só** para os dois.

**Reservados (`admin`, `carrinho`, `suporte`, ...) ficam fora do modelo.** São
política de cadastro público, não integridade de dados: se estivessem no
validador do modelo, o próprio dono da loja não conseguiria criar o
superusuário `admin` pelo terminal. Ficam no formulário público.

---

## 42. Login por username **ou** e-mail: backend, não view

**Decisão.** `UsernameOrEmailBackend(ModelBackend)`, trocando apenas a busca do
usuário.

**Por quê.** Herdando de `ModelBackend`, continuam valendo o hashing do Django,
o sistema de permissões e a recusa de conta inativa. Reescrever autenticação
para ganhar um campo de formulário seria trocar código testado por código novo.

**Detalhe que não é decorativo:** quando o identificador não existe, o backend
ainda executa um `set_password` descartável. Sem isso, o tempo de resposta
diferencia "conta não existe" de "senha errada" — um oráculo de contas medido
com cronômetro.

---

## 43. Token de confirmação: o estado da conta faz parte da assinatura

**Decisão.** Subclasse de `PasswordResetTokenGenerator` com sal próprio e
`_make_hash_value = pk + email + email_verified + timestamp`.

**Por que não uma tabela de tokens.** Uso único, expiração e invalidação por
troca de e-mail saem de graça do valor assinado: confirmar muda
`email_verified`, e todo token emitido antes deixa de bater. Uma tabela traria
escrita, limpeza e um estado a mais para manter em dia — sem ganho.

**Por que sem `last_login`.** O gerador do Django o inclui, para invalidar o
link depois de um acesso. Aqui isso seria um defeito: quem se cadastra é
autenticado na hora, então qualquer login posterior mataria um link de
confirmação ainda dentro da validade.

**Por que reescrevemos `check_token`.** O método original lê
`settings.PASSWORD_RESET_TIMEOUT` diretamente. Chamar `super()` deixaria o
prazo da senha mandando no prazo da confirmação; os dois precisam ser
configuráveis separadamente (`EMAIL_VERIFICATION_TIMEOUT`).

**Expirado ≠ inválido.** `check_token(..., ignore_timeout=True)` separa os dois
casos, porque as telas são diferentes: uma oferece novo envio, a outra não tem
o que oferecer.

---

## 44. `SITE_URL`: o domínio do link não vem da requisição

**Decisão.** Todo link enviado por e-mail é montado com `settings.SITE_URL`.

**Por quê.** `request.get_host()` é controlado por quem manda o cabeçalho
`Host`. Um atacante dispara o formulário de recuperação de senha com
`Host: site-falso`, e a JD PRINT envia — do próprio domínio, com a própria
marca — um e-mail cujo link aponta para o site dele. `ALLOWED_HOSTS` ajuda em
produção, mas a defesa correta é não deixar o cliente escolher o domínio.

Foi por isso que a view de recuperação de senha do Django não foi usada como
está: ela monta o link com `get_current_site(request)`.

---

## 45. Enumeração de contas: a resposta é a mesma

**Decisão.** Recuperação de senha e reenvio de confirmação respondem sempre
"se existir uma conta associada a este e-mail, enviaremos as instruções", com o
mesmo destino e o mesmo status. O login tem uma única mensagem para senha
errada, conta inexistente e conta desativada.

**A exceção consciente:** o cadastro **diz** que o e-mail já está em uso. Sem
isso a pessoa não teria como concluir o cadastro nem entender por quê. O ganho
de esconder ali seria nulo (basta tentar cadastrar para descobrir), e o custo
seria um formulário que falha sem explicação.

**Trava de envio em duas dimensões:** por usuário (`verification_sent_at`, 2
min) e por IP — esta como **cota** (N por janela) e não como "um por janela",
porque um escritório inteiro divide o mesmo IP e a segunda pessoa não pode
ficar sem o e-mail por causa da primeira.

---

## 46. Carrinho: dois armazenamentos, uma interface

**O que existia.** `Cart` lia e escrevia um dicionário na sessão.

**Decisão.** Extrair `SessionStorage` e `DatabaseStorage` com a mesma interface
(`read`, `write`, `total_quantity`, `clear`). O `Cart` escolhe um no construtor
e continua trabalhando com o mesmo dicionário de sempre.

**Por que assim.** As regras do carrinho — limite de estoque, sob encomenda,
identidade da linha, limpeza do que saiu do ar — não têm nada a ver com onde os
dados moram. Duplicá-las numa segunda classe seria garantir que uma das duas
ficaria para trás. Resultado prático: as views, os templates e os testes das
etapas 3 e 4 continuaram valendo sem alteração.

**Visitante não vira linha no banco.** Quem talvez nunca volte não precisa
deixar registro em tabela nenhuma — e não sobra lixo para limpar depois.

**Um carrinho por usuário via `OneToOne`.** É o banco garantindo o requisito,
não uma regra que todo código futuro precisa lembrar de aplicar.

**`CASCADE` em produto, variante e anexo.** Um carrinho não pode exibir item
que não existe mais. `OrderItem` fará o contrário — `PROTECT` e cópia do preço
—, porque um pedido precisa continuar existindo mesmo se o produto sair do
catálogo.

---

## 47. Merge no sinal `user_logged_in`, não na view de login

**Decisão.** Um receptor de `user_logged_in` (em `apps/cart/signals.py`) move o
carrinho da sessão para a conta.

**Por quê.** Cadastro, login e qualquer autenticação futura (um "entrar por
link", por exemplo) passam pelo mesmo ponto. Colocar isso na view de login
significaria lembrar de repetir em cada nova porta de entrada.

**Identidade da linha preservada:** produto + variante + personalização. Somar
por produto transformaria "caneca com o nome Marie" e "caneca com o nome Paul"
em duas canecas iguais.

**Estoque não é reservado.** A soma é limitada ao disponível **agora**, como
qualquer outra escrita no carrinho, e o cliente é avisado quando algo foi
cortado. Reserva de verdade é assunto do checkout.

**Silêncio quando não há surpresa.** Carrinho que veio junto do cadastro não
gera aviso — o contador do header já conta a história. Dois carrinhos que se
encontram, sim: ver itens que não foram postos naquele navegador merece
explicação.

---

## 48. Cookies e sessão: o padrão acompanha o `DEBUG`

**Decisão.** `SESSION_COOKIE_SECURE` e `CSRF_COOKIE_SECURE` têm como padrão
`not DEBUG`, e o `.env` manda em produção. `HttpOnly` na sessão, `SameSite=Lax`
nos dois, `CSRF_COOKIE_HTTPONLY=False` (o HTMX precisa ler o token).

**Por que não fixar `True`.** Em HTTP puro, um cookie `Secure` simplesmente não
é gravado: o ambiente local pararia de funcionar, e a saída óbvia — desligar a
proteção "só por enquanto" — costuma virar permanente.

A rotação de sessão no login é a do Django (`login()` chama `cycle_key()`),
proteção contra fixação de sessão. Sair é POST com CSRF: um `GET` que encerra
sessão pode ser disparado por uma `<img>` em qualquer página.

---

## 49. Hashing rápido nos testes, real em produção

**Decisão.** Sob `manage.py test`, `PASSWORD_HASHERS` usa MD5.

**Por quê.** O PBKDF2 é caro de propósito — e deve continuar sendo em produção
—, mas com centenas de contas criadas em teste ele dominava o tempo da suíte
(45 s de um total de 48 s). Com o hasher rápido, a suíte inteira roda em ~25 s.

O teste que verifica o hashing real reativa o PBKDF2 com `override_settings` e
confere o prefixo do hash: a garantia continua sendo testada, só não em todos
os outros 675 testes.

---

## 50. Design System em tokens, não em classes soltas

`static/src/input.css` tem um bloco `@theme` com cor, tipografia, raio e sombra.
O template nunca escreve `#6d2ce0`: escreve `bg-brand-600`. A diferença aparece
no dia em que o proprietário quiser outro roxo — é uma linha, não uma busca por
trinta arquivos.

A regra é sustentada por teste (`DesignTokenTests`), porque regra de estilo que
depende de disciplina volta a ser quebrada na terceira pressa. As exceções são
explícitas e justificadas no próprio teste: e-mail (não existe CSS externo em
cliente de e-mail) e a cor do filamento, que é **dado**, não estilo.

## 51. Tailwind 4: `@utility` e `@layer components` não são intercambiáveis

Uma classe declarada dentro de `@layer components` **não pode** ser usada em
`@apply`. O build falha com `Cannot apply unknown utility class`. Quem precisa
ser composta (`btn`, `badge`, `alert`, `chip`) é declarada como `@utility`; o
resto continua em `@layer components`.

O outro tropeço do Tailwind 4 é o mesmo de sempre: o scanner lê **texto**. Um
nome partido por template (`text-brand-{% if dark %}300{% endif %}`) nunca é
encontrado. Por isso `components/logo.html` escreve as duas variantes inteiras.

## 52. Fontes hospedadas aqui, não no Google

Loja belga, público europeu: cada `<link>` para `fonts.googleapis.com` é o IP do
cliente entregue a um terceiro, e uma base legal a explicar. Os arquivos `.woff2`
(só latin e latin-ext) estão em `static/fonts/` — 134 KB, uma requisição externa
a menos e nada a declarar.

## 53. A gaveta do carrinho tem dois alvos de troca, não um

O corpo da gaveta (`#cart-drawer-body`) é trocado pelo HTMX a cada ação. O
cabeçalho ("3 itens") **não está dentro dele** — ficaria congelado. Em vez de
esticar o alvo da troca para incluir o cabeçalho (o que remontaria a lista
inteira a cada clique), o contador saiu para `cart/_drawer_count.html` e volta
como *out-of-band swap*. Mesmo mecanismo já usado pelo contador do header.

## 54. Telas preparadas em vez de models por antecipação

"Endereços" e "Meus pedidos" existem como tela, com estado vazio honesto. Não
existe `CustomerAddress` nem `Order`. Endereço tem cardinalidade própria
(cobrança, entrega, vários por cliente) e pedido tem máquina de estados — os
dois nascem junto do checkout, com as regras dele à vista. Criar as tabelas
agora seria adivinhar o formato e migrar duas vezes.

## 55. A cor do bloco de categoria vem do slug

A faixa de categorias da Home é colorida, mas `Category` não tem campo de cor.
Em vez de uma migration por decoração, o filtro `category_accent` deriva a cor
do slug. A cor é **estável**: não muda quando o administrador reordena as
categorias, e muda se ele renomear — que é exatamente o comportamento
esperado. Quando existir `Category.image`, o bloco troca sem tocar no resto.

## 56. Sem produto fictício, sem promessa fictícia

O redesenho não inventou dado nem funcionalidade para preencher espaço:

* nenhum filtro de preço, material ou cor no Shop — o backend não os tem;
* nenhum formulário de newsletter, nenhum selo de "frete grátis";
* nenhuma imagem decorativa fixa no código: onde a arte entra, há uma moldura
  tracejada e o caminho do Admin escrito;
* busca e favoritos continuam desabilitados e rotulados "em breve".

Uma interface que promete o que o sistema não faz não é design; é dívida.

## 57. Tabela do carrinho empilha abaixo de 640px

Quatro colunas (produto, quantidade, preço, remover) não cabem em 390px: davam
441px de largura e rolagem horizontal na página inteira. A saída não foi
`overflow-x: auto` — rolar o carrinho de lado no celular é péssimo — e sim
`max-sm:block` nas células, transformando cada linha num cartão. A semântica de
tabela continua intacta para o desktop e para o leitor de tela.

---

## 58. `DeliveryCountry` em `core`, e não em `shipping`

País é usado pelo endereço (accounts), pela tarifa (shipping) e pelo imposto
(orders). Em qualquer um desses três apps, os outros dois teriam que importar
o terceiro. Em `core` — junto de `SiteLanguage`, que já é configuração da loja
— ninguém depende de ninguém para cima.

Ele carrega também a alíquota de TVA. Imposto não é "coisa de frete": é
característica do país onde a entrega acontece.

## 59. Cancelamento é um eixo à parte, não um `OrderStatus`

**Decisão.** `OrderStatus` ficou com os cinco estados pedidos. O cancelamento
solicitado entrou como campo próprio (`cancellation_status`), não como valor de
status.

**Por quê.** Um pedido com cancelamento pedido continua `confirmed`: foi pago,
está na fila de produção e só sai de lá quando alguém decidir. Se
"cancellation_requested" fosse um `OrderStatus`, ele descreveria nem o dinheiro
nem a produção — e recusar a solicitação exigiria lembrar qual era o status
anterior. Com um eixo separado, recusar é uma linha e o pedido nunca perde o
próprio estado.

## 60. Preço com TVA embutida

**Decisão.** `total = subtotal + frete − desconto`. O imposto é a parcela
**contida** nesse total, não uma soma sobre ele.

**Por quê.** No varejo B2C da União Europeia o preço anunciado tem que ser o
preço final (Diretiva 98/6/CE). Somar 21% na última tela do checkout, além de
irregular, é a origem clássica de carrinho abandonado. O cliente paga o mesmo
nos dois modelos; a diferença é que aqui o número nunca muda debaixo dele.

A alíquota é a do país de **destino** (venda a distância B2C, regime OSS) e é
copiada para o pedido: mudança de alíquota não reescreve fatura antiga.

## 61. Stripe Checkout hospedado, não Payment Element

**Decisão.** O cliente é redirecionado para a página da Stripe.

**Por quê.** Nenhum dado de cartão passa pelo nosso servidor nem pelo nosso
JavaScript — a loja fica no escopo PCI mais simples (SAQ-A em vez de
SAQ-A-EP). A Stripe resolve sozinha o 3-D Secure do PSD2, oferece
Bancontact/iDEAL conforme o país e traduz a própria página para os quatro
idiomas da loja. O Payment Element daria controle visual em troca de um fluxo
de pagamento inteiro em JavaScript, que é o oposto do que este projeto é.

**Custo da escolha.** O cliente sai do domínio da loja no último passo. É um
preço conhecido e pago de propósito.

## 62. `csrf_exempt` existe em um lugar só, e é o certo

O webhook da Stripe. O CSRF protege contra o **navegador do cliente** ser
induzido a enviar um POST; quem chama aquela URL não é um navegador — é o
servidor da Stripe, que não tem cookie de sessão nem token nosso. A
autenticação adequada ali é a assinatura `Stripe-Signature`: HMAC sobre o corpo
bruto, com um segredo que só nós e a Stripe conhecemos.

Por isso a ausência de `STRIPE_WEBHOOK_SECRET` é **erro**, nunca um "deixa
passar": sem ela, quem descobrisse a URL declararia pedidos como pagos.

## 63. Idempotência do webhook mora no banco, não num `if`

`WebhookEvent` grava `(provider, event_id)` com `UniqueConstraint` **antes** de
o evento ser aplicado. A segunda entrega esbarra na constraint e sai por 200
sem fazer nada.

Um `if WebhookEvent.objects.filter(...).exists()` não resolveria: a Stripe
reenvia, e duas entregas simultâneas passariam as duas pelo `if` antes de
qualquer uma gravar. Quem serializa isso é o banco.

Se o processamento falhar, o registro é apagado e a resposta é 500 — a Stripe
reenvia e a nova entrega tenta de novo. Marcar como processado algo que falhou
seria pior do que reprocessar.

## 64. Estoque cai na confirmação do pagamento, não no carrinho

Carrinho não é compromisso. Reservar ali significaria segurar peça de quem
talvez nunca volte, e obrigaria uma rotina de expiração de reserva.

A baixa acontece uma vez, com `select_for_update`, quando o pagamento é
confirmado. Se faltar estoque **nesse** ponto, o dinheiro já entrou: recusar
seria pior para o cliente do que avisar a produção. O saldo nunca fica
negativo, a falta entra no histórico e vira nota interna — e reembolso é ação
administrativa explícita, nunca automática.

## 65. Número do pedido tem sequência própria

`JD-2026-000001` sai de `OrderNumberSequence`, uma linha por ano lida com
`SELECT ... FOR UPDATE`.

O PK não serve: vaza o volume da loja, muda numa importação de dados e não tem
ano. E `count() + 1` também não: dois checkouts simultâneos leriam o mesmo
total. Como a reserva acontece dentro da transação que cria o pedido, uma
criação que falha devolve o número em vez de deixar buraco na contabilidade.

## 66. `OrderAddress` é cópia, não referência

Se o pedido apontasse para `CustomerAddress`, corrigir o número da casa hoje
mudaria a nota fiscal de três meses atrás — e apagar o endereço deixaria o
pedido sem destino. `source` guarda de onde a cópia veio, mas nada é lido de lá.

O mesmo raciocínio vale para todos os campos de `OrderItem`: nome, SKU,
variante, preço, prazo de produção e personalização são copiados. O produto
continua referenciado com `PROTECT` para o admin abrir a ficha, e catálogo se
**desativa** (`status = inactive`), não se apaga.

## 67. `/carrinho/finalizar/` continua respondendo 200 ao visitante

A URL é da etapa 3 e não muda de endereço por organização interna. O que mudou
foi o que há do outro lado.

Um `redirect` para o login teria sido mais curto de escrever e pior para a
loja: o visitante que chegou até o último passo veria uma tela de senha sem
entender por quê. Aqui ele vê o resumo do que comprou e o convite — com o
carrinho intacto, que é a parte que importa.

## 68. Prazo de produção é o maior item, não a soma

A oficina imprime em paralelo. Somar três peças de 3 dias daria 9 dias, um
prazo que nunca acontece e que só serviria para assustar o cliente. O prazo
mostrado é `max(produção dos itens) + transporte do método`, e o do item vai
para o snapshot: se o produto ficar mais rápido amanhã, o pedido antigo
continua tendo sido prometido com o prazo de ontem.

## 69. Faixa de peso: mínimo inclusivo, máximo exclusivo

0–2000 e 2000–5000 não se sobrepõem, e 2000 g cai na segunda faixa. Sem essa
convenção, toda tabela de preço precisaria ser cadastrada com 1999 — e alguém,
algum dia, cadastraria 2000 nas duas.

O `clean()` recusa faixas sobrepostas para o mesmo método e país: com duas
faixas cobrindo o mesmo peso, o preço "certo" passaria a depender da ordenação
da consulta.

## 70. O admin do pedido é para operar, não para editar

Valores, snapshots dos itens e endereços copiados são somente leitura. Pedido é
documento contábil: o que aconteceu não se reescreve.

Criar pedido pela mão está desligado (nasceria sem pagamento, sem snapshot e
sem baixa de estoque) e apagar também (apagar pedido é apagar contabilidade —
cancelar é outra coisa). O que se altera é o estado da produção, o rastreio e
as notas internas. Marcar como enviado e decidir um cancelamento são **ações**
explícitas, que registram autor e motivo no histórico.


---

# Decisões de arquitetura — Etapa 8 (catálogo: genérico × vendável)

## 71. `Product` é a definição; `ProductVariant` é o que se vende

**Decisão.** Preço, custo, margem, estoque, `allow_backorder`, peso, dimensões,
tempo de impressão, prazo de produção e `made_to_order` saíram de `Product` e
passaram a existir **só** em `ProductVariant`. Cor e material deixaram de ser
M2M do produto e passaram a ser eixos da variante. Todo produto vendável tem
pelo menos uma variante ativa.

**Por quê.** A etapa 4 criou a variante como camada *aditiva*: ela podia deixar
um campo nulo e herdar o do produto. Parecia econômico e era uma armadilha —
dois lugares guardando o mesmo número. Enquanto os dois concordam, nada
aparece; quando divergem (alguém corrige o peso no produto e esquece a
variante), o cliente paga um frete e a transportadora cobra outro. E o caso que
tornou a herança insustentável é o mais comum da loja: a mesma peça em 10 cm e
em 25 cm **não pesa o mesmo** e **não demora o mesmo** — herdar um valor único
estava sempre errado para uma das duas.

"Vaso Facetado" não tem preço. "Vaso Facetado, preto, 25 cm, PLA, € 27,90,
300 g, pronto em 3 dias" tem — e é isso que entra no carrinho, é pesado no
frete e é produzido.

**Consequência.** Não existe mais "variante implícita" montada com os dados do
produto. Produto sem variante ativa não é vendável: fica fora da vitrine
(`Product.objects.sellable()`), não entra no carrinho e o Admin recusa ativá-lo
(validação no formset do inline, não no `clean()` do produto — o admin grava o
pai antes dos inlines, e o modelo ainda não enxerga a variante que está sendo
criada na mesma tela).

**O que `Product` ainda responde sobre comércio** — `display_price`,
`price_range`, `available_stock`, `is_available`, `stock_state`, `made_to_order`,
`production_lead_time_days` — é **derivado** das variantes, num único lugar
(`models.py`). É resumo para o card e para o Admin, nunca fonte. Um teste que lê
o código-fonte recusa que qualquer arquivo comercial volte a ler preço, estoque,
peso ou prazo pelo produto.

## 72. A migração dos 23 produtos: três passos, não um

**Decisão.** `0003` acrescenta os campos comerciais à variante; `0004` move os
dados; `0005` remove os campos do produto. Três migrations, nessa ordem.

**Por quê.** O `makemigrations` gerou **uma** migration com `AddField` e
`RemoveField` na mesma transação. Ela teria apagado os dados comerciais dos 23
produtos antes que houvesse onde copiá-los: as colunas novas nasceriam vazias e
as antigas sumiriam no mesmo passo. Entre acrescentar e remover tem que haver
uma migration de dados.

**Como a 0004 move.** Produto **sem** variante ganha uma, copiando tudo e
**reaproveitando o SKU do produto** — é literalmente a mesma coisa que se
vendia antes, só que na tabela certa; SKU novo só quando o do produto já
estivesse em uso por outra variante, e aí determinístico (`-2`, `-3`). Produto
**com** variantes tem cada campo nulo preenchido a partir do produto — que era
exatamente o que a herança fazia em tempo de leitura. Cor e material do M2M só
descem para a variante quando havia **exatamente um** cadastrado: com dois, qual
deles seria o da peça é uma pergunta sem resposta no dado.

A migration é reversível (`devolver_ao_produto`), o que só é útil junto com a
reversão da 0005 — mas mantém a 0004 honesta.

Resultado: 23 produtos, 27 variantes, nenhum produto órfão.

## 73. A página do produto sempre tem uma variante selecionada

**Decisão.** `Product.default_variant` = a primeira **disponível**; se todas
estiverem esgotadas, a primeira ativa. `None` só quando não há variante nenhuma.

**Por quê.** A alternativa era abrir a página sem escolha e mostrar uma faixa de
preço ("€ 19,90 – 27,90"). Uma faixa não é um preço: o cliente não sabe o que
vai pagar, o botão não sabe o que adicionar e a ficha técnica não sabe qual peso
mostrar. Com uma variante selecionada, tudo na página é sobre uma coisa
concreta.

Cair na primeira ativa quando todas estão esgotadas é deliberado: a página
precisa de preço e ficha **para poder dizer que acabou**. Uma página vazia não
informa nada.

**Nenhuma opção fica escondida.** O seletor lista todas as variantes ativas,
inclusive a que antes era representada pelos dados do produto — e sem nenhuma
opção artificial chamada "original". As esgotadas aparecem `disabled`: sumir com
elas faria o cliente achar que a cor não existe, em vez de que acabou.

## 74. O `<select>` escondido também precisa funcionar

**Achado na validação com navegador real.** Com JavaScript ligado, o seletor
nativo recebe `sr-only` e os botões de cor/tamanho tomam a frente. O `<select>`
continua no DOM — é ele que carrega o `variant_id` no POST — e continua
acessível por teclado e por leitor de tela. Ele não tinha ouvinte de `change`.

Quem escolhesse por ele trocava a variante enviada no formulário e via na tela o
preço, o peso e o estoque da opção **anterior**. Compraria uma coisa achando que
era outra. Um ouvinte no `<select>` fecha o buraco; o teste no navegador
(troca a opção pelo seletor e confere preço, peso, referência, dimensões e
limite de quantidade) impede que ele volte.

## 75. Peso e prazo do frete: sempre da variante

**Decisão.** `line_weight_grams` e `production_days` leem `variant.weight_grams`
e `variant.production_lead_time_days`. Linha sem variante pesa zero e não tem
prazo.

**Por quê.** É o ponto onde a herança custava dinheiro de verdade. Cenário do
enunciado: 2 × 150 g + 1 × 400 g = **700 g** — três números que só existem
porque cada variante tem o seu.

Variante **com** peso não cadastrado conta zero, e não um valor inventado: um
frete zerado é um erro que o administrador vê e corrige; um frete estimado é um
erro que ninguém vê até a fatura da transportadora chegar.

O prazo continua sendo o **maior** entre os itens (decisão 68), agora lido da
variante: a oficina imprime em paralelo, e a peça de 25 cm é que manda.

## 76. `effective_price` e `effective_weight` foram removidos

Existiam para a herança: `sale_price` podia ser nulo e `effective_price` caía no
preço do produto. Sem herança, viraram sinônimos exatos dos campos — e dois
nomes para a mesma coisa fazem o próximo leitor supor que existe diferença entre
eles. `variant.sale_price` e `variant.weight_grams` dizem o que são.

## 77. `Cart.add` resolve a variante; o formulário exige a escolha

**Decisão.** `Cart.add(product)` sem variante usa `product.default_variant` e
recusa se não houver nenhuma — nunca grava uma linha com `variant_id` nulo. Já
`AddToCartForm` **exige** `variant_id` quando o produto tem mais de uma opção
ativa.

**Por quê.** São camadas com responsabilidades diferentes. O formulário é a
fronteira com o cliente: chegar ali sem `variant_id` num produto de várias
opções significa que a escolha se perdeu no caminho, e adivinhar a cor por ele
seria pior do que recusar. `Cart.add` é a API interna, e a invariante que ela
precisa garantir é outra: **nenhuma linha comercial sem variante**.

Produto de opção única não tem nada a escolher — a página manda a variante num
campo oculto, e o formulário aceita a ausência resolvendo para a única que
existe.


---

# Decisões de arquitetura — Etapa 9 (combinações, cadastro e cores)

## 78. A matriz de combinações (duas tentativas antes da regra final)

> **Superada.** A regra em vigor é a da decisão 103. Este registro fica porque
> as duas tentativas descartadas explicam por que ela é como é.

**O problema.** Cor e tamanho não formam uma grade cheia. Com
`Preto/25`, `Branco/30` e `Roxo/25`, a combinação `Branco + 25` não existe. Três
listas independentes deixariam o cliente montá-la.

**Primeira tentativa.** Desabilitar, em cada eixo, o que não casa com a seleção
dos outros. Correta na matemática e inutilizável na tela: abrindo em `Preto/25`,
o tamanho restringia a cor a Preto e Roxo **e** a cor restringia o tamanho a
25 cm. Como um `radio` não se desmarca, não havia clique que levasse a Branco. A
página travava na combinação com que abria. O navegador pegou isso; o teste de
unidade não teria pegado.

**Segunda tentativa.** Um "eixo livre": o eixo em que o cliente acabou de clicar
continuava inteiro e os outros se ajustavam a ele. Destravava, mas ao custo de
um estado extra e de uma assimetria difícil de explicar — a partir de
`Branco/30`, chegar a `Preto` exigia passar por `25 cm` primeiro, porque `Preto`
estava desabilitado.

Foi aí que ficou claro que o problema não era **qual** eixo restringir: era o
`disabled`.

## 79. O servidor é a autoridade sobre a combinação

O `AddToCartForm` passou a receber `option_color`, `option_size` e
`option_material` além do `variant_id`. Três caminhos, nesta ordem:

1. **veio `variant_id`** — a variante é essa, e os eixos que também tenham vindo
   têm que bater com ela;
2. **vieram só os eixos** — a combinação é resolvida no servidor e precisa
   apontar para **exatamente uma** variante ativa;
3. **não veio nada** — só é aceitável quando não há o que escolher.

O caso 1 é o que fecha o buraco de verdade. Um POST dizendo "variante 7" e "cor
azul", quando a 7 é preta, mente em um dos dois campos — e não há como saber
qual. Vender a do `variant_id` entregaria uma cor que o cliente não escolheu;
vender a dos eixos ignoraria o campo que a página de fato usa. Não existe
escolha honesta: recusa.

## 80. Sem JavaScript, o `<select>` é a interface inteira

Antes, o `<select>` e os botões apareciam juntos e o JavaScript escondia o
primeiro. Sem JavaScript o cliente via dois controles para a mesma escolha, e um
POST podia trazer os dois discordando.

Agora o padrão do HTML é o `<select>` sozinho, e os botões nascem `hidden`. É o
JavaScript que os revela e passa o `<select>` para `sr-only` — onde ele continua
sendo quem carrega o `variant_id` e quem responde ao teclado e ao leitor de
tela.

O ganho não é só de organização: **cada `<option>` do `<select>` é uma variante
que existe**. O caminho sem JavaScript não tem como montar uma combinação
inexistente, porque não monta combinação nenhuma — escolhe uma linha do banco.

**Eixo preenchido pela metade não vira botão.** Com duas variantes coloridas e
uma sem cor, os botões de cor não teriam botão para a terceira, e o `<select>`
que a alcançaria está escondido: ela existiria no catálogo e nenhum clique a
venderia. Um eixo só vira botões quando varia **e** está preenchido em todas as
variantes ativas.

## 81. O cadastro de variantes é uma tabela, não vinte formulários

**Decisão.** O inline de variantes passou a ter template próprio: uma tabela de
resumo (SKU, cor, tamanho, material, custo, venda, margem, estoque, peso,
produção, status) e um painel de edição por variante, aberto um de cada vez.

**Por quê.** Vinte variantes eram vinte formulários abertos na mesma página.
Encontrar a que tinha o preço errado era rolar a tela.

**O que NÃO mudou.** Continua sendo o formset padrão do Django. Os campos do
painel **são** os campos do inline; gravar o produto grava as variantes na mesma
transação, com a mesma validação. Sem endpoint próprio, sem gravação parcial,
sem uma segunda fonte de verdade sobre o que foi salvo. Um "salvar variante"
por AJAX teria criado justamente isso: um estado em que a variante está no banco
e o produto não.

As células do resumo nascem vazias e são preenchidas pelo JavaScript a partir
dos próprios campos. Duplicá-las no template criaria duas fontes para o mesmo
número, e a de baixo (o campo) é a que vai para o banco.

**Preço a pagar, explícito.** Sem JavaScript a tabela fica vazia. Os painéis
continuam no HTML e editáveis — nenhum campo deixa de existir por causa do
script —, mas o resumo não aparece. É Admin, não vitrine: aceitável.

`autocomplete_fields` saiu de cor e material: o select2 do Django não sobrevive
a ser clonado para uma variante nova sem reinicialização, e a lista de cores e
materiais de uma gráfica 3D cabe num `<select>` comum.

## 82. Custo primeiro, depois margem **ou** preço

**Decisão.** A ordem dos campos no painel é identificação → eixos → **custo** →
preço/margem. O preço só faz sentido depois de saber quanto a peça custa.

**O anti-ciclo.** `pricing_mode` é onde o administrador declara qual dos dois ele
está digitando, e só o **outro** é reescrito. Não existe margem → preço → margem.
No formulário, digitar num dos dois campos muda o `pricing_mode` sozinho: quem
digitou o preço declarou que o preço é a entrada. O modo é gravado, então o
servidor concorda com a tela.

**Margem, não markup.** `preço = custo / (1 - margem/100)`. Custo 5,00 a 50% dá
10,00, não 7,50. O JavaScript repete a fórmula de `apps/catalog/pricing.py` para
não ter duas definições de margem no projeto.

**O número da tela é pré-visualização.** O valor que vale é o que
`ProductVariant.save()` recalcula com `Decimal`. O JavaScript arredonda em
centavos inteiros (`Math.round(x * 100)`) para não herdar o erro do ponto
flutuante, mas continua sendo ponto flutuante — pode diferir no último centavo,
e é por isso que o painel diz "Recalculado no servidor ao salvar" em vez de
fingir que o número é definitivo.

## 83. Cor traduzível: quem tem idioma é a cor, não a variante

**Decisão.** `ColorTranslation`, pelo mesmo mecanismo de `ProductTranslation` e
`CategoryTranslation`. `Color.name` vira o nome **interno** (Admin, busca,
slug); `Color.display_name` é o que o cliente lê.

**Por quê não duplicar a variante.** Uma variante por idioma multiplicaria SKU,
estoque e preço por quatro — e o estoque de "Preto" e o de "Noir" seriam a mesma
peça em duas linhas. A cor é um atributo compartilhado; é ela que se traduz.

**Fallback.** O da casa: idioma pedido → português → qualquer tradução → nome
interno. Melhor "Preto" num seletor francês do que um espaço em branco.

**No snapshot do pedido**, `color_name` é copiado no idioma do cliente, como já
era o `product_name`. O pedido guarda o que ele leu, não o nome interno do
Admin.

**Consulta.** `color__translations` entrou no prefetch da vitrine, da página do
produto, do carrinho e da home. Sem isso, cada variante custaria uma consulta a
mais — a tradução é lida por variante.

## 84. O que ainda não é traduzível: `Material`

`Material.name` continua sendo texto único. "PLA" e "PETG" são nomes próprios e
não incomodam, mas "Resina" e "Madeira" aparecem em português numa loja
francesa. O mecanismo agora existe e a mudança é a mesma da cor —
`MaterialTranslation`, `display_name`, prefetch e migration de dados. Ficou de
fora por não ter sido pedido nesta etapa; está registrado para não se perder.


---

# Decisões de arquitetura — Etapa 10 (preparação operacional)

## 85. Idempotência **por etapa**, não por pedido (AUD-01)

**O defeito.** `confirm_payment` começava com um curto-circuito:

```python
if already_paid:
    return False
```

Ele protegia o **pedido**, não as etapas. Bastava a gravação do pagamento
passar e a baixa de estoque estourar logo depois: a reentrega do webhook via o
pedido já pago, voltava na hora, e estoque e e-mails nunca aconteciam. O pedido
ficava pago, sem baixa e sem ninguém avisado — e nada no sistema tentaria de
novo.

**A correção.** A função agora percorre as três etapas **sempre**, e cada uma
decide sozinha se já foi feita:

```text
pagamento          ->  payment_status / paid_at        (select_for_update)
    estoque        ->  stock_applied_at
        e-mails    ->  confirmation_email_sent_at, admin_email_sent_at
```

Repetir a chamada é seguro; interrompê-la no meio deixa o resto para a próxima.

**O campo que faltava.** `admin_email_sent_at` é novo. O e-mail do cliente já
tinha marca; o da produção não — uma reentrega mandaria a mesma ordem de novo, e
alguém imprimiria duas.

**O que continua valendo.** A view do webhook apaga o `WebhookEvent` quando o
processamento estoura, para a reentrega da Stripe poder reprocessar. Sem isso, a
correção acima não teria como acontecer: o evento seria descartado como
duplicado.

## 86. Reenviar e-mail é ação do administrador, nunca automática (AUD-02)

O envio pode falhar sem derrubar nada — é o certo: provedor fora do ar não pode
desfazer um pagamento. O efeito colateral é um pedido `paid` com
`confirmation_email_sent_at` vazio, e antes desta etapa a única saída era mexer
no banco à mão.

Agora há três ações separadas no Admin — confirmação ao cliente, ordem de
produção, aviso de envio. **Separadas de propósito:** um botão só "reenviar
tudo" mandaria de novo o que o cliente já recebeu.

O reenvio usa `force=True` e por isso **nunca** é chamado por caminho
automático: reenviar sozinho mandaria a mesma confirmação a cada reentrega do
webhook. E não toca em pagamento, estoque, situação, valores ou snapshot —
reenviar um e-mail é reenviar um e-mail. Fica registrado no histórico com autor
e resultado.

## 87. A tela do pedido responde "quem já foi avisado?"

Três linhas, com data, no topo do pedido. E a distinção que importa:

| | |
|---|---|
| ✓ enviado em … | saiu |
| ⚠ NÃO enviado | já deveria ter saído e não saiu |
| — ainda não enviado | ainda não era hora |

"Ainda não enviado" e "não enviado" são coisas diferentes. O aviso de envio de
um pedido que não saiu ainda está **certo** em não ter ido; marcá-lo de vermelho
ensinaria a ignorar o vermelho.

Na listagem, a coluna mostra só o que está pendente e deveria ter saído.

## 88. Mudar a produção deixa rastro, e o absurdo é recusado (AUD-04)

Mexer no campo pela tela do Admin gravava e pronto: o histórico não sabia que a
produção tinha andado nem quem tinha mexido.

Agora toda mudança passa por `change_fulfillment_status`, que registra autor,
estado anterior e novo. E há um freio: **voltar um passo é acerto legítimo**
(marquei "enviado" cedo demais); voltar de "Entregue" para "Não iniciado" não é
correção nenhuma — é a lista de opções clicada errado. Regressões de mais de um
passo são recusadas com uma mensagem que diz o que fazer.

Avançar continua livre, inclusive pulando etapas: pedido pequeno sai no mesmo
dia.

## 89. País ativo sem tarifa aparece no Admin (AUD-05)

Ativar um país é um clique; cadastrar a tabela de preços dele é outra tarefa,
noutra tela. Entre as duas cabe um país que aparece no cadastro de endereço,
aceita o cliente até o checkout e lá diz que não há entrega — e a loja não fica
sabendo.

O Admin de países agora avisa no topo da lista e marca cada linha. Dois casos:

* **nenhuma tarifa** — nada é vendável para lá;
* **teto de peso** — a tabela vai até X g e um pedido mais pesado fica sem opção.

Tarifa de método desligado ou de transportadora desligada **não conta**: ela
existe no banco e não existe para o cliente.

O aviso não bloqueia. Cadastrar o país antes da tarifa é ordem de trabalho
legítima; o que não pode é passar despercebido. Na primeira execução ele já
encontrou quatro países nessa situação no banco de desenvolvimento.

## 90. `EMAIL_BACKEND` existia com o nome errado

`settings.py` tinha:

```python
MAILERS = {"default": {"BACKEND": env("DJANGO_EMAIL_BACKEND", ...)}}
```

`MAILERS` **não é um setting do Django**. Nenhum código lia esse dicionário, e o
nome certo é `EMAIL_BACKEND`. Na prática, a variável `DJANGO_EMAIL_BACKEND` do
`.env` não fazia nada e o Django usava o backend SMTP padrão, sem host
configurado. Nos testes não aparecia, porque o runner troca o backend por
`locmem` sozinho.

## 91. Configuração de e-mail: Admin **ou** `.env`, com uma regra só

```text
EmailSettings (Admin), ativa e com servidor preenchido
    ↓  se não houver
variáveis do .env
```

A escolha acontece **a cada envio**, num backend próprio
(`apps.core.mailer.ConfiguredEmailBackend`) — por isso todo envio do projeto
segue a mesma regra sem cada módulo ter de lembrar de pedir a conexão certa. E
mudar a configuração no Admin vale no próximo e-mail, sem reiniciar.

Nunca as duas ao mesmo tempo, nunca metade de cada: uma configuração ativa **sem
servidor** não é usável e cai no `.env` — meia configuração não pode derrubar o
envio da loja.

O teste de envio do Admin usa **a mesma conexão** dos e-mails de pedido. Um
teste que usasse outra não provaria nada.

## 92. A senha do SMTP é cifrada no banco

Ela não é uma senha de login: não pode virar hash, porque o servidor de e-mail
precisa dela inteira. Guardá-la em texto puro significaria que **todo backup do
banco vira um vazamento de credencial** — e o procedimento de backup manda
copiar o dump para fora do servidor.

Então ela é cifrada (Fernet) com uma chave derivada do `DJANGO_SECRET_KEY`, que
vive no `.env` e **não** no banco. Quem tiver só o dump não tem a chave; quem
tiver só o `.env` não tem o dado.

**O que isto não protege:** quem tiver o servidor inteiro lê a senha, como leria
o `.env`. Não existe forma de guardar credencial reutilizável que resista a
isso; o que existe é reduzir a superfície.

**Consequência assumida:** trocar a `DJANGO_SECRET_KEY` invalida a senha
gravada. Ela volta como vazia e é digitada de novo. Melhor um campo
visivelmente vazio do que um valor silenciosamente errado.

Na tela, o campo é sempre desenhado **vazio**; deixá-lo vazio conserva o que
estava gravado. A senha nunca viaja de volta no HTML, onde ficaria no cache, no
histórico e em qualquer captura de tela.

## 93. Erro de SMTP na tela: apagar por valor, não por padrão

A primeira versão de `sanitize_error` procurava formatos (`password=`,
`AUTH ...`, base64 longo). Adivinhar formato é frágil: basta um servidor
devolver a senha solta no meio de uma frase e ela passa — e foi exatamente isso
que o teste pegou.

Como **conhecemos** a senha e o usuário em uso, eles são apagados por valor
primeiro; os padrões ficam como rede para o que vier de outra configuração.

## 94. `500.html` não herda de `base.html`

O 500 é renderizado quando algo já deu errado, e o `base.html` deste projeto
consulta o banco (categorias do menu, idiomas da loja, contador do carrinho). Se
o que quebrou foi justamente o banco, herdar do base faria a página de erro
estourar dentro do tratamento do erro — e o visitante veria a tela crua do
Django.

Por isso o 500 é autossuficiente: HTML próprio, o mesmo `tailwind.css` (que é
estático) e nenhuma tag que toque o banco. Um teste garante que ela não dispara
consulta nenhuma.

A 404, sim, herda do base: quem caiu num link velho continua dentro da loja, com
menu e rodapé.

## 95. `format_html` sem argumento — e com especificador de data

Dois defeitos da mesma família, encontrados nesta etapa:

```python
format_html('<strong>solicitado</strong>')          # TypeError: args or kwargs
format_html('{:%d/%m/%Y}', obj.last_test_at)        # ValueError na SafeString
```

O primeiro derrubava a listagem de pedidos justamente quando havia um
cancelamento para decidir. O segundo derrubava a tela de configuração de e-mail
assim que existisse um teste registrado — e foi o navegador que pegou, porque
nenhum teste tinha `last_test_at` preenchido.

`format_html` escapa cada argumento **antes** de chamar `str.format`, então o
`str.format` recebe uma `SafeString`, para a qual `{:%d/%m/%Y}` não é um
especificador válido. Datas são formatadas antes de entrar.

## 96. O log existe porque `DEBUG=False` não tem página de erro

Sem log, uma falha de e-mail, de webhook ou de pagamento desaparece. Os logs da
loja vão para `logs/jdprint.log` (rotacionado), fora do Git — log de aplicação
carrega e-mail e IP.

O handler de arquivo usa `delay=True`: o arquivo só é aberto no primeiro
registro. Sem isso, um diretório sem permissão de escrita derrubaria a
inicialização inteira do Django em vez de só falhar ao logar.

## 97. Auditoria de segredos que roda sozinha

A varredura manual não achou nada. Para ela não precisar ser refeita à mão, o
que ela procura virou teste:

* **chave em formato real** (`sk_live_`, `whsec_`, PEM, AWS) em qualquer arquivo
  versionado — inclusive teste: uma chave de teste também é uma chave;
* **arquivo que nunca pode ser versionado** (`.env`, banco, log, `media/`);
* **`.env.example` sem valor** onde deveria ter só o nome, e **com** todas as
  variáveis que o código lê.

O que o teste **não** procura é a palavra "password": ela aparece 294 vezes no
projeto, quase sempre como nome de campo ou fixture. Alarme que dispara sempre
acaba ignorado.

## 98. O modal grava; a tabela só resume

**O que estava errado.** A etapa 9 pôs os campos num painel **abaixo** da
tabela. Funcionava, mas abrir uma variante empurrava a página para baixo: com
vinte variantes era preciso rolar para achar o campo, e a tela ficava comprida
mesmo com tudo fechado.

**Agora** os campos vivem num modal `position: fixed`, sobre a página. A tabela
mostra só o que identifica e compara: SKU, cor, tamanho, material, peso,
estoque, preço, produção e situação.

## 99. Onde o "Salvar" do modal grava — e onde não pode gravar

Um botão "Salvar" que não salva é uma mentira de rótulo. Então ele grava: na
tela de **edição**, o modal manda a variante para
`admin:catalog_product_variant_save`, que valida com o mesmo
`ProductVariant.clean()` de sempre, recalcula preço e margem com `Decimal` e
devolve **o que gravou** — é esse valor que volta para os campos e para a linha,
não o que foi digitado.

Na tela de **cadastro** isso é impossível: o produto ainda não tem PK e não há a
que prender uma variante. Lá o modal continua alimentando o formset, e a tela
diz isso em uma linha. É a única diferença de comportamento, e ela está escrita
onde o administrador a encontra — não escondida atrás de um botão que se
comporta de dois jeitos sem avisar.

## 100. Criar e excluir recarregam a página; editar não

Não é preguiça. O formset do Django numera os formulários e conta quantos vieram
do banco (`INITIAL_FORMS`). Uma variante criada por fora dele passaria a existir
duas vezes — uma no banco, outra como formulário "novo" — e o próximo "Salvar"
do produto tentaria inseri-la de novo, batendo no SKU único. Escrever o PK de
volta no formulário não resolve: para um formulário *extra*, o Django ignora
esse PK e insere assim mesmo.

Editar não tem esse problema: o formulário já é o da variante certa, e depois da
gravação os valores no DOM são exatamente os que o servidor gravou. Reenviá-los
no "Salvar" do produto é um no-op. Por isso a edição — o caso comum — não
recarrega, e a criação e a exclusão — raras e estruturais — recarregam.

Há teste para as três combinações em `test_variant_modal.py`
(`ModalAndProductSaveTogetherTests`).

## 101. Cancelar precisa desfazer de verdade

Fechar o modal não bastava: os valores digitados ficariam no DOM e seriam
gravados no próximo "Salvar" do produto — um cancelamento que não cancela. O
modal tira uma fotografia dos campos ao abrir e a repõe ao cancelar.

## 102. Excluir pelo modal recusa a última variante ativa

A mesma regra que o formset já cobra ao gravar o produto (etapa 8): produto
ativo sem variante ativa não tem preço, peso nem estoque, e vira uma página que
não vende. Recusar na exclusão evita chegar lá com o produto no ar.

A caixa `DELETE` do formset continua existindo — é o que faz a exclusão
funcionar sem JavaScript.

## 103. A opção que não combina fica riscada — e clicável

**A regra.** O eixo em que o cliente **acabou de clicar tem prioridade**. O
sistema procura, entre as variantes reais, a que melhor atende essa escolha e
passa a seleção inteira para ela.

```text
Preto + 25 cm, clica em "30 cm"
    →  só existe Branco + 30 cm
    →  a seleção vira Branco + 30 cm

Branco + 30 cm, clica em "Preto"
    →  só existe Preto + 25 cm
    →  a seleção vira Preto + 25 cm
```

**Por que isso resolve o que as duas tentativas anteriores não resolviam.** O
impasse e a assimetria vinham ambos do `disabled`: uma opção desligada é um beco
sem saída, e a única forma de sair dele era abrir uma exceção para algum eixo.
Sem `disabled`, nada trava — e a exceção deixa de ser necessária. O código ficou
menor: saíram `freeAxis`, `reconcile()` e `reachable()`.

**Riscada não é desabilitada.** Desabilitar diz "esta opção não existe"; o que é
verdade é "não existe *nesta cor*". Riscada, mais fraca e clicável diz isso — e
o clique resolve. Para leitor de tela, `aria-disabled="true"` anuncia a
indisponibilidade sem tirar o controle da ordem de foco, que é justamente a
diferença entre `aria-disabled` e `disabled`.

**Critério de escolha**, quando várias variantes têm o valor clicado:

1. tem que ter o valor clicado no eixo clicado — é a prioridade;
2. concorda com o maior número possível dos **outros** eixos;
3. no empate, a que dá para comprar;
4. persistindo, a ordem do catálogo.

O peso de (2) domina (3) de propósito: preservar a escolha do cliente importa
mais do que oferecer algo em estoque que ele não pediu.

**Nunca um estado inválido, nem por um instante.** O clique não "marca 30 cm e
depois conserta": ele calcula a variante final e marca todos os eixos de uma
vez. `Preto + 30` não chega a existir.

**Genérico por construção.** Nada no código sabe o que é cor ou tamanho — os
eixos vêm do DOM e as combinações, do catálogo. Com três eixos, um clique pode
ajustar os outros dois de uma vez, e o teste de navegador cobre exatamente isso.

**A autoridade continua no servidor.** Nada disto mudou em `AddToCartForm`: um
POST forjado com `Branco + 25 cm` continua sendo recusado. O JavaScript é
conforto; a combinação é conferida de novo na hora de entrar no carrinho.

## 104. A foto da variante vive na mídia, não na variante

A associação foto ↔ variante é uma **coluna anulável na foto**
(`ProductMedia.variant`), e não uma coluna na variante.

**Por que desse lado.** Toda foto já cadastrada vira "foto geral" sem uma linha
de migração de dados: `NULL` já quer dizer isso. Nenhum arquivo é copiado — a
mesma linha, o mesmo arquivo, agora com um dono opcional. E uma variante pode
ter várias fotos no futuro sem mexer no esquema.

`on_delete=SET_NULL`: apagar a variante desfaz o vínculo e **mantém** a foto.
Com `CASCADE`, remover uma variante apagaria o arquivo — perda de trabalho por
um efeito colateral que ninguém pediu.

**A regra que o banco não tem.** A variante tem que ser **deste** produto. Isso
não cabe numa `constraint` (exigiria comparar duas tabelas), então mora em
`ProductMedia.clean()`. No Admin, o `<select>` já só oferece as variantes do
produto aberto e os quatro atalhos de popup do Django foram retirados da coluna
— eles alcançariam a variante de qualquer produto.

**Como a loja usa.** Cada variante entrega `mediaUrl`/`mediaAlt` no
`variant_payload`. Vazio quer dizer "use as fotos gerais", e é o caso normal:
`window.jdGallery.showVariantMedia("")` volta para a foto de abertura. Não fica
a foto da variante anterior na tela, que seria a forma mais silenciosa de
mostrar o produto errado.

## 105. `object-contain`: a foto do produto não pode ser cortada

A moldura continua quadrada — é ela que impede a grade de pular de altura
quando as fotos têm proporções diferentes. Quem se ajusta é a imagem.

`object-cover` preenche a moldura cortando o que sobra; numa loja de peças
impressas isso esconde justamente o que se está vendendo. Uma foto 1200×400
apareceria como uma faixa central de 400×400.

A lupa (`.jd-lightbox`) é `position: fixed` e limita a imagem à viewport
(`max-width`/`max-height` em `vw`/`vh`): uma foto de 4000 px não empurra a
página nem sai da tela. Fecha no X, no fundo e no ESC; as setas do teclado
andam entre as fotos.

## 106. Sugestões: uma regra simples, e assumidamente simples

Três passadas, sem repetir: **mesma categoria**, depois **destaques**, depois
**os mais recentes**. `sellable()` em todas — produto sem variante ativa não
tem preço, e um card sem preço não convida ninguém a clicar. O produto da
página nunca aparece.

Recomendação de verdade — "quem viu isto viu aquilo" — depende de dados de
navegação que a loja ainda não coleta. Inventar um critério agora só faria
parecer inteligente. A seção reaproveita `components/product_card.html`
inteiro: um card próprio aqui seria um segundo lugar para corrigir quando o
card mudar.

## 107. A ordem das seções do produto mora em `SECTION_ORDER`

O Django desenha **todos** os fieldsets e só depois **todos** os inlines. A
ordem pedida intercala os dois: CONTEÚDO, que é inline, fica entre CLASSIFICAÇÃO
e PERSONALIZAÇÃO, que são fieldsets.

`ProductAdmin._page_layout()` monta uma lista única a partir de
`SECTION_ORDER` — fieldsets pelo nome, inlines pelo modelo — e o
`change_form.html` do catálogo caminha por ela numa volta só, no bloco
`field_sets`. O bloco `inline_field_sets` fica vazio, senão cada formset seria
desenhado duas vezes.

Uma seção fora de `SECTION_ORDER` **não some**: entra no fim, na ordem em que o
Django a entregou. A lista diz a ordem, não a permissão de existir.

A AUDITORIA continua fora dela, no bloco `after_related_objects`: é sempre a
última, e só existe na edição.

## 108. Recolher é `<details>`, e sete das oito começam abertas

O `collapse` do Django 6.1 já rende `<details>`/`<summary>` nativos — teclado,
leitor de tela e "buscar na página" funcionam de graça, sem JavaScript. Só
faltava uma coisa: ele começa **fechado**, e quem abre um produto quer editá-lo,
não caçar onde clicar.

`templates/admin/includes/fieldset.html` é a cópia do template do Django com
**uma** linha diferente: `<details {% if "start-open" in fieldset.classes %}open{% endif %}>`.
Sendo opt-in, os `collapse` que já existiam noutras telas continuam como
estavam. Só a AUDITORIA, que é histórico, nasce fechada.

`is_collapsible` do Django já devolve `False` quando o bloco tem erro — a seção
com o campo recusado abre sozinha.

## 109. CONTEÚDO usa o mesmo modal das VARIANTES, não um parecido

`static/admin/js/jd_modal.js` é a casca: abrir, fechar, ESC, prisão do Tab,
trava de rolagem, fotografia dos campos para o Cancelar desfazer, e pintura dos
erros por campo. `variant_admin.js` e `content_admin.js` compõem essa casca e
guardam só o domínio — preço e margem de um lado, idioma do outro.

Um ouvinte de teclado para a página inteira, e não um por modal: com vinte
modais no DOM seriam vinte ouvintes fazendo a mesma pergunta, e duas respostas
para o mesmo ESC.

**Onde o "Salvar" grava** segue a decisão 99: na edição, grava o idioma sozinho
por `admin:catalog_product_content_save`; no cadastro não há PK a que prender
nada, o modal alimenta o formset e a gravação vem com o produto — e a tela diz
isso. Criar e remover recarregam a página pelo motivo da decisão 100.

**Nem `js-inline-admin-formset` nem `data-inline-formset`** nas duas tabelas:
são as marcas por onde o `inlines.js` do Django entra e desenha o próprio
"Adicionar outro(a)…", que monta a linha do jeito antigo — dois botões de
adicionar, um deles ignorando o modal. A MÍDIA mantém as duas, porque lá o
mecanismo do Django **é** a interface.

**A tarja de erro vazia.** O CSS do Admin declara `.errornote { display: block }`,
e uma classe vence o `[hidden]` do navegador: todo modal abria com uma tarja
vermelha vazia no topo — um erro que não existia. `.jd-modal .errornote[hidden]`
resolve, e vale para os dois modais porque a caixa é a mesma.

## 110. A ordem das seções mudou; `SECTION_ORDER` não

A ordem passou a ser IDENTIFICAÇÃO → CLASSIFICAÇÃO → PERSONALIZAÇÃO →
CONTEÚDO → VARIANTES → MÍDIA → AUDITORIA.

Foram duas linhas trocadas em `SECTION_ORDER` — nada em `fieldsets`, nada no
template, nada em `_page_layout`. Era exatamente para isto que a lista existia
(decisão 107).

Hoje essa ordem **coincide** com a natural do Django (os três fieldsets, depois
os três inlines). A máquina continua onde está mesmo assim: na etapa 13 não
coincidia, e a próxima mudança de ordem pode voltar a não coincidir. Apagá-la
agora só para reescrevê-la depois seria trabalho em dobro, e recolocaria a
ordem em dois lugares que precisam concordar.

**CONFIGURAÇÕES deixou de existir.** Tinha dois campos, e os dois são a mesma
funcionalidade: se o produto está em destaque (`is_featured`) e em que posição
(`featured_order`). Os dois foram para CLASSIFICAÇÃO. Mover só o primeiro
deixaria a ordem do destaque sem tela para editar — e é ela que ordena a
prateleira da home e a primeira passada das sugestões.

## 111. Clicar fora não fecha modal nenhum

O fundo escuro fechava o modal e descartava o que estava digitado. Num campo de
descrição de produto, um clique fora do diálogo custava parágrafos — e nada na
tela avisava que ia acontecer.

Sair do modal passou a ser uma decisão, com três portas: **Cancelar**, o **X**
e o **ESC**. As três continuam desfazendo o que foi digitado, que é o
comportamento validado desde a decisão 101. A diferença que isto cria é entre
"não quero" (Cancelar) e "cliquei errado" (fora) — a segunda não é mais uma
forma de perder trabalho.

A regra vive na casca (`jd_modal.js`), então vale para VARIANTES e CONTEÚDO ao
mesmo tempo. Um segundo mecanismo só para o conteúdo seria mais um lugar onde a
regra pode se perder.

**A guarda é no `.jd-modal` inteiro, não no `.jd-modal-backdrop`.** O fundo
escuro cobre a área toda hoje, mas basta um `padding` novo para aparecer uma
faixa que não é nem fundo nem diálogo. Perguntar "o clique veio de dentro do
diálogo?" não depende da geometria do CSS:

```js
element.addEventListener("click", function (evento) {
  if (self.dialog && self.dialog.contains(evento.target)) { return; }
  evento.preventDefault();
  evento.stopPropagation();
});
```

O ouvinte não fecha nada — ele **engole** o clique. Sem ele, o clique chegaria
ao formulário do produto atrás, que o `aria-modal` diz não estar lá.

Os ganchos `data-content-backdrop` e `data-variant-backdrop` saíram do HTML:
ninguém os lê mais, e gancho morto engana quem for mexer nisto depois.

## 112. Busca e categoria são a vitrine, com outro recorte

`ShopView` já sabia filtrar por árvore, ordenar por nome traduzido e por preço,
contar, paginar, trocar por HTMX e desenhar o card. O que faltava era um lugar
para entrar com **outro** critério que não fosse a categoria.

A consulta virou duas partes:

* `base_queryset()` — produtos vendáveis, anotados. Não sabe de categoria;
* `get_queryset()` — o recorte desta tela, por cima da base.

E as três telas passaram a ser a mesma view:

| Tela | Recorte |
|---|---|
| `/modelos/` | a árvore de Modelos |
| `/categorias/<slug>/` | a árvore daquela categoria |
| `/buscar/?q=` | o texto |

Escrever três consultas para "o que o cliente pode ver" seria três lugares para
corrigir quando a regra mudar. Um produto rascunho, ou sem variante ativa, some
das três de uma vez porque a regra é uma.

**A `ComingSoonView` foi apagada.** Ela existia só para a categoria sem
vitrine — Filamentos e Acessórios tinham produtos cadastrados e mostravam um
aviso de "em construção" porque só a árvore de Modelos tinha rota. A vitrine
nunca dependeu de qual árvore era; agora é de quem a pedir.

**As URLs não mudaram.** `Category.get_absolute_url()` continua mandando as
categorias da árvore de Modelos para `/modelos/?categoria=x` — duas URLs
mostrando a mesma grade seriam duas páginas para o buscador indexar e uma para
o cliente entender. `/categorias/gatos/` segue respondendo 301 para lá.

## 113. O que a busca procura, e em que idioma

Por palavra, em: nome, descrição curta, descrição, SKU do produto, SKU da
variante, nome da categoria e nome do material. Várias palavras funcionam como
"e", mas cada uma pode casar num campo diferente — "gato preto" aceita o nome
com "gato" e a variante com "preto". Por isso é um `.filter()` por palavra e
não um só: num `.filter()` único, a **mesma** tradução teria de conter as duas.

**Procura em todos os idiomas; mostra no idioma do cliente.** São duas coisas
diferentes, e misturá-las foi o primeiro desenho: a busca olhava só o idioma da
tela mais o português, e o mesmo produto **existia ou não conforme a
bandeirinha escolhida** — quem estava em neerlandês não achava "Dinosaure",
quem estava em francês não achava "Dinosaurus". Numa loja belga isso é errado:
o cliente lê o rótulo em francês, ouve falar do produto em neerlandês e vê o
nome em inglês numa rede social.

Não custa uma consulta por idioma — é o mesmo JOIN em `translations`, sem a
condição de idioma. O que ele pode repetir (uma linha por tradução que casou)
o `distinct()` já resolvia.

Achar por "Dinosaurus" não muda nada na tela: quem escreve o card é
`display_name`, e ele não sabe por qual tradução o produto foi encontrado.

**Sem SQL cru e sem `SearchVector`:** `icontains` no ORM. É o que este catálogo
pede; a busca por similaridade do PostgreSQL entra quando o número de produtos
justificar, e será uma troca dentro de `SearchView.search_filter()` — o único
lugar que sabe onde uma palavra pode aparecer.

**Dois limites que não são detalhe.** `MIN_SEARCH_LENGTH = 2`: uma letra
devolveria meio catálogo e custaria a varredura inteira para não ajudar
ninguém. `MAX_SEARCH_TERMS = 6`: cada palavra vira um `.filter()` com uma dezena
de JOINs, e uma URL com duzentas palavras seria um jeito barato de derrubar o
banco.

## 114. O filtro de material tem que casar na MESMA variante

```python
condition = Q(variants__is_active=True)
if self.selected_material is not None:
    condition &= Q(variants__material_id=self.selected_material.pk)
return condition
```

Numa `Q` só, e não em dois `.filter()`. Separadas, o Django faria dois JOINs e
um produto com uma variante **ativa de PLA** mais uma variante **desativada de
PETG** apareceria no filtro "PETG" — vendendo o que não está à venda.

**A lista só mostra material que tem produto:** um filtro que leva a zero
resultado é um beco sem saída. E ela é contada **sem** o material já escolhido —
senão, escolher PLA deixaria a lista com uma linha só e o cliente não teria como
trocar para PETG sem voltar.

**Com menos de dois materiais, `available_materials()` devolve `[]`.** Quem
decide "há o que filtrar?" é a view, e só ela: enquanto a view dizia "há 1
material" e o template dizia "só mostro com 2 ou mais", o que sobrava na tela
era um cartão branco vazio.

## 115. A grade da vitrine é declarada num lugar só

`shop.html` e `search.html` tinham, cada um, a sua cópia escrita à mão de
`mt-7 grid gap-6 lg:grid-cols-[254px_1fr] lg:gap-7`. Duas cópias que precisavam
concordar — e foi entre elas que a busca divergiu do catálogo sem ninguém
notar. Agora são o utilitário `.shop-layout`.

**O defeito que isso escondia.** Na busca sem filtro de material, a coluna
lateral vazia levava `hidden`. `display: none` **tira o item da grade**:
sobrava um item, e `#shop-results` ia parar na primeira coluna, a de 254 px,
com os quatro cards espremidos dentro dela.

Medido a 1440 px, antes:

```
catálogo   #shop-results  x=394  w=934   card=219px
busca      #shop-results  x=112  w=254   card=49px
```

Depois, as três páginas dão a mesma coisa nas quatro larguras.

**A coluna lateral vazia continua sendo um item no desktop** (`hidden lg:block`,
e não `hidden`). Não é só para o card medir o que mede no catálogo: é também
para ele não mudar de tamanho entre uma busca e outra, conforme o filtro de
material aparece ou some. No celular a grade é de uma coluna só, e aí a lateral
vazia sai mesmo — senão deixaria um vão de 24 px entre o campo e os resultados.

**Mas a grade só existe quando alguma coluna tem função.** A regra completa:

| Situação | Grade | Por quê |
|---|---|---|
| há cards | duas colunas | a lateral segura o alinhamento com o catálogo |
| sem cards, com filtro de material | duas colunas | o filtro tem que continuar alcançável |
| sem cards e sem filtro | uma coluna | não há nada a alinhar |

Faltava a terceira linha, e o efeito era visível: na busca sem resultado a
lateral vazia virava 254 px de vão morto que empurravam a barra e o estado
vazio para x=394, enquanto o título e o campo ficavam em x=112. A página
parecia deslocada porque estava.

Nesse caso o bloco recebe a medida do `<header>` (`max-w-2xl`): título,
subtítulo, barra e estado vazio passam a compartilhar uma coluna de leitura, e
o estado vazio deixa de ser uma faixa tracejada de 1216 px em volta de quatro
linhas de texto. A medida não foi inventada — é a que o cabeçalho logo acima já
usava.

**Nenhum `min-height` em lugar nenhum.** O rodapé sobe numa página de pouco
conteúdo, e é assim mesmo: empurrá-lo com altura fixa esconderia o sintoma em
vez de resolver a causa. O `.empty-state` também não foi tocado — a caixa da
busca é o mesmo componente do catálogo, com o mesmo `padding` e sem altura
mínima; a diferença de 28 px a 390 px vem de uma linha de texto a mais, não de
inflação de layout.

## 116. Conteúdo da loja: `storefront` para toda página, `home` para a Home

A faixa do topo e o rodapé estavam escritos no HTML — mudar "Envio para toda a
Europa" exigia editar um template e recompilar as traduções. Viraram cadastro,
com o mesmo `TranslationBase` de `ProductTranslation` e companhia: uma linha por
idioma, e acrescentar um idioma é inserir linhas, não alterar o schema.

**Dois apps, por um critério só — onde o conteúdo aparece:**

| App | O quê | Onde aparece |
|---|---|---|
| `storefront` | faixa do topo, rodapé (textos, contato, colunas, links) | toda página |
| `home` | banner, seções, cards, chamada final | só a Home |

`core` não serviu: lá mora infraestrutura (idiomas, e-mail, países de entrega),
e texto de vitrine não é infraestrutura. `home` também não: o rodapé aparece no
carrinho e no checkout.

**O que deliberadamente não virou model:**

* **Nome público de "Modelos"** — a vitrine é montada sobre a *categoria*
  `modelos`, que já tem nome e descrição traduzíveis e já é editável. `ShopView`
  passou a lê-los; um segundo lugar para o mesmo texto seria dois para
  corrigir. A URL não muda: quem manda nela é o slug.
* **Redes sociais** — o rodapé não tem nenhuma. Criar a tabela agora seria
  inventar estrutura para conteúdo que não existe.
* **Coluna de categorias do rodapé** — continua vindo do banco. Lista manual
  seria trocar algo que se atualiza sozinho por algo que alguém precisa lembrar
  de atualizar. Só o título dela é cadastrado.

**As promessas têm um dono só.** Elas apareciam duas vezes no HTML — na faixa do
topo e na lista do hero. `TopBarItem` alimenta as duas, e ganhou um `icon`
opcional porque a lista do hero tem ícone e a faixa não. Com duas cópias,
cadastrar uma e esquecer a outra era o jeito mais fácil de a loja se
contradizer.

## 117. "Ainda não configurei" e "não quero isto" são estados diferentes

Um `{% empty %}` simples trata os dois como um só — e o efeito é que desativar
o último card faz os três cards de fábrica voltarem, sem jeito de remover a
seção. A regra passou a ser:

```
nenhum registro          -> o padrão do template
registros, nenhum ativo  -> escondido
registros ativos         -> o que foi cadastrado
```

O padrão não é duplicação: é o conteúdo que a loja já tinha, e é o que faz uma
instalação recém-migrada abrir idêntica ao que era. Configurar passa a ser
opcional.

**"A linha existe" não prova que alguém configurou.** `HomeCallout.load()` cria
a linha, e o Admin a chama só para redirecionar para ela — usar a existência
como prova faria a chamada final sumir da Home porque alguém *olhou* a tela. O
que conta é ter conteúdo escrito, ou estar explicitamente desativada.

O rodapé é a exceção deliberada: lá `is_active=False` quer dizer "volte aos
textos padrão", porque rodapé escondido não é um estado que alguém queira.

**Saber os dois estados custa uma consulta, não duas.** `for_display()` mais um
`exists()` seriam duas idas à mesma tabela em toda visita. Uma consulta traz
tudo e o filtro de "ativo" acontece em memória — são punhados de registros.

## 118. Nada de caixa vazia, nunca

A regra que atravessa todos os blocos desta etapa: **campo em branco não vira
bloco vazio**. Título vazio não desenha `<h1>`; botão sem rótulo não vira
retângulo mudo; coluna sem link não aparece; contato sem e-mail nem telefone
não existe.

O caso que motivou a etapa: um banner que é só arte. Antes, com imagem
cadastrada, o hero sempre desenhava a faixa de gradiente e um `<h1>` — com o
título vazio isso virava uma tarja escura cobrindo o pé da imagem e um
cabeçalho invisível para o leitor de tela. A `CheckConstraint`
`home_banner_translation_title_not_empty` ainda **proibia** o caso no banco.

Agora `banner.has_text` decide, e o `<h1>` da página não some: quando o banner
não tem título, ele vem do `meta_title` em `sr-only`. Página sem `<h1>` é
página sem título para o buscador e para o leitor de tela.

**A cor do ícone dos cards precisa de `@source inline`.** `bg-{{ card.accent }}-100`
é montada em tempo de execução, e o Tailwind lê o HTML como texto: sem a
declaração, escolher "Ciano" no Admin daria um ícone sem fundo.

## 119. O favorito é do produto, e da conta

Não da variante e não da sessão. Duas decisões, cada uma fechando uma porta:

**Do produto, nunca da variante.** O cliente guarda "o vaso", não "o vaso preto
de 25 cm em PLA". Guardar a variante criaria três favoritos do mesmo produto,
uma lista que se repete, e a pergunta sem resposta boa de o que fazer quando
aquela variante saísse de linha. Escolher outra cor não muda o que foi
guardado.

**Da conta, nunca da sessão.** Um "favorito anônimo" viveria numa sessão que
expira, e o cliente perderia a lista sem entender por quê. O coração do
visitante é um **link para o login** — não um botão que falha nem um registro
que some. Depois de entrar, o clique funciona.

`UniqueConstraint(user, product)` no banco: dois cliques rápidos, um
duplo-clique ou um "reenviar" do navegador chegam ao mesmo estado. O
`get_or_create` + `delete()` é idempotente dos dois lados, e numa corrida quem
recusa a segunda linha é a tabela.

`CASCADE` nos dois lados: sem a conta ou sem o produto, a linha não significa
mais nada — e um favorito órfão seria um erro esperando a próxima listagem.

## 120. Fora do ar some da lista, não da conta

Produto desativado sai de `Favorite.objects.visible()`, não da tabela. Se ele
voltar, o cliente reencontra o que tinha guardado. Apagar seria decidir por ele
que perdeu o interesse.

**O contador conta o visível.** Um "3" no coração e dois produtos na tela é o
tipo de contradição que faz o cliente desconfiar da loja inteira. O mesmo
conjunto responde as duas perguntas — quais corações estão cheios e quantos
são — porque é o mesmo recorte.

## 121. O estado do coração custa uma consulta, não uma por card

O coração aparece em **cada** card. `Favorite.objects.filter(...)` dentro do
laço faria uma vitrine de 24 produtos custar 24 consultas.

`apps/accounts/context_processors.py` carrega uma vez o conjunto de ids
favoritados e visíveis; o card pergunta `{{ favorite_ids|has:product.pk }}`,
que é busca em memória. Uma consulta por requisição, qualquer que seja o número
de cards — e há teste que dobra a vitrine e exige a mesma contagem.

Visitante não paga nada: sai um conjunto vazio sem tocar no banco. E o conjunto
é montado **por requisição**, a partir de `request.user` — um cache global aqui
vazaria a lista de um cliente para o outro, e é o tipo de bug que só aparece em
produção, com dois usuários ao mesmo tempo.

O filtro `has` existe porque o `{% if x in y %}` do Django só funciona dentro de
um `if`: sem ele, o botão precisaria repetir a marcação nos dois ramos.

## 122. Quem decide de quem é o favorito é o servidor

`request.user`, e mais nada. O navegador manda `product_id` — diz **o que**,
nunca **de quem**. Não existe rota que aceite `user_id` nem o id do favorito,
então não existe o que forjar: um POST com `user_id` de outra conta grava na
conta de quem está autenticado, que é o comportamento correto e o que o teste
verifica.

Só produto `sellable()` pode ser favoritado — guardar um rascunho seria guardar
algo que o cliente não pode ver. E `product_id` é convertido para inteiro antes
do `get_object_or_404`: sem isso, `pk="abc"` levanta `ValueError` e derruba a
view com 500 em vez de devolver 404.

## 123. HTMX onde ajuda, formulário onde importa

O mesmo desenho do carrinho: um `<form method="post">` de verdade, com CSRF,
que funciona sem JavaScript; e `hx-post` por cima, trocando só o que mudou.

| Onde | Alvo | Por quê |
|---|---|---|
| card da loja | o próprio botão | só o coração muda; o produto fica onde estava |
| lista de favoritos | a grade inteira | desfavoritar tem de tirar o card, e o último precisa virar o estado vazio |

O contador do cabeçalho vem junto por troca *out of band* nos dois casos.

Redesenhar a grade inteira é barato: é a lista de um cliente, não um catálogo —
e é sempre correto, inclusive na transição para vazio, que um `hx-swap="delete"`
no card não saberia fazer.

---

## 124. Quatro páginas, uma view e um template

Envios, trocas, contato e revenda mudam no texto e, em uma delas, no
formulário. O resto — migalha, largura, título, meta description, estado vazio
— é o mesmo. Quatro views iguais seriam quatro lugares para corrigir quando o
layout mudasse, e é assim que uma delas acaba diferente das outras.

Então: `InstitutionalPageMixin` monta o contexto, `InstitutionalPageView` serve
as três de texto e `InstitutionalFormView` serve a de contato — nesta, o que
muda são três atributos (o form, o model onde grava e o assunto do e-mail), não
uma segunda classe.

O slug vem de `PageSlug`, uma lista fechada, e não de texto livre. Cada página
tem uma rota própria no `urls.py`; um slug inventado no Admin criaria conteúdo
escrito que ninguém consegue abrir. Quando a loja precisar de uma quinta
página, entram juntas a escolha e a rota — e o `page_url()` deriva o nome da
rota do nome da escolha, então nenhum template precisa saber o caminho.

---

## 125. O conteúdo inicial vem numa migration, não num comando

A loja não pode subir com quatro páginas em branco, e um comando de seed é uma
coisa a mais para alguém lembrar de rodar — no dia do deploy, com o servidor no
ar. A migration `0005_seed_institutional_content` cadastra as quatro páginas
nos quatro idiomas e a coluna do rodapé que as linka.

É **ponto de partida**, não conteúdo fixo: a migration roda uma vez, e o Admin
reescreve por cima sem que nada volte a apagar o que ele escreveu. Ela também
não sobrescreve uma página que já exista — se alguém cadastrou à mão antes de
migrar, o texto dele manda.

O que a migration não escreve: prazo em dias, prazo legal de desistência,
percentual de desconto de revenda, país específico. Nada disso está definido no
projeto, e número em página institucional é compromisso que a loja não assumiu.
O texto descreve o processo e manda confirmar o resto com a equipe — há um
teste que varre as traduções procurando "N dias/jours/dagen/days" justamente
para que ninguém acrescente um sem perceber.

E a página continua respondendo sem cadastro nenhum: se alguém apagar tudo pelo
Admin, `/trocas-e-devolucoes/` abre com o título da rota e um aviso curto de
que o texto está sendo preparado. Um 404 quebraria o link do rodapé em vez de
dizer que ainda não há o que ler.

---

## 126. A revenda é informativa, e é uma decisão

A primeira versão desta etapa tinha um segundo formulário — empresa, telefone,
país, cidade, tipo de negócio — e uma segunda caixa de entrada. Isso não é um
formulário: é o começo de um sistema de revendedores, com cadastro, status e
histórico, e nada disso está definido.

A página apresenta o programa e termina numa chamada para o contato. Quem
decide isso é `PAGE_CTA`, no model, e não o template: a chamada aparece na
página que declarar um destino, e o dia em que "Filamentos" precisar mandar
para "Contato" é uma linha nesse dicionário.

O contato ganhou uma seção sobre revenda no próprio texto — quem escreve por lá
diz na mensagem que o interesse é comercial. Uma caixa de entrada só, com
mensagens rotuladas pela própria pessoa, é menos máquina para o mesmo
resultado.

---

## 127. Grava primeiro, avisa depois

`form_valid` cria o `ContactMessage` e **só então** tenta mandar o e-mail. A
falha do provedor vai para o log; o cliente vê sucesso, porque para ele a
mensagem foi entregue: está no banco, e o Admin a mostra.

A ordem inversa — mandar o e-mail e gravar depois, ou só mandar o e-mail — faz
o pedido de um cliente depender de um servidor SMTP estar de pé naquele
segundo. Uma caixa de entrada não é um banco de dados.

O `Reply-To` é o e-mail de quem escreveu, mas o `From` continua sendo o da
loja: usar o endereço do visitante como remetente é o caminho mais curto para a
mensagem cair em spam.

---

## 128. Quem recebe o contato não depende do SMTP estar configurado no Admin

`EmailSettings.resolve()` só honra a linha do Admin quando ela está ativa **e**
tem servidor preenchido — faz sentido para as credenciais de envio, que só
valem juntas. Mas o destinatário do contato não é credencial.

Na instalação de teste o SMTP vem do `.env` e a linha do Admin está lá só para
o administrador preencher os destinatários; com a regra do `resolve()`, o
`contact_recipients` seria ignorado justamente no cenário mais provável.

Por isso `contact_recipients()` lê o campo direto da linha e, em branco, cai em
quem já recebe os pedidos (que segue a cadeia normal: Admin ativo, senão
`.env`). São duas perguntas diferentes — "com que conta eu envio?" e "para quem
eu aviso?" — e agora têm duas respostas.

---

## 129. `rich_text`: uma marcação mínima, e tudo escapado

O corpo das páginas é `TextField`. Um `|safe` ali seria XSS armazenado: quem
edita conteúdo não deveria poder executar JavaScript na loja, mesmo sendo da
equipe — a conta pode ser invadida, e o estrago sobrevive à sessão.

Um editor de HTML rico seria uma dependência nova, um upload de imagens, uma
política de sanitização e uma superfície de ataque, para escrever quatro
páginas de texto corrido.

O meio-termo é uma marcação mínima resolvida na exibição: `# ` vira `<h2>`,
`- ` vira item de lista, linha em branco separa parágrafos. **Todo o texto
passa por `conditional_escape`** antes de entrar na tag; o `mark_safe` final
marca só a estrutura que a própria função montou. HTML colado no Admin aparece
como texto na tela — que é exatamente o que ele é.

---

## 130. O link do rodapé aponta para a página, não para um endereço

`FooterLink` tinha só `url`, uma string. Com `i18n_patterns`, a mesma página
mora em `/contato/` e em `/fr/contato/`: um endereço digitado à mão mandaria o
visitante francês para a página portuguesa, e o `.po` não tem como consertar um
dado do banco.

Então `FooterLink.page` — uma referência —, e `href` resolve as duas origens: a
página primeiro, o endereço depois. O `url` continua existindo para o que é de
fora (uma rede social, um `mailto:`), e cadastrar os dois é recusado no
`clean()`: dois destinos para um link é ambiguidade, não flexibilidade.

É o mesmo princípio que o CTA da Home já usava desde a etapa 2 — "referências
internas são preferidas: continuam válidas se o slug mudar e sobrevivem à
chegada de rotas por idioma". A etapa 18 só terminou de aplicá-lo ao rodapé.

Duas consequências caem de graça:

* **o texto do link é o título da página**, quando o link não tem rótulo
  próprio. O nome é escrito num lugar só, e traduzir a página traduz o rodapé
  junto. Quem quiser "Fale conosco" em vez de "Contato" cadastra o rótulo.
* **um link para página despublicada some**. `visible_links` pergunta se a
  página está publicada e marcada para o rodapé; senão, o rodapé mostraria um
  link para um 404 — e `show_in_footer` voltaria a não significar nada.

O custo é uma consulta a mais por página (os títulos das páginas). A página vem
no mesmo `SELECT` dos links, por `select_related`, então o rodapé inteiro são
cinco consultas fixas — independentes de quantos links existam.

---

## 131. Não existe mais "coluna padrão" no rodapé

A primeira versão desta etapa montava a coluna de ajuda a partir das rotas
conhecidas, para o rodapé não ficar sem links numa instalação nova. Com a
migration de conteúdo isso virou uma segunda fonte para a mesma coluna — e duas
fontes são dois lugares para procurar quando o rodapé mostra a coisa errada.

Ficou o cadastro, e só ele. Sem coluna cadastrada o bloco não aparece, o que é
a regra da etapa 16 sem exceção: apagar a coluna é decisão do administrador.

O que a etapa 16 protegia — "instalação nova não pode ter rodapé vazio" —
continua protegido, mas por onde deveria: pelo conteúdo que a migration
cadastra, e não por um `{% empty %}` no template.

---

## 132. A ficha técnica é da variante — toda ela

O defeito vinha da etapa 13 e durou até aqui: trocar de opção atualizava peso,
dimensões, tempo de impressão e referência, mas deixava **cor, tamanho e
material** com o valor da variante com que a página tinha aberto. Quem entrava
no vaso preto e escolhia o branco lia "Cor: Preto" na ficha.

Eram duas causas, e uma sozinha não explicava o outro caso:

* o `variant_payload` levava `color` e `material` como **chaves primárias** —
  servem para casar a combinação, não para escrever na tela. O JavaScript não
  tinha o nome para colocar na ficha. Agora vão junto `colorLabel`, `sizeLabel`
  e `materialLabel`, traduzidos como o resto;
* `specifications` só criava a linha de um eixo quando a variante **de
  abertura** tinha aquele eixo. Se a primeira variante não tem tamanho, a linha
  não existe no HTML — e trocar para a de 30 cm não teria onde escrever. Agora
  a linha entra quando **qualquer** variante do produto tem o eixo, e nasce
  escondida, exatamente como peso e dimensões sempre fizeram.

A regra que ficou: quem lê a ficha está lendo a peça que vai receber. Não pode
existir estado em que metade dela fala de uma variante e metade de outra.

---

## 133. O carrinho mostra a peça comprada, não a capa do produto

Desde a etapa 13 uma foto pode estar vinculada a uma variante, e a página do
produto já trocava a imagem ao escolher. O carrinho não: usava
`line.product.display_media`, a foto de abertura. Quem comprou o preto via o
branco na conferência do pedido.

`CartLine.display_media` resolve na ordem certa: a foto da variante, quando ela
tem uma; a do produto, quando não. Sem foto própria é o caso comum, não falta
de dado.

Custa uma consulta a mais — o `prefetch` das fotos das variantes — e é uma só,
não uma por linha.

---

## 134. Id que vem do cliente pode não ser número

`get_object_or_404(Product, pk=request.POST.get("product_id"))` com
`product_id=abc` levanta `ValueError` no ORM **antes** de qualquer consulta:
500, não 404. É o mesmo defeito que apareceu nos favoritos na etapa 17, agora
no carrinho.

Id inválido e id inexistente são a mesma coisa para quem está do lado de fora:
não existe esse produto. A conversão passou a ser explícita, e o que não
converte vira `Http404`.

---

## 135. Dois cliques no "adicionar" eram duas unidades

O botão continuava clicável enquanto o POST estava no ar, e um duplo clique
mandava duas requisições — o cliente terminava com duas unidades sem ter
pedido.

`hx-disabled-elt="find [data-add-button]"` desliga o botão durante a
requisição. É do próprio HTMX: nenhum JavaScript novo, nenhum estado para
sincronizar, e o `.btn` já tinha o `disabled:opacity-45` da etapa 7 — o
feedback visual veio junto de graça.

Sem JavaScript o navegador já bloqueia o segundo envio do formulário, então o
comportamento é o mesmo dos dois lados.

---

## 136. A quantidade digitada também é corrigida

Os botões − e + limitavam entre 1 e o teto; digitar direto na caixa não passava
por lugar nenhum. Com `max="5"` e "99" digitado, o formulário ficava inválido e
o HTMX simplesmente não mandava a requisição: o cliente clicava em "Adicionar"
e **nada acontecia**.

O mesmo limite agora roda no `change` do campo, venha o valor de onde vier. Os
números saem do próprio `min`/`max` do input — que o servidor preencheu e que a
troca de variante atualiza. Nenhuma regra de estoque escrita no JavaScript:
`Cart.add` continua cortando pelo estoque de verdade, e é ele que avisa quando
o pedido foi maior que o disponível.
