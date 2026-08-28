"""Conteúdo da loja que aparece em **todas** as páginas.

A faixa do topo e o rodapé estavam escritos no HTML: mudar "Envio para toda a
Europa" exigia editar um template e recompilar as traduções. Aqui eles viram
cadastro, com o mesmo mecanismo de tradução do resto do projeto
(``TranslationBase`` + ``TranslatableMixin``) — uma linha por idioma, e
acrescentar um idioma é inserir linhas, não alterar o schema.

Por que um app próprio e não ``core`` ou ``home``:

* ``core`` guarda infraestrutura — idiomas, e-mail, países de entrega. Texto de
  vitrine não é infraestrutura.
* ``home`` guarda o que aparece **na Home**. O rodapé aparece em toda página.

O que é da Home (banner, seções, cards, chamada final) continua em ``home``,
onde o banner e as seções já viviam.

## O que **não** está aqui, e por quê

* **Redes sociais** — o rodapé não tem nenhuma hoje. Criar a tabela agora seria
  inventar estrutura para um conteúdo que não existe.
* **Nome público de "Modelos"** — a vitrine é montada sobre a *categoria*
  ``modelos``, que já tem nome e descrição traduzíveis. Um segundo lugar para
  o mesmo texto seria dois lugares para corrigir.
* **Coluna de categorias do rodapé** — continua vindo do banco, automática. Só
  o título dela é cadastrado.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, TranslatableMixin, TranslationBase


class ActiveOrderedQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_display(self):
        """Ativos, na ordem, com as traduções já carregadas.

        O ``prefetch_related`` não é otimização prematura: sem ele cada item da
        faixa do topo custa uma consulta, em **toda** página da loja.
        """
        return self.active().order_by("sort_order", "id").prefetch_related("translations")


# ---------------------------------------------------------------------------
# 1. Faixa do topo
# ---------------------------------------------------------------------------


#: Os ícones que o `icon.html` já sabe desenhar. Lista fechada, e não texto
#: livre: um nome errado renderizaria um espaço em branco e o administrador não
#: teria como descobrir por quê.
TOP_BAR_ICONS = (
    ("", "Sem ícone"),
    ("cube", "Cubo (produção)"),
    ("palette", "Paleta (cores e materiais)"),
    ("truck", "Caminhão (envio)"),
    ("sparkles", "Brilho (personalização)"),
    ("shield", "Escudo (segurança)"),
    ("clock", "Relógio (prazo)"),
    ("package", "Caixa (embalagem)"),
)


class TopBarItem(TranslatableMixin, TimeStampedModel):
    """Uma das promessas curtas da loja.

    Aparece em **dois** lugares, e é de propósito: na faixa escura acima do
    cabeçalho (só texto, como sempre foi) e na lista do hero quando não há
    banner com imagem (com ícone). Eram duas cópias no HTML — cadastrar uma e
    esquecer a outra era o jeito mais fácil de a loja se contradizer.

    O ícone é opcional e só é usado no hero: a faixa do topo nunca teve um, e
    pô-lo lá mudaria o desenho que já estava aprovado.

    Os itens da faixa são separados por um ponto, e o template esconde os
    últimos nas telas estreitas — quem cadastrar seis vê três no celular, o
    comportamento que já existia.
    """

    translatable_fields = ("text",)

    internal_name = models.CharField(
        "nome interno",
        max_length=120,
        help_text="Identificação administrativa. Não aparece para o cliente.",
    )
    icon = models.CharField(
        "ícone",
        max_length=20,
        choices=TOP_BAR_ICONS,
        blank=True,
        default="",
        help_text="Usado só na lista do hero. A faixa do topo é sempre só texto.",
    )
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField(
        "ordem", default=0, help_text="Menor valor aparece primeiro."
    )

    objects = ActiveOrderedQuerySet.as_manager()

    class Meta:
        verbose_name = "item da faixa do topo"
        verbose_name_plural = "FAIXA DO TOPO — promessas curtas"
        ordering = ("sort_order", "id")
        indexes = [
            models.Index(fields=["is_active", "sort_order"], name="topbar_active_order_idx"),
        ]

    def __str__(self) -> str:
        return self.internal_name

    @property
    def text(self) -> str:
        return self.tr("text")


class TopBarItemTranslation(TranslationBase):
    master = models.ForeignKey(
        TopBarItem, verbose_name="item", related_name="translations", on_delete=models.CASCADE
    )
    text = models.CharField("texto", max_length=120)

    class Meta:
        verbose_name = "tradução do item"
        verbose_name_plural = "traduções do item"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="topbar_translation_unique_language"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.text} ({self.get_language_display()})"


# ---------------------------------------------------------------------------
# 2. Rodapé
# ---------------------------------------------------------------------------


class FooterSettings(TranslatableMixin, TimeStampedModel):
    """O rodapé fora das colunas de links: texto institucional, contato, legal.

    **Uma linha só** (``pk=1``), como ``EmailSettings``: não é uma lista de
    rodapés, é *o* rodapé. A garantia fica na tabela e não numa convenção que
    alguém contorna.

    Tudo é opcional. Campo em branco não vira caixa vazia na tela — o template
    simplesmente não desenha o bloco. É a regra que permite entregar a loja com
    o rodapé meio configurado sem ela parecer quebrada.
    """

    translatable_fields = (
        "about_text",
        "categories_title",
        "copyright_text",
        "badge_text",
        "contact_title",
    )

    contact_email = models.EmailField("e-mail de contato", blank=True)
    contact_phone = models.CharField("telefone de contato", max_length=40, blank=True)
    is_active = models.BooleanField(
        "usar este rodapé",
        default=True,
        help_text="Desmarcado, o rodapé volta aos textos padrão do template.",
    )

    class Meta:
        verbose_name = "rodapé"
        verbose_name_plural = "RODAPÉ — textos e contato"

    def __str__(self) -> str:
        return "Rodapé da loja"

    def save(self, *args, **kwargs):
        """Uma linha só: qualquer gravação assume o mesmo PK."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "FooterSettings":
        obj, _criado = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def current(cls) -> "FooterSettings | None":
        """A configuração em uso, ou ``None`` para o template usar o padrão."""
        return cls.objects.filter(pk=1, is_active=True).prefetch_related("translations").first()

    @property
    def about_text(self) -> str:
        return self.tr("about_text")

    @property
    def categories_title(self) -> str:
        return self.tr("categories_title")

    @property
    def contact_title(self) -> str:
        return self.tr("contact_title")

    @property
    def copyright_text(self) -> str:
        return self.tr("copyright_text")

    @property
    def badge_text(self) -> str:
        return self.tr("badge_text")

    @property
    def has_contact(self) -> bool:
        return bool(self.contact_email or self.contact_phone)


