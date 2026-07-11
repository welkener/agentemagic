# Magic BI — Custódia de identidade fiscal: análise e decisão

> Responde à pergunta central: **"cofre com o .pfx do cliente ou procuração?"**
> Resposta curta: **não é um OU — é uma matriz por tipo de documento e por fase.**
> Procuração onde ela existe (serviços da Receita/NFS-e), assinatura remota em nuvem
> (PSC) como padrão quando certificado é inevitável, e cofre próprio de A1 só em escala,
> com salvaguardas de nível bancário. O credenciamento prévio pelo WhatsApp é o que
> permite escolher o modelo certo por cliente, no onboarding.

---

## 1. Por que não existe resposta única

A procuração eletrônica (e-CAC/gov.br) **só vale para serviços da Receita Federal**.
Ela cobre o caminho da NFS-e Nacional (Emissor Nacional/ADN), mas **não existe
procuração que assine XML de NF-e/NFC-e** nos webservices das SEFAZ estaduais — lá a
assinatura com certificado ICP-Brasil do emitente é obrigatória por especificação.
Como o roadmap tem os dois produtos (NFS-e agora, NF-e de produto na fase 2, "modelo
Manda a Nota"), precisamos dos dois modelos.

---

## 2. As quatro opções, avaliadas

| # | Modelo | Como funciona | Risco p/ Magic BI | Custo | Onde se aplica |
|---|---|---|---|---|---|
| 1 | **Procuração eletrônica** (e-CAC/gov.br, escopo limitado) | Cliente com conta gov.br Prata/Ouro outorga poderes específicos; nenhum certificado custodiado | **Mínimo** | R$ 0 | NFS-e Nacional (MEI serviço) — **⚠ verificar** cobertura exata na API Sefin/ADN em homologação |
| 2 | **Certificado em nuvem via PSC** (BirdID/Soluti, VIDaaS/Valid, SafeID) | Certificado vive no HSM da Autoridade Certificadora; nossa aplicação pede assinatura remota via API, cliente autoriza pelo app da AC (ou autorização de longa duração para lote) | Baixo — nunca tocamos a chave privada | ~R$ 100–200/ano por certificado + eventual custo por assinatura | NF-e/NFC-e e qualquer fluxo que exija certificado, enquanto o volume não justificar cofre próprio |
| 3 | **Middleware "NF como serviço"** (Focus NFe, eNotas, NFe.io, Webmania) | O middleware custodia o A1 e mantém a integração com as 27 SEFAZ; nós guardamos só a API key | Baixo (risco transferido contratualmente) | ~R$ 0,10–0,50/nota ou mensalidade | **Fase 2 (NF-e produto)** — é o caminho recomendado de entrada |
| 4 | **Cofre próprio de A1 (.pfx)** — "Sigillum" | Upload do A1 + senha pelo credenciamento; envelope encryption (KMS), senha em segredo separado, assinatura só em serviço isolado, HSM (CloudHSM) em escala | **Alto** — somos alvo; comprometimento = fraude fiscal em nome do cliente | Infra: KMS ~US$ centavos; CloudHSM ~US$ 1,5k/mês | Só quando volume/margem justificar internalizar o middleware — nunca no piloto |

**Observações honestas sobre o mercado:** o Manda a Nota e os middlewares fiscais
custodiam A1 — isto é prática padrão do setor e é viável com engenharia correta. A nossa
decisão de adiar o cofre próprio não é medo, é sequenciamento: no piloto o risco máximo
não paga o ganho, e o middleware entrega o mesmo resultado com risco transferido.
**A3 (token/cartão físico) nunca será suportado em custódia** — é tecnicamente
impossível custodiar e o Manda a Nota só o suporta em emissão local pelo app.

---

## 3. Decisão (matriz por produto e fase)

| Produto | Fase | Modelo de custódia |
|---|---|---|
| NFS-e MEI (Fiscus v1) | Piloto | **Opção 1 — procuração eletrônica**, fallback opção 2 se a API exigir certificado |
| NF-e produto (Fiscus v2) | Fase 2 | **Opção 3 — middleware** (comparar Focus NFe × eNotas × NFe.io: preço, cobertura MA, SLA) |
| NF-e produto em escala | Fase 3+ | Reavaliar **opção 4 (cofre Sigillum)** com HSM + auditoria de segurança externa + seguro cyber, se a economia por nota justificar |
| Conta Azul / ERPs | Sempre | OAuth2 por cliente — tokens no cofre, nunca senha do cliente |

---

## 4. Credenciamento prévio pelo WhatsApp (o "cadastro Manda Nota", só que no chat)

Fluxo de onboarding do cliente final — tudo iniciado e acompanhado no WhatsApp, com
apenas os passos sensíveis saindo para um link seguro:

```
1. Contato    Cliente manda "oi" no número da Rotina → Lumen se apresenta
2. Identidade Informa CNPJ → consulta pública (Receita/CNPJ) → confirma razão social,
              CNAE e regime; detecta se é MEI-serviço, MEI-comércio ou ME/EPP
3. Termos     Link único (expira em 24h) para o painel: aceite de termos de uso,
              consentimento LGPD e contrato de adesão — assinatura eletrônica simples
4. Custódia   Ramo por perfil detectado:
              a) MEI serviço  → guia passo a passo da procuração eletrônica no gov.br
                               (com prints), Lumen confirma quando a outorga aparece
              b) NF-e produto → ativação no middleware OU vínculo do certificado em
                               nuvem (PSC) OU — fase 3 — upload do A1 no link seguro
5. Validação  Emissão de teste em homologação → cliente confirma os dados no chat
6. Ativo      Lumen anuncia: "credenciamento concluído — pode pedir sua primeira nota"
```

**Regras invioláveis do credenciamento:**
- **Nunca** receber .pfx, senha de certificado ou senha gov.br **pelo chat do WhatsApp**
  (arquivos ficam na infraestrutura da Meta; mensagem não é canal de segredo). Todo
  material sensível entra por link único HTTPS para o painel, com upload direto ao cofre.
- Toda outorga/upload gera evento na trilha de auditoria append-only.
- Credenciamento incompleto expira em 7 dias e o Lumen faz um follow-up (template utility).
- O contador da Rotina vê o funil de credenciamento no Grimório e pode destravar casos.

---

## 5. Requisitos do cofre Sigillum (para quando a fase 3 chegar)

Registrar desde já para não improvisar depois:

1. Envelope encryption: chave de dados por cliente, embrulhada por CMK no KMS; senha do
   .pfx em segredo separado da chave.
2. Assinatura **somente** em microserviço isolado (rede privada, sem internet de saída
   além da SEFAZ), que recebe o hash e devolve a assinatura — o .pfx nunca sai do serviço.
3. HSM (CloudHSM/equivalente) quando houver > ~500 certificados sob custódia.
4. Least privilege + acesso auditado; alerta em qualquer leitura fora do fluxo de emissão.
5. Rotação e revogação: processo de descredenciamento apaga material criptográfico
   (crypto-shredding da chave de dados).
6. Pré-requisitos de negócio: auditoria de segurança externa, seguro de responsabilidade
   cyber, cláusula contratual de custódia revisada por advogado.
