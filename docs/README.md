# Magic BI × Rotina Contábil — Índice da documentação

Plataforma de agentes de IA no WhatsApp para emissão fiscal e gestão de empresas,
sob a marca **Magic BI**, distribuída em parceria com a **Rotina Contábil**
(rotinacontabil.com.br). Produtos: **Fiscus** (agente fiscal), **Lumen** (assistente
pessoal da empresa, sobre Hermes), **Grimório** (painel do contador), **Sigillum**
(cofre de credenciais).

## Documentos de decisão (jul/2026)

| Documento | O que responde |
|---|---|
| [magicbi-marca-e-nomes.md](magicbi-marca-e-nomes.md) | Marca, parceria, posicionamento vs. Manda a Nota, nomes (inspiração HP sem infringir direitos) |
| [magicbi-custodia-fiscal.md](magicbi-custodia-fiscal.md) | Cofre .pfx vs procuração — matriz de custódia por produto/fase + credenciamento prévio via WhatsApp |
| [magicbi-hermes-comunicador.md](magicbi-hermes-comunicador.md) | Hermes como comunicador oficial: abordagem, perfis por empresa, regras operacionais, hospedagem |
| [magicbi-cronograma.md](magicbi-cronograma.md) | Cronograma de 24 semanas até o lançamento comercial, com gates e marcos |
| [magicbi-mvp-cronograma.md](magicbi-mvp-cronograma.md) | **MVP de 8 semanas** — Fiscus (3–5 MEIs, notas reais) + agente ERP (Conta Azul e Bling reais, Tiny/Omie mock); comprime as Fases 1–3 |
| [magicbi-analise-disrupcao.md](magicbi-analise-disrupcao.md) | **Varredura 11/jul/2026**: estado real do backend, concorrência atualizada (Omie WhatsApp, Zucchetti voz, Meire/governo, Mei.ai), relógio regulatório set/2026–jan/2027 e white space D1–D7 |

## Documentos de base (anteriores, ainda válidos)

| Documento | Conteúdo |
|---|---|
| [AgenteRotinaContabil-arquitetura-tecnica.md](AgenteRotinaContabil-arquitetura-tecnica.md) | Arquitetura Django/Celery/React, contrato de adaptadores, modelo de dados, tiers |
| [requisitos-dev-piloto-rotina.md](requisitos-dev-piloto-rotina.md) | Especificação de engenharia do piloto: requisitos, segurança/LGPD, fallback, custos, hospedagem |
| [documento-requisitos-erp-whatsapp.md](documento-requisitos-erp-whatsapp.md) | Análise de negócio: públicos, timing de mercado, panorama de ERPs, riscos |
| [Análise de Produto e Proposta de Desenvolvimento com IA.md](<Análise de Produto e Proposta de Desenvolvimento com IA.md>) | Comparativo com o Manda a Nota e ferramentas de apoio |
| Rotina_Piloto_WhatsApp.pdf / .pptx | Apresentação executiva do piloto (14 slides) |

## Regra de ouro do projeto

**O LLM propõe, o núcleo determinístico decide e executa.** Nenhuma nota é emitida e
nenhum ERP é alterado direto pela saída do modelo — sempre via governança de tiers,
idempotência e auditoria append-only.
