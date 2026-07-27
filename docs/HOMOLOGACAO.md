# Homologação — estado e o que falta

Atualizado em 26/jul/2026. Conferido no código, não por memória.

São **duas homologações diferentes** e elas não dependem uma da outra:

| | O que é | Estado |
|---|---|---|
| **§1 Produção Restrita** | homologação do governo (NFS-e Nacional) | 🟡 **pode começar hoje** |
| **§2 Produto** | pronto para cliente real fora do time | 🔴 **bloqueado por 3 itens, nenhum é código** |

---

## §1 Produção Restrita — pode começar

O passo 1 é o **cadastro em `adn.producaorestrita.nfse.gov.br`**, que não depende
de nada nosso. Com ele feito e o `.pfx` da empresa subido no admin, o sistema
tenta emitir de verdade.

| Item | Estado |
|---|---|
| DPS válida contra o XSD oficial | ✅ testado, antes e depois de assinar |
| Assinatura XMLDSig (rsa-sha1 + C14N 1.0, como o schema fixa) | ✅ |
| Transporte mTLS com o `.pfx` em custódia | ✅ |
| Evento de cancelamento (`e101101`) assinado | ✅ validado contra o XSD |
| Numeração sequencial segura sob concorrência | ✅ `select_for_update` |
| **Cadastro no ADN** | ❌ **com você** |
| **Emissão aceita pela Sefin** | ❌ nunca exercido — é o que o cadastro destrava |
| **IBS/CBS** | ⚠ **não implementável**: `nfelib` 2.5.2 (a mais recente) não tem o grupo nos bindings |

**Antes de tentar contra a Sefin**, valide local (não gasta chamada, não depende
de cadastro):

```bash
python manage.py shell -c "
from apps.clients.models import Cliente
from apps.fiscal import dps
c = Cliente.objects.get(cnpj='SEU_CNPJ')
xml = dps.montar_dps(c, {'valor':100.0,'descricao_servico':'Teste','tomador':'Fulano'}, numero=1)
print('valida:', not dps.validar_contra_xsd(xml))"
```

Se der inválido, o problema é cadastro do cliente — resolve aqui, não contra o
governo.

---

## §2 Produto — o que falta

### Pronto ✅

| Item | Onde conferir |
|---|---|
| Isolamento entre escritórios | 17 testes, cenário de mesmo CNPJ e telefone em dois tenants |
| Escalada de privilégio no convite de colegas | 13 testes |
| Segredos cifrados **com rotação de chave** sem downtime | `apps/credentials/chaves.py` |
| Chave fora de variável de ambiente | arquivo (`docker secret`/`systemd`) |
| Trilha append-only com hash encadeado | adulteração é detectável |
| **Eliminação de dados do titular** sem quebrar a trilha | crypto-shredding, ver abaixo |
| Alerta de rejeição fiscal ao contador | e-mail + card no dashboard |
| Recusa de subir com chave de sessão padrão | `settings.py` |
| Cookies `secure`, HTTPS obrigatório, HSTS | fora de DEBUG |
| CI: 278 testes + `check --deploy` | `.github/workflows/ci.yml` |

### Bloqueado 🔴 — e nenhum é código

| Item | Com quem | Por quê bloqueia |
|---|---|---|
| **Parecer de responsabilidade civil** | jurídico | o checklist do projeto o marca como **pré-condição de qualquer emissão real** |
| **DPA + minuta citando subprocessadores** | jurídico | Groq recebe texto **e áudio** do cliente; ver `lgpd-inventario-dados.md` |
| **Prazos de retenção** | jurídico | mecanismo pronto e testado, mas **desligado** — o número é decisão legal |
| Pentest leve | terceiro | postura nunca testada por quem não escreveu o código |

---

## §3 Recomendação

**Comece a Produção Restrita agora e não emita nota real antes do parecer.**

As duas coisas são paralelas. A Produção Restrita é ambiente de homologação do
governo: emitir lá não produz efeito tributário, e é o único jeito de transformar
suposição em fato — cada rejeição da Sefin vira informação concreta em vez de
especulação. Não há motivo para esperar o jurídico para isso.

Emissão **real**, em produção, é outra história: aí há efeito tributário e
responsabilidade civil sobre documento emitido em nome de terceiro. O checklist
do próprio projeto já colocava o parecer como pré-condição, e isso não mudou.

**O gate honesto para "homologado" é:** uma emissão aceita pela Sefin em Produção
Restrita **e** o parecer assinado. Os outros itens são endereçáveis em paralelo.

### As três decisões que destravam trabalho meu

1. **Prazos de retenção** (dias por tipo de dado) → ligo o expurgo, que já está
   pronto e testado.
2. **Parecer sobre o áudio no Groq** — se voz for tratada como dado biométrico,
   troco por transcrição local; o seam já existe (`settings.TRANSCRITOR_AUDIO`).
3. **Cadastro no ADN** → primeira emissão real e o trabalho que vier dela.

---

## §4 Nota sobre a eliminação de dados (LGPD art. 18, VI)

Havia uma colisão real: a trilha guarda o texto das conversas, é imutável por
exigência fiscal, e apagar uma linha quebraria a cadeia de hash de todas as
seguintes.

Resolvido por **crypto-shredding**: o conteúdo pessoal é gravado **cifrado com
uma chave por titular**. Destruir a chave torna o conteúdo irrecuperável **sem
alterar um byte da linha** — a cadeia continua verificando, a trilha continua
provando que o evento aconteceu, e o conteúdo some. Validado no banco real:
23 linhas eliminadas, cadeia íntegra, dado fiscal preservado.

```bash
python manage.py eliminar_dados_titular <cnpj> --conferir
python manage.py eliminar_dados_titular <cnpj> --confirmar   # irreversível
```

⚠ **Vale só para o que for gravado a partir de agora.** As linhas que já existem
estão em texto claro e são imutáveis — não há como cifrá-las retroativamente
(mudar `dados` mudaria o hash). Para elas, a única eliminação possível seria
apagar a linha, quebrando a cadeia.

**Consequência prática: cada dia rodando sem isto acrescenta linhas que não
poderão ser eliminadas.** Se houver conversa real de cliente na base hoje, vale
decidir logo o que fazer com esse histórico — é a única parte deste problema que
piora com o tempo.
