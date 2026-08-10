# Cronograma — dois produtos sobre uma plataforma (a partir de 08/ago/2026)

Executa as decisões de [`hermes-contabil-decisoes.md`](hermes-contabil-decisoes.md),
com base no estado real do código levantado em
[`hermes-contabil-ajuste.md`](hermes-contabil-ajuste.md).

**Não é um plano do zero.** É o que falta para o backend que já roda sustentar os
**dois produtos** (DEC-02):

- **Fiscus/Lumen** — a empresa final (MEI e micro) no WhatsApp: emitir nota,
  radar de teto, consultar o ERP dela. **Funciona ponta a ponta hoje.**
- **Hermes Contábil** — o escritório contábil como tenant, atendendo centenas ou
  milhares de empresas: guias, obrigações, documentos, folha, integração com o
  sistema contábil. **É o que este cronograma constrói.**

Os itens ✅ já estão escritos e testados. O cronograma existe para o resto.

Relação com os cronogramas anteriores: substitui `magicbi-mvp-cronograma.md`
(encerrado, papel cumprido) e reenquadra `magicbi-cronograma.md`, cujas fases
F5/F6 seguem válidas — ver §6.

---

## 1. Capacidade — leia antes do calendário

Os cronogramas anteriores assumiam **1 backend + 1 full-stack + 1 contador/produto**.
O calendário principal mantém essa premissa, com sprints de 2 semanas. Se a
execução for de **um dev só** — que é o cenário real do que foi entregue até
aqui —, o mesmo escopo cabe em sprints de 3 semanas:

| Sprint | Entrega | Time de 3 (2 sem) | Dev solo (3 sem) | Real |
|---|---|---|---|---|
| S1 | Isolamento de 3 níveis | 10–21/ago | 10–28/ago | ✅ 09/ago |
| S2 | Tool registry + medição | 24/ago–04/set | 31/ago–18/set | ✅ 09/ago |
| **S2b** | **Grimório como aplicação** | **07–18/set** | **21/set–09/out** | ✅ 09/ago |
| S3 | Rotina contábil (guias, obrigações) | 21/set–02/out | 12–30/out | ✅ 10/ago |
| S4 | Pipeline de documento | 05–16/out | 02–20/nov | — |
| S5 | ERPAdapter + reconciliação | 19–30/out | 23/nov–11/dez | — |
| S6 | Contas a pagar, proativo, billing | 02–13/nov | 04–22/jan/2027 | — |

**A coluna "Real" precisa de uma ressalva, senão engana.** Os três primeiros
sprints fecharam muito à frente das duas estimativas, e a causa não é
produtividade: parte grande do escopo **já estava escrita** quando o cronograma
foi montado (registry, RLS, motor fiscal, `painel/metricas.py`), e o plano
contava esse trabalho como se fosse futuro. O que sobrou de S3 em diante é
majoritariamente código que não existe — e ali as estimativas do §1 continuam
valendo. Ler a coluna como velocidade projetada levaria a prometer o S4 para
agosto, e ele depende de OCR, storage por tenant e fila de revisão humana, que
não têm nada pronto.

**O que a diferença de capacidade realmente muda.** No cenário solo o S6 cai
depois do recesso, em jan/2027. Isso é aceitável e vale dizer por quê: a data
que não se move é o **destaque CBS/IBS chegando ao Simples/MEI em jan/2027**, e
quem responde por ela é o **motor fiscal (S3)**, que fecha em 30/out mesmo no
cenário solo. O S6 é comercial — billing, proativo, onboarding self-service —,
não conformidade. Escorregar ali custa receita, não risco fiscal.

**O que não se corta em nenhum cenário:** o teste de tool registry (S1), o RLS
(S1), o teste de escopo do Grimório fora do admin (S2b) e a fila de revisão
humana do OCR (S4). Os quatro são a diferença entre um produto que um escritório
assina e um que ele devolve.

---

## 2. Visão geral

```
Ago              Set              Out              Nov            Jan/27
S1  ██✅          Isolamento de 3 níveis: usuario + RLS + tool registry + T0
S2   ██✅         Tool registry + SessionContext + medição por tenant
S2b  ██✅         Grimório: aplicação própria (Hoje, Carteira, Ficha, Operação)
S3    ██✅        Rotina contábil: guias, obrigações, retenções
S4              ████ Pipeline de documento (OCR → revisão humana)
S5                 ████ ERPAdapter: ArquivoAdapter + reconciliação + DLQ
S6                    ████ Contas a pagar + proativo + billing + onboarding
T-Onda ██████     Onda Simples 1º/set — trilha comercial, sem dev novo
T-Piloto                 ████████  2–3 escritórios reais
```

