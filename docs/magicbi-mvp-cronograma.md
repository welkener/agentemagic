# Magic BI — Cronograma do MVP (testar e homologar a ideia)

> **Objetivo:** em ~8 semanas, provar com clientes reais da base da Rotina as **duas
> hipóteses** do produto, gastando o mínimo antes do investimento do plano completo:
>
> 1. **Fiscus (fiscal):** emitir NFS-e conversando no WhatsApp funciona, é confiável e
>    o MEI pagaria — 3–5 MEIs emitindo notas reais.
> 2. **Agente ERP:** consultar e rascunhar no ERP que a empresa já usa, pelo WhatsApp,
>    gera valor — 5–8 ME/EPP no **Conta Azul** + 2–3 no **Bling** (segundo ERP, que
>    prova a tese "novo ERP = novo adaptador, sem reescrever o agente").
>
> Relação com `magicbi-cronograma.md`: o MVP comprime as Fases 1–3 do plano de 24
> semanas (a parte Conta Azul da F3 é absorvida aqui; Hermes/Lumen real permanece na F3).
> Se o gate final der **go**, o plano continua da F3 aproveitando 100% do código. Se der
> **no-go**, o prejuízo foi de 8 semanas e infra quase zero.

---

## Escopo — o que entra e o que fica de fora

| ✅ Entra no MVP | ❌ Fica para depois do go |
|---|---|
| Fiscus: emissão de NFS-e (homologação → produção) | NF-e de produto (middleware) |
| Credenciamento simplificado via WhatsApp (CNPJ → termos → **certificado em nuvem/PSC**, não mais só procuração — spike resolvido 12/jul/2026, ver `magicbi-custodia-fiscal.md`) | Cofre próprio de A1 (Sigillum) |
| **Agente ERP — Conta Azul real** (OAuth2; consultas Tier 0 + rascunho Tier 1) | Escrita Tier 2–3 em qualquer ERP |
| **Agente ERP — Bling real** (2º adaptador, prova o padrão) | Tiny/Omie reais (ficam em mock/backlog) |
| Adaptadores **mock** de NFS-e e ERP genérico (+ Tiny/Omie simulados p/ demo) | TOTVS/SAP e ERPs de grande porte |
| Lumen como persona simples — **Opção A com Pydantic AI V2** (function-calling tipado, sem Hermes; ver `magicbi-hermes-comunicador.md` §7) | Framework Hermes real (entra na Fase 3, **1 processo-perfil por cliente** — §6) |
| Contador no loop: aprovação da 1ª emissão de cada cliente | Agente proativo (avisos, resumos semanais) |
| Painel mínimo: fila de aprovação + trilha de auditoria + status das conexões ERP | Grimório completo (perfis, exportações, RBAC fino) |
| **Número de teste da Meta** (gratuito, 5 destinatários) **+ upgrade p/ número real na sem. 6** (coorte total ~10–16 pessoas) | BSP com volume/templates em escala |
| Auditoria append-only + idempotência (inegociáveis mesmo no MVP) | Status page, segundo BSP, DR completo |
| Docker local + 1 VPS/PaaS barato para o piloto | AWS sa-east-1 completa (após o go) |

**O que NÃO se corta nem no MVP:** validação determinística de CNAE/alíquota, idempotência
por `message_id`, auditoria de toda emissão/escrita, tiers (ERP travado em 0–1), material
sensível fora do chat. São os guardrails que protegem o CNPJ dos clientes de teste.

**Atenção ao canal:** com a coorte ERP, o total de usuários passa de 5 — o número de
teste da Meta não cobre todo mundo. Plano: semanas 1–5 no número de teste (equipe +
MEIs); na semana 6 registrar um número real no WhatsApp Business Platform (Meta direto,
sem BSP ainda — tráfego iniciado pelo cliente é gratuito) para a coorte ERP.

---

## As 8 semanas

```
Sem   1        2        3        4        5        6        7        8
      ████ Setup + fundação (+ apps dev CA/Bling)
               ████ Fluxo mock ponta a ponta (fiscal + ERP)
                        ████ NFS-e real (homolog.) + credenciamento
                                 ████ Conta Azul real (OAuth2, Tier 0–1)
                                          ████ Painel + teste interno
                                                   ████ Bling + número real + onboarding ERP
                                                            ████ Piloto completo
                                                                     ████ Medir + go/no-go
```

