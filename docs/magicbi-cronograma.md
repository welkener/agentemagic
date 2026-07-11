# Magic BI × Rotina Contábil — Cronograma de lançamento

> Consolida o roadmap técnico dos documentos anteriores com as decisões novas: marca
> Magic BI, Hermes/Lumen como comunicador oficial, credenciamento prévio via WhatsApp e
> a matriz de custódia. Horizonte: **24 semanas** a partir do kickoff (referência:
> kickoff em julho/2026 → lançamento comercial ~dezembro/2026, antes da onda de
> obrigatoriedade fiscal de 2027).
>
> Time assumido: 1 backend + 1 full-stack + 1 contador/produto (Rotina). Datas em
> semanas relativas ao kickoff; fases se sobrepõem de propósito.
>
> **Atualização 11/jul/2026** (ver `magicbi-analise-disrupcao.md`): incorporadas as
> funcionalidades disruptivas D1–D7 (white space verificado por pesquisa de mercado) e o
> marco **M4.5 — Onda Simples set/2026**: em 1º/set/2026 as ME/EPP do Simples passam a
> emitir NFS-e exclusivamente pelo Emissor Nacional — a migração forçada é a janela de
> aquisição, e esperar o lançamento de dez/2026 significaria chegar 3 meses atrasado.

---

## Visão geral

```
Sem  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24
F0   ██ ██                                         Marca, jurídico, contas
F1      ██ ██ ██ ██                                Fundação técnica
F2            ██ ██ ██ ██ ██                       Fiscus v1 (NFS-e MEI)
F3                     ██ ██ ██ ██                 Lumen (Hermes) + Conta Azul
F4                              ██ ██ ██ ██        Operar & medir (piloto)
F4.5                      ██ ██ ██ ██ ██           Onda Simples set/2026 (M4.5)
F5                                       ██ ██ ██ ██ ██ ██   Fiscus v2 (NF-e) + lançamento + D1
F6                                                     ██ ██ ██ ██  Escala + proativo (D2/D3/D5/D7)
```

---

## Fase 0 — Marca, jurídico e credenciais (sem. 1–2)

| Entrega | Responsável | Critério de pronto |
|---|---|---|
| Busca INPI + registro de Fiscus/Lumen; domínios | Magic BI | Nomes livres e reservados |
| ~~Contrato de parceria Magic BI × Rotina~~ — **já existente** (formalizado nos testes anteriores); revisar apenas cláusula de responsabilidade fiscal e white-label | Ambos + advogado | Revisão ok |
| Parecer jurídico: responsabilidade por erro do agente; minuta do termo de adesão do cliente final | Advogado | Minuta aprovada |
| Contas: BSP WhatsApp (número da Rotina), AWS `sa-east-1`, API Groq (limites de gasto), app dev Conta Azul, homologação NFS-e Nacional | Magic BI | Todas acessíveis |
| DPA com a Rotina + subprocessadores; DPO indicado | Ambos | Assinado |
| Definição da coorte: 15–25 MEIs serviço + 5–8 ME/EPP Conta Azul da base Rotina | Rotina | Lista nomeada |

**Gate de saída F0:** contrato + parecer assinados. Sem isso, nenhuma escrita real em produção.

## Fase 1 — Fundação técnica (sem. 2–6)

Django 5.2 + Celery/Redis + Postgres; contrato do adaptador; webhook WhatsApp (assinatura
HMAC + idempotência por `message_id`); cofre (Secrets Manager/KMS); auditoria append-only
com hash encadeado; orquestrador Opção A (function-calling tipado com Pydantic AI V2); esqueleto do painel
Grimório (React/Vite); **fluxo de credenciamento v1** (CNPJ → termos → guia de procuração).
Adaptadores já nascem como servidores MCP internos (prepara a Fase 3).

**Gate:** mensagem no WhatsApp percorre webhook → orquestrador → resposta, com auditoria
gravada; credenciamento de um cliente de teste completo em homologação.

## Fase 2 — Fiscus v1: NFS-e MEI (sem. 4–9)

Adaptador NFS-e Nacional em Produção Restrita → produção; validação determinística de
CNAE/alíquota (regras configuráveis, IBS/CBS); máquina de estados de emissão com
contingência/retry; DANFSE de volta no chat; primeira emissão de cada cliente com
aprovação do contador (Grimório); onboarding da coorte MEI em ondas de 5.

