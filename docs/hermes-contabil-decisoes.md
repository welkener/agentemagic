# Hermes Contábil — decisões de arquitetura (08/ago/2026)

Registro das decisões tomadas ao adotar a especificação "Hermes Contábil"
(tenant = escritório contábil, 50 escritórios × até 1.000 empresas cliente).
Cada uma tem o **porquê**, não só o "o quê" — é isso que impede a decisão ser
revertida por engano daqui a três meses.

Análise que originou estas decisões: [`hermes-contabil-ajuste.md`](hermes-contabil-ajuste.md).
Cronograma que as executa: [`hermes-contabil-cronograma.md`](hermes-contabil-cronograma.md).

Formato: **DEC-nn** · situação · decisão · consequência. Decisão revogada nunca
é apagada — é marcada como substituída, com o número da que a substituiu.

---

## DEC-01 — Stack: manter Django 5.2 + Celery. Recusar FastAPI/SQLAlchemy/arq.

**Situação.** A spec pede Python 3.12 + FastAPI + SQLAlchemy 2.0 async + arq.
O backend em produção é Django 5.2 + DRF + Celery/Redis + Postgres 16, com
~10 mil linhas em `backend/apps` e 37 arquivos de teste.

**Decisão.** Manter Django. Adotar **todos os requisitos de arquitetura** da
spec sobre ele.

**Porquê.** Cada capacidade que a spec atribui à stack nova é alcançável na
atual, porque nenhuma delas é do framework:

| Requisito da spec | Como é atendido em Django |
|---|---|
| RLS por `current_setting('app.tenant_id')` | É recurso do **Postgres**, não do ORM. Migração `RunSQL` + role sem `BYPASSRLS` + `SET LOCAL` no middleware |
| Filas por prioridade (fiscal > documento > conversa) | `task_routes` do Celery + workers com `-Q` |
| Async | `config/asgi.py` já existe; o I/O lento já roda fora do request, no worker |
| SessionContext | Objeto próprio, independente de framework |
| Tool registry cacheável por tenant | Idem |

O que se perderia está pago e testado: escopo de tenant no admin
(`apps/tenants/escopo.py`), migrações de 8 apps, o Grimório, e a suíte de
isolamento (`tests/test_multitenancy.py`). O gargalo de p95 num agente de
WhatsApp é a latência do LLM e da API do ERP, não o overhead do framework —
e 150 sessões simultâneas de pico não forçam troca de stack.

**Consequência.** Nenhuma linha de FastAPI entra no repositório. Onde a spec
cita `arq`, lê-se Celery; onde cita SQLAlchemy async, lê-se Django ORM. Se um
componente novo exigir de fato async ponta a ponta, ele nasce como serviço
separado — nunca como migração do que já roda.

---

## DEC-02 — Dois produtos complementares sobre a mesma plataforma, não um pivô.

*(Revisada no mesmo dia, 08/ago/2026, por decisão do usuário: a primeira redação
tratava o Hermes como substituto do produto MEI. Não é — são complementares.)*

**Situação.** O projeto nasceu mirando o MEI/microempresa (Fiscus e Lumen no
cliente final). A spec Hermes mira o escritório contábil. Ler isso como escolha
excludente descartaria um produto que já funciona ponta a ponta.

**Decisão.** **Os dois produtos convivem**, sobre a mesma plataforma multi-tenant:

| | **Fiscus/Lumen** (MEI e micro) | **Hermes Contábil** (escritório) |
|---|---|---|
| Quem usa | A empresa final, no WhatsApp | O escritório, no painel + a carteira dele no WhatsApp |
| O que faz | Emite nota, radar de teto, consulta ERP da empresa | Guias, obrigações, documentos, folha, integração com o sistema contábil |
| Quem paga | Assinatura da empresa | Contrato do escritório, por cliente ativo/mês |

Um tenant pode operar **os dois ao mesmo tempo**: o escritório entrega Fiscus
aos MEIs da carteira e Hermes ao próprio time. E a venda direta ao MEI, sem
escritório no meio, é modelada como **um tenant operado pela própria Magic BI** —
não é caso especial no código, é mais um `Escritorio`.

