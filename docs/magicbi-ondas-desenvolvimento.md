# Magic BI — Ondas de desenvolvimento (24/jul/2026 → piloto real na Rotina)

> **Objetivo deste documento:** traduzir o estado real do `backend/` hoje + os requisitos
> novos de segurança em **ciclos curtos e acionáveis** (ondas de poucos dias, não semanas
> inteiras) para chegar o mais rápido possível a um teste real — o próprio time da Rotina
> Contábil usando Fiscus + agenteERP em produção controlada ("nossa contabilidade" como
> primeiro cliente/QA vivo). Não substitui `magicbi-mvp-cronograma.md` (a referência de
> escopo/critérios de "go") nem `magicbi-cronograma.md` (o plano de 24 semanas) — é o
> **sequenciamento tático** de como atacar o que falta, ordenado por dependência real de
> engenharia, não por calendário.

---

## 0. Onde estamos de verdade (checagem no código, não só no doc)

Confirmado em `backend/apps/` (24/jul/2026, ver commits até `0f5b08b`):

| Feito | Onde |
|---|---|
| Webhook WhatsApp (HMAC, idempotência, fila Celery) | `channel_whatsapp/` |
| Áudio→texto (Whisper/Groq) | `channel_whatsapp/transcricao.py` |
| Auditoria append-only + hash encadeado | `audit/` |
| Tiers 0–3 | `governance/tiers.py` |
| Máquina de estados fiscal + orquestrador ligado ao mock | `agents/agente_nf/`, `core/orchestrator.py` |
| Adapters mock (NFS-e, ERP) | `adapters/nfse_mock.py`, `adapters/erp_mock.py` |
| Adapter NFS-e Nacional **mapeado, não real** | `adapters/nfse_nacional.py` — auth ainda é placeholder Bearer; falta trocar por mTLS/assinatura, bloqueado na pendência técnica do §2 |
| Adapters Conta Azul/Bling **mapeados, não normalizados** | `adapters/conta_azul.py`, `adapters/bling.py`, `adapters/oauth2.py` — endpoints com nível de confiança marcado explicitamente (25/jul/2026); `_formatar` degrada graciosamente pro payload real desconhecido |
| Fila de aprovação do contador | Django admin (`agents/agente_nf/admin.py`) — interino, cobre o requisito funcional |
| Cofre MVP (Fernet) | `credentials/crypto.py` |
| CI (GitHub Actions) | testes a cada push |
| **`apps/security` (Onda 1)** | ✅ Concluído 25/jul/2026 — `SessaoWhatsapp`/`TokenMagicLink`/`Codigo2FA`, gate no orquestrador, 2FA de 3 turnos |
| **Login do painel (Onda 4, parcial)** | ✅ Concluído 25/jul/2026 — `django-sesame` + comando `enviar_link_contador` |
| **Custódia de certificado (PSC/.pfx/procuração)** | ✅ Concluído 25/jul/2026 — `apps/credentials/` (models, `certificados.py`, `services.py`, admin com upload) — cadastro pronto; falta só a assinatura/transmissão de fato (pendência de mTLS, §2) |
| **Canal Evolution API (teste local)** | ✅ Concluído 25/jul/2026 — `apps/channel_evolution/`, SÓ para teste, nunca produção; configurável pelo painel (`ConfiguracaoEvolution`) |
| **Dashboard do Grimório (home do `/admin/`) + branding por escritório** | ✅ Concluído 25/jul/2026 — `apps/painel/`, `Escritorio` (nome/logo/cores), validado com smoke test real (não só pytest). **26/jul/2026: absorvido pelo índice do admin** (era página solta em `/painel/`, que agora só redireciona) |
| **Não existe ainda** | Painel React (o Grimório mínimo em Django cobre a demonstração); deploy fora do localhost; qualquer chamada real a NFS-e/Conta Azul/Bling em ambiente vivo; assinatura/transmissão real da NFS-e (bloqueada na pendência de mTLS) |

**Leitura:** a fundação é sólida — o gargalo não é desenhar mais nada, é **trocar 3 mocks
por integrações reais** (NFS-e, Conta Azul, sessão/segurança) e sair do localhost. É
exatamente o que as ondas abaixo atacam, em ordem de dependência.

---

## 1. Onda 1 — Segurança de sessão (`apps/security`) — ✅ concluída 25/jul/2026

**Por quê primeiro:** nenhum cliente real (nem interno) deveria tocar o sistema sem o
vínculo `wa_id↔CNPJ`. Era rápido de construir e destravava testar com segurança.

- [x] Model `SessaoWhatsapp` (OneToOne por cliente) + `TokenMagicLink` + `Codigo2FA`
      (`apps/security/models.py`) — spec em `magicbi-seguranca-sessao.md`
- [x] Emissão/validação de JWT curto com **PyJWT** (`apps/security/services.py`:
      `gerar_magic_link`/`validar_magic_link`, TTL 15 min configurável)
- [x] Endpoint do painel que confirma o Magic Link e ativa a sessão
      (`apps/security/views.py` — `GET /security/validar/<token>/`)
- [x] Gate no orquestrador: `Orquestrador.processar()` bloqueia qualquer intenção e
      dispara `enviar_magic_link()` se `sessao_ativa(cliente)` for falso
      (`apps/core/orchestrator.py`)
- [x] Anticlonagem: `sessao_ativa()` bloqueia (`status=BLOQUEADA`) se o `wa_id`
      validado divergir do telefone atual do cadastro
- [x] Task Celery `expirar_sessoes_vencidas_task` (defesa em profundidade — o gate já
      expira sob demanda; agendamento via beat fica pra Onda 4/deploy)
- [x] 2FA por código avulso (e-mail) acima de `Perfil.valor_2fa_acima_de` — `secrets`,
      sem lib nova; fluxo de 3 turnos (emitir → sim → código) integrado no orquestrador,
      com expiração (5 min) e limite de tentativas (3) cancelando a emissão
- [x] `Cliente.email_contato` adicionado (canal do Magic Link/2FA — nunca o WhatsApp)
- [x] 18 testes novos em `tests/test_security.py` (sessão ausente/expirada/divergente,
      Magic Link feliz/reuso/expiração, e-mail ausente degrada, fluxo 2FA completo,
      código errado excede tentativas, código expira) — suíte completa 80/80 verde

**Gate da onda:** ✅ passou — sessão ausente/expirada bloqueia antes do roteamento de
intenção; fluxo de 2FA ponta a ponta emitindo nota real (mock) após código correto.

