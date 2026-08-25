"""Endereços do cliente: cadastro, padrões e — sobretudo — quem enxerga o quê.

O endereço é dado pessoal: nome, telefone e onde a pessoa mora. Metade destes
testes existe para garantir que um cliente nunca alcança o endereço de outro,
nem por id na URL nem por formulário.
"""

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import CustomerAddress
from apps.core.testing import LanguageResetMixin, make_address, make_country, make_user

SENHA = "senha-de-teste-77"


def address_payload(country, **overrides):
    data = {
        "label": "Casa",
        "first_name": "Diego",
        "last_name": "Joubert",
        "company_name": "",
        "vat_number": "",
        "phone": "",
        "street": "Rue du Test 12",
        "street_extra": "",
        "postal_code": "1000",
        "city": "Bruxelles",
        "region": "",
        "country": country.pk,
    }
    data.update(overrides)
    return data


class AddressModelTests(TestCase):
    def setUp(self):
        self.user = make_user(username="diego3d")
        self.customer = self.user.customer
        self.country = make_country("BE")

    def test_customer_can_have_several_addresses(self):
        make_address(self.customer, self.country, label="Casa")
        make_address(self.customer, self.country, label="Trabalho")

        self.assertEqual(self.customer.addresses.count(), 2)

    def test_first_address_becomes_the_default_for_both(self):
        address = make_address(self.customer, self.country)

        self.assertTrue(address.is_default_shipping)
        self.assertTrue(address.is_default_billing)

    def test_only_one_default_shipping_per_customer(self):
        first = make_address(self.customer, self.country, label="Casa")
        second = make_address(self.customer, self.country, label="Trabalho")

        second.make_default(shipping=True)

        first.refresh_from_db()
        self.assertFalse(first.is_default_shipping)
        self.assertTrue(second.is_default_shipping)
        self.assertEqual(self.customer.addresses.filter(is_default_shipping=True).count(), 1)

    def test_shipping_and_billing_defaults_are_independent(self):
        home = make_address(self.customer, self.country, label="Casa")
        office = make_address(self.customer, self.country, label="Trabalho")

        office.make_default(billing=True)

        home.refresh_from_db()
        self.assertTrue(home.is_default_shipping)
        self.assertTrue(office.is_default_billing)
        self.assertFalse(home.is_default_billing)

    def test_vat_and_postal_code_are_normalized(self):
        address = make_address(
            self.customer, self.country, vat_number="be 0123 456 789", postal_code=" 1000 "
        )

        self.assertEqual(address.vat_number, "BE0123456789")
        self.assertEqual(address.postal_code, "1000")

    def test_lines_skip_what_is_empty(self):
        address = make_address(self.customer, self.country, street_extra="", region="")

        self.assertNotIn("", address.lines())
        self.assertIn("Rue du Test 12", address.lines())

    def test_recipient_can_differ_from_the_customer(self):
        """É o que torna "enviar como presente" possível sem um segundo sistema."""
        self.customer.first_name = "Diego"
        self.customer.save()

        address = make_address(self.customer, self.country, first_name="Marie", last_name="Dubois")

        self.assertEqual(address.full_name, "Marie Dubois")
        self.assertNotEqual(address.full_name, self.customer.full_name)


class AddressAccessTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE")
        self.owner = make_user(username="dono", email="dono@example.com")
        self.intruder = make_user(username="intruso", email="intruso@example.com")
        self.address = make_address(self.owner.customer, self.country)

    def test_anonymous_is_sent_to_login(self):
        for url in (
            reverse("accounts:addresses"),
            reverse("accounts:address_create"),
            reverse("accounts:address_edit", args=[self.address.pk]),
            reverse("accounts:address_delete", args=[self.address.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response["Location"])

    def test_another_customer_cannot_open_the_address(self):
        self.client.force_login(self.intruder)

        response = self.client.get(reverse("accounts:address_edit", args=[self.address.pk]))

        self.assertEqual(response.status_code, 404)

    def test_another_customer_cannot_edit_the_address(self):
        self.client.force_login(self.intruder)

        response = self.client.post(
            reverse("accounts:address_edit", args=[self.address.pk]),
            address_payload(self.country, city="Invadida"),
        )

        self.assertEqual(response.status_code, 404)
        self.address.refresh_from_db()
        self.assertEqual(self.address.city, "Bruxelles")

    def test_another_customer_cannot_delete_the_address(self):
        self.client.force_login(self.intruder)

        response = self.client.post(reverse("accounts:address_delete", args=[self.address.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(CustomerAddress.objects.filter(pk=self.address.pk).exists())

    def test_another_customer_cannot_make_it_default(self):
        self.client.force_login(self.intruder)

        response = self.client.post(
            reverse("accounts:address_default", args=[self.address.pk]), {"kind": "both"}
        )

        self.assertEqual(response.status_code, 404)

    def test_the_list_shows_only_my_addresses(self):
        make_address(self.intruder.customer, self.country, label="Do intruso", city="Antwerpen")
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:addresses"))

        self.assertContains(response, "Bruxelles")
        self.assertNotContains(response, "Do intruso")


class AddressFormTests(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.country = make_country("BE")
        self.user = make_user(username="diego3d")
        self.client.force_login(self.user)

    def test_address_is_created_for_the_logged_customer(self):
        response = self.client.post(
            reverse("accounts:address_create"), address_payload(self.country)
        )

        self.assertRedirects(response, reverse("accounts:addresses"))
        address = CustomerAddress.objects.get()
        self.assertEqual(address.customer, self.user.customer)

    def test_incomplete_address_is_refused(self):
        response = self.client.post(
            reverse("accounts:address_create"), address_payload(self.country, street="")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerAddress.objects.exists())

    def test_only_active_countries_are_offered(self):
        """A lista sai do banco: desligar um país no admin o tira do formulário."""
        make_country("DE", "Alemanha", is_active=False)

        response = self.client.get(reverse("accounts:address_create"))
        offered = [country.iso_code for country in response.context["form"].fields["country"].queryset]

        self.assertIn("BE", offered)
        self.assertNotIn("DE", offered)

    def test_inactive_country_is_refused_by_the_form(self):
        germany = make_country("DE", "Alemanha", is_active=False)

        response = self.client.post(
            reverse("accounts:address_create"), address_payload(germany)
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerAddress.objects.exists())

    def test_next_takes_the_customer_back_to_the_checkout(self):
        """Quem veio do checkout volta para lá — está no meio de uma compra."""
        checkout = reverse("cart:checkout")
        response = self.client.post(
            f"{reverse('accounts:address_create')}?next={checkout}",
            {**address_payload(self.country), "next": checkout},
        )

        self.assertRedirects(response, checkout)

    def test_external_next_is_ignored(self):
        response = self.client.post(
            reverse("accounts:address_create"),
            {**address_payload(self.country), "next": "https://site-falso.example/"},
        )

        self.assertRedirects(response, reverse("accounts:addresses"))

    def test_default_can_be_changed_by_post(self):
        first = make_address(self.user.customer, self.country, label="Casa")
        second = make_address(self.user.customer, self.country, label="Trabalho")

        response = self.client.post(
            reverse("accounts:address_default", args=[second.pk]), {"kind": "both"}
        )

        self.assertRedirects(response, reverse("accounts:addresses"))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default_shipping)
        self.assertTrue(second.is_default_shipping)

    def test_changing_the_default_requires_post(self):
        """Mudar dado por GET seria um link que qualquer imagem poderia disparar."""
        address = make_address(self.user.customer, self.country)

        response = self.client.get(reverse("accounts:address_default", args=[address.pk]))

        self.assertEqual(response.status_code, 405)
