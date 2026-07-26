"""
Adaptador REAL do Bling — 2º ERP, prova a arquitetura de adaptadores
("novo ERP = novo adaptador, sem reescrever o agente", Semana 6 do MVP).
OAuth2 authorization-code, mesmo contrato do `AdapterBase`.

**Pesquisa de endpoints (25/jul/2026) — sem conta sandbox disponível.**
`developer.bling.com.br` confirma estrutura REST v3 padrão (GET/POST/PUT/
PATCH/DELETE, OAuth2) e cita `GET /pedidos/vendas/:idPedidoVenda`,
`POST /pedidos/vendas`, filtro por `GET /pedidos/vendas`, e menciona estoque/
contas como recursos existentes — mas **não deu pra confirmar se o path
relativo correto é `Api/v3/pedidos/vendas` (como está aqui) ou só
`pedidos/vendas`** (a diferença depende do que fica em `base_url` no admin:
se `base_url` já incluir `.../Api/v3`, o prefixo duplicado aqui quebra a
chamada). **Não mudei o path** para não trocar uma suposição por outra sem
verificar de verdade — testar contra a API real (ou a doc completa) assim
que houver conta de acesso, antes de ativar em produção.
`base_url`/`token_url` ficam no Django admin (`AplicativoIntegracao`).
"""
from . import normalizacao
from .oauth2 import AdapterErpOAuth2Base


class BlingAdapter(AdapterErpOAuth2Base):
    nome_integracao = "bling"

    # ⚠ verificar paths exatos e formato de filtros na doc oficial (API v3).
    mapa_endpoints = {
        "pedidos": "Api/v3/pedidos/vendas",
        "estoque": "Api/v3/estoques/saldos",
        "contas_pagar": "Api/v3/contas/pagar",
        "contas_receber": "Api/v3/contas/receber",
        "fluxo_caixa": "Api/v3/contas/saldos",
        "pedido": "Api/v3/pedidos/vendas",  # criar_rascunho
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
        """Traduz a resposta v3 pra forma canônica (`adapters/normalizacao.py`).

        Formas confirmadas em 26/jul/2026 pelos tipos do SDK da comunidade
        (`AlexandreBellas/bling-erp-api-js`, derivados da API real) — o portal
        oficial é SPA e não serve leitura automatizada. Fonte secundária:
        confirmar com `manage.py inspecionar_erp` antes de produção.

        `pedidos` e `fluxo_caixa` ficam **sem normalizador de propósito** — não
        consegui a forma da resposta deles com evidência, e mapear no chute
        entregaria número errado num painel financeiro. Sem normalizador vira
        `PAYLOAD_NAO_MAPEADO`, que degrada com mensagem honesta e loga o payload
        cru — o insumo pra fechar isso em minutos quando houver conta.
        """
        if recurso == "estoque":
            return normalizacao.bling_estoque(payload)
        if recurso == "contas_receber":
            return normalizacao.bling_contas(payload, papel="cliente")
        if recurso == "contas_pagar":
            return normalizacao.bling_contas(payload, papel="fornecedor")
        return None