---

## 1.5 Onda "testar hoje" — custódia de certificado + canal local — ✅ concluída 25/jul/2026

Decisão do usuário: em vez de esperar a pendência de mTLS do §2 se resolver, deixar o
**cadastro** dos 3 modos de custódia pronto agora, e destravar teste local com uma
instância Evolution já existente antes de configurar a Cloud API oficial da Meta.

- [x] **Custódia de certificado** (`docs/magicbi-custodia-fiscal.md` §2.1) — `Credencial`
      suporta `CERTIFICADO_PSC` (provedor/identificador), `CERTIFICADO_PFX` (upload do
      `.pfx` no admin, cifrado com Fernet, metadados extraídos automaticamente — CNPJ,
      razão social, validade) e `PROCURACAO` (consentimento/fallback). Upload valida o
      arquivo (abre com a senha via `cryptography`) **antes** de gravar qualquer coisa —
      senha errada nunca produz um registro quebrado no banco. Alerta visual no admin se
      o CNPJ do certificado não bate com o do cliente. `resolver_adapter_nfse` e
      `NfseNacionalAdapter` aceitam qualquer um dos 2 tipos de certificado — a assinatura/
      transmissão de fato continua bloqueada pela pendência de mTLS (§2), mas o
      credenciamento em si já é real e testável.
- [x] **Canal Evolution API para teste local** (`apps/channel_evolution/`) — webhook em
      `/webhook/evolution`, mesma pipeline de processamento do canal oficial
      (`apps/channel_whatsapp/pipeline.py`, extraída nesta onda para não duplicar lógica).
      **Marcado explicitamente como SÓ TESTE, nunca produção** (docstring do `apps.py`,
      settings, commit) — a decisão de usar exclusivamente a Cloud API oficial da Meta em
      produção continua de pé (`docs/magicbi-hermes-comunicador.md` §3); isto é só uma
      ponte pro escritório validar o fluxo ponta a ponta com o que já existe hoje.
- [x] 32 testes novos (custódia + canal Evolution); suíte completa 104/104 verde.

### Como rodar o teste local com a Rotina (checklist operacional)

1. `docker-compose up -d` (Postgres + Redis) e `python manage.py migrate`.
2. `.env`: preencher `EVOLUTION_BASE_URL`, `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` da
   instância Evolution que já existe.
3. Expor o Django local (`cloudflared tunnel` ou similar) e cadastrar a URL pública
   `/webhook/evolution` como webhook da instância Evolution (evento `messages.upsert`).
4. Django admin: cadastrar `Cliente` de teste (com `telefone_whatsapp` do celular de
   quem vai testar + `email_contato` real, necessário pro Magic Link/2FA funcionarem) e
   `Perfil`.
5. Mandar "oi" pelo WhatsApp vinculado à instância Evolution → deve vir a mensagem de
   credenciamento (Magic Link) da Onda 1 — abrir o link (`/security/validar/<token>/`)
   pra ativar a sessão antes de qualquer outra coisa funcionar.
6. Testar o fluxo fiscal (cai no mock de NFS-e até a Onda 2 resolver a pendência de
   mTLS) e o fluxo ERP (cai no mock de Conta Azul/Bling até haver credencial OAuth real).
7. **Nunca** apontar essa mesma instância Evolution para clientes reais fora do time —
   é só para teste interno, a produção exige a Cloud API oficial (risco de banimento).

**⚠ Achado do smoke test (25/jul/2026): sem `GROQ_API_KEY`, o fluxo fiscal nunca
completa por linguagem natural.** O roteamento por palavra-chave (fallback) classifica
a intenção certa ("emite nota..." → Fiscus), mas a **extração de campos** (tomador/
valor/descrição) sem Groq devolve sempre vazio — o cliente fica preso em "Quase lá!
Ainda preciso de: tomador, valor..." para sempre, mesmo mandando os dados na mensagem.
O fluxo ERP (`consultar_estoque` etc.) **funciona 100% sem Groq** (só depende de
roteamento por palavra-chave, não de extração). **Para a demonstração real com o
contador, `GROQ_API_KEY` precisa estar configurada** (`.env` ou painel — ainda não é
configurável pelo painel, só `.env` por enquanto).

---

## 1.6 Onda "painel + validação real" — ✅ concluída 25/jul/2026

Pedido do usuário: "deixe o painel do contador funcionando, para demonstração e
homologação... nada de hardcode... deixe os dois agentes funcionais para demonstração
com o contador via evolution para validar."

- [x] **Grimório mínimo — dashboard em `/painel/`** (`apps/painel/`): visão consolidada
      só-leitura pra demonstração — cards (notas emitidas hoje/mês, aguardando aprovação,
      sessões ativas, clientes ativos), status dos canais (Meta/Evolution), certificados
      fiscais vinculados (com alerta de CNPJ divergente), notas emitidas recentes
      (protocolo + link do DANFSE) e atividade capturada (últimos eventos da trilha de
      auditoria). Login por Magic Link (`django-sesame`, Onda 4) já leva direto pra cá —
      `LOGIN_REDIRECT_URL` mudou de `/admin/` pra `/painel/`.
- [x] **Configuração do WhatsApp pelo painel, não só `.env`**: novo model
      `ConfiguracaoEvolution` (`apps/channel_evolution/`) — `base_url`/`instancia`/
      `api_key` (cifrada) editáveis no admin; `.env` continua como fallback/bootstrap.
      Ação de admin "Testar conexão" chama `GET /instance/connectionState/{instance}`
      da Evolution e mostra o estado real (`open`/`close`/`connecting`).
- [x] **`Escritorio` (tenant) — nada hardcoded**: Magic BI é SaaS multi-tenant
      (`docs/magicbi-marca-e-nomes.md` §1 — a Rotina é o primeiro parceiro, não o único).
      Novo model `Escritorio` (`apps/painel/models.py`): nome, logo (upload,
      `Pillow`/`ImageField`), cor primária e de acento (hex, configuráveis). O dashboard
      nunca mais tem "Rotina Contábil"/navy/dourado fixos no template — sem escritório
      cadastrado, cai num branding genérico **"Magic BI"** (a marca da plataforma, não de
      um escritório específico). Testado explicitamente: `"Rotina Contábil" not in corpo`
      quando não há `Escritorio` ativo.
- [x] **Notas emitidas persistidas de verdade**: `Intencao` ganhou `protocolo` e
      `danfse_url`, preenchidos em `confirmar_emissao()` — antes só existiam na mensagem
      de resposta do WhatsApp, não ficavam consultáveis no banco/painel.
