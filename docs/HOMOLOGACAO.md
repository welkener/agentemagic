# Homologação — MVP rodando para a Rotina Contábil testar

Atualizado em 26/jul/2026. Conferido no código, não por memória.

**Homologar aqui = o MVP no ar, a Rotina entrando no painel e um cliente real
conversando com o agente pelo WhatsApp.** Não é homologação do governo (Produção
Restrita, §4) nem prontidão para vender a outros escritórios (§5) — as três são
coisas diferentes e não dependem uma da outra.

## Resposta curta

**O produto está pronto. O que falta é operação de deploy — e três configurações
que, se descobertas no dia do teste, queimam a sessão com o contador.**

---

## §1 O que já funciona (verificado)

O fluxo completo roda hoje, de ponta a ponta:

| | |
|---|---|
| Cliente pergunta estoque, pedidos, contas a pagar/receber, fluxo de caixa | ✅ |
| Cliente pede nota → confirma → nota emitida (mock) com protocolo e DANFSE | ✅ |
| Cliente consulta as notas dele | ✅ |
| Cliente pede cancelamento → vai para o contador decidir | ✅ |
| Contador entra no painel e vê **só a carteira dele** | ✅ |
| Contador aprova/rejeita na fila | ✅ |
| Contador é avisado por e-mail quando a Sefin recusa | ✅ |
| Voz: cliente manda áudio, sistema transcreve | ✅ (com `GROQ_API_KEY`) |
| 2FA por e-mail acima de um valor configurável | ✅ |

278 testes automatizados, **rodados localmente**. Fluxo conferido também fora
do pytest.

⚠ **O CI do GitHub Actions nunca rodou.** Todos os 14 runs desde que o workflow
foi criado falharam em 3 segundos, antes de executar qualquer step, com:
*"The job was not started because your account is locked due to a billing
issue."* Não é o código nem o workflow — é **cobrança da conta GitHub**. Até
resolver, "CI verde" não é uma garantia que este projeto tem: a única validação
é a suíte local.

---

## §2 🔴 As três coisas que travam o teste no dia

Nenhuma é código faltando — são configurações que ninguém percebe até a hora.

### 1. SMTP — sem ele o contador não consegue entrar

O acesso ao painel é por **Magic Link enviado por e-mail**. Sem SMTP
configurado, o Django cai no backend de console e **o link sai no log do
container**, não no e-mail do contador. Ele simplesmente não entra.

Os alertas de rejeição fiscal também não saem.

**Correção**: preencher `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD` no `.env` do servidor. Basta `EMAIL_HOST` que o backend
SMTP entra sozinho.

> Isto estava faltando no `docker-compose.deploy.yml` e foi corrigido agora.

### 2. O servidor está muito atrás do código

Desde o último deploy: **6 dependências novas** (`nfelib`, `signxml`, `lxml`,
`sentry-sdk`…) e **14 migrações**, incluindo duas de risco:

- **renomeação de tabelas** (`painel_escritorio` → `tenants_escritorio`);
- **backfill obrigatório** — todo `Cliente` existente precisa ganhar um
  escritório.

As duas foram validadas no banco de dev com dados, **inclusive o rollback**. Mas
exigem rebuild da imagem (não só `git pull`) e **backup antes**.

### 3. Canal WhatsApp e o escritório da Rotina

O roteamento é multi-tenant: a **instância que recebe** a mensagem identifica o
escritório. Precisa existir:

- escritório "Rotina Contábil" provisionado;
- `ConfiguracaoEvolution` vinculada **a ele** e marcada ativa;
- número conectado por QR;
- cliente(s) da Rotina cadastrados, com sessão WhatsApp ativa.

Com **um só** escritório ativo o sistema cai num fallback e funciona mesmo sem
instância casada. A partir do segundo, mensagem sem instância vinculada é
**descartada** — comportamento correto (nunca cai no tenant errado), mas que
parece "o bot não responde" se ninguém souber.

---

## §3 Roteiro do deploy