**Porquê.** São complementares em três sentidos concretos, e é por isso que
separá-los custaria mais do que mantê-los juntos:

1. **Compartilham a fundação inteira** — tenancy, RLS, sessão, auditoria, tiers,
   motor fiscal, canal WhatsApp. Fazer só um não reduziria o trabalho de base.
2. **O funil é o mesmo.** O radar de teto (D3) detecta o MEI que vai estourar e
   entrega o caso ao escritório para executar a migração MEI→ME. Sem o produto
   MEI não há detecção; sem o produto do escritório não há quem execute.
3. **Respondem a ameaças diferentes.** O Fiscus responde à Caveo e aos bots de
   R$ 30 (que atacam o cliente final); o Hermes responde ao Nibo e aos sistemas
   contábeis (que atacam o escritório). Abrir mão de um deixa um flanco aberto.

**Consequência.** A prioridade dos adapters muda, mas nada é descartado: sistema
**do escritório** (`ArquivoAdapter`, DEC-09) passa na frente porque é o que não
existe; Conta Azul e Bling continuam servindo o produto MEI/micro. E o painel
(DEC-12) mostra os dois mundos na mesma tela, com a distinção aparecendo no
vocabulário — um MEI tem radar de teto, uma ME/EPP tem obrigações.

---

## DEC-03 — Tenancy de três níveis: `escritorio → cliente → usuario`.

**Situação.** Hoje o telefone é campo do `Cliente`
(`Cliente.telefone_whatsapp`, único por escritório): uma empresa = um telefone.

**Decisão.** Criar o nível `usuario` (telefone). Um cliente tem vários usuários
(sócio, RH, financeiro); um telefone pode pertencer a mais de um cliente, e
nesse caso o agente pergunta de qual empresa se trata e **fixa a escolha na
sessão**.

**Porquê.** Numa carteira de 1.000 empresas, o telefone de contador terceirizado
ou de sócio de duas empresas não é exceção — é rotina. E o modelo atual não só
não representa isso: a constraint de unicidade por escritório **impede** o
cadastro, então o caso hoje falha em silêncio no atendimento.

**Consequência.** Migração de dados (telefone sai de `Cliente`, entra em
`Usuario`), reescrita de `ClienteManager.por_telefone` — a lógica de nono dígito
de `apps/clients/telefone.py` se preserva inteira —, estado novo em
`security.SessaoWhatsapp` para o cliente fixado, e extensão de
`tests/test_multitenancy.py` com o caso "mesmo telefone, dois clientes do mesmo
escritório".

---

## DEC-04 — Row Level Security no Postgres, como defesa em profundidade.

**Situação.** O isolamento hoje é de aplicação: `EscopoEscritorioMixin.get_queryset`
no admin e resolução por número no webhook. É bem feito e testado — e depende de
todo código futuro lembrar de filtrar.

**Decisão.** Policy de RLS em toda tabela de domínio, sobre
`current_setting('app.tenant_id')`, com role de aplicação **sem `BYPASSRLS`**.
O `SET LOCAL app.tenant_id` sai de um ponto único: middleware para HTTP e
`task_prerun` para o Celery.

**Porquê.** A regra que a spec chama de mais importante do sistema não pode
depender de disciplina. Um `.objects.filter()` esquecido em código novo vaza
dado fiscal entre escritórios concorrentes; com RLS, o mesmo esquecimento
devolve zero linhas. O escopo de aplicação **continua** — RLS é a rede embaixo,
não a substituta.

**Consequência.** Duas armadilhas conhecidas, ambas viram teste: (a) o worker
Celery rodando sem tenant setado — daí o `task_prerun`; (b) migrações e comandos
de gestão, que rodam como superuser do banco e legitimamente ignoram a policy.
Teste obrigatório: query sem `app.tenant_id` setado não devolve linha nenhuma.

---

## DEC-05 — Nenhum identificador de escopo em schema exposto ao modelo, e um teste que prova isso.

**Situação.** Já é verdade por construção: o único schema que o LLM preenche
(`DadosNotaExtraidos`) tem `tomador`, `valor` e `descricao_servico`; CNPJ e CNAE
vêm do cadastro (`core/orchestrator.py:363`). Mas nada **verifica** isso.