---

## 3. Os sprints

### Sprint 1 — Isolamento de três níveis (10–21/ago) — ✅ **fechado em 09/ago**

O primeiro commit é o teste, não a feature.

Fechou adiantado porque o escopo já estava metade escrito (registry e RLS
saíram nos commits `ff4f99f` e `0d21941`). O que restava — nível `usuario`,
desambiguação, T0 na frente e filas — saiu junto, com 489 testes verdes e a
migração ensaiada contra uma cópia do banco de desenvolvimento. Um item do gate
segue aberto e está marcado como tal: a medição do T0 existe, o **número real**
depende de tráfego.

- [x] **Teste de tool registry** (DEC-05) — `apps/agents/registry.py` +
      `tests/test_tool_registry.py` (commit `ff4f99f`). `@exposto_ao_modelo`
      recusa campo de escopo **no import**; inspeciona o JSON Schema (não
      `model_fields`), porque alias é o que o modelo enxerga; desce em `$defs`,
      listas e uniões. `criar_agente()` fecha o desvio de mandar schema não
      registrado, e um teste varre o código atrás de `pydantic_ai` importado
      fora do registry.
- [x] **RLS no Postgres** (DEC-04) — `apps/tenants/rls.py`, migração `0003_rls`,
      middleware e `task_prerun` (commit `0d21941`). Papel `magicbi_app` sem
      `BYPASSRLS`, assumido por `SET LOCAL ROLE` — a conexão do Django segue
      dona (migra) e cada requisição vale como restrita.
- [x] Teste: query **sem** `app.tenant_id` devolve zero linhas, inclusive dentro
      de task do Celery (`tests/test_rls.py`, 13 testes).
- [x] **Nível `usuario`** (DEC-03) — `clients.Usuario` +
      `VinculoUsuarioCliente`, migração `clients/0009_nivel_usuario`,
      `UsuarioManager.por_telefone` no lugar do manager de `Cliente`
      (`tests/test_nivel_usuario.py`, 27 testes). A lógica de nono dígito em
      `clients/telefone.py` ficou intacta — o que mudou foi de quem é o número.
- [x] Desambiguação "de qual empresa você quer falar?" — `core/desambiguacao.py`
      + `security.EmpresaEmFoco`, resolvida no pipeline antes do orquestrador.
- [x] **T0 como camada da frente** (DEC-08) — `core/t0.py`, com classificador
      **estrito** separado do fallback permissivo
      (`tests/test_t0_camada_da_frente.py`).
- [x] Extensão de `tests/test_multitenancy.py`: mesmo telefone em dois clientes
      do **mesmo** escritório — o caso agora passa, e o teste que exigia
      `IntegrityError` foi invertido.
- [x] **Filas por prioridade** no Celery (DEC-10) — três filas, workers
      separados no compose, e teste que exige rota declarada para toda task.

✅ **Já pronto:** tenant = escritório, roteamento pelo número que recebe, 14
testes de isolamento, auditoria encadeada, tiers, idempotência.

**Gate S1** — nenhuma tool nova antes de tudo isto verde:
- [x] **200 tentativas de cross-tenant por prompt injection, 0 vazamentos**
      (`tests/test_isolamento_adversarial.py`, 10/ago). Sete famílias: pedido
      direto, sobrescrita de instrução, persona, escopo escrito no texto,
      ofuscação, engenharia social e confusão de entidade.

      **O modelo é assumido comprometido**, não testado quanto à obediência: o
      dublê escolhe sempre a ferramenta de maior alcance que o schema aceita e
      preenche os campos como quiser. Medir a resistência do LLM mediria algo
      que muda quando o provedor troca de versão; o que se garante é estrutural
      — escopo pelo `SessionContext`, `Literal` gerado do catálogo do cliente,
      CNPJ/CNAE do cadastro e RLS embaixo.

      **A definição de vazamento custou uma rodada vermelha e vale registrar**:
      a primeira versão acusou sete casos em que o atacante escrevia o nome da
      empresa vizinha e recebia a confirmação de volta. Não era vazamento —
      nenhuma linha foi lida, o texto voltou porque ele o digitou. Vazamento é
      dado do vizinho que aparece **sem ter sido fornecido**.