- [x] **Smoke test real (não só pytest)** — rodei `runserver` de verdade (porta 8010,
      `CELERY_TASK_ALWAYS_EAGER=True`) e bati com HTTP real em `/webhook/evolution`:
      - **agenteERP: funcional ponta a ponta** — "qual meu estoque?" → resposta correta
        do mock em <1s, via HTTP real, sem nenhum mock de teste no meio.
      - **Fiscus (confirmação + emissão): funcional ponta a ponta** — intenção em
        `AGUARDANDO_APROVACAO` → "sim" via webhook → `CONCLUIDO` com `protocolo`/
        `danfse_url` reais gravados no banco (o achado do `GROQ_API_KEY` acima é sobre a
        *extração inicial* por linguagem natural, não sobre confirmação/emissão, que
        não depende de Groq).
      - Login + `/painel/` conferido via sessão real autenticada (`requests` + cookies) —
        mostrou a nota emitida, o cliente de teste e as métricas corretas.
      - **Lição operacional**: o banco local (`postgres`/`5432` nativo) não tinha as
        migrações novas desta sessão aplicadas — só o banco de teste do pytest (efêmero)
        estava em dia. Rodar `python manage.py migrate` sempre que uma sessão adiciona
        migração antes de testar contra o banco de dev real (já é o passo 1 do runbook
        acima, mas vale reforçar).
- [x] 9 testes novos em `tests/test_painel.py` (dashboard, branding, certificado
      divergente); suíte completa **113/113 verde**.

---

## 1.7 Onda "GROQ_API_KEY real" — ✅ concluída 25/jul/2026

Usuário configurou a chave de produção e pediu pra validar modelos gratuitos + os dois
agentes por voz/texto via Evolution. Achados e ajustes (detalhe completo do catálogo
Groq/free tier em `magicbi-hermes-comunicador.md` §7):

- [x] **D6 (voz) agora funciona também no canal Evolution**, não só no Meta — antes
      era só texto nesse canal (escopo deliberado da Onda 1.5); `apps/channel_evolution/
      services.py: baixar_midia()` usa `POST /chat/getBase64FromMediaMessage/{instance}`
      (diferente do Meta: busca pela própria chave da mensagem, não um `media_id`
      separado) + reaproveita `apps.channel_whatsapp.transcricao` (mesmo Whisper).
- [x] **Achado real do smoke test**: "envie um relatório das vendas" caía na resposta
      genérica do Lumen — nem Groq nem o fallback por palavra-chave reconheciam
      "relatório"/"vendas" como sinônimo de nenhuma intenção. Corrigido: mapeado pra
      `consultar_pedido` em `apps/core/orchestrator.py` (`_REGRAS_ERP`).
- [x] **Logging corrigido**: os dois `except Exception:` que escondiam o motivo real de
      falha do Groq (roteador e extração) agora logam `erro=str(exc)` — antes só
      diziam "indisponível, caiu no fallback" sem dizer por quê.
- [x] Confirmado (doc oficial): `llama-3.1-8b-instant`, `openai/gpt-oss-120b` e
      `whisper-large-v3-turbo` cabem no **free tier** da Groq — provavelmente cobre o
      piloto inteiro sem custo, watchpoint é monitorar o consumo real.
- [x] 1 teste novo (`tests/test_orchestrator.py`) + os de áudio na Onda anterior;
      suíte completa **117/117 verde**.

---

## 1.8 Onda "servidor de teste" — ✅ concluída 25/jul/2026

Deploy real no servidor compartilhado (`192.140.50.108`, credenciais em
`backend/servidor.txt`, gitignorado) pra demonstração com o contador.

- [x] `docker-compose.deploy.yml` (raiz do repo) — só `postgres` + `web`,
      `CELERY_TASK_ALWAYS_EAGER=True` (sem Redis/worker — servidor com RAM/disco
      apertados, ver §0). `STATIC_ROOT` + `runserver --insecure` pro admin renderizar
      com CSS mesmo com `DEBUG=False`.
- [x] **Reaproveitada a instância Evolution do projeto `aosatende`** (mesmo servidor,
      `evoapicloud/evolution-api:v2.3.7`, porta `8081`) — mas **numa instância nova e
      isolada** (`magicbi-rotina-teste`), nunca reaproveitando as 2 instâncias que já
      existiam lá (pertencem a outros clientes reais — uma com quase 9 mil mensagens de
      produção). Webhook da instância nova apontado pra
      `http://192.140.50.108:8020/webhook/evolution`.
- [x] `apps/painel` deployado em `http://192.140.50.108:8020/painel/`; `Escritorio`
      "Rotina Contábil" cadastrado (cores da marca, sem logo ainda — subir pelo admin
      quando tiver o arquivo).
- [x] Verificado ponta a ponta no servidor real (webhook simulado com `apikey` real) —
      pipeline completo funcionando; único "erro" esperado foi o envio de resposta
      falhar por timeout porque a instância Evolution ainda não tinha WhatsApp
      conectado (QR não escaneado ainda) — confirma que só falta o passo humano de
      parear o número.

**Pendente do lado do usuário:** escanear o QR code da instância `magicbi-rotina-teste`
com o WhatsApp que vai ser usado no teste (QR expira rápido — pedir um novo na hora).

**Atualização 25/jul/2026 — domínio + HTTPS:** usuário registrou DNS
(`painel.magicbi.com.br → 192.140.50.108`) e configurou o nginx-proxy-manager já
existente no servidor (mesmo padrão dos outros projetos ali — `fiscalis.magicbi.com.br`,
`nexio.magicbi.com.br`, `aosatende.magicbi.com.br`, todos sob o mesmo domínio raiz).
Certificado Let's Encrypt emitido automaticamente pelo NPM. Ajustes feitos do nosso
lado:
- `CSRF_TRUSTED_ORIGINS` + `SECURE_PROXY_SSL_HEADER` adicionados em `settings.py` —
  sem isso o login do admin quebra com erro de CSRF atrás de proxy que termina TLS.
- `DJANGO_ALLOWED_HOSTS` e `PAINEL_BASE_URL` atualizados pro domínio (IP continua na
  lista de hosts permitidos, então o webhook antigo apontando pro IP:8020 não quebrava
  durante a transição).
- Webhook da instância Evolution migrado de `http://192.140.50.108:8020/...` pra
  `https://painel.magicbi.com.br/webhook/evolution`.