### Semana 1 — Setup e fundação
**Dias 1–2 (dá pra fazer hoje):**
- [ ] Scaffold do projeto: Django 5.2 + DRF + Celery + docker-compose (Postgres/Redis) + `pydantic-ai`
- [ ] Instalar: Docker Desktop, Python 3.13, Node 20+ (máquina Windows local)
- [ ] Chave da API Groq (console.groq.com/keys) com limite de gasto
- [ ] App no Meta for Developers + número de teste WhatsApp + túnel local (cloudflared)
- [ ] **Registrar app de desenvolvedor no Conta Azul (OAuth2) e no Bling** — aprovação
      tem prazo de terceiros; disparar no dia 1 junto com a homologação NFS-e

**Dias 3–5:**
- [ ] Webhook WhatsApp: handshake, assinatura HMAC, ack rápido, fila Celery
- [ ] Idempotência por `message_id` + modelo `Auditoria` append-only
- [ ] Contrato `AdapterBase` + **adaptador NFS-e mock** + **adaptador ERP mock**
      (pedidos/estoque/financeiro fake, com dados realistas de uma empresa exemplo)
- [ ] **Iniciar cadastro na homologação da NFS-e Nacional (Produção Restrita)** — URLs
      reais confirmadas: `adn.producaorestrita.nfse.gov.br` / `sefin.producaorestrita.nfse.gov.br`
      (ver `magicbi-custodia-fiscal.md`)
- [x] **Spike da procuração eletrônica — RESOLVIDO (12/jul/2026): não cobre a API.**
      Confirmado por fonte oficial (FENACON oficiou a Receita em 12/06/2026 pedindo
      exatamente essa lacuna — "em desenvolvimento pelo Serpro, sem previsão"). A API do
      ADN/Sefin exige **mTLS com certificado ICP-Brasil do prestador em toda chamada**.
      **Plano B agora é o plano único**: certificado em nuvem (PSC) desde o piloto — não
      é mais uma decisão da Semana 3, é pré-requisito. **Nova ação Dias 1–2**: contratar
      um PSC (BirdID/Soluti, VIDaaS/Valid ou SafeID — comparar preço e API de assinatura
      remota) junto com as outras contas

**Gate S1:** mensagem no WhatsApp de teste → Django local → resposta do Lumen, com auditoria gravada; apps Conta Azul/Bling solicitados.

### Semana 2 — Fluxo ponta a ponta no mock (fiscal E ERP)
- [ ] Orquestrador Opção A com **Pydantic AI V2** sobre **Groq**: agentes tipados
      (`llama-3.1-8b-instant` roteia, `openai/gpt-oss-120b` monta nota/valida),
      extração de campos da nota como schema Pydantic com retry de validação
- [ ] Roteamento de intenção entre os dois agentes: "emite nota…" → Fiscus;
      "quanto tenho a receber?" / "estoque do produto X" / "rascunha pedido…" → agenteERP
- [ ] Máquina de estados fiscal: RECEBIDO → VALIDANDO → AGUARDANDO_APROVACAO → EMITINDO → CONCLUIDO
- [ ] Validação determinística de CNAE/valor/tomador (regras em config, não no prompt)
- [ ] Confirmação Tier 1 com botões ("✅ Emitir / ✏️ Corrigir / ❌ Cancelar") — nota e rascunho
- [ ] DANFSE fake (PDF) no chat; consultas ERP mock respondidas em linguagem natural
- [ ] Motor de tiers ativo: qualquer intenção Tier 2–3 recusada com explicação educada
- [x] **Áudio → texto (D6)** — implementado 12/jul/2026: webhook detecta `type=audio`,
      baixa a mídia da Graph API e transcreve com **Whisper da própria Groq**
      (`whisper-large-v3-turbo`, mesma chave/conta do roteamento — sem outro fornecedor),
      seguindo o pipeline de texto normal depois. Sem token/chave configurados, degrada
      pedindo pro cliente escrever (nunca trava nem inventa conteúdo). Paridade com o
      áudio→nota da Zucchetti/BNDES (ver `magicbi-analise-disrupcao.md` §4).
      `apps/channel_whatsapp/transcricao.py` + `services.baixar_midia`.

**Gate S2:** no mock — "emite nota de 500 pro João" vira nota com PDF em < 2 min E
"como tá meu contas a receber?" volta resumo correto em < 10 s, com os tiers bloqueando o que deve.

### Semana 3 — NFS-e real em homologação + credenciamento
- [ ] Adaptador NFS-e Nacional real apontando para Produção Restrita — payload é
      **híbrido**: chamada REST em JSON, mas a DPS/NFS-e em si é **XML assinado
      (XMLDSig), gzip + base64** dentro do JSON — não é um dict simples como o mock
      (`apps/adapters/nfse_nacional.py` precisa de rework: geração de XML conforme XSD
      oficial + assinatura via PSC, não Bearer token)
- [ ] Tratamento de rejeição real (campos IBS/CBS — grupo `IBSCBS` na DPS, NT
      SE/CGNFS-e 004/007, XSDs de out/2025 e fev/2026) com mensagem clara + retry
