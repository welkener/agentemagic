"""Normalização de payload de ERP real → forma canônica.

Os payloads usados aqui são as formas **reais** da API v3 do Bling, confirmadas
em 26/jul/2026 pelos tipos do SDK da comunidade
(`AlexandreBellas/bling-erp-api-js`). Se a API mudar, é aqui que quebra —
antes de chegar no cliente.

A regra que estes testes protegem: **nunca inventar dado**. Campo que a API não
manda vira ausência explícita, não um número plausível.
"""
import pytest

from apps.adapters import normalizacao
from apps.adapters.bling import BlingAdapter
from apps.adapters.conta_azul import ContaAzulAdapter
from apps.agents.agente_erp.services import AgenteErp

# Forma real de GET /contas/receber (contasReceber/interfaces/get.interface.ts)
PAYLOAD_CONTAS_RECEBER = {
    "data": [
        {
            "id": 12345,
            "situacao": 1,  # em aberto
            "vencimento": "2026-08-10",
            "valor": 1250.5,
            "dataEmissao": "2026-07-10",
            "contato": {"id": 99, "nome": "Mercadinho São José", "numeroDocumento": "123"},
            "formaPagamento": {"id": 1},
        },
        {
            "id": 12346,
            "situacao": 2,  # quitada — não pode entrar no total em aberto
            "vencimento": "2026-07-01",
            "valor": 800.0,
            "contato": {"id": 100, "nome": "Padaria do Bairro"},
        },
    ]
}

# Forma real de GET /estoques/saldos (estoques/interfaces/get-balances.interface.ts)
PAYLOAD_ESTOQUE = {
    "data": [
        {
            "produto": {"id": 555},
            "saldoFisicoTotal": 12.0,
            "saldoVirtualTotal": 9.0,
            "depositos": [{"id": 1, "saldoFisico": 12.0, "saldoVirtual": 9.0}],
        }
    ]
}


# ---------------------------------------------------------------------------
# Bling — contas
# ---------------------------------------------------------------------------
def test_contas_receber_vira_forma_canonica():
    canonico = normalizacao.bling_contas(PAYLOAD_CONTAS_RECEBER, papel="cliente")
    assert canonico == {
        "itens": [
            {
                "cliente": "Mercadinho São José",
                "valor": 1250.5,
                "vencimento": "2026-08-10",
                "status": "aberta",
            },
            {
                "cliente": "Padaria do Bairro",
                "valor": 800.0,
                "vencimento": "2026-07-01",
                "status": "quitada",
            },
        ]
    }


def test_situacao_desconhecida_nao_vira_aberta():
    """Se virasse 'aberta', entraria na soma e inflaria o total que o cliente vê."""
    payload = {"data": [{"situacao": 99, "valor": 10.0, "vencimento": "x", "contato": {"id": 1}}]}
    item = normalizacao.bling_contas(payload, papel="cliente")["itens"][0]
    assert item["status"] == "desconhecida"


def test_contato_sem_nome_nao_vira_vazio():
    payload = {"data": [{"situacao": 1, "valor": 10.0, "vencimento": "x", "contato": {"id": 77}}]}
    item = normalizacao.bling_contas(payload, papel="fornecedor")["itens"][0]
    assert item["fornecedor"] == "Contato #77"


def test_payload_fora_do_formato_devolve_none():
    """`None` = 'não sei ler isto' → PAYLOAD_NAO_MAPEADO, nunca um chute."""
    assert normalizacao.bling_contas({"erro": "algo"}, papel="cliente") is None
    assert normalizacao.bling_estoque({}) is None


# ---------------------------------------------------------------------------
# Bling — estoque (o caso do campo que a API NÃO manda)
# ---------------------------------------------------------------------------
def test_estoque_usa_saldo_fisico_e_nao_inventa_minimo():
    canonico = normalizacao.bling_estoque(PAYLOAD_ESTOQUE)
    item = canonico["itens"][0]
    assert item["quantidade"] == 12.0, "tem que ser o saldo FÍSICO, não o virtual"
    assert item["minimo"] is None, "Bling não expõe mínimo aqui — não pode ser inventado"
    assert item["produto"] == "Produto #555", "sem nome na API, mostra o id (verdade)"


def test_formatacao_de_estoque_sem_minimo_nao_quebra_nem_alerta():
    """Antes, `_formatar` fazia `quantidade < minimo` — com None, TypeError.
    E o pior caminho seria assumir um mínimo e alertar errado."""
    texto = AgenteErp()._formatar("estoque", normalizacao.bling_estoque(PAYLOAD_ESTOQUE))
    assert "Produto #555: 12.0" in texto
    assert "mín." not in texto
    assert "abaixo do mínimo" not in texto


def test_formatacao_de_estoque_com_minimo_ainda_alerta():
    """O mock (e ERP que exponha mínimo) continua ganhando o aviso."""
    dados = {"itens": [{"produto": "Farinha", "quantidade": 2, "minimo": 10}]}
    assert "abaixo do mínimo" in AgenteErp()._formatar("estoque", dados)


def test_total_em_aberto_ignora_conta_quitada():
    canonico = normalizacao.bling_contas(PAYLOAD_CONTAS_RECEBER, papel="cliente")
    texto = AgenteErp()._formatar("contas_receber", canonico)
    assert "1250.50" in texto
    assert "Padaria do Bairro" not in texto  # quitada não aparece nas abertas


# ---------------------------------------------------------------------------
# Roteamento do normalizador por adaptador
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "recurso,tem_normalizador",
    [
        ("estoque", True),
        ("contas_receber", True),
        ("contas_pagar", True),
        ("pedidos", False),  # forma não confirmada — de propósito sem mapa
        ("fluxo_caixa", False),
    ],
)
def test_bling_normaliza_so_o_que_foi_confirmado(recurso, tem_normalizador):
    payload = PAYLOAD_ESTOQUE if recurso == "estoque" else PAYLOAD_CONTAS_RECEBER
    resultado = BlingAdapter().normalizar(recurso, payload)
    assert (resultado is not None) is tem_normalizador


def test_conta_azul_ainda_nao_tem_forma_confirmada():
    """Endpoints inferidos, formato do corpo não — e errar aqui dá número errado
    no financeiro, não erro. Fica explicitamente sem mapa."""
    assert ContaAzulAdapter().normalizar("contas_receber", {"qualquer": "coisa"}) is None


@pytest.mark.django_db
def test_payload_nao_mapeado_da_resposta_honesta_ao_cliente(cliente):
    """"Tente de novo" seria mentira: tentar de novo dá exatamente no mesmo."""

    class AdapterSemMapa:
        def consultar(self, recurso, filtros, ctx):
            from apps.core.resultado import ResultadoAcao

            return ResultadoAcao(ok=False, erro_padronizado="PAYLOAD_NAO_MAPEADO")

    resposta = AgenteErp(adapter=AdapterSemMapa()).consultar(
        intencao="consultar_contas_receber",
        recurso="contas_receber",
        filtros={},
        perfil=cliente.perfil,
        cliente=cliente,
    )
    assert "não sei ler o formato" in resposta
    assert "tentar de novo" not in resposta