- [x] RLS bloqueia mesmo com um `.objects.filter()` de tenant omitido de
      propósito (`test_filtro_esquecido_nao_vaza`);
- [ ] T0 resolve ≥ 40% de uma amostra de 100 mensagens reais — **instrumentado,
      não medido.** Cada mensagem grava a camada que a resolveu, `metricas
      .uso_da_escada` lê a trilha e o Grimório mostra a taxa. O que falta é
      volume real: a suíte cobre um corpus de conveniência (40 frases), e a
      amostra de 100 mensagens reais só existe depois de o número voltar ao ar.

**O que o S1 mudou e não estava previsto** (achado escrevendo, não planejado):

- **`sessao_ativa` comparava o número da sessão com o cadastro da empresa.** Com
  vários números autorizados por CNPJ isso deixou de significar algo. Passou a
  comparar com **quem está escrevendo** — mais correto que antes, e é o que
  impede cadastrar o telefone de um colega conceder autoridade fiscal sozinho.
  Limite conhecido: a sessão continua sendo uma por empresa, então duas pessoas
  da mesma empresa não ficam ativas ao mesmo tempo. Falha para o lado seguro
  (pede validação); sessão por par (usuário, empresa) é o passo seguinte.
- **Migração de RLS não pode renderizar o mapa vivo de tabelas.** A `0003` cita
  agora uma lista congelada: assim que o DEC-03 acrescentou `clients_usuario` ao
  mapa, o SQL da primeira migração passou a citar tabela que só nasce três
  migrações depois — e quebrou só em banco limpo, ou seja, no deploy.
- **`clients/0009` não tem reversão automática**, por decisão: devolver o
  telefone a uma coluna só descartaria todo vínculo além do principal. O reverso
  recusa com mensagem em vez de quebrar no meio. Voltar = restaurar backup.

**Consequências do S1 que valem para tudo daqui em diante** (achadas rodando,
não previstas):

1. **Toda requisição toca o banco**, mesmo as que não usam model — declarar
   escopo é um `SET LOCAL`. Teste novo de view precisa de `django_db`.
2. **A requisição inteira é uma transação** (`transaction.atomic` no middleware),
   porque `SET LOCAL` não existe fora de uma. Bom por si só num sistema fiscal,
   mas é mudança de comportamento: view que estourar no meio não deixa escrita
   pela metade.
3. **Task sem `escritorio_id` não enxerga nada.** Toda task nova declara o tenant
   no argumento, ou é manutenção de plataforma e escreve `escopo_irrestrito()`
   com o motivo ao lado.
4. **Deploy exige rodar a migração** — é ela que cria o papel `magicbi_app` e os
   grants. Sem isso o servidor sobe com RLS inerte, e nada acusa.

---

### Sprint 2 — Tool registry e medição por tenant (24/ago–04/set) — ✅ **fechado em 09/ago**

Fechou junto com o S1 porque o S2b (Grimório) já tinha saído adiantado em
`de7d8a0`, e a tela que o S2 alimenta precisava existir para a medição não ser
invisível. O que restava era todo o resto — e saiu inteiro.

- [x] **`SessionContext`** formal — `apps/agents/contexto.py`, congelado,
      montado no webhook (`channel_whatsapp/pipeline.py`) a partir do número que
      **recebeu** (tenant), do número que **escreveu** (pessoa) e da
      desambiguação (empresa). Nenhum dos três sai do texto da mensagem.
      Confere a fronteira de tenant na construção, não no uso.
- [x] **Tool registry** — `apps/agents/ferramentas/`. As 9 intenções do
      `if/elif` viraram ferramentas registradas; o fluxo da nota saiu do
      orquestrador para `agente_nf/conversa.py`. O orquestrador ficou com
      sessão, continuações, escada de modelo e despacho.
- [x] **Um tier, um lugar** — conferido em `ferramentas.executar` e em lugar
      nenhum mais, com `_INTENCOES_VALIDAS` derivado do catálogo em vez de
      escrito à mão (era a lista que já divergiu em produção em 26/jul).