- **Achado**: o `DJANGO_SECRET_KEY` gerado originalmente tinha `$` no meio (ex.: `...puk$u`)
  — o parser de `.env` do Docker Compose interpreta `$u` como variável não definida e
  troca por string vazia, corrompendo o valor real usado silenciosamente (sem erro,
  só um aviso `"u" variable is not set`). Regerado sem `$` (usar `secrets.token_urlsafe`
  em vez de `get_random_secret_key` para segredos que vão em `.env` de Compose).
- Confirmado ponta a ponta via HTTPS + domínio: login funciona, webhook processa
  (fiscal e ERP), só falta o WhatsApp real ser conectado via QR.

**Feedback do usuário (mesmo dia): "ta feio... pelo menos temos que restilizar o
admin".** O Django admin (usado como fila de aprovação/CRUD, ver
`AgenteRotinaContabil-arquitetura-tecnica.md`) estava 100% no tema azul/verde padrão do
Django. Restilizado via CSS custom properties (mesmo mecanismo que o tema padrão do
admin já usa desde Django 3.2+ — não reescreve nenhum template de CRUD):
`templates/admin/base_site.html` (cabeçalho com `site_header` dinâmico) +
`static/admin/css/magicbi_admin.css` (paleta navy/periwinkle). Validado com screenshot
real (Edge headless, sem chromium-cli disponível nesse ambiente Windows) antes/depois —
cabeçalho, botões e sidebar agora usam a marca. O `/painel/` já tinha branding por
escritório desde a Onda 1.6; agora os dois ambientes (admin + painel) ficam
visualmente coerentes.

**Feedback seguinte, mesmo dia — "ainda não curti... redesenhe pra parecer uma
aplicação... veja o que tem de mais novo no mercado".** O reskin via CSS custom
properties (acima) não foi longe o suficiente. Pesquisei o panorama atual de temas de
admin Django e adotei **django-unfold** (0.101.0, Tailwind CSS, coberto no blog oficial
do Django em 2025, suporta Django 5.2/6.0 e Python 3.12-3.14, sem build JS) — mais
moderno e ativo que Jazzmin/Grappelli. Todos os 11 `ModelAdmin` + 1 inline (7 apps)
migrados pra `unfold.admin.*`; paleta indigo (Tailwind, família do periwinkle já usado
no painel); reskin manual anterior removido (superado). Validado com screenshots reais
antes de subir — login, índice e changelist agora têm cara de aplicação SaaS moderna,
não admin genérico. `/painel/` seguiu intacto, sem conflito.

**Feedback 26/jul/2026 — "não entendi o painel sendo que já vamos ter o admin do
django, pq não deixa o painel como se fosse dashboard".** Procede: deixar o `/painel/`
"intacto" (acima) foi meia decisão. Ele *já* era só dashboard (leitura pura, zero CRUD
— nunca duplicou o admin), mas era uma **página HTML solta**, com CSS próprio, fora do
unfold: duas superfícies web, dois visuais, dois lugares pra procurar a mesma coisa.
Como não havia responsabilidade a separar, o dashboard virou a **home do admin**:

- `DASHBOARD_CALLBACK` (`UNFOLD` em `config/settings.py`) → `apps/painel/views.py::
  dashboard_callback` injeta as métricas no contexto do índice; o template
  `apps/painel/templates/painel/dashboard.html` estende o `admin/index.html` do unfold
  e desenha os cards **acima** da lista de apps (o CRUD continua logo abaixo).
  Ligado por `admin.site.index_template` — nome próprio de propósito: um override
  chamado `admin/index.html` não consegue estender o original (o cached loader devolve
  ele mesmo e o Django estoura `extends cannot appear more than once`).
- **Branding do tenant subiu de escopo**: `SITE_HEADER`/`SITE_SUBHEADER`/`SITE_LOGO` do
  unfold aceitam callables (`unfold/sites.py::_get_value`), então `apps/painel/
  branding.py` resolve o `Escritorio` ativo por requisição — o nome/logo do escritório
  agora valem no admin **inteiro**, não só numa página. Antes valiam só no `/painel/`.
- `LOGIN_REDIRECT_URL` voltou pra `/admin/`; `/` e `/painel/` redirecionam pra lá
  (a URL antiga já circulou em e-mails de Magic Link, docs e no servidor de teste).
- `apps/painel/urls.py` e o HTML/CSS avulso foram removidos. `apps/painel` fica sendo
  o app de *apresentação* (métricas + branding + model `Escritorio`), sem rota própria.
- 121 testes passando; `tests/test_painel.py` reapontado pro `/admin/` e com dois casos
  novos (redirect do `/painel/` antigo; marca do tenant valendo também numa changelist).
  Validado com screenshot real do `/admin/` autenticado antes de subir.

⚠ **Consequência de produto a acompanhar**: o contador que entra por Magic Link agora
cai no admin completo, não numa tela curada. Isso é coerente com o que já estava
escrito (`magicbi-mvp-cronograma.md`: "o admin é interino, não o Grimório final" — a
fila de aprovação sempre foi o changelist de `Intencao`), mas quando entrar contador
de fora da Rotina vai precisar de permissões por grupo pra não expor model demais.
→ **Resolvido no mesmo dia**, ver 1.8 abaixo.

---

## 1.8 Multi-tenancy de verdade — ✅ concluída 26/jul/2026

**Pedido do usuário:** "o sistema é para ser SaaS, então Rotina é um escritório
contábil, e podemos vender o sistema para outro escritório, e cada escritório ter seus
clientes... certifique-se que hoje está assim."

**Auditoria: não estava.** O SaaS multi-tenant existia na doc
(`magicbi-marca-e-nomes.md` §1) e nos comentários, não no schema:

- `Escritorio` era só uma tabela de logo e cores — **nenhum outro model apontava pra
  ele**, e `escritorio_ativo()` devolvia "o ativo mais recente" global.
- `Cliente` não tinha dono. Como toda a cadeia pendura em `Cliente` (perfil,
  credenciais, intenções, auditoria, sessão), não havia por onde segregar.
- `auth.User` não tinha escritório e **nenhum dos 7 `admin.py` tinha `get_queryset`** —
  contador do escritório B veria a carteira, as credenciais e os certificados da Rotina.
  Isso era vazamento entre concorrentes, não só falta de feature.
- Canal WhatsApp era um só, global (`.env`), e o roteamento resolvia o cliente por
  `telefone_whatsapp` **unique global** — dois escritórios não podiam nem atender o
  mesmo CNPJ.

