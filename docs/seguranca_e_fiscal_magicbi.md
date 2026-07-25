# Estratégia de Segurança e Integração Fiscal - MagicBI

## 1. Segurança e Autenticação no WhatsApp
Para evitar clonagem e garantir que apenas o dono da empresa opere o agente, implementaremos o fluxo de **"Sessão Vinculada"**:

### Fluxo de Onboarding e Login:
1.  **Primeiro Contato:** O usuário envia o CNPJ via WhatsApp.
2.  **Magic Link de Validação:** O sistema gera um link único e temporário (15 min) enviado para o e-mail cadastrado na Receita Federal ou no ERP.
3.  **Vínculo de Dispositivo:** Ao clicar no link, o usuário entra em uma página React onde deve:
    *   Confirmar a senha mestra do escritório contábil.
    *   Autorizar o número de WhatsApp específico para aquela empresa.
4.  **Sessão de 24h/7d:** O sistema cria um vínculo entre o `wa_id` (ID interno do WhatsApp) e o `CNPJ`. Periodicamente (ex: a cada 24h de inatividade ou 7 dias), o sistema solicita uma nova validação via Magic Link ou senha no chat.

### Proteção contra Clonagem:
*   **Assinatura de Mensagem:** Embora o WhatsApp seja criptografado, o sistema validará o `wa_id` contra o banco de dados. Se o número mudar, o acesso é bloqueado instantaneamente até novo onboarding.
*   **Confirmação em Duas Etapas (2FA):** Para ações críticas (emissão acima de R$ X ou alteração de dados bancários), o assistente solicita um código enviado via SMS ou E-mail.

## 2. Custódia de Certificados (Cofre/Sigillum)
Para cifrar e guardar os certificados A1 (.pfx) dos clientes com segurança bancária:

*   **HashiCorp Vault:** Utilizaremos o Vault para armazenar as chaves privadas. O arquivo .pfx é cifrado com uma chave mestre (Master Key) que nunca sai do ambiente de produção.
*   **Criptografia em Repouso:** Os certificados são guardados no banco de dados como blobs criptografados (AES-256). A chave de decriptografia só é carregada na memória no momento exato da assinatura da nota.
*   **PSC (Certificado em Nuvem):** Para o MVP, priorizaremos o PSC (BirdID, etc), onde a chave fica na Autoridade Certificadora e o MagicBI apenas solicita a assinatura via API com autorização do usuário.

## 3. NF-e Estadual (Produto) - O Desafio dos 27 Estados
Diferente da NFS-e Nacional, a NF-e de produto exige integração com a Sefaz de cada estado.

### Estratégia de Middleware:
Para não reescrever a integração 27 vezes, utilizaremos um **Gateway de Notas Fiscais** (ex: Webmania, NFe.io ou Focus NFe) como middleware:
*   **Vantagem:** Eles tratam as instabilidades da Sefaz, contingência (SCAN/DPEC) e as particularidades de cada estado.
*   **Fluxo:** MagicBI (IA) -> JSON Estruturado -> Middleware API -> Sefaz Estadual -> Retorno XML/PDF.
*   **Custos:** O custo do middleware é repassado no tier de "Produto" do MagicBI.

## 4. Matriz de Custódia por Produto
| Produto | Tipo de Certificado | Armazenamento |
|---|---|---|
| **Fiscus (NFS-e)** | PSC (Nuvem) | Token de Acesso (Vault) |
| **Sigillum (Cofre)** | A1 (.pfx) | AES-256 + HashiCorp Vault |
| **Lumen (ERP)** | OAuth2 | Refresh Tokens Cifrados |
