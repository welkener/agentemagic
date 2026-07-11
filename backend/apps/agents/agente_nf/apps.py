from django.apps import AppConfig


class AgenteNfConfig(AppConfig):
    """Subagente fiscal (Fiscus): emissão e acompanhamento de NFS-e."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents.agente_nf"
    label = "agente_nf"
    verbose_name = "Agente NF (Fiscus)"
