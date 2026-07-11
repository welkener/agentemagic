from django.urls import path

from .views import WebhookWhatsAppView

urlpatterns = [
    path("", WebhookWhatsAppView.as_view(), name="webhook-whatsapp"),
]
