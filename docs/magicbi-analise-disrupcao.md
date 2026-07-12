# Magic BI — Análise de disrupção (varredura de 11/jul/2026)

> **Atualização mesma data:** as duas lacunas mais críticas do §1 (fluxo fiscal
> desconectado; nenhuma chamada real a LLM) foram fechadas — orquestrador agora liga
> roteamento + extração de campos (Groq/Pydantic AI, com fallback determinístico) à
> máquina de estados (`Intencao`) e ao adapter NFS-e mock, com idempotência por
> `message_id`. Provedor de IA trocado de Anthropic para **Groq** (custo ~1 ordem de
> grandeza menor — ver `magicbi-hermes-comunicador.md` §7). §1 abaixo permanece como
> registro do estado em que a varredura foi feita.

> Consolida três varreduras feitas em 11/jul/2026: (1) estado real da implementação do
> `backend/`, (2) pesquisa de mercado atualizada dos concorrentes, (3) cruzamento com os
> cronogramas. Conclui com o **white space** — funcionalidades que nenhum player
> brasileiro oferece — e as mudanças aplicadas em `magicbi-cronograma.md` e
> `magicbi-mvp-cronograma.md`.

---

## 1. Estado da implementação (o que existe de verdade)

O backend Django corresponde ao fim da **Semana 1–2 do MVP**, com fundação sólida porém
**sem o produto-fim funcionando ponta a ponta**.

**Implementado e testado (~35 testes):**
- Webhook WhatsApp: handshake, HMAC-SHA256, ack rápido, idempotência por `message_id`,
  fila Celery (`apps/channel_whatsapp/`).
- Auditoria append-only com hash encadeado + verificação de cadeia (`apps/audit/`).
- Motor de tiers 0–3 fail-safe (`apps/governance/tiers.py`).
- Máquina de estados fiscal RECEBIDO→…→CONCLUIDO com auditoria de transições
  (`apps/agents/agente_nf/models.py`).
- Adapters **mock** de NFS-e e ERP sob contrato `AdapterBase` (`apps/adapters/`).

**Lacunas críticas (em ordem de urgência):**
1. **Fluxo fiscal desconectado** — orquestrador, máquina de estados e adapter NFS-e não
   se conversam; "emite nota" devolve texto placeholder (`apps/core/orchestrator.py`).
2. **Nenhuma chamada real a LLM** — roteamento por palavra-chave; `anthropic` e
   `pydantic-ai` estão no requirements mas nunca são importados em runtime.
3. Sem adapter real (NFS-e Nacional, Conta Azul, Bling); sem cofre de credenciais em
   runtime; sem aprovação humana (estado `AGUARDANDO_APROVACAO` órfão); sem
   templates/mídia/áudio no WhatsApp; sem painel; `SECRET_KEY`/`DEBUG` inseguros;
   `db.sqlite3` versionado; **repo git sem nenhum commit**.

**Veredito:** a arquitetura respeita a regra de ouro (LLM propõe, núcleo decide) e os
guardrails inegociáveis já existem. O gargalo não é qualidade, é **conexão das peças**
(Semana 2 do MVP) — nada disso muda a estratégia, mas atrasa o relógio regulatório
abaixo.

---

## 2. Panorama competitivo (pesquisado em 11/jul/2026)

### Emissores MEI via WhatsApp (concorrentes diretos)
| Player | Preço | O que tem | Fonte |
|---|---|---|---|
| ZapCont ("Seu João") | R$ 29,79/mês | Nota, DAS, organização; vende "sem contador" | zapcont.com |
| ZAP MEI | R$ 49,90/mês | Nota, DAS, burocracia 24/7 | meucontadoronline.com.br |
| Nexmei | R$ 34,90+/mês | Nota (Portal Nacional), DAS, certidões | nexmei.com.br |
| **Mei.ai** | grátis–R$ 99 | O mais completo: NFS-e "30 s", DAS, **monitor de teto R$ 81k**, **simulador MEI vs Simples pós-reforma** (como dashboard) | meiai.com.br |
| Notaí, Zapnota, MEI Chat, Meitor, MaisMei | R$ 20–50 | Nicho pulverizado; MaisMei com reclamações de cobrança indevida no Reclame Aqui | — |

