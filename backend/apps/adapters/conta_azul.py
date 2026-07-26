"""
Adaptador REAL do Conta Azul — OAuth2 authorization-code (seção 8.2/7.5 dos
requisitos). Mesma interface do mock (`erp_mock.py`) — o núcleo não sabe qual
dos dois está por trás.

**Pesquisa de endpoints (25/jul/2026) — sem conta sandbox disponível** (a
conta gratuita de teste expirou; ver `docs/magicbi-ondas-desenvolvimento.md`).
`developers.contaazul.com` bloqueia fetch automatizado (403/bot-protection),
então a confirmação abaixo veio de páginas indexadas publicamente (slugs de
doc, changelog) — não é o mesmo nível de certeza que testar contra a API de
verdade. Registrando o que dá pra afirmar com razoável confiança:

- **Base URL confirmada**: `https://api-v2.contaazul.com/` (não mais o
  domínio legado `api.contaazul.com.br/pub/` de integrações antigas).
- **OAuth2 confirmado**: autorização em
  `https://auth.contaazul.com/login?response_type=code&client_id=...&redirect_uri=...&scope=openid+profile+aws.cognito.signin.user.admin`;
  token em `POST https://auth.contaazul.com/oauth2/token` (Basic auth com
  `client_id:client_secret` em base64) — preencher isto em
  `AplicativoIntegracao.token_url` no admin.
- **`contas_pagar`/`contas_receber` abaixo — confirmado**: existe
  documentação pública para `POST .../contas-a-pagar` (criar evento) e
  `GET/DELETE/POST .../contas-a-receber/...`, batendo com o path que já
  estava aqui.
- **`pedidos`/`estoque` — ainda não confirmado**: há referência a
  `/v1/venda/vendedores` (lista de vendedores) e a uma rota "createvenda",
  o que sugere `/v1/venda` para vendas, mas também apareceu `/v1/sales/{id}/items`
  em outro lugar (nomenclatura inconsistente entre documentos indexados) —
  **não mudei esses dois paths** para não trocar um chute por outro chute
  com falsa confiança. Confirmar contra a doc oficial (ou uma chamada real)
  assim que a conta de acesso voltar, antes de ativar em produção.

`base_url`/`token_url` ficam no Django admin (`AplicativoIntegracao`), não
hardcoded, porque mudam entre sandbox e produção.
"""
from .oauth2 import AdapterErpOAuth2Base


class ContaAzulAdapter(AdapterErpOAuth2Base):
    nome_integracao = "conta_azul"

    mapa_endpoints = {
        "pedidos": "v1/venda",  # ⚠ ainda não confirmado — ver nota acima
        "estoque": "v1/produto/estoque",  # ⚠ ainda não confirmado — ver nota acima
        "contas_pagar": "v1/financeiro/eventos-financeiros/contas-a-pagar",  # confirmado 25/jul/2026
        "contas_receber": "v1/financeiro/eventos-financeiros/contas-a-receber",  # confirmado 25/jul/2026
        "fluxo_caixa": "v1/financeiro/resumo",  # ⚠ ainda não confirmado
        "pedido": "v1/venda",  # criar_rascunho — ⚠ ainda não confirmado
    }

    def capacidades(self) -> set[str]:
        return {
            "consultar_pedido",
            "consultar_estoque",
            "consultar_contas_pagar",
            "consultar_contas_receber",
            "consultar_fluxo_caixa",
            "criar_rascunho_pedido",
        }

    def normalizar(self, recurso: str, payload: dict) -> dict | None:
        """Sem normalizador — a forma da resposta do Conta Azul não foi confirmada.

        `developers.contaazul.com` bloqueia leitura automatizada (403/bot
        protection) e não há SDK público com tipos derivados da API real, como
        existe pro Bling. Diferente dos *endpoints* (que dá pra inferir de
        páginas indexadas), o **formato do corpo** não tem como ser inferido —
        e errar aqui não dá erro, dá número errado no financeiro do cliente.

        Então: devolve `None` de propósito → `PAYLOAD_NAO_MAPEADO` → o núcleo
        responde honestamente que ainda não sabe ler, e o payload cru vai pro
        log. Com uma conta de acesso, `manage.py inspecionar_erp <cliente>
        contas_receber` imprime a resposta real e fechar este método vira
        trabalho de minutos, no molde de `normalizacao.bling_contas`.
        """
        return None
