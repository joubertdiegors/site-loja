"""Modelos abstratos reutilizados por todo o catálogo.

Três blocos de construção:

* ``TimeStampedModel`` / ``AuditableModel`` — auditoria.
* ``TranslationBase``  — base das tabelas de tradução.
* ``TranslatableMixin`` — leitura de campos traduzidos com fallback.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.text import get_valid_filename

from apps.core.uploads import BRAND_IMAGE_EXTENSIONS, validate_brand_image

from apps.core.constants import DEFAULT_LANGUAGE, Language
from apps.core.i18n import get_content_language


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("criado em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        abstract = True


class AuditableModel(TimeStampedModel):
    """Datas + autoria.

    ``created_by``/``updated_by`` são preenchidos pelo admin. São opcionais
    porque objetos podem nascer de migrações, comandos ou importações. Uma
    auditoria completa (histórico de alterações) pode ser adicionada depois
    sem alterar estes campos.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="criado por",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="atualizado por",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        editable=False,
    )

    class Meta:
        abstract = True


class TranslationBase(models.Model):
    """Base das tabelas de tradução.

    Cada modelo traduzível ganha uma tabela filha
    (``ProductTranslation``, ``CategoryTranslation``, ...) com uma linha por
    idioma. Nenhum campo ``nome_pt``/``nome_fr`` é criado: adicionar um idioma
    é inserir linhas, não alterar o schema.

    A subclasse concreta deve declarar::

        master = models.ForeignKey(Produto, related_name="translations", ...)

    e uma constraint de unicidade ``(master, language)``.
    """

    language = models.CharField(
        "idioma",
        max_length=5,
        choices=Language.choices,
        default=DEFAULT_LANGUAGE,
        db_index=True,
    )

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{self.get_language_display()}"


class TranslatableMixin:
    """Leitura de campos traduzidos.

    Requer um ``related_name="translations"`` apontando para o modelo.
    Use ``prefetch_related("translations")`` para evitar N+1.
    """

    #: Campos existentes na tabela de tradução (documentação e uso no admin).
    translatable_fields: tuple[str, ...] = ()

    def translations_by_language(self) -> dict[str, models.Model]:
        cache = getattr(self, "_translations_by_language", None)
        if cache is None:
            cache = {item.language: item for item in self.translations.all()}
            self._translations_by_language = cache
        return cache

    def tr(self, field: str, language: str | None = None, fallback: bool = True, default: str = "") -> str:
        """Valor traduzido de um campo, com fallback por CAMPO.

        Se a tradução holandesa existe mas está sem descrição curta, a
        descrição em português é usada no lugar de devolver vazio.
        """
        if self.pk is None:
            return default

        language = language or get_content_language()
        table = self.translations_by_language()

        candidates = [language]
        if fallback:
            candidates.append(DEFAULT_LANGUAGE.value)
            candidates.extend(sorted(table))

        for code in candidates:
            translation = table.get(code)
            if translation is None:
                continue
            value = getattr(translation, field, "")
            if value:
                return value

        return default

    def refresh_translations(self) -> None:
        """Descarta o cache de traduções (usar após gravar inlines)."""
        self._translations_by_language = None

    def available_languages(self) -> list[str]:
        return sorted(self.translations_by_language())


# ---------------------------------------------------------------------------
# Configuração da loja
# ---------------------------------------------------------------------------


class SiteLanguageQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True).order_by("sort_order", "code")


