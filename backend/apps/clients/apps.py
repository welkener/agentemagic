from django.apps import AppConfig


class ClientsConfig(AppConfig):
    """Clientes e perfis — provisionamento por cliente."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.clients"
    verbose_name = "Clientes"
