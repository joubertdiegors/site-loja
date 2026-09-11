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

import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import prefetch_related_objects
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy as _

from apps.core.colors import check_contrast, resolve, validate_color
from apps.core.constants import DEFAULT_LANGUAGE
from apps.core.models import TimeStampedModel, TranslatableMixin, TranslationBase
from apps.core.uploads import BRAND_IMAGE_EXTENSIONS, validate_brand_image

#: A frase que acompanha todo campo de cor no Admin.
COLOR_HELP = (
    "Uma cor da marca (creme, navy, purple, yellow, mint, coral, lavender…) "
    "ou um hexadecimal como <code>#4A1A8C</code>."
)


def css_vars(pares: dict) -> str:
    """Um `style` com variáveis CSS, para o bloco herdar as cores escolhidas."""
    return ";".join("--%s:%s" % (nome, valor) for nome, valor in pares.items() if valor)


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
        help_text="Opcional. Aparece antes do texto, na faixa e na lista do hero.",
    )
    color = models.CharField(
        "cor do texto",
        max_length=20,
        default="yellow",
        validators=[validate_color],
        help_text=(
            "A faixa alterna cores de propósito — amarelo, menta e coral no "
            "desenho da marca. " + COLOR_HELP
        ),
    )
    link_url = models.CharField(
        "endereço",
        max_length=500,
        blank=True,
        help_text="Opcional. Com um endereço, o item vira link.",
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

    @property
    def style(self) -> str:
        return css_vars({"item-fg": resolve(self.color, "yellow")})

    def clean(self):
        super().clean()
        # A faixa é sempre navy. Uma cor clara demais ali some; uma escura
        # demais também — e quem cadastra não tem como saber sem medir.
        erro = check_contrast(self.color, "navy", campo="color")
        if erro:
            raise ValidationError(erro)


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

    # -- cores --------------------------------------------------------------
    # A estrutura do rodapé (colunas, links, páginas institucionais) continua
    # vindo de `FooterColumn`/`FooterLink`: aqui só se escolhe a tinta.
    surface_color = models.CharField(
        "fundo", max_length=20, default="navy",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    text_color = models.CharField(
        "cor do texto", max_length=20, default="ink-on-deep",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    heading_color = models.CharField(
        "cor dos títulos das colunas", max_length=20, default="white",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    accent_color = models.CharField(
        "cor de destaque da logo", max_length=20, default="#b48cf0",
        validators=[validate_color],
        help_text="O \"Print\" do nome, no rodapé. " + COLOR_HELP,
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

    @property
    def style(self) -> str:
        return css_vars({
            "footer-surface": resolve(self.surface_color, "navy"),
            "footer-fg": resolve(self.text_color, "ink-on-deep"),
            "footer-heading": resolve(self.heading_color, "white"),
            "footer-accent": resolve(self.accent_color, "purple"),
        })

    def clean(self):
        super().clean()
        errors = {}
        for tinta, campo in (
            (self.text_color, "text_color"),
            (self.heading_color, "heading_color"),
            (self.accent_color, "accent_color"),
        ):
            erro = check_contrast(tinta, self.surface_color, campo=campo)
            if erro:
                errors.update(erro)
        if errors:
            raise ValidationError(errors)


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


# ---------------------------------------------------------------------------
# 5. Manutenção e lançamento — a página que fecha a loja
#
# Um modelo só para os dois desenhos (`kind`): a estrutura é a mesma — marca,
# status, tag, título com destaque, texto, botões, promessas, selos, rodapé —
# e o que muda é o miolo: a ilustração da impressora com o progresso
# (manutenção) ou a contagem regressiva com o formulário de aviso
# (lançamento). Dois modelos seriam dois cadastros para o mesmo conteúdo.
#
# **Só uma ativa.** A regra vale no banco, não só no formulário: um índice
# único parcial (`is_active = true`) torna duas linhas ativas impossíveis,
# inclusive com dois administradores salvando ao mesmo tempo — o segundo
# perde por `IntegrityError`, e não fica com duas páginas no ar. O `save()`
# desativa as outras antes de ligar esta, na mesma transação, para o caminho
# normal nunca chegar a bater no índice.
#
# Quem lê `SpecialPage.objects.current()` é o middleware
# (`apps/storefront/middleware.py`): com uma página ativa, toda rota pública
# recebe a página no lugar do site.
# ---------------------------------------------------------------------------


class SpecialPageKind(models.TextChoices):
    MAINTENANCE = "maintenance", "Manutenção"
    LAUNCH = "launch", "Lançamento"


class Tone(models.TextChoices):
    """Os tons dos selos e das promessas.

    Uma lista fechada, e não um campo de cor livre: cada tom carrega a sombra
    sólida e a tinta do texto que o desenho define para ele (amarelo com
    sombra âmbar, menta com sombra verde, branco com contorno navy...). Um hex
    solto não teria sombra nem contorno, e o selo sairia chapado.
    """

    YELLOW = "yellow", "Amarelo"
    MINT = "mint", "Menta"
    CORAL = "coral", "Coral"
    WHITE = "white", "Branco com contorno"
    PURPLE = "purple", "Roxo"


#: Fusos que o cadastro oferece para a hora do lançamento. Uma lista curta
#: (a loja é belga com público europeu e brasileiro), mas o campo aceita
#: qualquer nome da base IANA — o `clean()` confere.
TIMEZONE_CHOICES = (
    ("Europe/Brussels", "Bruxelas (Europe/Brussels)"),
    ("Europe/Paris", "Paris (Europe/Paris)"),
    ("Europe/Amsterdam", "Amsterdã (Europe/Amsterdam)"),
    ("Europe/Lisbon", "Lisboa (Europe/Lisbon)"),
    ("Europe/London", "Londres (Europe/London)"),
    ("America/Sao_Paulo", "São Paulo (America/Sao_Paulo)"),
    ("UTC", "UTC"),
)


def special_page_upload_to(instance, filename: str) -> str:
    """``media/brand/special-pages/<arquivo>`` — dentro da pasta pública `brand/`.

    A logo da página especial é servida direto, como as da marca; por isso
    mora numa subpasta de `brand/`, que já está em `PUBLIC_MEDIA_DIRS` e no
    mapeamento da hospedagem. Uma pasta nova exigiria um passo no servidor.
    """
    return f"brand/special-pages/{get_valid_filename(filename)}"


def _shade(hexa: str, factor: float) -> str:
    """Um tom mais escuro da cor: a sombra sólida dos botões e da placa.

    Os presets da marca têm o par escuro (roxo → roxo escuro); um hex livre
    não tem. Escurecer os canais é o que basta para a sombra acompanhar a cor
    escolhida em vez de ficar roxa sob um botão verde.
    """
    hexa = hexa.lstrip("#")
    if len(hexa) != 6:
        return "#" + hexa
    canais = (int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{max(0, min(255, round(c * factor))):02x}" for c in canais)


#: Os pares "cor → sombra" da marca. Fora deles, `_shade` calcula.
_SHADOW_OF = {
    "purple": "purple-dark",
    "navy-soft": "navy",
    "yellow": "#c9ab1f",
    "mint": "#3fa8a3",
    "coral": "#d9836c",
}


def shadow_for(value: str, default: str) -> str:
    base = resolve(value, default)
    escolhido = value if resolve(value) else default
    par = _SHADOW_OF.get(escolhido)
    if par:
        return resolve(par) or par
    return _shade(base, 0.62)


class SpecialPageQuerySet(models.QuerySet):
    def current(self):
        """A página que responde agora, com tudo o que o template lê — ou ``None``.

        Ativa **e em vigor**: um lançamento cuja hora já chegou não responde
        mais, mesmo continuando marcado como ativo (ver
        `SpecialPage.launch_is_over`). As traduções só são lidas depois dessa
        conferência: depois do lançamento cada requisição custa uma consulta,
        a mesma de quando não há página nenhuma.
        """
        page = self.filter(is_active=True).first()
        if page is None or page.launch_is_over:
            return None
        prefetch_related_objects([page], "translations", "benefits__translations")
        return page


class SpecialPage(TranslatableMixin, TimeStampedModel):
    """Uma página de manutenção ou de lançamento, pronta para assumir o site."""

    translatable_fields = (
        "status_text", "eyebrow", "title", "title_highlight", "description",
        "primary_label", "secondary_label", "progress_label",
        "sticker_1", "sticker_2", "sticker_3", "footer_text",
        "countdown_done_text", "form_placeholder", "form_button_label",
        "form_note", "form_success_text",
    )

    # -- geral -----------------------------------------------------------------
    internal_name = models.CharField(
        "nome interno", max_length=120,
        help_text="Só para o Admin: \"Lançamento outubro\", \"Manutenção do servidor\".",
    )
    kind = models.CharField(
        "tipo", max_length=20, choices=SpecialPageKind.choices,
        default=SpecialPageKind.MAINTENANCE,
    )
    is_active = models.BooleanField(
        "ativa",
        default=False,
        help_text=(
            "⚠️ ATIVAR ESTA PÁGINA BLOQUEARÁ O SITE PÚBLICO: todo visitante passa a "
            "ver só esta página, em qualquer endereço. Só uma página fica ativa por "
            "vez — ativar esta desativa a outra. O Admin continua acessível, e quem "
            "está logado como equipe segue vendo a loja normal para testar."
        ),
    )

    # -- marca e header ----------------------------------------------------------
    logo = models.FileField(
        "logo (arquivo)", upload_to=special_page_upload_to, blank=True,
        validators=[FileExtensionValidator(BRAND_IMAGE_EXTENSIONS), validate_brand_image],
        help_text="Opcional. Sem arquivo, o header mostra a marca desenhada (sigla + nome) abaixo.",
    )
    logo_mark = models.CharField(
        "sigla da marca", max_length=4, default="JD",
        help_text="As letras dentro do quadradinho roxo do header.",
    )
    logo_text = models.CharField("nome da marca", max_length=40, default="JD Print")
    logo_url = models.CharField(
        "link da marca", max_length=500, blank=True,
        help_text="Para onde a logo leva. Vazio = a própria página.",
    )
    status_color = models.CharField(
        "cor do ponto de status", max_length=20, default="yellow",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    status_pulse = models.BooleanField(
        "ponto pulsando", default=True,
        help_text="O ponto do status pisca devagar (o desenho da manutenção). Desligue para um ponto fixo.",
    )

    # -- botões -------------------------------------------------------------------
    primary_enabled = models.BooleanField("botão principal ligado", default=True)
    primary_url = models.CharField(
        "link do botão principal", max_length=500, blank=True,
        help_text="Endereço completo (https://…), mailto:… ou um caminho do site.",
    )
    secondary_enabled = models.BooleanField("botão secundário ligado", default=True)
    secondary_url = models.CharField("link do botão secundário", max_length=500, blank=True)

    # -- manutenção: a ilustração ---------------------------------------------------
    show_progress = models.BooleanField(
        "mostrar porcentagem e barra", default=True,
        help_text="Dentro do cartão da impressora.",
    )
    progress_percent = models.PositiveSmallIntegerField(
        "porcentagem", default=68, validators=[MaxValueValidator(100)],
        help_text="De 0 a 100. É só uma indicação para o visitante — nada é medido.",
    )

    # -- lançamento: a contagem e o formulário ----------------------------------------
    launch_date = models.DateField("data do lançamento", null=True, blank=True)
    launch_time = models.TimeField("hora do lançamento", null=True, blank=True)
    launch_timezone = models.CharField(
        "fuso horário", max_length=64, default="Europe/Brussels",
        help_text="A data e a hora acima valem neste fuso. O navegador do visitante converte para o dele.",
    )
    show_countdown = models.BooleanField("mostrar contagem regressiva", default=True)
    show_form = models.BooleanField(
        "mostrar formulário de aviso", default=True,
        help_text="O e-mail fica em CONFIGURAÇÕES DA LOJA › Inscritos do lançamento.",
    )

    # -- selos --------------------------------------------------------------------------
    sticker_1_tone = models.CharField("tom do selo 1", max_length=10, choices=Tone.choices, default=Tone.YELLOW)
    sticker_2_tone = models.CharField("tom do selo 2", max_length=10, choices=Tone.choices, default=Tone.MINT)
    sticker_3_tone = models.CharField("tom do selo 3", max_length=10, choices=Tone.choices, default=Tone.WHITE)

    # -- rodapé e contato ---------------------------------------------------------------
    instagram_url = models.URLField("Instagram", blank=True)
    whatsapp_url = models.URLField(
        "WhatsApp", blank=True, help_text="Ex.: https://wa.me/32470000000",
    )
    contact_email = models.EmailField("e-mail de contato", blank=True)

    # -- cores ----------------------------------------------------------------------------
    surface_color = models.CharField(
        "fundo da página", max_length=20, default="cream",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    brand_color = models.CharField(
        "cor principal", max_length=20, default="purple",
        validators=[validate_color],
        help_text="O poster do lançamento; na manutenção, o botão, a placa e a palavra destacada. " + COLOR_HELP,
    )
    brand_text_color = models.CharField(
        "texto sobre a cor principal", max_length=20, default="white",
        validators=[validate_color], help_text=COLOR_HELP,
    )
    accent_color = models.CharField(
        "cor de destaque", max_length=20, default="yellow",
        validators=[validate_color],
        help_text="A tag, a palavra marcada no lançamento, a barra e o botão do formulário. " + COLOR_HELP,
    )
    accent_text_color = models.CharField(
        "texto sobre o destaque", max_length=20, default="navy",
        validators=[validate_color], help_text=COLOR_HELP,
    )

    objects = SpecialPageQuerySet.as_manager()

    class Meta:
        verbose_name = "página de manutenção ou lançamento"
        verbose_name_plural = "MANUTENÇÃO E LANÇAMENTO — páginas especiais"
        ordering = ("-is_active", "-updated_at")
        constraints = [
            # A garantia de "só uma ativa" que vale mesmo em concorrência.
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="storefront_one_active_special_page",
            ),
        ]

    def __str__(self) -> str:
        return self.internal_name

    # -- ativação --------------------------------------------------------------------

    def save(self, *args, **kwargs):
        """Ligar esta página desliga as outras, na mesma transação."""
        if self.is_active:
            with transaction.atomic():
                outras = SpecialPage.objects.select_for_update().filter(is_active=True)
                if self.pk:
                    outras = outras.exclude(pk=self.pk)
                # `list()` antes do `update()`: o `update` não aceita o
                # `select_for_update`, e é o lock nas linhas que serializa dois
                # administradores ativando ao mesmo tempo.
                ids = [pagina.pk for pagina in outras]
                if ids:
                    SpecialPage.objects.filter(pk__in=ids).update(is_active=False)
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    def activate(self) -> None:
        self.is_active = True
        self.save(update_fields=["is_active", "updated_at"])

    def deactivate(self) -> None:
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])

    # -- validação -------------------------------------------------------------------

    def clean(self):
        super().clean()
        erros = {}
        for tinta, papel, campo in (
            (self.brand_text_color, self.brand_color, "brand_text_color"),
            (self.accent_text_color, self.accent_color, "accent_text_color"),
            ("navy", self.surface_color, "surface_color"),
        ):
            erro = check_contrast(tinta, papel, campo=campo)
            if erro:
                erros.update(erro)
        try:
            ZoneInfo(self.launch_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            erros["launch_timezone"] = ValidationError(
                "Fuso horário desconhecido. Use um nome da base IANA, como Europe/Brussels.",
                code="fuso_invalido",
            )
        if self.is_launch and self.show_countdown and not (self.launch_date and self.launch_time):
            erros["launch_date"] = ValidationError(
                "Informe a data e a hora do lançamento, ou desligue a contagem regressiva.",
                code="lancamento_sem_data",
            )
        if erros:
            raise ValidationError(erros)

    # -- leitura -----------------------------------------------------------------------

    @property
    def is_launch(self) -> bool:
        return self.kind == SpecialPageKind.LAUNCH

    @property
    def is_maintenance(self) -> bool:
        return self.kind == SpecialPageKind.MAINTENANCE

    @property
    def launch_at(self):
        """O instante do lançamento, com fuso — ou ``None`` sem data e hora."""
        if not (self.launch_date and self.launch_time):
            return None
        try:
            fuso = ZoneInfo(self.launch_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            fuso = ZoneInfo("UTC")
        return datetime.combine(self.launch_date, self.launch_time, tzinfo=fuso)

    @property
    def is_launched(self) -> bool:
        instante = self.launch_at
        return instante is not None and instante <= timezone.now()

    @property
    def launch_is_over(self) -> bool:
        """O lançamento terminou: a hora chegou, e a loja abre sozinha.

        Nada desliga o `is_active` — nenhuma tarefa agendada, nenhum cron. A
        página continua marcada como ativa no Admin, mas deixa de responder:
        `SpecialPageQuerySet.current()` a ignora, o middleware entrega o site
        e o formulário de aviso leva para a Home. A comparação é entre
        instantes com fuso (`launch_at` no fuso do cadastro contra
        `timezone.now()`), então não depende do relógio de ninguém.

        Só vale para lançamento com data. Manutenção não tem hora para acabar,
        e um lançamento sem data fica no ar até alguém o desligar — como antes.
        """
        return self.is_launch and self.is_launched

    @property
    def status_text(self) -> str:
        return self.tr("status_text")

    @property
    def eyebrow(self) -> str:
        return self.tr("eyebrow")

    @property
    def title(self) -> str:
        return self.tr("title")

    @property
    def title_highlight(self) -> str:
        return self.tr("title_highlight")

    @property
    def title_html(self):
        """O título com o trecho destacado num `<em>` — o mesmo mecanismo do banner.

        Tudo é escapado antes de a marcação entrar; o único HTML que sai daqui
        é o `<em>`. Trecho ausente do título = título inteiro, sem destaque.
        """
        titulo, trecho = self.title, self.title_highlight
        if not trecho or trecho not in titulo:
            return mark_safe(escape(titulo))
        antes, _, depois = titulo.partition(trecho)
        return mark_safe(f"{escape(antes)}<em>{escape(trecho)}</em>{escape(depois)}")

    @property
    def description(self) -> str:
        return self.tr("description")

    @property
    def description_html(self):
        """O texto com os trechos entre `**` em negrito — e nada mais.

        O desenho do lançamento destaca a data e a hora em branco dentro do
        texto. Em vez de HTML, quem escreve marca o trecho com asteriscos
        duplos; o texto é escapado antes e o único HTML gerado é o `<b>`.
        """
        partes = self.description.split("**")
        if len(partes) < 3:
            return mark_safe(escape(self.description))
        saida = []
        for indice, parte in enumerate(partes):
            texto = escape(parte)
            saida.append(f"<b>{texto}</b>" if indice % 2 == 1 and indice < len(partes) - 1 else texto)
        return mark_safe("".join(saida))

    @property
    def primary_label(self) -> str:
        return self.tr("primary_label")

    @property
    def secondary_label(self) -> str:
        return self.tr("secondary_label")

    @property
    def has_primary(self) -> bool:
        return bool(self.primary_enabled and self.primary_label and self.primary_url)

    @property
    def has_secondary(self) -> bool:
        return bool(self.secondary_enabled and self.secondary_label and self.secondary_url)

    @property
    def progress_label(self) -> str:
        return self.tr("progress_label")

    @property
    def stickers(self) -> list[dict]:
        """Os selos com texto, com o tom de cada um. Sem texto, sem selo."""
        saida = []
        for numero in (1, 2, 3):
            texto = self.tr(f"sticker_{numero}")
            if texto:
                saida.append({
                    "slot": numero,
                    "text": texto,
                    "tone": getattr(self, f"sticker_{numero}_tone"),
                })
        return saida

    @property
    def visible_benefits(self) -> list:
        return [b for b in self.benefits.all() if b.is_active and b.text]

    @property
    def footer_text(self) -> str:
        return self.tr("footer_text")

    @property
    def countdown_done_text(self) -> str:
        return self.tr("countdown_done_text")

    @property
    def form_placeholder(self) -> str:
        return self.tr("form_placeholder")

    @property
    def form_button_label(self) -> str:
        return self.tr("form_button_label")

    @property
    def form_note(self) -> str:
        return self.tr("form_note")

    @property
    def form_success_text(self) -> str:
        return self.tr("form_success_text")

    @property
    def has_form(self) -> bool:
        return self.is_launch and self.show_form

    @property
    def has_countdown(self) -> bool:
        return self.is_launch and self.show_countdown and self.launch_at is not None

    @property
    def style(self) -> str:
        """As cores escolhidas, como variáveis CSS do atributo `style`."""
        variaveis = {
            "sp-surface": resolve(self.surface_color, "cream"),
            "sp-brand": resolve(self.brand_color, "purple"),
            "sp-brand-dk": shadow_for(self.brand_color, "purple"),
            "sp-on-brand": resolve(self.brand_text_color, "white"),
            "sp-accent": resolve(self.accent_color, "yellow"),
            "sp-accent-dk": shadow_for(self.accent_color, "yellow"),
            "sp-on-accent": resolve(self.accent_text_color, "navy"),
            "sp-status": resolve(self.status_color, "yellow"),
        }
        return ";".join(f"--{nome}:{valor}" for nome, valor in variaveis.items())


class SpecialPageTranslation(TranslationBase):
    master = models.ForeignKey(
        SpecialPage, verbose_name="página", related_name="translations", on_delete=models.CASCADE
    )
    status_text = models.CharField(
        "status (header)", max_length=60, blank=True,
        help_text="A pílula ao lado da logo: \"Manutenção programada\", \"Lançamento em breve\".",
    )
    eyebrow = models.CharField("tag", max_length=60, blank=True, help_text="A tarja amarela acima do título.")
    title = models.CharField("título", max_length=160)
    title_highlight = models.CharField(
        "trecho destacado do título", max_length=80, blank=True,
        help_text="Uma palavra ou trecho do título, copiado exatamente. Sai roxo (manutenção) ou na pílula amarela (lançamento).",
    )
    description = models.TextField(
        "texto", blank=True,
        help_text="Trechos entre ** saem em negrito: \"Lançamos no dia **1 de outubro** às **10:00**\".",
    )
    primary_label = models.CharField("texto do botão principal", max_length=60, blank=True)
    secondary_label = models.CharField("texto do botão secundário", max_length=60, blank=True)
    progress_label = models.CharField(
        "legenda do progresso", max_length=80, blank=True,
        help_text="Manutenção: sob a barra. Ex.: \"Imprimindo a nova versão…\".",
    )
    sticker_1 = models.CharField("selo 1", max_length=40, blank=True)
    sticker_2 = models.CharField("selo 2", max_length=40, blank=True)
    sticker_3 = models.CharField("selo 3", max_length=40, blank=True)
    footer_text = models.CharField(
        "texto do rodapé", max_length=120, blank=True,
        help_text="Ex.: \"© 2026 JD Print · Feito na Bélgica\".",
    )
    countdown_done_text = models.CharField(
        "texto ao zerar a contagem", max_length=120, blank=True,
        help_text="Lançamento: o que aparece quando a data chega. Ex.: \"Já lançamos! Bem-vindo.\"",
    )
    form_placeholder = models.CharField("campo de e-mail (placeholder)", max_length=60, blank=True)
    form_button_label = models.CharField("botão do formulário", max_length=60, blank=True)
    form_note = models.CharField(
        "nota sob o formulário", max_length=160, blank=True,
        help_text="Ex.: \"Sem spam. Só o aviso de lançamento e o cupom.\"",
    )
    form_success_text = models.CharField(
        "texto de sucesso", max_length=160, blank=True,
        help_text="Depois de deixar o e-mail. Ex.: \"Pronto! Avisamos você no lançamento 🎉\"",
    )

    class Meta:
        verbose_name = "tradução da página"
        verbose_name_plural = "traduções da página"
        constraints = [
            models.UniqueConstraint(fields=["master", "language"], name="uq_specialpage_translation"),
        ]


class SpecialPageBenefit(TranslatableMixin, TimeStampedModel):
    """Uma promessa curta sob os botões: o quadradinho colorido e o texto."""

    translatable_fields = ("text",)

    page = models.ForeignKey(
        SpecialPage, verbose_name="página", related_name="benefits", on_delete=models.CASCADE
    )
    tone = models.CharField("cor do marcador", max_length=10, choices=Tone.choices, default=Tone.MINT)
    sort_order = models.PositiveIntegerField("ordem", default=0)
    is_active = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "benefício da página especial"
        verbose_name_plural = "MANUTENÇÃO E LANÇAMENTO — benefícios"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.tr("text", language=DEFAULT_LANGUAGE.value) or f"benefício #{self.pk}"

    @property
    def text(self) -> str:
        return self.tr("text")


class SpecialPageBenefitTranslation(TranslationBase):
    master = models.ForeignKey(
        SpecialPageBenefit, verbose_name="benefício", related_name="translations", on_delete=models.CASCADE
    )
    text = models.CharField("texto", max_length=80)

    class Meta:
        verbose_name = "tradução do benefício"
        verbose_name_plural = "traduções do benefício"
        constraints = [
            models.UniqueConstraint(fields=["master", "language"], name="uq_specialpagebenefit_translation"),
        ]


class LaunchSubscriber(TimeStampedModel):
    """Quem pediu para ser avisado do lançamento.

    Só o e-mail, o idioma em que a pessoa escreveu e de qual página veio. Sem
    integração externa: a lista fica no banco e sai por CSV do Admin; ligar um
    serviço de e-mail marketing depois é ler esta tabela, não mudar o
    formulário. O e-mail é único sem distinguir maiúsculas — a segunda
    inscrição do mesmo endereço não cria linha nem revela que já existia.
    """

    email = models.EmailField("e-mail")
    language = models.CharField("idioma", max_length=5, blank=True)
    page = models.ForeignKey(
        SpecialPage, verbose_name="página de origem", null=True, blank=True,
        related_name="subscribers", on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = "inscrito do lançamento"
        verbose_name_plural = "MANUTENÇÃO E LANÇAMENTO — inscritos"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(Lower("email"), name="storefront_subscriber_email_ci"),
        ]

    def __str__(self) -> str:
        return self.email

    @classmethod
    def subscribe(cls, email: str, *, language: str = "", page=None) -> tuple["LaunchSubscriber", bool]:
        """Grava uma vez por endereço. Devolve (inscrito, criado)."""
        normalizado = email.strip().lower()
        existente = cls.objects.filter(email__iexact=normalizado).first()
        if existente:
            return existente, False
        try:
            with transaction.atomic():
                return cls.objects.create(email=normalizado, language=language[:5], page=page), True
        except IntegrityError:
            # Duas inscrições do mesmo endereço no mesmo instante: a segunda
            # encontra a primeira, e ninguém vê erro.
            return cls.objects.get(email__iexact=normalizado), False


# ---------------------------------------------------------------------------
# Avisos da loja
# ---------------------------------------------------------------------------
#
# Um recurso geral: uma frase curta que a loja quer que o cliente veja —
# uma promoção, um prazo de entrega de fim de ano, uma mudança de horário.
# Não depende de nenhuma outra parte do cadastro: nem da página especial,
# nem do lançamento, nem das regras de frete. O texto é livre.
#
# **Onde** (`pages`) usa as áreas que as rotas da loja já definem — o
# namespace de cada `urls.py` (`home`, `catalog`, `cart`, `accounts`...), ver
# `apps/storefront/notices.py`. Uma área nova é uma entrada em `NoticePage` e
# uma linha no mapa de rotas, não um campo novo por página.
#
# **Como** (`position`) é uma lista fechada: cada posição tem o seu desenho
# em `static/src/input.css` (bloco "Avisos da loja"). Nada de CSS livre no
# Admin — um aviso nunca cobre o cabeçalho nem o botão de comprar.
# ---------------------------------------------------------------------------


class NoticePosition(models.TextChoices):
    BELOW_BANNER = "below_banner", "Abaixo do banner — faixa de destaque no fluxo da página"
    TOP = "top", "Topo da página — faixa fina acima do cabeçalho"
    AFTER_CONTENT = "after_content", "Após o conteúdo principal — discreto, antes do rodapé"
    CORNER = "corner", "Canto inferior direito — card flutuante, como uma mensagem"


class NoticePage(models.TextChoices):
    HOME = "home", "Home"
    CATALOG = "catalog", "Catálogo, categorias e busca"
    PRODUCT = "product", "Páginas de produto"
    CART = "cart", "Carrinho"
    CHECKOUT = "checkout", "Finalização da compra"
    ACCOUNT = "account", "Conta do cliente, pedidos e favoritos"
    INSTITUTIONAL = "institutional", "Páginas institucionais e contato"
    SPECIAL = "special", "Página de manutenção ou de lançamento (enquanto estiver ativa)"


def default_notice_pages() -> list:
    """Um aviso novo nasce na Home — o lugar em que ele mais é visto."""
    return [NoticePage.HOME.value]


def validate_notice_pages(value) -> None:
    if not isinstance(value, list) or not value:
        raise ValidationError("Escolha pelo menos uma página.", code="sem_pagina")
    desconhecidas = [pagina for pagina in value if pagina not in NoticePage.values]
    if desconhecidas:
        raise ValidationError(
            "Página desconhecida: %(paginas)s.",
            code="pagina_desconhecida",
            params={"paginas": ", ".join(map(str, desconhecidas))},
        )


#: Os começos de endereço que um link de aviso pode ter — os mesmos do rodapé.
NOTICE_LINK_PREFIXES = ("/", "http://", "https://", "mailto:", "tel:")


def safe_notice_link(url: str) -> str:
    """O link, se ele for seguro — senão ``""``.

    Espaços e quebras no meio do esquema («java\nscript:») são o truque de
    sempre: somem antes da comparação. `//outro-site` também fica de fora —
    parece um caminho interno, mas o navegador o lê como outro domínio.
    """
    endereco = (url or "").strip()
    comparavel = re.sub(r"\s+", "", endereco).lower()
    if not comparavel or comparavel.startswith("//"):
        return ""
    return endereco if comparavel.startswith(NOTICE_LINK_PREFIXES) else ""


class StoreNoticeQuerySet(models.QuerySet):
    def for_display(self):
        """Os avisos ligados, na ordem do Admin, com as traduções."""
        return self.filter(is_active=True).order_by("sort_order", "id").prefetch_related("translations")


class StoreNotice(TranslatableMixin, TimeStampedModel):
    """Um aviso da loja: título opcional, mensagem, link opcional — por idioma."""

    translatable_fields = ("title", "message", "link_label")

    # -- exibição -----------------------------------------------------------------
    is_active = models.BooleanField(
        "ativo", default=True, help_text="Desligado, o aviso some da loja. Nada é apagado.",
    )
    dismissible = models.BooleanField(
        "permitir fechar",
        default=True,
        help_text=(
            "Mostra um X. Quem fecha não vê mais este aviso ao navegar — até ele ser "
            "alterado: um texto ou link novo volta a aparecer para todos."
        ),
    )
    sort_order = models.PositiveIntegerField(
        "ordem",
        default=0,
        help_text=(
            "Menor aparece primeiro. Na mesma posição, as faixas se empilham nesta "
            "ordem; no canto aparece um card por vez — fechado, dá lugar ao seguinte."
        ),
    )

    # -- onde ------------------------------------------------------------------------
    pages = models.JSONField(
        "onde exibir",
        default=default_notice_pages,
        validators=[validate_notice_pages],
        help_text="As áreas da loja em que o aviso aparece.",
    )
    position = models.CharField(
        "posição",
        max_length=20,
        choices=NoticePosition.choices,
        default=NoticePosition.BELOW_BANNER,
        help_text=(
            "Abaixo do banner é a faixa de destaque da Home; nas páginas sem banner, "
            "ela abre o conteúdo, logo abaixo do cabeçalho."
        ),
    )

    # -- link ------------------------------------------------------------------------
    link_url = models.CharField(
        "link",
        max_length=500,
        blank=True,
        help_text=(
            "Opcional. Um caminho da loja (/modelos/), um endereço completo (https://…), "
            "mailto: ou tel:. Sem link, o aviso é só texto."
        ),
    )

    objects = StoreNoticeQuerySet.as_manager()

    class Meta:
        verbose_name = "aviso"
        verbose_name_plural = "AVISOS — faixas e cards da loja"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.tr("message", language=DEFAULT_LANGUAGE.value) or f"aviso #{self.pk}"

    def clean(self):
        super().clean()
        erros = {}
        if (self.link_url or "").strip() and not safe_notice_link(self.link_url):
            erros["link_url"] = ValidationError(
                "Use um caminho da loja (/algo), http/https, mailto: ou tel:.", code="link_inseguro"
            )
        if self.position == NoticePosition.CORNER and not self.dismissible:
            erros["dismissible"] = ValidationError(
                "O card do canto sempre pode ser fechado: no celular ele fica sobre o conteúdo.",
                code="canto_sem_fechar",
            )
        if erros:
            raise ValidationError(erros)

    # -- leitura -------------------------------------------------------------------

    def display_translation(self, language: str | None = None):
        """A linha de tradução que aparece — inteira, de um idioma só.

        O mesmo caminho de `tr` (o idioma de quem lê, depois o português,
        depois qualquer outro), mas escolhendo a LINHA, e não campo a campo:
        título, mensagem e texto do link saem do mesmo idioma. Um título em
        português sobre uma mensagem em francês seria pior que nenhum título.
        """
        if self.pk is None:
            return None
        from apps.core.i18n import get_content_language

        tabela = self.translations_by_language()
        for codigo in [language or get_content_language(), DEFAULT_LANGUAGE.value, *sorted(tabela)]:
            linha = tabela.get(codigo)
            if linha is not None and linha.message:
                return linha
        return None

    @property
    def href(self) -> str:
        return safe_notice_link(self.link_url)

    @property
    def version(self) -> str:
        """Uma impressão do conteúdo: muda quando o texto, o link ou a posição mudam.

        É o que o fechamento guarda. Fechar a versão de hoje não esconde a de
        amanhã: o Admin reescreve a frase, a impressão muda, o aviso volta.
        Ligar e desligar, reordenar ou mudar as páginas não muda o conteúdo —
        quem fechou continua sem ver.
        """
        partes = [self.link_url or "", self.position or ""]
        for linha in sorted(self.translations.all(), key=lambda item: item.language):
            partes += [linha.language, linha.title, linha.message, linha.link_label]
        return hashlib.sha1("\x1f".join(partes).encode("utf-8")).hexdigest()[:10]


class StoreNoticeTranslation(TranslationBase):
    master = models.ForeignKey(
        StoreNotice, verbose_name="aviso", related_name="translations", on_delete=models.CASCADE
    )
    title = models.CharField(
        "título", max_length=80, blank=True, help_text="Opcional. Sai em negrito, antes da mensagem.",
    )
    message = models.CharField(
        "mensagem", max_length=240, help_text="O texto do aviso. Curto: uma ou duas frases.",
    )
    link_label = models.CharField(
        "texto do link",
        max_length=40,
        blank=True,
        help_text="Opcional. Só aparece se o aviso tiver link; vazio, o link diz «Saiba mais».",
    )

    class Meta:
        verbose_name = "tradução do aviso"
        verbose_name_plural = "traduções do aviso"
        constraints = [
            models.UniqueConstraint(fields=["master", "language"], name="uq_storenotice_translation"),
        ]
