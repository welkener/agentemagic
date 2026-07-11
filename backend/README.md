# Magic BI — Backend (MVP, Semana 1)

Hub de backoffice que conecta o WhatsApp a APIs fiscais (Fiscus/NFS-e) e a
ERPs (agenteERP), com núcleo determinístico, adaptadores plugáveis e trilha de
auditoria append-only. Arquitetura completa em
`../docs/AgenteRotinaContabil-arquitetura-tecnica.md`; escopo do MVP em
`../docs/magicbi-mvp-cronograma.md`.

## Estado atual (Semana 1)

- Django 5.2 + DRF + Celery (Redis) scaffolded
- Webhook do WhatsApp (handshake + HMAC + fila + idempotência por `message_id`)
- Auditoria append-only com hash encadeado
- Motor de tiers 0–3
- Máquina de estados fiscal (RECEBIDO → … → CONCLUIDO)
- Adaptadores **mock**: NFS-e Nacional e ERP ("Padaria Estrela")
- Orquestrador provisório por palavras-chave (LLM entra na Semana 2)

## Setup local (Windows PowerShell)

```powershell
cd "D:\Sistemas\agente magic\backend"

# 1. Ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Dependências
pip install -r requirements.txt

# 3. Variáveis de ambiente
Copy-Item .env.example .env   # edite o .env com seus valores

# 4. Banco local (sqlite por padrão)
python manage.py migrate

# 5. Servidor de desenvolvimento
python manage.py runserver
```

Worker Celery (precisa de Redis rodando — use o docker compose abaixo ou um
Redis local):

```powershell
celery -A config worker -l info --pool=solo   # --pool=solo é necessário no Windows
```

## Testes

```powershell
pytest
```

Os testes usam sqlite em memória e Celery em modo eager — não precisam de
Docker, Redis nem de credenciais do Meta.

## Docker Compose (Postgres + Redis + web + worker)

Na raiz do repositório (`D:\Sistemas\agente magic`):

```powershell
docker compose up --build
```

Sobe: Postgres 16, Redis 7, `web` (runserver na porta 8000) e `worker`
(Celery). O `web` roda `migrate` automaticamente.

## Túnel para o webhook do Meta (cloudflared)

O Meta exige HTTPS público para o webhook. Em dev, use um túnel:

```powershell
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```

Copie a URL gerada (ex.: `https://algo-aleatorio.trycloudflare.com`) e
configure no app do Meta for Developers:

- **Callback URL**: `https://<sua-url>/webhook/whatsapp`
- **Verify token**: o mesmo valor de `WHATSAPP_VERIFY_TOKEN` do seu `.env`

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DJANGO_SECRET_KEY` | produção | Chave secreta do Django (padrão inseguro em dev) |
| `DJANGO_DEBUG` | não | `True` (padrão) / `False` |
| `DJANGO_ALLOWED_HOSTS` | produção | Hosts permitidos, separados por vírgula |
| `DATABASE_URL` | não | Padrão `sqlite:///db.sqlite3`; no compose, Postgres |
| `REDIS_URL` | não | Broker/result do Celery (padrão `redis://localhost:6379/0`) |
| `META_APP_SECRET` | sim (webhook) | App Secret do Meta — valida o HMAC `X-Hub-Signature-256` |
| `WHATSAPP_VERIFY_TOKEN` | sim (webhook) | Token do handshake GET do webhook |
| `WHATSAPP_TOKEN` | não em dev | Token da Cloud API; ausente = envio vira log |
| `WHATSAPP_PHONE_NUMBER_ID` | não em dev | ID do número de teste da Meta |
| `ANTHROPIC_API_KEY` | Semana 2 | Chave da Claude API (function-calling); sem uso se ausente |
| `CELERY_TASK_ALWAYS_EAGER` | não | `True` executa tasks inline (testes já usam) |

## Mapa dos apps

```
apps/
├── core/               # orquestrador (Opção A) + ResultadoAcao
├── clients/            # Cliente, Perfil (tier_maximo)
├── credentials/        # Credencial → referência ao cofre (nunca o segredo)
├── channel_whatsapp/   # webhook, idempotência, tasks, envio Cloud API
├── audit/              # Auditoria append-only com hash encadeado
├── governance/         # motor de tiers 0–3
├── agents/
│   ├── agente_nf/      # Intencao + máquina de estados fiscal (Fiscus)
│   └── agente_erp/     # consultas ao ERP pela interface única
└── adapters/           # AdapterBase + nfse_mock + erp_mock (Padaria Estrela)
```