"Manda a Nota": nenhuma informação confiável encontrada em jul/2026 (site/preço/notícia).

### Ameaças novas (2026)
- **Zucchetti + BNDES** (aporte R$ 35 mi): nota fiscal **por áudio** no WhatsApp — IA
  transcreve, valida, autoriza na Sefaz, devolve PDF/XML. Lançamento previsto para
  agora (fim do 1S2026). *Fonte: CartaCapital.*
- **Governo federal**: app **Meu MEI Digital** + assistente IA **Meire**
  (jan/2026, Sebrae/Receita/Serpro) — DAS, notas, regularização e dúvidas, **grátis**.
  Comprime o valor do "tira-dúvidas MEI" pago. *Fontes: gov.br/secom, B3.*
- **Omie** (jun/2025): **ERP inteiro operável via WhatsApp, texto e voz** — saldo,
  pedidos, fluxo de caixa, estoque com ordem de compra automática. Grátis para a base.
  A tese "incumbente adiciona canal antes de o entrante adicionar ERP" **se confirmou
  no caso Omie** — mas só para quem já é cliente Omie. *Fonte: startups.com.br.*
- **Conta Azul**: **Conta AI Captura** (ago/2025) — documentos por WhatsApp/e-mail/DDA
  lidos por IA que sugere lançamentos. É captura, ainda não agente transacional.
- **Itaú ia.i**: saldo e Pix por WhatsApp em rollout para ~2 mi de clientes; abertura de
  conta PJ pelo WhatsApp em <5 min (mai/2026). **Magie**: US$ 5 mi captados, vertical
  B2B de pagamentos conversacionais em rollout 2026. Nenhum dos dois toca emissão fiscal.
- **Nibo** (foco contador): Radar e-CAC, WhatsApp integrado com IA, leitura de
  notas/boletos. **Bling/Tiny**: nada relevante de agente WhatsApp encontrado.
  **Contabilizei**: US$ 125 mi (Warburg); CEO declarou publicamente que "IA não entrega
  vantagem competitiva" — aposta em produtos financeiros, não em agentes. Espaço aberto.

### Relógio regulatório 2026–2027 (o fator que ninguém pode mover)
- **1º/set/2026**: ME/EPP do Simples obrigadas a emitir NFS-e **exclusivamente pelo
  Emissor Nacional** (web ou API) — migração forçada de emissores municipais = **janela
  única de aquisição**; quem captura a troca de ferramenta fica com o cliente.
- **03/08/2026**: campos IBS/CBS passam a **rejeitar** NF-e sem preenchimento correto.
- **jan/2027**: destaque CBS/IBS passa a valer também para Simples/MEI.
- Dado Qive (104 mi de notas, 1T2026): conformidade CBS/IBS de **16,3% em serviços**
  (vs 78,5% em mercadorias) — o setor de serviços está massivamente atrasado.
- Teto MEI congelado em R$ 81 mil desde 2019 + inflação = safra recorde de MEIs
  estourando o teto → demanda por radar + transição assistida para ME.

### Referências internacionais
- **Pilot** (EUA, fev/2026): contador IA totalmente autônomo (fechamento sem humano).
- **Puzzle** (EUA): automatiza ~98% e fez **parceria com escritórios contábeis em vez de
  substituí-los** — validação internacional exata do modelo Magic BI.
- **KhataBuddy** (Índia): fatura GST por **texto ou áudio no WhatsApp + lembretes de
  cobrança + relatórios no mesmo chat** — valida o combo emissão+cobrança+gestão.
- **Bench** (Canadá): colapsou em 2024 — alerta contra bookkeeping barato sem unit
  economics.

---

## 3. Leitura estratégica

O tabuleiro de jul/2026 tem três blocos, e todos deixam o mesmo vão aberto:

