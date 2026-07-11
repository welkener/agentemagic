"""Testes da máquina de estados fiscal (agenteNF / Fiscus)."""
import pytest

from apps.agents.agente_nf.models import Intencao, TransicaoInvalida
from apps.audit.models import Auditoria


@pytest.fixture
def intencao(cliente):
    return Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="idem-teste-001",
        tipo_acao="emitir_nfse",
        payload={"valor": 500.0, "tomador": "João"},
    )


@pytest.mark.django_db
def test_fluxo_feliz_completo(intencao):
    assert intencao.estado == Intencao.Estado.RECEBIDO
    intencao.transicionar(Intencao.Estado.VALIDANDO)
    intencao.transicionar(Intencao.Estado.AGUARDANDO_APROVACAO)
    intencao.transicionar(Intencao.Estado.EMITINDO)
    intencao.transicionar(Intencao.Estado.CONCLUIDO)
    assert intencao.estado == Intencao.Estado.CONCLUIDO


@pytest.mark.django_db
def test_recebido_nao_pode_pular_para_emitindo(intencao):
    with pytest.raises(TransicaoInvalida):
        intencao.transicionar(Intencao.Estado.EMITINDO)
    # Estado permanece intacto após a tentativa inválida.
    intencao.refresh_from_db()
    assert intencao.estado == Intencao.Estado.RECEBIDO


@pytest.mark.django_db
def test_estado_terminal_nao_transiciona(intencao):
    intencao.transicionar(Intencao.Estado.CANCELADO)
    with pytest.raises(TransicaoInvalida):
        intencao.transicionar(Intencao.Estado.VALIDANDO)


@pytest.mark.django_db
def test_toda_transicao_e_auditada(intencao):
    antes = Auditoria.objects.filter(evento="intencao_fiscal_transicao").count()
    intencao.transicionar(Intencao.Estado.VALIDANDO, motivo="dados ok")
    intencao.transicionar(Intencao.Estado.REJEITADO, motivo="CNAE ausente")
    depois = Auditoria.objects.filter(evento="intencao_fiscal_transicao").count()
    assert depois == antes + 2

    ultimo = Auditoria.objects.filter(evento="intencao_fiscal_transicao").last()
    assert ultimo.dados["de"] == "VALIDANDO"
    assert ultimo.dados["para"] == "REJEITADO"
    assert ultimo.dados["motivo"] == "CNAE ausente"
