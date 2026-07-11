from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Núcleo determinístico: orquestrador, contrato de resultado, roteamento."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Núcleo (orquestrador)"