- [ ] Credenciamento v0 no chat: CNPJ → consulta pública → confirmação de dados →
      link único para termos → **vínculo do certificado em nuvem (PSC) via app da AC**
      (não mais só procuração — ela vira só o consentimento LGPD/adesão)
- [x] Spike resolvido na Semana 1: certificado em nuvem confirmado como caminho único
      para a API (não "decisão", já é fato)

**Gate S3:** nota emitida na homologação do governo, ponta a ponta, partindo de uma frase no WhatsApp.

### Semana 4 — Conta Azul real (o agente ERP sai do mock)
- [ ] Fluxo OAuth2 authorization code por cliente; refresh token criptografado; renovação automática
- [ ] Mapear endpoints reais na interface única: consultas de vendas/pedidos, estoque,
      contas a pagar/receber, fluxo de caixa (Tier 0) + criar rascunho de pedido (Tier 1)
- [ ] Cache de leitura curto (degradação graciosa se a API cair)
- [ ] Tradução de erros p/ catálogo interno (`AUTH_EXPIRADA`, `RATE_LIMIT`, `INDISPONIVEL`)
- [ ] Testar com conta Conta Azul de teste/desenvolvedor (⚠ confirmar sandbox; se não
      houver, usar a conta de um cliente cobaia da Rotina com consentimento por escrito)

**Gate S4:** conectar uma empresa real via OAuth e responder "quanto tenho a receber essa semana?" com dado verdadeiro do Conta Azul.

### Semana 5 — Painel mínimo + teste interno ("friends & family")
- [x] **Fila de aprovação + trilha de auditoria + status das conexões — adiantado
      (12/jul/2026) via Django admin**, antes do React: ações "Aprovar e emitir" /
      "Rejeitar" na `Intencao` chamam o mesmo `agente_nf/services.py` do fluxo por
      WhatsApp (uma só máquina de estados, dois canais de confirmação); `Auditoria` e
      `Credencial`/`AplicativoIntegracao` (status das conexões) já visíveis lá.
      **Falta ainda**: login por magic link (hoje é usuário/senha do Django) e a versão
      React propriamente dita — o admin é interino, não o Grimório final.
- [ ] Painel React mínimo (Grimório completo entra depois do go, isto é só a versão
      "bonita" do que o admin já faz — avaliar se compensa antes do go, dado que o
      admin já cobre o requisito funcional)
- [ ] Login por magic link para o contador da Rotina
- [ ] Deploy em VPS/PaaS barato (região Brasil) para sair do localhost
- [ ] Teste interno: você + 1–2 pessoas da Rotina usando fiscal E ERP por 3 dias
- [ ] Rotina fecha as coortes: 3–5 MEIs (≥ 4 notas/mês) + 5–8 ME/EPP Conta Azul + 2–3 Bling

**Gate S5:** contador aprova emissão pelo painel sem ajuda; zero falha silenciosa no teste interno.

### Semana 6 — Bling (2º ERP) + número real + onboarding ERP
- [ ] **Adaptador Bling** implementando o mesmo contrato — meta explícita: **≤ 1 semana
      de esforço e zero mudança no núcleo** (é a prova da arquitetura de adaptadores)
- [ ] Tiny/Omie permanecem como variação do mock (demo comercial), reais só pós-go
- [ ] Registrar número real no WhatsApp Business Platform (Meta direto, sem BSP)
- [ ] Onboarding da coorte ERP: OAuth Conta Azul/Bling guiado pelo Lumen no chat
      (link seguro), em ondas de 3

**Gate S6:** cliente Bling e cliente Conta Azul recebem a mesma experiência; conexão de um ERP novo não tocou em `apps/core`.

### Semana 7 — Piloto completo (fiscal + ERP, tudo real)
- [ ] MEIs: migrar emissão para **produção** do Emissor Nacional (notas reais)
- [ ] Contador valida a primeira nota de cada MEI antes de liberar emissão direta
- [ ] Coorte ERP usa consultas + rascunho no dia a dia real
- [ ] Acompanhamento diário: cada rejeição/resposta errada vira correção no mesmo dia
- [ ] Medir custo real de IA por interação (tokens) nos dois agentes

**Gate S7:** cada MEI emitiu ≥ 1 nota real válida; cada empresa ERP fez ≥ 5 consultas reais e ≥ 1 rascunho.

