"""Admin da entrega.

A tabela de preços é cadastrada de dentro para fora: transportadora →
método → faixas de peso por país. As faixas aparecem como inline do método,
que é como quem digita a grade da Bpost pensa — uma linha por país e faixa.
"""

from django.contrib import admin

from apps.core.admin_mixins import DuplicateAdminMixin
from apps.shipping.models import ShippingCarrier, ShippingMethod, ShippingRate


class ShippingMethodInline(admin.TabularInline):
    model = ShippingMethod
    extra = 0
    fields = ("name", "code", "min_days", "max_days", "is_active", "sort_order")
    show_change_link = True


@admin.register(ShippingCarrier)
class ShippingCarrierAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "method_count", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}
    inlines = (ShippingMethodInline,)
    fieldsets = (
        (None, {"fields": ("name", "code", "is_active", "sort_order")}),
        (
            "RASTREIO",
            {
                "fields": ("tracking_url_template",),
                "description": (
                    "Endereço de acompanhamento com <code>{tracking}</code> no lugar do "
                    "código. Sem ele, o pedido mostra o número sem link."
                ),
            },
        ),
    )

    @admin.display(description="métodos")
    def method_count(self, obj):
        return obj.methods.count()


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 1
    fields = ("country", "min_weight_grams", "max_weight_grams", "price", "is_active")
    autocomplete_fields = ("country",)


@admin.register(ShippingMethod)
class ShippingMethodAdmin(DuplicateAdminMixin):
    """Duplicar um método é copiar a **grade de preços** junto.

    "Bpost Standard" e "Bpost Express" têm a mesma lista de países e as mesmas
    faixas de peso; o que muda é o preço de cada linha e o prazo. Sem as
    tarifas, duplicar economizaria seis campos e deixaria quinze linhas para
    digitar de novo.

    O `code` vem copiado: com a `UniqueConstraint (carrier, code)`, salvar sem
    mexer em nada volta com "Método de entrega com este Transportadora e
    Código já existe" — que é a recusa certa, vinda da regra que já existia.
    """

    #: As tarifas acompanham. Cada uma nasce apontando para o método novo, e
    #: `ShippingRate.clean()` continua sendo quem impede faixas sobrepostas —
    #: agora dentro do método novo, onde a conta é outra.
    duplicate_inlines = {ShippingRate: ()}

    list_display = ("__str__", "delivery_days_display", "rate_count", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "carrier")
    search_fields = ("name", "code", "carrier__name")
    autocomplete_fields = ("carrier",)
    inlines = (ShippingRateInline,)
    fieldsets = (
        (None, {"fields": ("carrier", "name", "code", "description")}),
        (
            "PRAZO DE TRANSPORTE",
            {
                "fields": ("min_days", "max_days"),
                "description": (
                    "Em dias úteis, só o transporte. O prazo de produção vem do produto "
                    "e é somado a este no pedido."
                ),
            },
        ),
        ("EXIBIÇÃO", {"fields": ("is_active", "sort_order")}),
    )

    @admin.display(description="prazo (dias)")
    def delivery_days_display(self, obj):
        return obj.delivery_days_display

    @admin.display(description="faixas")
    def rate_count(self, obj):
        return obj.rates.count()


@admin.register(ShippingRate)
class ShippingRateAdmin(DuplicateAdminMixin):
    """A tabela mais repetitiva do projeto: país × faixa de peso × preço.

    Duplicar traz método, país, faixa e preço; troca-se o que muda — o país, ou
    a faixa seguinte — e salva. Salvar sem trocar nada é recusado por
    `ShippingRate.clean()`: uma cópia idêntica se sobrepõe à original, e duas
    faixas sobrepostas dariam dois preços para o mesmo pedido.
    """

    list_display = ("method", "country", "weight_range_display", "price", "is_active")
    list_editable = ("price", "is_active")
    list_filter = ("is_active", "country", "method__carrier")
    search_fields = ("method__name", "method__carrier__name", "country__iso_code")
    autocomplete_fields = ("method", "country")

    @admin.display(description="faixa de peso")
    def weight_range_display(self, obj):
        return obj.weight_range_display
