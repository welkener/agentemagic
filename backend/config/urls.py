"""Rotas do projeto Magic BI (superfície de API — seção 8 da arquitetura)."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.painel.entrada import EntrarView

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
    # Porta de entrada do Grimório. Era a `LoginView` do django-sesame direto, e
    # ela responde **403 a quem chega sem token** — o que transformava o endereço
    # do painel num beco para todo contador cuja sessão expirou, com a única
    # saída dependendo de um e-mail. Agora `EntrarView` atende gente e delega ao
    # sesame quando há token: o Magic Link não mudou em nada.
    path("entrar/", EntrarView.as_view(), name="painel_login"),
]

if settings.DEBUG:
    # Servir uploads (logo do escritório) localmente — produção usa storage em nuvem.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
