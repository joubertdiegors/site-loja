"""Arquivos enviados pelo cliente para personalizar um produto.

Por que uma tabela e não só um caminho na sessão:

* o binário **não pode** ir para a sessão (ela é serializada a cada requisição);
* guardar um caminho solto na sessão significaria confiar num texto para
  montar um caminho de arquivo depois — é exatamente o tipo de coisa que vira
  travessia de diretório;
* quando existir ``OrderItem``, a personalização precisa continuar apontando
  para o mesmo arquivo. Com uma linha no banco, o pedido só copia a referência.

A sessão guarda apenas o ``id`` desta linha.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


def customization_upload_to(instance, filename: str) -> str:
    """Nome gerado por nós, nunca o do cliente.

    O nome original fica guardado em ``original_name`` para exibição; o arquivo
    em disco recebe um nome aleatório com a extensão já validada.
    """
    extension = (instance.extension or "bin").lower()
    return f"customizations/{uuid.uuid4().hex}.{extension}"


class CustomizationUpload(TimeStampedModel):
    file = models.FileField("arquivo", upload_to=customization_upload_to)
    original_name = models.CharField("nome original", max_length=200, blank=True)
    content_type = models.CharField("tipo detectado", max_length=60, blank=True)
    extension = models.CharField("extensão", max_length=10, blank=True)
    size_bytes = models.PositiveIntegerField("tamanho (bytes)", default=0)
    session_key = models.CharField(
        "sessão",
        max_length=64,
        blank=True,
        db_index=True,
        help_text="De quem enviou. Serve para limpeza de arquivos órfãos.",
    )

    class Meta:
        verbose_name = "arquivo de personalização"
        verbose_name_plural = "arquivos de personalização"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.original_name or self.file.name

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.0f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Carrinho persistente
# ---------------------------------------------------------------------------


class Cart(TimeStampedModel):
    """O carrinho de quem tem conta.

    Visitante continua guardando tudo na sessão — não criamos linha no banco
    para quem talvez nunca volte. Ao entrar (ou ao se cadastrar), o carrinho da
    sessão é mesclado neste aqui e a sessão é esvaziada; a partir daí o banco é
    a única fonte da verdade para aquele usuário.

    ``OneToOne`` porque o requisito é "no máximo um carrinho ativo por
    usuário", e essa é a forma de o banco garantir isso — não uma regra que
    depende de todo mundo lembrar de filtrar por ``is_active``.

    O carrinho fica ligado ao ``User`` e não ao ``Customer``: comprar depende
    de estar autenticado, não de ter preenchido os dados comerciais.

    ``created_at``/``updated_at`` vêm de ``TimeStampedModel`` e são justamente o
    que um lembrete de carrinho abandonado vai precisar depois — quando o
    carrinho nasceu e quando foi mexido pela última vez.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="usuário",
        related_name="cart",
        on_delete=models.CASCADE,
    )

    class Meta:
        verbose_name = "carrinho"
        verbose_name_plural = "carrinhos"
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"Carrinho de {self.user}"

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items.all())


class CartItem(TimeStampedModel):
    """Uma linha do carrinho persistente.

    A identidade da linha é **produto + variante + personalização**, a mesma da
    sessão: "caneca com o nome Marie" e "caneca com o nome Paul" são duas
    linhas, não uma com quantidade 2. ``line_key`` guarda essa identidade já
    calculada, para o merge comparar strings em vez de reconstruir dicionários.

    ``CASCADE`` em produto, variante e anexo: um carrinho não pode mostrar item
    que não existe mais. Um ``OrderItem`` fará o contrário — ``PROTECT`` e
    cópia do preço —, porque um pedido precisa continuar existindo mesmo se o
    produto sair do catálogo.
    """

    cart = models.ForeignKey(Cart, verbose_name="carrinho", related_name="items", on_delete=models.CASCADE)
    line_key = models.CharField("identidade da linha", max_length=64, db_index=True)

    product = models.ForeignKey(
        "catalog.Product", verbose_name="produto", related_name="cart_items", on_delete=models.CASCADE
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        verbose_name="opção",
        related_name="cart_items",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    quantity = models.PositiveIntegerField("quantidade", default=1)

    customization_type = models.CharField("tipo de personalização", max_length=20, blank=True)
    customization_text = models.TextField("texto", blank=True)
    customization_notes = models.TextField("observações", blank=True)
    customization_upload = models.ForeignKey(
        CustomizationUpload,
        verbose_name="arquivo enviado",
        related_name="cart_items",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    class Meta:
        verbose_name = "item do carrinho"
        verbose_name_plural = "itens do carrinho"
        ordering = ("created_at", "pk")
        constraints = [
            models.UniqueConstraint(fields=("cart", "line_key"), name="cart_item_unique_line"),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product_id}"

    # -- conversão de/para o formato do carrinho ---------------------------

    def customization(self) -> dict | None:
        """A personalização no mesmo formato usado na sessão.

        As chaves e a ordem importam: é sobre este dicionário que a assinatura
        da linha é calculada.
        """
        if not self.customization_type:
            return None
        return {
            "type": self.customization_type,
            "upload_id": self.customization_upload_id,
            "text": self.customization_text,
            "notes": self.customization_notes,
        }

    def to_item(self) -> dict:
        return {
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "quantity": self.quantity,
            "customization": self.customization(),
        }

    @staticmethod
    def fields_from_item(item: dict) -> dict:
        customization = item.get("customization") or {}
        return {
            "product_id": item["product_id"],
            "variant_id": item.get("variant_id") or None,
            "quantity": int(item.get("quantity", 0)),
            "customization_type": customization.get("type") or "",
            "customization_text": customization.get("text") or "",
            "customization_notes": customization.get("notes") or "",
            "customization_upload_id": customization.get("upload_id") or None,
        }