class SiteLanguage(models.Model):
    """Quais idiomas a loja oferece hoje ao cliente.

    Duas listas diferentes, que estavam misturadas:

    * ``settings.LANGUAGES`` — idiomas que o **sistema** suporta. Não pode ser
      dinâmico: ``i18n_patterns`` monta os prefixos de URL na importação do
      urlconf e ``get_supported_language_variant`` é cacheado pelo Django;
    * esta tabela — idiomas **oferecidos na loja** agora. É o que alimenta o
      seletor do header e o que a troca de idioma aceita.

    Nome e nome nativo não são colunas: o Django já os conhece
    (``get_language_info``), e duplicá-los só criaria duas versões da mesma
    verdade.
    """

    # Sem ``choices`` de propósito: ``settings.LANGUAGES`` seria congelado
    # dentro da migration, e mexer na lista de idiomas suportados passaria a
    # gerar migrations falsas. As opções vêm do formulário do admin, e o
    # ``clean()`` garante que o código é um idioma suportado.
    code = models.CharField("idioma", max_length=10, unique=True)
    is_active = models.BooleanField(
        "disponível na loja",
        default=False,
        help_text="Desmarcado, o idioma some do seletor e a loja recusa a troca para ele.",
    )
    sort_order = models.PositiveIntegerField(
        "ordem", default=0, help_text="Ordem no seletor do header."
    )

    objects = SiteLanguageQuerySet.as_manager()

    class Meta:
        verbose_name = "idioma da loja"
        verbose_name_plural = "idiomas da loja"
        ordering = ("sort_order", "code")

    def __str__(self) -> str:
        return f"{self.native_name} ({self.code})"

    @property
    def is_default(self) -> bool:
        return self.code == settings.LANGUAGE_CODE

    @property
    def info(self) -> dict:
        from django.utils.translation import get_language_info

        try:
            return get_language_info(self.code)
        except KeyError:  # idioma fora do catálogo do Django
            return {"code": self.code, "name": self.code, "name_local": self.code, "bidi": False}

    @property
    def name(self) -> str:
        return self.info["name"]

    @property
    def native_name(self) -> str:
        return self.info["name_local"].capitalize()

    def clean(self):
        super().clean()

        if self.code not in dict(settings.LANGUAGES):
            raise ValidationError(
                {"code": "Idioma não suportado pelo sistema (ver settings.LANGUAGES)."}
            )

        # O idioma padrão nunca pode sair do ar: a loja ficaria sem fallback.
        if self.is_default and not self.is_active:
            raise ValidationError(
                {"is_active": "O idioma padrão da loja não pode ser desativado."}
            )

    def save(self, *args, **kwargs):
        if self.is_default:
            self.is_active = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_default:
            raise ValidationError("O idioma padrão da loja não pode ser removido.")
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Países de entrega
# ---------------------------------------------------------------------------


class DeliveryCountryQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_checkout(self):
        return self.active().prefetch_related("translations").order_by("sort_order", "iso_code")


