# Documento de requisitos — Agentes de conexão via WhatsApp (Fiscal MEI + Integração ERP)

> **Atualização (jul/2026):** produto agora sob a marca **Magic BI** em parceria com a
> Rotina Contábil (nomes: Fiscus, Lumen, Grimório, Sigillum — ver
> `magicbi-marca-e-nomes.md`). A decisão de custódia da seção 2.4 foi refinada em uma
> matriz por produto/fase em `magicbi-custodia-fiscal.md`; cronograma consolidado em
> `magicbi-cronograma.md`.

> **Mudança de direção registrada nesta versão**: o projeto deixou de ser
> "construir um ERP próprio" e passou a ser "agentes que se conectam a
> sistemas que já existem" — governo (emissão fiscal) e ERPs de mercado.
> Isso é estrategicamente melhor: você não assume a responsabilidade de
> manter um sistema fiscal/contábil certificado, e foca no que é seu
> diferencial real — a experiência via WhatsApp + IA por cima de
> infraestrutura que outros já validaram.

---

## 1. Os dois públicos e os dois agentes

| Público | Necessidade | Agente |
|---|---|---|
| **MEI** | Emitir nota fiscal (serviço e/ou produto) sem aprender portal de governo | **Agente fiscal MEI** — vinculado ao cadastro na sua plataforma |
| **ME, EPP, grande porte** | Já tem ERP — quer gerenciar pedidos/estoque/financeiro por WhatsApp sem trocar de sistema | **Agente de integração com ERP existente** |

Os dois são produtos diferentes, com modelos de risco e de negócio
diferentes — tratados em seções separadas abaixo.

---

## 2. Agente fiscal para MEI — análise completa

### 2.1 O que muda entre nota de serviço e nota de produto (ponto central)

Pesquisei a fundo antes de escrever isto, porque é a decisão que mais
afeta a arquitetura:

| | NFS-e (serviço) | NF-e / NFC-e (produto/mercadoria) |
|---|---|---|
| Quem regula | Governo Federal (Receita Federal/ADN) | Cada Secretaria da Fazenda estadual (27 sistemas diferentes) |
| Existe padrão nacional? | **Sim** — desde set/2023 o MEI é obrigado a emitir exclusivamente pelo Emissor Nacional (ADN). APIs de produção liberadas em out/2025, com documentação técnica e Swagger oficiais. | **Não** — cada estado tem seu próprio sistema, credenciamento e regras. É por isso que "começar pelo Maranhão" é a abordagem certa: dá pra expandir estado por estado. |
| Esforço de integração | **Uma integração cobre o Brasil inteiro** pra serviço | Uma integração por estado (ou um middleware que já abstraia isso — ver seção 2.3) |

**Conclusão prática**: comece pelo agente de NFS-e (MEI prestador de
serviço) — é a integração de maior alcance com menor esforço técnico.
NF-e pra produto, começando no Maranhão, é o passo 2.

### 2.2 Por que o timing de mercado importa agora

Não é só uma boa ideia técnica — há uma janela real se abrindo:

- Vários estados estão tornando **obrigatória** a partir de **abril de
  2026** a Inscrição Estadual e a emissão de documento fiscal eletrônico
  para MEI com atividade sujeita a ICMS (já publicado em decreto em mais
  de um estado). Muito MEI que nunca precisou lidar com nota de produto
  vai precisar agora, sem saber como.
- A partir de **2027**, pela Nota Técnica do ENCAT ligada à Reforma
  Tributária, **NF-e e NFC-e em novo layout serão obrigatórias para
  todos**, incluindo MEI — e 2026 é o ano de adequação.
- Em janeiro de 2026 entraram em vigor os novos campos de IBS/CBS na
  NFS-e — sistemas desatualizados já estão gerando rejeição de nota.

Isso significa: nos próximos 12–18 meses existe uma onda concreta de MEI
que vai precisar se adequar e não vai saber como. É a janela certa pra
lançar.

### 2.3 Arquitetura recomendada

**Para NFS-e (serviço)**: integrar direto com a API NFS-e Nacional
(ADN/Sefin Nacional). É uma API REST/JSON, com ambiente de testes
("Produção Restrita") antes de produção — siga esse caminho, nunca pule
direto pra produção.

**Para NF-e (produto, começando no Maranhão)**: **não recomendo**
integrar direto com o webservice SOAP do SEFAZ-MA na mão. O custo de
manutenção disso é alto — assinatura de XML, gestão de certificado,
modo de contingência, mudança de schema a cada nota técnica nova (e como
vimos, isso está mudando com frequência agora por causa da Reforma
Tributária). A categoria de fornecedor que existe exatamente pra resolver
isso é o **"emissor de nota fiscal como serviço"** (ex: Focus NFe, eNotas,
NFe.io, Webmania, entre outros) — eles já mantêm a integração com os 27
SEFAZ atrás de uma API simples, e cobram por nota emitida ou uma
mensalidade pequena. Isso é exatamente "agente que se conecta", não
"reconstruir a complexidade do SEFAZ você mesmo". Compare 2–3 desses
antes de escolher — preço e cobertura por estado variam.

