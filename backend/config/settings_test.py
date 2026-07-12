"""
Configurações de teste — Postgres local (mesmo motor do dev/produção; sqlite
descontinuado para não mascarar comportamento específico de Postgres, ex.:
SELECT FOR UPDATE na auditoria). O runner de testes cria/derruba
"test_<NAME>" sozinho — não precisa criar esse banco manualmente.
Celery roda em modo eager (síncrono, dentro do próprio teste).
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://postgres:123456@localhost:5432/magicbi"),
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
# Chave de teste fixa (nunca reaproveitar em produção) — permite testar
# CampoTextoCifrado sem depender de variável de ambiente.
FIELD_ENCRYPTION_KEY = "df5bR-gm_9bHB30ETfhliXsyHxx33q0GoUHQ8csiW_0="