class DeliveryCountry(TranslatableMixin, models.Model):
    """Para onde a loja envia — e com qual alíquota de TVA.

    Fica em ``core``, junto de ``SiteLanguage``, porque é **configuração da
    loja**: o endereço do cliente (accounts), a tarifa de frete (shipping) e o
    imposto do pedido (orders) apontam todos para cá. Em qualquer outro app,
    dois dos três teriam que importar o terceiro.

    O código é ISO 3166-1 alfa-2 (``BE``, ``FR``, ``NL``) porque é o que a
    Stripe, as transportadoras e as declarações fiscais falam. O nome vem da
    tabela de traduções — o mesmo mecanismo do catálogo —, então um cliente
    francês vê "Belgique" e não "Bélgica".

    ``vat_rate`` é a alíquota do país de **destino**: na venda a distância B2C
    dentro da UE é ela que vale (regime OSS). Guardar aqui evita o 21% escrito
    no meio do código — e permite ligar um país novo sem alterar o sistema.
    """

    translatable_fields = ("name",)

    iso_code = models.CharField(
        "código ISO",
        max_length=2,
        unique=True,
        help_text="ISO 3166-1 alfa-2, em maiúsculas: BE, FR, NL, DE...",
    )
    is_active = models.BooleanField(
        "envia para este país",
        default=False,
        help_text="Desmarcado, o país some do checkout e do cadastro de endereços.",
    )
    vat_rate = models.DecimalField(
        "TVA (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("21.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Alíquota aplicada a pedidos entregues neste país.",
    )
    sort_order = models.PositiveIntegerField(
        "ordem", default=0, help_text="Ordem na lista do checkout."
    )
    free_shipping_enabled = models.BooleanField(
        "frete grátis neste país",
        default=False,
        help_text=(
            "Ligado, pedidos que alcançarem o valor abaixo não pagam entrega "
            "neste país. Os outros países não são afetados."
        ),
    )
    free_shipping_min_subtotal = models.DecimalField(
        "frete grátis a partir de (€)",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "O valor dos produtos (sem a entrega) a partir do qual o frete "
            "para este país sai zero. Ex.: 50,00 — a partir de € 50,00 a "
            "entrega é grátis. Vale só com a opção acima ligada."
        ),
    )
    # As duas apontam para `shipping` por **nome**: o app de entrega já importa
    # este módulo (a tarifa aponta para o país), e uma referência tardia evita
    # o import circular sem mudar nada de lugar.
    free_shipping_carrier = models.ForeignKey(
        "shipping.ShippingCarrier",
        verbose_name="transportadora do frete grátis",
        related_name="free_shipping_countries",
        null=True,
        blank=True,
        # Apagar a transportadora não pode derrubar o país: a regra apenas
        # deixa de valer, e o frete volta a ser o das tarifas.
        on_delete=models.SET_NULL,
        help_text="Quem entrega de graça neste país quando o pedido alcança o valor.",
    )
    free_shipping_method = models.ForeignKey(
        "shipping.ShippingMethod",
        verbose_name="modalidade grátis",
        related_name="free_shipping_countries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=(
            "A modalidade **dessa** transportadora que sai zero. As outras "
            "continuam com o preço da tabela, e o cliente escolhe no checkout."
        ),
    )

    objects = DeliveryCountryQuerySet.as_manager()

    class Meta:
        verbose_name = "país de entrega"
        verbose_name_plural = "países de entrega"
        ordering = ("sort_order", "iso_code")

    def __str__(self) -> str:
        return f"{self.name} ({self.iso_code})"

    @property
    def name(self) -> str:
        """Nome traduzido; sem tradução nenhuma, o próprio código ISO."""
        return self.tr("name", default=self.iso_code)

    def free_shipping_applies(self, subtotal) -> bool:
        """Este subtotal alcança o frete grátis **deste** país?

        ``subtotal`` é o valor dos produtos, sem a entrega — o mesmo número
        que o resumo do checkout mostra e que o pedido grava. A regra é do
        país de **destino**: a Bélgica pode dar frete grátis a partir de
        € 50,00 e a França a partir de € 60,00, sem que uma saiba da outra.

        Responder ``True`` ainda não é frete grátis: a gratuidade vale para
        **uma** modalidade (ver ``free_shipping_method_id_for``). Desligada,
        sem limite, sem transportadora ou sem modalidade, a resposta é não e o
        frete segue as tarifas de sempre. O cadastro incompleto é recusado no
        Admin (ver ``clean``); a conferência aqui é a rede de baixo, para um
        dado gravado por fora não zerar frete nenhum por engano.
        """
        if not self.free_shipping_enabled:
            return False
        if not self.free_shipping_carrier_id or not self.free_shipping_method_id:
            return False
        minimo = self.free_shipping_min_subtotal or Decimal("0.00")
        if minimo <= 0:
            return False
        return Decimal(subtotal or 0) >= minimo

    def free_shipping_method_id_for(self, subtotal):
        """A modalidade que sai de graça neste pedido, ou ``None``.

        É o que o cálculo do frete pergunta: uma modalidade, nunca todas. O
        cliente continua vendo as outras no checkout, com o preço da tabela.
        """
        return self.free_shipping_method_id if self.free_shipping_applies(subtotal) else None

    def clean(self):
        super().clean()
        self.iso_code = (self.iso_code or "").strip().upper()
        if len(self.iso_code) != 2 or not self.iso_code.isalpha():
            raise ValidationError({"iso_code": "Use o código ISO de duas letras (BE, FR, NL...)."})
        if self.free_shipping_enabled:
            self._clean_free_shipping()

    def _clean_free_shipping(self):
        """A regra ligada precisa estar inteira: valor, transportadora e modalidade.

        E a modalidade tem que ser **daquela** transportadora: o formulário
        filtra a lista, mas o que chega no POST é do navegador, e o par é
        conferido aqui.
        """
        erros = {}
        # Ligar a regra sem valor daria frete grátis para qualquer pedido, e o
        # engano seria caro. Quem quiser isso escreve o valor.
        if (self.free_shipping_min_subtotal or 0) <= 0:
            erros["free_shipping_min_subtotal"] = (
                "Informe a partir de qual valor a entrega é grátis neste país "
                "(ex.: 50,00), ou desligue o frete grátis."
            )
        if not self.free_shipping_carrier_id:
            erros["free_shipping_carrier"] = (
                "Escolha a transportadora que fará a entrega grátis neste país."
            )
        if not self.free_shipping_method_id:
            erros["free_shipping_method"] = (
                "Escolha a modalidade que sai de graça — as outras continuam pagas."
            )
        elif (
            self.free_shipping_carrier_id
            and self.free_shipping_method.carrier_id != self.free_shipping_carrier_id
        ):
            erros["free_shipping_method"] = (
                f"«{self.free_shipping_method.name}» é uma modalidade de "
                f"{self.free_shipping_method.carrier.name}, não da transportadora escolhida."
            )
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        self.iso_code = (self.iso_code or "").strip().upper()
        super().save(*args, **kwargs)


