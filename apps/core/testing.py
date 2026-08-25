"""Atalhos de criação de dados usados pelos testes.

Vive em ``core`` (e não dentro de um app de teste) porque Home, Shop e
Carrinho precisam dos mesmos objetos. Não é um módulo de teste: o runner não
o coleta.
"""

from decimal import Decimal

from django.conf import settings
from django.utils import translation

from apps.accounts.models import Customer, User
from apps.catalog.models import Product, ProductStatus, ProductTranslation
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


def make_product(
    sku="PROD-01",
    name="Produto de teste",
    category=None,
    price=Decimal("10.00"),
    status=ProductStatus.ACTIVE,
    **kwargs,
):
    product = Product.objects.create(
        sku=sku, category=category, sale_price=price, status=status, **kwargs
    )
    if name:
        ProductTranslation.objects.create(master=product, language="pt", name=name)
        product.refresh_translations()
        product.slug = ""
        product.save()
    return product


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
    """

    def setUp(self):
        super().setUp()
        translation.activate(settings.LANGUAGE_CODE)
        self.addCleanup(translation.activate, settings.LANGUAGE_CODE)


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


def make_shipping_setup(country=None, price="5.90"):
    """Transportadora + método + tarifa: o mínimo para um checkout funcionar."""
    country = country or make_country()
    method = make_method()
    make_rate(method=method, country=country, min_weight=0, max_weight=2000, price=price)
    return country, method
