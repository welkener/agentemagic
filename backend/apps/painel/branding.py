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


def marca_do_usuario(usuario) -> dict:
    """Marca resolvida pelo usuário (não pelo request) — para o Grimório.

    O admin resolve por request porque o django-unfold entrega o request aos
    callables acima. As views do Grimório já têm o usuário em mãos, e depender
    do request aqui só acrescentaria um objeto a carregar.

    As cores saem do `Escritorio` e entram como custom properties no CSS — é o
    que faz a marca do tenant valer na aplicação inteira sem nada hardcoded
    (lição registrada em 25/jul/2026, quando "Rotina Contábil" apareceu escrito
    dentro de um template).
    """
    escritorio = escritorio_do_usuario(usuario) or escritorio_ativo()
    if escritorio is None:
        return {
            "nome": "Magic BI",
            "sigla": "MB",
            "cor_primaria": "#1a1a2e",
            "cor_acento": "#5B67C9",
            "logo": None,
        }
    return {
        "nome": escritorio.nome,
        # Iniciais como brasão quando não há logo — melhor que espaço vazio, e
        # não inventa imagem que o escritório não forneceu.
        "sigla": "".join(p[0] for p in escritorio.nome.split()[:2]).upper(),
        "cor_primaria": escritorio.cor_primaria,
        "cor_acento": escritorio.cor_acento,
        "logo": escritorio.logo.url if escritorio.logo else None,
    }