### 2.4 A parte mais crítica: custódia de identidade fiscal

"Vinculado ao cadastro da plataforma de agentes" significa que sua
plataforma vai, de alguma forma, agir em nome do MEI perante o governo.
Isso é um nível de risco comparável a guardar a senha do banco de
alguém — é a decisão de segurança mais importante deste documento.

Opções, da mais segura pra mais arriscada:

1. **Procuração eletrônica via e-CAC/gov.br, com escopo limitado** — o
   MEI autoriza sua aplicação a agir em nome dele dentro de uma permissão
   específica, sem você guardar nenhum certificado físico dele.
   **Recomendado para começar.**
2. **Certificado digital em nuvem** — a Autoridade Certificadora guarda o
   certificado, sua aplicação só recebe um token de assinatura por sessão.
   Reduz seu risco de custódia, mas ainda depende de um fluxo de
   autorização por cliente.
3. **Você mesmo armazenando o arquivo do certificado (.pfx) do cliente**
   — **evite isso.** Se sua infraestrutura for comprometida, um atacante
   pode emitir nota fiscal falsa em nome do MEI, ou pior, autorizar outras
   operações perante a Receita. É o cenário de maior responsabilidade
   possível em todo este projeto.

### 2.5 Riscos específicos do agente fiscal MEI

| Risco | Mitigação |
|---|---|
| Erro de classificação fiscal (CNAE/alíquota errada) gerando cobrança indevida | Validar CNAE e regras tributárias antes de enviar — nunca deixar o agente "inferir" alíquota livremente |
| Nota emitida em duplicidade (mensagem repetida no WhatsApp) | Idempotência — mesma prática já usada no agente de ERP |
| Vazamento de credencial = fraude fiscal em nome do cliente | Modelo de custódia da seção 2.4 — nunca guardar .pfx cru sem proteção de nível bancário (HSM/cofre) |
| Instabilidade do sistema nacional (já relatada em notícias de início de 2026) | Modo de contingência e retry — o agente não pode falhar silenciosamente |
| Mudança de legislação/leiaute (IBS/CBS em 2026, split payment em 2027) | Arquitetura com regras de tributação configuráveis, nunca hardcoded |

---

## 3. Agente de integração com ERP existente — ME, EPP, grande porte

### 3.1 Panorama dos maiores ERPs do mercado brasileiro

| ERP | Porte típico | API pública confirmada | Observação |
|---|---|---|---|
| **Bling** | Pequeno/médio | ✅ Confirmado — REST, OAuth2/JWT, webhooks (pedidos, estoque, financeiro, NF-e) | Já documentado em detalhe nesta conversa anteriormente |
| **Conta Azul Pro** | Pequeno/médio | ✅ Confirmado — REST, OAuth2, módulos Vendas/Estoque/Cadastros/Financeiro/Notas fiscais, portal de desenvolvedor próprio | Nova versão da API lançada em mar/2025; pede 2FA na configuração inicial do app |
| **Tiny ERP (Olist)** | Pequeno/médio | Historicamente tem API pública | Confirme documentação atual antes de integrar |
| **Omie** | Pequeno/médio | Historicamente tem API pública | Confirme documentação atual antes de integrar |
| **TOTVS** | Médio/grande | Depende muito da linha de produto (Protheus, RM etc.) | Integração tende a ser mais pesada — avalie caso a caso |
| **SAP Business One** | Médio/grande | API existe (SAP B1 Service Layer) | Integração de porte empresarial — exige parceiro SAP em geral |
| **Sankhya / Senior** | Médio/grande | Possuem integração via API/middleware próprios | Confirme com cada fornecedor — não validei nesta pesquisa |

**Recomendação**: comece com **Bling** (já documentado) e adicione
**Conta Azul** como segunda opção confirmada — os dois cobrem boa parte
do mercado de pequeno/médio porte, que é onde a venda é mais rápida.
TOTVS/SAP/Senior/Sankhya entram só quando aparecer um cliente de porte
maior disposto a pagar pela integração mais pesada.

### 3.2 Arquitetura — reaproveita o que já desenhamos

A mesma lógica de **adaptador por ERP** que já está nas skills
`guia-fiscal` se aplica aqui: uma interface interna única
("consultar pedido", "criar rascunho", "alterar pedido"), com um adaptador
diferente por ERP atrás dela. Isso permite vender pra cliente com Bling
e cliente com Conta Azul sem reescrever a lógica do agente.

### 3.3 Tiers de risco — os mesmos da versão anterior deste documento

| Tier | Exemplo | Quem decide |
|---|---|---|
| 0 — Leitura | Consultar pedido/estoque | Agente decide sozinho |
| 1 — Escrita de baixo risco | Criar rascunho de pedido | Agente cria, nada confirma sem aprovação |
| 2 — Escrita de risco médio | Alterar item/quantidade de pedido não confirmado | Aprovação humana em 1 clique |
| 3 — Escrita de alto risco | Alterar pedido confirmado, endereço, pagamento, cancelamento | Aprovação humana + confirmação fora do WhatsApp se for cliente externo |

