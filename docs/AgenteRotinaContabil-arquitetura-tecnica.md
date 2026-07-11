# AgenteRotinaContabil — Arquitetura Técnica

> **Atualização (jul/2026):** o produto passa a ser construído sob a marca **Magic BI**,
> em parceria com a Rotina Contábil. Nomes dos produtos: **Fiscus** (agenteNF),
> **Lumen** (assistente/comunicador sobre Hermes), **Grimório** (painel), **Sigillum**
> (cofre). O Hermes foi **promovido de "opção B" a decisão**: é o comunicador oficial na
> borda conversacional, com os adaptadores expostos como MCP e o núcleo determinístico
> mantendo a execução (ver `magicbi-hermes-comunicador.md`). Custódia detalhada em
> `magicbi-custodia-fiscal.md`; cronograma em `magicbi-cronograma.md`.

Hub de backoffice que conecta o WhatsApp a APIs fiscais e a múltiplos ERPs, com dois
subagentes:

- **agenteNF** — emissão e acompanhamento de notas fiscais (começando por NFS-e Nacional).
- **agenteERP** — operação de pedidos, estoque e financeiro sobre o ERP que o cliente já usa.

Stack alvo: **Python + Django (DRF) + React**, com uma **camada de orquestração** por cima.
Princípio de design: *um núcleo determinístico, N adaptadores, um perfil por cliente*.

---

## 1. Stack e versões

| Camada | Escolha | Versão alvo | Observação |
|---|---|---|---|
| Linguagem | Python | 3.13 | 3.12–3.14 também servem |
| Backend web/API | Django + Django REST Framework | **Django 5.2 LTS** | Suporte de segurança até abr/2028; estabilidade importa em sistema fiscal |
| Tarefas assíncronas | Celery + Redis (broker/result) | Celery 5.x | Fila, retries, idempotência, agendamentos |
| Tempo real (aprovação/painel) | Django Channels ou SSE | — | Opcional no piloto; webhook + polling já resolve |
| Banco | PostgreSQL | 16+ | Auditoria append-only, JSONB para payloads |
| Cofre de segredos | AWS Secrets Manager + KMS (ou Vault) | — | Tokens OAuth e procuração isolados; **nunca `.pfx` cru** |
| Frontend | React + Vite + TypeScript | React 18/19 | Painel de aprovação, perfis de cliente, trilha de auditoria |
| Canal | WhatsApp Business Platform (Cloud API) via BSP | — | 360dialog / Gupshup / Meta; **não** usar bridge não-oficial |
| Orquestração | Ver seção 6 | — | Orquestrador determinístico próprio; Hermes opcional na borda conversacional |
| Observabilidade | Sentry + logs estruturados (structlog) | — | Alertas de rejeição fiscal |

> Por que Django e não FastAPI, já que é IO-pesado? O Django REST Framework te dá admin,
> auth, migrações, ORM e um painel prontos — que aqui viram o **console de aprovação de
> tier e a trilha de auditoria** quase de graça. O peso assíncrono (WhatsApp, LLM, chamadas
> a ERP) fica no Celery, não nas views. É a troca certa para este produto.

---

## 2. Mapa de módulos (apps Django)

```
agenterotina/                      # projeto Django
├── config/                        # settings, urls, celery, asgi/wsgi
├── apps/
│   ├── core/                      # orquestrador, máquina de estado, motor de tiers
│   ├── agents/
│   │   ├── agente_nf/             # subagente fiscal (NFS-e, depois NF-e)
│   │   └── agente_erp/            # subagente de ERP
│   ├── adapters/                  # interface interna única + adaptadores concretos
│   │   ├── base.py                # AdapterBase (contrato)
│   │   ├── nfse_nacional/         # adaptador API NFS-e Nacional (ADN/Sefin)
│   │   ├── conta_azul/            # adaptador Conta Azul (OAuth2 v2)
│   │   ├── bling/                 # adaptador Bling (P1)
│   │   └── nfe_middleware/        # NF-e produto via "NF como serviço" (P2)
│   ├── clients/                   # cliente, perfil, provisionamento
│   ├── credentials/              # referências ao cofre, renovação de token
│   ├── channel_whatsapp/          # webhook Cloud API, envio/recebimento
│   ├── audit/                     # trilha append-only, hash encadeado
│   └── governance/                # tiers 0–3, aprovações, limites
├── frontend/                      # app React (Vite) — painel
└── deploy/                        # docker-compose, infra, migrations
```

---

## 3. Arquitetura de alto nível

