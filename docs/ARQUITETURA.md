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
`production_lead_time_days` em `Product`.

**Por quê.** A etapa pede o mínimo utilizável sem inventar um subsistema.

**Como estender.** Um app `inventory` com `StockMovement`
(entrada / saída / reserva / ajuste, por variante e por localização) passa a
ser a fonte de verdade, e `stock_quantity` vira o saldo consolidado
recalculado a partir das movimentações. A leitura pública já passa por
`Product.is_available`, então o resto do sistema não precisa mudar.

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
