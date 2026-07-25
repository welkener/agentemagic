# Magic BI — Segurança de sessão, autenticação e 2FA (SecurityManager)

> **Origem:** consolida e decide sobre `prompt_magicbi_v3_seguranca.md` e
> `seguranca_e_fiscal_magicbi.md` (docs recebidos 24/jul/2026). Cobre uma camada que
> **ainda não existia formalizada** no resto da documentação: quem, no WhatsApp, tem
> permissão de falar em nome de qual CNPJ — distinto da custódia do certificado fiscal
> (`magicbi-custodia-fiscal.md`, que decide quem assina o documento perante a Sefaz/ADN).

---

## 1. Três identidades diferentes — não confundir

| Camada | Pergunta que responde | Onde já está decidido |
|---|---|---|
| **Identidade fiscal** | Quem assina a DPS/NFS-e perante o governo? | `magicbi-custodia-fiscal.md` — certificado em nuvem (PSC) |
| **Identidade de sessão** (este doc) | Este número de WhatsApp pode agir pelo CNPJ X agora? | **Novo — decidido aqui** |
| **Identidade de painel** | Quem loga no Grimório (contador/cliente)? | Já previsto (magic link/OTP) em `requisitos-dev-piloto-rotina.md` §7.10 e `magicbi-mvp-cronograma.md` S5 |

Os dois arquivos novos descrevem principalmente a **identidade de sessão** — o vínculo
`wa_id ↔ CNPJ` — que faltava como spec de engenharia. Isso vira o app novo
**`apps/security`**.

---

## 2. Modelo de dados novo (`apps/security`)

```python
class SessaoWhatsapp(models.Model):
    wa_id = models.CharField(max_length=32, db_index=True)       # ID interno do WhatsApp
    cliente = models.ForeignKey("clients.Cliente", on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default="pendente")  # pendente/ativa/expirada/bloqueada
    validado_em = models.DateTimeField(null=True)
    expira_em = models.DateTimeField()                            # validado_em + 7 dias (configurável/perfil)

    class Meta:
        constraints = [
            # 1 wa_id ativo por vez -> troca de aparelho/número exige novo credenciamento
            models.UniqueConstraint(fields=["wa_id"], condition=Q(status="ativa"),
                                     name="unico_wa_id_ativo"),
        ]

class TokenMagicLink(models.Model):
    cliente = models.ForeignKey("clients.Cliente", on_delete=models.CASCADE)
    wa_id = models.CharField(max_length=32)
    jti = models.CharField(max_length=64, unique=True)   # id do token, não o token em si
    usado_em = models.DateTimeField(null=True)
    expira_em = models.DateTimeField()                    # criado + 15 min
```

O token em si (JWT assinado) **não é persistido** — só o `jti` para permitir invalidação
de uso único e detecção de replay. Igual ao padrão já usado no resto do sistema
(auditoria por hash, nunca guardar o segredo em si).

---

## 3. Fluxo (encaixa no credenciamento já decidido, não substitui)

Isto é o **detalhamento de engenharia** do passo 3 ("Termos") do fluxo já descrito em
`magicbi-custodia-fiscal.md` §4 — não um fluxo paralelo:

```
1. Cliente manda "oi" / CNPJ no WhatsApp → Lumen identifica wa_id + CNPJ
2. Núcleo gera JWT curto (15 min, claims: cliente_id, wa_id, jti) → link único por e-mail
   cadastrado na Receita/ERP (nunca reaproveita o wa_id como canal do link — 2º fator de canal)
3. Cliente abre o link → painel web confirma identidade (+ senha mestra do escritório,
   se aplicável) → webhook interno cria/renova SessaoWhatsapp(status="ativa")
4. Lumen libera a conversa: "credenciamento concluído"
5. A cada mensagem recebida: orquestrador verifica SessaoWhatsapp.status=="ativa" e
   not expirada ANTES de rotear para qualquer agente (Fiscus/agenteERP)
6. Sessão expira (7 dias de inatividade, configurável por perfil) → Lumen responde:
   "Sua sessão expirou por segurança. Valide seu acesso aqui: [link]" e bloqueia
   qualquer ação Tier ≥ 1 até revalidar (Tier 0 de leitura pública, tipo "oi", segue liberado)
```

**Proteção contra clonagem/troca de número:** se chega mensagem de um `wa_id` diferente
alegando ser o mesmo CNPJ, o sistema **nunca** transfere a sessão automaticamente —
sempre reabre credenciamento completo (novo Magic Link para o e-mail cadastrado). Isso é
o que a UniqueConstraint acima força no nível de banco.

---

## 4. 2FA para ações críticas

Threshold configurável por perfil (`Perfil.limites`, já existe o campo JSONField —
adiciona chave `valor_2fa_acima_de` e `acoes_sempre_2fa: ["alterar_dados_bancarios"]`).
Quando a intenção bate o threshold, o núcleo (não o LLM) força um passo extra antes de
Tier 1 virar "aprovada":

- Código de 6 dígitos por **e-mail** (preferir a SMS — ver risco de SIM swap abaixo).
- Implementação mínima: `secrets.randbelow(1_000_000)`, hash (mesmo padrão de
  `apps/credentials/crypto.py`) e expiração de 5 min. **Não precisa de biblioteca de TOTP**
  para isto — é código avulso por canal, não app autenticador.