```
             WhatsApp Cloud API (via BSP)
                        │  webhook (mensagem do cliente)
                        ▼
              apps/channel_whatsapp
                        │
                        ▼
   ┌───────────────────────────────────────────────┐
   │ apps/core — ORQUESTRADOR (determinístico)       │
   │  1. resolve cliente + perfil                    │
   │  2. LLM (function-calling) → PROPÕE intenção     │
   │  3. governance → aplica tier, limite, idempot.  │
   │  4. despacha para o subagente certo             │
   │  5. audit → registra tudo (append-only)         │
   └───────────────────────────────────────────────┘
              │                         │
              ▼                         ▼
      apps/agents/agente_nf     apps/agents/agente_erp
              │                         │
              └──────── apps/adapters ──┘
                        │  interface única
        consultar · criar_rascunho · alterar · emitir
              │                         │
              ▼                         ▼
     [NFS-e Nacional]           [Conta Azul] [Bling] ...
              │                         │
              └──────── apps/credentials ┘
                        │
                        ▼
              Cofre (Secrets Manager + KMS)
```

Regra inviolável: **o LLM propõe, o núcleo determinístico decide e executa.** Emitir nota
ou escrever em ERP nunca acontece direto a partir da saída do modelo — passa sempre pelo
motor de governança (tier + idempotência + auditoria).

---

## 4. Contrato do adaptador (interface interna única)

```python
# apps/adapters/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ResultadoAcao:
    ok: bool
    dados: dict | None = None
    erro_padronizado: str | None = None   # ex.: "RECURSO_NAO_ENCONTRADO"
    referencia_externa: str | None = None # id no sistema de destino

class AdapterBase(ABC):
    """Todo ERP/API fiscal implementa este contrato. O núcleo só conhece isto."""

    @abstractmethod
    def capacidades(self) -> set[str]:
        """Ex.: {'consultar_pedido','criar_rascunho_pedido','emitir_nfse'}."""

    @abstractmethod
    def consultar(self, recurso: str, filtros: dict, ctx) -> ResultadoAcao: ...   # Tier 0

    @abstractmethod
    def criar_rascunho(self, recurso: str, dados: dict, ctx) -> ResultadoAcao: ... # Tier 1

    @abstractmethod
    def alterar(self, recurso: str, id_ext: str, mudancas: dict, ctx) -> ResultadoAcao: ... # Tier 2-3

    @abstractmethod
    def emitir(self, documento: str, dados: dict, ctx) -> ResultadoAcao: ...       # Tier 1-3
```

`ctx` carrega cliente, perfil, chave de idempotência e a credencial resolvida do cofre.
Erros são **padronizados** pelo adaptador: o núcleo trata "estoque não encontrado" igual,
venha do Conta Azul ou do Bling.

---

## 5. Os dois subagentes

### 5.1 agenteNF (fiscal)

Responsável por emitir e acompanhar documentos fiscais. Fase 1 = NFS-e Nacional.

Fluxo de emissão (máquina de estado):

```
RECEBIDO → VALIDANDO → AGUARDANDO_APROVACAO → EMITINDO → CONCLUIDO
                 │                                 │
                 └── REJEITADO_VALIDACAO           └── FALHA_RETRY / CONTINGENCIA
```

- **VALIDANDO**: confere CNAE, valor, dados do tomador. **Nunca** infere alíquota livremente —
  o cálculo de IBS/CBS é centralizado na calculadora da RTC; o agente monta a DPS, não a regra.
- **AGUARDANDO_APROVACAO**: no piloto, primeira emissão de cada cliente exige confirmação
  humana (do MEI ou do contador), mesmo em Tier 1.
- **EMITINDO**: chama `adapter.emitir("nfse", dados, ctx)` → API NFS-e Nacional
  (ambiente restrito → produção). Idempotência obrigatória (mensagem repetida ≠ nota dupla).
- **CONCLUIDO**: DANFSE/PDF e link voltam pelo WhatsApp.
- **CONTINGENCIA**: retry com backoff; nunca falhar em silêncio.

Custódia: procuração eletrônica e-CAC/gov.br com escopo limitado (login gov.br Prata/Ouro
para MEI). Fase 2 (NF-e produto) exige certificado digital → via middleware "NF como serviço".

### 5.2 agenteERP

Opera o ERP do cliente pela interface única, respeitando `capacidades()` do adaptador.

| Intenção do usuário | Método | Tier |
|---|---|---|
| "qual meu estoque de X?" | `consultar("estoque", ...)` | 0 |
| "status do pedido 123" | `consultar("pedido", ...)` | 0 |
| "rascunha pedido pro cliente Y" | `criar_rascunho("pedido", ...)` | 1 |
| "muda a quantidade do item" | `alterar("pedido", ...)` | 2 |
| "cancela / muda pagamento" | `alterar(...)` | 3 |

