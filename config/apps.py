"""Configuração dos apps do Django que o projeto substitui.

Hoje só uma: o Admin, para o menu ser agrupado por assunto em vez de por app
Python (ver `config/admin.py`).

Este é o caminho oficial da troca — `AdminConfig.default_site` —, e não um
`admin.site = ...` no meio de um módulo qualquer. A diferença importa: o
`autodiscover` do Django roda no `ready()` deste app, então **todo**
`@admin.register` do projeto encontra o site já trocado, sem ordem de import
para acertar e sem registro duplicado.

Nada além do menu muda: as URLs continuam `/admin/<app>/<model>/`, e as
permissões continuam sendo as do Django.
"""

from django.contrib.admin.apps import AdminConfig


class JDPrintAdminConfig(AdminConfig):
    default_site = "config.admin.JDPrintAdminSite"
