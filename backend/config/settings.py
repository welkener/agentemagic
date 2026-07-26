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

# Atrás de proxy reverso com TLS (nginx-proxy-manager no servidor de teste — o
# app em si só fala HTTP; o proxy termina o HTTPS e repassa X-Forwarded-Proto).
# Sem isto, Django acha que a requisição é HTTP puro e rejeita POST (CSRF) vindo
# de um domínio HTTPS.
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    # django-unfold precisa vir ANTES de django.contrib.admin (troca os
    # templates do admin por uma UI Tailwind moderna — ver config/urls.py e
    # cada apps/*/admin.py, que agora herdam de unfold.admin.ModelAdmin).
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
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
    "apps.tenants",  # multi-tenancy: Escritorio/MembroEscritorio + escopo de acesso
    "apps.clients",
    "apps.credentials",
    "apps.security",
    "apps.painel",
    "apps.channel_whatsapp",
    "apps.channel_evolution",  # SÓ TESTE LOCAL — nunca produção (ver apps/channel_evolution/apps.py)
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
        "DIRS": [BASE_DIR / "templates"],  # override de admin/base_site.html — ver templates/admin/
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
STATIC_ROOT = BASE_DIR / "staticfiles"  # `collectstatic` — servido via `runserver --insecure` no piloto
STATICFILES_DIRS = [BASE_DIR / "static"]  # CSS de marca do admin (static/admin/css/magicbi_admin.css)

# Upload de logo do escritório (apps/tenants). Em produção, trocar por storage
# em nuvem (S3/equivalente) — local só serve pro piloto/dev.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

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
# Evolution API — canal de TESTE LOCAL apenas, nunca produção (protocolo não-
# oficial Baileys/whatsmeow, risco de banimento — decisão já registrada em
# docs/magicbi-hermes-comunicador.md §3 e docs/requisitos-dev-piloto-rotina.md
# §7.1). Serve só para o time/escritório testar o fluxo ponta a ponta antes
# de configurar a Cloud API oficial da Meta. Sem estas 3 variáveis, o envio
# degrada pra log (apps/channel_evolution/services.py) — fica testável offline.
# ---------------------------------------------------------------------------
EVOLUTION_BASE_URL = env("EVOLUTION_BASE_URL", default="")
EVOLUTION_API_KEY = env("EVOLUTION_API_KEY", default="")
EVOLUTION_INSTANCE = env("EVOLUTION_INSTANCE", default="")

# ---------------------------------------------------------------------------
# Chave de cifra de campo (Credencial/AplicativoIntegracao — apps/credentials/
# crypto.py). Gerar com `Fernet.generate_key()`; nunca reaproveitar entre
# ambientes. Sem ela, salvar um segredo levanta ErroChaveDeCifraAusente.
# ---------------------------------------------------------------------------
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# ---------------------------------------------------------------------------
# apps/security — Magic Link (vínculo wa_id↔CNPJ) e 2FA por código avulso.
# Ver docs/magicbi-seguranca-sessao.md. Sem MAGICLINK_SIGNING_KEY, cai no
# próprio SECRET_KEY (ok para MVP; recomenda-se chave dedicada em produção).
# ---------------------------------------------------------------------------
MAGICLINK_SIGNING_KEY = env("MAGICLINK_SIGNING_KEY", default="")
MAGICLINK_TTL_MINUTOS = env.int("MAGICLINK_TTL_MINUTOS", default=15)
SESSAO_TTL_DIAS = env.int("SESSAO_TTL_DIAS", default=7)
CODIGO_2FA_TTL_MINUTOS = env.int("CODIGO_2FA_TTL_MINUTOS", default=5)
PAINEL_BASE_URL = env("PAINEL_BASE_URL", default="http://localhost:8000")

# E-mail: console em dev (imprime no terminal em vez de enviar de verdade);
# configurar SMTP real (ou SES) antes do piloto com clientes reais.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@magicbi.local")

# ---------------------------------------------------------------------------
# django-sesame — login do PAINEL (contador/Grimório) por Magic Link.
# Caso de uso diferente do apps/security acima: aqui é login de auth.User
# (django-sesame resolve isso nativamente); o vínculo wa_id↔CNPJ do WhatsApp
# é modelo de domínio próprio (PyJWT), não login de User. Ver
# docs/magicbi-seguranca-sessao.md §5.
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "sesame.backends.ModelBackend",
]
SESAME_MAX_AGE = MAGICLINK_TTL_MINUTOS * 60  # segundos — mesmo TTL do Magic Link do wa_id
LOGIN_REDIRECT_URL = "/admin/"  # o índice do admin É o dashboard do Grimório

# ---------------------------------------------------------------------------
# django-unfold — tema moderno do admin (substitui o reskin manual por CSS
# custom properties da Onda "restilizar o admin"; ver
# docs/magicbi-ondas-desenvolvimento.md). Paleta = escala indigo do Tailwind
# (mais próxima da cor de acento periwinkle já usada no /painel/, ver
# apps/tenants/models.py Escritorio.cor_acento).
# ---------------------------------------------------------------------------
from django.urls import reverse_lazy as _reverse_lazy  # noqa: E402

UNFOLD = {
    "SITE_TITLE": "Magic BI",
    # Callables (o unfold resolve por requisição) — o nome/logo vêm do
    # Escritorio ativo, então a marca do tenant vale no admin inteiro, não só
    # numa página. Ver apps/painel/branding.py.
    "SITE_HEADER": "apps.painel.branding.site_header",
    "SITE_SUBHEADER": "apps.painel.branding.site_subheader",
    "SITE_LOGO": "apps.painel.branding.site_logo",
    "SITE_SYMBOL": "auto_awesome",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    # O índice do admin é o dashboard do Grimório: este callback injeta as
    # métricas no contexto de templates/admin/index.html (apps/painel/views.py).
    "DASHBOARD_CALLBACK": "apps.painel.views.dashboard_callback",
    # Escala indigo do Tailwind (mesma família de cor do periwinkle #5B67C9 já
    # usado como cor_acento padrão do Escritorio, apps/tenants/models.py).
    "COLORS": {
        "primary": {
            "50": "oklch(95.8% .019 282.83)",
            "100": "oklch(91.5% .038 285.66)",
            "200": "oklch(83.2% .078 283.78)",
            "300": "oklch(74.8% .12 282.58)",
            "400": "oklch(66.7% .163 280.24)",
            "500": "oklch(58.5% .204 277.12)",
            "600": "oklch(50.5% .244 272.22)",
            "700": "oklch(42.1% .244 268.01)",
            "800": "oklch(32.9% .19 268.01)",
            "900": "oklch(23.8% .138 267.96)",
            "950": "oklch(18.7% .108 268.05)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "navigation": [
            {
                "title": "Grimório",
                "separator": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": _reverse_lazy("admin:index"),
                    },
                    {
                        "title": "Fila de aprovação",
                        "icon": "pending_actions",
                        "link": _reverse_lazy("admin:agente_nf_intencao_changelist"),
                    },
                    {
                        "title": "Trilha de auditoria",
                        "icon": "history",
                        "link": _reverse_lazy("admin:audit_auditoria_changelist"),
                    },
                ],
            },
        ],
    },
}

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