(Esta seção é a mesma análise da versão anterior do documento — ela não
muda com a troca de ERP, só generaliza de "Bling" pra qualquer ERP do
catálogo acima.)

---

## 4. Modelo de negócio — "gestão via WhatsApp"

Os dois públicos pedem modelos de receita diferentes:

### 4.1 Camada MEI — volume alto, ticket baixo
- **Assinatura mensal fixa e baixa** (não cobrança por nota emitida —
  MEI é sensível a atrito de preço, e cobrança por unidade gera fatura
  que ninguém entende). Pense em faixas como "ilimitado dentro de um
  teto razoável de notas/mês".
- **Canal de venda mais eficiente: contadores.** A maioria dos MEI já
  tem (ou devia ter) um contador. Isso fecha o círculo com o projeto
  original desta conversa — o agente fiscal MEI pode ser **um módulo que
  os próprios escritórios de contabilidade que já são seus clientes
  oferecem aos clientes MEI deles**, em vez de você vender direto pra
  cada MEI individualmente. Você ganha dois jeitos de vender a mesma
  construção.

### 4.2 Camada ME/EPP/grande porte — ticket mais alto
- **Taxa de implementação** (configurar o adaptador do ERP específico
  do cliente) + **assinatura mensal por volume de interação/usuário**.
- Argumento de venda: "não pedimos pra você trocar de ERP, só conectamos
  o que você já usa ao WhatsApp" — reduz a fricção de venda
  drasticamente comparado a vender uma trocadeira completa de sistema.

### 4.3 O que evitar nos dois casos
- Cobrança por crédito/consumo de IA — gera atrito e fatura incompreensível
- Contrato anual antes de provar valor
- Prometer cobertura de todos os ERPs/estados desde o dia 1 — comece
  estreito (1 região fiscal + 2 ERPs) e expanda com receita validada

---

## 5. Riscos consolidados do projeto inteiro

| Categoria | Risco | Severidade |
|---|---|---|
| Fiscal/Custódia | Vazamento de credencial fiscal do MEI | **Crítica** — pode gerar fraude em nome do cliente |
| Fiscal/Operacional | Erro de classificação tributária | Alta — gera cobrança indevida, retrabalho |
| ERP | Escrita indevida em pedido (visto no documento anterior) | Alta — perda financeira/operacional do cliente |
| Regulatório | Mudança de legislação (Reforma Tributária ainda em adequação até 2027) | Média-alta — exige manutenção contínua, não é "constrói e esquece" |
| Negócio | Responsabilidade civil por erro do agente | Alta — discutir com advogado antes de produção (ver seção 6) |
| Mercado | Concorrência de soluções gratuitas do governo/Sebrae para emissão simples | Média — seu diferencial é a conveniência via WhatsApp, não a emissão em si, que é gratuita |

---

## 6. Responsabilidade — segue valendo o alerta da versão anterior

Um agente que emite nota fiscal ou altera pedido em nome de um cliente
levanta a mesma pergunta de antes, agora com peso fiscal: se o agente
errar a classificação tributária ou emitir nota indevida, quem responde?
Vale levar isso pro seu advogado antes de qualquer fase com escrita real
em produção — contrato de prestação de serviço com escopo claro de
responsabilidade, e seguro de responsabilidade civil profissional, se
fizer sentido pro volume que você for atingir.

---

## 7. Plano de implantação — fases

1. **Fase 1** — Agente de NFS-e Nacional para MEI prestador de serviço,
   com modelo de custódia via procuração eletrônica/certificado em
   nuvem (não armazenar .pfx). Uma integração, cobertura nacional.
2. **Fase 2** — Agente de NF-e para MEI de comércio, via middleware de
   nota fiscal como serviço, começando pelo Maranhão.
3. **Fase 3** — Agente de integração com 1 ERP de mercado (Bling) para
   o público ME/EPP, seguindo os tiers de risco da seção 3.3.
4. **Fase 4** — Segundo ERP (Conta Azul) + expansão geográfica de NF-e
   pra outros estados, conforme demanda validada.

---

## 8. Checklist resumido

- [ ] Confirmar modelo de custódia fiscal (procuração eletrônica é o ponto de partida recomendado)
- [ ] Cadastro e testes em ambiente de homologação da API NFS-e Nacional
- [ ] Comparar 2–3 fornecedores de "nota fiscal como serviço" pra NF-e do Maranhão
- [ ] Validar com contador(es) parceiros se o modelo de canal via escritório de contabilidade faz sentido como porta de entrada
- [ ] Registrar app de desenvolvedor no Bling e na Conta Azul
- [ ] Aplicar os mesmos tiers de risco (0–3) em qualquer escrita, fiscal ou de ERP
- [ ] Conversa com advogado sobre responsabilidade antes de qualquer escrita real em produção
