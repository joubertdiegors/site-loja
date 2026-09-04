"""Testes do gerenciamento das seções pelo Django Admin.

Cobrem o caminho real do administrador: criar, traduzir, escolher produtos,
ordenar, ativar/desativar e duplicar — tudo sem tocar em código.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.home.models import (
    CtaTarget,
    HomeBanner,
    HomeSection,
    HomeSectionLayout,
    HomeSectionProduct,
    HomeSectionType,
)
from apps.core.testing import add_products, make_category, make_product, make_section


def inline_payload(prefix, rows, initial=0):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f"{prefix}-{index}-{field}"] = value
    return data


class HomeSectionAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@jdprint.test", password="senha-de-teste"
        )
        cls.category = make_category(slug="modelos", name="Modelos")
        cls.first = make_product(sku="P-1", name="Gato Pompom", category=cls.category)
        cls.second = make_product(sku="P-2", name="Dragão", category=cls.category)

    def setUp(self):
        self.client.force_login(self.user)

    def payload(self, **overrides):
        data = {
            "internal_name": "Destaques de Modelos",
            "is_active": "on",
            "sort_order": "1",
            "section_type": HomeSectionType.FEATURED_PRODUCTS,
            "layout": HomeSectionLayout.GRID,
            "product_limit": "4",
            "category": "",
            "include_subcategories": "on",
            "cta_target": CtaTarget.NONE,
            "cta_category": "",
            "cta_product": "",
            "cta_url": "",
        }
        data.update(
            inline_payload(
                "translations",
                [
                    {
                        "language": "pt",
                        "title": "Destaques de Modelos",
                        "subtitle": "Confira alguns dos nossos modelos favoritos.",
                        "cta_label": "",
                        "id": "",
                        "master": "",
                    }
                ],
            )
        )
        data.update(inline_payload("items", []))
        data.update(overrides)
        return data

    # -- páginas ------------------------------------------------------------

    def test_add_page_loads(self):
        response = self.client.get(reverse("admin:home_homesection_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IDENTIFICAÇÃO")
        self.assertContains(response, "CONTEÚDO DA SEÇÃO")

    def test_changelist_loads(self):
        make_section(internal_name="Destaques", title="Destaques")
        response = self.client.get(reverse("admin:home_homesection_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Destaques")

    def test_conditional_fields_script_is_loaded(self):
        response = self.client.get(reverse("admin:home_homesection_add"))
        self.assertContains(response, "home_section_admin.js")

    def test_home_app_is_visible_in_the_admin_index(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Seções da Home")
        self.assertContains(response, "Banners da Home")

    # -- criação ------------------------------------------------------------

    def test_creates_a_section_with_translation(self):
        response = self.client.post(
            reverse("admin:home_homesection_add"), self.payload(), follow=True
        )
        self.assertEqual(response.status_code, 200)

        section = HomeSection.objects.get(internal_name="Destaques de Modelos")
        self.assertEqual(section.title, "Destaques de Modelos")
        self.assertEqual(section.subtitle, "Confira alguns dos nossos modelos favoritos.")
        self.assertTrue(section.is_active)

    def test_portuguese_translation_is_required(self):
        payload = self.payload()
        payload["translations-0-language"] = "fr"
        payload["translations-0-title"] = "Modèles à la une"

        response = self.client.post(reverse("admin:home_homesection_add"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(HomeSection.objects.filter(internal_name="Destaques de Modelos").exists())

    def test_category_type_requires_a_category(self):
        payload = self.payload(section_type=HomeSectionType.CATEGORY_PRODUCTS, category="")
        response = self.client.post(reverse("admin:home_homesection_add"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(HomeSection.objects.exists())

    def test_creates_a_manual_section_with_ordered_products(self):
        payload = self.payload(section_type=HomeSectionType.MANUAL_PRODUCTS)
        payload.update(
            inline_payload(
                "items",
                [
                    {"product": str(self.second.pk), "sort_order": "1", "id": "", "section": ""},
                    {"product": str(self.first.pk), "sort_order": "2", "id": "", "section": ""},
                ],
            )
        )

        self.client.post(reverse("admin:home_homesection_add"), payload, follow=True)

        section = HomeSection.objects.get(internal_name="Destaques de Modelos")
        ordered = [item.product for item in section.items.all()]
        self.assertEqual(ordered, [self.second, self.first])

    def test_creates_a_section_with_a_category_cta(self):
        payload = self.payload(
            cta_target=CtaTarget.CATEGORY, cta_category=str(self.category.pk)
        )
        self.client.post(reverse("admin:home_homesection_add"), payload, follow=True)

        section = HomeSection.objects.get(internal_name="Destaques de Modelos")
        self.assertTrue(section.has_cta)
        self.assertEqual(section.cta_link, self.category.get_absolute_url())

    # -- edição -------------------------------------------------------------

    def test_edits_an_existing_section(self):
        section = make_section(internal_name="Destaques", title="Título antigo")
        translation = section.translations.get(language="pt")

        payload = self.payload(internal_name="Destaques", sort_order="5")
        payload.update(
            inline_payload(
                "translations",
                [
                    {
                        "language": "pt",
                        "title": "Título novo",
                        "subtitle": "",
                        "cta_label": "",
                        "id": str(translation.pk),
                        "master": str(section.pk),
                    }
                ],
                initial=1,
            )
        )
        payload.update(inline_payload("items", []))

        self.client.post(
            reverse("admin:home_homesection_change", args=[section.pk]), payload, follow=True
        )

        section.refresh_from_db()
        section.refresh_translations()
        self.assertEqual(section.title, "Título novo")
        self.assertEqual(section.sort_order, 5)

    def test_bulk_reorder_from_the_changelist(self):
        first = make_section(internal_name="A", title="A", sort_order=1)
        second = make_section(internal_name="B", title="B", sort_order=2)

        payload = {
            "form-TOTAL_FORMS": "2",
            "form-INITIAL_FORMS": "2",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(first.pk),
            "form-0-sort_order": "10",
            "form-0-is_active": "on",
            "form-1-id": str(second.pk),
            "form-1-sort_order": "2",
            "form-1-is_active": "on",
            "_save": "Salvar",
        }
        response = self.client.post(
            reverse("admin:home_homesection_changelist"), payload, follow=True
        )

        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.sort_order, 10)

    # -- ações --------------------------------------------------------------

    def run_action(self, action, sections):
        return self.client.post(
            reverse("admin:home_homesection_changelist"),
            {
                "action": action,
                "_selected_action": [str(section.pk) for section in sections],
            },
            follow=True,
        )

    def test_deactivate_action(self):
        section = make_section(internal_name="Natal", title="Promoção de Natal")
        self.run_action("action_deactivate", [section])

        section.refresh_from_db()
        self.assertFalse(section.is_active)

    def test_activate_action(self):
        section = make_section(internal_name="Natal", title="Natal", is_active=False)
        self.run_action("action_activate", [section])

        section.refresh_from_db()
        self.assertTrue(section.is_active)

    def test_duplicate_action_copies_translations_and_products(self):
        section = make_section(
            internal_name="Nova Coleção",
            title="Nova Coleção",
            section_type=HomeSectionType.MANUAL_PRODUCTS,
        )
        add_products(section, [self.first, self.second])

        self.run_action("action_duplicate", [section])

        copy = HomeSection.objects.get(internal_name="Nova Coleção (cópia)")
        self.assertFalse(copy.is_active)
        self.assertEqual(copy.title, "Nova Coleção")
        self.assertEqual(copy.items.count(), 2)
        self.assertEqual(HomeSectionProduct.objects.filter(section=section).count(), 2)

    def test_best_sellers_warning_is_shown(self):
        payload = self.payload(section_type=HomeSectionType.BEST_SELLERS)
        response = self.client.post(
            reverse("admin:home_homesection_add"), payload, follow=True
        )
        self.assertContains(response, "módulo de pedidos")


class HomeBannerAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="admin2", email="admin2@jdprint.test", password="senha-de-teste"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_add_page_loads(self):
        response = self.client.get(reverse("admin:home_homebanner_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IMAGENS")

    def test_creates_a_banner(self):
        payload = {
            "internal_name": "Boas-vindas",
            "layout": "editorial",
            "is_active": "on",
            "sort_order": "1",
            "cta_target": CtaTarget.NONE,
            "cta_category": "",
            "cta_product": "",
            "cta_url": "",
            "cta_secondary_url": "",
            "plate_color": "purple",
            "frame_color": "lavender",
            "surface_color": "cream",
        }
        payload.update(
            inline_payload(
                "translations",
                [
                    {
                        "language": "pt",
                        "title": "Peças sob medida",
                        "subtitle": "",
                        "cta_label": "",
                        "image_alt": "",
                        "id": "",
                        "master": "",
                    }
                ],
            )
        )

        self.client.post(reverse("admin:home_homebanner_add"), payload, follow=True)

        from apps.home.models import HomeBanner

        banner = HomeBanner.objects.get(internal_name="Boas-vindas")
        self.assertEqual(banner.title, "Peças sob medida")


class AdminPermissionTests(TestCase):
    def test_anonymous_cannot_reach_the_section_admin(self):
        response = self.client.get(reverse("admin:home_homesection_changelist"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_regular_user_cannot_reach_the_section_admin(self):
        get_user_model().objects.create_user(
            username="joao", email="joao@jdprint.test", password="senha-de-teste"
        )
        self.client.login(username="joao", password="senha-de-teste")

        response = self.client.get(reverse("admin:home_homesection_changelist"))
        self.assertEqual(response.status_code, 302)


class BannerMedidaTests(TestCase):
    """A medida da arte tem que estar escrita onde ela é usada.

    Quem cadastra um banner não abre o model nem a documentação: olha a tela.
    Por isso a medida aparece nos dois lugares que a tela mostra — o texto de
    ajuda do campo e a descrição da seção — e o teste cobra os dois.

    A medida também mora no `help_text` do model, e não só no Admin, porque
    ela é propriedade do campo: quem ler o model, um serializer ou uma tela
    futura tem que encontrar o mesmo número.
    """

    MEDIDA = "1920 × 700"
    PROPORCAO = "2,74:1"

    @classmethod
    def setUpTestData(cls):
        cls.chefe = get_user_model().objects.create_superuser(
            "chefe-banner", "chefe-banner@jdprint.test", "senha-de-teste-77"
        )

    def setUp(self):
        self.client.force_login(self.chefe)

    def test_the_field_help_text_carries_the_exact_size(self):
        campo = HomeBanner._meta.get_field("image_desktop")

        self.assertIn(self.MEDIDA, campo.help_text)
        self.assertIn(self.PROPORCAO, campo.help_text)

    def test_the_add_screen_shows_the_size(self):
        resposta = self.client.get(reverse("admin:home_homebanner_add"))

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.content.decode()
        self.assertIn(self.MEDIDA, corpo)
        self.assertIn(self.PROPORCAO, corpo)

    def test_the_section_description_explains_what_happens_outside_the_size(self):
        """Só o número não basta: quem cadastra precisa saber o que se perde."""
        resposta = self.client.get(reverse("admin:home_homebanner_add"))

        self.assertContains(resposta, "cortada pelo centro")

    def test_the_old_recommendation_is_gone(self):
        """Duas medidas diferentes na mesma tela seriam pior que nenhuma."""
        campo = HomeBanner._meta.get_field("image_desktop")
        corpo = self.client.get(reverse("admin:home_homebanner_add")).content.decode()

        self.assertNotIn("1920×720", campo.help_text)
        self.assertNotIn("1920×720", corpo)
