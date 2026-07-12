"""
Adaptador REAL do Conta Azul — OAuth2 authorization-code (seção 8.2/7.5 dos
requisitos). Mesma interface do mock (`erp_mock.py`) — o núcleo não sabe qual
dos dois está por trás.

⚠ `mapa_endpoints` usa paths de exemplo — confirmar na doc oficial do
desenvolvedor Conta Azul (developers.contaazul.com) no momento da integração
(Semana 4 do MVP) antes de ativar em produção. `base_url`/`token_url` ficam no
Django admin (`AplicativoIntegracao`), não hardcoded, porque mudam entre
sandbox e produção.
"""
from .oauth2 import AdapterErpOAuth2Base


class ContaAzulAdapter(AdapterErpOAuth2Base):
    nome_integracao = "conta_azul"

    # ⚠ verificar paths exatos e formato de filtros na doc oficial.
    mapa_endpoints = {
        "pedidos": "v1/venda",
        "estoque": "v1/produto/estoque",
        "contas_pagar": "v1/financeiro/eventos-financeiros/contas-a-pagar",
        "contas_receber": "v1/financeiro/eventos-financeiros/contas-a-receber",
        "fluxo_caixa": "v1/financeiro/resumo",
        "pedido": "v1/venda",  # criar_rascunho
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
