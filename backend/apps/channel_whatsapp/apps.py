from django.apps import AppConfig


class ChannelWhatsappConfig(AppConfig):
    """Canal WhatsApp Cloud API: webhook, idempotência e envio de mensagens."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.channel_whatsapp"
    verbose_name = "Canal WhatsApp"
