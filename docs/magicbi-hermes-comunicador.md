# Magic BI — Hermes como comunicador oficial e assistente pessoal (Lumen)

> **Decisão registrada (jul/2026):** o Hermes deixa de ser "opção B avaliar depois" e
> passa a ser o **comunicador oficial** da plataforma — a persona **Lumen**, assistente
> pessoal de cada empresa no WhatsApp. A regra inviolável da arquitetura permanece
> intacta: **o Hermes conversa; quem decide e executa é o núcleo determinístico**
> (Django + governança de tiers). Hermes nunca encosta em credencial nem chama API
> fiscal/ERP diretamente.

---

## 1. Divisão de responsabilidades (a fronteira que não se cruza)

```
WhatsApp Cloud API (BSP oficial)          ← canal: SEMPRE o oficial, nunca o gateway
        │ webhook                            de consumidor que o Hermes traz embutido
        ▼
channel_whatsapp (Django) ── ack rápido + idempotência por message_id
        ▼
   HERMES (Lumen) ─ borda conversacional
   · entende linguagem natural, mantém o tom, memória da conversa
   · 1 perfil por empresa (persona, idioma, quais ferramentas enxerga)
   · só enxerga FERRAMENTAS MCP — nunca credenciais, nunca APIs externas
        ▼ chamada de ferramenta
   SERVIDORES MCP (nosso código, dentro do Django/core)
   · mcp-fiscus   → validar/emissão NFS-e (máquina de estados do agenteNF)
   · mcp-erp      → consultar/rascunhar no Conta Azul (agenteERP)
   · mcp-conta    → status do credenciamento, plano, suporte
        ▼
   GOVERNANÇA (tiers 0–3, idempotência, aprovação humana, auditoria append-only)
        ▼
   Adaptadores → NFS-e Nacional · Conta Azul · (Bling, middleware NF-e…)
```

Por que essa fronteira: auditor fiscal exige reprodutibilidade (mesma entrada → mesma
ação). Um agente auto-evolutivo não pode ser dono do caminho que emite nota. O Hermes
"aprender" só melhora a conversa — nunca muda o que é permitido executar.

## 2. Como usar o sistema de perfis (o assistente "de cada empresa")

O recurso que justifica o Hermes é o **perfil por cliente** distribuído como configuração:

| Campo do perfil | Exemplo |
|---|---|
| Persona | "Lumen, assistente da Padaria Estrela — trata o dono por 'seu João'" |
| Ferramentas MCP visíveis | MEI serviço: só `mcp-fiscus` + `mcp-conta`; ME/EPP: + `mcp-erp` |
| Tiers habilitados | Piloto: 0–1 |
| Limites | `notas_mes: 200`, horário de avisos proativos |
| Conhecimento local | CNAEs do cliente, tomadores frequentes, produtos de estoque |

Provisionar empresa nova = gerar perfil + credenciais no cofre + registrar no Grimório.
Nenhum deploy. É exatamente o "novo cliente = novo perfil, não novo código" da arquitetura.

**Avisos proativos** (o lado "comunicador oficial"): nota emitida, rejeição fiscal,
aprovação pendente, boleto vencendo, resumo semanal do caixa. Saem como templates
*utility* aprovados na Meta (fora da janela de 24h), disparados pelo núcleo — o Hermes
apenas redige a variação conversacional dentro do template permitido.

## 3. Regras operacionais para adotar o Hermes com segurança

1. **Versão fixada (pin)** — o Hermes lança minor quase toda semana sem garantia de
   estabilidade de API. Fixar versão exata, upgrade mensal via canary (perfil interno de
   teste conversa 48h antes de promover).
2. **Canal só pelo BSP oficial** — o gateway de consumidor do framework (Baileys, não
   oficial) fica desligado; risco de banimento do número e violação da política Meta.
   Desde a **v0.17.0 (jun/2026)** o Hermes suporta oficialmente a WhatsApp Business
   Cloud API — usar sempre ≥ v0.17.
3. **Task-specific por política Meta (jan/2026)** — a persona declara propósito: fiscal
   e gestão. Pedidos fora do escopo → recusa gentil padronizada no prompt do perfil.
4. **Auto-evolução desligada em produção** — skills novas entram por release de perfil,
   revisadas, nunca aprendidas ao vivo.
