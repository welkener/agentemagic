"""Rotas do projeto Magic BI (superfície de API — seção 8 da arquitetura)."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from sesame.views import LoginView

# SITE_HEADER/SITE_TITLE ficam no dict UNFOLD (settings.py) — django-unfold lê de lá.
admin.site.index_title = "Visão geral"
# O índice do admin É o dashboard do Grimório: o template estende o
# `admin/index.html` do unfold e injeta os cards acima da lista de apps.
# Métricas vêm do DASHBOARD_CALLBACK (settings.py → UNFOLD).
admin.site.index_template = "painel/dashboard.html"

urlpatterns = [
    # A raiz é o Grimório (DEC-12): a aplicação do contador, não o admin. O
    # admin continua em `/admin/` como backoffice — cadastro, exceção e equipe
    # da plataforma.
    path("", RedirectView.as_view(url="/grimorio/", permanent=False)),
    path("grimorio/", include("apps.painel.urls")),
    path("admin/", admin.site.urls),
    # `/painel/` já circulou em e-mails de Magic Link, na documentação e no
    # servidor de teste. Continua respondendo — agora apontando para a
    # aplicação, não mais para o índice do admin.
    path("painel/", RedirectView.as_view(url="/grimorio/", permanent=False), name="painel_dashboard"),
    # Webhook do WhatsApp Cloud API (GET = handshake, POST = mensagens)
    path("webhook/whatsapp", include("apps.channel_whatsapp.urls")),
    # Webhook da Evolution API — SÓ TESTE LOCAL, nunca produção (ver apps/channel_evolution/apps.py)
    path("webhook/evolution", include("apps.channel_evolution.urls")),
    # Vínculo de sessão wa_id↔CNPJ — validação do Magic Link (apps/security)
    path("security/", include("apps.security.urls")),
    # Login do painel (contador) por Magic Link — django-sesame, ver settings.py
    path("entrar/", LoginView.as_view(), name="painel_login"),
]

if settings.DEBUG:
    # Servir uploads (logo do escritório) localmente — produção usa storage em nuvem.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
