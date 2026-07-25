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
