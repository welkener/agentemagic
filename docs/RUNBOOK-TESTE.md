# Runbook — como testar o Magic BI

Objetivo: sair de um clone do repositório e chegar a **conversar com o agente**
em poucos minutos, sem depender de WhatsApp, certificado ou cadastro no governo.

Cada camada externa (canal, ERP, Sefin) tem um degrau próprio aqui. A ideia é
que uma falha diga **qual** camada quebrou — testar tudo junto de primeira faz
qualquer erro parecer erro de canal.

---

## 0. Uma vez só

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt
```

Postgres é obrigatório (sqlite foi descontinuado — evita divergência de
comportamento, ex.: `SELECT FOR UPDATE` na auditoria). Local ou via
`docker compose up -d` na raiz.

`.env` mínimo em `backend/.env` — copie de `.env.example`. Para o degrau 1 só
`DJANGO_SECRET_KEY`, `DATABASE_URL` e `FIELD_ENCRYPTION_KEY` importam:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```bash
python manage.py migrate
python manage.py preparar_teste
```

`preparar_teste` cria escritório, contador responsável (com grupo de
permissões), cliente com **cadastro fiscal completo**, perfil Tier 1 e sessão
WhatsApp ativa. É idempotente; `--limpar` desfaz.

> Por que isso existe: acertar essas sete coisas na mão, na ordem certa, era o
> que consumia a primeira meia hora de cada rodada de teste — e errar qualquer
> uma dá um erro que não diz qual foi.

---

## 1. Conversar com o agente (sem canal nenhum)

```bash
python manage.py testar_conversa "qual meu estoque?"
python manage.py testar_conversa                       # interativo
```

Roda o orquestrador de verdade — mesmo gate de sessão, mesmos tiers, mesma
máquina de estados. Só a saída muda: terminal em vez de WhatsApp.

**Roteiro que exercita o produto inteiro:**

| Você diz | Esperado |
|---|---|
| `qual meu estoque?` | lista do ERP mock |
| `quanto tenho a receber?` | contas em aberto com total |
| `emite uma nota de 450 pro Joao, consultoria` | pede confirmação |
| `sim` | emite, devolve protocolo e DANFSE |
| `quais notas eu emiti?` | lista com protocolo e link |
| `quero cancelar a nota` | registra pedido e **manda pro contador** |

O cancelamento **não** acontece aí: o cliente pede, o contador decide. Para
aprovar, entre no admin → *Intenções fiscais* → selecione o pedido → ação
**Aprovar**.

---

## 2. Painel do contador

```bash
python manage.py createsuperuser     # equipe Magic BI: vê tudo
# ou, como contador do escritório (vê só a carteira dele):
python manage.py enviar_link_contador teste.contador
```

`http://localhost:8000/admin/` — o índice **é** o dashboard (cards, canais,
certificados, notas recentes, auditoria) e a lista de apps fica logo abaixo.

Diferença que importa: **superuser vê a plataforma inteira; contador vê só o
próprio escritório.** Para testar o isolamento de verdade, crie um segundo
escritório com `provisionar_escritorio` e confira que um não enxerga o outro.

---

## 3. WhatsApp (Evolution — só teste interno)

> ⚠ Evolution API usa protocolo não-oficial (Baileys), com risco de banimento
> do número. É **só para teste interno** dos agentes; produção exige a Cloud
> API oficial da Meta.

1. Admin → *Configurações Evolution* → cadastre `base_url`, `instancia`,
   `api_key`, vincule ao escritório e marque **ativo**.
2. Ação **Testar conexão** confirma que a instância responde.
3. Aponte o webhook da instância para `POST /webhook/evolution`.
4. Ajuste o telefone do cliente de teste para o **seu número**:
   `python manage.py preparar_teste --telefone 5511999998888`

Multi-tenant: o roteamento usa a **instância que recebeu** a mensagem, não o
remetente. Com dois escritórios, cada um precisa da instância dele cadastrada —
mensagem sem instância casada é descartada, nunca cai no tenant errado.

---

## 4. Emissão fiscal real (Produção Restrita)

Nesta ordem — cada passo depende do anterior:

1. **Cadastro no ADN** (`adn.producaorestrita.nfse.gov.br`). Sem isso nada
   abaixo funciona, e é o único passo que não depende de código.
2. **Certificado**: admin → *Credenciais* → tipo `Certificado digital — .pfx`,
   suba o arquivo e a senha. O `.pfx` é validado na hora (senha errada nem
   toca o banco) e fica cifrado em repouso.
3. **App de integração**: admin → *Aplicativos de integração* → `nfse_nacional`
   → marque **ativo**. É isso que troca o mock pelo adapter real, por cliente.
4. **Confira o cadastro fiscal do cliente** — o `preparar_teste` já preenche,
   mas cliente real precisa de: código IBGE do município, `cTribNac`
   (6 dígitos — **não é o CNAE**), opção pelo Simples, regime especial,
   tributação e retenção do ISS.

Se faltar algo, a emissão recusa com `CADASTRO_FISCAL_INCOMPLETO` **listando o
que falta** — não é preciso adivinhar.

### Antes de tentar contra a Sefin

```bash
python manage.py shell -c "
from apps.clients.models import Cliente
from apps.fiscal import dps
c = Cliente.objects.get(cnpj='11222333000181')
xml = dps.montar_dps(c, {'valor':100.0,'descricao_servico':'Teste','tomador':'Fulano'}, numero=1)
print('valida no XSD oficial:', not dps.validar_contra_xsd(xml))
"
```

Isso valida contra o `DPS_v1.00.xsd` oficial **sem chamar o governo**. Se der
inválido, o problema é o cadastro — resolva aqui, não contra a Sefin.

---

## 5. ERP real (Conta Azul / Bling)

Endpoints estão mapeados; o **formato da resposta** do Conta Azul não pôde ser
confirmado sem conta de acesso. Com credencial OAuth cadastrada:

```bash
python manage.py inspecionar_erp <cnpj> contas_receber
```

Imprime a resposta crua. Se aparecer `PAYLOAD_NAO_MAPEADO`, é esse payload que
precisa virar um normalizador em `apps/adapters/normalizacao.py` — o molde é
`bling_contas`. Enquanto isso, o cliente recebe uma resposta honesta ("consegui
falar com seu ERP, mas ainda não sei ler o formato"), nunca um número inventado.

---

## Testes automatizados

```bash
python -m pytest -q        # 227 testes
```

Cobrem, entre outros: isolamento entre escritórios (dois tenants com **mesmo
CNPJ e mesmo telefone**), escalada de privilégio no convite de colegas, DPS e
evento de cancelamento **validados contra o XSD oficial**, e o caminho LLM da
orquestração com o agente dublado.

---

## O que ainda NÃO dá para testar

Honestidade sobre os limites — nada disto é código faltando:

| | Por quê |
|---|---|
| Emissão aceita pela Sefin | falta cadastro na Produção Restrita |
| IBS/CBS | `nfelib` 2.5.2 (a mais recente) não tem o grupo nos bindings |
| WhatsApp oficial (Meta) | falta app/WABA/número verificado |
| Conta Azul com dado real | falta conta de acesso para confirmar o payload |
| DANFSE em PDF | não implementado |
| Onboarding de cliente pelo chat | não implementado — cadastro é manual no admin |
