"""
Normalização de payload de ERP → forma canônica interna.

O núcleo (`agents/agente_erp/services.py::_formatar`) só conhece **uma** forma,
a mesma que o `erp_mock` produz. Cada adaptador real traduz o formato da API
dele pra cá. Sem esta camada, trocar de ERP obrigaria a mexer no agente — que é
exatamente o que a arquitetura de adaptadores existe pra evitar.

FORMA CANÔNICA (contrato — mudar aqui é mudar `_formatar` junto):

    estoque         {"itens": [{"produto": str, "quantidade": float,
                                "minimo": float | None}]}
    pedidos         {"itens": [{"id": str, "cliente": str, "total": float,
                                "status": str, "data": str}]}
    contas_pagar    {"itens": [{"fornecedor"|"cliente": str, "valor": float,
    contas_receber              "vencimento": str, "status": str}]}
    fluxo_caixa     {"fluxo_caixa": {"saldo_atual", "entradas_previstas_7d",
                                     "saidas_previstas_7d", "saldo_projetado_7d"}}

**`minimo` é `float | None` de propósito.** O Bling não expõe estoque mínimo no
saldo (ver `bling.py`), e inventar um número num aviso de "abaixo do mínimo"
seria dar ao cliente uma informação de negócio que ninguém cadastrou. Sem o
dado, o aviso simplesmente não aparece.

Normalizador que não sabe traduzir devolve `None` — vira
`PAYLOAD_NAO_MAPEADO` no `ResultadoAcao`, com o payload cru no log. Nunca
adivinha campo: resposta errada num painel financeiro é pior que resposta
ausente.
"""
from __future__ import annotations


def _num(valor, padrao: float = 0.0) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


# ---------------------------------------------------------------------------
# Bling — API v3
# ---------------------------------------------------------------------------
# Formas confirmadas em 26/jul/2026 a partir dos tipos do SDK oficialmente
# publicado da comunidade (github.com/AlexandreBellas/bling-erp-api-js, tipos
# derivados da API real) — `developer.bling.com.br/referencia` é SPA e não
# serve conteúdo pra leitura automatizada. É uma fonte secundária: confirmar
# contra uma chamada real antes de produção (`manage.py inspecionar_erp`).

# contasReceber/types/situacao.type.ts — situação é NUMÉRICA.
BLING_SITUACAO_CONTA = {
    1: "aberta",
    2: "quitada",
    3: "parcial",
    4: "devolvida",
    5: "cancelada",
    6: "devolvida_parcial",
    7: "confirmada",
}


def bling_contas(payload: dict, papel: str) -> dict | None:
    """`{"data": [{id, situacao, vencimento, valor, contato: {id, nome}}]}`.

    `papel` é "fornecedor" (contas a pagar) ou "cliente" (a receber) — é só o
    nome da coluna na resposta ao usuário; o dado vem do mesmo `contato`.
    """
    linhas = payload.get("data")
    if not isinstance(linhas, list):
        return None

    itens = []
    for linha in linhas:
        contato = linha.get("contato") or {}
        situacao = linha.get("situacao")
        itens.append(
            {
                papel: contato.get("nome") or f"Contato #{contato.get('id', '?')}",
                "valor": _num(linha.get("valor")),
                "vencimento": linha.get("vencimento") or "—",
                # Situação desconhecida NÃO vira "aberta": entraria na soma do
                # total em aberto e inflaria o número que o cliente vê.
                "status": BLING_SITUACAO_CONTA.get(situacao, "desconhecida"),
            }
        )
    return {"itens": itens}


def bling_estoque(payload: dict) -> dict | None:
    """`{"data": [{produto: {id}, saldoFisicoTotal, saldoVirtualTotal, depositos}]}`.

    ⚠ Este endpoint devolve só o **id** do produto — nome e estoque mínimo não
    vêm aqui. O nome exigiria um `GET /produtos` por item (N+1); enquanto isso
    não for resolvido, mostramos `Produto #id`, que é verdade, em vez de um
    nome inventado. `minimo=None` desliga o aviso de "abaixo do mínimo".
    """
    linhas = payload.get("data")
    if not isinstance(linhas, list):
        return None

    itens = []
    for linha in linhas:
        produto = linha.get("produto") or {}
        itens.append(
            {
                "produto": produto.get("nome") or f"Produto #{produto.get('id', '?')}",
                # Saldo FÍSICO, não o virtual: virtual já desconta reserva de
                # pedido não faturado, e "quanto tenho em estoque" é o físico.
                "quantidade": _num(linha.get("saldoFisicoTotal")),
                "minimo": None,  # Bling não expõe aqui — ver docstring
            }
        )
    return {"itens": itens}
