"""
Adaptador REAL do Bling — 2º ERP, prova a arquitetura de adaptadores
("novo ERP = novo adaptador, sem reescrever o agente", Semana 6 do MVP).
OAuth2 authorization-code, mesmo contrato do `AdapterBase`.

⚠ `mapa_endpoints` usa paths de exemplo (API v3 do Bling) — confirmar na
documentação oficial (developer.bling.com.br) antes de ativar em produção.
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
