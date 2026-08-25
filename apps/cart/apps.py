from django.apps import AppConfig


class CartConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cart"
    label = "cart"
    verbose_name = "Carrinho"

    def ready(self):
        # Liga o merge do carrinho ao login (apps/cart/signals.py).
        from apps.cart import signals  # noqa: F401
