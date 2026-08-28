"""Favoritos — etapa 17.

O favorito é **do produto e do usuário**, e mais nada. Não guarda variante, não
existe sem conta, e não é apagado porque o produto saiu do ar.

O que estes testes guardam, em ordem de importância:

1. **ninguém mexe na lista de outra pessoa** — o servidor decide de quem é o
   favorito pelo usuário da requisição, e não por um id vindo do navegador;
2. **o coração não custa uma consulta por card** — era o jeito mais fácil de
   uma vitrine de 24 produtos virar 24 consultas;
3. **produto fora do ar some da lista, não da conta** — se voltar, o favorito
   continua lá.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.accounts.models import Favorite
from apps.catalog.models import ProductStatus
from apps.core.testing import (
    LanguageResetMixin,
    make_category,
    make_product,
    translate_product,
)

FAVORITOS = "/favoritos/"
ALTERNAR = "/favoritos/alternar/"
SENHA = "senha-bem-comprida"


class FavoriteBase(LanguageResetMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.User = get_user_model()
        self.ana = self.User.objects.create_user(
            username="ana", email="ana@jdprint.test", password=SENHA
        )
        self.bruno = self.User.objects.create_user(
            username="bruno", email="bruno@jdprint.test", password=SENHA
        )

        self.categoria = make_category(slug="modelos", name="Modelos")
        self.gato = make_product(
            sku="GATO-01", name="Gato Pompom", category=self.categoria,
            price=Decimal("8.90"), stock_quantity=5,
        )
        self.vaso = make_product(
            sku="VASO-77", name="Vaso Facetado", category=self.categoria,
            price=Decimal("19.90"), stock_quantity=3,
        )

    def entrar(self, user=None):
        self.client.force_login(user or self.ana)

    def alternar(self, product, **extra):
        return self.client.post(ALTERNAR, {"product_id": product.pk, **extra})

    def skus(self, resposta):
        return [product.sku for product in resposta.context["favorites"]]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class FavoriteModelTests(FavoriteBase):
    def test_creating_a_favorite(self):
        favorito = Favorite.objects.create(user=self.ana, product=self.gato)

        self.assertEqual(favorito.user, self.ana)
        self.assertEqual(favorito.product, self.gato)
        self.assertIsNotNone(favorito.created_at)

    def test_the_same_user_cannot_favorite_twice(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Favorite.objects.create(user=self.ana, product=self.gato)

    def test_two_users_can_favorite_the_same_product(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.bruno, product=self.gato)

        self.assertEqual(Favorite.objects.filter(product=self.gato).count(), 2)

    def test_the_same_user_can_favorite_several_products(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)

        self.assertEqual(Favorite.objects.for_user(self.ana).count(), 2)

    def test_the_newest_comes_first(self):
        """A ordem em que o cliente pensa na própria lista."""
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)

        produtos = [f.product.sku for f in Favorite.objects.for_user(self.ana)]

        self.assertEqual(produtos, ["VASO-77", "GATO-01"])

    def test_deleting_the_user_deletes_the_favorite(self):
        """Sem a conta, a linha não significa mais nada."""
        Favorite.objects.create(user=self.ana, product=self.gato)

        self.ana.delete()

        self.assertEqual(Favorite.objects.count(), 0)

    def test_deleting_the_product_deletes_the_favorite(self):
        """Favorito órfão seria um erro esperando a próxima listagem."""
        Favorite.objects.create(user=self.ana, product=self.gato)

        self.gato.delete()

        self.assertEqual(Favorite.objects.count(), 0)

    def test_a_favorite_has_no_variant(self):
        """É do produto: escolher outra cor não muda o que foi guardado."""
        campos = {campo.name for campo in Favorite._meta.get_fields()}

        self.assertNotIn("variant", campos)
        self.assertNotIn("color", campos)
        self.assertNotIn("size", campos)

    def test_visible_hides_a_draft_product(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        self.gato.status = ProductStatus.DRAFT
        self.gato.save()

        self.assertEqual(Favorite.objects.for_user(self.ana).count(), 1)
        self.assertEqual(Favorite.objects.for_user(self.ana).visible().count(), 0)


# ---------------------------------------------------------------------------
# Visitante
# ---------------------------------------------------------------------------


class AnonymousVisitorTests(FavoriteBase):
    def test_the_visitor_sees_the_heart(self):
        resposta = self.client.get("/modelos/")

        self.assertContains(resposta, "data-favorite-login")

    def test_the_visitor_gets_a_link_to_the_login_not_a_form(self):
        """Botão que falha e registro anônimo que expira são as duas armadilhas."""
        html = self.client.get("/modelos/").content.decode()

        self.assertNotIn("data-favorite-button", html)
        self.assertIn("/conta/entrar/?next=", html)

    def test_posting_without_a_session_creates_nothing(self):
        resposta = self.alternar(self.gato)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/conta/entrar/", resposta.url)
        self.assertEqual(Favorite.objects.count(), 0)

    def test_the_header_heart_leads_to_the_login(self):
        html = self.client.get("/").content.decode()

        self.assertIn('id="favorites-link"', html)
        self.assertIn("/conta/entrar/?next=/favoritos/", html)

    def test_the_favorites_page_requires_login(self):
        resposta = self.client.get(FAVORITOS)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/conta/entrar/", resposta.url)

    def test_after_logging_in_the_visitor_can_favorite(self):
        """§2: depois do login, o clique tem de funcionar."""
        self.client.post("/conta/entrar/", {"username": "ana", "password": SENHA}, follow=True)
        self.alternar(self.gato)

        self.assertTrue(Favorite.objects.filter(user=self.ana, product=self.gato).exists())


# ---------------------------------------------------------------------------
# Alternar
# ---------------------------------------------------------------------------


class ToggleTests(FavoriteBase):
    def setUp(self):
        super().setUp()
        self.entrar()

    def test_adding(self):
        resposta = self.alternar(self.gato)

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(Favorite.objects.filter(user=self.ana, product=self.gato).exists())

    def test_clicking_again_removes(self):
        self.alternar(self.gato)
        self.alternar(self.gato)

        self.assertFalse(Favorite.objects.filter(user=self.ana, product=self.gato).exists())

    def test_it_is_idempotent(self):
        """Duplo clique, reenvio do navegador, corrida: um estado final só."""
        for _ in range(5):
            self.alternar(self.gato)

        self.assertLessEqual(Favorite.objects.filter(user=self.ana).count(), 1)

    def test_never_two_rows_for_the_same_pair(self):
        for _ in range(6):
            self.alternar(self.gato)
            self.assertLessEqual(
                Favorite.objects.filter(user=self.ana, product=self.gato).count(), 1
            )

    def test_a_get_is_refused(self):
        self.assertEqual(self.client.get(ALTERNAR).status_code, 405)

    def test_an_unknown_product_is_404(self):
        resposta = self.client.post(ALTERNAR, {"product_id": 999999})

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(Favorite.objects.count(), 0)

    def test_a_missing_product_id_is_404(self):
        self.assertEqual(self.client.post(ALTERNAR, {}).status_code, 404)

    def test_garbage_as_product_id_does_not_break(self):
        for lixo in ("abc", "'; DROP TABLE catalog_product; --", "-1", "1.5"):
            with self.subTest(lixo=lixo):
                resposta = self.client.post(ALTERNAR, {"product_id": lixo})
                self.assertIn(resposta.status_code, (404, 400))
        self.assertEqual(Favorite.objects.count(), 0)

    def test_a_draft_product_cannot_be_favorited(self):
        """Guardar um rascunho seria guardar algo que ele não pode ver."""
        self.gato.status = ProductStatus.DRAFT
        self.gato.save()

        self.assertEqual(self.alternar(self.gato).status_code, 404)
        self.assertEqual(Favorite.objects.count(), 0)

    def test_a_product_without_an_active_variant_cannot_be_favorited(self):
        self.gato.variants.update(is_active=False)

        self.assertEqual(self.alternar(self.gato).status_code, 404)

    def test_csrf_is_required(self):
        cliente = self.client_class(enforce_csrf_checks=True)
        cliente.force_login(self.ana)

        resposta = cliente.post(ALTERNAR, {"product_id": self.gato.pk})

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(Favorite.objects.count(), 0)

    def test_the_redirect_refuses_an_external_host(self):
        resposta = self.alternar(self.gato, next="https://exemplo-malicioso.test/")

        self.assertNotIn("exemplo-malicioso", resposta.url)


# ---------------------------------------------------------------------------
# Segurança: a lista é de quem está autenticado
# ---------------------------------------------------------------------------


class FavoriteSecurityTests(FavoriteBase):
    def test_a_user_id_from_the_browser_is_ignored(self):
        """O navegador diz *o que*, nunca *de quem*."""
        self.entrar(self.ana)

        self.alternar(self.gato, user=self.bruno.pk, user_id=self.bruno.pk)

        self.assertTrue(Favorite.objects.filter(user=self.ana, product=self.gato).exists())
        self.assertFalse(Favorite.objects.filter(user=self.bruno).exists())

    def test_one_user_cannot_delete_anothers_favorite(self):
        Favorite.objects.create(user=self.bruno, product=self.gato)
        self.entrar(self.ana)

        self.alternar(self.gato)  # para Ana isto CRIA, não apaga

        self.assertTrue(Favorite.objects.filter(user=self.bruno, product=self.gato).exists())
        self.assertTrue(Favorite.objects.filter(user=self.ana, product=self.gato).exists())

    def test_a_favorite_id_from_another_account_is_useless(self):
        """Não existe rota que aceite o id do favorito — só o do produto."""
        alheio = Favorite.objects.create(user=self.bruno, product=self.gato)
        self.entrar(self.ana)

        self.client.post(ALTERNAR, {"product_id": self.gato.pk, "favorite_id": alheio.pk})

        self.assertTrue(Favorite.objects.filter(pk=alheio.pk).exists())

    def test_one_user_does_not_see_anothers_list(self):
        Favorite.objects.create(user=self.bruno, product=self.gato)
        self.entrar(self.ana)

        self.assertEqual(self.skus(self.client.get(FAVORITOS)), [])

    def test_the_list_does_not_leak_between_sessions(self):
        """§16: nada de estado global de favoritos entre usuários."""
        Favorite.objects.create(user=self.ana, product=self.gato)

        self.entrar(self.ana)
        self.assertEqual(self.skus(self.client.get(FAVORITOS)), ["GATO-01"])

        self.client.logout()
        self.entrar(self.bruno)
        self.assertEqual(self.skus(self.client.get(FAVORITOS)), [])

    def test_the_heart_state_does_not_leak_between_users(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        self.entrar(self.bruno)
        html = self.client.get("/modelos/").content.decode()

        self.assertNotIn('data-favorite="1"', html)


# ---------------------------------------------------------------------------
# Listagem
# ---------------------------------------------------------------------------


class FavoritesPageTests(FavoriteBase):
    def setUp(self):
        super().setUp()
        self.entrar()

    def test_the_favorites_show_up(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        resposta = self.client.get(FAVORITOS)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.skus(resposta), ["GATO-01"])
        self.assertContains(resposta, "Gato Pompom")

    def test_the_newest_comes_first(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)

        self.assertEqual(self.skus(self.client.get(FAVORITOS)), ["VASO-77", "GATO-01"])

    def test_it_is_not_alphabetical(self):
        Favorite.objects.create(user=self.ana, product=self.vaso)
        Favorite.objects.create(user=self.ana, product=self.gato)

        self.assertEqual(self.skus(self.client.get(FAVORITOS)), ["GATO-01", "VASO-77"])

    def test_it_reuses_the_shop_card_and_grid(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        resposta = self.client.get(FAVORITOS)

        self.assertTemplateUsed(resposta, "components/product_card.html")
        self.assertContains(resposta, 'class="product-grid')

    def test_an_unavailable_product_disappears_from_the_page(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        self.gato.status = ProductStatus.INACTIVE
        self.gato.save()

        self.assertEqual(self.skus(self.client.get(FAVORITOS)), [])

    def test_but_the_favorite_is_not_deleted(self):
        """Apagar seria decidir pelo cliente que ele perdeu o interesse."""
        Favorite.objects.create(user=self.ana, product=self.gato)
        self.gato.status = ProductStatus.INACTIVE
        self.gato.save()
        self.client.get(FAVORITOS)

        self.assertTrue(Favorite.objects.filter(user=self.ana, product=self.gato).exists())

    def test_and_it_comes_back_when_the_product_returns(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        self.gato.status = ProductStatus.INACTIVE
        self.gato.save()
        self.assertEqual(self.skus(self.client.get(FAVORITOS)), [])

        self.gato.status = ProductStatus.ACTIVE
        self.gato.save()

        self.assertEqual(self.skus(self.client.get(FAVORITOS)), ["GATO-01"])

    def test_the_empty_state(self):
        resposta = self.client.get(FAVORITOS)

        self.assertEqual(self.skus(resposta), [])
        self.assertContains(resposta, "Você ainda não guardou nenhum produto")
        self.assertContains(resposta, "Explorar produtos")
        self.assertContains(resposta, '/modelos/')

    def test_the_count_is_shown(self):
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)

        self.assertContains(self.client.get(FAVORITOS), "2 produtos guardados")

    def test_the_hearts_on_the_page_are_all_filled(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        html = self.client.get(FAVORITOS).content.decode()

        self.assertIn('data-favorite="1"', html)
        self.assertNotIn('data-favorite="0"', html)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class FavoriteCardTests(FavoriteBase):
    def setUp(self):
        super().setUp()
        self.entrar()

    def test_the_heart_is_on_the_shop_cards(self):
        self.assertContains(self.client.get("/modelos/"), "data-favorite-button")

    def test_it_is_filled_for_a_favorite_and_hollow_otherwise(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        html = self.client.get("/modelos/").content.decode()

        self.assertIn('data-favorite="1"', html)
        self.assertIn('data-favorite="0"', html)

    def test_the_state_is_announced_to_a_screen_reader(self):
        """Não pode depender só de cor."""
        Favorite.objects.create(user=self.ana, product=self.gato)

        html = self.client.get("/modelos/").content.decode()

        self.assertIn('aria-pressed="true"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn("Remover Gato Pompom dos favoritos", html)
        self.assertIn("Adicionar Vaso Facetado aos favoritos", html)

    def test_it_is_a_real_button_inside_a_form(self):
        html = self.client.get("/modelos/").content.decode()

        self.assertIn('<button type="submit"', html)
        self.assertIn('action="/favoritos/alternar/"', html)
        self.assertIn("csrfmiddlewaretoken", html)

    def test_the_click_does_not_open_the_product(self):
        """O card inteiro é clicável; o coração tem de parar a propagação.

        No `<button>`, e não antes dele: `onclick` vem depois do
        `data-favorite-button` na ordem dos atributos.
        """
        html = self.client.get("/modelos/").content.decode()
        botao = html.split("data-favorite-button", 1)[1].split(">", 1)[0]

        self.assertIn("event.stopPropagation()", botao)

    def test_the_button_is_not_inside_the_add_to_cart_form(self):
        """Senão, clicar no coração enviaria o formulário do carrinho."""
        html = self.client.get("/modelos/").content.decode()
        card = html.split('class="card card-hover', 1)[1].split("</article>", 1)[0]
        antes_do_coracao = card.split("data-favorite-button", 1)[0]

        self.assertEqual(antes_do_coracao.count("<form"), 1)  # só o do favorito

    def test_it_shows_on_the_product_page_recommendations(self):
        outro = make_product(
            sku="CAO-01", name="Cão Bola", category=self.categoria, price=Decimal("12.00")
        )
        Favorite.objects.create(user=self.ana, product=outro)

        html = self.client.get(self.gato.get_absolute_url()).content.decode()

        self.assertIn("data-favorite-button", html)


# ---------------------------------------------------------------------------
# Cabeçalho e contador
# ---------------------------------------------------------------------------


class FavoritesHeaderTests(FavoriteBase):
    def test_the_link_works_for_a_logged_user(self):
        self.entrar()

        html = self.client.get("/").content.decode()

        self.assertIn('id="favorites-link"', html)
        self.assertIn('href="/favoritos/"', html)

    def test_the_counter_shows_the_number(self):
        self.entrar()
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)

        self.assertContains(self.client.get("/"), 'data-favorites-count="2"')

    def test_without_favorites_there_is_no_badge(self):
        self.entrar()

        self.assertNotContains(self.client.get("/"), "data-favorites-count")

    def test_an_unavailable_favorite_does_not_count(self):
        """O contador tem de bater com o que a página mostra."""
        self.entrar()
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)
        self.vaso.status = ProductStatus.DRAFT
        self.vaso.save()

        self.assertContains(self.client.get("/"), 'data-favorites-count="1"')

    def test_the_counter_is_per_user(self):
        Favorite.objects.create(user=self.bruno, product=self.gato)
        self.entrar(self.ana)

        self.assertNotContains(self.client.get("/"), "data-favorites-count")


# ---------------------------------------------------------------------------
# HTMX
# ---------------------------------------------------------------------------


class FavoriteHtmxTests(FavoriteBase):
    def setUp(self):
        super().setUp()
        self.entrar()

    def htmx(self, product, **extra):
        return self.client.post(
            ALTERNAR, {"product_id": product.pk, **extra}, headers={"hx-request": "true"}
        )

    def test_the_response_is_a_fragment_not_a_redirect(self):
        resposta = self.htmx(self.gato)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "data-favorite-button")

    def test_the_button_comes_back_already_filled(self):
        resposta = self.htmx(self.gato)

        self.assertContains(resposta, 'data-favorite="1"')
        self.assertContains(resposta, 'aria-pressed="true"')

    def test_and_hollow_after_removing(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        resposta = self.htmx(self.gato)

        self.assertContains(resposta, 'data-favorite="0"')

    def test_the_header_counter_comes_along(self):
        resposta = self.htmx(self.gato)

        self.assertContains(resposta, 'id="favorites-link"')
        self.assertContains(resposta, 'hx-swap-oob="true"')
        self.assertContains(resposta, 'data-favorites-count="1"')

    def test_a_toast_comes_along(self):
        self.assertContains(self.htmx(self.gato), "Adicionado aos favoritos")

    def test_from_the_list_the_whole_grid_comes_back(self):
        """Desfavoritar ali tem de tirar o card da tela."""
        Favorite.objects.create(user=self.ana, product=self.gato)
        Favorite.objects.create(user=self.ana, product=self.vaso)

        resposta = self.htmx(self.gato, from_list="1")

        self.assertContains(resposta, 'id="favorites-grid"')
        self.assertContains(resposta, "Vaso Facetado")
        self.assertNotContains(resposta, "Gato Pompom")

    def test_removing_the_last_one_turns_into_the_empty_state(self):
        Favorite.objects.create(user=self.ana, product=self.gato)

        resposta = self.htmx(self.gato, from_list="1")

        self.assertContains(resposta, "Você ainda não guardou nenhum produto")

    def test_the_page_is_not_replaced(self):
        """A resposta é um pedaço, não uma página inteira."""
        resposta = self.htmx(self.gato)

        self.assertNotContains(resposta, "<!DOCTYPE html>")


# ---------------------------------------------------------------------------
# Desempenho
# ---------------------------------------------------------------------------


class FavoritePerformanceTests(FavoriteBase):
    def test_the_heart_does_not_cost_a_query_per_card(self):
        """Era o jeito mais fácil de 24 produtos virarem 24 consultas."""
        self.entrar()
        for numero in range(12):
            produto = make_product(
                sku=f"P{numero}", name=f"Produto {numero}", category=self.categoria,
                price=Decimal("10.00"),
            )
            if numero % 2 == 0:
                Favorite.objects.create(user=self.ana, product=produto)

        with CaptureQueriesContext(connection) as capturadas:
            self.client.get("/modelos/")
        base = len(capturadas.captured_queries)

        for numero in range(12, 24):
            produto = make_product(
                sku=f"P{numero}", name=f"Produto {numero}", category=self.categoria,
                price=Decimal("10.00"),
            )
            Favorite.objects.create(user=self.ana, product=produto)

        with self.assertNumQueries(base):
            self.client.get("/modelos/")

    def test_the_state_costs_exactly_one_query(self):
        self.entrar()
        antes = self.contar("/modelos/")

        Favorite.objects.create(user=self.ana, product=self.gato)

        self.assertEqual(self.contar("/modelos/"), antes)

    def test_a_visitor_pays_nothing(self):
        """Sem conta, o conjunto sai vazio sem tocar no banco."""
        com_conta = None
        self.entrar()
        com_conta = self.contar("/modelos/")
        self.client.logout()
        sem_conta = self.contar("/modelos/")

        self.assertLess(sem_conta, com_conta)

    def test_the_favorites_page_does_not_grow_with_the_list(self):
        self.entrar()
        for numero in range(4):
            produto = make_product(
                sku=f"F{numero}", name=f"Favorito {numero}", category=self.categoria,
                price=Decimal("10.00"),
            )
            Favorite.objects.create(user=self.ana, product=produto)
        base = self.contar(FAVORITOS)

        for numero in range(4, 16):
            produto = make_product(
                sku=f"F{numero}", name=f"Favorito {numero}", category=self.categoria,
                price=Decimal("10.00"),
            )
            Favorite.objects.create(user=self.ana, product=produto)

        with self.assertNumQueries(base):
            self.client.get(FAVORITOS)

    def contar(self, url):
        with CaptureQueriesContext(connection) as capturadas:
            self.client.get(url)
        return len(capturadas.captured_queries)


# ---------------------------------------------------------------------------
# Idiomas
# ---------------------------------------------------------------------------


class FavoriteLanguageTests(FavoriteBase):
    def test_the_page_answers_in_the_four_languages(self):
        self.entrar()
        Favorite.objects.create(user=self.ana, product=self.gato)

        for prefixo in ("", "/fr", "/nl", "/en"):
            with self.subTest(idioma=prefixo or "pt"):
                resposta = self.client.get(f"{prefixo}{FAVORITOS}")
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(self.skus(resposta), ["GATO-01"])

    def test_the_product_name_follows_the_language(self):
        self.entrar()
        translate_product(self.gato, "fr", name="Chat Pompon")
        Favorite.objects.create(user=self.ana, product=self.gato)

        self.assertContains(self.client.get(f"/fr{FAVORITOS}"), "Chat Pompon")

    def test_the_empty_state_is_translated(self):
        self.entrar()

        resposta = self.client.get(f"/fr{FAVORITOS}")

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "Você ainda não guardou nenhum produto")

    def test_the_interface_texts_are_not_stored_in_the_database(self):
        """§14: texto de interface é i18n, não coluna."""
        from django.apps import apps

        favorito = apps.get_model("accounts", "Favorite")
        campos = {campo.name for campo in favorito._meta.get_fields()}

        self.assertEqual(campos & {"title", "label", "text", "translations"}, set())
