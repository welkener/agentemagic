"""Testes dos adaptadores mock (NFS-e e ERP) — caminho feliz + erro."""
from apps.adapters.erp_mock import ErpMockAdapter
from apps.adapters.nfse_mock import NfseMockAdapter

CTX = {"cliente": None, "perfil": None}


# ---------------------------------------------------------------------------
# NFS-e mock
# ---------------------------------------------------------------------------
def _dps_valida():
    return {
        "cnpj_prestador": "12345678000190",
        "cnae": "1091101",
        "valor": 500.0,
        "descricao_servico": "Fornecimento de pães e salgados para evento",
        "tomador": {"nome": "João da Silva", "cpf": "12345678909"},
    }


def test_nfse_emissao_feliz_retorna_protocolo_e_danfse():
    adapter = NfseMockAdapter()
    resultado = adapter.emitir("nfse", _dps_valida(), CTX)
    assert resultado.ok is True
    assert resultado.dados["situacao"] == "AUTORIZADA"
    assert resultado.dados["protocolo"].startswith("NFSE-")
    assert resultado.dados["danfse_url"].endswith(".pdf")
    assert resultado.referencia_externa == resultado.dados["protocolo"]


def test_nfse_sem_cnae_e_rejeitada():
    adapter = NfseMockAdapter()
    dps = _dps_valida()
    dps.pop("cnae")
    resultado = adapter.emitir("nfse", dps, CTX)
    assert resultado.ok is False
    assert resultado.erro_padronizado == "REJEITADA_CNAE_AUSENTE"


def test_nfse_campo_obrigatorio_ausente():
    adapter = NfseMockAdapter()
    dps = _dps_valida()
    dps.pop("tomador")
    resultado = adapter.emitir("nfse", dps, CTX)
    assert resultado.ok is False
    assert resultado.erro_padronizado == "CAMPO_OBRIGATORIO_AUSENTE"
    assert "tomador" in resultado.dados["campos_faltantes"]


def test_nfse_capacidades():
    assert "emitir_nfse" in NfseMockAdapter().capacidades()


# ---------------------------------------------------------------------------
# ERP mock (Padaria Estrela)
# ---------------------------------------------------------------------------
def test_erp_consulta_estoque():
    adapter = ErpMockAdapter()
    resultado = adapter.consultar("estoque", {}, CTX)
    assert resultado.ok is True
    produtos = [i["produto"] for i in resultado.dados["itens"]]
    assert any("Farinha" in p for p in produtos)


def test_erp_consulta_estoque_por_produto():
    adapter = ErpMockAdapter()
    resultado = adapter.consultar("estoque", {"produto": "farinha"}, CTX)
    assert resultado.ok is True
    assert len(resultado.dados["itens"]) == 1


def test_erp_consulta_pedido_por_id():
    adapter = ErpMockAdapter()
    resultado = adapter.consultar("pedidos", {"id": "PED-1001"}, CTX)
    assert resultado.ok is True
    assert resultado.dados["pedido"]["cliente"] == "Mercadinho São José"


def test_erp_consulta_fluxo_caixa():
    adapter = ErpMockAdapter()
    resultado = adapter.consultar("fluxo_caixa", {}, CTX)
    assert resultado.ok is True
    assert "saldo_atual" in resultado.dados["fluxo_caixa"]


def test_erp_recurso_inexistente_retorna_erro_padronizado():
    adapter = ErpMockAdapter()
    resultado = adapter.consultar("folha_pagamento", {}, CTX)
    assert resultado.ok is False
    assert resultado.erro_padronizado == "RECURSO_NAO_ENCONTRADO"


def test_erp_criar_rascunho_de_pedido():
    adapter = ErpMockAdapter()
    resultado = adapter.criar_rascunho(
        "pedido",
        {"cliente": "Café Aurora", "itens": [{"produto": "Croissant", "quantidade": 24}]},
        CTX,
    )
    assert resultado.ok is True
    assert resultado.dados["rascunho_id"].startswith("RASC-PED-")


def test_erp_rascunho_sem_campos_obrigatorios():
    adapter = ErpMockAdapter()
    resultado = adapter.criar_rascunho("pedido", {"cliente": "Café Aurora"}, CTX)
    assert resultado.ok is False
    assert resultado.erro_padronizado == "CAMPO_OBRIGATORIO_AUSENTE"


def test_erp_alterar_nao_suportado_no_piloto():
    adapter = ErpMockAdapter()
    resultado = adapter.alterar("pedidos", "PED-1001", {"status": "cancelado"}, CTX)
    assert resultado.ok is False
    assert resultado.erro_padronizado == "OPERACAO_NAO_SUPORTADA"
