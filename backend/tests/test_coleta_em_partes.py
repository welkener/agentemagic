"""
Nota pedida em partes — o defeito visto na conversa real de 27/jul/2026.

Transcrição do que aconteceu (trilha de auditoria do servidor de teste):

    cliente> você pode emitir uma nota fiscal para mim
    agente > Ainda preciso de: tomador, valor, descrição do serviço
    cliente> Emitir pra 01337862347 serviços de ti
    agente > Ainda preciso de: valor                     <- tinha 2 dos 3 campos
    cliente> 100 reais
    agente > Ainda preciso de: tomador, descrição        <- ESQUECEU os 2

Cada mensagem era extraída isoladamente e nada era guardado: a `Intencao` só
nascia quando os três campos chegavam juntos. Quem responde em partes — que é
como se conversa no WhatsApp — entrava num loop de pedidos.

E na sequência, um segundo defeito:

    cliente> Cancelar, vou consultar outro servico
    agente > Não encontrei nenhuma nota emitida que possa ser cancelada 🤔

"Cancelar" foi lido como "cancelar uma nota já emitida" porque não havia
nenhum fluxo em aberto para desistir.

Os testes abaixo dublam a extração (não chamam a Groq) — o que está sob teste é
a memória entre turnos, não a qualidade do LLM.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.core import orchestrator as orq
from apps.core.orchestrator import DadosNotaExtraidos, Orquestrador


@pytest.fixture
def extracao_dublada(monkeypatch):
    """Mapeia mensagem -> campos extraídos, como a Groq faria."""
    roteiro: dict[str, DadosNotaExtraidos] = {}

    def fake_extrair(self, mensagem):
        return roteiro.get(mensagem.strip().lower(), DadosNotaExtraidos())

    monkeypatch.setattr(Orquestrador, "_extrair_dados_nota", fake_extrair)
    return roteiro


@pytest.fixture
def roteador_dublado(monkeypatch):
    """Classificação determinística, sem depender do LLM nem das palavras-chave."""
    rotas: dict[str, str] = {}

    def fake_classificar(self, mensagem):
        return rotas.get(mensagem.strip().lower(), "desconhecida")

    monkeypatch.setattr(Orquestrador, "_classificar_intencao", fake_classificar)
    return rotas


@pytest.mark.django_db
class TestColetaEmPartes:
    def test_conversa_real_que_falhou(self, cliente, extracao_dublada, roteador_dublado):
        """A transcrição do topo do arquivo, turno a turno."""
        roteador_dublado["você pode emitir uma nota fiscal para mim"] = "emitir_nota"
        extracao_dublada["você pode emitir uma nota fiscal para mim"] = DadosNotaExtraidos()
        extracao_dublada["emitir pra 01337862347 serviços de ti"] = DadosNotaExtraidos(
            tomador="01337862347", descricao_servico="serviços de ti"
        )
        extracao_dublada["100 reais"] = DadosNotaExtraidos(valor=100.0)

        agente = Orquestrador()

        r1 = agente.processar("você pode emitir uma nota fiscal para mim", cliente, message_id="m1")
        assert "tomador" in r1 and "valor" in r1

        r2 = agente.processar("Emitir pra 01337862347 serviços de ti", cliente, message_id="m2")
        assert "valor" in r2
        assert "tomador" not in r2  # já foi dito, não pede de novo

        r3 = agente.processar("100 reais", cliente, message_id="m3")

        # Antes, aqui vinha "Ainda preciso de: tomador, descrição do serviço".
        assert "Confirma a emissão" in r3
        assert "01337862347" in r3
        assert "R$ 100.00" in r3
        assert "serviços de ti" in r3

    def test_uma_intencao_so_para_a_conversa_inteira(self, cliente, extracao_dublada, roteador_dublado):
        """Três mensagens não podem virar três notas."""
        roteador_dublado["emite uma nota"] = "emitir_nota"
        extracao_dublada["emite uma nota"] = DadosNotaExtraidos()
        extracao_dublada["pro joao"] = DadosNotaExtraidos(tomador="Joao")
        extracao_dublada["300 de consultoria"] = DadosNotaExtraidos(
            valor=300.0, descricao_servico="consultoria"
        )

        agente = Orquestrador()
        agente.processar("emite uma nota", cliente, message_id="a1")
        agente.processar("pro Joao", cliente, message_id="a2")
        agente.processar("300 de consultoria", cliente, message_id="a3")

        assert Intencao.objects.filter(cliente=cliente, tipo_acao="emitir_nfse").count() == 1

    def test_valor_novo_corrige_o_anterior(self, cliente, extracao_dublada, roteador_dublado):
        """"Na verdade são 200" tem que sobrescrever, não ser ignorado."""
        roteador_dublado["emite nota de 100 pro joao, consultoria"] = "emitir_nota"
        extracao_dublada["emite nota de 100 pro joao, consultoria"] = DadosNotaExtraidos(
            tomador="Joao", valor=100.0, descricao_servico="consultoria"
        )
        extracao_dublada["na verdade são 200"] = DadosNotaExtraidos(valor=200.0)

        agente = Orquestrador()
        agente.processar("emite nota de 100 pro joao, consultoria", cliente, message_id="b1")
        # Já foi para AGUARDANDO_APROVACAO, então a correção cai na confirmação —
        # este teste cobre a coleta, então parte de uma coleta incompleta:
        Intencao.objects.filter(cliente=cliente).update(estado=Intencao.Estado.RECEBIDO)
        Intencao.objects.filter(cliente=cliente).update(payload={"tomador": "Joao"})

        resposta = agente.processar("na verdade são 200", cliente, message_id="b2")

        intencao = Intencao.objects.get(cliente=cliente)
        assert intencao.payload["valor"] == 200.0
        assert "descrição do serviço" in resposta  # ainda falta, e é só isso que pede


@pytest.mark.django_db
class TestDesistirNoMeio:
    def test_cancelar_durante_a_coleta_abandona_a_nota(
        self, cliente, extracao_dublada, roteador_dublado
    ):
        """O segundo defeito da conversa real: "cancelar" virava busca por nota
        emitida, e o cliente ouvia "não encontrei nenhuma nota"."""
        roteador_dublado["emite uma nota"] = "emitir_nota"
        extracao_dublada["emite uma nota"] = DadosNotaExtraidos()

        agente = Orquestrador()
        agente.processar("emite uma nota", cliente, message_id="c1")

        resposta = agente.processar("Cancelar, vou consultar outro servico", cliente, message_id="c2")

        assert "cancelei" in resposta.lower()
        assert "não encontrei nenhuma nota" not in resposta.lower()
        assert Intencao.objects.get(cliente=cliente).estado == Intencao.Estado.CANCELADO

    def test_mudar_de_assunto_solta_o_cliente_do_fluxo(
        self, cliente, extracao_dublada, roteador_dublado
    ):
        """Perguntar outra coisa no meio não pode prender o cliente na nota."""
        roteador_dublado["emite uma nota"] = "emitir_nota"
        roteador_dublado["qual meu estoque?"] = "consultar_estoque"
        extracao_dublada["emite uma nota"] = DadosNotaExtraidos()
        extracao_dublada["qual meu estoque?"] = DadosNotaExtraidos()

        agente = Orquestrador()
        agente.processar("emite uma nota", cliente, message_id="d1")

        resposta = agente.processar("qual meu estoque?", cliente, message_id="d2")

        assert "preciso de" not in resposta.lower()
        assert Intencao.objects.get(cliente=cliente).estado == Intencao.Estado.CANCELADO


@pytest.mark.django_db
class TestColetaExpira:
    def test_coleta_velha_nao_contamina_pedido_novo(
        self, cliente, extracao_dublada, roteador_dublado
    ):
        """Mesclar fora do contexto emitiria para o tomador errado, sem ninguém pedir."""
        roteador_dublado["emite uma nota"] = "emitir_nota"
        roteador_dublado["emite nota de 300, consultoria"] = "emitir_nota"
        extracao_dublada["emite uma nota"] = DadosNotaExtraidos(tomador="Maria")
        extracao_dublada["emite nota de 300, consultoria"] = DadosNotaExtraidos(
            valor=300.0, descricao_servico="consultoria"
        )

        agente = Orquestrador()
        agente.processar("emite uma nota", cliente, message_id="e1")

        antiga = Intencao.objects.get(cliente=cliente)
        vencida = timezone.now() - timedelta(minutes=orq.COLETA_TTL_MINUTOS + 5)
        Intencao.objects.filter(pk=antiga.pk).update(atualizado_em=vencida)

        resposta = agente.processar("emite nota de 300, consultoria", cliente, message_id="e2")

        antiga.refresh_from_db()
        assert antiga.estado == Intencao.Estado.CANCELADO
        assert "Maria" not in resposta  # a nota nova não herdou o tomador velho

        nova = Intencao.objects.exclude(pk=antiga.pk).get(cliente=cliente)
        assert nova.payload.get("tomador") in (None, "")
