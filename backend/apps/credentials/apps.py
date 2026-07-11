from django.apps import AppConfig


class CredentialsConfig(AppConfig):
    """Referências ao cofre de segredos — nunca o segredo em si."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.credentials"
    verbose_name = "Credenciais"
