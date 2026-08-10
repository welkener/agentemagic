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

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-inseguro-troque-em-producao")  # ver bloco de endurecimento abaixo
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Atrás de proxy reverso com TLS (nginx-proxy-manager no servidor de teste — o
# app em si só fala HTTP; o proxy termina o HTTPS e repassa X-Forwarded-Proto).
# Sem isto, Django acha que a requisição é HTTP puro e rejeita POST (CSRF) vindo
# de um domínio HTTPS.
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Endurecimento fora de DEBUG.
#
# Os defaults acima são de conveniência para dev — e é justamente aí que mora o
# risco: esquecer `DJANGO_DEBUG=False`/`DJANGO_SECRET_KEY` no deploy fazia o
# sistema subir **silenciosamente** com DEBUG ligado e uma chave pública neste
# repositório. Sistema fiscal não pode falhar em silêncio nisso: aqui ele recusa
# subir.
# ---------------------------------------------------------------------------
SECRET_KEY_DEV = "dev-inseguro-troque-em-producao"

if not DEBUG:
    from django.core.exceptions import ImproperlyConfigured  # noqa: E402

    if SECRET_KEY == SECRET_KEY_DEV:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY não configurada com DEBUG=False. A chave padrão é "
            "pública (está versionada neste repositório) — com ela, qualquer um "
            "forja sessão e token de Magic Link. Gere uma com: python -c "
            "\"from django.core.management.utils import get_random_secret_key as g; print(g())\""
        )

    # Cookies só por HTTPS. O app fala HTTP puro (o proxy termina o TLS), então
    # sem isto o cookie de sessão do contador trafega em claro entre proxy e app.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Redirect HTTP→HTTPS fica DESLIGADO por padrão, e não é descuido: quem
    # termina o TLS aqui é o proxy reverso, e ele já redireciona. Ligar no
    # Django, com a porta do app também exposta direto (8020 no compose de
    # deploy), faz quem acessar por IP cair em **loop infinito de redirect** —
    # inclusive quem só quer conferir se o container subiu. Ligue apenas se o
    # app deixar de ser alcançável fora do proxy.
    SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=False)

    # HSTS começa em 1 hora de propósito. É a única configuração aqui que o
    # navegador **memoriza**: errar com 1 ano deixa o domínio inacessível por 1
    # ano, sem desfazer. Suba para 31536000 só depois de confirmar que TUDO
    # (inclusive subdomínios) responde em HTTPS.
    SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=3600)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_HSTS_SUBDOMAINS", default=False)

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
    # Sem models — entra na lista só pra o Django descobrir o management
    # command `inspecionar_erp` (fechar mapeamento de payload de ERP real).
    "apps.adapters",
    "apps.tenants",  # multi-tenancy: Escritorio/MembroEscritorio + escopo de acesso
    "apps.clients",
    "apps.credentials",
    "apps.security",
    "apps.painel",
    "apps.channel_whatsapp",
    "apps.channel_evolution",  # SÓ TESTE LOCAL — nunca produção (ver apps/channel_evolution/apps.py)
    "apps.audit",
    "apps.governance",
    "apps.observabilidade",  # alertas de negócio + captura de erro (Sentry)
    "apps.fiscal",  # DPS: montagem/assinatura do XML + numeração sequencial
    "apps.atendimento",  # chamados e pedidos de atendimento abertos pela conversa
    "apps.rotina",  # guias, obrigações, certidões e folha — a rotina do escritório
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
    # Depois do Authentication de propósito: o escopo é decidido pelo vínculo do
    # usuário logado, e antes deste ponto `request.user` ainda não existe.
    "apps.tenants.middleware.EscopoDeTenantMiddleware",
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
# "R$ 31647,52" não se lê — com separador vira "R$ 31.647,52". Conferido que
# isto NÃO afeta os campos numéricos dos formulários do admin: `forms.Field`
# nasce com `localize=False`, então o input continua recebendo `31647.5` cru e
# o parse no POST não muda. (Testado, não deduzido.) `django.contrib.humanize`
# foi descartado aqui: `floatformat:2|intcomma` corrompe o número em pt-BR —
# lê a vírgula decimal como separador de grupo e devolve "31,647,50".
USE_THOUSAND_SEPARATOR = True
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

