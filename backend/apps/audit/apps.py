from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Trilha de auditoria append-only com hash encadeado."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    verbose_name = "Auditoria"
