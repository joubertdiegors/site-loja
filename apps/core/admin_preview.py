"""Pré-visualização ao vivo no Admin — o site de verdade, dentro da tela de edição.

## A regra

O preview **não tem desenho próprio**: ele renderiza o mesmo template que a
loja usa (`components/footer.html`, `components/home_about.html`,
`components/hero.html`...), com o mesmo CSS, dentro de um `<iframe>` na tela
de edição. O que muda é só a origem dos dados: em vez do banco, o registro é
montado a partir do formulário como ele está agora — sem salvar.

## Como funciona

1. `LivePreviewMixin` acrescenta ao ModelAdmin uma rota `live-preview/`
   (POST, dentro do `admin_view`, com as permissões do próprio cadastro);
2. o JavaScript da tela (`static/admin/js/live_preview.js`) manda o
   formulário para essa rota a cada alteração e coloca o HTML devolvido no
   iframe (`srcdoc`);
3. a view monta o registro com `construct_instance` sobre o formulário do
   Admin (só os campos que passaram na validação; os outros ficam como
   estavam) e as traduções a partir do inline, em memória
   (`_translations_by_language`) — o mesmo cache que o `tr()` lê;
4. cada ModelAdmin diz qual componente renderizar e com que contexto
   (`preview_component` + `get_preview_context`), ou assume a renderização
   inteira (`render_live_preview`), como faz a página de manutenção.

## Limites, de propósito

* Arquivos (imagens) não vão no preview antes de salvar: um upload a cada
  tecla seria caro, e o `FileField` de um objeto não gravado não tem URL. O
  painel avisa; a imagem já salva aparece.
* Registros filhos de outros cadastros (os passos de uma chamada, os links de
  uma coluna) vêm do banco: o que ainda não foi salvo lá não existe.
* Nada é gravado e nada é lido de fora do que o próprio Admin já autoriza.
"""

from django.conf import settings
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db import models
from django.forms.models import construct_instance
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import translation

from apps.core.models import TranslationBase

#: A chave de um registro ainda não gravado, para o `tr()` ler o cache de
#: traduções (ele devolve o padrão quando `pk` é `None`).
PREVIEW_PK = -1

#: Os idiomas do painel: código do site e rótulo do botão.
PREVIEW_LANGUAGES = (("pt-br", "PT"), ("fr", "FR"), ("nl", "NL"), ("en", "EN"))


class Wrapped:
    """Um registro com alguns atributos trocados, sem gravar nada.

    O template lê `column.visible_links` ou `callout.visible_steps`; aqui essas
    listas são as do banco com o registro em edição no lugar do gravado. Tudo
    o mais continua vindo do objeto original.
    """

    def __init__(self, target, **overrides):
        self._target = target
        self._overrides = overrides

    def __getattr__(self, name):
        overrides = self.__dict__.get("_overrides", {})
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["_target"], name)

    def __bool__(self):
        return True

    def __str__(self):
        return str(self._target)


def replace_or_append(items, instance):
    """A lista do banco com o registro em edição no lugar do gravado.

    Registro novo (ainda sem chave de verdade) entra no fim; a ordenação
    fica com quem chama, que sabe por qual campo ordenar.
    """
    saida = []
    encontrado = False
    for item in items:
        if instance.pk not in (None, PREVIEW_PK) and item.pk == instance.pk:
            saida.append(instance)
            encontrado = True
        else:
            saida.append(item)
    if not encontrado:
        saida.append(instance)
    return saida


def ordered(items, campo="sort_order"):
    return sorted(items, key=lambda o: (getattr(o, campo, 0) or 0, o.pk if o.pk and o.pk > 0 else 10**9))