- [x] **System prompt + schema por tenant** (`apps/agents/prompt.py`), os dois
      **gerados da mesma lista** e cacheados por combinação de ferramentas. O
      `Literal` do roteador é restrito ao que aquele cliente pode executar — o
      modelo fica *impedido* de devolver o que não pode, em vez de instruído a
      não tentar.
- [x] **Tools de leitura sem ERP**: `consultar_nota` (já existia, virou tool),
      `consultar_faturamento_acumulado` (usa `fiscal/teto_mei.py`),
      `abrir_chamado` e `agendar_atendimento` — as duas com `apps/atendimento`
      e fila visível na tela Hoje.
- [x] **Medição por tenant** — `observabilidade.ConsumoLLM`: tokens (incluindo
      os servidos do cache do provedor), custo em reais, latência, requisições,
      tool calls e erro, uma linha por chamada ao modelo. Preços em `settings`
      (tabela do Groq conferida em 09/ago), cotação configurável.
- [x] **Teto de gasto com degradação** — `observabilidade/orcamento.py`:
      abaixo de 80% normal; entre 80% e 100% a **extração** cai para o modelo
      barato; acima de 100% nenhuma chamada ao modelo. Em nenhum dos três o
      atendimento para.
- [x] **Tela Operação no Grimório** — custo do mês, custo por empresa contra o
      critério de R$ 0,60, p95 e taxa de T0.

**Gate S2**
- [x] teste de registry passa com o registry **real** — e ganhou o outro lado da
      DEC-05: nenhuma ferramenta recebe escopo por **parâmetro**, não só por
      schema (`tests/test_ferramentas_e_contexto.py`);
- [ ] **custo por cliente/mês medido** — o mecanismo existe e a tela mostra;
      o número depende de tráfego real, como o T0 do S1;
- [ ] **p95 fora de pico < 8s** — instrumentado (`latencia_ms` em toda mensagem,
      `metricas.latencia_p95`), aguardando volume: percentil só é calculado a
      partir de 20 mensagens.

**O que o S2 mudou e não estava previsto:**

- **`cancelar_nota` não pode ser travado pelo tier.** Centralizar a conferência
  aplicou o Tier 3 do catálogo à ferramenta — e ela não cancela, **pede** o
  cancelamento ao contador. O efeito teria sido o perfil comum ouvindo "não
  liberado" ao tentar avisar que uma nota saiu errada. O catálogo continua
  dizendo 3 (o *ato* é destrutivo); a ferramenta declara `tier_conferido=False`
  com o motivo ao lado. A trava do cancelamento é do fluxo, não do tier.
- **A camada da mensagem passou a ser medida por consumo, não por ramo.**
  Confirmação e coleta não passam pelo roteador, então não tinham camada e
  contavam como `t1` por omissão — o que deixava a taxa de T0 pessimista. Agora
  a pergunta é a que interessa: *esta mensagem custou um modelo?*
  (`llm.chamadas_feitas`, um `ContextVar`).
- **Chamado que ninguém lê é pior que chamado nenhum.** As duas tools de escrita
  respondem "a equipe já está vendo"; sem fila do outro lado a frase é falsa e o
  cliente descobre pelo silêncio. Por isso `apps/atendimento` nasceu com tela, e
  não só com modelo.
- **Texto de titular em tabela mutável não usa crypto-shredding.** A trilha é
  append-only e por isso cifra; `Solicitacao` é mutável, então a eliminação
  apaga o texto de fato. O que não podia era o direito de eliminação passar a
  valer só em parte porque o dado nasceu numa tabela nova.

---

### Sprint 2b — Grimório como aplicação própria (07–18/set) — ✅ **fechado em 09/ago**

Executa a DEC-12. **Não é retrabalho de UI**: é a superfície onde o contador
trabalha, e as três telas que os sprints seguintes exigem (revisão de OCR, DLQ,
consumo) nascem dentro dela em vez de virarem mais páginas penduradas no admin.

Saiu adiantado em `de7d8a0`; a tela Operação fechou junto com o S2, que é quem
a alimenta.

- [x] **Casca da aplicação**: URLs próprias em `apps/painel` (`/grimorio/`),
      layout e CSS próprios. Marca do tenant vem de `painel/branding.py` — e um
      teste varre os templates atrás de nome de cliente escrito no código.
      **Sem Tailwind e sem HTMX**: o CSS escrito à mão cobriu o que as telas
      pedem, e nenhuma delas precisou de atualização parcial. Entram quando
      houver tela que os exija, não antes.