| Bloco | O que faz | O que NÃO faz |
|---|---|---|
| Bots MEI R$ 20–50 (ZapCont, Mei.ai…) | Nota + DAS no chat | Não conectam ERP; vendem "sem contador"; sem proatividade real |
| Incumbentes (Omie, Conta Azul, Itaú, Magie) | Canal conversacional para a **própria base** | Não adquirem quem está fora; não unem fiscal + gestão + humano |
| Governo (Meire) | Básico grátis | Sem execução autônoma, sem contador, sem gestão |

**O espaço defensável do Magic BI** é o cliente *entre* mundos — MEI/micro sem sistema e
PME cujo ERP não conversa com o fiscal — com **contador humano no loop como diferencial
de confiança** que nem o governo, nem os bots de R$ 30, nem os bancos oferecem. A
Contabilizei desdenhando IA e a Puzzle provando o modelo "agente + contador" nos EUA
reforçam a tese.

**Correção de rota mais importante:** o lançamento comercial em dez/2026 chega **três
meses depois** da onda de set/2026 (Simples inteiro trocando de emissor). O cronograma
foi ajustado para capturar essa onda (ver §5).

---

## 4. White space — funcionalidades que ninguém no Brasil oferece

Numeradas por prioridade. Cada uma indica o guardrail de tier correspondente — a
disrupção aqui é **agência com governança**, não feature de dashboard.

### D1. Ciclo fechado cobrança → pagamento → nota ("Pix caiu, nota emitida")
O cliente diz "cobra 800 do João pelo site". O agente gera cobrança (link/Pix), detecta
o pagamento via webhook, **emite a NFS-e automaticamente e envia ao tomador** — tudo no
mesmo chat. Magie faz só o pagamento; os bots MEI fazem só a nota; KhataBuddy prova o
combo na Índia; **ninguém fecha o ciclo no Brasil**. Tier 1 (emissão pós-pagamento é
regra determinística: pagamento confirmado + dados validados). É também a resposta à
adjacência da Magie: chegar ao pagamento pelo fiscal antes que ela chegue ao fiscal
pelo pagamento.

