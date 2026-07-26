from django.apps import AppConfig


class TenantsConfig(AppConfig):
    """Multi-tenancy: o escritório contábil parceiro é o tenant.

    Saiu de `apps.painel` em 26/jul/2026 — `painel` é apresentação (dashboard,
    branding) e estava segurando a raiz do domínio, o que deixava a direção de
    dependência invertida: `clients` (domínio) importando de `painel` (tela).
    Aqui ficam `Escritorio`, `MembroEscritorio`, o escopo de acesso do admin e
    o provisionamento de escritório novo.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tenants"
    verbose_name = "Escritórios (tenants)"
