# Análise de Produto e Proposta de Desenvolvimento com IA

> **Nota (11/jul/2026):** documento histórico (análise comparativa inicial). As menções a
> "Claude"/"Haiku"/"Sonnet" como motor de IA abaixo foram substituídas na decisão vigente
> por **Groq** (`llama-3.1-8b-instant` / `openai/gpt-oss-120b`) — ver
> `magicbi-hermes-comunicador.md` §7 e `requisitos-dev-piloto-rotina.md` §7.2/§12.2 para o
> estado atual.

## 1. Introdução

Este documento apresenta uma análise comparativa entre a proposta de produto do usuário, focada em agentes de IA para emissão de notas fiscais e gestão via WhatsApp, e o produto de referência "Manda a Nota". Além disso, serão fornecidas recomendações de ferramentas de IA gratuitas e prompts para auxiliar no desenvolvimento do produto.

## 2. Análise do Produto de Referência: Manda a Nota

O "Manda a Nota" [1] é uma solução que permite a emissão de notas fiscais pelo celular ou computador através de uma interface conversacional com inteligência artificial. Seus principais pontos são:

*   **Emissão Conversacional:** O usuário interage com uma IA para emitir notas fiscais, eliminando a necessidade de preencher formulários complexos.
*   **Automação de Dados:** Carrega dados do tomador automaticamente ao informar o CNPJ.
*   **Entrega:** Envia a nota emitida por e-mail e WhatsApp.
*   **Público-alvo:** MEI, ME, EPP e empresas de qualquer porte.
*   **Certificados:** Compatível com certificados A1 e A3.
*   **Plataformas:** Disponível como aplicativo (Google Play, App Store) e acesso via computador.
*   **Modelo de Negócio:** Oferece um plano gratuito com 7 notas fiscais por mês e planos pagos para maior volume.

**Pontos Fortes:**

*   **Simplicidade e Conveniência:** A emissão via conversa é um grande diferencial de usabilidade.
*   **Multiplataforma:** Acessível via mobile e web.
*   **Modelo Freemium:** Atrai usuários com o plano gratuito e monetiza com planos pagos.

**Pontos Fracos (em relação à proposta do usuário):**

*   **Foco:** Parece focado primariamente na emissão de notas fiscais, sem aprofundar na gestão financeira/ERP via IA.
*   **Integração com WhatsApp:** Embora entregue notas por WhatsApp, a interação principal parece ser via aplicativo próprio, não utilizando o WhatsApp como interface primária para a IA.

## 3. Análise da Proposta do Usuário: Agentes de IA para Rotina Contábil

A proposta do usuário [2] é ambiciosa e bem detalhada, focando na criação de agentes de IA que se conectam, via WhatsApp, a sistemas existentes (governo para emissão fiscal e Conta Azul para ERP/financeiro). O modelo de negócio sugerido é SaaS *white-label* B2B2C, distribuído através de escritórios contábeis.

**Principais Componentes e Diferenciais:**

*   **Canal Primário: WhatsApp:** A interação com os agentes de IA ocorre majoritariamente via WhatsApp, tornando a experiência mais fluida para o usuário final.
*   **Dois Produtos Iniciais:**
    *   **Agente Fiscal MEI (NFS-e):** Emissão de NFS-e nacionalmente via WhatsApp, com validação do contador no circuito.
    *   **Agente Conta Azul:** Consulta e operação (leitura + rascunho) do Conta Azul via WhatsApp, conectando o que o cliente já usa.
*   **Modelo B2B2C:** Distribuição através de escritórios contábeis, resolvendo problemas de CAC, risco fiscal e retenção.
*   **Arquitetura Robusta:** Proposta de arquitetura com orquestrador, camada de adaptadores, motor de IA (Claude function-calling), idempotência, fila, cofre de credenciais, trilha de auditoria e um painel web para consulta e fallback.
*   **Foco em Resiliência e Fallback:** Detalhamento de planos de contingência para falhas no WhatsApp, NFS-e Nacional, Conta Azul e Claude API, incluindo um painel web como canal primário de consulta em caso de indisponibilidade do WhatsApp.
*   **Segurança e LGPD:** Abordagem detalhada para autenticação, segurança de dados, inventário de dados pessoais, controles técnicos e governança.

