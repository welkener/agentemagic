"""Testes do orquestrador conectado: roteamento, tiers e emissão de NFS-e.

Sem GROQ_API_KEY (settings_test.py), o orquestrador sempre cai no roteamento
determinístico por palavra-chave — os testes aqui exercitam esse caminho, que
é o mesmo caminho de fallback usado em produção se a API do Groq cair.
"""
import pytest

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.core.orchestrator import Orquestrador


@pytest.mark.django_db
def test_cliente_desconhecido_recebe_orientacao_de_cadastro():
    resposta = Orquestrador().processar("oi", cliente=None)
    assert "não encontrei seu cadastro" in resposta


@pytest.mark.django_db
def test_consulta_erp_continua_funcionando(cliente):
    resposta = Orquestrador().processar("qual meu estoque?", cliente)
    assert "estoque" in resposta.lower()


@pytest.mark.django_db
def test_emitir_nota_pede_campos_faltantes(cliente):
    resposta = Orquestrador().processar("quero emitir uma nota", cliente)
    assert "tomador" in resposta
    assert "valor" in resposta
    assert Intencao.objects.count() == 0


@pytest.mark.django_db
def test_emitir_nota_sem_tier_e_recusada(cliente):
    cliente.perfil.tier_maximo = 0
    cliente.perfil.save()
    resposta = Orquestrador().processar("emite uma nota", cliente)
    assert "não está liberada" in resposta
    assert Intencao.objects.count() == 0


@pytest.mark.django_db
def test_emitir_nota_sem_cnae_bloqueia(cliente):
    cliente.cnae_padrao = ""
    cliente.save()
    # Sem GROQ, a extração cai vazia, então o pedido de campos vem primeiro —
    # simulamos o caso de dados completos monkeypatchando a extração.
    orq = Orquestrador()
    from apps.core.orchestrator import DadosNotaExtraidos

    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="João", valor=500.0, descricao_servico="Corte de cabelo"
    )
    resposta = orq.processar("emite nota de 500 pro João, corte de cabelo", cliente)
    assert "CNAE" in resposta
    assert Intencao.objects.count() == 0


@pytest.mark.django_db
def test_fluxo_completo_de_emissao_com_confirmacao(cliente):
    orq = Orquestrador()
    from apps.core.orchestrator import DadosNotaExtraidos

    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="João", valor=500.0, descricao_servico="Corte de cabelo"
    )

    resposta = orq.processar("emite nota de 500 pro João, corte de cabelo", cliente, message_id="wamid.001")
    assert "Confirma a emissão" in resposta
    intencao = Intencao.objects.get()
    assert intencao.estado == Intencao.Estado.AGUARDANDO_APROVACAO
    assert intencao.chave_idempotencia == "nfse-wamid.001"

    resposta = orq.processar("sim", cliente)
    assert "Nota emitida com sucesso" in resposta
    intencao.refresh_from_db()
    assert intencao.estado == Intencao.Estado.CONCLUIDO

    eventos = list(Auditoria.objects.filter(evento="intencao_fiscal_transicao").values_list("dados", flat=True))
    caminho = [e["para"] for e in eventos]
    assert caminho == ["VALIDANDO", "AGUARDANDO_APROVACAO", "EMITINDO", "CONCLUIDO"]


@pytest.mark.django_db
def test_fluxo_de_emissao_cancelado(cliente):
    orq = Orquestrador()
    from apps.core.orchestrator import DadosNotaExtraidos

    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="João", valor=500.0, descricao_servico="Corte de cabelo"
    )
    orq.processar("emite nota de 500 pro João, corte de cabelo", cliente)
    resposta = orq.processar("não, cancela", cliente)
    assert "cancelei" in resposta.lower()
    assert Intencao.objects.get().estado == Intencao.Estado.CANCELADO


@pytest.mark.django_db
def test_retry_do_mesmo_message_id_nao_duplica_intencao(cliente):
    orq = Orquestrador()
    from apps.core.orchestrator import DadosNotaExtraidos

    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="João", valor=500.0, descricao_servico="Corte de cabelo"
    )
    orq.processar("emite nota de 500 pro João", cliente, message_id="wamid.retry")

    # Fecha a confirmação para simular reprocessamento após conclusão.
    intencao = Intencao.objects.get()
    intencao.transicionar(Intencao.Estado.EMITINDO)
    intencao.transicionar(Intencao.Estado.CONCLUIDO)

    resposta = orq.processar("emite nota de 500 pro João", cliente, message_id="wamid.retry")
    assert "já foi emitida" in resposta
    assert Intencao.objects.count() == 1


@pytest.mark.django_db
def test_confirmacao_com_resposta_ambigua_pede_esclarecimento(cliente):
    orq = Orquestrador()
    from apps.core.orchestrator import DadosNotaExtraidos

    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="João", valor=500.0, descricao_servico="Corte de cabelo"
    )
    orq.processar("emite nota de 500 pro João", cliente)
    resposta = orq.processar("talvez", cliente)
    assert "Não entendi" in resposta
    assert Intencao.objects.get().estado == Intencao.Estado.AGUARDANDO_APROVACAO
