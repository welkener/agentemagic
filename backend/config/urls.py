"""Rotas do projeto Magic BI (superfície de API — seção 8 da arquitetura)."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Webhook do WhatsApp Cloud API (GET = handshake, POST = mensagens)
    path("webhook/whatsapp", include("apps.channel_whatsapp.urls")),
    # Vínculo de sessão wa_id↔CNPJ — validação do Magic Link (apps/security)
    path("security/", include("apps.security.urls")),
]
