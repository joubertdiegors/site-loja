# JD PRINT — Loja em Django

Backend e vitrine do e-commerce da JD PRINT.

| Etapa | Entrega | Estado |
|---|---|---|
| 1 | Cadastro e gerenciamento de produtos (catálogo, categorias, materiais, cores, marcas, traduções, mídia, custos, preço/margem, estoque, sob encomenda) | concluída |
| 2 | Home + sistema de seções da Home + componentes visuais base + idiomas | concluída |
| 3 | Correção da troca de idiomas + Shop de Modelos + carrinho de sessão | concluída |
| 4 | Página de produto + variantes + personalização + carrinho lateral + idiomas administráveis | concluída |
| 5 | Contas, clientes, confirmação de e-mail e carrinho persistente | concluída |
| 6 | Design System e redesenho do frontend (tokens, tipografia, Minha Conta, produto, shop, gaveta, Home, responsividade) | concluída |
| 7 | Endereços, países, frete, pedidos, checkout, Stripe, cancelamento e e-mails do pedido | concluída |
| 8 | Reestruturação do catálogo: `Product` genérico, `ProductVariant` como unidade vendável | concluída |
| 9 | Matriz de combinações, cadastro de variantes em tabela, custo→margem/preço e cores traduzidas | concluída |
| 10 | **Preparação operacional: material traduzível, recuperação do webhook, reenvio de e-mail, configuração de e-mail no Admin, 404/500, hardening** | concluída |
| 11+ | Cupons, devoluções, faturas em PDF, painel de produção | não iniciada |

Este README é a documentação pública do projeto: o que alguém precisa para
instalar, entender as decisões e rodar a suíte. Os materiais internos —
procedimento de deploy, rotina de backup e restore, e o registro das decisões de
arquitetura com o porquê de cada uma — **não são versionados**: o repositório é
público, e eles descrevem a operação de uma loja específica.

Stack: **Python 3.13 · Django 6.1 · PostgreSQL · Django Templates · Tailwind CSS 4 · Django Admin**.
Sem React, Vue, Angular ou qualquer SPA: a página é renderizada no servidor.

---

## Índice

