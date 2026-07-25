from django.urls import path

from .views import ValidarMagicLinkView

urlpatterns = [
    path("validar/<str:token>/", ValidarMagicLinkView.as_view(), name="validar_magic_link"),
]
