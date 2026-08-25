"""O caminho inteiro, do jeito que um cliente faz.

Cada peça já tem teste próprio; este arquivo garante que elas funcionam
*juntas* — é onde aparecem os erros que nenhum teste isolado pega, como o
carrinho que se perde entre o cadastro e a primeira volta à loja.
"""

import re
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Customer, User
from apps.cart.models import CartItem
from apps.cart.storage import CART_SESSION_KEY
from apps.core.testing import LanguageResetMixin, make_category, make_product, make_user

SIGNUP = {
    "username": "diego3d",
    "email": "diego@example.com",
    "password1": "vaso-facetado-77",
    "password2": "vaso-facetado-77",
}


def path_from_email(message) -> str:
    """Caminho do link do e-mail, sem o domínio."""
    found = re.search(r"https?://[^\s]+/conta/[^\s]+", message.body)
    return "/conta/" + found.group(0).split("/conta/")[1] if found else ""


class FullJourneyTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        category = make_category(slug="modelos", name="Modelos")
        self.product = make_product(
            sku="VASO-01",
            name="Vaso Facetado",
            category=category,
            price=Decimal("19.90"),
            stock_quantity=10,
        )

    def cart_quantity(self):
        return self.client.get(reverse("cart:detail")).context["cart"].total_quantity

    def test_visitor_becomes_customer_without_losing_anything(self):
        # 1. visitante adiciona um produto e abre o carrinho
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2})
        drawer = self.client.get(reverse("cart:drawer"))
        self.assertContains(drawer, "Vaso Facetado")
        self.assertContains(drawer, "Não perca seu carrinho")  # convite para criar conta

        # 2. cria a conta
        response = self.client.post(reverse("accounts:register"), SIGNUP)
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(username="diego3d")
        self.assertTrue(Customer.objects.filter(user=user).exists())
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertFalse(user.email_verified)

        # 3. o carrinho virou carrinho da conta
        self.assertEqual(self.cart_quantity(), 2)
        self.assertEqual(CartItem.objects.filter(cart__user=user).count(), 1)
        self.assertFalse(self.client.session.get(CART_SESSION_KEY))

        # 4. confirma o e-mail pelo link recebido
        self.assertEqual(len(mail.outbox), 1)
        self.client.get(path_from_email(mail.outbox[0]))
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

        # 5. sai
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.cart_quantity(), 0)

        # 6. entra pelo username: o carrinho volta
        self.client.post(
            reverse("accounts:login"),
            {"username": "diego3d", "password": "vaso-facetado-77"},
        )
        self.assertEqual(self.cart_quantity(), 2)

        # 7. sai e entra pelo e-mail: o carrinho volta de novo
        self.client.post(reverse("accounts:logout"))
        self.client.post(
            reverse("accounts:login"),
            {"username": "DIEGO@example.com", "password": "vaso-facetado-77"},
        )
        self.assertEqual(self.cart_quantity(), 2)

        # 8. nada se duplicou pelo caminho
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(CartItem.objects.count(), 1)

    def test_visitor_cart_merges_into_an_existing_one(self):
        other = make_product(
            sku="LUM-01",
            name="Luminária",
            category=self.product.category,
            price=Decimal("34.00"),
            stock_quantity=5,
        )
        user = make_user(username="maria", password="vaso-facetado-77")

        # carrinho antigo da conta
        self.client.force_login(user)
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 1})
        self.client.post(reverse("cart:add"), {"product_id": other.pk, "quantity": 3})
        self.client.logout()

        # carrinho do visitante, no mesmo navegador
        self.client.post(reverse("cart:add"), {"product_id": self.product.pk, "quantity": 2})

        self.client.post(
            reverse("accounts:login"), {"username": "maria", "password": "vaso-facetado-77"}
        )

        merged = {
            item.product.sku: item.quantity
            for item in CartItem.objects.select_related("product")
        }
        self.assertEqual(merged, {"VASO-01": 3, "LUM-01": 3})
        self.assertEqual(self.cart_quantity(), 6)

    def test_password_recovery_end_to_end(self):
        make_user(username="diego3d", email="diego@example.com", password="vaso-facetado-77")

        self.client.post(reverse("accounts:password_reset"), {"email": "diego@example.com"})
        self.assertEqual(len(mail.outbox), 1)

        response = self.client.get(path_from_email(mail.outbox[0]), follow=True)
        form_url = response.redirect_chain[-1][0]
        self.client.post(
            form_url,
            {"new_password1": "nova-senha-forte-88", "new_password2": "nova-senha-forte-88"},
        )

        entered = self.client.post(
            reverse("accounts:login"),
            {"username": "diego3d", "password": "nova-senha-forte-88"},
        )
        self.assertEqual(entered.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class AccountInEveryLanguageTests(LanguageResetMixin, TestCase):
    """As telas de conta existem nos quatro idiomas oferecidos hoje."""

    def test_pages_answer_in_every_active_language(self):
        for prefix in ("", "/fr", "/nl", "/en"):
            for path in ("/conta/entrar/", "/conta/criar/", "/conta/senha/"):
                with self.subTest(url=f"{prefix}{path}"):
                    response = self.client.get(f"{prefix}{path}")
                    self.assertEqual(response.status_code, 200)

    def test_language_that_is_off_redirects(self):
        response = self.client.get("/de/conta/entrar/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/conta/entrar/")

    def test_selector_is_present_on_the_account_pages(self):
        response = self.client.get("/conta/entrar/")

        self.assertContains(response, 'value="fr"')
        self.assertContains(response, 'value="nl"')
        self.assertNotContains(response, 'value="de"')

    def test_signing_up_in_dutch_keeps_the_customer_in_dutch(self):
        self.client.post("/nl/conta/criar/", SIGNUP)

        user = User.objects.get()
        self.assertEqual(user.preferred_language, "nl")
        self.assertIn("/nl/conta/confirmar/", mail.outbox[0].body)