- Reservar `pyotp` (ver §5) só se/quando o produto oferecer TOTP via app autenticador
  como opção adicional (não é necessidade do piloto).

## 5. Bibliotecas maduras recomendadas (evitar reinventar)

| Necessidade | Biblioteca | Por quê |
|---|---|---|
| Token do Magic Link (e-mail → painel) | **PyJWT** | Já é dependência transitiva comum no ecossistema Django; claims customizadas (`cliente_id`, `wa_id`, `jti`) não se encaixam no modelo de "login de `User`" do django-sesame/django-magiclink — aqui o token carrega vínculo de sessão de negócio, não autentica um `auth.User`. Assinar com o mesmo `DJANGO_SECRET_KEY` ou uma chave dedicada (`MAGICLINK_SIGNING_KEY`) via cofre. |
| Login do **painel** (contador/cliente, Grimório) | **django-sesame** | Esse caso é literalmente "login de `User`" — é o que a lib resolve nativamente (mantida por Aymeric Augustin, também autor de partes do Django Channels/asgiref; ampla adoção). Não usar para o vínculo `wa_id↔CNPJ` (§3), que é modelo de domínio próprio. |
| 2FA por código avulso (e-mail/SMS) | `secrets` (stdlib) | Ver §4 — não precisa de lib externa para MVP. |
| 2FA por TOTP (app autenticador), se/quando entrar | **pyotp** | Padrão RFC 6238, simples, sem dependências pesadas. |
| Cofre real (Vault), se/quando a Fase 6 avançar o Sigillum | **hvac** | Cliente Python oficial da comunidade HashiCorp Vault; API principal (`secrets.kv`, `sys.auth`) cobre o caso de uso (envelope encryption, ver `magicbi-custodia-fiscal.md` §5). No piloto **mantém-se a decisão já tomada**: Fernet local (`apps/credentials/crypto.py`) → Secrets Manager/KMS na Fase 1 de produção. HashiCorp Vault self-hosted é uma alternativa equivalente ao Secrets Manager+KMS, não uma peça adicional — escolher **um dos dois**, não os dois. |

**Nota de coerência com decisões existentes:** os dois arquivos novos sugerem Vault como
se fosse decisão nova — na verdade `magicbi-custodia-fiscal.md` já cita Vault na matriz
do Sigillum (§ "Matriz de Custódia por Produto"). Não há conflito; este documento só
formaliza que Vault (via `hvac`) e AWS Secrets Manager+KMS são **alternativas
equivalentes**, e o piloto não decide entre elas ainda (decisão adiada para quando o
cofre próprio for avaliado, Fase 6).

---

## 6. Onde isso entra no núcleo (regra de ouro preservada)

```
channel_whatsapp (recebe mensagem)
        ▼
apps/security — verifica SessaoWhatsapp ANTES de qualquer coisa
        │ inativa/expirada/inexistente → dispara fluxo de credenciamento/Magic Link
        │ ativa → segue
        ▼
apps/core (orquestrador) — igual hoje: LLM propõe, tiers decidem
        │ ação bate threshold de 2FA (§4) → apps/security pede código, aguarda confirmação
        ▼
governance (tiers 0-3) → adapters → auditoria
```

`apps/security` é **checagem de identidade**, não de autorização de ação — os tiers
0–3 (`apps/governance`) continuam sendo quem decide **o que** pode ser feito; este
módulo decide **quem** está falando. As duas camadas são independentes e compostas, não
uma substituindo a outra.

---

## 7. Riscos específicos desta camada

| Risco | Mitigação |
|---|---|
| Phishing do Magic Link (link falso pedindo confirmação) | Domínio fixo e comunicado no onboarding; nunca pedir senha/segredo por mensagem, só pelo link oficial; token de uso único (`jti` consumido) |
| SIM swap comprometendo 2FA por SMS | Preferir e-mail para código avulso (§4); SMS só como alternativa se o cliente não tiver e-mail confiável |
| Replay do JWT do Magic Link | `jti` marcado como usado no primeiro clique; expiração de 15 min; checagem de `wa_id` no claim contra o que originou o pedido |
| Sessão "eterna" por falta de expiração | `expira_em` obrigatório no model; job Celery periódico expira sessões vencidas (não depende de checagem só no momento da mensagem) |
| Engenharia social pedindo troca de número por mensagem | Nunca automatizado — sempre reabre credenciamento completo com novo Magic Link (§3) |

---

## 8. Impacto nos documentos existentes

- `magicbi-custodia-fiscal.md` §4: o passo "Termos" passa a referenciar este documento
  para o detalhamento técnico do Magic Link.
- `AgenteRotinaContabil-arquitetura-tecnica.md` §2/§3: novo módulo `apps/security` no
  mapa e no diagrama, entre `channel_whatsapp` e `core`.
- `magicbi-cronograma.md` Fase 1 e `magicbi-mvp-cronograma.md` Semana 3: entrega
  explícita do `SecurityManager` (model + Magic Link + verificação por mensagem).
- `magicbi-analise-disrupcao.md` §6 (riscos): "categoria contaminada por golpes MEI no
  WhatsApp" ganha mitigação concreta e vira **argumento de venda** — nenhum concorrente
  de R$ 20–50/mês documenta proteção contra clonagem de número.