**Comparativo com Manda a Nota:**

| Característica | Manda a Nota | Proposta do Usuário |
|---|---|---|
| **Interface Principal** | Aplicativo próprio (mobile/web) | WhatsApp |
| **Foco Principal** | Emissão de NFS-e | Emissão de NFS-e + Gestão ERP (Conta Azul) |
| **Modelo de Negócio** | B2C (freemium) | B2B2C (white-label via contadores) |
| **Integrações** | API NFS-e | API NFS-e Nacional + API Conta Azul |
| **Fallback/Resiliência** | Não detalhado na LP | Detalhado e robusto (painel web, filas, etc.) |
| **Segurança/LGPD** | Não detalhado na LP | Detalhado e abrangente |

A proposta do usuário é significativamente mais abrangente e tecnicamente detalhada, com um foco claro na integração via WhatsApp e na resiliência do sistema. O modelo B2B2C também é um diferencial estratégico.

## 4. Recomendações de Ferramentas de IA Gratuitas e de Baixo Custo

Para a construção do produto, considerando a necessidade de integração com WhatsApp, APIs fiscais e ERPs, e a geração de código/UI, as seguintes ferramentas podem ser consideradas:

### 4.1. Plataformas de Desenvolvimento com IA (Code/UI Generation)

Ferramentas como Bolt.new, Lovable.dev e v0.dev oferecem capacidades de geração de código e UI a partir de prompts. Elas geralmente possuem camadas gratuitas com limites de uso:

*   **Bolt.new [3]:** Oferece um plano gratuito com 300.000 tokens diários e 1 milhão de tokens mensais. É uma boa opção para desenvolvedores que desejam um ambiente com IA para codificação.
*   **Lovable.dev [4]:** Possui um plano gratuito com 5 créditos de construção diários (até 30 por mês) e créditos mensais para a nuvem. Focado na geração de código-fonte.
*   **v0.dev [5]:** Oferece um plano gratuito com $5 em créditos mensais, que são consumidos com base no uso de tokens. Focado em componentes de UI dentro do Vercel.

**Recomendação:** Para o piloto, **Bolt.new** parece oferecer um limite de tokens mais generoso na camada gratuita, o que pode ser vantajoso para experimentação e desenvolvimento inicial. No entanto, é crucial monitorar o consumo de tokens e os custos reais à medida que o projeto avança.

### 4.2. Integração WhatsApp com IA

A integração do WhatsApp com IA pode ser feita através da WhatsApp Cloud API da Meta ou soluções de terceiros. A proposta do usuário já menciona o uso da WhatsApp Cloud API via BSP, o que é o caminho recomendado para um produto escalável e em conformidade.

*   **WhatsApp Cloud API (Meta) [6]:** A Meta oferece 1.000 conversas iniciadas pelo cliente gratuitas por mês. As mensagens de serviço iniciadas pelo cliente dentro da janela de 24h são gratuitas. O custo principal estará nos templates fora da janela de 24h e no fee do BSP (Business Solution Provider).
*   **Evolution API [7]:** É um projeto open-source que permite conectar aplicações ao WhatsApp sem depender diretamente da API oficial da Meta. Pode ser uma alternativa para testes e desenvolvimento inicial, mas para um produto em produção, a conformidade e a robustez da WhatsApp Cloud API oficial via BSP são preferíveis.

**Recomendação:** Seguir a arquitetura proposta no documento do usuário, utilizando a **WhatsApp Cloud API da Meta via um BSP**. Isso garante conformidade, escalabilidade e acesso aos recursos oficiais. Para o piloto, o custo de mensageria será baixo devido às conversas iniciadas pelo cliente serem gratuitas dentro da janela de 24h.

### 4.3. APIs Fiscais e ERP

*   **API NFS-e Nacional [8]:** O portal Gov.br oferece documentação para a API NFS-e Nacional. É fundamental realizar o cadastro e testes em ambiente de homologação antes de ir para produção. Existem também middlewares de NF como serviço (ex: TecnoSpeed, Focus NFE, GerandoNotaFacil, Notaas) que podem simplificar a integração, mas a proposta do usuário já prevê a integração direta, o que é viável.
*   **API Conta Azul [9]:** O Conta Azul possui um portal de desenvolvedores. Embora não tenha sido possível confirmar um sandbox gratuito diretamente, a documentação é acessível e a integração via OAuth2 é padrão. É provável que seja necessário uma conta de desenvolvedor e, possivelmente, uma conta de teste do Conta Azul para a homologação da integração. O acesso à documentação da API é gratuito [10].