class DeliveryCountryTranslation(TranslationBase):
    master = models.ForeignKey(
        DeliveryCountry,
        verbose_name="país",
        related_name="translations",
        on_delete=models.CASCADE,
    )
    name = models.CharField("nome", max_length=80)

    class Meta:
        verbose_name = "nome do país"
        verbose_name_plural = "nomes do país"
        ordering = ("master", "language")
        constraints = [
            models.UniqueConstraint(
                fields=("master", "language"), name="delivery_country_unique_language"
            ),
        ]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Configuração de e-mail
# ---------------------------------------------------------------------------


class EmailSettings(TimeStampedModel):
    """Como a loja envia e-mail — configurável sem mexer no servidor.

    **Uma linha só.** Não é uma lista de servidores: é *a* configuração. O
    ``clean()`` e o Admin garantem isso; ter duas ativas seria ter duas
    respostas para "de onde sai o e-mail".

    **Prioridade** (documentada em ``docs/OPERACAO.md``)::

        EmailSettings ativa e com servidor preenchido
            ↓  se não houver
        variáveis do .env / settings

    Ou seja: o ``.env`` continua funcionando exatamente como antes, e o Admin
    só entra em cena quando alguém de fato cadastrar a configuração e marcar
    "ativa". Nunca as duas ao mesmo tempo, nunca metade de cada.

    **A senha** é cifrada antes de entrar no banco (``apps.core.secrets``) e
    nunca volta para a tela: o formulário do Admin mostra um campo vazio, e
    deixá-lo vazio conserva o que já estava gravado.
    """

    is_active = models.BooleanField(
        "usar esta configuração",
        default=False,
        help_text=(
            "Desmarcada, a loja volta a usar as variáveis do .env. "
            "Marque só depois de testar o envio."
        ),
    )

    host = models.CharField("servidor SMTP", max_length=255, blank=True)
    port = models.PositiveIntegerField("porta", default=587)
    username = models.CharField("usuário", max_length=255, blank=True)
    #: Cifrada. Nunca leia este campo direto — use a propriedade ``password``.
    password_encrypted = models.TextField("senha (cifrada)", blank=True, editable=False)

    use_tls = models.BooleanField(
        "usar TLS (STARTTLS)",
        default=True,
        help_text="O normal na porta 587. Não marque junto com SSL.",
    )
    use_ssl = models.BooleanField(
        "usar SSL",
        default=False,
        help_text="O normal na porta 465. Não marque junto com TLS.",
    )
    timeout = models.PositiveIntegerField(
        "tempo limite (s)",
        default=10,
        help_text="Quanto esperar pelo servidor antes de desistir de um envio.",
    )

    from_email = models.EmailField("e-mail remetente", blank=True)
    from_name = models.CharField("nome do remetente", max_length=120, blank=True)
    reply_to = models.EmailField(
        "e-mail de resposta",
        blank=True,
        help_text="Para onde vai a resposta do cliente. Vazio: o próprio remetente.",
    )
    admin_recipients = models.TextField(
        "e-mails que recebem os pedidos",
        blank=True,
        help_text=(
            "Um por linha, ou separados por vírgula. É para cá que vai a ordem "
            "de produção de cada pedido novo."
        ),
    )

    contact_recipients = models.TextField(
        "e-mails que recebem os contatos",
        blank=True,
        help_text=(
            "Um por linha, ou separados por vírgula. Recebem as mensagens do "
            "formulário de contato e os pedidos de revenda. "
            "Em branco, vão para quem recebe os pedidos."
        ),
    )

    last_test_at = models.DateTimeField("último teste em", null=True, blank=True)
    last_test_ok = models.BooleanField("último teste funcionou", default=False)
    last_test_message = models.CharField(
        "resultado do último teste", max_length=300, blank=True
    )

    class Meta:
        verbose_name = "configuração de e-mail"
        verbose_name_plural = "configuração de e-mail"

    def __str__(self) -> str:
        if not self.host:
            return "configuração de e-mail (sem servidor)"
        estado = "ativa" if self.is_active else "inativa"
        return f"{self.host}:{self.port} ({estado})"

    # -- a senha -----------------------------------------------------------

    @property
    def password(self) -> str:
        """A senha em texto puro. Só para montar a conexão SMTP."""
        from apps.core.secrets import decrypt

        return decrypt(self.password_encrypted)

    @password.setter
    def password(self, value: str) -> None:
        from apps.core.secrets import encrypt

        self.password_encrypted = encrypt(value or "")

    @property
    def has_password(self) -> bool:
        return bool(self.password_encrypted)

    # -- leitura -----------------------------------------------------------

    @property
    def sender(self) -> str:
        """"Nome <e-mail>", ou só o e-mail quando não há nome."""
        if not self.from_email:
            return ""
        if self.from_name:
            return f"{self.from_name} <{self.from_email}>"
        return self.from_email
    @staticmethod
    def _split(bruto: str) -> list[str]:
        """Uma lista de e-mails aceitando vírgula ou uma por linha."""
        return [
            linha.strip()
            for linha in (bruto or "").replace(",", "\n").splitlines()
            if linha.strip()
        ]

    def recipient_list(self) -> list[str]:
        """Os e-mails da equipe que recebem a ordem de produção."""
        return self._split(self.admin_recipients)

    @property
    def is_usable(self) -> bool:
        """Dá para enviar por aqui? Sem servidor, não dá."""
        return bool(self.is_active and self.host)

    # -- validação ---------------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}

        if self.use_tls and self.use_ssl:
            # Os dois juntos fazem o smtplib abrir SSL e depois pedir STARTTLS
            # numa conexão que já é cifrada: o servidor recusa e o erro que
            # chega ao administrador não diz nada sobre a causa.
            errors["use_tls"] = "Escolha TLS **ou** SSL, não os dois."
            errors["use_ssl"] = "Escolha TLS **ou** SSL, não os dois."

        if self.is_active and not self.host:
            errors["host"] = (
                "Informe o servidor SMTP antes de ativar — sem ele não há para "
                "onde enviar, e a loja pararia de mandar e-mail."
            )

        if self.is_active and not self.from_email:
            errors["from_email"] = "Informe o e-mail remetente antes de ativar."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Uma linha só: qualquer gravação assume o mesmo PK.

        Quem tentar um segundo ``objects.create()`` recebe um erro de chave
        primária do banco — e é o que se quer. A garantia de "uma configuração
        só" fica onde ela é mais forte, na tabela, e não numa convenção que
        alguém pode contornar. Para editar, use ``load()`` ou o Admin.
        """
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "EmailSettings":
        """A configuração atual — criando a linha vazia se ainda não existir."""
        obj, _criado = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def active(cls) -> "EmailSettings | None":
        """A configuração utilizável, ou ``None`` para cair no ``.env``."""
        obj = cls.objects.filter(pk=1, is_active=True).first()
        return obj if (obj is not None and obj.is_usable) else None


# ---------------------------------------------------------------------------
# Identidade visual
# ---------------------------------------------------------------------------


def brand_upload_to(instance, filename: str) -> str:
    """``media/brand/<arquivo>`` — pasta pública, como `products/` e `banners/`.

    Fora de `static/` de propósito. `static/` é código: vai no repositório, é
    coletado no deploy e só muda com um commit. Uma logo trocada pelo dono da
    loja numa terça-feira não é código.
    """
    return f"brand/{get_valid_filename(filename)}"


class BrandAssets(TimeStampedModel):
    """As imagens da marca, gerenciáveis sem passar por um programador.

    **Uma linha só** (``pk=1``), como `EmailSettings`: não é uma galeria, é *a*
    identidade da loja.

    Quatro imagens independentes, e a independência é o ponto. Antes, uma
    função procurava `jdprint-logo.svg` dentro de `static/images/logo/` e a
    mesma imagem servia header e rodapé — trocar a do rodapé por uma versão
    clara exigia um commit. Aqui cada uma tem o seu campo, o seu upload e o seu
    preview.

    **Todas opcionais.** Sem imagem cadastrada o site não quebra: o header e o
    rodapé mostram a marca tipográfica que já existia, a página sai sem
    ``<link rel="icon">`` e o produto sem foto continua com o espaço reservado.
    Uma loja recém-instalada funciona antes de alguém abrir esta tela.
    """

    header_logo = models.FileField(
        "logo do topo",
        upload_to=brand_upload_to,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=list(BRAND_IMAGE_EXTENSIONS)),
            validate_brand_image,
        ],
        help_text=(
            "<b>Onde aparece:</b> no cabeçalho de todas as páginas, sobre fundo "
            "claro.<br>"
            "<b>Tamanho recomendado:</b> 600 × 150 px (158,75 × 39,69 mm a 96 "
            "DPI). É uma recomendação, não uma exigência — qualquer imagem é "
            "aceita e o cabeçalho a ajusta para 40 px de altura.<br>"
            "SVG é o formato preferido: vetor não borra em tela retina, e aí a "
            "medida em pixels deixa de importar.<br>"
            "<b>Em branco:</b> o cabeçalho mostra a marca tipográfica. O site "
            "não quebra."
        ),
    )
    footer_logo = models.FileField(
        "logo do rodapé",
        upload_to=brand_upload_to,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=list(BRAND_IMAGE_EXTENSIONS)),
            validate_brand_image,
        ],
        help_text=(
            "<b>Onde aparece:</b> no rodapé, e <b>só</b> nele. É independente da "
            "logo do topo: o rodapé tem fundo escuro, e a mesma marca costuma "
            "sumir ali — normalmente se envia aqui a versão clara.<br>"
            "<b>Tamanho recomendado:</b> 500 × 150 px (132,29 × 39,69 mm a 96 "
            "DPI). Recomendação, não exigência.<br>"
            "<b>Em branco:</b> o rodapé mostra a marca tipográfica."
        ),
    )
    favicon = models.FileField(
        "favicon",
        upload_to=brand_upload_to,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=list(BRAND_IMAGE_EXTENSIONS)),
            validate_brand_image,
        ],
        help_text=(
            "<b>Onde aparece:</b> na aba do navegador e nos favoritos "
            "(<code>&lt;link rel=\"icon\"&gt;</code>), e no atalho "
            "<code>/favicon.ico</code>. Independente das duas logos: num "
            "quadrado de 32 px a marca completa não se lê, e o que funciona é o "
            "símbolo sozinho.<br>"
            "<b>Tamanho recomendado:</b> 512 × 512 px (135,47 × 135,47 mm a 96 "
            "DPI), quadrado. Recomendação, não exigência — o navegador "
            "redimensiona.<br>"
            "<b>Em branco:</b> a página sai sem ícone e o navegador mostra o "
            "dele."
        ),
    )
    product_placeholder = models.FileField(
        "imagem padrão dos produtos",
        upload_to=brand_upload_to,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=list(BRAND_IMAGE_EXTENSIONS)),
            validate_brand_image,
        ],
        help_text=(
            "<b>Onde aparece:</b> <b>apenas</b> nos produtos que ainda não têm "
            "foto própria. Nenhum produto com foto é afetado — esta imagem "
            "nunca substitui uma existente.<br>"
            "<b>Tamanho recomendado:</b> 1000 × 1000 px (264,58 × 264,58 mm a "
            "96 DPI), quadrada, porque o card do produto é 1:1. Recomendação, "
            "não exigência.<br>"
            "<b>Em branco:</b> aparece o espaço reservado com a inicial do "
            "produto — nunca a foto de outro produto."
        ),
    )

    class Meta:
        verbose_name = "logos e imagens"
        verbose_name_plural = "LOGOS E IMAGENS"

    def __str__(self) -> str:
        return "Logos e imagens"

    def save(self, *args, **kwargs):
        """Uma linha só, garantida pela chave primária e não por convenção."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "BrandAssets":
        obj, _criado = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def current(cls) -> "BrandAssets | None":
        """A linha, ou ``None`` numa instalação em que ninguém a abriu ainda."""
        return cls.objects.filter(pk=1).first()

    @classmethod
    def url_for(cls, campo: str) -> str:
        """A URL da imagem pedida, ou ``""`` quando não há imagem.

        Um lugar só para a pergunta que as tags de template fazem. Devolver
        string vazia — em vez de levantar — é o que permite ao template decidir
        entre a imagem e o espaço reservado com um `{% if %}`.
        """
        config = cls.current()
        if config is None:
            return ""
        arquivo = getattr(config, campo, None)
        return arquivo.url if arquivo else ""