```bash
# 1. BACKUP — há renomeação de tabela e backfill
docker compose -f docker-compose.deploy.yml exec postgres \
  pg_dump -U magicbi magicbi > backup-antes-$(date +%F).sql

# 2. .env: acrescentar SMTP (ver .env.deploy.example)

# 3. rebuild (deps novas — `git pull` sozinho não basta)
git pull
docker compose -f docker-compose.deploy.yml build web
docker compose -f docker-compose.deploy.yml up -d      # migra no start

# 4. conferir
docker compose -f docker-compose.deploy.yml logs web | tail -30
```

Depois, provisionar a Rotina:

```bash
docker compose -f docker-compose.deploy.yml exec web \
  python manage.py provisionar_escritorio "Rotina Contábil" \
  --contador rotina.responsavel --email <e-mail-real>

docker compose -f docker-compose.deploy.yml exec web \
  python manage.py cadastrar_cliente <CNPJ> --escritorio rotina-contabil \
  --telefone 55DDNUMERO
```

O `cadastrar_cliente` puxa razão social, município (IBGE) e CNAE da Receita.
Restará definir o **código de tributação nacional** no admin — é decisão do
contador, não dá para deduzir do CNAE.

Por fim: `enviar_link_contador rotina.responsavel`, configurar a instância
Evolution no admin e conectar o número por QR.

### Antes de chamar o contador

```bash
docker compose -f docker-compose.deploy.yml exec web \
  python manage.py testar_conversa --cnpj <CNPJ> "qual meu estoque?"
```

Testa o agente **sem passar pelo WhatsApp**. Se responder aqui e não no
WhatsApp, o problema é canal — não agente. Vale rodar o roteiro completo do
`RUNBOOK-TESTE.md` §1 antes da sessão.

---

## §4 Isto NÃO é homologação do governo

Emissão real de NFS-e continua **bloqueada** por falta de cadastro em
`adn.producaorestrita.nfse.gov.br`. No teste com a Rotina, **as notas são
emitidas pelo mock** — protocolo e DANFSE falsos, sem efeito tributário.

Isso é adequado para validar o fluxo, a conversa e a fila de aprovação. Precisa
estar claro para o contador: **nenhuma nota do teste existe perante o fisco.**

O que já está pronto do lado fiscal (DPS válida no XSD oficial, assinatura,
mTLS, evento de cancelamento) só entra em ação quando houver cadastro no ADN e
certificado `.pfx` cadastrado.

---

## §5 Isto também NÃO é prontidão para vender a outro escritório

Para cliente pagante fora da Rotina, seguem pendentes — e nenhum é código:
parecer de responsabilidade civil, DPA citando os subprocessadores, prazos de
retenção (mecanismo pronto, desligado) e pentest. Ver
`docs/lgpd-inventario-dados.md`.

A multi-tenancy em si está pronta e testada — o que falta é jurídico.

---

## §6 Recomendação

0. **Resolver a pendência de cobrança do GitHub** — sem isso o CI segue sem
   rodar, e nenhuma regressão é pega automaticamente. Não bloqueia o teste com
   a Rotina, mas é a rede de segurança que hoje não existe.
1. **Atualizar o servidor com backup**, seguindo §3.
2. **Configurar SMTP e testar o Magic Link com o próprio e-mail** antes de
   mandar para o contador.
3. **Rodar o roteiro de conversa pelo terminal** antes de envolver o WhatsApp —
   separa falha de agente de falha de canal.
4. **Deixar explícito para a Rotina que as notas do teste são simuladas.**

Feito isso, o MVP está homologável no sentido que interessa agora: dá para
sentar com o contador e usar.

⚠ Um ponto que piora com o tempo: a partir de agora o conteúdo das conversas é
gravado cifrado, o que permite atender pedido de eliminação. **Conversa gravada
antes disso está em texto claro e é imutável** — se o teste com a Rotina gerar
conversa real, vale subir a versão nova *antes* de começar.
