# Hermes Contábil — análise de encaixe com o código atual (08/ago/2026)

Responde a uma pergunta só: **o que muda no Magic BI para atender a especificação
"Hermes Contábil"** (SaaS multi-tenant onde o tenant é o escritório contábil, 50
escritórios × até 1.000 empresas cliente).

Três seções: (1) o que já existe e serve, (2) o que a spec pede e não existe,
(3) o que a spec pede que **contradiz** decisão já tomada aqui — e a recomendação.

---

## 0. Veredito em um parágrafo

> **Atualização 08/ago/2026:** a leitura inicial tratava o Hermes como
> substituto do produto MEI. Decidido que **não é** — os dois convivem e são
> complementares (DEC-02). O resto da análise abaixo permanece válido.

A spec não é um produto novo: é o Magic BI **um nível acima**, vendido para o
escritório além do MEI. ~70% dos guardrails que ela exige (tenancy por
escritório, isolamento testado, auditoria encadeada, tiers, confirmação em duas
etapas, idempotência, motor fiscal separado do LLM, cofre de credenciais) **já
estão escritos e testados** — 10 mil linhas em `backend/apps`, 37 arquivos de
teste. O que falta é real, mas é adição, não fundação.

O ponto de atrito não é técnico, é de stack: a spec pede FastAPI + SQLAlchemy
async + arq + React. Reescrever isso custa ~6 sprints só para voltar ao ponto
onde já estamos, sem entregar um recurso novo de produto. **Recomendação:
adotar todos os requisitos de arquitetura da spec sobre o Django que já roda.**
Detalhe em §3.1.

---

## 1. O que já existe e atende a spec

| Requisito da spec | Onde está | Observação |
|---|---|---|
| Tenant = escritório contábil | `apps/tenants/models.py::Escritorio` | Já é a raiz de multi-tenancy, não um objeto de branding |
| 1 número WhatsApp por tenant | `Escritorio.whatsapp_phone_number_id` + `channel_whatsapp/views.py:80` | O número que **recebe** resolve o tenant — exatamente a regra da spec. Constraint de unicidade impede ambiguidade |
| Teste de isolamento | `tests/test_multitenancy.py` (14 testes) | Cenário do pior caso: dois escritórios com **mesmo CNPJ e mesmo telefone**. Cobre banco, webhook, listagem do admin, URL direta, dropdown de FK, agregados do dashboard, branding |
| Nenhuma tool recebe escopo do LLM | `core/orchestrator.py::DadosNotaExtraidos` | O schema exposto ao modelo tem só `tomador`, `valor`, `descricao_servico`. `cnpj_prestador` e `cnae` vêm do cadastro (`orchestrator.py:363`) — nunca do modelo. **A regra de ouro da spec já é a regra de ouro do projeto** |
| Motor fiscal sem LLM | `apps/fiscal/` (`dps.py`, `eventos.py`, `numeracao.py`, `teto_mei.py`) | DPS montada com `nfelib` contra o XSD oficial, assinatura XMLDSig com `signxml`. Devolve pendências em português quando falta campo — o comportamento que a spec descreve |
| Confirmação em duas etapas | `Intencao` (máquina de estados) + `_resolver_confirmacao` | `RECEBIDO → VALIDANDO → AGUARDANDO_APROVACAO → CONCLUIDO`, com o motivo da autorização amarrado ao `message_id` e ao `wa_id` (`_motivo_autorizacao`) |
| Auditoria imutável | `apps/audit/` | Append-only com hash encadeado e verificação de cadeia |
| Governança de risco | `apps/governance/tiers.py` | Tiers 0–3 com fail-safe (intenção desconhecida = tier 3, recusada) |
| Cofre de credenciais | `apps/credentials/` | `.pfx` cifrado em repouso, rotação de chave, detecção de CNPJ divergente |
| Idempotência | `MensagemProcessada` + `Intencao.chave_idempotencia` | Retry do Celery não duplica emissão |
| Adapter port | `apps/adapters/base.py::AdapterBase` | Contrato único já existe (Conta Azul, Bling, NFS-e Nacional, mocks) |
| Fila + ack rápido | `channel_whatsapp/views.py` → Celery | Webhook responde 200 na hora, HMAC validado |
| Áudio | `channel_whatsapp/transcricao.py` | Whisper via Groq |
| Painel do tenant | `apps/painel/` (Grimório, django-unfold) | Dashboard, Carteira, Integrações, Documentos — somente leitura, escopado por tenant |