# --- Filas por prioridade (DEC-10) -----------------------------------------
# A sazonalidade é o inimigo declarado do produto: pico nos dias 5–10 (DAS,
# folha) e 15–20. Com fila única, um lote de OCR de 300 documentos entra na
# frente da confirmação de uma emissão fiscal que tem prazo legal — e nenhum
# aumento de worker conserta ordem de chegada. Dimensionar fila é mais barato
# que dimensionar worker.
#
#   fiscal    — emissão, cancelamento, DPS. Tem prazo legal e pessoa esperando.
#   documento — OCR, importação por arquivo, sincronização de ERP. Lote, pesado,
#               tolera minutos.
#   conversa  — mensagem do WhatsApp. Não tem prazo legal, mas tem gente do
#               outro lado olhando o "digitando..."; é latência percebida.
#
# `task_default_queue` aponta para `conversa` em vez do `celery` implícito: se
# uma task nova escapar do roteamento, ela cai na fila de menor consequência, e
# não numa fila que nenhum worker está escutando. Quem impede o escape virar
# hábito é `tests/test_filas_celery.py`, que exige rota declarada para toda task
# registrada.
CELERY_TASK_DEFAULT_QUEUE = "conversa"
CELERY_TASK_ROUTES = {
    "apps.channel_whatsapp.tasks.*": {"queue": "conversa"},
    "apps.channel_evolution.tasks.*": {"queue": "conversa"},
    # Manutenção de plataforma: roda uma vez por dia e leva milissegundos.
    # Fica na fila de menor prioridade porque atraso aqui não custa nada — a
    # expiração de sessão já acontece sob demanda na primeira mensagem.
    "apps.security.tasks.*": {"queue": "conversa"},
}
# Uma task por vez por worker. Sem isto o Celery pré-carrega um lote inteiro no
# processo, e a mensagem que chegou depois espera o lote acabar mesmo com worker
# livre — o efeito é exatamente o que as filas vieram evitar.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Reconhecer só depois de executar: se o worker morre no meio de uma emissão, a
# task volta para a fila em vez de sumir. A idempotência por `message_id` é que
# torna isso seguro (ver apps/channel_whatsapp/views.py).
CELERY_TASK_ACKS_LATE = True

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
# Preço do que o modelo consome (DEC-08 item 2).
#
# USD por 1 milhão de tokens, tabela pública do Groq conferida em 09/ago/2026 —
# não de memória. Entrada servida do cache do provedor é cobrada pela metade, e
# `observabilidade/precos.py` já aplica esse desconto.
#
# Fica em `settings` pelo mesmo motivo do teto do MEI: é número do mundo, não do
# sistema. Provedor reajusta e o dólar anda sem pedir release. Modelo que não
# estiver aqui é gravado com custo zero e um aviso no log — nunca somado como
# grátis em silêncio, que faria o critério de R$ 0,60/cliente/mês passar por
# engano.
# ---------------------------------------------------------------------------
PRECOS_LLM = {
    "llama-3.1-8b-instant": {"entrada": "0.05", "saida": "0.08"},
    "openai/gpt-oss-120b": {"entrada": "0.15", "saida": "0.60"},
}
# Arredondado para cima sobre os ~5,08 do dia: entre superestimar e subestimar o
# custo, o erro que dói é o que deixa a fatura passar do previsto.
COTACAO_USD_BRL = env("COTACAO_USD_BRL", default="5.10")

# ---------------------------------------------------------------------------
# Retenções na fonte sobre serviços (apps/fiscal/retencoes.py).
#
# Conferidas em 10/ago/2026 em fonte pública, não de memória: IRRF 1,5%
# (art. 714 do RIR/2018), CSRF 4,65% (art. 30 da Lei 10.833/2003) e INSS 11%
# (art. 31 da Lei 8.212/1991). A dispensa vale quando o valor A RETER não passa
# de R$ 10 — regra das federais, não do ISS.
#
# Em `settings` pelo mesmo motivo do teto do MEI: mudança de lei não deveria
# exigir release.
# ---------------------------------------------------------------------------
RETENCAO_IRRF_PERCENTUAL = env("RETENCAO_IRRF_PERCENTUAL", default="1.5")
RETENCAO_CSRF_PERCENTUAL = env("RETENCAO_CSRF_PERCENTUAL", default="4.65")
RETENCAO_INSS_PERCENTUAL = env("RETENCAO_INSS_PERCENTUAL", default="11")
RETENCAO_DISPENSA_ATE = env("RETENCAO_DISPENSA_ATE", default="10.00")

