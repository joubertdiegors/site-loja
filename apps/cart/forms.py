"""Validação do "adicionar ao carrinho".

A regra de personalização mora aqui, não no template: o `accept` do HTML e o
`required` do navegador são conveniência, não segurança. Um POST direto tem que
encontrar a mesma validação.
"""

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.cart.models import CustomizationUpload
from apps.catalog.models import PersonalizationType, ProductVariant

#: Assinaturas de arquivo aceitas. Confiar na extensão ou no content-type que o
#: navegador manda seria confiar no cliente.
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


def sniff_image(header: bytes) -> tuple[str, str] | None:
    """(extensão, tipo) a partir dos primeiros bytes, ou ``None``."""
    for signature, extension, content_type in IMAGE_SIGNATURES:
        if header.startswith(signature):
            return extension, content_type
    # WEBP: "RIFF" + 4 bytes de tamanho + "WEBP"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


class AddToCartForm(forms.Form):
    """Um formulário por produto: os campos exigidos dependem dele."""

    variant_id = forms.IntegerField(required=False)
    # Texto, não inteiro: quantidade estranha na requisição vira 1 em vez de
    # derrubar o pedido inteiro (ver clean_quantity).
    quantity = forms.CharField(required=False)
    personalization_mode = forms.ChoiceField(
        required=False, choices=(("photo", "photo"), ("text", "text"))
    )
    personalization_photo = forms.FileField(required=False)
    personalization_text = forms.CharField(required=False, strip=True)
    personalization_notes = forms.CharField(required=False, strip=True)

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.variant = None
        self.upload = None

    # -- campos ------------------------------------------------------------

    def clean_quantity(self):
        try:
            quantity = int(self.cleaned_data.get("quantity") or 1)
        except (TypeError, ValueError):
            return 1
        return max(1, quantity)

    def clean_variant_id(self):
        variant_id = self.cleaned_data.get("variant_id")
        if not variant_id:
            return None

        self.variant = (
            ProductVariant.objects.filter(
                pk=variant_id, product=self.product, is_active=True
            )
            .select_related("color", "material", "product")
            .first()
        )
        if self.variant is None:
            raise forms.ValidationError(_("Esta opção não está disponível."))
        return variant_id

    def clean_personalization_notes(self):
        notes = self.cleaned_data.get("personalization_notes", "")
        limit = getattr(settings, "CUSTOMIZATION_NOTES_MAX_LENGTH", 500)
        if len(notes) > limit:
            raise forms.ValidationError(
                _("As observações podem ter no máximo %(limit)s caracteres.") % {"limit": limit}
            )
        return notes

    def clean_personalization_photo(self):
        uploaded = self.cleaned_data.get("personalization_photo")
        if not uploaded:
            return None

        limit = getattr(settings, "CUSTOMIZATION_MAX_UPLOAD_SIZE", 10 * 1024 * 1024)
        if uploaded.size > limit:
            raise forms.ValidationError(
                _("A imagem pode ter no máximo %(limit)s MB.")
                % {"limit": limit // (1024 * 1024)}
            )
        if uploaded.size == 0:
            raise forms.ValidationError(_("O arquivo enviado está vazio."))

        header = uploaded.read(32)
        uploaded.seek(0)
        sniffed = sniff_image(header)
        if sniffed is None:
            raise forms.ValidationError(
                _("Envie uma imagem JPG, PNG ou WEBP.")
            )

        extension, content_type = sniffed
        allowed = getattr(
            settings, "CUSTOMIZATION_IMAGE_EXTENSIONS", ("jpg", "jpeg", "png", "webp")
        )
        if extension not in allowed:
            raise forms.ValidationError(_("Envie uma imagem JPG, PNG ou WEBP."))

        self._sniffed = (extension, content_type)
        return uploaded

    # -- regra do produto --------------------------------------------------

    def clean(self):
        cleaned = super().clean()
        if self.product is None:
            return cleaned

        # Produto com variantes exige escolher uma delas.
        if self.product.has_variants and self.variant is None:
            self.add_error("variant_id", _("Escolha uma opção do produto."))

        self._validate_personalization(cleaned)
        return cleaned

    def _validate_personalization(self, cleaned):
        kind = self.product.personalization_type
        if kind == PersonalizationType.NONE:
            return

        photo = cleaned.get("personalization_photo")
        text = cleaned.get("personalization_text", "")
        mode = cleaned.get("personalization_mode")

        if kind == PersonalizationType.PHOTO:
            mode = "photo"
        elif kind == PersonalizationType.TEXT:
            mode = "text"
        elif not mode:
            self.add_error(
                "personalization_mode", _("Escolha entre enviar uma foto ou informar um texto.")
            )
            return

        if mode == "photo":
            if not photo:
                self.add_error("personalization_photo", _("Envie a foto da personalização."))
            cleaned["personalization_text"] = ""
        else:
            if not text:
                self.add_error("personalization_text", _("Informe o texto da personalização."))
            elif len(text) > self.product.personalization_text_limit:
                self.add_error(
                    "personalization_text",
                    _("O texto pode ter no máximo %(limit)s caracteres.")
                    % {"limit": self.product.personalization_text_limit},
                )
            cleaned["personalization_photo"] = None

        cleaned["personalization_mode"] = mode

    # -- resultado ---------------------------------------------------------

    def build_customization(self, session_key: str = "") -> dict | None:
        """Salva o arquivo (se houver) e devolve o que vai para a sessão.

        Só chamar depois de ``is_valid()``. O binário vai para o storage; a
        sessão guarda o id da linha em ``CustomizationUpload``.
        """
        if self.product is None or not self.product.needs_personalization:
            return None

        mode = self.cleaned_data.get("personalization_mode")
        notes = self.cleaned_data.get("personalization_notes", "")
        customization = {"type": mode, "upload_id": None, "text": "", "notes": notes}

        if mode == "photo":
            uploaded = self.cleaned_data["personalization_photo"]
            extension, content_type = getattr(self, "_sniffed", ("bin", ""))
            upload = CustomizationUpload(
                original_name=uploaded.name[:200],
                content_type=content_type,
                extension=extension,
                size_bytes=uploaded.size,
                session_key=session_key or "",
            )
            upload.file.save(uploaded.name, uploaded, save=False)
            upload.save()
            self.upload = upload
            customization["upload_id"] = upload.pk
        else:
            customization["text"] = self.cleaned_data.get("personalization_text", "")

        return customization

    @property
    def error_message(self) -> str:
        """Primeira mensagem de erro, para o toast."""
        for messages in self.errors.values():
            if messages:
                return messages[0]
        return str(_("Não foi possível adicionar o produto."))