**Coisas que o projeto tem e a spec nem menciona** — e que não devem ser
descartadas, porque são o argumento de confiança contra bot de R$ 30:

- **Vínculo de sessão `wa_id↔CNPJ` com Magic Link e expiração** (`apps/security/`).
  A spec identifica o usuário só pelo telefone. Telefone é clonável; num produto
  que emite documento fiscal isso é um buraco. Manter.
- **2FA por valor** (`Perfil.valor_2fa_acima_de`) — emissão acima do teto do
  perfil pede código por e-mail (segundo canal).
- **Cancelamento nunca pelo cliente** (`_pedir_cancelamento`) — vira pedido ao
  contador. A spec lista `cancelar_nota` como tool de escrita comum; aqui já
  se decidiu que cancelamento tem efeito contábil e prazo legal, e a decisão é
  de quem responde tecnicamente. Manter a regra mais restritiva.
- **Radar de teto do MEI** (`fiscal/teto_mei.py`) e **LGPD** (inventário,
  expurgo, eliminação por titular).

---

## 2. O que a spec pede e não existe

Em ordem de esforço/risco.

### 2.1 Terceiro nível de tenancy: `usuario` — **quebra de schema**
Hoje: `Escritorio → Cliente`, e o telefone é **campo do Cliente**
(`Cliente.telefone_whatsapp`, único por escritório). A spec quer
`tenant → cliente → usuario(telefone)`, com um cliente tendo vários telefones
(sócio, RH, financeiro) e o mesmo telefone podendo pertencer a mais de um
cliente ("de qual empresa você quer falar?" fixado na sessão).

Impacto: migração que move o telefone para uma tabela nova, reescrita de
`ClienteManager.por_telefone` (a lógica de nono dígito em `clients/telefone.py`
se preserva inteira), e um estado novo de sessão para o cliente fixado. Toca
`security.SessaoWhatsapp`, o pipeline e os testes de isolamento.

### 2.2 Row Level Security no Postgres — **não existe**
O isolamento hoje é de aplicação: `EscopoEscritorioMixin.get_queryset` no admin
e resolução por número no webhook. É bem feito e testado, mas **um `.objects.filter()`
esquecido em código novo vaza**. A spec pede a rede embaixo: policy em toda
tabela de domínio sobre `current_setting('app.tenant_id')`.

Dá para fazer em Django sem trocar de ORM: migração `RunSQL` com as policies,
role de aplicação sem `BYPASSRLS`, e um ponto único que faz `SET LOCAL app.tenant_id`
— middleware para HTTP e um `task_prerun` para o Celery (é onde a maioria das
implementações esquece e o worker roda sem tenant setado).

### 2.3 Tool registry — **não existe**
Hoje o orquestrador é um `if/elif` de 9 intenções fixas com roteador Groq 8B
(`orchestrator.py:204`). A spec pede 8 tools de leitura + 7 de escrita, com
registry, schema por tenant e cacheável.

Junto vem o **teste que a spec chama de mais importante**: varrer o registry e
falhar se qualquer schema exposto ao modelo contiver `tenant_id`, `cliente_id`,
`cnpj` ou `empresa_id`. Hoje isso é verdade por construção, mas **não é
verificado** — nada impede o próximo campo entrar errado. É barato e deve ser o
primeiro teste escrito.

### 2.4 Pipeline de documento (OCR) — **não existe**
Existe transcrição de áudio, nada de imagem/PDF. Falta: storage S3 por tenant,
OCR com extração de chave de acesso NF-e/valor/CNPJ/vencimento, classificação,
confirmação com o cliente, lançamento via adapter, e **fila de revisão humana no
painel** para OCR de baixa confiança. A spec chama isso de maior ROI do produto
e concordo — é também exatamente onde o Nibo já está (§4).

