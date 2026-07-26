"""Branding por escritório (tenant) aplicado ao admin inteiro.

A marca resolve pelo **escritório do usuário logado** (`escopo.py`), não por um
"escritório ativo" global — com dois tenants no ar, mostrar a marca do
escritório A pro contador do B seria pior que mostrar a genérica.

Fallback pra `escritorio_ativo()` (o único ativo, se houver só um) cobre as
telas sem usuário — tela de login, sobretudo — e a instalação single-tenant
que já está no ar. Com mais de um escritório, `escritorio_ativo()` devolve
None de propósito e a marca vira a genérica "Magic BI".

`SITE_HEADER`/`SITE_SUBHEADER`/`SITE_LOGO` do django-unfold aceitam callables
que recebem o request (`unfold/sites.py::_get_value`), então isto é resolvido
por requisição — trocar o escritório no admin reflete na hora.
"""
from apps.tenants.escopo import escritorio_do_usuario
from apps.tenants.models import escritorio_ativo


def _escritorio(request):
    usuario = getattr(request, "user", None)
    return escritorio_do_usuario(usuario) or escritorio_ativo()


def site_header(request) -> str:
    escritorio = _escritorio(request)
    return f"Grimório — {escritorio.nome}" if escritorio else "Grimório — Magic BI"


def site_subheader(request) -> str:
    """Deixa explícito que a plataforma é a Magic BI, mesmo com marca de terceiro."""
    return "plataforma Magic BI" if _escritorio(request) else "painel do contador"


def site_logo(request) -> str | None:
    escritorio = _escritorio(request)
    if escritorio and escritorio.logo:
        return escritorio.logo.url
    return None
