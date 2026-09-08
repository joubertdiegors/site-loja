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

# O reconhecimento por assinatura mora em `apps.core.uploads`: o comprovante de
# pagamento (etapa dos ajustes finais) precisa exatamente do mesmo, e duas
# tabelas de bytes mágicos seriam duas chances de uma envelhecer sem a outra.
from apps.core.uploads import sniff_image


class AddToCartForm(forms.Form):
    """Um formulário por produto: os campos exigidos dependem dele."""

    variant_id = forms.IntegerField(required=False)
    # Os eixos escolhidos nos botões da página. Existem para serem
    # **conferidos** contra `variant_id`, não para substituí-lo: um POST que
    # diga "variante 7" e "cor azul" quando a 7 é preta está mentindo em algum
    # dos dois campos, e nenhum dos dois merece o benefício da dúvida.
    option_color = forms.CharField(required=False)
    option_size = forms.CharField(required=False)
    option_material = forms.CharField(required=False)
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
            .prefetch_related("option_values")
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

        self._resolve_variant(cleaned)
        self._validate_personalization(cleaned)
        return cleaned

    # -- a variante ---------------------------------------------------------

    #: O prefixo dos eixos das opções adicionais no POST: ``option_opt-<id>``
    #: (o mesmo ``opt-<id>`` dos grupos da página).
    OPTION_PREFIX = "option_opt-"

    def product_option_ids(self) -> set:
        """Os ids das opções adicionais DESTE produto — os únicos aceitos."""
        cache = getattr(self, "_product_option_ids", None)
        if cache is None:
            cache = self._product_option_ids = set(self.product.options.values_list("pk", flat=True))
        return cache

    def axes_from(self, cleaned) -> dict:
        """Eixos que vieram no POST, só os preenchidos.

        Cor e material chegam como PK em texto; tamanho é o próprio texto do
        catálogo. Eixo ausente não restringe nada — a página só desenha botões
        para o eixo que varia.

        Etapa 3D: as opções adicionais chegam como ``option_opt-<id>`` com o
        id do valor. Uma opção que não é deste produto, ou um id que não é
        número, é uma combinação que não existe — e é assim que é tratada.
        """
        enviados = {
            "color": (cleaned.get("option_color") or "").strip(),
            "size": (cleaned.get("option_size") or "").strip(),
            "material": (cleaned.get("option_material") or "").strip(),
        }
        eixos = {eixo: valor for eixo, valor in enviados.items() if valor}
        for nome in self.data:
            if not nome.startswith(self.OPTION_PREFIX):
                continue
            valor = (self.data.get(nome) or "").strip()
            if not valor:
                continue
            option_id = nome[len(self.OPTION_PREFIX):]
            if not option_id.isdigit() or int(option_id) not in self.product_option_ids() or not valor.isdigit():
                eixos[nome[len("option_"):]] = "?"  # nunca casa com variante nenhuma
                continue
            eixos[nome[len("option_"):]] = valor
        return eixos

    def variant_axes(self, variant) -> dict:
        """Os eixos desta variante, no mesmo formato do POST.

        As opções adicionais entram como ``opt-<id>`` com o id do valor; a
        opção sem escolha na variante não entra — e um valor pedido para ela
        diverge, como deve.
        """
        eixos = {
            "color": str(variant.color_id) if variant.color_id else "",
            "size": variant.size or "",
            "material": str(variant.material_id) if variant.material_id else "",
        }
        for link in variant.option_values.all():
            eixos[f"opt-{link.option_id}"] = str(link.value_id)
        return eixos

    def _resolve_variant(self, cleaned):
        """Decide qual variante está sendo comprada — ou recusa a compra.

        Três caminhos, nesta ordem:

        1. veio ``variant_id`` — a variante é essa, e os eixos que também
           tenham vindo têm que bater com ela;
        2. vieram só os eixos (JavaScript reescrito, cliente curioso, script) —
           a combinação é resolvida aqui e precisa apontar para **exatamente
           uma** variante;
        3. não veio nada — só é aceitável quando não há o que escolher.
        """
        eixos = self.axes_from(cleaned)

        if self.variant is not None:
            divergentes = {
                eixo: valor
                for eixo, valor in eixos.items()
                if self.variant_axes(self.variant).get(eixo, "") != valor
            }
            if divergentes:
                # A combinação pedida não é a da variante enviada. Vender a
                # variante do `variant_id` entregaria uma cor que o cliente não
                # escolheu; vender a dos eixos ignoraria o campo que a página
                # de fato usa. Não há escolha honesta: recusa.
                self.add_error(
                    "variant_id", _("Esta combinação não está disponível.")
                )
            return

        if eixos:
            candidatas = [
                variante
                for variante in self.product.active_variants()
                if all(
                    self.variant_axes(variante).get(eixo, "") == valor
                    for eixo, valor in eixos.items()
                )
                # Etapa 3D: a variante com uma escolha que o POST não pediu não
                # é «a» combinação pedida — «Parede» sozinho não é «Parede + Fosco».
                and all(
                    eixo in eixos
                    for eixo in self.variant_axes(variante)
                    if eixo.startswith("opt-")
                )
            ]
            if len(candidatas) == 1:
                self.variant = candidatas[0]
            elif not candidatas:
                self.add_error(
                    "variant_id", _("Esta combinação não está disponível.")
                )
            else:
                # Mais de uma variante casa: falta escolher algum eixo.
                self.add_error("variant_id", _("Escolha uma opção do produto."))
            return

        # Nenhum sinal de escolha. Com mais de uma opção, adivinhar a cor pelo
        # cliente seria pior do que recusar. Com uma opção só não há nada a
        # escolher — a página manda a variante num campo oculto, e se o POST
        # vier sem ela, é essa mesma.
        if self.product.has_multiple_variants:
            self.add_error("variant_id", _("Escolha uma opção do produto."))
            return

        self.variant = self.product.default_variant
        if self.variant is None:
            self.add_error("variant_id", _("Este produto não está disponível."))

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