### 2.5 Tools de rotina contábil — **não existem**
`consultar_das`, `segunda_via_guia` (DAS/DARF/GPS/FGTS/ISS), `status_obrigacoes`
(DCTFWeb/EFD/eSocial/SPED), `consultar_folha`, `listar_certidoes`,
`solicitar_admissao`/`demissao`. As tools atuais são de ERP do cliente (estoque,
pedido, fluxo de caixa) — outro público. Essas dependem inteiramente de §2.6.

### 2.6 Adapters de sistema contábil — **pivô de portfólio**
Os adapters escritos (Conta Azul, Bling) são ERP **da empresa cliente**. A spec
pede Domínio/Thomson Reuters, Alterdata, Questor, Omie — sistemas **do
escritório**. É outro conjunto de integrações, com outra realidade: a maioria
não tem API pública utilizável, e por isso a spec pede `ArquivoAdapter`
(TXT/CSV por pasta monitorada). Isso está certo e é o caminho realista.

O `AdapterBase` atual é **síncrono** e genérico por string de recurso
(`consultar(recurso, filtros, ctx)`); a spec quer porta tipada e async. Convertível
sem drama, mas é reescrita do contrato e dos 4 adapters existentes.

Falta também **fila de reconciliação com retry exponencial e DLQ visível no
painel** — hoje há retry do Celery, sem DLQ nem tela.

### 2.7 Mensagem proativa — **não existe**
Nenhum envio ativo, nenhum template utility aprovado, nenhum opt-out, nenhuma
janela de horário comercial por tenant. A spec acerta ao dizer que **custa mais
que o LLM**: a 25 msg/empresa/mês, o template proativo é o item dominante da
conta unitária, não o token.

### 2.8 Escada de modelo e medição por tenant — **parcial**
Existe roteador T1 (Groq 8B) e T2/T3 (`gpt-oss-120b`), com fallback determinístico
por palavra-chave. Faltam:
- **T0 como camada da frente** (meta de 40%): hoje o determinístico é só
  *fallback quando o Groq cai*, não o primeiro atendente. Inverter isso é a
  mudança de maior impacto no custo e na latência p95.
- **Registro de tokens/custo/latência/tool calls/erro por tenant** — não existe.
- **Limite de gasto por tenant com degradação para T1** — não existe.
- **Prompt caching** — Groq não tem o mesmo modelo de cache que a Anthropic;
  se a spec depende de cache de system prompt + tools por tenant, isso é um
  motivo legítimo para reavaliar o provedor no T2/T3.

### 2.9 Filas por prioridade — **não existe**
Celery com fila única. A spec quer `fiscal > documento > conversa`. Em Celery é
`task_routes` + workers com `-Q`: baixo custo, alto valor no pico dos dias 5–10.

### 2.10 Onboarding self-service e billing — **não existe**
Provisionamento hoje é comando de gestão (`provisionar_escritorio`,
`cadastrar_cliente`). Falta embedded signup do WABA, upload de A1 no fluxo,
teste de conectividade do ERP, importação inicial da carteira, e billing por
cliente ativo/mês com medidor de tokens.

### 2.11 Painel React
Hoje é django-unfold dentro do admin. Ver §3.3.

---

## 3. Onde a spec contradiz decisão já tomada

### 3.1 Stack: FastAPI + SQLAlchemy async + arq
**Recomendação: não migrar.** Motivos concretos, não preferência:

- O que a spec ganha com FastAPI/SQLAlchemy — async, RLS, filas por prioridade,
  isolamento por contexto — **é todo alcançável em Django**. RLS é do Postgres,
  não do ORM. Fila por prioridade é `task_routes`. Async tem `async def` em
  views e `ASGI` (o `config/asgi.py` já existe).
- O que se perde é grande e está pago: admin com escopo de tenant testado
  (`escopo.py`), migrações de 8 apps, Grimório, e os 37 arquivos de teste.
- O gargalo de p95 num agente WhatsApp é a **latência do LLM e da API do ERP**,
  não o overhead do framework. Async ajuda em concorrência de I/O — e o worker
  Celery já dá isso por processo.
- 150 sessões simultâneas de pico não é carga que force troca de stack.

Se ainda assim a preferência for FastAPI, a decisão precisa ser tomada **agora**,
antes de escrever mais uma tool — não depois.

