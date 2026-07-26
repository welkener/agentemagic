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


# ---------------------------------------------------------------------------
# O catálogo TEM que falar a mesma língua do orquestrador
# ---------------------------------------------------------------------------
def test_toda_intencao_que_o_orquestrador_emite_esta_no_catalogo():
    """Regressão do bug de 26/jul/2026.

    O catálogo dizia `consultar_contas`; o orquestrador emitia
    `consultar_contas_receber`/`_pagar`. Nome que não bate cai no fail-safe
    Tier 3, então "quanto tenho a receber?" era recusado em produção como se
    fosse operação destrutiva. Este teste cruza as duas listas — qualquer
    intenção nova sem entrada no catálogo quebra aqui, não no WhatsApp do
    cliente.
    """
    from apps.core.orchestrator import _INTENCOES_VALIDAS

    emitidas = {i for i in _INTENCOES_VALIDAS if i != "desconhecida"}
    faltando = sorted(emitidas - set(CATALOGO_TIERS))
    assert not faltando, f"intenções sem tier (cairiam no fail-safe 3): {faltando}"


@pytest.mark.django_db
def test_consultas_financeiras_sao_tier_zero(cliente):
    """Eram as duas que o bug derrubava."""
    perfil = cliente.perfil  # tier_maximo = 1
    assert verificar_tier(tier_da_intencao("consultar_contas_receber"), perfil) is True
    assert verificar_tier(tier_da_intencao("consultar_contas_pagar"), perfil) is True


def test_cancelar_nota_continua_sendo_tier_maximo():
    """Cancelar documento fiscal é destrutivo — nunca pode virar Tier 0/1 sem
    decisão explícita (o fluxo passa pelo contador, ver agente_nf/services.py)."""
    assert CATALOGO_TIERS["cancelar_nota"] == 3
    assert CATALOGO_TIERS["consultar_nota"] == 0
