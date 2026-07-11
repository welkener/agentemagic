from django.apps import AppConfig


class AgenteErpConfig(AppConfig):
    """Subagente ERP: opera o ERP do cliente pela interface única."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents.agente_erp"
    label = "agente_erp"
    verbose_name = "Agente ERP"