class FooterSettingsTranslation(TranslationBase):
    master = models.ForeignKey(
        FooterSettings, verbose_name="rodapé", related_name="translations", on_delete=models.CASCADE
    )
    about_text = models.TextField(
        "texto institucional",
        blank=True,
        help_text="O parágrafo ao lado do logo.",
    )
    categories_title = models.CharField(
        "título da coluna de categorias",
        max_length=80,
        blank=True,
        help_text="A lista em si continua vindo das categorias cadastradas.",
    )
    contact_title = models.CharField("título do bloco de contato", max_length=80, blank=True)
    copyright_text = models.CharField(
        "texto de copyright",
        max_length=200,
        blank=True,
        help_text="Deixe em branco para usar o texto padrão com o ano corrente.",
    )
    badge_text = models.CharField(
        "selo do rodapé",
        max_length=80,
        blank=True,
        help_text='O texto ao lado do escudo (hoje: "Compra segura").',
    )

    class Meta:
        verbose_name = "tradução do rodapé"
        verbose_name_plural = "traduções do rodapé"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="footer_translation_unique_language"
            ),
        ]


class FooterColumn(TranslatableMixin, TimeStampedModel):
    """Uma coluna de links do rodapé — hoje, "Ajuda".

    A coluna de **categorias** não é uma destas: ela vem do banco e continua
    automática. Transformá-la em lista manual seria trocar algo que se atualiza
    sozinho por algo que alguém precisa lembrar de atualizar.
    """

    translatable_fields = ("title",)

    internal_name = models.CharField("nome interno", max_length=120)
    is_active = models.BooleanField("ativa", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    objects = ActiveOrderedQuerySet.as_manager()

    class Meta:
        verbose_name = "coluna do rodapé"
        verbose_name_plural = "RODAPÉ — colunas de links"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.internal_name

    @property
    def title(self) -> str:
        return self.tr("title")

    def visible_links(self):
        """Links que aparecem, lidos do prefetch quando houver.

        Um link para uma página despublicada levaria a um 404, e um link
        para uma página marcada como fora do rodapé é exatamente o que
        `show_in_footer` existe para impedir. Os dois somem aqui.
        """
        return [link for link in self.links.all() if link.is_active and link.is_visible]


class FooterColumnTranslation(TranslationBase):
    master = models.ForeignKey(
        FooterColumn, verbose_name="coluna", related_name="translations", on_delete=models.CASCADE
    )
    title = models.CharField("título", max_length=80)

    class Meta:
        verbose_name = "tradução da coluna"
        verbose_name_plural = "traduções da coluna"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="footer_column_translation_unique_language"
            ),
        ]


