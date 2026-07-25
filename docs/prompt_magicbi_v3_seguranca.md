# Prompt de Desenvolvimento: MagicBI - Segurança e NF-e de Produto

Este prompt atualiza o sistema para incluir protocolos de segurança bancária e suporte a notas de produto (NF-e) via middleware.

---

## 1. Módulo de Segurança (Auth & Vault)
Implemente o sistema de autenticação **"WhatsApp-First, Web-Verified"**:
- **Magic Link:** Crie um serviço que gere tokens JWT de curta duração para validação de identidade via e-mail/web antes de liberar o "Hermes" no WhatsApp.
- **Cofre (Sigillum):** Desenvolva a integração com **HashiCorp Vault** (ou `cryptography` library para MVP) para cifrar certificados A1. O sistema deve suportar o upload do .pfx via interface Web segura, nunca pelo WhatsApp.
- **Validação de Sessão:** O agente deve verificar o `wa_id` em cada interação. Se a sessão expirar (ex: 7 dias), o agente deve responder: "Sua sessão expirou por segurança. Por favor, valide seu acesso aqui: [Link]".

## 2. Módulo NF-e Estadual (Produto)
Para suportar a venda de mercadorias em todo o Brasil:
- **Middleware Integration:** Implemente um adaptador para um gateway de notas (ex: Webmania ou NFe.io). O agente deve traduzir a intenção do usuário ("Vendi 10 unidades do produto X") para o JSON esperado pelo middleware.
- **Cálculo de Impostos:** Delegue o cálculo tributário complexo (ICMS, IPI, PIS/COFINS) para o middleware ou para as regras pré-configuradas pelo contador no painel Grimório. A IA nunca deve "inventar" alíquotas.

## 3. Fluxo de Autenticação Agentica
Ao receber um CNPJ no WhatsApp:
1.  O agente verifica se o CNPJ está na base da contabilidade.
2.  Se sim, dispara o Magic Link para o e-mail do sócio.
3.  O agente aguarda o webhook de confirmação do link para liberar a conversa.
4.  Durante 24h de atividade, a conversa flui sem novas autenticações.

## 4. Prompt para Geração de Código
"Atue como um Especialista em Segurança e Backend. Desenvolva o `SecurityManager` do MagicBI. Ele deve gerenciar o vínculo entre o `wa_id` do WhatsApp e o `CNPJ` da empresa. Implemente a lógica de criptografia AES-256 para os certificados A1 e a integração com a API de um gateway de NF-e para emissão de notas de produto. Garanta que o fluxo de autenticação via Magic Link seja robusto e que o sistema bloqueie qualquer tentativa de emissão se o certificado não estiver validado ou a sessão estiver expirada."