5. **Fallback sem LLM** — se Hermes ou o provedor de modelo cair, o núcleo serve o menu
   determinístico ("1-emitir nota, 2-status…") direto pelo channel_whatsapp. O canal
   nunca depende do Hermes para existir.
6. **Custo sob controle** — roteamento de modelo por baixo do Hermes sobre **Groq**
   (decisão jul/2026, ver §7): `llama-3.1-8b-instant` para turno comum, `openai/gpt-oss-120b`
   para montagem/validação de nota; prompt caching no perfil.

## 4. Hospedagem

| Componente | Onde | Racional |
|---|---|---|
| Núcleo Django + Celery + Postgres + Redis | **AWS `sa-east-1`** (piloto pode nascer em PaaS região Brasil — Railway/Render — se confirmada residência de dados) | LGPD, latência, RDS/ElastiCache/Secrets prontos |
| **Hermes (Lumen)** | Mesmo VPC, **host próprio (EC2 + Docker ou Fargate)** rodando **1 processo-perfil POR EMPRESA** (~200–300 MB cada), co-locados | Isolamento de dados por design (ver §6); isolar ciclo de deploy do núcleo; rede privada até os MCP |
| Servidores MCP | Dentro do núcleo Django (in-process/sidecar), expostos só na rede privada | Guardrails e credenciais ficam do lado governado |
| Cofre | Secrets Manager + KMS | Nunca segredo em variável de ambiente do Hermes |
| Observabilidade | Sentry + structlog nos dois lados; correlação por `message_id` | Uma conversa rastreável ponta a ponta |

Dimensionamento piloto: 1 host (2 vCPU/4 GB) empacota ~15–25 processos-perfil com folga
— sessões são IO-bound e cada processo consome ~200–300 MB. Um host 4 vCPU/8 GB cobre
~25–30 empresas. A partir de ~50 clientes, 2+ hosts (reduz blast radius). Container/host
**dedicado** a um único cliente só quando parceiro white-label exigir isolamento
contratual — nunca como padrão (custo 7–12× maior, ver §6).

**O que NÃO fazer:** hospedar Hermes em máquina fora do Brasil (LGPD), dar ao Hermes
acesso à internet aberta de saída (só BSP + MCP + provedor LLM), ou deixar o webhook da
Meta apontando direto para o Hermes (sempre passa pelo channel_whatsapp, que ack'a,
deduplica e enfileira).

## 5. Migração a partir da arquitetura atual

A recomendação anterior ("comece pela Opção A, adaptadores já como MCP") foi o que tornou
esta decisão barata: o núcleo continua o mesmo. Sequência:

1. Semana 1–2 do cronograma: núcleo com function-calling direto (Opção A) para destravar
   o Fiscus em homologação.
2. Em paralelo, empacotar os adaptadores como servidores MCP internos (já previsto).
3. Introduzir o Hermes atrás de flag por perfil: coorte interna → 3 clientes → todos.
4. Se o Hermes travar a entrega, o produto degrada para a Opção A sem retrabalho — o
   caminho de execução é o mesmo.

---

## 6. Hermes independente por cliente — DECIDIDO (jul/2026): sim, por processo

Pergunta respondida com pesquisa: **um Hermes por cliente não só é possível como é o
único modelo seguro hoje** — porém por **processo**, não por container.