class FooterLink(TranslatableMixin, TimeStampedModel):
    """Um item de uma coluna do rodapé.

    O destino pode vir de dois lugares, e a ordem importa:

    * ``page`` — uma das páginas da loja. É o jeito certo para conteúdo
      interno: com `i18n_patterns`, a mesma página mora em `/contato/` e em
      `/fr/contato/`, e só a referência sabe disso. Um endereço digitado à
      mão mandaria o visitante francês para a página portuguesa.
    * ``url`` — para o que é de fora (uma rede social) ou para `mailto:`/`tel:`.

    Sem os dois, o item aparece como **texto**, sem link. Continua sendo
    intencional: link para página que dá 404 é pior que texto sem link.
    """

    translatable_fields = ("label",)

    column = models.ForeignKey(
        FooterColumn, verbose_name="coluna", related_name="links", on_delete=models.CASCADE
    )
    page = models.ForeignKey(
        "InstitutionalPage",
        verbose_name="página da loja",
        related_name="footer_links",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=(
            "Aponte para uma página da loja em vez de digitar o endereço: "
            "o link acompanha o idioma do visitante."
        ),
    )
    url = models.CharField(
        "endereço",
        max_length=500,
        blank=True,
        help_text=(
            "Caminho interno (/algo) ou endereço http/https. "
            "Em branco, o item aparece como texto — útil para página em construção."
        ),
    )
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    objects = ActiveOrderedQuerySet.as_manager()

    class Meta:
        verbose_name = "link do rodapé"
        verbose_name_plural = "links do rodapé"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return f"{self.internal_label} ({self.column.internal_name})"

    @property
    def internal_label(self) -> str:
        from apps.core.constants import DEFAULT_LANGUAGE

        reserva = self.page.get_slug_display() if self.page_id else (self.url or "—")
        return self.tr("label", language=DEFAULT_LANGUAGE.value, default=reserva)

    @property
    def label(self) -> str:
        """O texto cadastrado; apontando para uma página, o título dela.

        Assim o nome da página é escrito num lugar só, e traduzir a página
        traduz o rodapé junto. Quem quiser um texto diferente ("Fale conosco"
        em vez de "Contato") continua podendo: basta cadastrar o rótulo.
        """
        proprio = self.tr("label")
        if proprio:
            return proprio
        return self.page.title if self.page_id else ""

    @property
    def is_visible(self) -> bool:
        """Aponta para uma página que o rodapé pode mostrar?

        Link solto (sem página) é sempre visível: quem digitou o endereço
        responde por ele.
        """
        if not self.page_id:
            return True
        return self.page.is_active and self.page.show_in_footer

    @property
    def href(self) -> str:
        """O endereço do link, no idioma de quem está lendo."""
        if self.page_id:
            return self.page.get_absolute_url()
        return self.url

    def clean(self):
        """O mesmo cuidado do CTA: nada de `javascript:` ou `data:` no rodapé."""
        super().clean()
        if self.page_id and (self.url or "").strip():
            raise ValidationError(
                {"url": "Escolha a página da loja ou digite um endereço, não os dois."}
            )
        valor = (self.url or "").strip().lower()
        if valor and not valor.startswith(("/", "http://", "https://", "mailto:", "tel:")):
            raise ValidationError(
                {"url": "Use um caminho interno (/algo), http/https, mailto: ou tel:."}
            )