No piloto, agenteERP fica em **Tier 0–1** (leitura + rascunho). Tier 2–3 só após validar
precisão e responsabilidade.

---

## 6. Camada de orquestração — Hermes ou orquestrador próprio?

**Recomendação: o núcleo de decisão é seu, escrito em Python dentro do `apps/core`.**
Um agente auto-evolutivo não pode ser dono do caminho que emite nota fiscal — auditor pede
reprodutibilidade (mesma entrada → mesma ação) e trilha, o oposto de "aprender skills em
produção".

Duas opções, ambas viáveis:

**Opção A — Orquestrador in-process (recomendada para o piloto).**
`apps/core` faz o function-calling direto (SDK do provedor LLM), define as *tools* como as
ações do contrato do adaptador, e o próprio Django aplica tier/idempotência/auditoria. Menos
partes móveis, tudo em um repositório, mais fácil de auditar.

**Opção B — Hermes na borda conversacional + adaptadores como MCP.**
Se quiser aproveitar o sistema de *perfis* do Hermes (perfil por cliente distribuído como
config), exponha **cada adaptador como um servidor MCP** (seu código, com os guardrails
dentro). O Hermes só *chama* as ferramentas; a decisão de executar continua no seu MCP.
"Ajustar o perfil por cliente" = escolher quais MCP + credenciais + tiers o perfil enxerga.
Cuidados: fixe uma versão do Hermes (ele lança minor quase toda semana, sem garantia de
estabilidade de API entre elas); e mantenha o canal WhatsApp no Cloud API oficial via BSP,
não no gateway de consumidor do framework.

> Sugestão prática: **comece pela Opção A** (menos risco, entrega mais rápida). Deixe os
> adaptadores já como servidores MCP internos — assim, se depois quiser plugar Hermes ou
> outro orquestrador, é encaixe, não reescrita.

> **DECIDIDO (jul/2026):** caminho A→B confirmado. Fase 1–2 rodam na Opção A; a partir da
> Fase 3 o **Hermes entra como comunicador oficial (persona Lumen)**, atrás de flag por
> perfil, com versão fixada e canal exclusivamente via BSP oficial. A execução fiscal/ERP
> permanece no núcleo determinístico. Detalhes, regras operacionais e hospedagem em
> `magicbi-hermes-comunicador.md`.

---

## 7. Modelo de dados (esboço Django)

```python
# apps/clients/models.py
class Cliente(models.Model):
    razao_social = models.CharField(max_length=200)
    cnpj = models.CharField(max_length=14, unique=True)
    contador = models.ForeignKey("Contador", null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, default="ativo")

class Perfil(models.Model):
    cliente = models.OneToOneField(Cliente, on_delete=models.CASCADE)
    adaptadores_ativos = models.JSONField(default=list)   # ["nfse_nacional","conta_azul"]
    tiers_habilitados = models.JSONField(default=lambda: [0, 1])
    limites = models.JSONField(default=dict)              # {"notas_mes": 200}
    idioma = models.CharField(max_length=8, default="pt-br")

# apps/credentials/models.py
class Credencial(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    adaptador = models.CharField(max_length=40)
    ref_segredo = models.CharField(max_length=255)  # ARN/chave no cofre — NUNCA o segredo
    escopo = models.CharField(max_length=255, blank=True)
    validade = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, default="ativa")

# apps/governance/models.py
class Intencao(models.Model):
    chave_idempotencia = models.CharField(max_length=100, unique=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    tipo_acao = models.CharField(max_length=40)
    tier = models.PositiveSmallIntegerField()
    payload = models.JSONField()
    status = models.CharField(max_length=20, default="proposta")  # proposta/aprovada/executada/falha

# apps/audit/models.py  (append-only)
class Auditoria(models.Model):
    criado_em = models.DateTimeField(auto_now_add=True)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    adaptador = models.CharField(max_length=40)
    acao = models.CharField(max_length=40)
    request = models.JSONField()
    response = models.JSONField()
    resultado = models.CharField(max_length=20)
    hash_anterior = models.CharField(max_length=64, blank=True)  # encadeamento
    hash_atual = models.CharField(max_length=64)
```

---

## 8. Superfície de API (DRF) e canal

| Rota | Método | Função |
|---|---|---|
| `/webhook/whatsapp` | POST | Recebe mensagens do BSP; enfileira no Celery |
| `/api/clientes` `/api/perfis` | CRUD | Provisionamento por cliente |
| `/api/intencoes/{id}/aprovar` | POST | Aprovação de tier 2–3 (painel) |
| `/api/auditoria` | GET | Consulta da trilha (read-only) |
| `/api/adapters/{cliente}/oauth/callback` | GET | Callback OAuth2 (Conta Azul etc.) |