**Decisões do usuário (26/jul/2026):** número de WhatsApp **por escritório**; um cliente
pertence a **um** escritório; isolamento **com auditoria de vazamento**.

**O que foi feito:**

- **`Escritorio` vira raiz de tenant** (`apps/painel/models.py`): ganha `slug`, canal
  WhatsApp próprio (`whatsapp_phone_number_id` + token cifrado) e `MembroEscritorio`
  (vínculo `auth.User` ↔ escritório). `ativo` mudou de sentido — era "é este que aparece
  no branding", agora é "escritório habilitado".
- **`Cliente.escritorio`** (FK `PROTECT` — nunca apagar escritório levando dado fiscal).
  `cnpj`/`telefone_whatsapp` deixam de ser únicos globalmente e passam a ser únicos
  **por escritório**: cobre cliente que troca de contador, ou que tem contador fiscal e
  trabalhista separados.
- **Roteamento pelo número que RECEBEU**, não pelo remetente
  (`escritorio_por_phone_number_id` / `escritorio_por_instancia`). É o que faz o
  isolamento não depender de o cadastro do cliente estar certo. Sem escritório
  resolvido a mensagem é **descartada** — nunca processada num tenant arbitrário.
  Fallback explícito: com **um** escritório ativo, número sem casar cai nele (não há
  pra onde vazar) — é o que mantém a instalação que já está no ar funcionando.
- **Escopo no admin** (`apps/painel/escopo.py`): superuser vê tudo; membro vê o próprio
  escritório; **sem vínculo não vê nada** (padrão seguro — staff meio-provisionado nunca
  pode significar acesso total). O mixin filtra `get_queryset` *e* `formfield_for_foreignkey`
  — só o primeiro deixaria o contador criar credencial apontando pro cliente do vizinho
  pelo dropdown. `AplicativoIntegracao` (app OAuth da Magic BI) fica como plataforma,
  só superuser.
- **Branding e dashboard escopados**: a marca segue o escritório do usuário logado (antes
  era global), e as métricas do dashboard filtram pelo mesmo escopo do admin — senão o
  agregado revelaria o que a listagem esconde.
- **`provisionar_escritorio`**: cria escritório + primeiro contador + vínculo num comando
  (os três andam juntos de propósito — sem o vínculo o contador loga e não vê nada).

**Validação — 17 testes novos em `tests/test_multitenancy.py`** (138 no total). O cenário
é o pior caso de propósito: **dois escritórios com cliente de mesmo CNPJ e mesmo
telefone** — se o isolamento dependesse de os dados serem distintos, não seria
isolamento. Cobre banco (unicidade por tenant), webhook (mensagem cai no cliente do dono
do número), admin (listagem, URL direta de objeto alheio, dropdown de formulário, filtro
lateral, staff sem vínculo, superuser), dashboard e branding.

Dois vazamentos foram achados **na própria revisão do que eu tinha acabado de escrever**,
não pelos testes iniciais: (1) o filtro lateral "escritório" na lista de clientes listava
o nome de todos os escritórios — entregaria a carteira de parceiros da Magic BI pro
contador; (2) um teste de isolamento passava mesmo se a página voltasse vazia, porque só
tinha asserção negativa — agora toda superfície afirma *também* que o dado do próprio
escritório aparece.

Migração validada **contra o banco de dev real** (2 clientes, backfill, 0 órfãos), não só
contra o banco efêmero do pytest, e conferida com dois tenants reais no banco: o contador
do escritório novo não enxergou nenhum cliente do outro, nem na listagem nem no dashboard.

⚠ **Ficam pendentes** (nenhum bloqueia vender pro segundo escritório): `Escritorio` ainda
mora em `apps/painel` (app de apresentação) — o lugar certo seria `apps/tenants`, mas
mover model entre apps com dado em produção pede `SeparateDatabaseAndState` e não valia o
risco agora; e o contador continua vendo o admin completo do próprio escritório (sem
grupos de permissão mais finos por papel dentro do escritório).
→ **As duas resolvidas em 1.9, abaixo.**

---

## 1.9 Pendências da multi-tenancy — ✅ concluída 26/jul/2026

Pedido do usuário: "podemos ajustar as pendências".

### `apps/tenants` — o tenant sai da app de apresentação

`Escritorio`/`MembroEscritorio` moraram em `apps/painel` desde que nasceram como
"branding do dashboard". Com eles virando a raiz do domínio, a direção de dependência
ficou invertida: `apps/clients` (domínio) importando de `apps/painel` (tela). Agora
`apps/tenants` guarda model, escopo (`escopo.py`), permissões (`permissoes.py`), admin e
provisionamento; `apps/painel` fica só com o que o nome diz — dashboard e branding.

**As tabelas foram renomeadas de verdade** (`painel_escritorio` → `tenants_escritorio`),
não deixadas com `db_table` legado: `tenants/0001` usa `SeparateDatabaseAndState` —
estado recriado no `tenants`, removido do `painel` (`painel/0003`), e no banco só um
`ALTER TABLE ... RENAME`. Nenhuma linha é copiada. As FKs não precisam de operação de
banco: no Postgres a constraint referencia a tabela por OID e acompanha o rename sozinha,
então `clients/0005` e `channel_evolution/0003` são só estado.

**Pegadinha que quebrou em banco novo e está anotada na migração:** o grafo permitia o
rename rodar *antes* de `clients/0004` (que faz backfill lendo `painel_escritorio` pelo
nome antigo) e de `channel_evolution/0002` (que cria FK pra ela). O rename precisa
declarar as duas como dependência pra ser a última coisa a tocar a tabela antiga.

Validado no **banco de dev real, com dado**: migrou (1 escritório, 2 clientes, 0 órfãos),
**rolou de volta** (`migrate tenants zero` — tabelas voltaram aos nomes antigos, dados
intactos) e reaplicou. Migração arriscada tem que ser reversível, e essa é.

### Papéis: Grupo do Django (o quê) + bit `responsavel` (quem convida)

Decisão do usuário: **sem papéis fixos no código** (a equipe Magic BI monta as permissões
caso a caso) **e** o responsável pode cadastrar os próprios colegas. As duas juntas têm
uma tensão — sem papéis, quem é "o responsável"? Resolvida separando as duas coisas:

- **`Group` do Django, um por escritório** (`escritorio:<slug>`) decide *o que* a pessoa
  faz. `permissoes.py` só define a **linha de largada** (para o escritório novo não
  nascer com grupo vazio nem com permissão demais) — dali em diante é no admin de Grupos,
  não no código. Fora do baseline de propósito, com o porquê escrito no módulo: `auth.*`
  (quem edita User edita `is_superuser` — escalada), `AplicativoIntegracao` (é da
  plataforma) e `MembroEscritorio` (gate é o bit, não a permissão).