class FooterLinkTranslation(TranslationBase):
    master = models.ForeignKey(
        FooterLink, verbose_name="link", related_name="translations", on_delete=models.CASCADE
    )
    label = models.CharField("texto do link", max_length=120)

    class Meta:
        verbose_name = "tradução do link"
        verbose_name_plural = "traduções do link"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="footer_link_translation_unique_language"
            ),
        ]


# ---------------------------------------------------------------------------
# 3. Páginas institucionais
# ---------------------------------------------------------------------------


class PageSlug(models.TextChoices):
    """As quatro páginas que a loja tem rota para servir.

    Uma lista fechada, e não um slug livre, porque cada uma tem uma URL própria
    no `urls.py`. Um slug inventado no Admin criaria uma página sem endereço —
    conteúdo escrito que ninguém consegue abrir.

    Os rótulos passam pelo `gettext`: são texto de interface, e servem de
    título de reserva enquanto ninguém cadastrou o conteúdo. Sem isso, o rodapé
    francês mostraria "Envios e prazos" em português.

    Quando a loja precisar de uma quinta, entram juntas a escolha e a rota.
    """

    SHIPPING = "envios-e-prazos", _("Envios e prazos")
    RETURNS = "trocas-e-devolucoes", _("Trocas e devoluções")
    CONTACT = "contato", _("Contato")
    RESELLER = "revenda", _("Seja um revendedor")


#: Slug -> qual formulário a página mostra abaixo do texto.
#:
#: Só o contato tem formulário. A revenda é informativa e termina numa
#: chamada para o contato (`PAGE_CTA`): uma segunda caixa de entrada, com
#: campos próprios, já seria um sistema de revendedores.
PAGE_FORMS = {
    PageSlug.CONTACT: "contact",
}

#: Slug -> para qual página o texto manda o leitor no fim.
PAGE_CTA = {
    PageSlug.RESELLER: PageSlug.CONTACT,
}


def page_url(slug: str) -> str:
    """A URL de uma página institucional, a partir do slug.

    Num lugar só: o nome da rota é derivado do nome da escolha
    (`SHIPPING` -> `page_shipping`), então acrescentar uma quinta página é
    acrescentar a escolha e a rota — nenhum template precisa saber o caminho.
    """
    from django.urls import reverse

    return reverse(f"storefront:page_{PageSlug(slug).name.lower()}")


def page_cta_url(slug: str) -> str:
    """Para onde a chamada no fim desta página leva, ou vazio."""
    destino = PAGE_CTA.get(PageSlug(slug))
    return page_url(destino) if destino else ""


class InstitutionalPageQuerySet(models.QuerySet):
    def for_display(self):
        return self.filter(is_active=True).order_by("sort_order", "slug")

    def in_footer(self):
        return self.for_display().filter(show_in_footer=True)


