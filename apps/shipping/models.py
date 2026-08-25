"""Transportadoras, métodos e tarifas de entrega.

Três níveis em vez de um campo "frete = X":

* **Transportadora** (``ShippingCarrier``) — Bpost, DPD, GLS, Mondial Relay.
  É quem transporta e quem tem URL de rastreio.
* **Método** (``ShippingMethod``) — "Standard", "Express", "Ponto de retirada".
  É o que o cliente escolhe e é onde vive o **prazo de transporte**.
* **Tarifa** (``ShippingRate``) — país + faixa de peso + preço. É a linha da
  tabela de preços da transportadora.

Assim, acrescentar a DPD amanhã é cadastrar linhas no admin; nada aqui muda.

O que este app **não** faz nesta etapa: falar com a API de nenhuma
transportadora. O preço vem da tabela cadastrada, que é exatamente como as
transportadoras publicam suas grades.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class ShippingCarrierQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class ShippingCarrier(TimeStampedModel):
    """Quem transporta."""

    name = models.CharField("nome", max_length=80, unique=True)
    code = models.SlugField(
        "código",
        max_length=40,
        unique=True,
        help_text="Identificador curto e estável: bpost, dpd, gls...",
    )
    is_active = models.BooleanField("ativa", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)
    tracking_url_template = models.URLField(
        "URL de rastreio",
        blank=True,
        max_length=300,
        help_text=(
            "Endereço de acompanhamento com {tracking} no lugar do código. "
            "Ex.: https://track.bpost.cloud/btr/web/#/search?itemCode={tracking}"
        ),
    )

    objects = ShippingCarrierQuerySet.as_manager()

    class Meta:
        verbose_name = "transportadora"
        verbose_name_plural = "transportadoras"
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name

    def tracking_url(self, tracking_number: str) -> str:
        """Link de rastreio, ou string vazia quando não há como montar um."""
        if not tracking_number or "{tracking}" not in (self.tracking_url_template or ""):
            return ""
        return self.tracking_url_template.replace("{tracking}", tracking_number.strip())


class ShippingMethodQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True, carrier__is_active=True)


class ShippingMethod(TimeStampedModel):
    """O que o cliente escolhe no checkout.

    O prazo de **transporte** vive aqui, em dias úteis e como faixa (2 a 3
    dias), porque é assim que a transportadora se compromete. O prazo de
    **produção** vem do produto e é somado no pedido — ver
    ``apps.orders.services``.
    """

    carrier = models.ForeignKey(
        ShippingCarrier, verbose_name="transportadora", related_name="methods", on_delete=models.CASCADE
    )
    name = models.CharField("nome", max_length=80)
    code = models.SlugField(
        "código", max_length=40, help_text="Identificador curto: standard, express..."
    )
    description = models.CharField(
        "descrição", max_length=160, blank=True, help_text="Uma linha, mostrada no checkout."
    )
    min_days = models.PositiveIntegerField(
        "prazo mínimo (dias úteis)", default=2, help_text="Tempo de transporte, sem a produção."
    )
    max_days = models.PositiveIntegerField("prazo máximo (dias úteis)", default=3)
    is_active = models.BooleanField("ativo", default=True)
    sort_order = models.PositiveIntegerField("ordem", default=0)

    objects = ShippingMethodQuerySet.as_manager()

    class Meta:
        verbose_name = "método de entrega"
        verbose_name_plural = "métodos de entrega"
        ordering = ("sort_order", "carrier__name", "name")
        constraints = [
            models.UniqueConstraint(fields=("carrier", "code"), name="shipping_method_unique_code"),
        ]

    def __str__(self) -> str:
        return f"{self.carrier.name} — {self.name}"

    @property
    def label(self) -> str:
        return f"{self.carrier.name} {self.name}"

    @property
    def delivery_days_display(self) -> str:
        if self.min_days == self.max_days:
            return str(self.min_days)
        return f"{self.min_days}–{self.max_days}"

    def clean(self):
        super().clean()
        if self.max_days < self.min_days:
            raise ValidationError(
                {"max_days": "O prazo máximo não pode ser menor que o mínimo."}
            )


class ShippingRateQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True, method__is_active=True, method__carrier__is_active=True)

    def for_country(self, country):
        return self.filter(country=country)

    def for_weight(self, grams: int):
        """Faixas que contêm este peso.

        O limite inferior é inclusivo e o superior é **exclusivo**: 0–2000 e
        2000–5000 não se sobrepõem, e 2000 g cai na segunda faixa. Sem essa
        convenção, toda tabela de preço teria que ser cadastrada com 1999.
        """
        return self.filter(min_weight_grams__lte=grams).filter(
            models.Q(max_weight_grams__isnull=True) | models.Q(max_weight_grams__gt=grams)
        )


class ShippingRate(TimeStampedModel):
    """Uma linha da tabela de preços: país + faixa de peso + preço.

    ``max_weight_grams`` vazio significa "sem teto" — é a última faixa da
    tabela. Sem isso, um pedido de 12 kg simplesmente não teria frete e o
    cliente veria um checkout sem opção nenhuma, sem entender por quê.
    """

    method = models.ForeignKey(
        ShippingMethod, verbose_name="método", related_name="rates", on_delete=models.CASCADE
    )
    country = models.ForeignKey(
        "core.DeliveryCountry",
        verbose_name="país",
        related_name="shipping_rates",
        on_delete=models.CASCADE,
    )
    min_weight_grams = models.PositiveIntegerField(
        "peso mínimo (g)", default=0, help_text="Inclusivo."
    )
    max_weight_grams = models.PositiveIntegerField(
        "peso máximo (g)",
        null=True,
        blank=True,
        help_text="Exclusivo. Em branco = sem limite (última faixa).",
    )
    price = models.DecimalField(
        "preço",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_active = models.BooleanField("ativa", default=True)

    objects = ShippingRateQuerySet.as_manager()

    class Meta:
        verbose_name = "tarifa de entrega"
        verbose_name_plural = "tarifas de entrega"
        ordering = ("country__sort_order", "method__sort_order", "min_weight_grams")
        indexes = [
            models.Index(fields=("country", "min_weight_grams"), name="shipping_rate_lookup_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.method} · {self.country.iso_code} · {self.weight_range_display}"

    @property
    def weight_range_display(self) -> str:
        low = self.min_weight_grams / 1000
        if self.max_weight_grams is None:
            return f"{low:g} kg ou mais"
        return f"{low:g}–{self.max_weight_grams / 1000:g} kg"

    def contains(self, grams: int) -> bool:
        if grams < self.min_weight_grams:
            return False
        return self.max_weight_grams is None or grams < self.max_weight_grams

    def clean(self):
        super().clean()

        if self.max_weight_grams is not None and self.max_weight_grams <= self.min_weight_grams:
            raise ValidationError(
                {"max_weight_grams": "O peso máximo tem que ser maior que o mínimo."}
            )

        if self.method_id is None or self.country_id is None:
            return

        # Duas faixas sobrepostas para o mesmo método e país dariam dois preços
        # para o mesmo pedido — e o "certo" dependeria da ordenação da consulta.
        overlapping = ShippingRate.objects.filter(
            method_id=self.method_id, country_id=self.country_id
        ).exclude(pk=self.pk)
        for other in overlapping:
            starts_before_other_ends = (
                other.max_weight_grams is None or self.min_weight_grams < other.max_weight_grams
            )
            ends_after_other_starts = (
                self.max_weight_grams is None or self.max_weight_grams > other.min_weight_grams
            )
            if starts_before_other_ends and ends_after_other_starts:
                raise ValidationError(
                    "Esta faixa de peso se sobrepõe a outra já cadastrada "
                    f"para {self.method} em {other.country.iso_code}: {other.weight_range_display}."
                )
