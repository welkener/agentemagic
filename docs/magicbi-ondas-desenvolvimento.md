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
| Adapter NFS-e Nacional **mapeado, não real** | `adapters/nfse_nacional.py` — auth ainda por `Credencial` tipo procuração, que **não serve mais** (spike resolvido: API exige mTLS+certificado) |
| Adapters Conta Azul/Bling **mapeados, não normalizados** | `adapters/conta_azul.py`, `adapters/bling.py`, `adapters/oauth2.py` — falta `_formatar` da resposta real |
| Fila de aprovação do contador | Django admin (`agents/agente_nf/admin.py`) — interino, cobre o requisito funcional |
| Cofre MVP (Fernet) | `credentials/crypto.py` |
| CI (GitHub Actions) | testes a cada push |
| **`apps/security` (Onda 1)** | ✅ Concluído 25/jul/2026 — `SessaoWhatsapp`/`TokenMagicLink`/`Codigo2FA`, gate no orquestrador, 2FA de 3 turnos. 80/80 testes verdes |
| **Não existe ainda** | Painel React; deploy fora do localhost; qualquer chamada real a NFS-e/Conta Azul/Bling em ambiente vivo (Ondas 2–4) |

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

## 2. Onda 2 — NFS-e real em homologação — 5 a 8 dias

**Por quê agora:** é o item que prova a Hipótese 1 do MVP e o que mais lacunas técnicas
tem hoje (XML assinado, mTLS, IBS/CBS). Bibliotecas maduras cortam a maior parte do
esforço:

- [ ] **Instalar `nfelib`** ([akretion/nfelib](https://github.com/akretion/nfelib)) —
      bindings Python gerados do XSD oficial (`xsdata`) para a DPS/NFS-e nacional; evita
      escrever o schema na mão e already cobre os campos `IBSCBS` das NTs de 2025/2026
- [ ] **Instalar `brans-nfe`** ([badbrans/brans-nfe](https://github.com/badbrans/brans-nfe)) —
      comunicação com o SEFIN: mTLS com o certificado ICP-Brasil do PSC, assinatura
      XMLDSig, payload gzip+base64 — é literalmente o rework pendente de
      `apps/adapters/nfse_nacional.py`. Avaliar se dá para usar direto ou só como
      referência de implementação (projeto independente, sem afiliação oficial — revisar
      o código antes de colocar em produção com dado fiscal real)
- [ ] Contratar 1 PSC (BirdID/Soluti é o mais documentado; comparar VIDaaS/SafeID) e
      testar a API de assinatura remota **isoladamente** antes de plugar no adapter
- [ ] Reescrever `NfseNacionalAdapter.emitir()` usando as duas libs acima + credencial
      tipo PSC (não mais "procuração")
- [ ] Tratamento de rejeição real com mensagem clara (grupo `IBSCBS`, NT SE/CGNFS-e
      004/007)
- [ ] Cadastro em `adn.producaorestrita.nfse.gov.br` / `sefin.producaorestrita.nfse.gov.br`
      (Produção Restrita = ambiente de homologação do governo — ver §5 abaixo)

**Gate da onda:** nota emitida de ponta a ponta em Produção Restrita, partindo de uma
frase no WhatsApp, com o certificado PSC de teste.

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

- [ ] Login do painel por Magic Link com **`django-sesame`** (caso de uso é literalmente
      login de `auth.User` — não confundir com o `apps/security` da Onda 1, que é vínculo
      de sessão de WhatsApp, não login de painel)
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

---

## 9. O que NÃO entra ainda (fica fora das ondas 1–5, propositalmente)

Middleware de NF-e produto (Focus NFe/eNotas/NFe.io/Notaas/Nuvem Fiscal — comparar só na
Fase 2/5), cofre próprio Sigillum com HSM, segundo BSP, Hermes/Lumen real (a Opção A com
Pydantic AI já resolve o piloto), Tier 2–3 em qualquer adaptador. Resistir à tentação de
adiantar — cada um desses é semanas de esforço que não destrava o teste rápido que este
documento existe para viabilizar.