- [x] **Mixin de escopo de tenant para views**, fora do admin — três camadas
      independentes (`EscopoGrimorioMixin`, `metricas._escopar`, RLS), com um
      teste que escreve uma view errada de propósito para provar que a terceira
      segura sozinha.
- [x] **Hoje** (home): o que exige o contador agora — e, desde o S2, também os
      chamados abertos pelos clientes no WhatsApp.
- [x] **Carteira**: uma linha por empresa, com drill-down e filtro por situação.
- [x] **Ficha da empresa**: linha do tempo, integrações e quem fala pela empresa.
- [x] **Operação**: consumo, custo por empresa contra o critério de aceite, p95
      e taxa de T0. *"O que o agente não entendeu" fica para o S3*, junto com o
      resto da rotina contábil — hoje a trilha registra, a tela ainda não lista.
- [x] Admin permanece como **backoffice** da equipe Magic BI, escopado como hoje.

✅ **Reusado sem reescrita:** `painel/metricas.py` (317 linhas testadas — séries
mensais, carteira, integrações, documentos, radar de teto), `painel/apresentacao.py`,
`painel/branding.py`.

**O que faltou ao S2b e só apareceu depois** (10/ago/2026):

- **Celular.** As cinco telas nunca tinham sido abertas numa tela estreita, e o
  contador abre no telefone entre um cliente e outro. O que havia era um
  `@media` que só deitava a barra lateral e a deixava rolando na horizontal —
  "Integrações" e "Operação" ficavam escondidos atrás de um gesto que ninguém
  adivinha, e a tabela cortava justo faturamento e teto. Agora o menu quebra em
  linhas (nada escondido) e cada linha de tabela vira cartão com o rótulo da
  coluna ao lado do valor. `tests/test_grimorio_celular.py` trava o contrato:
  coluna nova sem `data-rotulo` quebra o teste, não o telefone.
- **Uma armadilha de ferramenta que vale registrar**: o Edge headless no Windows
  não respeita `--window-size` abaixo de ~500 px — pedi 390 e o viewport foi
  496. Três rodadas de screenshot foram lidas como defeito de layout antes de
  alguém medir. Para viewport real de celular, moldura com `<iframe width=390>`
  e medição por script, não julgamento por imagem.

**Gate S2b**
- [ ] O contador cumpre um dia de trabalho **sem abrir o `/admin/`** — quase.
      O Grimório deixou de ser somente leitura em 10/ago e, das cinco ações da
      fila do Hoje, **quatro já acontecem nele**: resolver chamado, vincular e
      renovar certificado, completar cadastro fiscal e ver a empresa. Sobra uma:
      **aprovar/cancelar nota**, que depende de uma decisão de produto (aprovar
      direto da fila ou abrir tela de conferência antes) e é a única que mexe em
      documento fiscal.

      A regra que atravessa as três telas de escrita: **o botão muda de lugar, o
      caminho não.** O certificado passa por
      `credentials.services.vincular_certificado_pfx` (o mesmo do admin), o
      cadastro é validado por `fiscal.dps.conferir_cadastro` (a mesma função que
      a emissão usa) e a nota, quando vier, passará pelos serviços de
      `agents/agente_nf`. Nenhuma tela reimplementa regra que já existe —
      duplicá-la é como a tela passa a dizer "pronto" com a operação ainda
      falhando.
      Toda escrita nova obedece a três regras, cobradas em
      `tests/test_grimorio_acoes.py`: só POST muda estado; o objeto vem do
      escopo do contador, nunca do id da URL; e o ato entra na trilha com quem
      clicou.
- [x] teste prova que uma view nova sem o mixin não devolve dado de outro tenant
      (`tests/test_grimorio.py`);
- [x] as telas renderizam certo em screenshot real, não só em teste (as cinco
      armadilhas de 27/jul não quebraram nenhum teste — só apareceram na imagem).

---

### Sprint 3 — Rotina contábil: o que o escritório de fato pede (21/set–02/out) — ✅ **fechado em 10/ago**