### 3.2 Certificado A1 "por tenant"
A spec diz "certificado digital A1 por tenant". No modelo atual a `Credencial` é
**por cliente** (`credentials/models.py:38`), com validação de que o CNPJ do
certificado bate com o do cliente. Isso está **mais correto** para o cenário
contábil: cada empresa tem seu próprio certificado. O que o escritório costuma
ter é **procuração e-CAC**, que é outra coisa (já mapeada em
`magicbi-custodia-fiscal.md`). Manter o modelo atual e tratar "certificado do
tenant" como caso adicional, não substituto.

### 3.3 Painel React × Grimório atual
> ⚠ **Esta recomendação foi revista no mesmo dia — ver DEC-12.** O texto abaixo
> defendia manter o Unfold e somar três telas. O usuário recusou a visualização
> do admin pela **terceira vez** em 08/ago/2026, pedindo "uma aplicação completa
> de gestão da informação". Três rodadas do mesmo feedback não são questão de
> conteúdo. **Decisão vigente:** Grimório vira aplicação própria server-rendered
> (Django + Tailwind + HTMX), fora do admin; o admin fica como backoffice.
> React sobre API DRF foi avaliado e descartado por custo (~2×) e por criar uma
> superfície de API nova onde o escopo de tenant precisaria ser reimplementado.

Trocar o admin por React é ~1 sprint inteiro que não entrega recurso novo — e o
feedback registrado em 27/jul (`painel/views.py`) foi resolvido com páginas
analíticas dentro do Unfold. A camada que realmente falta no painel não é o
framework de UI, é **conteúdo**: fila de revisão de OCR, DLQ de reconciliação,
medidor de custo por tenant. Recomendo construir essas três telas no Unfold e
reavaliar React só se a UI virar objeção comercial concreta.

### 3.4 "Comece por tenancy + RLS + teste de isolamento"
Concordo com a ordem, com uma correção: **tenancy de escritório já está pronta e
verde**. O que falta na Sprint 1 é o nível `usuario` (§2.1), o RLS (§2.2) e o
**teste de tool registry** (§2.3) — que é o único dos três que ainda não tem
nenhuma linha escrita e é o que a spec chama de crítico.

---

## 4. Concorrência — o tabuleiro muda com o pivô

Vender **para o escritório** troca o campo de batalha. Os concorrentes do
`magicbi-analise-disrupcao.md` (ZapCont, Mei.ai, Nexmei, Meire) miravam o MEI
direto — viram *não-concorrentes*, ou até canal. Quem passa a importar:

