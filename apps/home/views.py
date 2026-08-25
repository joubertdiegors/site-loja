"""View da Home.

A view não monta conteúdo: pede ao service as seções já resolvidas e passa
adiante. Toda a lógica de "de onde vêm os produtos desta seção" está em
``apps/home/services.py``, testável sem HTTP.
"""

from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.home import services


class HomeView(TemplateView):
    template_name = "home/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(services.get_home_context())
        context["meta_title"] = _("JD PRINT — Impressão 3D criativa")
        context["meta_description"] = _(
            "Produtos criativos feitos com impressão 3D: modelos decorativos, "
            "acessórios e filamentos. Peças sob encomenda e personalização."
        )
        return context