### D2. Emissão proativa e recorrente por agente
O agente aprende o padrão ("todo dia 5 você emite 1.200 para a Acme") e **propõe** —
"chegou dia 5, emito as 3 notas de sempre? ✅/✏️/❌" — ou emite sozinho com regra
pré-aprovada pelo cliente e revisão do contador. Emissão recorrente existe em SaaS de
formulário (Spedy, Vimbo); **como agente conversacional proativo, não existe**. É a
inversão que nenhum bot reativo de R$ 30 faz: o produto trabalha quando o cliente não
está olhando. Tier 1 com mandato explícito registrado em auditoria (padrão "standing
order": teto de valor + tomadores whitelistados).

### D3. Radar de teto + transição assistida MEI→ME com contador executando
O Mei.ai tem monitor de teto e simulador **como dashboard**. O white space é a versão
**agente**: acompanhamento contínuo no chat ("no ritmo atual você estoura o teto em
outubro; ultrapassando 20% o desenquadramento é retroativo — simulei seu custo como ME
no Simples: R$ X/mês"), e o **handoff para o contador da Rotina executar a migração**.
Ninguém tem o último passo — e ele é exatamente o funil natural MEI→ME→honorários da
Rotina, o motor econômico do B2B2C. Tier 0 (leitura) + humano (migração).

### D4. Copiloto da reforma tributária no chat (o produto com prazo)
Serviços estão com 16,3% de conformidade CBS/IBS; campos viram rejeição na NF-e em
03/08/2026 e o destaque chega ao Simples em jan/2027. O Fiscus valida IBS/CBS
deterministicamente **antes** de emitir (zero rejeição, já no roadmap) e explica o
impacto por CNAE em linguagem de WhatsApp ("sua alíquota efetiva muda assim em 2027…"),
com material assinado pelo contador. A Qive fez a calculadora estática; **agente
conversacional que garante conformidade na emissão, não existe**. Custo marginal baixo:
a validação determinística já está no plano — o copiloto é a camada de comunicação.

### D5. Fluxo de caixa preditivo conversacional
Omie **consulta** fluxo de caixa via WhatsApp; **previsão com recomendação acionável**
("com os recebíveis do Conta Azul e seu padrão de despesas, o caixa fica negativo em
~15/ago; antecipar as cobranças X e Y cobre o buraco — quer que eu cobre?") não existe
em nenhum player. Combina Tier 0 (projeção) com D1 (agir sobre a recomendação). Começa
com heurística determinística (recebíveis confirmados − despesas recorrentes), não
precisa de ML no dia 1.

### D6. Voice-first fiscal (paridade urgente, não diferencial) — ✅ implementado 12/jul/2026
Áudio→nota é a aposta da Zucchetti/BNDES lançando **agora**. Para o público MEI
(WhatsApp = áudio), aceitar áudio é higiene de produto em 2026. Custo baixo: transcrição
(API) na borda → o resto do pipeline já é texto. Entra no MVP como item barato;
reivindicar como diferencial só se a Zucchetti atrasar.

### D7. Memória fiscal do cliente (o fosso de longo prazo)
Cada emissão/tomador/correção alimenta um perfil por cliente (tomadores frequentes,
serviços, valores típicos, sazonalidade) que torna cada interação mais curta ("nota de
sempre pro João?" em vez de 5 perguntas) e alimenta D2/D3/D5. Nenhum bot MEI acumula
esse ativo — e é ele que torna a troca de fornecedor cara. Base já existe: auditoria
append-only + `Perfil`.

**O pacote D1–D7 forma um degrau que nenhum bloco alcança inteiro:** os bots de R$ 30
não têm ERP nem contador; a Omie não adquire fora da base; a Magie não tem fiscal; o
governo não executa. E cada item roda sobre os guardrails já construídos (tiers,
auditoria, núcleo determinístico) — a governança vira o argumento que permite vender
*agência* sem vender risco.

---

## 5. Mudanças aplicadas nos cronogramas

**`magicbi-mvp-cronograma.md`** (escopo preservado — MVP continua provando as 2 hipóteses):
- Áudio→texto (D6) adicionado como item barato na Semana 2 (transcrição na borda).
- Semana 8: roteiro de entrevistas passa a sondar disposição a pagar pelos
  diferenciais D1–D5 (validação de demanda a custo zero).
- Nota de timing: go/no-go da Semana 8 coincide com a onda de set/2026 — se "go",
  a oferta para ME/EPP do Simples não espera dez/2026.

**`magicbi-cronograma.md`** (plano de 24 semanas):
- Novo marco **M4.5 — Onda Simples set/2026**: oferta de emissão pelo Emissor Nacional
  para ME/EPP da base Rotina já na migração obrigatória (antecipada da F5).
- F5 ganha D1 (ciclo cobrança→nota) e D4 (copiloto da reforma) como diferenciais de
  lançamento; F6 ganha D2/D3/D5 (proatividade e preditivo) e D7 como trilha contínua.
- Riscos atualizados: Zucchetti voice-first, Meire (governo), Omie na base instalada.

## 6. Riscos novos a monitorar

| Risco | Sinal | Resposta |
|---|---|---|
| Zucchetti lança áudio→nota em escala | Notícias BNDES/Zucchetti 2S2026 | D6 no MVP; diferencial migra para D1/D2 (ciclo e proatividade, que ela não tem) |
| Meire (governo) absorve o básico MEI | Adoção do Meu MEI Digital | Não competir no "tira-dúvidas grátis"; vender execução + contador + gestão |
| Omie abre o agente para não-clientes | Anúncio Omie 2S2026 | Acelerar D1/D3 (fiscal + transição, que ERP não faz) e multi-ERP (não prender a um) |
| Magie entra em emissão fiscal | Vertical B2B da Magie ganhando features fiscais | D1 primeiro: chegar ao pagamento pelo fiscal antes do inverso |
| Categoria contaminada por golpes "MEI no WhatsApp" | Reclame Aqui | Marca do contador (Rotina) na conversa; número verificado; transparência de preço |