**Decisão.** Teste que percorre o tool registry e falha se qualquer schema JSON
exposto ao modelo contiver campo chamado `tenant_id`, `cliente_id`, `cnpj`,
`empresa_id` ou `escritorio_id`. Escopo entra sempre por `ctx: SessionContext`,
resolvido no webhook — nunca como parâmetro do modelo.

**Porquê.** É o que impede "me mostra o faturamento do concorrente". A regra
vale hoje por sorte de projeto; o teste é o que a mantém verdadeira quando a
décima tool for escrita por outra pessoa em outro mês. É o critério de aceite
nº 2 da spec e custa poucas horas.

**Consequência.** Primeiro commit do Sprint 1, antes de qualquer tool nova.

---

## DEC-06 — Certificado A1 continua por **cliente**, não por tenant.

**Situação.** A spec diz "certificado digital A1 por tenant". O modelo atual tem
`Credencial` por cliente (`credentials/models.py:38`), com detecção de CNPJ
divergente entre certificado e cliente.

**Decisão.** Manter por cliente. "Certificado do tenant" é caso **adicional**
(escritório que emite em nome próprio), nunca substituto.

**Porquê.** No cenário contábil cada empresa tem o seu certificado, e a API do
ADN/Sefin exige mTLS com o certificado **do prestador** em toda chamada
(confirmado em 12/jul/2026 — a procuração eletrônica não cobre a API, ver
`magicbi-custodia-fiscal.md`). Um certificado por escritório emitiria nota do
CNPJ errado. O que o escritório tem de coletivo é procuração e-CAC, que é outra
coisa e já está mapeada.

**Consequência.** A spec, neste ponto, está errada para o mercado brasileiro. O
cofre (`apps/credentials`) fica como está; o onboarding do tenant (DEC-11 do
cronograma, Sprint 6) coleta certificado **por cliente**, em lote.

---

## DEC-07 — ~~Painel: Grimório/django-unfold continua.~~ **SUBSTITUÍDA por DEC-12.**