class InstitutionalPage(TranslatableMixin, TimeStampedModel):
    """Uma das páginas de informação da loja.

    **Um model para as quatro**, e não quatro registros únicos: elas têm a mesma
    forma — título, introdução e texto, nos quatro idiomas. Quatro singletons
    seriam quatro telas idênticas no Admin e quatro migrations quando um campo
    mudasse.

    A página de contato usa este mesmo cadastro para o texto; o formulário é
    acrescentado pela view (ver `PAGE_FORMS`). Assim o lojista escreve a
    chamada da página de contato no mesmo lugar em que escreve a política de
    trocas.

    Nada de texto institucional no HTML: o template desenha o que estiver
    cadastrado. Sem cadastro, a página continua respondendo — com o título que
    a rota conhece e sem corpo. Ver `templates/storefront/page.html`.
    """

    translatable_fields = ("title", "intro", "body", "meta_description")

    slug = models.CharField(
        "página",
        max_length=40,
        unique=True,
        choices=PageSlug.choices,
        help_text="Qual das páginas da loja este conteúdo preenche.",
    )
    is_active = models.BooleanField(
        "publicada",
        default=True,
        help_text="Despublicada, a página some do rodapé e mostra só o formulário, quando houver.",
    )
    show_in_footer = models.BooleanField("mostrar no rodapé", default=True)
    sort_order = models.PositiveIntegerField("ordem no rodapé", default=0)

    objects = InstitutionalPageQuerySet.as_manager()

    class Meta:
        verbose_name = "página institucional"
        verbose_name_plural = "PÁGINAS — envios, trocas, contato e revenda"
        ordering = ("sort_order", "slug")

    def __str__(self) -> str:
        return self.get_slug_display()

    def get_absolute_url(self) -> str:
        return page_url(self.slug)

    @property
    def title(self) -> str:
        """O título cadastrado; sem ele, o nome da própria página.

        `get_slug_display()` é rótulo de interface, não conteúdo institucional:
        serve para o link do rodapé não ficar vazio antes de alguém escrever.
        """
        return self.tr("title", default=self.get_slug_display())

    @property
    def intro(self) -> str:
        return self.tr("intro")

    @property
    def body(self) -> str:
        return self.tr("body")

    @property
    def meta_description(self) -> str:
        return self.tr("meta_description", default=self.intro)

    @property
    def form_kind(self) -> str:
        """`contact` ou vazio."""
        return PAGE_FORMS.get(PageSlug(self.slug), "")

    @property
    def cta_url(self) -> str:
        """A página para onde esta manda o leitor no fim, ou vazio."""
        return page_cta_url(self.slug)

    @property
    def has_content(self) -> bool:
        return bool(self.intro or self.body)


class InstitutionalPageTranslation(TranslationBase):
    master = models.ForeignKey(
        InstitutionalPage,
        verbose_name="página",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    title = models.CharField("título", max_length=200, blank=True)
    intro = models.TextField(
        "introdução",
        blank=True,
        help_text="Um parágrafo curto abaixo do título. Nas páginas com formulário, aparece acima dele.",
    )
    body = models.TextField(
        "texto",
        blank=True,
        help_text=(
            "O conteúdo da página. Uma linha em branco separa parágrafos; "
            "uma linha começando com # vira subtítulo."
        ),
    )
    meta_description = models.CharField(
        "descrição para buscadores",
        max_length=300,
        blank=True,
        help_text="Em branco, usa a introdução.",
    )

    class Meta:
        verbose_name = "tradução da página"
        verbose_name_plural = "traduções da página"
        ordering = ("language",)
        constraints = [
            models.UniqueConstraint(
                fields=["master", "language"], name="institutional_page_unique_language"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title or self.master.get_slug_display()} ({self.get_language_display()})"


# ---------------------------------------------------------------------------
# 4. O que chega pelos formulários
# ---------------------------------------------------------------------------


class ContactMessage(TimeStampedModel):
    """Uma mensagem do formulário de contato.

    Guardada **antes** de o e-mail sair. Um provedor fora do ar não pode
    apagar o pedido de um cliente — e é o que aconteceria se a mensagem só
    existisse dentro da caixa de entrada de alguém.

    Não é um CRM: não há responsável, status, prazo nem histórico. Há uma
    marca de "já foi lida", que é o mínimo para a equipe não reler a mesma
    mensagem duas vezes.
    """

    name = models.CharField("nome", max_length=120)
    email = models.EmailField("e-mail")
    subject = models.CharField("assunto", max_length=200)
    message = models.TextField("mensagem")
    language = models.CharField(
        "idioma", max_length=5, blank=True, help_text="Em que idioma a pessoa escreveu."
    )
    is_handled = models.BooleanField("já respondida", default=False)

    class Meta:
        verbose_name = "mensagem de contato"
        verbose_name_plural = "CONTATO — mensagens recebidas"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["is_handled", "-created_at"], name="contact_handled_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.subject} — {self.name}"