class LivePreviewMixin:
    """Um ModelAdmin com o painel de pré-visualização ao vivo.

    Configure `preview_component` (o template incluído no quadro) e, se o
    contexto padrão (`{"object": instance}`) não bastar, `get_preview_context`.
    `preview_note` é a frase curta sob o título do painel; `preview_scripts`
    inclui o `app.js` do site no quadro (o carrossel precisa dele).
    """

    preview_component = ""
    preview_title = "Pré-visualização"
    preview_note = ""
    preview_scripts = False
    preview_body_class = ""
    preview_languages = PREVIEW_LANGUAGES
    change_form_template = "admin/jd_change_form.html"

    # -- rotas e tela ----------------------------------------------------------

    def _preview_url_name(self, suffix):
        return "%s_%s_%s" % (self.opts.app_label, self.opts.model_name, suffix)

    def get_urls(self):
        extra = [
            path(
                "live-preview/",
                self.admin_site.admin_view(self.live_preview_view),
                name=self._preview_url_name("live_preview"),
            ),
            path(
                "<path:object_id>/live-preview/",
                self.admin_site.admin_view(self.live_preview_view),
                name=self._preview_url_name("live_preview_change"),
            ),
        ]
        return extra + super().get_urls()

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        if object_id is not None:
            url = reverse("admin:" + self._preview_url_name("live_preview_change"), args=[object_id])
        else:
            url = reverse("admin:" + self._preview_url_name("live_preview"))
        extra_context.update(
            {
                "preview_url": url,
                "preview_title": self.preview_title,
                "preview_note": self.preview_note,
                "preview_languages": self.preview_languages,
            }
        )
        return super().changeform_view(request, object_id, form_url, extra_context)

    # -- a view -------------------------------------------------------------------

    def live_preview_view(self, request, object_id=None):
        """O HTML do quadro, a partir do formulário como está (POST, sem gravar)."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        obj = None
        if object_id is not None:
            obj = self.get_object(request, unquote(object_id))
            if obj is None:
                raise Http404
            if not (self.has_view_permission(request, obj) or self.has_change_permission(request, obj)):
                raise PermissionDenied
        elif not self.has_add_permission(request):
            raise PermissionDenied

        lang = request.GET.get("lang", settings.LANGUAGE_CODE)
        if not translation.check_for_language(lang):
            lang = settings.LANGUAGE_CODE
        with translation.override(lang):
            instance = self.build_preview_instance(request, obj)
            response = self.render_live_preview(request, instance)
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    # -- montagem do registro ------------------------------------------------------

    def build_preview_instance(self, request, obj):
        """O registro como o formulário o descreve agora — em memória.

        Só os campos que passaram na validação individual entram
        (`construct_instance` ignora o que não está em `cleaned_data`); os
        demais ficam como estão no banco. Arquivos ficam de fora.
        """
        instance = obj if obj is not None else self.model()
        ModelForm = self.get_form(request, obj, change=obj is not None)
        form = ModelForm(request.POST, request.FILES, instance=instance)
        form.full_clean()
        exclude = [f.name for f in self.model._meta.fields if isinstance(f, models.FileField)]
        construct_instance(form, instance, exclude=exclude)
        self.apply_preview_translations(request, obj, instance)
        if instance.pk is None:
            instance.pk = PREVIEW_PK
        return instance

    def apply_preview_translations(self, request, obj, instance):
        """As traduções do inline, como estão no formulário, no cache do `tr()`."""
        if not hasattr(instance, "translations_by_language"):
            return
        for inline in self.get_inline_instances(request, obj):
            if not issubclass(inline.model, TranslationBase):
                continue
            formset_class = inline.get_formset(request, obj)
            prefix = formset_class.get_default_prefix()
            fk_name = formset_class.fk.name
            try:
                total = int(request.POST.get(f"{prefix}-TOTAL_FORMS", 0))
            except (TypeError, ValueError):
                total = 0
            campos = [
                f for f in inline.model._meta.fields
                if f.name not in ("id", fk_name, "language") and not isinstance(f, models.FileField)
            ]
            cache = {}
            for indice in range(total):
                chave = f"{prefix}-{indice}-"
                if request.POST.get(chave + "DELETE"):
                    continue
                idioma = request.POST.get(chave + "language", "")
                if not idioma:
                    continue
                dados = {}
                for campo in campos:
                    bruto = request.POST.get(chave + campo.name)
                    if bruto is None:
                        continue
                    try:
                        dados[campo.name] = campo.to_python(bruto)
                    except Exception:  # noqa: BLE001 - valor inválido: fica o padrão do campo
                        continue
                cache[idioma] = inline.model(language=idioma, **dados)
            instance._translations_by_language = cache
            return

    # -- renderização ------------------------------------------------------------------

    def get_preview_context(self, request, instance) -> dict:
        return {"object": instance}

    def render_live_preview(self, request, instance):
        context = self.get_preview_context(request, instance)
        context.update(
            {
                "preview_component": self.preview_component,
                "preview_scripts": self.preview_scripts,
                "preview_body_class": self.preview_body_class,
            }
        )
        return render(request, "admin/preview/frame.html", context)
