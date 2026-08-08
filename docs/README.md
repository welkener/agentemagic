# Magic BI × Rotina Contábil — Índice da documentação

Plataforma de agentes de IA no WhatsApp para emissão fiscal e gestão de empresas,
sob a marca **Magic BI**. Desde 08/ago/2026 sustenta **dois produtos
complementares** sobre a mesma fundação multi-tenant (DEC-02):

- **Fiscus / Lumen** — a empresa final (MEI e micro) no WhatsApp: emitir nota,
  radar de teto, consultar o próprio ERP.
- **Hermes Contábil** — o **escritório contábil** como tenant, atendendo centenas
  ou milhares de empresas: guias, obrigações, documentos, folha e integração com
  o sistema contábil.

A **Rotina Contábil** (rotinacontabil.com.br) é o primeiro tenant, não o único
caso. Demais componentes: **Grimório** (a aplicação do contador), **Sigillum**
(cofre de credenciais).

## ⭐ Vigente — dois produtos, uma plataforma (ago/2026)

Adotada em 08/ago/2026 a especificação Hermes Contábil, que acrescenta o
**escritório contábil** como tenant — sem substituir o produto para a empresa
final. Leia nesta ordem:

| Documento | O que responde |
|---|---|
| [hermes-contabil-ajuste.md](hermes-contabil-ajuste.md) | **Comece aqui.** Encaixe da spec com o código real: o que já atende (~70%), o que falta, o que contradiz decisão tomada, e a concorrência do posicionamento novo (Nibo, Domínio/Onvio, Caveo) |
| [hermes-contabil-decisoes.md](hermes-contabil-decisoes.md) | As 12 decisões de arquitetura com o porquê de cada uma — dois produtos, stack, tenancy de 3 níveis, RLS, escada de modelo, adapters, o Grimório como aplicação própria, e o que da spec foi recusado |
| [hermes-contabil-cronograma.md](hermes-contabil-cronograma.md) | **Cronograma vigente** — 7 blocos de sprint (ago/2026–jan/2027), gates, trilhas paralelas, critérios de aceite e orçamento unitário |

## Documentos de decisão (jul/2026)

| Documento | O que responde |
|---|---|
| [magicbi-marca-e-nomes.md](magicbi-marca-e-nomes.md) | Marca, parceria, posicionamento vs. Manda a Nota, nomes (inspiração HP sem infringir direitos) |
| [magicbi-custodia-fiscal.md](magicbi-custodia-fiscal.md) | Cofre .pfx vs procuração — matriz de custódia por produto/fase + credenciamento prévio via WhatsApp |
| [magicbi-seguranca-sessao.md](magicbi-seguranca-sessao.md) | **Novo 24/jul/2026** — vínculo de sessão `wa_id↔CNPJ` (Magic Link), expiração, 2FA para ações críticas, anticlonagem de número (`apps/security`) |
| [magicbi-hermes-comunicador.md](magicbi-hermes-comunicador.md) | Hermes como comunicador oficial: abordagem, perfis por empresa, regras operacionais, hospedagem |
| [magicbi-cronograma.md](magicbi-cronograma.md) | ⚠ **Reenquadrado 08/ago/2026** — F0–F4 absorvidas; F5/F6 (D1–D7) seguem válidas como roadmap de produto do tenant |
| [magicbi-mvp-cronograma.md](magicbi-mvp-cronograma.md) | ⚠ **Encerrado 08/ago/2026, histórico** — MVP de 8 semanas: Fiscus (MEIs, notas reais) + agente ERP (Conta Azul e Bling reais) |
| [magicbi-ondas-desenvolvimento.md](magicbi-ondas-desenvolvimento.md) | **Novo 24/jul/2026** — sequenciamento tático em ondas curtas a partir do estado real do código, até o piloto interno na Rotina; bibliotecas maduras e checklist de homologação |
| [magicbi-analise-disrupcao.md](magicbi-analise-disrupcao.md) | Varredura 11/jul/2026 + **atualização 24/jul/2026**: estado real do backend, concorrência (Omie WhatsApp, Zucchetti voz, Meire/governo, Mei.ai), relógio regulatório set/2026–jan/2027 e white space D1–D7 |

## Documentos de base (anteriores, ainda válidos)

| Documento | Conteúdo |
|---|---|
| [AgenteRotinaContabil-arquitetura-tecnica.md](AgenteRotinaContabil-arquitetura-tecnica.md) | Arquitetura Django/Celery/React, contrato de adaptadores, modelo de dados, tiers |
| [requisitos-dev-piloto-rotina.md](requisitos-dev-piloto-rotina.md) | Especificação de engenharia do piloto: requisitos, segurança/LGPD, fallback, custos, hospedagem |
| [documento-requisitos-erp-whatsapp.md](documento-requisitos-erp-whatsapp.md) | Análise de negócio: públicos, timing de mercado, panorama de ERPs, riscos |
| [Análise de Produto e Proposta de Desenvolvimento com IA.md](<Análise de Produto e Proposta de Desenvolvimento com IA.md>) | Comparativo com o Manda a Nota e ferramentas de apoio |
| Rotina_Piloto_WhatsApp.pdf / .pptx | Apresentação executiva do piloto (14 slides) |

## Regras de ouro do projeto

**1. O LLM propõe, o núcleo determinístico decide e executa.** Nenhuma nota é emitida e
nenhum ERP é alterado direto pela saída do modelo — sempre via governança de tiers,
idempotência e auditoria append-only.

**2. Nenhuma tool recebe escopo como parâmetro do LLM.** `tenant_id`, `cliente_id`,
`cnpj` e `empresa_id` nunca aparecem em schema exposto ao modelo — entram por
`ctx: SessionContext`, resolvido no webhook. É o que impede "me mostra o faturamento
do concorrente", e há teste que varre o registry e falha se alguém esquecer
(DEC-05 em [hermes-contabil-decisoes.md](hermes-contabil-decisoes.md)).
