from django.urls import path

from .views import WebhookEvolutionView

urlpatterns = [
    path("", WebhookEvolutionView.as_view(), name="webhook_evolution"),
]