**Gate:** ≥ 15 MEIs credenciados; taxa de rejeição fiscal ≈ 0 em 2 semanas de produção.

## Fase 3 — Lumen (Hermes) + Agente Conta Azul (sem. 7–11)

Deploy do Hermes (≥ v0.17, versão fixada, rede privada, MCP) em host próprio com **1
processo-perfil por empresa** (nunca multi-tenant num processo — vazamento de memória
entre tenants, ver `magicbi-hermes-comunicador.md` §6); persona Lumen
+ perfis por empresa; flag de rollout (interno → 3 clientes → coorte); adaptador Conta
Azul (OAuth2, Tier 0–1: consultas + rascunho, cache de leitura); avisos proativos por
template utility; fallback de menu determinístico testado (desligar Hermes em staging e
o canal continua).

**Gate:** 5–8 ME/EPP ativos; Lumen atendendo 100% da coorte; fallback validado.

## Fase 4 — Operar & medir o piloto (sem. 10–14)

Rodar sem feature nova. Métricas: ativação (% da coorte que usou), adoção (% notas via
WhatsApp vs portal), qualidade (rejeição ≈ 0), eficiência (tempo de emissão, horas do
contador economizadas), NPS + disposição a pagar. Ajustes de conversa/persona; hardening
(pentest leve, revisão de acesso ao cofre); definição de preço de atacado com a Rotina.

**Gate (go/no-go do lançamento):** ativação > 60% · rejeição ≈ 0 · NPS ≥ 8 · preço validado.

## Fase 4.5 — Onda Simples (set/2026, sem. ~8–12) *(novo, jul/2026)*

Corre em paralelo às Fases 3–4, aproveitando o que a F2 já entregou. Em **1º/set/2026**
toda ME/EPP do Simples passa a emitir NFS-e exclusivamente pelo Emissor Nacional (web ou
API) — clientes trocando de emissor municipal são clientes decidindo ferramenta nova.
Entregas: oferta "migre emitindo pelo WhatsApp" para as ME/EPP de serviço da base
Rotina (o adaptador NFS-e Nacional da F2 já serve — muda o credenciamento, procuração de
PJ não-MEI); campanha do contador para a base; **copiloto da reforma (D4) v0**: validação
determinística IBS/CBS já existente vira argumento "zero rejeição em 03/08/2026 e
pronto para o destaque de jan/2027", com explicação por CNAE no chat.

**Gate:** ≥ 10 ME/EPP do Simples emitindo pelo Magic BI durante a própria onda de
migração; zero nota rejeitada por IBS/CBS.

## Fase 5 — Fiscus v2 (NF-e produto) + lançamento comercial (sem. 14–19)

Comparativo e contratação do middleware "NF como serviço" (Focus NFe × eNotas × NFe.io —
preço, cobertura MA, SLA); adaptador `nfe_middleware`; credenciamento ramo produto
(middleware ou certificado em nuvem PSC — ver `magicbi-custodia-fiscal.md` §3); piloto
NF-e no Maranhão com 5–10 MEIs de comércio; site/landing Magic BI + material white-label
da Rotina; lançamento comercial à base completa da Rotina (dez/2026), aproveitando a
janela da obrigatoriedade de abr/2026-2027.

**Diferenciais de lançamento (novo, jul/2026):**
- **D1 — Ciclo cobrança → pagamento → nota:** "cobra 800 do João" → link/Pix → webhook
  de pagamento confirma → NFS-e emitida e enviada ao tomador, no mesmo chat. Nenhum
  player brasileiro fecha esse ciclo (Magie só paga; bots MEI só emitem). Tier 1
  determinístico: emissão disparada por pagamento confirmado + dados pré-validados.
  Requer 1 gateway Pix/cobrança (avaliar na sem. 14 junto com o middleware NF-e).
- **D6 — Voice-first** já validado no MVP (áudio→texto na borda) vira recurso de
  marketing do lançamento — paridade com a aposta Zucchetti/BNDES.

**Gate:** NF-e emitida em produção no MA; funil de credenciamento < 48h por cliente;
≥ 1 cliente usando o ciclo cobrança→nota de ponta a ponta.

## Fase 6 — Escala + agente proativo (sem. 19–24)

