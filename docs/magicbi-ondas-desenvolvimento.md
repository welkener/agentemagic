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
| **Canal Evolution API (teste local)** | ✅ Concluído 25/jul/2026 — `apps/channel_evolution/`, SÓ para teste, nunca produção |
| **Não existe ainda** | Painel React; deploy fora do localhost; qualquer chamada real a NFS-e/Conta Azul/Bling em ambiente vivo; assinatura/transmissão real da NFS-e (bloqueada na pendência de mTLS) |

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
