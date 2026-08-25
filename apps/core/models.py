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
from django.db import models

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

    def get_translation(self, language: str | None = None, fallback: bool = True):
        """Retorna a tradução do idioma pedido.

        Ordem de fallback: idioma pedido -> idioma padrão (pt) -> qualquer
        tradução existente -> ``None``.
        """
        if self.pk is None:
            return None

        language = language or get_content_language()
        table = self.translations_by_language()

        translation = table.get(language)
        if translation is not None or not fallback:
            return translation

        translation = table.get(DEFAULT_LANGUAGE.value)
        if translation is not None:
            return translation

        return next(iter(table.values()), None)

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

    def clean(self):
        super().clean()
        self.iso_code = (self.iso_code or "").strip().upper()
        if len(self.iso_code) != 2 or not self.iso_code.isalpha():
            raise ValidationError({"iso_code": "Use o código ISO de duas letras (BE, FR, NL...)."})

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
