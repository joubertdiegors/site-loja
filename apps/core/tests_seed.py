"""Testes do comando de dados de demonstração."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.catalog.models import Product, ProductStatus
from apps.categories.models import Category
from apps.home.models import HomeBanner, HomeSection


# O test runner do Django roda com DEBUG=False; o comando exige DEBUG=True
# (ou --forcar) de propósito, então o ligamos aqui.
@override_settings(DEBUG=True)
class SeedDemoDataTests(TestCase):
    def seed(self, *args):
        output = StringIO()
        call_command("seed_demo_data", *args, stdout=output)
        return output.getvalue()

    def test_creates_catalogue_and_home(self):
        self.seed()

        self.assertGreater(Product.objects.count(), 0)
        self.assertGreater(Category.objects.count(), 0)
        self.assertGreater(HomeSection.objects.count(), 0)
        self.assertEqual(HomeBanner.objects.count(), 1)

    def test_products_are_active_and_named(self):
        self.seed()
        product = Product.objects.get(sku="DEMO-GATO-01")

        self.assertEqual(product.status, ProductStatus.ACTIVE)
        self.assertEqual(product.name_in("pt"), "Marcador de Página — Gato Pompom")
        self.assertEqual(product.name_in("fr"), "Marque-page — Chat Pompon")
        self.assertEqual(product.slug, "marcador-de-pagina-gato-pompom")

    def test_no_fake_media_is_created(self):
        self.seed()
        for product in Product.objects.all():
            self.assertEqual(product.media.count(), 0)

    def test_running_twice_changes_nothing(self):
        self.seed()
        counts = (Product.objects.count(), HomeSection.objects.count(), Category.objects.count())

        self.seed()

        self.assertEqual(
            counts,
            (Product.objects.count(), HomeSection.objects.count(), Category.objects.count()),
        )

    def test_does_not_touch_existing_products(self):
        from apps.core.testing import make_product

        mine = make_product(sku="MEU-01", name="Produto do dono")
        self.seed()

        mine.refresh_from_db()
        self.assertEqual(mine.name_in("pt"), "Produto do dono")

    def test_home_renders_after_seeding(self):
        self.seed()
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Destaques de Modelos")
        self.assertContains(response, "Filamentos em Destaque")

    def test_remove_only_deletes_demo_data(self):
        from apps.core.testing import make_product

        mine = make_product(sku="MEU-01", name="Produto do dono")
        self.seed()
        self.seed("--remover")

        self.assertFalse(Product.objects.filter(sku__startswith="DEMO-").exists())
        self.assertTrue(Product.objects.filter(pk=mine.pk).exists())
        self.assertEqual(HomeSection.objects.count(), 0)
        # Categorias, materiais e cores são preservados de propósito.
        self.assertGreater(Category.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_refuses_to_run_in_production_without_the_flag(self):
        with self.assertRaises(CommandError):
            self.seed()

    @override_settings(DEBUG=False)
    def test_runs_in_production_with_the_flag(self):
        self.seed("--forcar")
        self.assertGreater(Product.objects.count(), 0)