- **`MembroEscritorio.responsavel`** decide *quem administra a equipe*. Não é papel de
  permissão — é uma capacidade só.

Permissão **não** é isolamento, e isso está escrito nos dois módulos: dar permissão demais
amplia o que a pessoa faz dentro do escritório dela, nunca a faz alcançar o vizinho (isso
é `MembroEscritorio` + `escopo.py`).

**Convite de colega sem tocar em `auth.User`:** o responsável não tem (e não pode ter)
acesso ao admin de usuários. O convite é um formulário em `MembroEscritorio` que cria o
usuário já no formato certo — `is_staff`, sem senha utilizável (acesso por Magic Link) e
no grupo do escritório. O `escritorio` não é campo do formulário: vem do vínculo de quem
convida, então mandar outro no POST não adianta.

**13 testes novos em `tests/test_papeis.py`** (150 no total), cada um tentando furar uma
trava: convidar pro escritório do vizinho, puxar um superuser da Magic BI pra dentro,
roubar membro de outro escritório, ver a equipe do vizinho, alcançar `/admin/auth/user/`,
se auto-remover deixando o escritório órfão, e membro comum abrindo a tela de equipe.

Uma trava saiu de um detalhe que só aparece rodando: o `slug` é **readonly** pro contador,
porque ele nomeia o grupo de permissões — trocá-lo desligaria a equipe inteira das
próprias permissões sem nenhum aviso. (E isso forçou `get_prepopulated_fields` a devolver
vazio pra não-superuser: `prepopulated_fields` sobre campo readonly estoura `KeyError`.)

Conferido também fora do pytest, com escritório provisionado de verdade no banco de dev:
responsável entra na tela de equipe (200), leva 403 em `/admin/auth/user/`,
`/admin/auth/group/` e no app da plataforma, e o colega convidado nasce `is_staff=True`,
`is_superuser=False`, sem senha utilizável e no grupo certo.

---

## 1.10 Consultar e cancelar nota + bug do catálogo de tiers — ✅ concluída 26/jul/2026

Saiu de um levantamento de lacunas pedido pelo usuário ("falta qual funcionalidade?").
Conferindo o código contra o roadmap, apareceu **um bug em produção, não uma lacuna**.

### 🔴 Contas a pagar/receber estavam recusadas em produção

`CATALOGO_TIERS` (`apps/governance/tiers.py`) tinha `consultar_contas`, mas o orquestrador
emite `consultar_contas_receber`/`consultar_contas_pagar`. Nome que não bate cai no
**fail-safe Tier 3** e a intenção é recusada como se fosse destrutiva. Resultado real,
reproduzido antes de corrigir:

> **Cliente:** quanto tenho a receber essa semana?
> **Lumen:** Essa operação não está liberada para o seu perfil no momento. 🙏

Duas das cinco consultas do agenteERP estavam mortas. O smoke test de 25/jul só exercitou
"qual meu estoque?", por isso passou batido — e nenhum teste cruzava as duas listas.

Corrigido, e a correção real é o **teste de regressão**
(`test_toda_intencao_que_o_orquestrador_emite_esta_no_catalogo`): cruza `_INTENCOES_VALIDAS`
com `CATALOGO_TIERS`, então intenção nova sem tier quebra o CI, não o WhatsApp do cliente.

### Consultar e cancelar nota (ponto 5 do levantamento)

Estavam no catálogo de tiers desde sempre, mas **inalcançáveis**: o classificador só
conhecia 7 intenções, e nenhuma delas. Cancelamento de NFS-e é obrigação legal — não dava
pra ir a cliente real sem.

- **`consultar_nota` (Tier 0)**: lista as últimas 5 notas com valor, tomador, protocolo,
  DANFSE e marca `❌ CANCELADA` quando for o caso.
- **`cancelar_nota` (Tier 3)**: o cliente **nunca cancela sozinho**, e isso não depende do
  `tier_maximo` — nem perfil Tier 3 cancela direto (tem teste). O pedido nasce em
  `AGUARDANDO_APROVACAO` e vai pra mesma fila onde o contador já aprova emissão. Cancelar
  documento fiscal tem efeito contábil e prazo legal; a decisão é de quem responde por ela.
- **O pedido de cancelamento é uma `Intencao` própria** (`tipo_acao="cancelar_nfse"`,
  `intencao_original` apontando pra nota). Não se reaproveitou o estado `CANCELADO` da
  nota: lá ele significa "o cliente desistiu ANTES de emitir", coisa diferente de "a nota
  existiu e foi cancelada na Sefin". Misturar apagaria a distinção da trilha de auditoria.
  A nota cancelada **continua `CONCLUIDO`** (ela foi emitida de verdade) e ganha
  `cancelada_em` + `protocolo_cancelamento`.
- **Adapter**: `cancelar()` entra no `AdapterBase` como método **concreto** que devolve
  `OPERACAO_NAO_SUPORTADA` — abstrato obrigaria todo adapter de ERP a escrever um `pass`
  sem sentido. `nfse_mock` simula o evento (e a rejeição por justificativa ausente, que é
  a mais comum); `nfse_nacional` fica com o mesmo placeholder honesto do `emitir()` —
  cancelamento real é **evento assinado** (`e101101`), mTLS, e tem prazo legal.
- **Admin**: uma ação só ("Aprovar"), que despacha pelo `tipo_acao`. Duas ações separadas
  obrigariam o contador a saber de antemão o que selecionou, e escolher a errada só daria
  erro. Nova coluna "situação fiscal" — sem ela, nota cancelada e nota normal ficariam
  idênticas na lista, já que ambas são `CONCLUIDO`.

**Pegadinha do classificador:** as três intenções contêm a palavra "nota". A primeira
tentativa usou `in` e quebrou em `"emitir nfs-e"` → `"emiti"` é **prefixo** de `"emitir"`.
Virou regex de palavra inteira, com ordem explícita: cancelar > verbo de emissão >
consulta > default emitir. Assim `"quero emitir minha nota"` é emissão (apesar do "minha",
que é palavra de consulta) e `"minhas notas"` é consulta.

**23 testes novos** (173 no total), incluindo os casos ambíguos do classificador, a recusa
da Sefin sem justificativa (o pedido tem que ir pra `REJEITADO`, não ficar preso em
`EMITINDO`), pedido duplicado, cancelar nota nunca emitida e cancelar duas vezes.