Segundo ERP (Bling) provando "novo ERP = novo adaptador + perfil"; Tier 2 (alterações com
aprovação 1-clique) se a Fase 4 validou precisão; segundo escritório contábil parceiro
(prova do white-label além da Rotina); segundo BSP como redundância; **estudo do cofre
Sigillum** (custódia própria de A1) — só avança com auditoria externa + seguro cyber +
economia por nota comprovada vs middleware.

**Camada proativa (novo, jul/2026 — é ela que separa o Magic BI dos bots reativos):**
- **D2 — Emissão recorrente por agente:** o agente detecta padrão de emissão e propõe
  ("dia 5: emito as 3 notas de sempre? ✅/✏️/❌"); com mandato explícito do cliente
  (teto de valor + tomadores whitelistados, registrado em auditoria) emite sozinho e o
  contador revisa no Grimório. Nenhum concorrente tem agente proativo de emissão.
- **D3 — Radar de teto + transição assistida:** projeção contínua do teto MEI no chat,
  simulação de desenquadramento (regra dos 20% retroativa) e **handoff para o contador
  da Rotina executar a migração MEI→ME** — o Mei.ai tem o dashboard; o funil com humano
  executando é exclusivo e alimenta os honorários da Rotina (motor do B2B2C).
- **D5 — Fluxo de caixa preditivo:** projeção determinística (recebíveis do ERP −
  despesas recorrentes) com recomendação acionável ("antecipe as cobranças X e Y") que
  se encadeia com D1. Omie só consulta; previsão + ação não existe no mercado.
- **D7 — Memória fiscal do cliente:** perfil por cliente (tomadores, serviços, valores,
  sazonalidade) alimentado por cada interação — encurta conversas ("nota de sempre pro
  João?"), alimenta D2/D3/D5 e cria custo de troca. Trilha contínua a partir daqui.

**Gate F6 (adicional):** ≥ 30% das notas da coorte emitidas por fluxo proativo (D1/D2)
sem retrabalho; primeira migração MEI→ME concluída via D3.

---

## Marcos executivos

| Marco | Semana | Sinal |
|---|---|---|
| M1 — Contrato e marca fechados | 2 | Pode-se construir |
| M2 — Primeira NFS-e em produção via WhatsApp | 8 | Produto existe |
| M3 — Lumen atendendo toda a coorte | 11 | Assistente pessoal real |
| **M4.5 — Onda Simples set/2026** | ~10 | ME/EPP do Simples emitindo pelo Magic BI durante a migração obrigatória ao Emissor Nacional |
| M4 — Go/no-go comercial | 14 | Métricas do piloto |
| M5 — Lançamento Magic BI × Rotina + NF-e MA + ciclo cobrança→nota (D1) | 19 | Receita fora da coorte |
| M6 — 2º ERP + 2º parceiro + camada proativa (D2/D3/D5) | 24 | Modelo replicável e agente que trabalha sozinho |

## Riscos de cronograma (top 5)

| Risco | Efeito | Mitigação |
|---|---|---|
| Homologação NFS-e Nacional instável (histórico 2026) | F2 desliza | Começar cadastro na sem. 1 (F0); contingência/retry desde o 1º dia |
| Procuração eletrônica não cobrir a API (⚠ da custódia) | F2 muda de modelo | Spike de verificação na sem. 3; plano B = certificado em nuvem PSC já mapeado |
| Hermes instável (release semanal) | F3 desliza | Versão fixada + fallback Opção A (Pydantic AI) — F3 nunca bloqueia F4 |
| Parecer jurídico atrasar | Bloqueia escrita em produção | Advogado contratado na sem. 1; F1 não depende dele |
| Coorte da Rotina não engajar | Métricas fracas na F4 | Contador apresenta pessoalmente; onboarding em ondas de 5 com acompanhamento |
| Zucchetti/BNDES lançar áudio→nota em escala (previsto p/ agora) | Perde o argumento voice-first | D6 no MVP (transcrição é barata); diferencial real é D1/D2, que ela não tem |
| Meire (governo, grátis) absorver o básico do MEI | Comprime o tira-dúvidas pago | Não competir no grátis: vender execução + contador humano + gestão ERP |
| Omie abrir o agente WhatsApp para não-clientes | Ataca a mesma PME | Multi-ERP + camada fiscal-consultiva que ERP não faz (D3/D4); acelerar D1 |
| Perder a onda de set/2026 (Simples → Emissor Nacional) | Cliente escolhe outra ferramenta na migração | Fase 4.5 dedicada; F2 entrega o adaptador nacional antes de set |