# Fração do limite mensal em que a degradação começa — acima dela a extração de
# campos cai para o modelo barato, e só no limite cheio o modelo é cortado
# (apps/observabilidade/orcamento.py).
ORCAMENTO_FRACAO_DEGRADACAO = env("ORCAMENTO_FRACAO_DEGRADACAO", default="0.8")

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
# Chave de cifra de campo (Credencial/AplicativoIntegracao/Escritorio — ver
# apps/credentials/crypto.py e chaves.py).
#
# Preferir o ARQUIVO: variável de ambiente é legível por `docker inspect` e
# `/proc/<pid>/environ`, ou seja, por qualquer um com o socket do Docker ou
# acesso ao host. `FIELD_ENCRYPTION_KEY_FILE` é o formato de `docker secret` e
# de `systemd LoadCredential`, e não aparece em nenhum dos dois.
#
# Rotação: várias chaves separadas por vírgula, a PRIMEIRA é a ativa. Ver o
# procedimento completo em apps/credentials/chaves.py e o comando
# `manage.py rotacionar_chave`.
FIELD_ENCRYPTION_KEY_FILE = env("FIELD_ENCRYPTION_KEY_FILE", default="")
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
# Sem SMTP configurado, cai no console — e aí o Magic Link do contador sai no
# log do container em vez de chegar no e-mail dele. Em dev isso é conveniente;
# num servidor de teste com gente real, é o que trava a primeira sessão.
# Basta definir EMAIL_HOST para o backend SMTP entrar sozinho.
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.smtp.EmailBackend"
        if EMAIL_HOST
        else "django.core.mail.backends.console.EmailBackend"
    ),
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
# Teto do MEI — configurável de propósito. O valor está congelado em R$ 81.000
# desde 2018, mas há projetos em tramitação para elevá-lo (PLP 60/2025 e
# 67/2025). Quando a lei mudar, muda-se a variável de ambiente; o radar de teto
# (apps/fiscal/teto_mei.py) não precisa de release.
# ---------------------------------------------------------------------------
TETO_MEI_ANUAL = env("TETO_MEI_ANUAL", default="81000.00")
TETO_MEI_TOLERANCIA = env("TETO_MEI_TOLERANCIA", default="0.20")  # 20% -> R$ 97.200

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
    # Navegação escrita no vocabulário do contador, não no do Django. O que ele
    # procura é "carteira", "notas", "documentos" — não "Intenções fiscais" nem
    # "Credenciais". Os models continuam acessíveis na lista de apps abaixo do
    # dashboard; isto é o caminho curto pro que se usa todo dia.
    "SIDEBAR": {
        "show_search": True,
        "navigation": [
            {
                "title": "Visão geral",
                "separator": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": _reverse_lazy("admin:index"),
                    },
                    {
                        "title": "Carteira de clientes",
                        "icon": "groups",
                        "link": _reverse_lazy("admin:painel_carteira"),
                    },
                ],
            },
            {
                "title": "Fiscal",
                "separator": True,
                "items": [
                    {
                        "title": "Fila de aprovação",
                        "icon": "pending_actions",
                        "link": _reverse_lazy("admin:agente_nf_intencao_changelist"),
                    },
                    {
                        "title": "Documentos fiscais",
                        "icon": "description",
                        "link": _reverse_lazy("admin:painel_documentos"),
                    },
                ],
            },
            {
                "title": "Configuração",
                "separator": True,
                "items": [
                    {
                        "title": "Integrações",
                        "icon": "hub",
                        "link": _reverse_lazy("admin:painel_integracoes"),
                    },
                    {
                        "title": "Ensinar o agente",
                        "icon": "school",
                        "link": _reverse_lazy("admin:core_revisar_nao_entendidas"),
                    },
                    {
                        "title": "Clientes",
                        "icon": "apartment",
                        "link": _reverse_lazy("admin:clients_cliente_changelist"),
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

# ---------------------------------------------------------------------------
# Sentry — captura de exceção. **Desligado sem SENTRY_DSN**, de propósito:
# ligá-lo adiciona um subprocessador de dado fiscal/pessoal, e isso é decisão
# de quem configura o deploy, não efeito colateral de instalar dependência.
# A raspagem do payload fica em apps/observabilidade/sentry.py — leia antes de
# ligar em produção (o projeto já tem pendência de DPA por causa do Groq).
# ---------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="desenvolvimento")

if SENTRY_DSN:
    from apps.observabilidade.sentry import configurar as _configurar_sentry  # noqa: E402

    _configurar_sentry(SENTRY_DSN, SENTRY_ENVIRONMENT)

# ---------------------------------------------------------------------------
# Retenção de dados (LGPD) — `manage.py expurgar_dados`.
#
# **Desligado por padrão de propósito.** `None` = reter indefinidamente, que é o
# comportamento histórico. Definir prazo é decisão JURÍDICA, não técnica — o
# mecanismo está pronto e testado, e passa a valer quando alguém puser o número.
# Ver docs/lgpd-inventario-dados.md §3.
#
# Não cobre a trilha de auditoria: ela é imutável, e a eliminação do conteúdo
# dela é por crypto-shredding (`manage.py eliminar_dados_titular`).
# ---------------------------------------------------------------------------
RETENCAO_MENSAGENS_PROCESSADAS_DIAS = env.int("RETENCAO_MENSAGENS_PROCESSADAS_DIAS", default=0) or None
RETENCAO_TOKENS_MAGIC_LINK_DIAS = env.int("RETENCAO_TOKENS_MAGIC_LINK_DIAS", default=0) or None
RETENCAO_CODIGOS_2FA_DIAS = env.int("RETENCAO_CODIGOS_2FA_DIAS", default=0) or None

# ---------------------------------------------------------------------------
# Transcrição de áudio — hoje via Groq (Whisper), o que envia a VOZ do cliente
# para fora do Brasil. Se o parecer jurídico considerar voz dado biométrico
# (LGPD art. 5º, II), trocar aqui por uma implementação local com o mesmo
# contrato: `(audio_bytes, mime_type) -> str | None`. Ver
# docs/lgpd-inventario-dados.md §6.
# ---------------------------------------------------------------------------
TRANSCRITOR_AUDIO = env("TRANSCRITOR_AUDIO", default="apps.channel_whatsapp.transcricao.transcrever_groq")