*Registro preservado conforme a regra do documento.* A decisão original era não
reescrever a UI e apenas somar três telas ao Unfold, com o argumento de que o
que faltava era conteúdo, não framework. **A objeção do usuário se repetiu pela
terceira vez em 08/ago/2026** ("não gostamos dessa visualização do admin,
queremos uma aplicação completa de gestão da informação") — três rodadas do
mesmo feedback não são questão de conteúdo. Ver DEC-12.

O que dessa decisão continua valendo: as contas moram em `painel/metricas.py`,
separadas da apresentação. É justamente isso que faz a substituição custar
interface e não lógica.

---

## DEC-08 — Escada de modelo: T0 determinístico vira a camada da frente. Groq fica em T1. T2/T3 reavaliado com número.

**Situação.** Hoje o roteador Groq (`llama-3.1-8b-instant`) atende **primeiro** e
o determinístico por palavra-chave é só fallback de indisponibilidade
(`orchestrator.py:236`). Não há registro de tokens, custo, latência ou tool calls
por tenant, nem limite de gasto.

**Decisão.**
1. **Inverter a ordem**: o determinístico atende primeiro (2ª via de guia, status
   de obrigação, "recebeu meu documento", menu), meta de 40% das mensagens.
   O LLM só entra no que sobrar.
2. Registrar **tokens, custo, latência, tool calls e erro por tenant**.
3. **Limite de gasto por tenant com degradação** (cai para T1) antes de cortar.
4. Groq permanece no T1. Para T2/T3, medir o efeito do prompt caching por tenant
   com número real antes de decidir trocar de provedor — a escolha do Groq foi
   feita quando o contexto era pequeno e não havia system prompt por tenant.

**Porquê.** É a mudança de maior impacto simultâneo em custo e em p95: resposta
determinística é instantânea e grátis, e no pico dos dias 5–10 são justamente as
mensagens repetitivas que dominam o volume. E sem (2) não há como saber se o
critério de aceite de R$ 0,60/cliente/mês foi atingido — hoje o número não
existe.

**Consequência.** O fallback determinístico deixa de ser código de emergência e
passa a ser caminho principal, então ganha teste próprio de cobertura de
intenções. A troca de provedor no T2/T3 fica condicionada a medição, não a
opinião.

---

## DEC-09 — Adapters: `ArquivoAdapter` primeiro; porta tipada; API só onde existir.

**Situação.** `AdapterBase` (`apps/adapters/base.py`) é síncrono e genérico por
string de recurso. Os adapters escritos (Conta Azul, Bling) são ERP **da empresa
cliente**. A spec pede Domínio/Thomson Reuters, Alterdata, Questor e Omie —
sistemas **do escritório**.

**Decisão.** Converter `AdapterBase` para a porta tipada da spec e implementar
**`ArquivoAdapter` (TXT/CSV por pasta monitorada) antes de qualquer API**.
Conta Azul e Bling permanecem como adapters de ERP-da-empresa.

**Porquê.** A maioria dos ~78 mil escritórios brasileiros opera em Domínio,
Alterdata ou Questor, e essas plataformas não expõem API pública utilizável para
o que o Hermes precisa. A spec já reconhece isso ao pedir o `ArquivoAdapter` —
e ele não é contorno técnico, é **o posicionamento**: Hermes é a camada
conversacional de quem já tem sistema contábil e não vai trocar. Um adapter de
API alcança um fornecedor; o de arquivo alcança o mercado.

**Consequência.** Toda sincronização é assíncrona, idempotente (chave natural +
hash) e tem fila de reconciliação com retry exponencial e **DLQ visível no painel
do tenant** (DEC-07). Sem a DLQ visível, importação por arquivo falha em
silêncio — que é exatamente como esse tipo de integração costuma morrer.

---

## DEC-10 — Filas do Celery por prioridade: fiscal > documento > conversa.

**Decisão.** Três filas, `task_routes` no settings, workers dedicados.

**Porquê.** A sazonalidade é o inimigo declarado da spec: pico nos dias 5–10
(DAS/folha) e 15–20. Com fila única, um lote de OCR de 300 documentos atrasa a
confirmação de uma emissão fiscal com prazo legal. Dimensionar a fila é mais
barato que dimensionar worker.

---

## DEC-11 — As garantias que a spec omite permanecem.

**Situação.** A spec identifica o usuário **só pelo telefone**, lista
`cancelar_nota` como tool comum e não menciona 2FA nem trilha encadeada.

**Decisão.** Mantidas, sem exceção:

| Garantia | Onde | Porquê não cai |
|---|---|---|
| Vínculo de sessão `wa_id↔CNPJ` com Magic Link e expiração | `apps/security/` | Telefone é clonável. Num produto que emite documento fiscal, identidade por telefone puro é buraco, não simplificação |
| 2FA por valor (`Perfil.valor_2fa_acima_de`) | `apps/security/` | Segundo canal (e-mail) para emissão acima do teto do perfil |
| Cancelamento nunca pelo cliente — vira pedido ao contador | `_pedir_cancelamento` | Cancelamento tem efeito contábil e prazo legal; a decisão é de quem responde tecnicamente por ela |
| Auditoria append-only com hash encadeado | `apps/audit/` | É o que reconstitui "quem autorizou esta nota e a partir de quê" meses depois |
| Tiers 0–3 com fail-safe | `apps/governance/tiers.py` | Intenção desconhecida é recusada como destrutiva, não liberada como leitura |

**Porquê.** São o argumento de confiança que separa o produto de um bot de R$ 30
— e, na venda ao escritório (DEC-02), viram objeção respondida em vez de risco
assumido. Nenhum concorrente da faixa documenta proteção contra clonagem de
número.

---

## DEC-12 — O Grimório vira aplicação própria (server-rendered), fora do admin. O admin fica como backoffice.

*(Substitui DEC-07. Decidida em 08/ago/2026.)*

**Situação.** Terceira rodada do mesmo feedback sobre a interface. A camada
analítica (`apps/painel/metricas.py`, 317 linhas testadas) já é sólida e não tem
UI dentro dela; o que o contador vê são changelists do admin com tema aplicado —
tabelas de banco de dados, não uma aplicação de gestão.

**Decisão.** Construir o Grimório como **aplicação própria server-rendered**:
Django + Tailwind + HTMX, layout e componentes próprios, **fora do admin**. O
Django admin permanece como **backoffice** — CRUD da equipe Magic BI, casos de
exceção e edição de cadastro — mas deixa de ser a interface de trabalho do
contador.

Descartadas, com motivo:
- **React + TS sobre API DRF** (o que a spec pedia): entrega o mesmo resultado
  visual por ~2× o esforço, com build Node no deploy, duas autenticações e uma
  superfície de API nova para manter. O ganho real — interações ricas, estado de
  cliente — não é o que este produto pede: o trabalho do contador é acompanhar,
  filtrar e agir sobre exceções, que é leitura com ação pontual. Fica disponível
  como evolução: com RLS (DEC-04) no banco, expor uma API depois deixa de ser o
  risco que era.
- **Mais telas no Unfold**: é a decisão que já falhou três vezes.

**Porquê server-rendered e não SPA.** Além do custo: o isolamento hoje vive na
camada de aplicação e no banco (DEC-04). Uma SPA exige uma API que reimplemente
o escopo em serializers e permissions — mais um lugar onde dado de um escritório
pode vazar para outro. Server-rendered reusa `metricas.py`, que já aplica o
escopo e já é testado. Menos superfície, mesma tela.

**O que a aplicação é** — a diferença entre "tela bonita" e "aplicação de
gestão" está na organização, não no CSS:

| Área | O que responde |
|---|---|
| **Hoje** (home) | "O que exige você agora": OCR de baixa confiança, nota rejeitada, certificado vencendo, MEI perto do teto, DLQ de sincronização, cliente sem responder. Ação no lugar onde o problema aparece |
| **Carteira** | Uma linha por empresa, com faturamento, teto, pendências de cadastro e estado do WhatsApp. Drill-down para a ficha da empresa |
| **Ficha da empresa** | A linha do tempo do cliente: conversas, notas, documentos, guias, obrigações e integrações — num lugar só |
| **Notas e documentos** | Emissões, cancelamentos, XML/DANFSE, fila de revisão de OCR |
| **ERP** | Estado da sincronização por cliente, última execução, **DLQ visível** com o erro em português e reprocessamento |
| **Operação** | Consumo e custo por tenant, latência, o que o agente não entendeu |

**Consequência.**
1. `apps/painel` deixa de pendurar páginas no `get_urls()` de ModelAdmins e passa
   a ter URLs próprias, com layout próprio.
2. `metricas.py` **não é reescrito** — ganha funções novas para as áreas novas.
3. O escopo de tenant passa a ser aplicado por um mixin de view próprio, não
   herdado do admin: é o item que exige teste dedicado no primeiro dia, porque
   sair do admin significa sair de `EscopoEscritorioMixin`.
4. O admin continua vivo e escopado — não vira tela morta, vira ferramenta de
   quem administra a plataforma.
5. Custo: ~1,5 sprint. O cronograma absorve isso como um bloco próprio (S2b).

**Armadilha conhecida, herdada da experiência de 27/jul:** sair do Unfold
significa perder o CSS pré-compilado dele. O Tailwind da aplicação nova precisa
de build próprio (CLI standalone, sem Node no servidor) — senão classe
inexistente vira estilo silenciosamente inerte, que é exatamente como os cinco
bugs visuais daquela sessão passaram por testes verdes.

---

## Decisões deixadas em aberto de propósito

| Questão | Quando decidir | O que destrava |
|---|---|---|
| Trocar de provedor de LLM no T2/T3 | Fim do Sprint 2 | Medição de custo/tenant existir (DEC-08 item 2) |
| Qual adapter de API depois do `ArquivoAdapter` | Sprint 5 | Primeiro escritório real contratado e o sistema que ele usa |
| API pública / app móvel do contador | Depois do S2b | RLS em produção (DEC-04) + o Grimório próprio estabilizado (DEC-12) |
| Cofre próprio de A1 (Sigillum) | Inalterado — só com auditoria externa + seguro cyber + economia comprovada | `magicbi-custodia-fiscal.md` |
