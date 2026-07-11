"""Testes do motor de tiers."""
import pytest

from apps.governance.tiers import CATALOGO_TIERS, tier_da_intencao, verificar_tier


@pytest.mark.django_db
def test_perfil_tier1_pode_consultar_e_rascunhar(cliente):
    perfil = cliente.perfil  # tier_maximo = 1
    assert verificar_tier(tier_da_intencao("consultar_estoque"), perfil) is True
    assert verificar_tier(tier_da_intencao("criar_rascunho"), perfil) is True
    assert verificar_tier(tier_da_intencao("emitir_nota"), perfil) is True


@pytest.mark.django_db
def test_perfil_tier1_nao_pode_tier2(cliente):
    perfil = cliente.perfil  # tier_maximo = 1
    assert verificar_tier(tier_da_intencao("alterar_pedido"), perfil) is False


@pytest.mark.django_db
def test_perfil_tier1_nao_pode_tier3(cliente):
    perfil = cliente.perfil
    assert verificar_tier(tier_da_intencao("excluir_pedido"), perfil) is False
    assert verificar_tier(tier_da_intencao("pagar_conta"), perfil) is False


def test_intencao_desconhecida_recebe_tier_mais_restritivo():
    assert tier_da_intencao("intencao_inventada") == 3


def test_catalogo_cobre_os_niveis_esperados():
    assert CATALOGO_TIERS["consultar_estoque"] == 0
    assert CATALOGO_TIERS["emitir_nota"] == 1
    assert CATALOGO_TIERS["criar_rascunho"] == 1
    assert CATALOGO_TIERS["alterar_pedido"] == 2
    assert CATALOGO_TIERS["excluir_pedido"] == 3
    assert CATALOGO_TIERS["pagar_conta"] == 3


def test_sem_perfil_recusa_tudo():
    assert verificar_tier(0, None) is False
