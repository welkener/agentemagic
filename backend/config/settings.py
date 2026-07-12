"""
Configurações do projeto Magic BI (MVP — Semana 1).

Arquitetura: docs/AgenteRotinaContabil-arquitetura-tecnica.md
- Django 5.2 LTS + DRF; assíncrono no Celery (broker Redis).
- Banco: sqlite para checagens locais rápidas; Postgres via docker-compose
  (controlado por DATABASE_URL).
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, True),
)
# Lê o .env se existir (dev local); em produção usar variáveis reais de ambiente.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-inseguro-troque-em-producao")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceiros
    "rest_framework",
    # Apps Magic BI (mapa de módulos — seção 2 da arquitetura)
    "apps.core",
    "apps.clients",
    "apps.credentials",
    "apps.channel_whatsapp",
    "apps.audit",
    "apps.governance",
    "apps.agents.agente_nf",
    "apps.agents.agente_erp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Banco: só Postgres (sqlite descontinuado — evita divergência de comportamento,
# ex.: SELECT FOR UPDATE na auditoria). Local (postgres/123456) ou docker-compose.
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://postgres:123456@localhost:5432/magicbi"),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# ---------------------------------------------------------------------------
# Celery — recepção do WhatsApp responde rápido (ack) e o processamento pesado
# (LLM, ERP, SEFAZ) roda na fila, com retry e idempotência (seção 8 da arquitetura).
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE

# ---------------------------------------------------------------------------
# WhatsApp Cloud API (Meta)
# ---------------------------------------------------------------------------
META_APP_SECRET = env("META_APP_SECRET", default="")
WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN", default="")
WHATSAPP_TOKEN = env("WHATSAPP_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")

# ---------------------------------------------------------------------------
# Groq API — orquestração (function-calling) via Pydantic AI. Sem a chave, o
# orquestrador cai no roteamento determinístico por palavra-chave (offline).
# ---------------------------------------------------------------------------
GROQ_API_KEY = env("GROQ_API_KEY", default="")

# ---------------------------------------------------------------------------
# Chave de cifra de campo (Credencial/AplicativoIntegracao — apps/credentials/
# crypto.py). Gerar com `Fernet.generate_key()`; nunca reaproveitar entre
# ambientes. Sem ela, salvar um segredo levanta ErroChaveDeCifraAusente.
# ---------------------------------------------------------------------------
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# ---------------------------------------------------------------------------
# Logs estruturados (structlog)
# ---------------------------------------------------------------------------
import sys  # noqa: E402

import structlog  # noqa: E402

# As mensagens do produto usam emoji (🧾🎉😕 etc.). O console do Windows abre
# stdout/stderr no codepage legado (cp1252) por padrão, que não representa
# esses caracteres — sem isto, logar qualquer resposta com emoji derruba a
# request inteira com UnicodeEncodeError. `backslashreplace` nunca lança.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