---

## 2. Onda 2 — NFS-e real em homologação — 5 a 8 dias (⚠ replanejada 25/jul/2026)

**Por quê agora:** é o item que prova a Hipótese 1 do MVP e o que mais lacunas técnicas
tem hoje (XML assinado, mTLS, IBS/CBS).

**⚠ Achado técnico novo (25/jul/2026) — reabre uma decisão que parecia fechada.**
Instalei e inspecionei as duas libs (ambas existem de verdade no PyPI, `pip install
nfelib brans-nfe` funciona):

- **`nfelib`** ([akretion/nfelib](https://github.com/akretion/nfelib)) — confirma
  como esperado: bindings Python gerados do XSD oficial (`xsdata`), cobre DPS/NFS-e
  nacional/NF-e/CT-e/MDF-e/BP-e. Sem ressalvas, encurta bastante o trabalho de
  schema.
- **`brans-nfe`** ([badbrans/brans-nfe](https://github.com/badbrans/brans-nfe)) — API
  rica e bem desenhada (`NfseClient`, `Certificado`, `Prestador`, `Tomador`,
  `Servico`, `construir_dps`, `assinar_xml`...), **mas** `Certificado` é construído a
  partir de bytes de um **arquivo .pfx local** (`Certificado.from_pfx_bytes(pfx, senha)`)
  — a lib assume que a chave privada existe em memória no nosso processo. Pior: não é
  só a assinatura do XML (`assinar_xml(xml, certificado)`) que precisa disso —
  **`NfseClient.__init__(certificado, ambiente, ...)` usa o mesmo objeto pro mTLS da
  conexão HTTP com o ADN/Sefin**. Ou seja: a API do governo não exige certificado só
  pra *assinar o documento*, exige certificado **na própria camada de transporte
  (handshake TLS)** — é mais fundamental do que `magicbi-custodia-fiscal.md` havia
  registrado.

**Por que isso importa pra decisão de custódia:** o piloto decidiu PSC (certificado
em nuvem, chave nunca sai da AC) justamente pra nunca ter a chave privada em memória
no nosso processo. Um PSC "tradicional" (só assina hash sob demanda via API REST) **não
resolve mTLS de transporte** — o handshake TLS precisa da chave em tempo real na pilha
de rede, não é um hash isolado que dá pra mandar pra uma API externa assinar. Isso só
funciona se o PSC específico oferecer uma dessas duas coisas:
1. **Engine PKCS#11 / CSP** que expõe o certificado em nuvem como se fosse local pro
   OpenSSL (alguns PSCs — vale confirmar se BirdID/Soluti oferecem isso — mascaram a
   chamada de rede atrás da interface PKCS#11, então o Python/OpenSSL "acha" que está
   assinando local mas na real está chamando a nuvem); ou
2. **Proxy mTLS terminado no lado do PSC** (menos comum, mais raro de existir pronto).

- [ ] **Novo item de pesquisa, antes de qualquer código**: confirmar com BirdID/Soluti
      (e VIDaaS/SafeID como alternativa) se o produto deles oferece um desses dois
      modelos pra mTLS de aplicação servidor-a-servidor (não é o caso de uso comum do
      PSC, que é normalmente assinatura de documento avulso tipo contrato/procuração
      — usar o Bird ID Pro/Hub de Integrações como ponto de partida da pergunta)
- [ ] Se nenhum PSC suportar mTLS remoto: a arquitetura de custódia precisa de uma
      revisão consciente antes da Onda 2 seguir — as opções ficam entre (a) aceitar
      internalizar a chave só no microsserviço isolado de emissão (o desenho já
      previsto pro Sigillum em escala, mas antecipado só pro MVP, com os mesmos
      cuidados de envelope encryption); ou (b) usar o PSC só pra ASSINATURA da DPS
      (que talvez seja negociável separadamente do transporte, se o Sefin aceitar
      alguma forma de client cert "genérico" + assinatura no payload — precisa
      confirmar na doc técnica do ADN, não assumir). **Isto é uma decisão de negócio/
      arquitetura, não só técnica — envolve risco e recomendo validar com o usuário
      antes de escrever o adapter real.**
- [ ] Uso de `nfelib` para os bindings XSD segue de pé independente da decisão acima —
      não tem ressalva, pode ser adotado já
- [ ] `brans-nfe` continua útil como **referência de implementação e para a construção/
      serialização da DPS** (`construir_dps`, `serializar_dps`, `gzip_b64`) mesmo que a
      parte de `Certificado`/mTLS precise de uma camada diferente por cima
- [ ] Reescrever `NfseNacionalAdapter.emitir()` só depois da decisão de custódia acima
      estar clara — não antes, pra não construir em cima de uma premissa errada
- [ ] Tratamento de rejeição real com mensagem clara (grupo `IBSCBS`, NT SE/CGNFS-e
      004/007)
- [ ] Cadastro em `adn.producaorestrita.nfse.gov.br` / `sefin.producaorestrita.nfse.gov.br`
      (Produção Restrita = ambiente de homologação do governo — ver §5 abaixo) — pode
      ser feito em paralelo à pesquisa acima, não depende dela

**Gate da onda:** nota emitida de ponta a ponta em Produção Restrita, partindo de uma
frase no WhatsApp, com o modelo de custódia final escolhido (PSC com mTLS remoto
confirmado, ou microsserviço isolado como fallback).

---

## 3. Onda 3 — Conta Azul real — 4 a 6 dias

- [ ] Fluxo OAuth2 authorization code completo com uma conta de teste/sandbox (confirmar
      se a Conta Azul oferece; se não, conta cobaia da Rotina com consentimento por
      escrito e escopo read-only)
- [ ] Implementar `_formatar()` em `agente_erp/services.py` a partir do payload real
      (hoje devolve o JSON cru)
- [ ] Cache de leitura curto (Redis, TTL minutos) para degradação graciosa
- [ ] Testar "quanto tenho a receber essa semana?" com dado verdadeiro

**Gate da onda:** consulta real respondida em linguagem natural + 1 rascunho de pedido
criado sem retrabalho no Conta Azul.

---

## 4. Onda 4 — Sair do localhost — 3 a 5 dias

Pode rodar **em paralelo** com a Onda 3 (frentes diferentes: infra vs. integração).

- [x] **Login do painel por Magic Link com `django-sesame` — concluído 25/jul/2026.**
      `AUTHENTICATION_BACKENDS` + `sesame.views.LoginView` em `/entrar/`; comando
      `python manage.py enviar_link_contador <email_ou_username>` gera e manda o link
      (reaproveita o mesmo `EMAIL_BACKEND`/`PAINEL_BASE_URL` do Magic Link do wa_id, mas
      é fluxo separado — aqui é login de `auth.User`, não vínculo de sessão de WhatsApp).
      4 testes novos (`tests/test_painel_login.py`); sem tela de "esqueci a senha"
      self-service ainda — onboarding do painel continua manual, como o resto do
      provisionamento no MVP.
- [ ] Deploy em VPS/PaaS região Brasil (Railway/Render, ou já AWS `sa-east-1` se o time
      preferir não trocar de plataforma depois)
- [ ] Variáveis de ambiente e segredos fora do repo (checar que `db.sqlite3`/`.env` não
      estão versionados — sinalizado como problema na varredura de 11/jul)
- [ ] Sentry + logs estruturados mínimos (alertar rejeição fiscal)

**Gate da onda:** mensagem enviada de um celular real (não túnel local) percorre o fluxo
completo; contador acessa o painel remotamente com Magic Link.

---

## 5. Onda 5 — Piloto interno "nossa contabilidade" — 3 a 5 dias de acompanhamento

Este é o teste ágil que o pedido original menciona — validar rápido, dentro de casa,
antes de abrir para a coorte da Rotina:

- [ ] 1–2 pessoas do time + 1 MEI real da base (com consentimento) usando Fiscus e o
      agente Conta Azul por alguns dias
- [ ] Toda emissão real sai em **Produção Restrita** (homologação do governo) até o gate
      de qualidade abaixo passar — nunca pular direto para produção do Emissor Nacional
- [ ] Acompanhar cada interação manualmente (não é hora de automação de QA, é hora de
      olhar toda mensagem)
- [ ] Corrigir no mesmo dia qualquer resposta errada ou rejeição

**Gate da onda:** zero falha silenciosa; nota sai correta em Produção Restrita; sessão
de segurança (Onda 1) não travou ninguém indevidamente.

---

## 6. Ondas seguintes (retomam o `magicbi-mvp-cronograma.md` a partir daqui)

Depois da Onda 5, o caminho volta a ser o já documentado: **Semana 6** (Bling + número
real do WhatsApp + onboarding em ondas de 3), **Semana 7** (piloto completo com a coorte
da Rotina, emissão em produção real), **Semana 8** (medir e decidir go/no-go). Não repito
aqui — ver o documento original para os critérios de sucesso e os riscos.

---

## 7. Checklist de prontidão para homologação

Duas coisas diferentes usam a palavra "homologação" neste projeto — separar para não
confundir:

### 7.1 Homologação do governo (Produção Restrita da NFS-e Nacional)
- [ ] Cadastro feito em `adn.producaorestrita.nfse.gov.br` (ação Dia 1 do MVP)
- [ ] Certificado PSC de teste vinculado e testado isoladamente antes do adapter
- [ ] Ao menos 1 emissão de cada cenário relevante testada (serviço padrão, com desconto,
      com retenção, rejeição proposital para validar o tratamento de erro)
- [ ] Campos `IBSCBS` validados contra a NT vigente (SE/CGNFS-e 004/007)
- [ ] Só migra para produção real depois do gate de qualidade da Onda 5

### 7.2 Homologação interna do produto (pronto para clientes reais fora do time)
- [ ] `SECRET_KEY`/`DEBUG` seguros; nada sensível versionado (`db.sqlite3`, `.env`)
- [ ] Cofre com segredos reais fora de variável de ambiente em texto puro
- [ ] Sessão de segurança (Onda 1) ativa e testada contra clonagem de número
- [ ] Auditoria append-only cobrindo 100% das escritas (fiscal e ERP)
- [ ] CI verde; suíte de testes cobrindo os fluxos críticos (emissão, aprovação,
      idempotência, expiração de sessão)
- [ ] Revisão de acesso ao cofre + pentest leve (já previsto na Fase 4 do cronograma de
      24 semanas — pode ser antecipado de forma leve antes do piloto interno se o tempo
      permitir, não é bloqueante para a Onda 5)
- [ ] DPA + minuta de adesão citando o subprocessador Groq (pendência já registrada em
      `requisitos-dev-piloto-rotina.md` §9.3)
- [ ] Parecer jurídico de responsabilidade civil assinado **antes** de qualquer emissão
      real fora do time (gate de negócio, não técnico — é o Fase 0 do cronograma de 24
      semanas, não pode ser pulado mesmo num piloto interno rápido)

---

## 8. Bibliotecas maduras — resumo consolidado desta rodada

| Necessidade | Biblioteca | Estado |
|---|---|---|
| XML/XSD oficial de NFe/NFS-e nacional | `nfelib` (akretion) | Ativo, gera bindings do XSD oficial |
| Comunicação SEFIN (mTLS, XMLDSig, PSC) | `brans-nfe` | Projeto independente — revisar código antes de produção |
| Magic Link (sessão WhatsApp, domínio próprio) | `PyJWT` | Já usado implicitamente no ecossistema Django, sem dependência pesada |
| Magic Link (login do painel/Grimório) | `django-sesame` | Mantido, ampla adoção, resolve exatamente "login de User" |
| 2FA código avulso | `secrets` (stdlib) | Suficiente para o MVP, sem lib nova |
| 2FA TOTP (se/quando pedir app autenticador) | `pyotp` | RFC 6238, leve |
| Cliente HashiCorp Vault (se a Fase 6 avançar o Sigillum) | `hvac` | Cliente oficial da comunidade, cobre KV/Transit |
| Leitura de metadados de certificado `.pfx` (CNPJ/validade) | `cryptography` (`pkcs12`) | Já é dependência do projeto; usado em vez de `brans-nfe` pra isto — abrir chave privada de cliente é sensível demais pra depender de lib não-oficial só pra extrair 3 campos |

---

## 9. O que NÃO entra ainda (fica fora das ondas 1–5, propositalmente)

Middleware de NF-e produto (Focus NFe/eNotas/NFe.io/Notaas/Nuvem Fiscal — comparar só na
Fase 2/5), cofre próprio Sigillum com HSM, segundo BSP, Hermes/Lumen real (a Opção A com
Pydantic AI já resolve o piloto), Tier 2–3 em qualquer adaptador. Resistir à tentação de
adiantar — cada um desses é semanas de esforço que não destrava o teste rápido que este
documento existe para viabilizar.
