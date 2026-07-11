from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    """Motor de tiers 0–3: consultas, rascunho, alteração, ações críticas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.governance"
    verbose_name = "Governança"