- [x] `consultar_das`, `segunda_via_guia` — DAS, DARF, GPS, FGTS, ISS.
- [x] `status_obrigacoes` — DCTFWeb, EFD, eSocial, SPED, DEFIS.
- [x] `consultar_folha`, `listar_certidoes`.
- [x] **`fiscal/` ganhou retenções** (`fiscal/retencoes.py`): ISS, IRRF, INSS e
      CSRF, com alíquotas conferidas em fonte pública em 10/ago e fundamento
      legal ao lado de cada valor.
- [x] **`fiscal/` não importa `agent/`** — e ficou **sem importar app nenhum**:
      o `teto_mei` deixou de depender de `clients.models`. Dois testes cobrem.
- [x] Tabela **`confirmacoes`** (`agente_nf.Confirmacao`), com a invariante do
      critério de aceite como teste: nenhuma nota concluída sem confirmação.
- [x] **Tela Rotina no Grimório** — não estava na lista e é obrigatória: sem
      alguém lançando guia e obrigação, as cinco ferramentas responderiam
      "ainda não tenho" para sempre. Mesma lição dos chamados do S2.

**As três decisões que definiram o sprint:**

1. **De onde vem o dado.** Não há API pública utilizável para buscar DAS, DARF
   ou o status da DCTFWeb — é o que motivou a DEC-09 a pôr o `ArquivoAdapter`
   antes de qualquer integração. Então as tabelas são preenchidas **pelo
   escritório**: hoje à mão, no S5 por arquivo, um dia por API onde existir.
2. **Sem registro, "ainda não tenho".** Nunca valor calculado, estimado ou
   repetido do mês anterior. Um DAS inventado é pior que nenhum: o cliente paga
   errado, e paga confiando. Vale igual para prazo e validade de certidão.
3. **As retenções não adivinham hipótese.** Se o serviço é do art. 714, se há
   cessão de mão de obra, se o ISS é retido no tomador — tudo isso é julgamento
   jurídico, declarado por quem sabe. E a regra que quase inverte o senso comum:
   **optante do Simples (o MEI incluído) não sofre IRRF nem CSRF**, então o
   desfecho correto para quase toda esta carteira é "não há retenção federal".
   O módulo também **se recusa** a calcular INSS de MEI: ali não há retenção de
   11% do prestador, e sim contribuição de 3% do contratante (art. 18-B da
   LC 123/2006) — devolver 11% seria erro com cara de resposta.

✅ **Já pronto:** motor fiscal com DPS via `nfelib` contra o XSD oficial,
assinatura XMLDSig, numeração/série, cancelamento, radar de teto do MEI,
confirmação em duas etapas, e a regra de que o cliente não cancela sozinho.

**Gate S3** — as guias vêm de dado real do primeiro escritório, não de mock;
`fiscal/` compila sem nada de `agent/` no import graph.

---

### Sprint 4 — Pipeline de documento (05–16/out)

O maior ROI do produto, e o terreno onde o concorrente mais forte já está (§8).

- [x] Storage S3-compatível: **bucket por tenant, prefixo por cliente**.
      (10/ago — MinIO em contêiner, provado no servidor; ir para a AWS é apagar
      `S3_ENDPOINT`.)
