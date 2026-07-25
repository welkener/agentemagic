"""Rotas do projeto Magic BI (superfície de API — seção 8 da arquitetura)."""
from django.contrib import admin
from django.urls import include, path
from sesame.views import LoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Webhook do WhatsApp Cloud API (GET = handshake, POST = mensagens)
    path("webhook/whatsapp", include("apps.channel_whatsapp.urls")),
    # Webhook da Evolution API — SÓ TESTE LOCAL, nunca produção (ver apps/channel_evolution/apps.py)
    path("webhook/evolution", include("apps.channel_evolution.urls")),
    # Vínculo de sessão wa_id↔CNPJ — validação do Magic Link (apps/security)
    path("security/", include("apps.security.urls")),
    # Login do painel (contador) por Magic Link — django-sesame, ver settings.py
    path("entrar/", LoginView.as_view(), name="painel_login"),
]
