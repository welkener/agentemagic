"""Branding por escritório (tenant) aplicado ao admin inteiro.

Antes isto vivia só no template do `/painel/`. Com o dashboard virando a home
do admin (ver views.py), o nome/logo do escritório passam a valer no cabeçalho
de *todas* as telas — o que sempre foi a intenção do model `Escritorio`: Magic
BI é a plataforma, o escritório contábil é quem aparece pro contador.

`SITE_HEADER`/`SITE_LOGO` do django-unfold aceitam callables que recebem o
request (`unfold/sites.py::_get_value`), então a marca é resolvida por
requisição, sem reiniciar o processo quando o escritório muda no admin.
"""
from .models import escritorio_ativo


def site_header(request) -> str:
    escritorio = escritorio_ativo()
    return f"Grimório — {escritorio.nome}" if escritorio else "Grimório — Magic BI"


def site_subheader(request) -> str:
    """Deixa explícito que a plataforma é a Magic BI, mesmo com marca de terceiro."""
    return "plataforma Magic BI" if escritorio_ativo() else "painel do contador"


def site_logo(request) -> str | None:
    escritorio = escritorio_ativo()
    if escritorio and escritorio.logo:
        return escritorio.logo.url
    return None