**Por que multi-tenant num processo único é inviável:** há issue crítica aberta no
repositório ([NousResearch/hermes-agent #34352 — "Solving the Multi-Tenant Hermes
Problem"](https://github.com/NousResearch/hermes-agent/issues/34352)) documentando que
operações de memória do agente **vazam contexto entre conversas** quando um processo
atende vários tenants. Em contexto fiscal/LGPD isso é eliminatório. O modelo nativo do
Hermes é 1 instância = 1 identidade: cada perfil (`hermes -p <nome>`) roda processo
próprio com home isolado (config, memória, skills, sessões) — o isolamento por processo
é *feature*, resolve o vazamento sem esperar fix upstream.

**Por que NÃO 1 container/Fargate por cliente:** custo e operação explodem sem ganho
que o piloto exija (Fargate `sa-east-1`, 24/7, jul/2026):

| Modelo | 10 clientes | 30 clientes | 100 clientes |
|---|---|---|---|
| 1 container/cliente (0,5 vCPU/1 GB) | ~R$ 1.430/mês | ~R$ 4.290/mês | ~R$ 14.300/mês |
| **Processos-perfil co-locados (escolhido)** | ~R$ 570/mês | ~R$ 1.150/mês | ~R$ 3.430/mês |

(EC2/VPS BR custa ~40–50% menos que Fargate.) Além do custo: release minor semanal do
Hermes → upgrade+canary ×N containers, drift de versão entre clientes, N pipelines.
No modelo co-locado o pin de versão e o canary são **um só por host** e provisionar
cliente = criar perfil (segundos, sem deploy) — preserva o "novo cliente = novo perfil,
não novo código".

**Regras do modelo escolhido:** supervisor + healthcheck por perfil (systemd/supervisor);
~15–25 perfis por host 2 vCPU/4 GB; 2+ hosts a partir de ~50 clientes; host dedicado só
por exigência contratual de white-label (tier premium).

## 7. Framework de orquestração da Opção A — DECIDIDO (jul/2026): Pydantic AI

Avaliados em jul/2026: LangGraph (v1.2, mai/2026), Agno (v2.5.x), Pydantic AI (V2
estável em jun/2026), OpenAI Agents SDK, Google ADK, CrewAI, Claude Agent SDK e SDK puro.

**Escolha: [Pydantic AI V2](https://ai.pydantic.dev/)** como biblioteca da borda
conversacional (a "Opção A" deixa de ser SDK puro):

- **Encaixe arquitetural perfeito**: é uma biblioteca leve, roda **dentro da task
  Celery** (não impõe runtime/servidor próprio, ao contrário do Agno AgentOS); o LLM
  propõe via function-calling **tipado e validado** — a extração dos campos da nota
  (valor, tomador, descrição do serviço) vira schema Pydantic com retry automático de
  validação, e o núcleo Django continua decidindo/executando (CNAE nunca vem do
  modelo — sempre do cadastro do cliente).
- **Groq de primeira classe + MCP nativo**: `GroqProvider`/`GroqModel` de primeira
  classe na biblioteca — roteamento `llama-3.1-8b-instant` (turno comum, ~R$0,25/milhão
  tokens input) / `openai/gpt-oss-120b` (montagem de nota, ~R$0,75/milhão) trivial;
  cliente MCP nativo prepara a fronteira com os servidores mcp-fiscus/mcp-erp da Fase 3.
- **Ativo e estável**: V2 estável (jun/2026), mantido pela equipe Pydantic, redesign
  "harness-first" com capabilities compostas.

**Por que não os outros:** LangGraph é o mais maduro em estado durável, mas seu valor
central (grafo/checkpoint de estados) duplica o que a nossa máquina de estados
determinística já faz no Django — pagaríamos curva de aprendizado íngreme por
redundância. Agno é rápido e multi-tenant nativo, porém v2 recente com churn de API e
orientado ao seu próprio runtime FastAPI. Claude Agent SDK tem o MCP mais profundo, mas
é desenhado para agentes de código/SO e trava fornecedor na borda. CrewAI/ADK/OpenAI
SDK: centrados em outros provedores ou em "crews" que não usamos.

**Por que Groq em vez de Anthropic (decisão jul/2026, atualizada):** a inferência da
Groq (LPU) é ordens de grandeza mais barata por token que Claude Haiku/Sonnet, com
latência menor — decisivo num produto de ticket baixo (R$ 20–50/mês por MEI) em que o
custo de IA por interação precisa ficar bem abaixo de R$ 0,05. Contrapartida: os modelos
open-weight servidos pela Groq (Llama, GPT-OSS) raciocinam um degrau abaixo de
Sonnet/Opus em casos ambíguos — mitigado pelo guard determinístico (CNAE/alíquota nunca
saem do modelo) e pelo fallback por palavra-chave se a extração falhar ou vier
incompleta. Bônus de pilha única: a Groq também serve **Whisper** (transcrição), o que
cobre o item de voz do MVP (áudio→texto, `magicbi-mvp-cronograma.md`) sem outro
fornecedor. Ver nota de LGPD/subprocessador em
`requisitos-dev-piloto-rotina.md` §9.3.

**Relação com o Hermes:** não muda. Pydantic AI é a Opção A (MVP e fallback permanente);
o Hermes/Lumen entra na Fase 3 como runtime da persona, por cima da mesma fronteira MCP.
Se o Hermes decepcionar, a borda Pydantic AI já atende produção.