### Semana 8 — Medir e decidir
- [ ] Entrevistas: MEIs, empresas ERP e contador (roteiros separados por perfil)
- [ ] **Sondar os diferenciais D1–D5 nas entrevistas** (custo zero de validar agora):
      "pagaria mais se a nota saísse sozinha quando o Pix cair?" (D1), "e se o agente
      emitisse as notas recorrentes sem você pedir?" (D2), "quer aviso de teto com o
      contador cuidando da migração?" (D3) — ver `magicbi-analise-disrupcao.md` §4
- [ ] Consolidar métricas (tabela abaixo) e custo por cliente ativo
- [ ] Decisão **go/no-go** para o plano de 24 semanas (da Fase 3 em diante)
- [ ] Se go: contratar BSP, abrir AWS sa-east-1, iniciar Hermes/Lumen real (≥ v0.17,
      1 processo-perfil por cliente co-locados — `magicbi-hermes-comunicador.md` §6), NF-e produto
- [ ] ⏰ **Timing**: o go/no-go coincide com **1º/set/2026** (Simples inteiro migrando
      ao Emissor Nacional). Se "go", disparar a **Fase 4.5 — Onda Simples** imediatamente
      (ver `magicbi-cronograma.md`) — não esperar o lançamento de dez/2026

---

## Critérios de homologação da ideia (o que o MVP precisa provar)

### Hipótese 1 — Fiscus (fiscal)
| Métrica | Meta para "go" |
|---|---|
| Notas emitidas com sucesso / tentativas | ≥ 95%, rejeição final ≈ 0 |
| Tempo da frase à nota emitida | < 2 min (vs ~10 min no portal) |
| Credenciamento por MEI | < 48h, sem visita presencial |
| MEIs que passam a preferir o WhatsApp ao portal | ≥ 4 de 5 |
| "Pagaria R$ 30–50/mês?" | ≥ 3 de 5 sim |

### Hipótese 2 — Agente ERP
| Métrica | Meta para "go" |
|---|---|
| Consultas respondidas corretamente (amostra auditada) | ≥ 90% |
| Tempo de resposta de consulta | < 10 s |
| Rascunhos criados corretos (sem retrabalho no ERP) | ≥ 90% |
| Uso recorrente (empresas que voltam ≥ 3×/semana na sem. 7) | ≥ 5 de 8 |
| "Pagaria implementação + mensalidade?" | ≥ 4 de 8 sim |
| **2º ERP (Bling) sem tocar no núcleo** | Provado (esforço ≤ 1 semana) |

### Transversal
| Métrica | Meta |
|---|---|
| Contador da Rotina oferece à base toda? | Sim, sem ressalvas graves |
| Custo de IA + infra por cliente ativo/mês | < R$ 10 |
| Incidentes de segurança / vazamento | Zero |

## Custo do MVP (ordem de grandeza)

| Item | Custo |
|---|---|
| WhatsApp (número teste → número real Meta direto; tráfego iniciado pelo cliente) | ~R$ 0–50/mês |
| Groq API (2 agentes, coorte ~15, com caching) | ~R$ 15–50/mês |
| VPS/PaaS região Brasil | ~R$ 50–150/mês |
| NFS-e Nacional + procuração | R$ 0 |
| Conta Azul/Bling (apps de desenvolvedor) | R$ 0 (⚠ confirmar conta de teste CA) |
| **Total** | **~R$ 150–500/mês** — o investimento real é o tempo de desenvolvimento |

## Riscos específicos do MVP

| Risco | Mitigação |
|---|---|
| Aprovação do app Conta Azul/Bling demorar | Solicitar no dia 1; semanas 2–3 rodam no mock; se atrasar muito, inverter S4↔S6 (Bling primeiro) |
| Conta Azul sem sandbox | Cliente cobaia da Rotina com consentimento por escrito + escopo read-only até S5 |
| Cadastro da homologação NFS-e demorar | Iniciar no dia 1; mock cobre as 2 primeiras semanas |
| ~~Procuração não cobrir a API~~ — **confirmado que não cobre (12/jul/2026)** | Não é mais risco, é fato: certificado em nuvem PSC é o caminho desde o piloto — contratar PSC vira ação do Dia 1, não plano B condicional |
| Onboarding com certificado (PSC) ter mais atrito que procuração pura | Testar o fluxo de vínculo do certificado com 1 MEI cobaia antes de abrir pra coorte toda; medir tempo real vs. meta de <48h (métrica já existente no doc) |
| Coorte total estourar o número de teste da Meta | Upgrade planejado p/ número real na S6 (Meta direto, sem BSP) |
| Duas frentes (fiscal + ERP) dispersarem o dev solo | Semanas 3–4 são sequenciais de propósito (fiscal primeiro, depois ERP); nunca as duas em paralelo na mesma semana |
| Escopo inchar ("já que estamos, adiciona X") | Tudo que não prova as 2 hipóteses vai para o backlog da Fase 3 |