**Recomendação:** A integração direta com a API NFS-e Nacional e a API Conta Azul, conforme detalhado na arquitetura do usuário, é o caminho mais direto. Para o Conta Azul, será necessário investigar a disponibilidade de um ambiente de testes ou a necessidade de uma conta de teste paga para a homologação.

## 5. Arquitetura Proposta (Refinada)

A arquitetura proposta no documento do usuário é sólida e bem pensada. O princípio de "um cérebro, vários adaptadores" é excelente para garantir flexibilidade e escalabilidade. O foco em resiliência, fallback e segurança (LGPD, cofre de credenciais, trilha de auditoria) é crucial para um produto financeiro/fiscal.

**Pontos a Reforçar:**

*   **Monitoramento de Custos de IA:** Com o uso de modelos como Claude (Haiku e Sonnet), é vital implementar monitoramento rigoroso dos custos de tokens, especialmente no piloto, para evitar surpresas.
*   **Estratégia de Cache:** A estratégia de prompt caching e cache de leitura para o Conta Azul é fundamental para otimizar custos e desempenho.
*   **Painel Web como Fallback:** O painel web é um componente crítico para a continuidade do serviço e a experiência do usuário em caso de falhas no WhatsApp ou outros sistemas.

## 6. Prompts para Geração de Produto com IA

Considerando as ferramentas de geração de código/UI mencionadas (Bolt.new, Lovable.dev, v0.dev), os prompts devem ser o mais detalhados possível, descrevendo os componentes e funcionalidades desejadas.

### 6.1. Prompt para Geração da Interface do Painel Web (Exemplo para v0.dev ou similar)

```
Crie um dashboard web responsivo para um sistema de gestão de notas fiscais e ERP via WhatsApp. O dashboard deve ser read-only para o cliente e exibir:

1.  **Seção de Notas Fiscais:** Uma tabela com as notas fiscais emitidas, incluindo colunas para ID da Nota, Tomador, Valor, Data de Emissão, Status (Emitida, Pendente, Rejeitada), e um link para download do DANFSE (PDF).
2.  **Seção de Pedidos/Rascunhos (Conta Azul):** Uma tabela com pedidos consultados ou rascunhos criados, incluindo colunas para ID do Pedido, Cliente, Valor, Status (Rascunho, Confirmado), e Data.
3.  **Seção de Pendências de Aprovação:** Uma lista de ações que requerem aprovação humana (Tier 1), com botões de 'Aprovar' e 'Rejeitar'.
4.  **Histórico de Interações:** Um feed cronológico das interações do usuário com o agente de IA via WhatsApp.
5.  **Funcionalidades Adicionais:** Botões para exportar dados (PDF/CSV) e um campo de busca para filtrar as tabelas.

O design deve ser limpo, moderno, utilizando a paleta de cores da Rotina Contábil: navy (#000066) dominante, dourado (#E8A93C) como acento, e periwinkle (#5B67C9) de apoio. O login deve ser via magic link/OTP, sem senha. O dashboard deve ser otimizado para visualização em desktop e mobile.
```

### 6.2. Prompt para Geração de Componentes de Backend (Exemplo para Bolt.new ou similar)

```
Desenvolva um módulo de backend em Node.js (TypeScript) para gerenciar a camada de adaptadores de um sistema de agentes de IA para WhatsApp. O módulo deve expor uma interface interna única com as seguintes funções:

1.  `consultar(entidade: string, filtro: any): Promise<any>`: Para consultar dados de entidades (ex: pedidos, estoque, financeiro) em sistemas externos.
2.  `criar_rascunho(payload: any): Promise<any>`: Para criar rascunhos de entidades (ex: pedidos) em sistemas externos.
3.  `alterar(id: string, payload: any): Promise<any>`: Para alterar entidades existentes.
4.  `emitir(payload: any): Promise<any>`: Para emitir documentos fiscais (NFS-e).

Cada função deve ser implementada por adaptadores específicos para NFS-e Nacional e Conta Azul. O módulo deve incluir tratamento de erros, traduzindo erros externos para um catálogo de erros interno (ex: `REJEICAO_FISCAL`, `AUTH_EXPIRADA`). Deve-se considerar a idempotência para operações de escrita. Utilize um banco de dados PostgreSQL para persistência de dados e Redis para filas de eventos.
```

