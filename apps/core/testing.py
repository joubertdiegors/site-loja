"""Atalhos de criação de dados usados pelos testes.

Vive em ``core`` (e não dentro de um app de teste) porque Home, Shop e
Carrinho precisam dos mesmos objetos. Não é um módulo de teste: o runner não
o coleta.
"""

from decimal import Decimal

from django.conf import settings
from django.utils import translation

from apps.accounts.models import Customer, User
from apps.catalog.models import Product, ProductStatus, ProductTranslation, ProductVariant
from apps.categories.models import Category, CategoryTranslation
from apps.home.models import (
    HomeBanner,
    HomeBannerTranslation,
    HomeSection,
    HomeSectionLayout,
    HomeSectionProduct,
    HomeSectionTranslation,
    HomeSectionType,
)


def make_category(slug="modelos", name="Modelos", parent=None, is_active=True, **kwargs):
    category = Category.objects.create(slug=slug, parent=parent, is_active=is_active, **kwargs)
    CategoryTranslation.objects.create(master=category, language="pt", name=name)
    category.refresh_translations()
    return category


def translate_category(category, language, name):
    CategoryTranslation.objects.create(master=category, language=language, name=name)
    category.refresh_translations()
    return category


#: Campos que hoje são da variante. Um teste que passa ``price=`` ou
#: ``stock_quantity=`` para ``make_product`` está falando da variante — o
#: helper redireciona em vez de estourar, para os testes das etapas anteriores
#: continuarem legíveis.
VARIANT_FIELDS = (
    "sale_price",
    "stock_quantity",
    "weight_grams",
    "production_lead_time_days",
    "made_to_order",
    "allow_backorder",
    "width",
    "height",
    "depth",
    "dimension_unit",
    "print_time",
    "pricing_mode",
    "profit_margin",
    "filament_cost",
    "energy_cost",
    "color",
    "material",
    "size",
)


def make_product(
    sku="PROD-01",
    name="Produto de teste",
    category=None,
    price=Decimal("10.00"),
    status=ProductStatus.ACTIVE,
    with_variant=True,
    variant_sku=None,
    **kwargs,
):
    """Produto **com** a variante padrão — que é o que se vende.

    Desde a etapa 8 não existe produto vendável sem variante. O helper cria as
    duas coisas: o produto genérico e uma variante carregando preço, estoque,
    peso e prazo. Argumentos comerciais (``price``, ``stock_quantity``,
    ``weight_grams``…) vão para a variante.

    ``with_variant=False`` cria só o produto — serve para testar exatamente o
    caso "produto sem variante não é vendável".
    """
    variant_kwargs = {
        field: kwargs.pop(field) for field in VARIANT_FIELDS if field in kwargs
    }

    product = Product.objects.create(sku=sku, category=category, status=status, **kwargs)
    if name:
        ProductTranslation.objects.create(master=product, language="pt", name=name)
        product.refresh_translations()
        product.slug = ""
        product.save()

    if with_variant:
        variant_kwargs.setdefault("sale_price", price)
        make_variant(product, sku=variant_sku or sku, **variant_kwargs)
        product.refresh_from_db()

    return product