Recepção do WhatsApp deve responder **rápido** (ack) e jogar o processamento para o Celery,
onde ficam as chamadas lentas (LLM, ERP, SEFAZ) com retry e idempotência.

```python
# apps/channel_whatsapp/tasks.py
@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem(self, cliente_id, mensagem, chave_idempotencia):
    if Intencao.objects.filter(chave_idempotencia=chave_idempotencia).exists():
        return  # já processada — idempotência
    orquestrador.tratar(cliente_id, mensagem, chave_idempotencia)
```

---

## 9. Frontend React (painel da Rotina)

App Vite + TypeScript, consumindo a API DRF. Telas do piloto:

- **Fila de aprovação** — intenções em `aguardando_aprovacao` (Tier 2–3 e 1ª emissão),
  aprovar/recusar em 1 clique.
- **Perfis de cliente** — ligar/desligar adaptadores, tiers e limites por cliente.
- **Trilha de auditoria** — busca read-only por cliente/ação/período.
- **Onboarding** — disparar fluxo OAuth do Conta Azul e a procuração da NFS-e.

Autenticação do painel: usuários do escritório (contadores), via auth do Django.

---

## 10. Segurança, custódia e LGPD

- Tokens OAuth por cliente e procuração isolados no cofre; no banco só a **referência**.
- Trate `client_id`/`client_secret`/`access_token` como senha de alto risco.
- **Nunca** armazenar `.pfx` cru; criptografia em repouso e em trânsito.
- Trilha de auditoria append-only com hash encadeado de toda ação fiscal.
- DPA com o escritório; minimização e consentimento de dados.
- Modo de contingência + retry — nada de falha silenciosa.
- Hospedar em nuvem no Brasil (ex.: AWS sa-east-1) por LGPD e latência.

---

## 11. Ambiente de desenvolvimento

```bash
# backend
python -m venv .venv && source .venv/bin/activate
pip install "django~=5.2" djangorestframework celery[redis] psycopg[binary] \
            structlog sentry-sdk boto3 httpx
django-admin startproject config .
python manage.py migrate && python manage.py runserver

# worker
celery -A config worker -l info

# frontend
cd frontend && npm create vite@latest . -- --template react-ts && npm i && npm run dev
```

`docker-compose` para o piloto: `web` (Django+gunicorn/uvicorn), `worker` (Celery),
`postgres`, `redis`, `frontend`.

Variáveis de ambiente mínimas: `DJANGO_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`,
`WHATSAPP_BSP_TOKEN`, `WHATSAPP_VERIFY_TOKEN`, `LLM_API_KEY`, `SECRETS_BACKEND`,
`CONTAAZUL_CLIENT_ID`, `CONTAAZUL_CLIENT_SECRET`, `NFSE_AMBIENTE=restrito`.

---

## 12. Roadmap técnico

1. **Fundação** — projeto Django, Celery, Postgres, cofre, contrato do adaptador,
   webhook WhatsApp, idempotência, auditoria. Orquestrador (Opção A).
2. **agenteNF** — adaptador NFS-e Nacional em ambiente restrito → produção, Tier 0–1,
   coorte 15–25 MEIs. Painel de aprovação em React.
3. **agenteERP** — adaptador Conta Azul (OAuth2 por empresa), leitura + rascunho, 5–8 ME/EPP.
4. **2º adaptador** — Bling, provando "novo ERP = novo adaptador + perfil", sem tocar no núcleo.
5. **Expansão validada** — Tiny/Omie/NF-e produto; Tier 2–3 após validar precisão e
   responsabilidade; avaliar Hermes/MCP (Opção B) se o volume de perfis justificar.

---

## 13. Decisões em aberto

- [x] Opção A vs. B — **decidido**: A no piloto, Hermes/Lumen entra na Fase 3 como
      comunicador oficial (`magicbi-hermes-comunicador.md`).
- [x] Modelo de custódia — **decidido**: matriz por produto/fase — procuração p/ NFS-e,
      middleware p/ NF-e, cofre próprio só em escala (`magicbi-custodia-fiscal.md`).
- [x] Marca e nomes — **decidido**: Magic BI / Fiscus / Lumen / Grimório / Sigillum
      (`magicbi-marca-e-nomes.md`), pendente busca INPI.
- [ ] Django 5.2 LTS (até abr/2028) vs. 6.0 — recomendação: 5.2 LTS por estabilidade.
- [ ] Escolha do BSP do WhatsApp.
- [ ] Backend de cofre (Secrets Manager+KMS vs. Vault).
- [ ] Provedor/modelo LLM para function-calling.
- [ ] Conversa com advogado sobre responsabilidade **antes** de escrita real em produção
      (agendada na Fase 0 do cronograma).