### 6.3. Prompt para Geração de Lógica de Roteamento de IA (Exemplo para Claude function-calling)

```
Você é o orquestrador de um agente de IA para WhatsApp. Sua função é rotear as intenções do usuário para as ferramentas apropriadas e gerenciar o estado da conversa. As ferramentas disponíveis são:

*   `emitir_nfse(tomador: string, valor: number, descricao: string)`: Emite uma Nota Fiscal de Serviço Eletrônica.
*   `consultar_pedido(cliente: string, status: string)`: Consulta pedidos no Conta Azul.
*   `criar_rascunho_pedido(cliente: string, itens: Array<{produto: string, quantidade: number}>)`: Cria um rascunho de pedido no Conta Azul.
*   `consultar_financeiro(tipo: 'pagar' | 'receber' | 'fluxo_caixa')`: Consulta informações financeiras no Conta Azul.

Ao receber uma mensagem do usuário, você deve:

1.  Classificar a intenção (ex: emitir nota, consultar pedido, criar rascunho, consultar financeiro).
2.  Extrair os parâmetros necessários para a ferramenta identificada.
3.  Se a intenção for 'emitir nota', validar o CNAE e regras tributárias antes de chamar a ferramenta `emitir_nfse`. Nunca inferir alíquotas livremente.
4.  Gerar um resumo para confirmação humana (Tier 1) antes de executar ações de escrita.
5.  Gerenciar o estado da conversa, persistindo-o em um banco de dados.

Exemplo de interação:
Usuário: "Quero emitir uma nota para a Empresa X, serviço de consultoria, valor de 500 reais."
Resposta esperada: Chamar a ferramenta `emitir_nfse` com os parâmetros extraídos, após validação e confirmação.
```

## 7. Conclusão

A proposta do usuário para agentes de IA para a Rotina Contábil é promissora e bem fundamentada. A abordagem via WhatsApp, o modelo B2B2C e a arquitetura detalhada são pontos fortes. Com a utilização estratégica de ferramentas de IA gratuitas ou de baixo custo para geração de código/UI e a integração com APIs existentes, o piloto pode ser desenvolvido de forma eficiente. É crucial manter o foco na segurança, resiliência e monitoramento de custos para garantir o sucesso do projeto.

## 8. Referências

[1] [Manda a Nota!](https://titanioproducoes.com.br/api/lp/manda-a-nota?utm_source=ig&utm_medium=social&utm_content=link_in_bio&fbclid=PAb21jcASqLNtleHRuA2FlbQIxMQBzcnRjBmFwcF9pZA81NjcwNjczNDMzNTI0MjcAAaceKx35VSPdZhiPILMTuB4evU0h7116T0YptXpCu_CwoX6O5wFYA7yN4-dfyQ_aem_lF-w48_FF6Xw2IBOKhQlVw)
[2] [Requisitos de desenvolvimento — Piloto Rotina Contábil](/home/ubuntu/upload/requisitos-dev-piloto-rotina.md)
[3] [Plans & pricing: Bolt's AI powered website and app builder](https://bolt.new/pricing)
[4] [Lovable Pricing](https://lovable.dev/pricing)
[5] [Plans and Pricing - v0 by Vercel](https://v0.app/pricing)
[6] [WhatsApp Business Platform Pricing](https://whatsappbusiness.com/products/platform-pricing/)
[7] [Evolution API WhatsApp: Open Source Alternative to Integrate](https://gurusup.com/blog/evolution-api-whatsapp)
[8] [APIs - Prod. Restrita e Produção - Portal Gov.br](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/apis-prod-restrita-e-producao)
[9] [Conta Azul: Portal do Desenvolvedor](https://developers-portal.contaazul.com/)
[10] [Integração API: perguntas frequentes de desenvolvedores](https://ajuda.contaazul.com/hc/pt-br/articles/360044777972-Integra%C3%A7%C3%A3o-API-perguntas-frequentes-de-desenvolvedores)