- [A. Estrutura de arquivos](#a-estrutura-de-arquivos)
- [B. Modelos](#b-modelos)
- [C. Banco de dados](#c-banco-de-dados)
- [D. Multi-idioma](#d-multi-idioma)
- [E. Cálculo de preço](#e-cálculo-de-preço)
- [F. Home e seções](#f-home-e-seções)
- [F2. Shop de Modelos](#f2-shop-de-modelos)
- [F3. Carrinho](#f3-carrinho)
- [F4. Página de produto e variantes](#f4-página-de-produto-e-variantes)
- [F5. Personalização](#f5-personalização)
- [F6. Idiomas da loja](#f6-idiomas-da-loja)
- [F7. Contas, clientes e carrinho persistente](#f7-contas-clientes-e-carrinho-persistente)
- [F8. Endereços e países de entrega](#f8-endereços-e-países-de-entrega)
- [F9. Frete](#f9-frete)
- [F10. Pedidos e checkout](#f10-pedidos-e-checkout)
- [F11. Pagamento com Stripe](#f11-pagamento-com-stripe)
- [F12. E-mails do pedido](#f12-e-mails-do-pedido)
- [G. Design System e identidade visual](#g-design-system-e-identidade-visual)
- [H. Como executar](#h-como-executar)
- [I. Testes](#i-testes)
- [J. Limitações conhecidas](#j-limitações-conhecidas)
- [K. Repositório, segredos e deploy](#k-repositório-segredos-e-deploy)

---

## A. Estrutura de arquivos

```text
Site JD-Print/
├── config/                       # projeto Django (settings, urls, wsgi, asgi)
├── apps/
│   ├── core/                     # fundações reutilizáveis (sem tabelas próprias)
│   │   ├── constants.py          # idiomas, unidades, moedas
│   │   ├── i18n.py               # idioma da interface -> idioma de conteúdo
│   │   ├── models.py             # TimeStamped/Auditable/Translation base
│   │   ├── admin_mixins.py       # slug via tradução, auditoria, formsets
│   │   ├── context_processors.py # marca + menu de categorias
│   │   ├── templatetags/jdprint.py  # logo, ícones, inicial
│   │   ├── management/commands/seed_demo_data.py
│   │   └── tests.py, tests_seed.py
│   ├── categories/               # árvore de categorias (+ tree.py)
│   ├── catalog/                  # produtos, atributos, mídia, preço
│   │   ├── models.py, pricing.py, admin.py
│   │   ├── views.py, urls.py     # Shop de Modelos + páginas provisórias
│   │   └── tests/
│   ├── accounts/                 # ← ETAPA 5: conta de acesso e cliente
│   │   ├── models.py             # User (custom) + Customer
│   │   ├── managers.py           # normalização e busca sem caixa
│   │   ├── validators.py         # regras do username
│   │   ├── backends.py           # login por username OU e-mail
│   │   ├── tokens.py             # token da confirmação de e-mail
│   │   ├── emails.py             # e-mails transacionais (SITE_URL, idioma)
│   │   ├── forms.py, views.py, urls.py, admin.py
│   │   └── tests/
│   ├── cart/                     # carrinho (sessão + banco)
│   │   ├── cart.py               # a classe Cart: regras e leitura
│   │   ├── keys.py               # identidade da linha
│   │   ├── storage.py            # sessão (visitante) / banco (autenticado)
│   │   ├── merge.py              # sessão -> conta, no login
│   │   ├── signals.py            # liga o merge ao user_logged_in
│   │   ├── forms.py              # validação do "adicionar"
│   │   ├── models.py             # CustomizationUpload, Cart, CartItem
│   │   ├── views.py, urls.py     # adicionar / atualizar / remover / gaveta
│   │   ├── context_processors.py # contador do header
│   │   └── tests/
│   ├── shipping/                 # ← ETAPA 7: transportadoras e tarifas
│   │   ├── models.py             # ShippingCarrier, ShippingMethod, ShippingRate
│   │   ├── services.py           # peso do pedido, prazo e cotação
│   │   ├── admin.py              # a grade de preços, do jeito que se digita
│   │   └── tests/
│   ├── orders/                   # ← ETAPA 7: pedidos, checkout e pagamento
│   │   ├── models.py             # Order, OrderAddress, OrderItem, Payment…
│   │   ├── services.py           # validar, criar, confirmar, enviar, cancelar
│   │   ├── taxes.py              # TVA embutida, alíquota por país
│   │   ├── emails.py             # confirmação, ordem de produção, envio
│   │   ├── payments/             # camada de gateway (base + Stripe)
│   │   │   ├── base.py           # a interface: start / parse_webhook / handle
│   │   │   └── stripe_provider.py
│   │   ├── forms.py, views.py, urls.py, admin.py
│   │   └── tests/
│   └── home/                     # ← ETAPA 2
│       ├── models.py             # HomeBanner, HomeSection, HomeSectionProduct
│       ├── services.py           # resolve o conteúdo de cada seção
│       ├── views.py, urls.py     # a Home
│       ├── admin.py              # gerenciamento das seções
│       └── tests/
├── templates/
│   ├── base.html
│   ├── home/index.html
│   ├── catalog/shop.html, _shop_results.html, coming_soon.html
│   ├── cart/detail.html, _cart_panel.html, _update.html, _drawer_body.html
│   ├── orders/                   # checkout, resumo, confirmação, retorno
│   ├── accounts/                 # entrar, conta, endereços, pedidos
│   ├── emails/                   # e-mails transacionais (HTML + texto)
│   └── components/               # header, footer, hero, cards, seção, idioma…
├── static/
│   ├── src/input.css             # fonte do Tailwind (tema + componentes)
│   ├── css/tailwind.css          # CSS gerado (npm run build:css)
│   ├── js/app.js                 # menu, dropdowns, carrossel, filtros
│   ├── vendor/htmx.min.js        # HTMX (carrinho, filtros, paginação)
│   ├── admin/                    # css/js do Django Admin
│   └── images/logo/              # ← coloque a logo oficial aqui
├── locale/{fr,en,nl}/LC_MESSAGES/  # traduções da interface (.po/.mo)
├── scripts/compile_messages.py   # compila .po sem precisar do gettext
├── media/                        # uploads (products/…, banners/…, brand/…)
├── package.json                  # apenas o build do Tailwind
└── manage.py
```

Cinco apps de domínio (`categories`, `catalog`, `home`, `cart`, `accounts`) e
um de fundação (`core`). Não criei um app `media`: `ProductMedia` só existe em função
do produto e o nome colidiria com `MEDIA_ROOT` do Django.

---

## B. Modelos

### `core` (abstratos, sem tabela)

| Modelo | Papel |
|---|---|
| `TimeStampedModel` | `created_at`, `updated_at` |
| `AuditableModel` | o anterior + `created_by`, `updated_by` |
| `TranslationBase` | base das tabelas de tradução (campo `language`) |
| `TranslatableMixin` | leitura traduzida com fallback (`tr()`, `name_in()`) |

### `categories`

`Category` (auto-relacionamento `parent`, hierarquia livre) + `CategoryTranslation`.

### `catalog`

`Brand`, `Material`, `Color`, `ColorTranslation`, `Product`,
`ProductTranslation`, `ProductMedia` e `ProductVariant`.

`Color` e `Material` são traduzíveis pelo mesmo mecanismo de `Product` e
`Category`: uma linha por idioma em `ColorTranslation` / `MaterialTranslation`.
O campo `name` é o nome **interno** (Admin, busca, slug); `display_name` é o que
o cliente lê, com o fallback da casa (idioma pedido → português → qualquer uma →
nome interno). A variante continua sendo um registro só: quem tem tradução é o
atributo, não a variante.

No snapshot do pedido, cor e material são copiados **no idioma do cliente**,
como já era o nome do produto — o pedido guarda o que ele leu.

**A divisão, desde a etapa 8:**

| | `Product` | `ProductVariant` |
|---|---|---|
| o que é | a **definição genérica** — o conceito | a **unidade comercial vendável** — a coisa |
| exemplo | "Vaso Facetado" | "Vaso Facetado, preto, 25 cm, PLA" |
| tem | SKU, slug, categoria, marca, moeda, textos traduzidos, mídia, personalização, destaque | SKU, cor, tamanho, material, **preço, custo, margem, estoque, peso, dimensões, tempo de impressão, prazo de produção** |
| não tem | preço, estoque, peso, dimensões, prazo | tradução, mídia, categoria |

**Todo produto precisa de pelo menos uma variante ativa para ser vendido.** Sem
ela não há preço, peso nem estoque em lugar nenhum: o produto fica fora da
vitrine, não entra no carrinho e o Admin recusa ativá-lo.

Não existe "variante implícita" feita a partir dos dados do produto. O que o
`Product` ainda responde sobre preço e estoque (`display_price`, `price_range`,
`available_stock`, `is_available`, `stock_state`, `made_to_order`) é **derivado**
das variantes — resumo para o card, nunca fonte.

### `accounts` — novo na etapa 5

| Modelo | Papel | Campos principais |
|---|---|---|
| `User` | **conta de acesso** | `username`, `email`, `password`, `is_active`, `email_verified`, `email_verified_at`, `verification_sent_at`, `preferred_language`, `date_joined`, `last_login` |
| `Customer` | **cliente da loja** (1:0..1 com `User`) | `first_name`, `last_name`, `phone`, `company_name`, `vat_number` |

### `cart` — carrinho persistente (etapa 5)

| Modelo | Papel |
|---|---|
| `Cart` | um por usuário (`OneToOne`), com `created_at`/`updated_at` |
| `CartItem` | linha: produto, variante, quantidade, personalização e `line_key` |
| `CustomizationUpload` | arquivo enviado pelo cliente (etapa 4) |

### `core` — configuração da loja

| Model | Para quê |
|---|---|
| `SiteLanguage` | quais idiomas a loja oferece hoje |
| `DeliveryCountry` | para onde a loja envia + alíquota de TVA (ISO 3166-1 alfa-2) |
| `DeliveryCountryTranslation` | o nome do país em cada idioma |

### `accounts` — endereços (etapa 7)

| Model | Para quê |
|---|---|
| `CustomerAddress` | endereço do cliente; vários por cliente, com padrão de entrega e de faturamento independentes |

Nome e sobrenome ficam no endereço e não são lidos do `Customer`: quem recebe
pode não ser quem compra.

### `shipping` — novo na etapa 7

| Model | Para quê |
|---|---|
| `ShippingCarrier` | quem transporta (+ URL de rastreio) |
| `ShippingMethod` | o que o cliente escolhe; guarda o prazo de **transporte** |
| `ShippingRate` | país + faixa de peso + preço |

### `orders` — novo na etapa 7

| Model | Para quê |
|---|---|
| `Order` | o pedido: número público, três estados, valores, imposto, entrega, presente, cancelamento |
| `OrderAddress` | **cópia** do endereço de entrega e do de faturamento |
| `OrderItem` | **snapshot** completo da linha comprada, inclusive personalização |
| `OrderStatusHistory` | linha do tempo (com marcação de visível para o cliente) |
| `OrderNote` | nota interna — nunca aparece para o cliente |
| `Payment` | uma linha por tentativa de pagamento; só identificadores opacos |
| `WebhookEvent` | evento já processado — é o que torna o webhook idempotente |
| `OrderNumberSequence` | contador de `JD-2026-000001`, um por ano |

### `home`

| Modelo | Papel | Campos principais |
|---|---|---|
| `HomeBanner` | área de destaque do topo | `internal_name`, `image_desktop`, `image_mobile`, `is_active`, `sort_order`, CTA |
| `HomeBannerTranslation` | conteúdo do banner por idioma | `language`, `title`, `subtitle`, `cta_label`, `image_alt` |
| `HomeSection` | uma faixa de produtos da Home | `internal_name`, `section_type`, `layout`, `is_active`, `sort_order`, `product_limit`, `category`, `include_subcategories`, CTA |
| `HomeSectionTranslation` | conteúdo da seção por idioma | `language`, `title`, `subtitle`, `cta_label` |
| `HomeSectionProduct` | produto escolhido manualmente | `section`, `product`, `sort_order` |

O CTA (`CtaMixin`, compartilhado por banner e seção) aponta para **categoria**,
**produto** ou uma **URL livre**. Referências internas são preferidas: continuam
válidas se o slug mudar e sobrevivem à chegada de rotas por idioma.

### Alterações em models existentes

Apenas quatro acréscimos, todos métodos/properties — **nenhuma migration**, nenhum
campo alterado:

| Onde | O quê | Por quê |
|---|---|---|
| `Product.get_absolute_url()` | URL pública do produto | o card precisa de um destino real |
| `Product.display_media` | imagem principal → primeira imagem/GIF → `None` | o card não pode quebrar sem foto (vídeo não serve de capa) |
| `Product.display_short_description` | descrição curta traduzida | usada no card |
| `Category.get_absolute_url()` | URL pública da categoria | menu, cards de categoria e CTA |

---

## C. Banco de dados

Relações novas:

```text
HomeBanner  ──< HomeBannerTranslation       1:N, única por (banner, idioma)
HomeSection ──< HomeSectionTranslation      1:N, única por (seção, idioma)
HomeSection ──< HomeSectionProduct >── Product   N:N com ordem própria
HomeSection ──> Category                    FK, PROTECT (tipo "categoria")
HomeSection ──> Category/Product            FK do CTA, SET_NULL
```

Constraints:

* uma tradução por idioma, por banner/seção; título não vazio
* `product_limit` entre 1 e 24
* seção do tipo "categoria" **obriga** ter categoria (constraint condicional)
* um produto não pode ser adicionado duas vezes à mesma seção

Índices: `is_active + sort_order` (banner e seção), `section + sort_order`
(produtos escolhidos).

Tudo criado por migration (`apps/home/migrations/0001_initial.py`), sem nenhum
recurso exclusivo do SQLite — aplica igual no PostgreSQL.

---

## D. Multi-idioma

### Conteúdo (banco)

Mesma arquitetura da etapa 1, agora também para a Home: **uma tabela de
tradução por modelo**, uma linha por idioma. Nenhum campo `title_pt`,
`title_fr`, `title_nl`.

Fallback **por campo**: idioma pedido → português → qualquer tradução → padrão.
Se a seção tem título em holandês mas não tem subtítulo, aparece o subtítulo
português em vez de um vazio.

### Interface (gettext)

Textos fixos usam `{% translate %}` / `gettext`. Catálogos em
`locale/{fr,en,nl}/LC_MESSAGES/django.po`. Português é o idioma-fonte, então
não precisa de catálogo.

### URLs e persistência

`i18n_patterns` com `prefix_default_language=False`:

```text
/           português (padrão)
/fr/        francês
/nl/  /en/  /de/  /es/  /it/  /tr/  /ar/
```

A troca de idioma usa uma view do projeto (`apps/core/views.py`), no lugar da
view do Django, e mantém o mesmo nome de rota (`set_language`). Ela grava o
cookie e redireciona para a **mesma página** no novo idioma, preservando o
caminho, a query string e o fragmento.

Por que não a view do Django: ela traduz a URL de retorno com `translate_url()`,
que resolve o caminho usando o idioma **ativo na requisição** — e o POST chega
em `/i18n/setlang/`, fora do `i18n_patterns`. Vindo de `/en/`, o caminho não
resolvia, a URL voltava intacta e o prefixo `/en/` continuava mandando na
página apesar do cookie novo. Era esse o bug de "o idioma não muda".
`apps/core/i18n.py::translate_path` resolve a URL sob o idioma **dela** e a
remonta sob o idioma de destino; se a rota não resolver, troca o prefixo
diretamente.

Um segundo detalhe, também estrutural: com `prefix_default_language=False` o
Django **ignora o cookie em URLs sem prefixo** (`/modelos/` é sempre a versão
canônica em português). Quem escolheu francês e voltasse pela URL sem prefixo
cairia no português. `apps.core.middleware.PreferredLanguageRedirectMiddleware`
fecha essa lacuna redirecionando para a versão prefixada — só em `GET`/`HEAD` e
só nas rotas públicas.

O seletor do header lê `settings.LANGUAGES` — nenhum idioma escrito no
template. Árabe recebe `dir="rtl"` automaticamente.

---

## E. Cálculo de preço

Inalterado (`apps/catalog/pricing.py`):

```text
custo_total = soma dos componentes de custo
margem (%)  = ((preço − custo_total) / preço) × 100
preço       = custo_total / (1 − margem/100)
```

O campo `pricing_mode` define qual lado o administrador digita; o outro é
derivado. Margem sobre o preço de venda, não markup. Tudo em `Decimal`.

---

## F. Home e seções

### Como a Home é montada

```text
HomeView
  └── services.get_home_context()
        ├── get_active_banner()      -> primeiro banner ativo (ou None)
        ├── get_home_sections()      -> seções ativas, ordenadas, resolvidas,
        │                               sem as vazias
        └── get_category_cards()     -> categorias raiz com produtos
```

O template `home/index.html` só percorre `sections` e inclui
`components/home_section.html`. **Nenhuma seção está escrita no HTML**:
adicionar, remover ou reordenar faixas é trabalho do Admin.

### Tipos de seção

| Tipo | De onde vêm os produtos |
|---|---|
| `MANUAL_PRODUCTS` | escolhidos um a um, na ordem definida pelo administrador |
| `CATEGORY_PRODUCTS` | de uma categoria (com ou sem as subcategorias) |
| `FEATURED_PRODUCTS` | produtos marcados como destaque, na ordem de destaque |
| `NEWEST_PRODUCTS` | mais recentes |
| `BEST_SELLERS` | **sem fonte de dados nesta etapa** (ver limitações) |

Acrescentar um tipo novo (promoções, lançamentos de uma marca) = uma opção em
`HomeSectionType` + uma função em `services.RESOLVERS`. O template não muda.

### Regras aplicadas

* seção inativa não aparece (útil para campanhas sazonais — desativa, não apaga);
* seção sem produtos disponíveis não aparece (nunca um título com zero itens);
* `product_limit` é respeitado (10 selecionados, limite 4 → mostra 4);
* layout **grade** ou **carrossel** por seção;
* produto sem imagem usa um espaço reservado elegante — a Home não quebra.

### Consultas

A Home usa um número **fixo** de consultas: cresce com o número de seções
ativas, não com o tamanho do catálogo. Dois testes garantem isso
(`assertNumQueries`), e foi assim que um N+1 nas traduções de categoria foi
encontrado e corrigido durante o desenvolvimento.

### O menu do Admin

O índice do Admin (`config/admin.py`) é organizado como a loja é vista, e
não por app Python: sete caixas — PEDIDOS, CATÁLOGO, CLIENTES, ENTREGA, HOME,
CONFIGURAÇÕES DA LOJA e EQUIPE — com subtítulos e itens na ordem em que a
coisa acontece ou aparece na tela. A caixa HOME segue a página de cima para
baixo (faixa do topo, banner principal com o carrossel recuado abaixo dos
banners, os blocos na ordem em que aparecem, rodapé); o que faz parte de um
bloco vem recuado logo abaixo dele (as pílulas do "Sobre a loja", os passos
da chamada final, os links dentro das colunas do rodapé). Os nomes na tela
são os de `config/admin.py`, escritos para quem administra; mudar um nome
ali não gera migration nem altera URL, e os models continuam com os seus
endereços e permissões. Cada item pode levar uma dica de uma linha, que o
índice mostra e a barra lateral omite (`templates/admin/app_list.html`,
`static/admin/css/jdprint_menu.css`). `apps/core/tests_admin_sections.py`
garante que nada sumiu, nada mudou de endereço e ninguém ganhou acesso.

### As telas de edição: grade de campos e pré-visualização ao vivo

Os formulários do Admin não são mais "uma linha por campo": campos
relacionados dividem a linha (`fields = (("internal_name", "layout"), ...)`
nos `fieldsets` de cada `ModelAdmin`), com rótulo em cima e a grade
respondendo à largura — três ou duas colunas no desktop, duas no tablet, uma
no celular. É só CSS sobre o markup do próprio Django
(`static/admin/css/jdprint_forms.css`, carregada por
`templates/admin/base_site.html`); textos longos continuam sozinhos na
linha, e os blocos de idioma dos inlines seguem a mesma grade. Os campos
condicionais (tipo de banner, tipo de seção, tipo de página especial, destino
do botão) continuam escondidos pelo JavaScript de sempre, agora caixa a caixa
(`static/admin/js/jd_fields.js`): esconder um campo não esconde a linha em
que ele está, e uma linha só some quando todas as caixas dela somem.

As telas que desenham algo no site têm **pré-visualização ao vivo** ao lado
do formulário (`apps/core/admin_preview.py`, `LivePreviewMixin`). O painel
envia o formulário como está — por POST, sem salvar — para
`…/live-preview/`, uma rota do próprio `AdminSite` (passa por `admin_view`,
exige login de equipe e a permissão de ver/alterar o registro, ou de
adicionar quando ele ainda não existe; responde só a POST, com
`Cache-Control: no-store` e `X-Robots-Tag: noindex`). No servidor o registro
é montado em memória com `construct_instance` (só os campos que passaram na
validação individual entram; arquivos ficam de fora — uma imagem nova
aparece depois de salvar) e os textos do inline de idiomas vão para o cache
do `tr()`, no idioma pedido (`?lang=`). O HTML devolvido é o **mesmo
componente do site** — `components/banner_carousel.html`,
`components/home_composition.html` (a seção resolvida por
`services.resolve_section`, com os mesmos prefetches da Home),
`components/top_bar.html`, `components/footer.html`,
`storefront/_page_article.html`, e a página especial inteira via
`render_special_page` — dentro de um documento mínimo com o `tailwind.css`
da loja (`templates/admin/preview/frame.html`), mostrado num `iframe` com
`srcdoc` e `sandbox`. Nada é escrito no banco, o que a pessoa digita é texto
(escapado pelo template), e nenhuma URL pública é criada. Os botões PT/FR/NL/
EN trocam o idioma do quadro; Desktop/Tablet/Celular mudam só a largura
visual do quadro (toda a área, ~768 px ou ~390 px, centralizado). O painel
fica no topo da área principal, na largura toda, com o formulário abaixo —
assim os campos têm o espaço todo para a grade — e gruda no alto da janela
enquanto a página rola (`position: sticky`): o cabeçalho com os controles
está sempre à mão. No topo da página o quadro abre no fluxo; depois de
rolar, abre como camada flutuante sob o cabeçalho, sem mexer na rolagem nem
no formulário (um espaçador guarda a altura que o quadro tinha no fluxo).
"Recolher" deixa só o cabeçalho (e suspende as atualizações até expandir);
recolhido/expandido e a largura escolhida ficam no `localStorage` do
navegador, por cadastro. O inline de links da coluna do rodapé vira um card
por link até 820 px (`fieldset.jd-cards` + `admin/js/jd_tabular_cards.js`,
que copia o cabeçalho da tabela para o rótulo de cada campo).
`apps/core/tests_admin_preview.py` cobre o acesso, o
não-salvar, os idiomas, o escape, a seção da Home, os irmãos de um registro
novo, a página especial e a grade.

### A composição da Home

Desde a etapa 20 a Home é uma **lista de seções** (`home.HomeSection`), na
ordem do Admin — e não só as faixas de produtos: os blocos que tinham posição
fixa no template (categorias em destaque, como trabalhamos, chamada final,
sobre a loja) viraram tipos de seção. Cada linha é uma **instância**: tipo
(o modelo do bloco), nome interno (esta instância — "Categorias — Coleções"),
ordem, ativa. Uma mesma seção pode existir quantas vezes quiser, cada uma com
o seu conteúdo: os blocos de categoria (`HomeCategoryCard`) e os cards de
"como trabalhamos" (`HomeCard`) pertencem à sua seção; a chamada final
(`HomeCallout`, com os passos) e o "Sobre a loja" (`HomeAbout`, com as
pílulas) são registros de conteúdo que a seção escolhe — duas seções podem
apontar para o mesmo texto. `apps/home/services.py` resolve tudo numa
consulta de seções com um número fixo de prefetches e descarta o que não tem
o que mostrar; `templates/home/index.html` só escolhe o componente pelo
`kind`.

A **faixa de fundo** de cada seção é consequência da posição entre as que
sobraram: a primeira depois do banner é branca (com os fios), a segunda
creme, e assim por diante — remover ou mover uma seção reajusta as outras, e
nada é gravado. A chamada final, o "Sobre a loja" (cores do cadastro) e a
faixa lilás de "como trabalhamos" pintam por cima, mas contam na alternância.
Sem nenhuma seção cadastrada, a Home mostra a composição padrão (aviso de
vitrine em montagem, os três cards e a chamada de fábrica); a migration
`home/0009` transformou a Home existente em seções, na mesma ordem, sem apagar
nem recriar nada.

### Gerenciando pelo Admin

`/admin/` → **HOME** → **3 · Seções da Home**: a lista é a página, numerada
de cima para baixo (nº, nome da instância, tipo, o que mostra, ativa, ordem).
Reordenar e ativar é direto na grade. Ao criar uma seção, o formulário mostra
só o que o tipo usa: layout, limite e botão nas faixas de produtos; o texto
escolhido na chamada final e no "Sobre a loja"; os links para os blocos e
cards nas outras duas. Para uma faixa de produtos:

1. **Adicionar seção** → nome interno, ativa, ordem.
2. **Tipo** — o formulário mostra só o que importa: escolher "Produtos de uma
   categoria" revela o campo *Categoria*; escolher "Produtos escolhidos
   manualmente" revela a lista de produtos; os demais tipos não pedem nada.
3. **Layout** (grade/carrossel) e **quantidade de produtos**.
4. **Botão (CTA)** — destino: sem botão, categoria, produto ou endereço livre.
5. **CONTEÚDO** — título, subtítulo e texto do botão. Português é obrigatório;
   "Adicionar outro Conteúdo por idioma" acrescenta francês, holandês, etc.
6. **PRODUTOS** (tipo manual) — escolha o produto e o número de ordem.

Na listagem: ativar/desativar e reordenar direto na grade, filtros por tipo e
estado, e as ações **Ativar**, **Desativar** e **Duplicar** (a cópia nasce
desativada, com traduções e produtos).

Banners: **HOME → 2 · Banner principal → Banners** (imagem desktop, imagem
mobile, título, subtítulo, botão); o comportamento da rotação fica em
**Carrossel**, logo abaixo. Sem imagem, a Home mostra o destaque tipográfico.
Os blocos de categoria, os cards, os textos da chamada final (com os passos)
e os blocos "Sobre a loja" (com as pílulas) ficam recuados abaixo de **Seções
da Home**: são o conteúdo que cada seção usa.

---

## F2. Shop de Modelos

`/modelos/` — a primeira página real de catálogo.

### O que define "modelos"

A categoria raiz indicada por `settings.SHOP_MODELS_CATEGORY_SLUG`
(padrão `modelos`, ajustável por variável de ambiente). A vitrine mostra os
produtos dessa árvore inteira — filamentos, impressoras e acessórios ficam de
fora porque estão em outras raízes. **Nenhum campo novo foi criado no
`Product`**: a categorização que já existia dá conta.

`ShopView` é genérica: abrir uma vitrine de Filamentos é acrescentar uma rota
apontando para outra raiz.

### Filtros, ordenação e paginação

| Recurso | Como |
|---|---|
| Categoria | `?categoria=<slug>` — inclui as subcategorias; slug fora da árvore é ignorado |
| Ordenação | `?ordenar=` `recentes` (padrão), `nome-az`, `nome-za`, `preco-asc`, `preco-desc` |
| Quantidade por página | `?per_page=` — 4, 8, 12, 16, 20 ou 24 (múltiplos de 4); valor fora da lista cai no padrão |
| Paginação | `?page=` — `settings.SHOP_PAGE_SIZE` (12) por página |
| Contagem | respeita o filtro atual ("8 produtos") |

Ordenar por nome usa o **nome traduzido**: como ele mora na tabela de
traduções, entra por subconsulta (`Subquery` + `Coalesce` com o português como
reserva) e é resolvido no banco — a paginação continua correta.

A barra lateral mostra a árvore de categorias com a contagem de cada uma (a
selecionada em navy) e o filtro de material em pílulas. No celular ela vira
uma gaveta: um `popover` nativo, aberto pelo botão **Filtros** e fechado pelo
✕, pelo "Ver N produtos", pelo Esc ou pelo toque fora — sem JavaScript. A
anatomia da página (trilha, banner roxo, título com a contagem, filtros ativos
em pílulas, cards com moldura pastel) segue o arquivo da direção visual; as
medidas estão por extenso em `static/src/input.css`, bloco "O catálogo".

### HTMX

Filtrar, ordenar e paginar trocam **apenas a grade** (`hx-get` + `hx-target`
+ `hx-push-url`): a URL muda, o histórico funciona e a página não recarrega.
Todos os controles são links e formulários reais, então **sem JavaScript tudo
continua funcionando** — a view devolve a página inteira quando o pedido não
vem do HTMX.

---

## F3. Carrinho

Carrinho de **sessão**, sem banco e sem login. Nenhum model `Order` foi criado.

```python
request.session["cart"] = {"12": {"quantity": 2}, "34": {"quantity": 1}}
```

| Rota | O quê |
|---|---|
| `/carrinho/` | página do carrinho |
| `/carrinho/adicionar/` | POST — adiciona (soma se já existir) |
| `/carrinho/atualizar/` | POST — `+`, `−` ou quantidade absoluta |
| `/carrinho/remover/` | POST — remove a linha |
| `/carrinho/painel/` | conteúdo da gaveta lateral (HTMX) |
| `/carrinho/finalizar/` | **checkout** (etapa 7); responde 200 também ao visitante |

O ícone do carrinho no header abre uma **gaveta lateral** (mini-cart) em vez de
navegar: produtos, quantidades, remover, subtotal e "Ver carrinho". Fecha no X,
no fundo escuro ou com ESC; o foco fica preso dentro dela enquanto está aberta
e volta para o ícone ao fechar. Adicionar um produto abre a gaveta sozinha.
Sem JavaScript, o mesmo ícone continua levando para `/carrinho/`.

Regras aplicadas:

* só produtos **ativos** entram; produto que sai do ar some da sessão sozinho;
* **esgotado** (estoque 0, sem backorder e sem encomenda) não pode ser
  adicionado — o botão do card aparece desabilitado;
* **sob encomenda** e **venda sem estoque** não dependem do saldo;
* estoque limitado é respeitado: pedir 9 de um produto com 2 adiciona 2 e avisa;
* quantidade 0 remove a linha;
* subtotal em `Decimal` — nenhum `float` participa do cálculo;
* o contador do header mostra **unidades** (2 + 3 = 5), não linhas, e não faz
  nenhuma consulta ao banco.

Com HTMX, adicionar ao carrinho atualiza o contador e mostra um aviso sem sair
da página (troca *out of band*). Sem JavaScript, o mesmo formulário faz POST e
volta com a mensagem do Django.

**Preparado para variantes:** a chave da sessão hoje é o id do produto; quando
`ProductVariant` existir, ela passa a identificar o par produto/variante e
`CartLine` ganha o campo. Não há tabela para migrar.

---

### Os quatro desenhos do banner

`HomeBanner.layout` aceita **Hero editorial**, **Imagem completa**, **Poster
Pop** (poster roxo de conteúdo centrado: palavra do título marcada em amarelo,
selos menta e branco, três quadros de foto na base) e **Bento Criativo** (grade
de cartões: texto no cartão branco com o selo coral, foto no cartão menta com o
selo amarelo, e embaixo os cartões de cores e de avaliação). Os dois novos
reaproveitam os campos do editorial (tarja, destaque, promessas, selos, dois
botões) e acrescentam só o que não existia: os dois quadros laterais do Poster
Pop (`image_tile_left`/`image_tile_right`, o do meio é a `image_desktop`) e,
por idioma, `badge_coral`, `colors_note`, `rating_value`, `rating_note` e os
textos alternativos dos quadros. O Admin mostra só os campos do desenho
escolhido (`static/admin/js/home_banner_admin.js`); as medidas estão em
`static/src/input.css`, bloco "Poster Pop e Bento Criativo". A seed cria os
dois como banners inativos, para o Admin ver como ficam.

### O carrossel de banners

O banner mora num **quadro único** (`.hero-stage`, em
`components/banner_carousel.html`): a largura do `container-page`, proporção
1280×550 no desktop e 780px de altura até 1100px, raio de bloco e
`overflow: hidden`. Os quatro desenhos preenchem o quadro e ajustam o miolo a
ele (no desktop as medidas estão em `cqw`, encolhendo com o quadro), então
todos têm a mesma largura, altura e posição, nenhum fundo passa da borda — a
Imagem Completa não faz mais uma faixa roxa de ponta a ponta — e trocar de
slide não move a página. Com **um** banner ativo, o hero sozinho no quadro;
sem banner, o destaque tipográfico de sempre. Com **dois ou mais**, os banners
ativos viram slides na ordem do campo *ordem*, empilhados no quadro. Só o
primeiro slide tem o `<h1>`; os outros ficam `inert`, com o título em `<h2>` e
as fotos em `loading="lazy"`. A configuração é global e mora em HOME › CARROSSEL DE
BANNERS (`HomeBannerCarousel`, uma linha só): rotação automática (desligada
por padrão), intervalo de 2 a 30 segundos, setas, indicadores, pausa ao passar
o cursor e pausa após interação — tudo chega ao `app.js` por `data-*`. Setas,
indicadores, ← → com o foco no carrossel e o deslize no celular navegam;
`prefers-reduced-motion` desliga a rotação e o fade.

O CSS e o JavaScript saem do `base.html` por `{% static_versioned %}`
(`/static/js/app.js?v=<hash do conteúdo>`): a URL muda quando o arquivo muda e
o navegador não reaproveita um `app.js` antigo do cache — foi assim que um
carrossel com o HTML novo chegou a um navegador sem o script novo (setas na
tela, clique sem efeito). Os testes de navegador
(`apps/home/tests/test_banner_carousel_browser.py`, Playwright, tag `browser`)
abrem a Home num Chromium e clicam de verdade: setas, indicadores, teclado,
deslize, rotação automática, pausas e `prefers-reduced-motion`, nas cinco
larguras. Sem o pacote (`requirements/dev.txt`) ou sem navegador, são pulados;
`manage.py test --exclude-tag browser` os deixa de fora.

### Manutenção e lançamento — a página que fecha a loja

CONFIGURAÇÕES DA LOJA › MANUTENÇÃO E LANÇAMENTO (`storefront.SpecialPage`):
quantas páginas se quiser, cada uma do tipo **manutenção** (duas colunas,
ilustração da impressora com porcentagem e barra) ou **lançamento** (o poster
roxo com contagem regressiva e formulário de aviso), reproduzindo os dois
HTMLs da direção visual (`templates/storefront/special_page.html`, bloco "A
página de manutenção e de lançamento" no CSS). Todo texto é traduzível por
idioma (`SpecialPageTranslation`), os benefícios são itens à parte
(`SpecialPageBenefit`, com tom de cor e ordem) e as cores saem do sistema de
cores do projeto, com contraste conferido ao salvar.

**Só uma página fica ativa**, e isso vale no banco: um índice único parcial
sobre `is_active` impede duas linhas ativas mesmo com dois administradores
salvando ao mesmo tempo; ativar uma desliga a outra na mesma transação. Com
uma página ativa, `apps/storefront/middleware.py` responde com ela em **toda**
rota pública, em qualquer idioma e método — manutenção com 503 e
`Retry-After`, lançamento com 200; as duas com `noindex` (meta e
`X-Robots-Tag`) e `Cache-Control: no-store`, então desativar vale na
requisição seguinte. Continuam abertos `/admin/`, `/i18n/`, estáticos, mídia,
o webhook da Stripe e a rota do formulário; quem está logado como **equipe**
vê a loja normal para testar (é a sessão do Admin, não uma URL secreta), e o
cliente comum logado vê a página especial. No Admin: ativação por ação com
tela de confirmação (e aviso ao marcar «ativa» no formulário),
pré-visualização de qualquer página inativa (`.../<id>/preview/?lang=fr`,
só para equipe com permissão de ver) e a lista de inscritos do lançamento
(`LaunchSubscriber`, e-mail único sem distinguir maiúsculas, exportável em
CSV). O formulário tem CSRF, honeypot e trava por IP; sem integração externa
— a lista fica no banco. A contagem regressiva é calculada no navegador a
partir da data, hora e fuso do cadastro (`static/js/special_page.js`) e, ao
chegar a zero, mostra o texto final configurado.

### A página do carrinho

`/carrinho/` segue o arquivo da direção visual (`Carrinho.html`): título com a
contagem de itens e "Continuar comprando"; cada linha é um card com foto,
categoria, nome, as opções em pílulas (cor com a bolinha, tamanho, material,
sob encomenda, personalização), preço unitário, "Remover", o total da linha e a
quantidade; à direita, o resumo escuro (subtotal, envio calculado no checkout,
prazo de produção — o maior entre as linhas, como o checkout calcula —, total,
o botão amarelo e as formas de pagamento disponíveis) com as páginas de envios
e trocas como dois cartões embaixo; no fim, "Você também pode gostar" com o
card do catálogo. A contagem do título volta por troca *out of band* a cada
ação do HTMX. Cupom e limite de frete grátis, que o desenho mostra, não
existem no backend e não foram desenhados. As medidas estão em
`static/src/input.css`, bloco "O carrinho".

## F4. Página de produto e variantes

`/produtos/<slug>/` — galeria, compra, descrição e ficha técnica.

Mantive a URL no plural, criada na etapa 3, em vez do `/produto/<slug>/`
sugerido: a rota já era usada pelos cards da Home e do Shop, e trocá-la só
geraria redirecionamento. É uma linha para mudar, se preferir.

### O que a página mostra

* **galeria** de `ProductMedia` — imagem principal primeiro, miniaturas abaixo,
  vídeo suportado. Sem foto, o mesmo espaço reservado dos cards;
* **nome e descrição** traduzidos, com fallback para português;
* **preço da variante selecionada** — nunca uma faixa: a página sempre tem
  uma opção escolhida, e o preço é o dela;
* **estado de estoque da variante**: Em estoque / Últimas unidades / Esgotado /
  Produzido sob encomenda (com o prazo);
* **ficha técnica da variante** — peso, dimensões e tempo de impressão são da
  unidade que o cliente vai receber, não do conceito.

A anatomia segue o arquivo da direção visual (`Produto.html`): galeria à
esquerda (moldura de 36px, miniaturas em quatro colunas), coluna de compra
presa ao rolar à direita — etiquetas, título, preço com a referência, cores em
bolinhas, tamanhos e materiais em pílulas, a linha quantidade + botão +
coração, três cartões (impressão, envio, material) e um acordeão `<details>`
com descrição, ficha e informações adicionais. As medidas estão por extenso em
`static/src/input.css`, bloco "A página do produto". A linha de compra fica
fora do `<form>` do carrinho (o coração é um formulário próprio) e aponta para
ele por `form="add-to-cart"`.

Trocar de opção atualiza a página inteira sem recarregar: preço,
disponibilidade, teto de quantidade, foto (quando a variante tem uma) e a
**ficha técnica completa** — cor, tamanho, material, peso, dimensões, tempo de
impressão e referência. Até a etapa 18 as três primeiras ficavam com o valor da
variante com que a página tinha aberto; a etapa 19 corrigiu.

A quantidade começa em 1 e para no teto da variante, tanto nos botões −/+
quanto no valor digitado. O número final é sempre do servidor: pedir mais que
o estoque traz o máximo disponível e avisa.

### Variantes

`ProductVariant` é a unidade vendável: SKU próprio, cor, tamanho, material,
**preço, estoque, peso, dimensões e prazo de produção**. Nada é herdado do
produto — o valor da variante *é* o valor.

```text
Vaso Facetado                      ← Product: o conceito, sem preço
├── Preto  / 15 cm → € 19,90 → 6 un. → 120 g → 2 dias
├── Branco / 15 cm → € 19,90 → 3 un. → 120 g → 2 dias
├── Roxo   / 15 cm → € 21,90 → 0 un. → 120 g → 2 dias  (aparece esgotada)
├── Preto  / 25 cm → € 27,90 → 4 un. → 300 g → 3 dias
└── Branco / 25 cm → € 27,90 → 2 un. → 300 g → 3 dias
```

**A página sempre abre com uma variante selecionada:** a primeira disponível.
Se todas estiverem esgotadas, a primeira — para a página ainda ter preço e
ficha ao anunciar que acabou. Nenhuma variante fica escondida: as esgotadas
aparecem no seletor, desabilitadas, para o cliente ver que existem.

### A matriz de combinações

Os eixos (cor, tamanho, material) **não** são três listas independentes. Nem
toda combinação existe: no exemplo acima, `Branco + 15 cm` não é uma variante
cadastrada, e portanto não é uma compra possível.

A cada clique, as opções dos **outros** eixos são recalculadas contra as
variantes que existem de verdade. As que não combinam com a escolha atual ficam
**riscadas — e clicáveis**. Desabilitá-las diria "esta opção não existe"; o que
é verdade é "não existe *nesta cor*".

O eixo em que o cliente acabou de clicar **tem prioridade**: o sistema procura a
variante real que melhor o atende, preservando o que der dos outros eixos, e
passa a seleção inteira para ela. Por isso funciona nos dois sentidos:

```text
Preto + 25 cm, clica em "30 cm"   →  vira Branco + 30 cm
Branco + 30 cm, clica em "Preto"  →  vira Preto + 25 cm
```

A seleção nunca passa por um estado inválido, nem por um instante: o clique
calcula a variante final e marca todos os eixos de uma vez.

**O JavaScript é conforto, não autoridade.** O formulário do carrinho recebe os
eixos escolhidos e os confere contra o `variant_id`; um POST que peça uma
combinação inexistente é recusado no servidor, com a mesma resposta para um
navegador sem JavaScript, um script ou um cliente curioso. Nunca se vende "a
variante mais próxima".

Escolher uma opção atualiza **tudo o que é da variante** — preço, selo de
estoque, limite de quantidade, botão, peso, dimensões, tempo de impressão e a
referência — sem recarregar. Sem JavaScript, a escolha é um `<select>` normal;
com JavaScript, o `<select>` continua funcionando (é ele que manda o
`variant_id` no POST, e quem navega por teclado escolhe por ele).

Produto de opção única não mostra seletor: a variante vai num campo oculto.

No Shop, o card de um produto com mais de uma opção mostra **Escolher opções** e
leva à página — adicionar "um vaso" sem saber a cor seria adicionar algo
indefinido. Com preços diferentes, o card mostra o menor com "a partir de".

---

## F5. Personalização

Campo do produto (`personalization_type`), não categoria:

| Tipo | O cliente |
|---|---|
| Nenhuma | compra direto |
| Foto | envia uma imagem (obrigatória) |
| Texto | escreve um texto (obrigatório, com limite por produto) |
| Foto ou texto | escolhe entre os dois |

Configurável em **Catálogo › Produtos › PERSONALIZAÇÃO**, sem tocar em código.

### Segurança do upload

* validação por **assinatura do arquivo** (primeiros bytes), não por extensão —
  um PDF renomeado para `.png` é recusado;
* tamanho máximo de 10 MB (`CUSTOMIZATION_MAX_UPLOAD_SIZE`);
* nome gerado por nós (`uuid4`), o nome do cliente só é guardado para exibição;
* arquivo em `MEDIA_ROOT/customizations/`, nunca em `static/`.

### Onde fica

O arquivo vira uma linha em `cart.CustomizationUpload`; a sessão guarda só o id.
A linha do carrinho passou a ser identificada por
**produto + variante + personalização** — dois textos diferentes do mesmo
produto são duas linhas. É a mesma identidade que `OrderItem` vai precisar.

---

## F6. Idiomas da loja

Duas listas, agora separadas:

| | Onde | Quem muda |
|---|---|---|
| Idiomas **suportados pelo sistema** | `settings.LANGUAGES` (9) | desenvolvimento |
| Idiomas **oferecidos na loja** | tabela `core.SiteLanguage` | administrador |

Em **Núcleo › Idiomas da loja** o administrador liga e desliga cada idioma e
define a ordem do seletor. Hoje estão disponíveis **português, francês,
holandês e inglês**; alemão, espanhol, italiano, turco e árabe seguem
cadastrados e desligados.

* o idioma padrão (português) **não pode** ser desativado nem apagado;
* idioma desligado some do seletor, a troca para ele é recusada e `/de/`
  redireciona para a versão em português;
* ativar um idioma **não exige** ter tudo traduzido — o que faltar cai no
  português.

Nome e nome nativo não são colunas: vêm do próprio Django.

---

## F7. Contas, clientes e carrinho persistente

### Duas entidades, de propósito

```text
User  (conta de acesso)          Customer  (cliente da loja)
├── username                     ├── first_name / last_name
├── email + email_verified       ├── phone
├── password (hash do Django)    ├── company_name
├── preferred_language           └── vat_number
└── is_active                          │
        │  1 : 0..1                    └──> CustomerAddress, Order, Invoice (futuro)
        └────────────────────────────────┘
```

Entrar na loja não pode depender de ter preenchido dados de faturamento, e o
checkout vai precisar de nome, telefone e endereço que não têm nada a ver com
autenticação. Por isso o cadastro público pede **três campos** — usuário,
e-mail e senha — e cria o `Customer` vazio junto; "Meus dados" preenche o
resto depois.

### Unicidade sem diferenciar maiúsculas

`Diego3D`, `diego3d` e `DIEGO3D` são **a mesma conta**; o mesmo vale para o
e-mail. Isso é garantido em três lugares, e os três importam:

| Camada | O que faz | Por que não basta sozinha |
|---|---|---|
| Formulário | mensagem no campo certo | um POST direto não passa por ele |
| `full_clean()` | protege admin e scripts disciplinados | `objects.create()` não chama |
| **Banco** (`UniqueConstraint` sobre `Lower(...)`) | recusa a gravação | é a única que vale para importação, shell e API futura |

O e-mail ainda é normalizado para minúsculas antes de gravar. Funciona igual no
PostgreSQL e no SQLite — nenhum recurso exclusivo de um dos dois.

### Entrar com usuário **ou** e-mail

Um campo só na tela. Quem resolve é um backend próprio
(`accounts/backends.py`) que herda de `ModelBackend`: hashing, permissões e
bloqueio de conta inativa continuam sendo os do Django; só muda **como o
usuário é encontrado**. Senha errada, conta inexistente e conta desativada dão
a **mesma** resposta — três mensagens diferentes seriam três formas de
descobrir quem tem conta na loja.

O username não pode ser um e-mail, justamente para os dois nunca se
confundirem nesse campo único.

### Confirmação de e-mail

Cadastro → conta criada → cliente criado → **autenticado na hora** →
`email_verified = False` → e-mail enviado.

O token é o gerador do Django (HMAC com a `SECRET_KEY`), com sal próprio e um
detalhe importante: o valor assinado inclui o **e-mail** e o
**`email_verified`**. Consequências diretas:

* usar o link marca a conta como confirmada, e o mesmo link **para de valer** —
  uso único sem tabela de tokens gastos;
* trocar o endereço invalida um link pendente;
* `last_login` **não** entra no valor assinado (o padrão do Django inclui):
  como quem se cadastra é autenticado na hora, isso mataria o link a cada
  login.

Quatro desfechos, cada um com a sua tela: confirmado, link já usado, link
expirado (com botão de novo envio) e link inválido. Adulterar `uid`, `token` ou
`email` na URL não confirma conta nenhuma — o token é conferido contra o
usuário que o `uid` aponta, com um segredo que só o servidor tem.

Prazo em um lugar só: `EMAIL_VERIFICATION_TIMEOUT` (24 h por padrão).

### Recuperação de senha

Mecanismo nativo do Django, telas e e-mail nossos. A resposta é sempre a mesma,
exista ou não a conta: *"Se existir uma conta associada a este e-mail,
enviaremos as instruções"*.

### O domínio dos links não vem da requisição

`SITE_URL` monta todo link enviado por e-mail. Usar `request.get_host()` faria
a loja enviar, do próprio domínio, um link para o endereço que o atacante
colocou no cabeçalho `Host`.

### E-mail no idioma do cliente

Quem se cadastra em `/fr/` fica com `preferred_language = fr` e recebe os
e-mails em francês — inclusive com o link já prefixado (`/fr/conta/...`). A
lista de idiomas vem da mesma tabela `core.SiteLanguage` do seletor do header.

### Carrinho: sessão para o visitante, banco para o cliente

```text
visitante        →  sessão do Django          (nada no banco)
autenticado      →  cart.Cart / cart.CartItem (sobrevive ao navegador)
```

O `Cart` (a classe) não sabe qual dos dois está em uso: escolhe um
*armazenamento* no construtor e trabalha sempre com o mesmo dicionário. Foi o
que permitiu acrescentar persistência sem reescrever views, templates nem
regras — e por isso os testes das etapas 3 e 4 continuaram passando sem
alteração.

**Merge no login** (e no cadastro — os dois passam pelo sinal
`user_logged_in`): a identidade da linha continua sendo
**produto + variante + personalização**.

```text
sessão:  A/Preto/25cm × 2   B × 1
conta:   A/Preto/25cm × 1              C × 3
--------------------------------------------
depois:  A/Preto/25cm × 3   B × 1      C × 3
```

Estoque **não** é reservado: a soma é limitada ao disponível agora, como
qualquer outra escrita no carrinho, e aí sim o cliente é avisado. Um carrinho
que simplesmente veio junto do cadastro não gera aviso nenhum — o contador do
header já conta essa história.

Um usuário tem **no máximo um** carrinho: `OneToOne`, garantido pelo banco e
não por uma regra que todo mundo precisa lembrar de aplicar.

### Aviso discreto para o visitante

Com itens no carrinho e sem conta, a gaveta mostra, acima do subtotal:

> 💡 **Não perca seu carrinho** — Crie uma conta ou entre para mantê-lo
> disponível mesmo se trocar de dispositivo. **[ Criar conta ] [ Entrar ]**

Sem pop-up, sem bloqueio: comprar sem cadastro continua funcionando igual.

### Rotas

| URL | O que é |
|---|---|
| `/conta/` | painel (estado do e-mail, acesso, dados, carrinho) |
| `/conta/entrar/` · `/conta/criar/` · `/conta/sair/` | acesso (sair é POST) |
| `/conta/dados/` | dados do cliente + idioma preferido |
| `/conta/confirmar/<uid>/<token>/` | confirmação de e-mail |
| `/conta/confirmar/reenviar/` | novo envio (com trava de tempo) |
| `/conta/senha/…` | recuperação de senha (4 telas) |

### Admin

Dois cadastros separados, espelhando a arquitetura: **Contas › Usuários**
(acesso, confirmação, idioma, permissões, com os dados do cliente num inline) e
**Contas › Clientes** (só o comercial, com link para a conta). A senha nunca
aparece — o Django mostra apenas o resumo do hash e um botão para trocá-la.

**Carrinho › Carrinhos** lista os carrinhos persistentes com dono, linhas,
unidades e datas: é a base do futuro lembrete de carrinho abandonado.

---

## F8. Endereços e países de entrega

### Onde a loja entrega

`core.DeliveryCountry` — **configuração da loja**, ao lado de `SiteLanguage`.
Guarda o código ISO 3166-1 alfa-2, se está ativo, a ordem no checkout e a
**alíquota de TVA** do país. O nome vem de uma tabela de tradução, a mesma
mecânica do catálogo: um cliente francês vê "Belgique".

Oito países já vêm cadastrados (migration `core/0004`); BE, FR, NL e LU ativos.
Ligar a Alemanha é um clique no admin — nunca uma alteração de código. Desligar
um país o tira do checkout e do cadastro de endereços na mesma requisição;
endereços já salvos continuam visíveis na conta, mas o checkout os recusa com
mensagem clara.

### Endereços do cliente

`accounts.CustomerAddress` — tabela separada, nunca colunas dentro de
`Customer`. O cliente tem quantos quiser, com dois padrões independentes
(entrega e faturamento) garantidos por **constraint parcial no banco**, não por
um `if` na view.

Nome e sobrenome ficam no endereço e **não** são lidos do `Customer`: quem
recebe pode não ser quem compra. É isso — e só isso — que faz "enviar como
presente" funcionar sem um segundo sistema de endereços.

| Ação | URL | Método |
|---|---|---|
| listar | `/conta/enderecos/` | GET |
| criar | `/conta/enderecos/novo/` | GET/POST |
| editar | `/conta/enderecos/<id>/` | GET/POST |
| remover | `/conta/enderecos/<id>/remover/` | GET/POST |
| definir padrão | `/conta/enderecos/<id>/padrao/` | **POST** |

Toda consulta é filtrada pelo dono (`customer=self.customer`): o id vem da URL,
e id de URL não prova nada. Outro cliente recebe 404 — nunca 403, que
confirmaria a existência do endereço.

---

## F9. Frete

Três níveis, para caber qualquer transportadora sem tocar em código:

```text
ShippingCarrier   Bpost                     (+ URL de rastreio)
  └── ShippingMethod   Standard  2–3 dias   (prazo de TRANSPORTE)
        └── ShippingRate   BE  0–2 kg  € 5,90
        └── ShippingRate   BE  2–5 kg  € 7,90
        └── ShippingRate   FR  0–2 kg  € 9,90
```

**Faixas de peso:** limite inferior inclusivo, superior **exclusivo**. 0–2000 e
2000–5000 não se sobrepõem e 2000 g cai na segunda faixa; sem essa convenção
toda tabela precisaria ser cadastrada com 1999. Faixa sem teto (`max` vazio) é
a última da tabela. O `clean()` recusa faixas sobrepostas: duas faixas cobrindo
o mesmo peso dariam dois preços para o mesmo pedido.

**Peso do pedido:** vem do catálogo — `variant.effective_weight` quando há
variante, `product.weight_grams` quando não. Nada específico de impressão 3D
entra na conta: o mesmo cálculo serve para um vaso, um rolo de filamento ou uma
impressora.

**Prazo mostrado ao cliente = produção + transporte.** A produção é o **maior**
prazo entre os itens, não a soma: a oficina imprime em paralelo, e somar daria
um prazo que nunca acontece. Produção 3 dias + transporte 2–3 = "5–6 dias
úteis".

---

## F10. Pedidos e checkout

### A regra de entrada

Navegar, montar carrinho e mudar quantidade continuam abertos a qualquer
visitante. **Fechar a compra exige conta** — um pedido precisa de dono, de
endereço e de um lugar para ser acompanhado. `/carrinho/finalizar/` (a URL de
sempre) responde 200 para todo mundo: o visitante vê o resumo do pedido e o
convite para entrar, em vez de um redirecionamento seco que perde a compra.

### O que acontece ao clicar em "Pagar"

```text
1. revalidar produto, variante, preço, estoque e personalização — AGORA
2. recalcular frete e imposto no servidor (nada vem do formulário)
3. copiar os dois endereços para o pedido
4. criar Order + OrderItem, em UMA transação
5. criar a sessão de pagamento na Stripe
6. esvaziar o carrinho (só aqui, com o pedido já gravado)
7. redirecionar para a Stripe
```

O estoque **não** é reservado em nenhum desses passos. Ver F11.

### Cinco eixos, não um

| Campo | Valores | Responde |
|---|---|---|
| `status` | `pending` · `confirmed` · `completed` · `cancelled` | o pedido está de pé? |
| `payment_status` | `pending` · `paid` · `failed` · `not_charged` | o dinheiro entrou? |
| `fulfillment_status` | `not_started` · `in_production` · `ready` · `shipped` · `delivered` · `halted` | onde a peça está? |
| `cancellation_status` | `none` · `requested` · `approved` · `refused` | há um pedido de cancelamento esperando decisão? |
| `refund_status` | `none` · `pending` · `partially_refunded` · `refunded` | o dinheiro voltou? |

São perguntas diferentes, e por isso são campos diferentes. Um pedido pago que
ainda não saiu da oficina é `confirmed` + `paid` + `in_production`; com um campo
só, esse estado não teria nome.

**`status` é derivado.** Ele é consequência dos outros — cancelado vence
entregue, que vence pago — e quem o escreve é `services.recompute_status`,
nunca o formulário do Admin. Enquanto era um `select`, era possível gravar
"cancelado" num pedido e deixar a oficina imprimindo a peça.

**Reembolso não é estado de pagamento.** Um pedido reembolsado continua tendo
sido pago, e apagar isso perderia a informação de que houve cobrança. Daí o
campo próprio — e o `not_charged`, que é a resposta certa para um pedido
cancelado antes de pagar: ele não está "pendente", ninguém espera esse dinheiro.

`halted` é a produção interrompida por um cancelamento aprovado. É terminal e
não se escolhe na tela: quem o escreve é a aprovação, junto com a decisão sobre
o estoque e a abertura do reembolso.

### Numeração

`JD-2026-000001`. O número sai de `OrderNumberSequence`, uma linha por ano lida
com `SELECT ... FOR UPDATE`: dois checkouts simultâneos não recebem o mesmo
número, e o número não é o PK (que vazaria o volume da loja e mudaria numa
importação de dados).

### Snapshots — a regra que sustenta o app inteiro

Um pedido antigo **não pode mudar porque o catálogo mudou**. São copiados no
momento da compra:

* **item** — nome, SKU, rótulo da variante, cor, tamanho, material, quantidade,
  preço unitário, total, peso, tipo de atendimento, prazo de produção e a
  personalização inteira (tipo, texto, observações, arquivo);
* **endereços** — entrega e faturamento, em `OrderAddress`, com o nome do país
  no idioma do pedido;
* **entrega** — nome da transportadora e do método, prazos, peso;
* **dinheiro** — moeda, subtotal, frete, alíquota, imposto e total.

`product`/`variant`/`upload` continuam referenciados com `PROTECT` — para o
admin abrir a ficha e para o arquivo do cliente não sumir. Catálogo se
**desativa** (`status = inactive`), não se apaga.

### TVA

**Os preços do catálogo já incluem TVA** — no varejo B2C da UE o preço
anunciado tem que ser o preço final. Então a conta não é somar imposto, é
separar o que já está dentro:

```text
imposto = bruto × taxa / (100 + taxa)
```

A alíquota é a do país de **destino** (regime de venda a distância B2C, OSS),
vem de `DeliveryCountry.vat_rate` e é **copiada para o pedido**: mudança de
alíquota amanhã não reescreve a fatura de hoje. O frete é tributado junto com
os bens, que é o tratamento normal de uma entrega acessória.

### Cancelamento

O cliente **solicita**; quem decide é a loja. A solicitação entra em
`cancellation_status = requested` com o motivo, aparece no admin e é resolvida
por ação explícita (aprovar/recusar), com decisão e motivo no histórico.
Aprovar cancela o pedido e **não** emite reembolso — reembolso é ação separada,
feita no painel da Stripe.

---

## F11. Pagamento com Stripe

> **Hoje a loja cobra por transferência bancária** (`PAYMENT_PROVIDER=transfer`),
> uma solução **provisória** até o gateway entrar. O pedido é registrado como
> pendente, a loja recebe um aviso e envia os dados bancários à mão; quem marca
> o pedido como pago é uma pessoa, no Admin. A conta que recebe é cadastrada em
> *Admin › Pedidos › dados para transferência*.
>
> Os dois meios convivem no mesmo registro (`apps/orders/payments/`) e a troca
> é uma linha no `.env`. Nenhum pedido antigo muda: cada `Payment` guarda o
> provedor que o criou. O que a seção abaixo descreve continua valendo para
> quando `PAYMENT_PROVIDER=stripe`.

### Stripe Checkout hospedado

O cliente digita o cartão **no domínio da Stripe**. Nenhum dado de cartão passa
pelo nosso servidor nem pelo nosso JavaScript, o que mantém a loja no escopo
PCI mais simples (SAQ-A). A Stripe também resolve o 3-D Secure exigido pelo
PSD2, oferece Bancontact/iDEAL conforme o país e traduz a própria página para
PT/FR/NL/EN. O Payment Element daria mais controle visual em troca de um fluxo
de pagamento inteiro em JavaScript — o oposto do que este projeto é.

### O webhook é a autoridade

```text
cliente → Stripe → webhook → JD PRINT
                              ├── Payment = succeeded
                              ├── Order   = confirmed / paid / in_production
                              ├── baixa de estoque (uma vez)
                              └── e-mails (cliente + produção)
```

`/pedido/<numero>/confirmacao/` **não confirma nada**: é uma URL que qualquer
pessoa pode abrir, e o cliente pode fechar o navegador antes de voltar. Ela lê
o estado que o webhook já gravou; enquanto ele não chega, mostra "estamos
confirmando".

### Idempotência, em dois pontos

* **ao cobrar** — chave de idempotência da própria Stripe, para um duplo clique
  não virar duas cobranças;
* **ao processar** — `WebhookEvent` grava o `evt_...` com **unicidade no
  banco** antes de aplicar o evento. A segunda entrega esbarra na constraint e
  sai por 200 sem fazer nada. Vale inclusive para duas entregas simultâneas,
  porque quem decide é o banco e não um `if`.

Se o processamento falhar, o registro é apagado e devolvemos 500 — a Stripe
reenvia e a nova entrega tenta de novo.

### Segurança do endpoint

`/pagamento/stripe/webhook/` fica **fora do `i18n_patterns`** (a Stripe não tem
idioma) e é o **único** lugar do projeto com `csrf_exempt`. O CSRF protege
contra o navegador do cliente ser induzido a postar; quem chama esta URL não é
um navegador. A autenticação certa aqui é a assinatura `Stripe-Signature` —
HMAC sobre o corpo bruto. Sem `STRIPE_WEBHOOK_SECRET` configurado, **nada**
passa.

### Estoque

Carrinho não é compromisso: nada é reservado quando o produto entra nele, nem
quando o pedido é criado. A baixa acontece **uma vez**, na confirmação do
pagamento, com `select_for_update`. Se faltar estoque nesse ponto o dinheiro já
entrou — recusar seria pior para o cliente do que avisar a produção: o saldo
nunca fica negativo, a falta entra no histórico e vira nota interna para
alguém decidir.

### Configuração

```ini
PAYMENT_PROVIDER=stripe
STRIPE_SECRET_KEY=sk_test_...        # dashboard.stripe.com/apikeys
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...      # dashboard.stripe.com/webhooks
STRIPE_SESSION_EXPIRES_IN=1800
```

Em desenvolvimento, para receber os eventos:

```powershell
stripe listen --forward-to 127.0.0.1:8000/pagamento/stripe/webhook/
```

**Sem `STRIPE_SECRET_KEY` a loja funciona inteira** — só o passo de pagamento
avisa que o meio de pagamento está indisponível. É o que evita tanto uma chave
de teste esquecida em produção quanto um checkout que finge funcionar sem
gateway.

---

## F12. E-mails do pedido

Três, e só três — um cliente não precisa de uma sequência de sete mensagens.

| Quando | Para | O que leva |
|---|---|---|
| pagamento confirmado | cliente | itens, variantes, personalização, os dois endereços, método, prazo, subtotal, frete, TVA, total |
| pagamento confirmado | `ORDER_ADMIN_EMAILS` | tudo acima **mais** SKU, link do arquivo enviado, prazo por item, peso, telefone e observação do cliente |
| marcado como enviado | cliente | transportadora, código e link de rastreio |

O idioma é o **do pedido** (`Order.language`, gravado na compra), não o da
requisição que dispara o envio: quem comprou em `/fr/` recebe em francês mesmo
que o disparo venha do admin em português — e continua recebendo em francês
três meses depois. O e-mail administrativo sai sempre em português: quem lê é
a equipe.

Cada e-mail é enviado **uma vez** (`confirmation_email_sent_at`,
`shipped_email_sent_at`), e uma falha do provedor de e-mail nunca desfaz um
pagamento — ela vai para o log.

---

## F13. Páginas institucionais

Quatro páginas, um model e um template. As rotas ficam na raiz de cada idioma:

| Página | Endereço | O que tem |
|---|---|---|
| Envios e prazos | `/envios-e-prazos/` | só texto |
| Trocas e devoluções | `/trocas-e-devolucoes/` | só texto |
| Contato | `/contato/` | texto + formulário |
| Seja um revendedor | `/revenda/` | texto + chamada para o contato |

### O conteúdo é cadastrado, e já vem escrito

`storefront.InstitutionalPage` guarda `slug`, se está publicada, se aparece no
rodapé e a ordem; `InstitutionalPageTranslation` guarda título, chamada, corpo
e a meta description em **pt / fr / nl / en** — o mesmo par
`TranslationBase` + `TranslatableMixin` do resto do projeto, com o mesmo
fallback por campo (idioma atual → pt → qualquer → padrão).

O slug vem de uma lista fechada (`PageSlug`), não de texto livre: cada página
tem uma rota própria, e um slug inventado no Admin criaria conteúdo escrito que
ninguém consegue abrir.

O texto inicial das quatro páginas, nos quatro idiomas, é cadastrado pela
migration `storefront/0005_seed_institutional_content`. Não é conteúdo fixo: é
ponto de partida, e o Admin reescreve por cima sem tocar em código. O que a
migration **não** escreve é prazo em dias, prazo legal de desistência ou
percentual de revenda — nada disso está definido no projeto, e número em página
institucional é compromisso que a loja não assumiu.

Despublicar (`is_active=False`) devolve 404 e tira o link do rodapé.
Desmarcar "mostrar no rodapé" tira só o link.

O corpo aceita uma marcação mínima — `# ` vira subtítulo, `- ` vira item de
lista, linha em branco separa parágrafos — resolvida na hora de exibir pela tag
`{% rich_text %}`, que **escapa tudo**: HTML colado no Admin aparece como
texto, nunca é executado.

### Um formulário, não dois

O contato pergunta nome, e-mail, assunto e mensagem, grava em
`storefront.ContactMessage` **antes** de o e-mail sair e aparece no Admin como
caixa de entrada em modo leitura, com marcação de "tratado". Provedor de e-mail
fora do ar atrasa o aviso da equipe; não apaga o pedido do cliente.

A revenda é **informativa**: apresenta o programa e termina numa chamada para o
contato. Um formulário próprio, com empresa/país/tipo de negócio e caixa de
entrada separada, já seria um sistema de revendedores — fora do escopo desta
etapa. Quem decide que a revenda chama o contato é `PAGE_CTA`, no model, não o
template.

O aviso vai para `EmailSettings.contact_recipients` quando preenchido, e para
quem já recebe os pedidos quando não — e o `Reply-To` é o e-mail de quem
escreveu, então responder é apertar "responder".

O formulário tem um campo-armadilha (`website`) escondido por CSS, fora da
ordem de tabulação e `aria-hidden`: robô preenche, cliente e leitor de tela não
veem. Não é captcha — é higiene, e sem atrito para quem é de verdade.

### O rodapé aponta para a página, não para um endereço

`FooterLink` ganhou `page`: uma referência à página da loja, no lugar de uma
string. Com `i18n_patterns`, a mesma página mora em `/contato/` e em
`/fr/contato/` — um endereço digitado à mão mandaria o visitante francês para a
página portuguesa. O `url` continua existindo, para o que é de fora.

Sem rótulo próprio, o texto do link é o **título da página**: o nome é escrito
num lugar só, e traduzir a página traduz o rodapé junto. Um link cuja página
está despublicada ou fora do rodapé simplesmente não aparece — senão o rodapé
levaria a um 404.

A coluna "Informações", com as quatro páginas, é cadastrada pela mesma
migration de conteúdo. Vale a regra da etapa 16 sem exceção: o rodapé é o que o
Admin cadastrou, e apagar a coluna a faz sumir.

---

## G. Design System e identidade visual

A loja é **creme, roxa e arredondada**: fundo creme (nunca branco), cartões
claros pousados por cima, cantos generosos e sombras que quase não se veem. O
roxo é a cor de quem decide — só ele veste botão de ação. Amarelo, menta e
coral entram como acento, sempre em fundo suave com texto escuro por cima.

O listrado fino de 7px (`.layers`) continua sendo a assinatura da marca: a peça
impressa nasce camada por camada, e ele aparece nas molduras e nos blocos
escuros. O resultado pretendido é amigável e criativo sem deixar de ser
profissional.

### Onde ficam os tokens

Tudo em **`static/src/input.css`**, no bloco `@theme`. Nenhuma cor, fonte,
raio ou sombra é escrita à mão no template — quem quiser mudar a identidade
mexe num arquivo só e roda `npm run build:css`:

| Grupo | Tokens | Uso no template |
|---|---|---|
| Marca | `--color-brand-50` … `--color-brand-950` (600 = `#4a1a8c`, a cor de ação) | `bg-brand-600`, `text-brand-700` |
| Texto | `--color-ink`, `-soft`, `-muted`, `-on-deep` | `text-ink-soft` |
| Superfícies | `--color-surface` (`#faf8f4`), `-soft`, `-brand`, `-deep` | `bg-surface` |
| Borda | `--color-surface-line` (`#e8e3da`), `-strong`; `--border-hairline`, `--border-strong` | `border-surface-line` |
| Acentos | `yellow`, `mint`, `coral` (50…700) | `bg-mint-100`, `text-coral-700` |
| Estados | `--color-state-success \| warning \| error` (+ `-dark`, `-soft`) | `text-state-error` |
| Famílias | `--font-display`, `--font-sans`, `--font-mono` | `font-display` |
| Pesos | `--font-weight-title-soft \| title \| title-strong \| ui \| ui-strong` | `font-title` |
| Escala de título | `--text-display-sm \| display \| -lg \| -xl`, `--tracking-display` | `text-display-lg` |
| Forma | `--radius-control` (12px), `-pill` (9999px), `-card` (22px), `-block` (28px), `-panel` (36px) | `rounded-card` |
| Profundidade | `--shadow-card`, `--shadow-lift`, `--shadow-action` | `shadow-card` |
| Transição | `--default-transition-duration/-timing-function`, `--ease-soft`, `--ease-entrance`, `--transition-fast \| base \| slow` | `transition` |

O bloco é `@theme static`: o Tailwind publica **todas** as variáveis acima, e
não só as que alguma classe já usa. Sem isso um token recém-criado existiria no
código-fonte e não no CSS servido, e quem o consumisse fora de uma classe
utilitária receberia vazio.

Sobram dois apelidos, `cyan-100/700` e `magenta-100/700`, e por um motivo só:
`HomeCard.accent` guarda a string `"cyan"` ou `"magenta"` **no banco**, e o
template monta a classe com o valor gravado (`bg-{{ card.accent }}-100`). Trocar
isso pede migração de esquema e de dados. `indigo` saiu por completo, e todo o
resto do site usa `mint`, `coral` e `yellow`.

O Tailwind varre `templates/`, `apps/` **e `static/js/`** — o seletor de
variante remonta o selo de estoque pelo JavaScript, e sem varrer o JS aquelas
classes não entrariam no CSS. E `@source not` exclui o próprio `tailwind.css`:
sem isso o build lê a saída anterior, encontra ali uma classe já removida e a
regera, para sempre.

Um teste (`core/tests_templates.py::DesignTokenTests`) falha se alguém escrever
`#rrggbb` num template da interface. Há duas exceções, e as duas são exceções
de verdade:

* **Os e-mails** (`templates/emails/`). Caixa de entrada não carrega folha de
  estilo externa, então ali a cor vai em atributo `style`, escrita à mão. É a
  mesma paleta do `@theme` — creme, navy, roxo e os três acentos —, mas
  duplicada por necessidade: não há como um e-mail ler a variável de lá. O
  que sobrevive ao Outlook manda nessa pasta, não o que é elegante no site.
* **A cor de uma variante**, que é dado do banco e entra num `style` inline. A
  cor de reserva de quando não há `hex_code` cadastrado NÃO é exceção: mora no
  utilitário `color-swatch`, no Design System.

### Componentes

Declarados no mesmo arquivo, como `@utility` (quando precisam ser compostos com
`@apply`) ou em `@layer components`: `btn-primary` / `-secondary` / `-ghost` /
`-soft` / `-dark` / `-mint` / `-danger` (+ `btn-sm`, `btn-lg`), `card`,
`card-hover`, `panel`, `alert-*`, `chip`, `field-input`, `form-label` /
`-help` / `-error`, `empty-state`, `account-link`, `shop-filter`, `page-link`,
`variant-option`, `product-grid`, `carousel-track`, `toast`, `layers`,
`grid-mesh`, `focus-ring`.

**Três degraus de título**, e um por papel: `page-title` (um por documento),
`section-title` (a faixa) e `card-title` (o bloco dentro dela). Antes os dois
primeiros dividiam uma classe só, e o `<h1>` da vitrine tinha o tamanho de um
subtítulo de faixa.

**As etiquetas dizem o papel, não a tinta**: `badge-success`, `-warning`,
`-danger`, `-info`, `-accent` (+ `-accent-solid`), `badge-brand` / `-brand-soft`
e `badge-tech`. Os nomes anteriores eram a cor de quando foram criadas —
`badge-mint` era verde e `badge-muted` era vermelho —, então trocar a tinta de
um estado obrigava a reescrever template. Agora não.

### Tipografia

Três famílias, **hospedadas no próprio servidor** (`static/fonts/`, 187 KB no
total, só os intervalos latin e latin-ext): **Baloo 2** nos títulos, **Nunito**
no corpo e na interface, **IBM Plex Mono** nas etiquetas, referências e
números. Nada é buscado no Google em tempo de execução — uma requisição externa
a menos e nenhum IP de cliente europeu entregue a terceiros.

Baloo 2 e Nunito são variáveis: **um** arquivo por subconjunto cobre de 400 a
800, e sai mais leve que os pesos estáticos separados. Títulos vivem em
600/700/800; corpo e interface, em 400/600/700/800 — o peso 500 não é usado de
propósito, porque a diferença para o 400 não se lê.

Quem baixa e gera é `scripts/fetch_fonts.py`, que reescreve
`static/src/fonts.css` inteiro. Rode-o ao trocar de família e mande junto no
commit os `.woff2` que ele gravar: o servidor não roda script de build.

### A logo

`static/images/logo/jdprint-logo.svg` é **o arquivo oficial e é usado como
está** — sem redesenho, sem mudar proporção. A tag `{% brand_logo_url %}`
procura o arquivo; se ele sumir, aparece uma moldura tracejada reservada, nunca
uma logo inventada. Veja `static/images/logo/LEIA-ME.md`.

---

## H. Como executar

### 0. Requisitos e primeiro clone

| Requisito | Versão | Obrigatório? |
|---|---|---|
| Python | **3.13** | sim — ver a explicação em H.1 |
| PostgreSQL | 14+ | sim em produção; em desenvolvimento dá para adiar (H.3) |
| Node.js | 20+ | só para rebuildar o CSS (H.2); o `tailwind.css` já vem pronto |
| Git | qualquer | sim |

```powershell
git clone https://github.com/joubertdiegors/site-loja.git
cd site-loja
copy .env.example .env
```

**O `.env` é o primeiro passo, não o último.** Ele não vem no repositório —
por decisão de segurança, nunca virá — então uma instalação nova começa sem
nenhuma configuração. Copie o `.env.example`, que documenta as 45 variáveis
que o `config/settings.py` lê, e preencha o que a sua máquina precisa.

No mínimo, ajuste antes de rodar qualquer coisa:

```ini
DJANGO_SECRET_KEY=     # gere uma sua (comando abaixo)
DJANGO_DEBUG=True      # desenvolvimento
DJANGO_DB_ENGINE=      # postgres, ou sqlite enquanto o Postgres não existir
```

Para gerar uma `SECRET_KEY` nova:

```powershell
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

### 1. Ambiente e dependências Python

**A versão do Python é 3.13 — não é preferência, é o teto da hospedagem.** O
PythonAnywhere (system image `innit`, Ubuntu 22.04) oferece no máximo o Python
3.13; não existe 3.14 em nenhuma imagem deles. Desenvolver numa versão que a
produção não tem é testar num carro e entregar noutro. O arquivo
`.python-version` na raiz registra esse alvo.

```powershell
py -3.13 -m venv venv
.\venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Se o `py -3.13` reclamar que a versão não existe, instale com `py install 3.13`.

O Django 6.1 continua valendo: ele aceita Python 3.12, 3.13 e 3.14, e o
PythonAnywhere não impõe versão de Django nenhuma — o framework vive dentro da
virtualenv do projeto. Rebaixar para o 5.2 LTS custaria código: o
`config/settings.py` usa `MAILERS`, que só existe a partir do Django 6.0.

### 2. CSS (Tailwind)

Precisa do Node.js. Só é necessário quando você **mexe nos templates ou no
tema** — o CSS gerado (`static/css/tailwind.css`) está versionado.

```powershell
npm install
npm run build:css      # gera o CSS uma vez
npm run watch:css      # regenera a cada alteração (durante o desenvolvimento)
```

### 3. Banco de dados

O banco oficial é o **PostgreSQL**. Enquanto ele não estiver instalado, o
`.env` está com `DJANGO_DB_ENGINE=sqlite`. Para migrar depois:

```ini
DJANGO_DB_ENGINE=postgres
POSTGRES_DB=jdprint
POSTGRES_USER=jdprint
POSTGRES_PASSWORD=jdprint
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

```sql
CREATE DATABASE jdprint;
CREATE USER jdprint WITH PASSWORD 'jdprint';
ALTER DATABASE jdprint OWNER TO jdprint;
ALTER USER jdprint CREATEDB;   -- necessário para rodar os testes
```

### 4. Migrations e superusuário

```powershell
python manage.py migrate
python manage.py createsuperuser
```

> **Atenção — banco recriado na etapa 5.** A troca para um *custom user model*
> (`AUTH_USER_MODEL = "accounts.User"`) exige histórico de migrations limpo. O
> banco de desenvolvimento foi recriado e o catálogo foi restaurado; o
> superusuário anterior não sobreviveu. Se você tinha outro banco, exporte o
> catálogo antes (`dumpdata categories catalog home core`), apague o arquivo,
> rode `migrate`, crie o superusuário e importe de volta (`loaddata`).

### 5. Traduções da interface

Os `.mo` já estão compilados. Depois de editar um `.po`:

```powershell
python scripts\compile_messages.py
```

(`manage.py compilemessages` exige o binário `gettext`, que não vem no Windows;
o script faz o mesmo em Python puro.)

### 5b. Variáveis novas (opcionais)

```ini
SHOP_MODELS_CATEGORY_SLUG=modelos   # categoria raiz da vitrine /modelos/
SHOP_PAGE_SIZE=12                   # produtos por página no Shop (padrão)
SHOP_PAGE_SIZE_OPTIONS=4,8,12,16,20,24   # opções oferecidas ao cliente
CUSTOMIZATION_MAX_UPLOAD_SIZE=10485760   # 10 MB por foto de personalização
CUSTOMIZATION_NOTES_MAX_LENGTH=500
CART_MAX_QUANTITY_PER_LINE=99       # teto de unidades por linha do carrinho

# Contas e e-mail (etapa 5)
SITE_URL=http://127.0.0.1:8000      # domínio usado nos links enviados por e-mail
DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DJANGO_DEFAULT_FROM_EMAIL=JD PRINT <nao-responda@jd-print.com>
EMAIL_VERIFICATION_TIMEOUT=86400    # validade do link de confirmação (24 h)
EMAIL_VERIFICATION_RESEND_INTERVAL=120   # intervalo mínimo entre reenvios
EMAIL_VERIFICATION_IP_INTERVAL=60   # janela da cota por IP
ACCOUNT_EMAIL_IP_LIMIT=5            # e-mails de conta por IP dentro da janela
PASSWORD_RESET_TIMEOUT=86400
SESSION_COOKIE_SECURE=False         # em produção HTTPS: True
CSRF_COOKIE_SECURE=False            # em produção HTTPS: True
```

Em desenvolvimento os e-mails saem no **terminal** (backend de console): o link
de confirmação aparece no console do `runserver`.

### 5c. Pagamento (Stripe)

As chaves **nunca** ficam no código. Pegue-as em
[dashboard.stripe.com/apikeys](https://dashboard.stripe.com/apikeys) e o
segredo do webhook em
[dashboard.stripe.com/webhooks](https://dashboard.stripe.com/webhooks):

```ini
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
ORDER_ADMIN_EMAILS=pedidos@jd-print.com
```

Para receber os eventos em desenvolvimento (CLI da Stripe):

```powershell
stripe listen --forward-to 127.0.0.1:8000/pagamento/stripe/webhook/
```

Sem `STRIPE_SECRET_KEY` a loja continua inteira — só o passo de pagamento
avisa que está indisponível.

### 5d. Frete e países

Nada de frete vem escrito no código. No Admin:

1. **Entrega › Transportadoras** — crie a transportadora (e a URL de rastreio);
2. **Entrega › Métodos de entrega** — o que o cliente escolhe, com o prazo de
   transporte em dias úteis;
3. **Entrega › Tarifas** (ou o inline do método) — país + faixa de peso + preço;
4. **Configuração › Países de entrega** — ligue os países e confira a alíquota.

Sem tarifa cadastrada para o destino, o checkout diz isso ao cliente em vez de
mostrar um preço inventado.

### 6. Servidor

```powershell
python manage.py runserver
```

* Home: <http://127.0.0.1:8000/>
* Shop de Modelos: <http://127.0.0.1:8000/modelos/>
* Carrinho: <http://127.0.0.1:8000/carrinho/>
* Conta: <http://127.0.0.1:8000/conta/entrar/>
* Admin: <http://127.0.0.1:8000/admin/>

> Ao editar templates, **não use `--noreload`**: desde o Django 5.1 o cache de
> templates fica ligado em desenvolvimento e é o auto-reloader que o limpa.

### 7. Dados de demonstração (opcional)

```powershell
python manage.py seed_demo_data      # cria catálogo + banner + 4 seções
python manage.py seed_demo_data --remover
```

Regras do comando: nunca sobrescreve nada existente, usa SKUs com prefixo
`DEMO-`, **não cria imagens nem vendas fictícias** e se recusa a rodar com
`DEBUG=False` sem `--forcar`.

### 8. Arquivos estáticos

Em desenvolvimento, com `DEBUG=True`, o Django serve `static/` sozinho e não é
preciso fazer nada. Em produção é obrigatório reunir tudo num só lugar:

```powershell
python manage.py collectstatic --noinput
```

O comando copia `static/`, os assets do Django Admin e o que vier dos apps para
`staticfiles/` (o `STATIC_ROOT`). Essa pasta é **gerada** e não é versionada —
refaça-a a cada deploy.

Note a diferença, porque confundir as duas é a causa mais comum de "o CSS
sumiu em produção":

| Pasta | O que é | Versionada? |
|---|---|---|
| `static/` | **origem** — o que você escreve e edita | sim |
| `staticfiles/` | **destino** do `collectstatic` | não |

### 9. Produção

O deploy no PythonAnywhere tem um passo a passo próprio, mantido fora do
repositório junto com o resto do material de operação. Em linhas gerais: `git
pull`, `pip install -r requirements.txt`, `manage.py migrate`, `manage.py
collectstatic`, o mapeamento das pastas públicas de `media/` no proxy da
hospedagem (`products/`, `banners/` e `brand/` — nunca `media/` inteiro) e o
reload da aplicação.

---

## I. Testes

```powershell
python manage.py test               # tudo
python manage.py test apps.home     # só a Home
```

**1784 testes**, todos passando.

| Área | Arquivo | Testes |
|---|---|---|
| custo, margem, preço | `catalog/tests/test_pricing.py` | 12 |
| produto: SKU, slug, validações, estoque, relações | `catalog/tests/test_product.py` | 45 |
| conteúdo multilíngue do produto | `catalog/tests/test_translations.py` | 10 |
| mídia, imagem principal, tipos | `catalog/tests/test_media.py` | 10 |
| admin do produto | `catalog/tests/test_admin.py` | 8 |
| categorias, hierarquia, ciclos | `categories/tests.py` | 12 |
| models da Home (validação, CTA, ordem, tradução) | `home/tests/test_models.py` | 31 |
| resolução das seções | `home/tests/test_services.py` | 21 |
| página inicial, idiomas, imagens, consultas | `home/tests/test_views.py` | 43 |
| admin das seções e banners | `home/tests/test_admin.py` | 19 |
| comando de demonstração | `core/tests_seed.py` | 9 |
| normalização de idioma | `core/tests.py` | 3 |
| **troca de idioma (matriz completa), persistência, fallback** | `core/tests_i18n.py` | 37 |
| **sanidade dos templates** (nada de sintaxe crua na página) | `core/tests_templates.py` | 3 |
| **Shop: escopo, filtros, ordenação, paginação, idiomas, HTMX** | `catalog/tests/test_shop.py` | 51 |
| **carrinho (a classe `Cart`)** | `cart/tests/test_cart.py` | 28 |
| **carrinho (páginas e ações, com e sem HTMX)** | `cart/tests/test_views.py` | 28 |
| **variantes** | `catalog/tests/test_variants.py` | 24 |
| **página de produto** (galeria, estoque, traduções, variantes, consultas) | `catalog/tests/test_product_page.py` | 34 |
| **quantidade por página** | `catalog/tests/test_page_size.py` | 20 |
| **personalização** (foto, texto, escolha, uploads) | `cart/tests/test_personalization.py` | 28 |
| **gaveta do carrinho** | `cart/tests/test_drawer.py` | 17 |
| **idiomas da loja** | `core/tests_site_languages.py` | 23 |
| **nome de usuário** (formato, reservados, unicidade nas 3 camadas) | `accounts/tests/test_username.py` | 17 |
| **e-mail** (obrigatório, normalizado, único sem caixa, troca) | `accounts/tests/test_email.py` | 12 |
| **login** (username, e-mail, caixa, senha, conta inativa, logout) | `accounts/tests/test_login.py` | 17 |
| **cadastro** (conta, cliente, login automático, carrinho, idioma) | `accounts/tests/test_registration.py` | 19 |
| **confirmação de e-mail** (token, prazo, uso único, reenvio) | `accounts/tests/test_verification.py` | 22 |
| **recuperação de senha** | `accounts/tests/test_password_reset.py` | 14 |
| **cliente separado da conta** | `accounts/tests/test_customer.py` | 13 |
| **admin de contas e clientes** | `accounts/tests/test_admin.py` | 12 |
| **fluxo completo** (visitante → conta → confirmação → login) | `accounts/tests/test_integration.py` | 7 |
| **carrinho persistente e merge** | `cart/tests/test_persistent_cart.py` | 28 |
| **área do cliente** (acesso, senha, troca de e-mail, telas preparadas) | `accounts/tests/test_account_area.py` | 20 |
| **Design System e telas da conta** (tokens, sintaxe crua, tradução) | `core/tests_templates.py` | +4 |
| **barra do Shop no HTMX** (troca out-of-band) | `catalog/tests/test_shop.py` | +2 |
| **endereços do cliente** (padrões, países ativos, autorização) | `accounts/tests/test_addresses.py` | 21 |
| **frete** (faixas de peso, países, transportadoras, prazos) | `shipping/tests/test_rates.py` | 24 |
| **TVA** (imposto embutido, alíquota por país) | `orders/tests/test_taxes.py` | 14 |
| **pedido** (numeração, snapshots, estoque, cancelamento) | `orders/tests/test_orders.py` | 56 |
| **checkout** (visitante, criação, dados forjados, HTMX) | `orders/tests/test_checkout.py` | 26 |
| **pedidos na conta** (autorização, cancelamento, confirmação) | `orders/tests/test_account_orders.py` | 23 |
| **Stripe** (sessão, assinatura, idempotência do webhook) | `orders/tests/test_stripe.py` | 32 |
| **e-mails do pedido** (conteúdo, idioma, envio único) | `orders/tests/test_emails.py` | 23 |
| **admin dos pedidos** (proteções, ações, notas internas) | `orders/tests/test_admin.py` | 16 |
| **busca** (campos, matriz de 4 idiomas, vazio, duplicidade, layout) | `catalog/tests/test_search.py` | 63 |
| **páginas de categoria e filtro por material** | `catalog/tests/test_category_pages.py` | 40 |
| **faixa do topo e rodapé administráveis** | `storefront/tests/test_storefront.py` | 42 |
| **blocos da Home e nome público da vitrine** | `home/tests/test_content_blocks.py` | 41 |
| **favoritos** (model, visitante, alternar, segurança, listagem, cards, HTMX, N+1) | `accounts/tests/test_favorites.py` | 71 |
| **modal de variantes** (tabela, gravação, cancelar, erros, teclado) | `catalog/tests/test_variant_modal.py` | 61 |
| **foto vinculada à variante** (opcional, produto certo, troca na loja) | `catalog/tests/test_media_variant.py` | 21 |
| **galeria e lupa** (sem corte, abrir/fechar/navegar, viewport) | `catalog/tests/test_gallery.py` | 12 |
| **sugestões** (regra, exclusões, limite, card reaproveitado) | `catalog/tests/test_recommendations.py` | 14 |
| **CONTEÚDO: tabela + modal** (gravar, validar, idioma repetido, cadastro, clicar fora) | `catalog/tests/test_content_modal.py` | 43 |
| **seções do produto no Admin** (ordem pedida, recolher, AUDITORIA por último) | `catalog/tests/test_admin_sections.py` | 18 |
| **páginas institucionais** (texto, 4 idiomas, contato, e-mail, rodapé, conteúdo de fábrica, rotas) | `storefront/tests/test_pages.py` | 72 |
| **fluxo de compra** (ficha por variante, foto da linha, ids e quantidades do cliente, preço) | `cart/tests/test_purchase_flow.py` | 41 |

Cobrem, entre outros: Home responde 200; seções ativas aparecem, inativas e
vazias não; ordem respeitada e alterável; limite respeitado; produto correto,
tradução correta e fallback para português; imagem principal usada e ausência
de imagem não quebra; criação/edição/ativação/ordenação/duplicação pela
interface administrativa; **as 20 combinações de troca de idioma
(PT↔FR↔EN↔NL↔DE) e a permanência do idioma ao navegar**; **Shop: só produtos
ativos da árvore de Modelos, filtro por categoria com subcategorias, as cinco
ordenações, paginação preservando filtros, contagem e resposta parcial ao
HTMX**; **carrinho: adicionar, somar, limite de estoque, sob encomenda,
esgotado, alterar quantidade, remover, contador em unidades, subtotal em
`Decimal` e carrinho vazio**; **contas: unicidade sem caixa nas três camadas
(inclusive `objects.create` e `bulk_create`), login por usuário e por e-mail,
resposta idêntica nas três formas de falhar, token de confirmação válido /
inválido / expirado / reutilizado / adulterado / de outro usuário, recuperação
de senha ponta a ponta e resposta genérica, carrinho preservado no cadastro e
no login, merge somando só linhas idênticas (variante e personalização
diferentes não se misturam), estoque respeitado e isolamento entre usuários**;
e orçamentos fixos de consultas para impedir N+1. **Etapa 13: a foto do produto nunca é cortada (`object-contain`) e a lupa respeita a viewport; a foto vinculada a uma variante troca a imagem principal ao escolher aquela opção, e a variante sem foto própria volta para a foto de abertura em vez de herdar a da anterior; a foto de outro produto não pode ser vinculada, nem pelo widget nem por um POST montado à mão; as sugestões nunca incluem o produto da página nem um produto sem variante ativa; o CONTEÚDO é tabela + modal com a mesma casca das variantes, e o idioma repetido é recusado pelo servidor com o modal aberto; e as oito seções do produto aparecem na ordem pedida, todas recolhíveis, com a AUDITORIA por último.** **Etapa 7: um pedido não muda quando o produto é renomeado, desativado ou reprecificado; o imposto é a parcela contida no total e sai da alíquota do país de destino; o método de entrega que veio do formulário é revalidado contra país e peso; o endereço de outro cliente é 404 em todas as telas; um POST no webhook sem assinatura válida não confirma nada; e o mesmo evento entregue duas vezes não baixa estoque nem envia e-mail duas vezes.**

---

## J. Limitações conhecidas

| Item | Situação | Depende de |
|---|---|---|
| **Mais vendidos** | tipo de seção existe, mas resolve vazio — não há dado de vendas e nenhum critério falso foi inventado | módulo de pedidos |
| **Reembolso** | aprovar um cancelamento cancela o pedido; a devolução do dinheiro é feita no painel da Stripe | integração de refund |
| **Cupons e descontos** | `Order.discount_total` existe e é somado; não há cadastro de cupom | etapa própria |
| **Fatura em PDF** | os dados fiscais estão todos no pedido; falta o documento | etapa própria |
| **APIs de transportadora** | as tarifas são a grade cadastrada no admin, não uma cotação em tempo real | quando o volume justificar |
| **Mensagem de presente** | `Order.gift_message` existe; ainda não é pedida no checkout | quando fizer falta |
| **Vitrines de Filamentos / Impressoras / Acessórios** | a `ShopView` já é genérica; falta a rota apontando para cada raiz | quando houver produtos |
| **Busca por similaridade** | `icontains` no ORM, sem tolerância a erro de grafia nem ranking por relevância | `pg_trgm` / `SearchVector`, quando o catálogo justificar |
| **HTMX** | em uso no Shop (filtros, ordenação, paginação) e no carrinho (adicionar, quantidade, contador). A Home continua 100% renderizada no servidor | — |
| **Devoluções** | a página descreve o processo e é administrável; prazo legal, quem paga o retorno e regras de reembolso ficaram de fora — não estão definidos no projeto | política comercial |
| **Painel de produção** | a ordem de produção chega por e-mail e o Admin do pedido é operacional; não há fila dedicada | quando o volume justificar |
| **Login social e 2FA** | fora do escopo desta etapa | decisão comercial |
| **Atributos de variante** | três eixos fixos (cor, tamanho, material); um sistema genérico entra por cima quando fizer falta | demanda real |
| **Limpeza de uploads órfãos** | arquivos de personalização de carrinhos abandonados ficam no storage | rotina de limpeza |
| **Lembrete de carrinho abandonado** | os dados já estão persistidos (dono, itens, datas); o envio automático é etapa própria | agendamento |
| **Editor de texto rico** | o corpo das páginas aceita `# ` para subtítulo e `- ` para lista; não há WYSIWYG, negrito nem imagem no meio do texto | quando o conteúdo justificar |
| **Anexo no contato** | o cliente escreve, mas não anexa arquivo; para orçamento com modelo 3D, ainda é por e-mail | quando fizer falta |
| **Programa de revenda** | a página apresenta e manda falar com a equipe; não há cadastro de revendedor, tabela de preço nem área logada | decisão comercial |
| **Anti-spam** | campo-armadilha escondido, sem captcha nem limite por IP | se o lixo aparecer |
| **Redes sociais no rodapé** | não existem hoje; não foi criada tabela para conteúdo que não existe | quando houver perfis |
| **Página 404/500 própria** | ainda usa a padrão do Django | etapa própria |
| **Imagens de produto** | nenhuma cadastrada; os cards mostram espaço reservado | fotos reais |
| **Alt text da foto por idioma** | `ProductMedia.alt_text` é único, no idioma padrão | tela dedicada (o Admin não faz inline aninhado) |
| **Sugestões do produto** | regra determinística (categoria → destaque → recentes); não há "quem viu isto viu aquilo" | dados de navegação |
| **Idiomas da interface** | francês, inglês e holandês traduzidos; alemão, espanhol, italiano, turco e árabe caem no português | tradução dos `.po` |
| **PostgreSQL** | configurado, mas a suíte roda hoje em SQLite | instalação do PostgreSQL |

As decisões de arquitetura e os pontos de extensão de cada uma delas estão
registrados fora do repositório; o essencial de cada escolha está explicado na
seção correspondente deste README.

---

## K. Repositório, segredos e deploy

O repositório é **público**. Isso não é um detalhe administrativo: muda o que
pode existir dentro dele. Um segredo que entra num commit está comprometido no
instante seguinte, mesmo que o commit seja revertido depois — o GitHub serve o
objeto antigo, robôs varrem repositórios novos em minutos e a única resposta
correta passa a ser **revogar a chave**, não apagar o arquivo.

### O que nunca é versionado

| Item | Por quê |
|---|---|
| `.env` | é onde moram todos os valores reais |
| `db.sqlite3` e qualquer `*.bak*`, `*.sql`, `*.dump` | o banco de desenvolvimento tem contas com hash de senha, endereços, pedidos e sessões de pessoas reais |
| `media/` | foto que o cliente enviou para a litofania; é dado dele, não código |
| `staticfiles/` | gerado pelo `collectstatic` a cada deploy |
| `venv/`, `node_modules/`, `__pycache__/` | reconstruídos por `pip install` e `npm install` |
| `*.log` | log de aplicação carrega e-mail, IP e corpo de requisição |
| `*.pem`, `*.key`, `*.crt`, `id_rsa*` | chave privada e certificado |

O [`.gitignore`](.gitignore) cobre todos eles, mas **`.gitignore` não é
auditoria**: ele só age sobre arquivo ainda não rastreado. Antes de um commit
que mexa em configuração, confira o que realmente entrou:

```powershell
git status
git ls-files | Select-String "env|sqlite|\.bak|media/|secret|token|key"
git diff --cached
```

### Segredos que nunca entram no código

Estes vivem só no `.env` (na sua máquina) e nas variáveis de ambiente do
servidor. O `config/settings.py` lê todos eles com `env(...)` e nenhum tem
valor real escrito no código:

| Variável | O que é |
|---|---|
| `DJANGO_SECRET_KEY` | assina sessão, CSRF e os tokens de confirmação de e-mail |
| `STRIPE_SECRET_KEY` | cobra de verdade; vazou, cobra na sua conta |
| `STRIPE_WEBHOOK_SECRET` | valida a assinatura do webhook; sem ele, qualquer um forja "pagamento aprovado" |
| `POSTGRES_PASSWORD` | acesso ao banco de produção |
| credenciais SMTP | envio de e-mail em nome do domínio |

### E nos testes?

Os testes da Stripe usam constantes deliberadamente falsas — `sk_test_falsa`,
`whsec_teste_jdprint` — e assinam o webhook com HMAC calculado na hora
(`apps/orders/tests/test_stripe.py`). Nenhuma chave real, nem de teste, é
necessária para rodar a suíte. **Não troque essas constantes por chaves suas**,
nem as do modo de teste: uma chave `sk_test_` real ainda identifica a sua conta
Stripe e dá acesso aos seus dados de teste.

### Deploy

O passo a passo do PythonAnywhere — incluindo as pendências que precisam ser
resolvidas **antes** de a loja receber um pedido real — é mantido fora do
repositório, com o resto do material de operação. O que vale registrar aqui é o
que o repositório público **não** pode conter: nenhuma das variáveis da tabela
acima, em nenhuma forma, nem mesmo como exemplo preenchido. O `.env.example`
existe para documentar os nomes; os valores vivem no `.env` do servidor.
