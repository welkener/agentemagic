"""
Adaptador MOCK de ERP genérico — dados realistas da empresa exemplo
"Padaria Estrela" (pedidos, estoque, contas a pagar/receber, fluxo de caixa).

Serve para demo comercial e para o fluxo ponta a ponta das semanas 1–2.
Os adaptadores reais (Conta Azul na S4, Bling na S6) implementam o mesmo
contrato sem tocar no núcleo.
"""
import copy
import itertools

from .base import AdapterBase, ResultadoAcao

# ---------------------------------------------------------------------------
# Base de dados fake da Padaria Estrela Ltda (CNPJ fictício 12.345.678/0001-90)
# ---------------------------------------------------------------------------
_DADOS_PADARIA_ESTRELA = {
    "pedidos": [
        {
            "id": "PED-1001",
            "cliente": "Mercadinho São José",
            "itens": [
                {"produto": "Pão francês (kg)", "quantidade": 30, "preco_unitario": 18.90},
                {"produto": "Bolo de fubá", "quantidade": 5, "preco_unitario": 32.00},
            ],
            "total": 727.00,
            "status": "em_separacao",
            "data": "2026-07-06",
        },
        {
            "id": "PED-1002",
            "cliente": "Café Aurora",
            "itens": [
                {"produto": "Croissant", "quantidade": 60, "preco_unitario": 4.50},
                {"produto": "Pão de queijo congelado (kg)", "quantidade": 12, "preco_unitario": 42.00},
            ],
            "total": 774.00,
            "status": "entregue",
            "data": "2026-07-04",
        },
        {
            "id": "PED-1003",
            "cliente": "Escola Pequeno Príncipe",
            "itens": [
                {"produto": "Lanche natural", "quantidade": 120, "preco_unitario": 7.80},
            ],
            "total": 936.00,
            "status": "aguardando_aprovacao",
            "data": "2026-07-07",
        },
    ],
    "estoque": [
        {"produto": "Farinha de trigo (saco 25kg)", "quantidade": 14, "minimo": 10},
        {"produto": "Fermento biológico (kg)", "quantidade": 6, "minimo": 4},
        {"produto": "Açúcar refinado (saco 10kg)", "quantidade": 9, "minimo": 8},
        {"produto": "Ovos (cartela 30un)", "quantidade": 22, "minimo": 15},
        {"produto": "Manteiga (kg)", "quantidade": 3, "minimo": 5},  # abaixo do mínimo!
        {"produto": "Pão de queijo congelado (kg)", "quantidade": 40, "minimo": 20},
    ],
    "contas_pagar": [
        {"id": "CP-301", "fornecedor": "Moinho Boa Safra", "valor": 2350.00, "vencimento": "2026-07-10", "status": "aberta"},
        {"id": "CP-302", "fornecedor": "Distribuidora Laticínios Sul", "valor": 890.50, "vencimento": "2026-07-12", "status": "aberta"},
        {"id": "CP-303", "fornecedor": "Energia Elétrica (CPFL)", "valor": 1120.33, "vencimento": "2026-07-15", "status": "aberta"},
    ],
    "contas_receber": [
        {"id": "CR-501", "cliente": "Mercadinho São José", "valor": 727.00, "vencimento": "2026-07-13", "status": "aberta"},
        {"id": "CR-502", "cliente": "Café Aurora", "valor": 774.00, "vencimento": "2026-07-11", "status": "aberta"},
        {"id": "CR-503", "cliente": "Escola Pequeno Príncipe", "valor": 936.00, "vencimento": "2026-07-20", "status": "aberta"},
        {"id": "CR-500", "cliente": "Café Aurora", "valor": 512.00, "vencimento": "2026-06-28", "status": "recebida"},
    ],
    "fluxo_caixa": {
        "saldo_atual": 8420.75,
        "entradas_previstas_7d": 1501.00,
        "saidas_previstas_7d": 3240.50,
        "saldo_projetado_7d": 6681.25,
    },
}

_RECURSOS_CONSULTAVEIS = {"pedidos", "estoque", "contas_pagar", "contas_receber", "fluxo_caixa"}


class ErpMockAdapter(AdapterBase):
    """ERP fake, porém realista, para o piloto (Tier 0 consultas + Tier 1 rascunho)."""

    _contador_rascunho = itertools.count(1)

    def __init__(self):
        # Cópia por instância — os testes podem mexer sem sujar o módulo.
        self._dados = copy.deepcopy(_DADOS_PADARIA_ESTRELA)

    def capacidades(self) -> set[str]:
        return {
            "consultar_pedido",
            "consultar_estoque",
            "consultar_contas_pagar",
            "consultar_contas_receber",
            "consultar_fluxo_caixa",
            "criar_rascunho_pedido",
        }

    # ------------------------------------------------------------------
    # Tier 0 — consultas
    # ------------------------------------------------------------------
    def consultar(self, recurso: str, filtros: dict, ctx) -> ResultadoAcao:
        if recurso not in _RECURSOS_CONSULTAVEIS:
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")

        dados = self._dados[recurso]

        if recurso == "pedidos" and filtros.get("id"):
            pedido = next((p for p in dados if p["id"] == filtros["id"]), None)
            if pedido is None:
                return ResultadoAcao(ok=False, erro_padronizado="PEDIDO_NAO_ENCONTRADO")
            return ResultadoAcao(ok=True, dados={"pedido": pedido}, referencia_externa=pedido["id"])

        if recurso == "estoque" and filtros.get("produto"):
            termo = filtros["produto"].lower()
            itens = [i for i in dados if termo in i["produto"].lower()]
            if not itens:
                return ResultadoAcao(ok=False, erro_padronizado="PRODUTO_NAO_ENCONTRADO")
            return ResultadoAcao(ok=True, dados={"itens": itens})

        chave = recurso if recurso == "fluxo_caixa" else "itens"
        return ResultadoAcao(ok=True, dados={chave: dados})

    # ------------------------------------------------------------------
    # Tier 1 — rascunho
    # ------------------------------------------------------------------
    def criar_rascunho(self, recurso: str, dados: dict, ctx) -> ResultadoAcao:
        if recurso != "pedido":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        if not dados.get("cliente") or not dados.get("itens"):
            return ResultadoAcao(
                ok=False,
                erro_padronizado="CAMPO_OBRIGATORIO_AUSENTE",
                dados={"campos_obrigatorios": ["cliente", "itens"]},
            )
        rascunho_id = f"RASC-PED-{next(self._contador_rascunho):04d}"
        return ResultadoAcao(
            ok=True,
            dados={"rascunho_id": rascunho_id, "pedido": dados, "status": "rascunho"},
            referencia_externa=rascunho_id,
        )

    # ------------------------------------------------------------------
    # Tier 2–3 — fora do escopo do piloto (ERP travado em 0–1)
    # ------------------------------------------------------------------
    def alterar(self, recurso: str, id_ext: str, mudancas: dict, ctx) -> ResultadoAcao:
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")

    def emitir(self, documento: str, dados: dict, ctx) -> ResultadoAcao:
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")