- [ ] OCR: chave de acesso NF-e, valor, CNPJ emitente, vencimento.
- [ ] Classificação: nota de entrada, nota de serviço, boleto, extrato, contrato.
- [ ] Validação determinística → confirmação com o cliente ("R$ 1.240,00 da
      Fornecedor X, vence 15/09. Confirma?") → recibo de volta no WhatsApp.
- [x] Recebimento pelo WhatsApp (foto/PDF → bucket → recibo). O nome ficou
      `documento_fn` no pipeline do canal, e não uma tool: não há o que rotear
      quando o cliente manda um arquivo, e gastar a escada de modelo para
      concluir o óbvio deixaria o desfecho dependendo de uma classificação que
      pode errar.
- [x] **Fila de revisão humana** — no Grimório, menu "Revisão". (10/ago)

**A ordem dentro do sprint foi invertida de propósito.** A fila de revisão veio
antes do OCR, e não depois: com 100% de revisão humana o pipeline já fecha ponta
a ponta, e o OCR, quando entrar, só REDUZ a fila. O caminho oposto — ligar o OCR
primeiro e ir corrigindo — começaria por lançamento automático de dado não
conferido, que é exatamente o que o gate abaixo proíbe.

**Gate S4**
- Documento com baixa confiança **nunca** vira lançamento automático, com teste
  que force o caminho;
- ≥ 80% dos documentos de alta confiança percorrem foto → recibo sem humano.

---

### Sprint 5 — ERPAdapter e reconciliação (19–30/out)

- [ ] `AdapterBase` convertido para a porta tipada (`buscar_cliente`,
      `listar_titulos`, `lancar_conta_pagar`, `enviar_documento`,
      `consultar_obrigacoes`).
- [ ] **`ArquivoAdapter`** — importação/exportação TXT/CSV por pasta monitorada
      (DEC-09). Vem **antes** de qualquer API.
- [ ] Sincronização assíncrona e idempotente (chave natural + hash).
- [ ] Fila de reconciliação com retry exponencial e **DLQ na área ERP do
      Grimório**, com o erro em português e reprocessamento.
- [ ] Segundo adapter: definido pelo sistema que o primeiro escritório contratado
      usa — decisão adiada de propósito.

✅ **Já pronto:** contrato de adapter, resolver, normalização, OAuth2 e os
adapters Conta Azul/Bling — que continuam servindo o produto MEI/micro (DEC-02).

**Gate S5** — carteira de um escritório real importada por arquivo, ponta a
ponta; uma falha de sincronização aparece na DLQ em vez de sumir no log.

---

### Sprint 6 — Contas a pagar, proativo, billing e onboarding (02–13/nov)

- [ ] `listar_contas_pagar(periodo, status)` e `lancar_conta_pagar(...)`.
- [ ] `solicitar_admissao` / `solicitar_demissao`.
- [ ] **Proativo**: vencimento de guia, folha fechada, documento pendente, teto
      do Simples se aproximando, certidão vencendo. Template utility aprovado,
      **opt-out por usuário** e janela de horário comercial **por tenant**.
- [ ] **Orçamento do proativo medido antes do primeiro disparo** — em volume ele
      custa mais que o LLM (§7).
- [ ] **Billing**: por cliente ativo/mês + medidor de tokens.
- [ ] **Onboarding self-service**: cadastro do tenant → verificação de negócio
      Meta → embedded signup do WABA → upload de certificado **por cliente**
      (DEC-06) → conexão do ERP com teste de conectividade → importação inicial
      da carteira → configuração de tools, tom, horário e limite de gasto.

**Gate S6** — um escritório novo entra **sozinho**, sem intervenção da equipe
Magic BI, e emite a primeira nota da carteira dele no mesmo dia.

**Ordem de corte, se o sprint estourar:** billing e onboarding podem escorregar;
proativo e contas a pagar, não.

---

## 4. Trilhas paralelas

### T-Onda — Simples → Emissor Nacional (agora até 30/set)
Em **1º/set/2026** as ME/EPP do Simples passam a emitir NFS-e exclusivamente
pelo Emissor Nacional. **Não exige desenvolvimento**: o adapter da NFS-e Nacional
já está escrito. É trilha comercial — oferta "migre emitindo pelo WhatsApp" para
a base do primeiro tenant, feita pelo contador. Cai dentro do S2, sem consumir
sprint.

### T-Piloto — 2 a 3 escritórios reais (nov/dez)
Roda depois do S6, sem feature nova, medindo os critérios de aceite (§5).

### Relógio regulatório (não se move)
| Data | Evento | Efeito aqui |
|---|---|---|
| 03/ago/2026 ✅ | NF-e passou a rejeitar IBS/CBS incorreto | Já ocorrido — validação determinística existente cobre |
| **1º/set/2026** | Simples só emite pelo Emissor Nacional | T-Onda |
| **jan/2027** | Destaque CBS/IBS chega a Simples/MEI | Respondido pelo S3, que fecha em out nos dois cenários |

---

## 5. Critérios de aceite — estado hoje e onde fecham

| Critério | Hoje | Fecha em |
|---|---|---|
| 200 tentativas cross-tenant por prompt injection, 0 vazamentos | ✅ **fechado em 10/ago** — 200 mensagens, sete famílias, modelo assumido comprometido | **S1** |
| Nenhum schema de tool com identificador de escopo | ✅ verificado no import (S1) — e, desde o S2, também na **assinatura** das ferramentas | **S1** |
| Escopo de tenant vale fora do admin | Não existe superfície fora do admin ainda | **S2b** |
| Contador trabalha um dia sem abrir o `/admin/` | Não | **S2b** |
| Nenhuma nota sem confirmação registrada em `confirmacoes` | ✅ **fechado em 10/ago** — tabela `agente_nf.Confirmacao`, com a invariante como teste | **S3** |
| Custo de LLM por cliente/mês < R$ 0,60 | **Instrumentado, sem tráfego** — `ConsumoLLM` + tela Operação | S2 mede · S6 comprova |
| Resolução sem humano ≥ 60% | Não medido | T-Piloto |
| p95 < 8s fora de pico, < 15s em pico | **Instrumentado, sem tráfego** — `latencia_ms` em toda mensagem | S2 (fora de pico) · T-Piloto (pico) |

---

## 6. O que muda nos cronogramas anteriores

**`magicbi-mvp-cronograma.md` — encerrado, papel cumprido.** As 8 semanas
provaram as duas hipóteses e entregaram fundação, segurança de sessão, adapters
reais e painel. As pendências que sobreviveram foram resolvidas por decisão, não
por atraso: DEC-12 (painel) e DEC-01 (stack). Fica como registro histórico.

**`magicbi-cronograma.md` — reenquadrado, não descartado.** F0–F4 estão
absorvidas pelo que já existe; F4.5 vira T-Onda. **F5 e F6 seguem válidas** como
roadmap do produto MEI/micro (D1 ciclo cobrança→nota, D2 emissão recorrente,
D3 radar de teto com transição assistida, D4 copiloto da reforma, D5 fluxo de
caixa preditivo, D7 memória fiscal) — e com DEC-02 elas ganham um segundo uso:
cada uma vira também recurso que o **escritório** oferece à carteira, e portanto
argumento de renovação do contrato do tenant.

O **D3** é o exemplo mais claro de por que os dois produtos são complementares e
não concorrentes: o radar de teto detecta o MEI que vai estourar (produto MEI) e
entrega o caso ao escritório para executar a migração MEI→ME (produto Hermes).
Nenhum dos dois lados funciona sozinho.

---

## 7. Orçamento unitário — o que realmente pesa

Por empresa cliente/mês, a ~25 mensagens:

| Item | Ordem de grandeza | Observação |
|---|---|---|
| LLM (Groq, com T0 absorvendo 40%) | centavos | O critério de R$ 0,60 é folgado se DEC-08 for cumprido |
| **Template utility proativo** | **domina a conta** | Cada disparo é cobrado pela Meta; 5 avisos/mês × 1.000 empresas × 50 tenants decide a margem |
| OCR | proporcional ao volume | Pico nos dias 5–10 |
| Infra (Postgres, Redis, S3, workers) | diluído por tenant | 50 × 1.000 não força arquitetura nova |

**Consequência de produto:** o proativo precisa de teto por tenant e opt-out por
usuário desde o primeiro disparo (S6), não depois da primeira fatura.

---

## 8. Riscos do cronograma

| Risco | Sinal | Resposta |
|---|---|---|
| **Nibo já entrega o S4** (WhatsApp com protocolo + IA que lê documento e sugere lançamento) | Base instalada crescendo | Não competir no OCR: diferenciar por ERP-agnóstico (`ArquivoAdapter`, S5), agente que **executa** com confirmação em duas etapas, e governança verificável |
| Domínio/Alterdata/Questor lançarem camada conversacional nativa | Anúncio de qualquer um | Acelerar S5; o valor passa a ser multi-sistema, não o canal |
| **Isolamento se perder ao sair do admin** (S2b) | Uma view nova sem o mixin | Teste de escopo no primeiro dia do S2b, antes da primeira tela — e o RLS do S1 como rede embaixo |
| **Quarta rodada de "não gostei"** no painel | Feedback visual no S2b | Screenshot real a cada tela, revisado **antes** do fim do sprint — não na entrega |
| Migração do nível `usuario` quebrar atendimento | Testes de isolamento vermelhos | S1 é o único sprint que mexe em schema de produção — feature flag e migração reversível |
| RLS derrubar worker do Celery | Task sem tenant setado | `task_prerun` + teste que roda uma task sem contexto esperando zero linhas |
| Escopo do S6 estourar | Metade do sprint | Ordem de corte já definida no S6 |
| Capacidade real ser de um dev e o calendário ser o de três | Atraso já no S2 | Adotar a coluna de 3 semanas do §1 **antes** do S2, não depois |
