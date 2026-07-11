"""
Configurações de teste — sqlite em memória e Celery em modo eager
(tarefas executam de forma síncrona dentro do próprio teste).
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Tarefas Celery rodam inline nos testes.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Segredos previsíveis para os testes do webhook.
META_APP_SECRET = "segredo-de-teste"
WHATSAPP_VERIFY_TOKEN = "token-verificacao-teste"
# Sem token do WhatsApp: envio real vira log (mantém dev/teste offline).
WHATSAPP_TOKEN = ""
WHATSAPP_PHONE_NUMBER_ID = ""
GROQ_API_KEY = ""