### Ameaça nº 1 — Nibo (concorrente direto do Hermes)
O [Nibo](https://www.nibo.com.br/) já vende exatamente o posicionamento da spec:
[WhatsApp integrado ao Obrigações Plus](https://www.nibo.com.br/atendimento-whatsapp),
onde cada mensagem trocada com o cliente vira **atendimento rastreável com
histórico e protocolo**, e a IA **lê o documento encaminhado pelo WhatsApp e
sugere valores, datas e fornecedores** para o lançamento. Isso é o §2.4 desta
análise — já em produção, com base instalada.

Onde o Hermes se diferencia de verdade (e onde não se diferencia):
- **Não se diferencia** em: canal WhatsApp, inbox com protocolo, OCR que sugere
  lançamento. Entrar por aí é atacar o forte do incumbente.
- **Se diferencia** em: (a) **ERP-agnóstico** — o Nibo exige que o escritório
  seja Nibo; o `ArquivoAdapter` para Domínio/Alterdata/Questor alcança a maioria
  do mercado que não vai trocar de sistema contábil; (b) **agente que executa**,
  não canal que assiste um humano — a spec pede tools de escrita com confirmação
  em duas etapas, e o Nibo posiciona a IA como sugestão para o operador;
  (c) **governança verificável** — tiers, auditoria encadeada, vínculo de sessão
  anticlonagem, teste de isolamento contra prompt injection. Num produto que fala
  com 1.000 empresas por escritório, isso é objeção de venda real.

### Ameaça nº 2 — os donos do sistema contábil
Domínio/Onvio (Thomson Reuters), Alterdata, Questor. Nenhuma evidência pública de
agente WhatsApp com IA até agora — o [Portal do Cliente do
Onvio](https://onvio.com.br/clientcenter/) é portal e app, sem camada
conversacional. Mas eles têm o dado e a distribuição. **Consequência de produto:**
a integração via `ArquivoAdapter` não é só solução técnica de contorno, é o
posicionamento — Hermes é a camada conversacional de quem já tem o sistema
contábil e não quer trocar.

### Ameaça nº 3 — contabilidade tech-enabled financiada
A [Caveo captou R$ 54 mi em Série A](https://www.robertodiasduarte.com.br/caveo-capta-r54-mi-e-usa-ia-via-whatsapp-na-contabilidade/)
para escalar contabilidade tech-enabled com assistente de IA no WhatsApp (vertical
de médicos PJ). Não compete pelo escritório — **compete pelo cliente do
escritório**. Isso reforça a proposta de valor do Hermes junto ao contador:
o escritório que não tiver camada conversacional perde carteira para quem tem.

### O resto do tabuleiro (inalterado)
Omie e Conta Azul seguem servindo a própria base (ERP da empresa, não do
escritório); Meire/governo segue comprimindo o básico grátis do MEI; Zucchetti
segue na aposta de áudio→nota. Nada disso ataca o escritório contábil.

### Mercado
~78 mil escritórios contábeis ativos no Brasil, ~72% com até 10 funcionários. A
meta de 50 tenants da spec é 0,06% do mercado — o gargalo é venda e onboarding,
não escala técnica. O que reforça §2.10 (onboarding self-service) como item de
receita, não de conveniência.

---

## 5. Sprints ajustados ao código real

Reescrita das 6 sprints da spec descontando o que já existe:

| Sprint | Spec original | Ajustado |
|---|---|---|
| 1 | tenancy + RLS + SessionContext + teste isolamento + T0 | **Nível `usuario` + RLS + teste de tool registry + T0 como camada da frente.** Tenancy de escritório e teste de isolamento já verdes — estender, não criar |
| 2 | agente T2 com tools de leitura + painel | **Tool registry + migrar as 9 intenções atuais para tools + medição por tenant.** Painel: 3 telas novas no Grimório |
| 3 | motor fiscal + NFS-e com confirmação | **Já existe.** Sobra: guias (DAS/DARF/GPS/FGTS) e retenções, que são o que o escritório pede — e o teste de que `fiscal/` não importa `agent/` (hoje `teto_mei.py` importa `clients.models`) |
| 4 | pipeline de documento | Inalterado — **é o maior item pendente e o mais disputado (Nibo)** |
| 5 | ERPAdapter + 2 implementações + reconciliação | **`ArquivoAdapter` primeiro**, depois a API que houver. Converter `AdapterBase` para a porta tipada async |
| 6 | contas a pagar + proativo + billing + onboarding | Inalterado. Orçar o template utility antes de escrever a primeira mensagem proativa |

**Ordem sugerida para começar hoje**, se a decisão de §3.1 for "manter Django":
1. Teste de tool registry (algumas horas, e é o critério de aceite nº 2 da spec).
2. RLS + `SET LOCAL` em middleware e `task_prerun` (a rede embaixo do que já
   funciona).
3. Nível `usuario` + desambiguação "de qual empresa você quer falar?".
4. Só então tool registry de verdade e as tools novas.

---

## 6. Decisões que precisavam ser tomadas — todas resolvidas em 08/ago/2026

Registro fechado em [`hermes-contabil-decisoes.md`](hermes-contabil-decisoes.md):

1. **Stack** → manter Django (DEC-01). Nenhuma linha de FastAPI entra no repo.
2. **Público** → **os dois produtos convivem** (DEC-02). Não é pivô: Fiscus/Lumen
   (empresa final) e Hermes Contábil (escritório) são complementares e dividem a
   fundação inteira. Conta Azul/Bling continuam servindo o produto MEI/micro;
   `ArquivoAdapter` para sistema do escritório passa na frente por não existir.
3. **Provedor de LLM no T2/T3** → decisão adiada **com gatilho**: reavaliar no
   fim do Sprint 2, quando a medição de custo por tenant existir (DEC-08).
4. **Painel** → aplicação própria server-rendered, fora do admin (DEC-12).
   Substitui a recomendação de §3.3 acima.