def make_variant(product, sku=None, price=None, stock=None, **kwargs):
    """Uma variante do produto: a unidade que se vende.

    ``price`` e ``stock`` são atalhos para ``sale_price`` e ``stock_quantity``,
    os dois valores que quase todo teste precisa dizer.
    """
    if price is not None:
        kwargs.setdefault("sale_price", price)
    if stock is not None:
        kwargs.setdefault("stock_quantity", stock)
    kwargs.setdefault("sale_price", Decimal("10.00"))

    base = sku or f"{product.sku}-V{product.variants.count() + 1}"
    candidate = base
    counter = 2
    while ProductVariant.objects.filter(sku=candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1

    return ProductVariant.objects.create(product=product, sku=candidate, **kwargs)


def translate_product(product, language, name, short_description=""):
    ProductTranslation.objects.create(
        master=product, language=language, name=name, short_description=short_description
    )
    product.refresh_translations()
    return product


def make_section(
    internal_name="Seção de teste",
    title="Seção de teste",
    section_type=HomeSectionType.FEATURED_PRODUCTS,
    layout=HomeSectionLayout.GRID,
    is_active=True,
    sort_order=0,
    product_limit=4,
    subtitle="",
    **kwargs,
):
    section = HomeSection.objects.create(
        internal_name=internal_name,
        section_type=section_type,
        layout=layout,
        is_active=is_active,
        sort_order=sort_order,
        product_limit=product_limit,
        **kwargs,
    )
    HomeSectionTranslation.objects.create(
        master=section, language="pt", title=title, subtitle=subtitle
    )
    section.refresh_translations()
    return section


def translate_section(section, language, title, subtitle="", cta_label=""):
    HomeSectionTranslation.objects.create(
        master=section, language=language, title=title, subtitle=subtitle, cta_label=cta_label
    )
    section.refresh_translations()
    return section


def add_products(section, products):
    for position, product in enumerate(products, start=1):
        HomeSectionProduct.objects.create(section=section, product=product, sort_order=position)
    return section


def make_banner(internal_name="Banner", title="Banner de teste", **kwargs):
    banner = HomeBanner.objects.create(internal_name=internal_name, **kwargs)
    HomeBannerTranslation.objects.create(master=banner, language="pt", title=title)
    banner.refresh_translations()
    return banner


def make_user(
    username="cliente",
    email=None,
    password="senha-de-teste-77",
    with_customer=True,
    **kwargs,
):
    """Conta de acesso (e o cliente vazio que nasce junto dela)."""
    user = User.objects.create_user(
        username=username,
        email=email or f"{username}@jdprint.test",
        password=password,
        **kwargs,
    )
    if with_customer:
        Customer.objects.create(user=user)
    return user


class LanguageResetMixin:
    """Garante que cada teste começa no idioma padrão.

    Uma requisição a ``/fr/`` ativa o francês na thread, e o Django não desfaz
    isso entre testes: sem este reset, a ordem em que os testes rodam mudaria o
    resultado dos que dependem do idioma. Não é um problema de produção — cada
    requisição HTTP ativa o seu próprio idioma —, é isolamento de teste.

    Pelo mesmo motivo esvazia o cache: as travas por IP (login, cadastro,
    reenvio de e-mail) contam lá, e a contagem sobrevive ao rollback.
    """

    def setUp(self):
        super().setUp()
        translation.activate(settings.LANGUAGE_CODE)
        self.addCleanup(translation.activate, settings.LANGUAGE_CODE)

        # O cache vive no processo, nao no banco: o rollback do `TestCase` nao
        # o esvazia. Sem isto, as travas por IP (login, cadastro, reenvio de
        # e-mail) atravessam de um teste para o outro e o resultado passa a
        # depender da ordem em que a suite roda.
        from django.core.cache import cache

        cache.clear()


# ---------------------------------------------------------------------------
# Etapa 7: países, frete e endereços
# ---------------------------------------------------------------------------


def make_country(iso_code="BE", name="Bélgica", is_active=True, vat_rate="21.00", **kwargs):
    """País de entrega já traduzido em português."""
    from apps.core.models import DeliveryCountry, DeliveryCountryTranslation

    country, _created = DeliveryCountry.objects.get_or_create(
        iso_code=iso_code,
        defaults={"is_active": is_active, "vat_rate": Decimal(vat_rate), **kwargs},
    )
    country.is_active = is_active
    country.vat_rate = Decimal(vat_rate)
    for field, value in kwargs.items():
        setattr(country, field, value)
    country.save()

    DeliveryCountryTranslation.objects.get_or_create(
        master=country, language="pt", defaults={"name": name}
    )
    country.refresh_translations()
    return country


def make_carrier(name="Bpost", code="bpost", **kwargs):
    from apps.shipping.models import ShippingCarrier

    carrier, _created = ShippingCarrier.objects.get_or_create(
        code=code, defaults={"name": name, **kwargs}
    )
    return carrier


def make_method(carrier=None, name="Standard", code="standard", min_days=2, max_days=3, **kwargs):
    from apps.shipping.models import ShippingMethod

    carrier = carrier or make_carrier()
    method, _created = ShippingMethod.objects.get_or_create(
        carrier=carrier,
        code=code,
        defaults={"name": name, "min_days": min_days, "max_days": max_days, **kwargs},
    )
    return method


def make_rate(method=None, country=None, min_weight=0, max_weight=2000, price="5.90", **kwargs):
    from apps.shipping.models import ShippingRate

    return ShippingRate.objects.create(
        method=method or make_method(),
        country=country or make_country(),
        min_weight_grams=min_weight,
        max_weight_grams=max_weight,
        price=Decimal(price),
        **kwargs,
    )


def make_bank_account(label="Principal", is_default=True, **kwargs):
    """A conta que recebe as transferências.

    Faz parte do cenário mínimo de uma loja que vende, como o país, o método de
    entrega e a tarifa: com ``PAYMENT_PROVIDER=transfer`` — o provedor do
    projeto —, ``create_order`` recusa a compra sem uma conta padrão ativa.

    Um teste que não fala de pagamento chama isto pelo mesmo motivo que chama
    ``make_rate``: para que exista uma loja capaz de vender. E como a conta é
    ignorada quando o provedor é outro, quem a cria funciona sob os dois — o
    resultado deixa de depender do ``.env`` de quem roda a suíte.
    """
    from apps.orders.models import BankAccount

    campos = {
        "beneficiary": "JD PRINT SRL",
        "iban": "BE68 5390 0754 7034",
        "bic": "GEBABEBB",
        "is_default": is_default,
    }
    campos.update(kwargs)
    return BankAccount.objects.create(label=label, **campos)


def make_address(customer, country=None, **kwargs):
    """Endereço completo de um cliente, com valores plausíveis por padrão."""
    from apps.accounts.models import CustomerAddress

    defaults = {
        "label": "Casa",
        "first_name": "Diego",
        "last_name": "Joubert",
        "street": "Rue du Test 12",
        "postal_code": "1000",
        "city": "Bruxelles",
        "country": country or make_country(),
    }
    defaults.update(kwargs)
    return CustomerAddress.objects.create(customer=customer, **defaults)
