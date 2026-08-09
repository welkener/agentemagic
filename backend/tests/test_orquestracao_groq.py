"""Caminho LLM da orquestração (Groq via Pydantic AI), com o agente dublado.

Até aqui a suíte só exercitava o fallback por palavra-chave — `settings_test`
não tem `GROQ_API_KEY`, então **nenhum** teste passava pelo caminho que roda em
produção. Estes testes fecham isso sem gastar chamada real: dublam
`pydantic_ai.Agent`, que é a única fronteira externa.

O que se garante aqui:
1. o LLM decide o que o T0 não resolveu — e o núcleo respeita a decisão dele;
2. o prompt descreve todas as intenções do schema — um modelo de 8B não
   adivinha `cancelar_nota` a partir de "escolha do schema";
3. LLM fora do ar volta pro fallback determinístico, sem derrubar a mensagem;
4. o guard vale nos DOIS caminhos: o LLM nunca decide CNAE.

**Atualizado em 09/ago/2026 (DEC-08).** A escada inverteu: o T0 determinístico
atende primeiro e o LLM só vê o que sobrou. Mensagem inequívoca ("como está meu
estoque?") não chega mais aqui — e isso é a feature, não regressão. Os testes
abaixo passaram a usar mensagem ambígua de propósito, e o dublê agora conta
chamadas, porque "o LLM decidiu" e "o LLM nem foi chamado" precisavam deixar de
ser indistinguíveis.
"""
import pytest

from apps.agents.agente_nf.models import Intencao
from apps.core.orchestrator import (
    _PROMPT_ROTEADOR,
    DadosNotaExtraidos,
    IntencaoClassificada,
    Orquestrador,
)


class _Saida:
    def __init__(self, output):
        self.output = output


# O orquestrador usa DOIS agentes Groq (roteador e extrator de campos), e o
# dublê substitui os dois — por isso responde conforme o `output_type` pedido,
# em vez de devolver sempre uma intenção.
_RESPOSTA_COMBINADA = {
    "intencao": "desconhecida",
    "extracao": DadosNotaExtraidos(),
}


class _AgenteDublado:
    """Substitui `pydantic_ai.Agent` — registra como foi construído e devolve
    a resposta combinada, sem tocar a rede."""

    ultima_construcao: dict = {}
    chamadas_ao_roteador: int = 0

    def __init__(self, modelo, output_type=None, system_prompt=None, **kwargs):
        if output_type is IntencaoClassificada:
            type(self).chamadas_ao_roteador += 1
            type(self).ultima_construcao = {
                "modelo": modelo,
                "output_type": output_type,
                "system_prompt": system_prompt,
            }
            self._resposta = _RESPOSTA_COMBINADA["intencao"]
        else:
            self._resposta = _RESPOSTA_COMBINADA["extracao"]
        self._output_type = output_type

    def run_sync(self, mensagem):
        if isinstance(self._resposta, Exception):
            raise self._resposta
        if self._output_type is IntencaoClassificada:
            return _Saida(IntencaoClassificada(intencao=self._resposta))
        return _Saida(self._resposta)


@pytest.fixture
def groq_dublado(monkeypatch, settings):
    """Liga o caminho LLM e troca o Agent por um dublê."""
    settings.GROQ_API_KEY = "chave-de-teste"

    import pydantic_ai

    monkeypatch.setattr(pydantic_ai, "Agent", _AgenteDublado)
    _AgenteDublado.chamadas_ao_roteador = 0

    def responder(intencao, extracao=None):
        _RESPOSTA_COMBINADA["intencao"] = intencao
        _RESPOSTA_COMBINADA["extracao"] = extracao or DadosNotaExtraidos()

    yield responder
    _RESPOSTA_COMBINADA["intencao"] = "desconhecida"
    _RESPOSTA_COMBINADA["extracao"] = DadosNotaExtraidos()


# ---------------------------------------------------------------------------
# 1. Com chave, quem decide é o LLM
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_roteador_llm_decide_e_o_nucleo_obedece(cliente, groq_dublado):
    groq_dublado("consultar_contas_receber")

    # Mensagem sem evidência inequívoca: o T0 se cala e o LLM decide. Se a
    # resposta for de contas a receber, foi ele — nenhuma palavra da frase
    # apontaria para lá sozinha.
    resposta = Orquestrador().processar("me dá um panorama da firma aí", cliente)

    assert "receber" in resposta.lower()
    assert _AgenteDublado.chamadas_ao_roteador == 1


@pytest.mark.django_db
def test_t0_resolve_o_inequivoco_sem_gastar_llm(cliente, groq_dublado):
    """A inversão do DEC-08, medida onde ela se paga.

    O dublê está armado para responder "contas a receber". Se a resposta vier
    de estoque E o roteador não tiver sido construído nenhuma vez, então a
    mensagem foi resolvida de graça — que é o ponto inteiro da camada.
    """
    groq_dublado("consultar_contas_receber")

    resposta = Orquestrador().processar("como está meu estoque?", cliente)

    assert "estoque" in resposta.lower()
    assert _AgenteDublado.chamadas_ao_roteador == 0


@pytest.mark.django_db
def test_saudacao_nao_chega_ao_llm(cliente, groq_dublado):
    groq_dublado("desconhecida")

    resposta = Orquestrador().processar("bom dia", cliente)

    assert "Lumen" in resposta
    assert _AgenteDublado.chamadas_ao_roteador == 0


@pytest.mark.django_db
def test_llm_pode_rotear_para_as_intencoes_novas_de_nota(cliente, groq_dublado):
    """`consultar_nota`/`cancelar_nota` entraram no schema em 26/jul — este é o
    teste de que o caminho LLM realmente chega nelas."""
    groq_dublado("consultar_nota")
    assert "nenhuma nota emitida" in Orquestrador().processar("oi, tudo bem?", cliente)


@pytest.mark.django_db
def test_llm_roteia_cancelamento_para_a_fila_do_contador(cliente, groq_dublado):
    from apps.agents.agente_nf.services import confirmar_emissao

    nota = Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="groq-canc-1",
        tipo_acao="emitir_nfse",
        payload={
            "cnpj_prestador": cliente.cnpj,
            "cnae": cliente.cnae_padrao,
            "valor": 100.0,
            "descricao_servico": "Serviço",
            "tomador": "Ana",
        },
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )
    confirmar_emissao(nota, motivo="teste")

    groq_dublado("cancelar_nota")
    resposta = Orquestrador().processar("aquela nota saiu errada", cliente)

    assert "contador" in resposta
    assert Intencao.objects.filter(tipo_acao="cancelar_nfse").exists()


# ---------------------------------------------------------------------------
# 2. O prompt precisa descrever o schema inteiro
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_prompt_do_roteador_descreve_todas_as_intencoes_do_schema(cliente, groq_dublado):
    """Regressão: o prompt antigo só dizia "escolha entre as opções do schema".

    Intenção nova no `Literal` sem linha no prompt quebra aqui — que é onde dá
    pra consertar, e não no WhatsApp do cliente com um modelo de 8B chutando.
    """
    groq_dublado("desconhecida")
    Orquestrador().processar("qualquer coisa", cliente)

    prompt = _AgenteDublado.ultima_construcao["system_prompt"]
    assert prompt == _PROMPT_ROTEADOR

    do_schema = set(IntencaoClassificada.model_fields["intencao"].annotation.__args__)
    faltando = sorted(i for i in do_schema if i not in prompt)
    assert not faltando, f"intenções sem descrição no prompt do roteador: {faltando}"


def test_prompt_desempata_o_par_que_mais_confunde():
    """As três intenções de nota usam quase as mesmas palavras — o prompt tem
    que dizer explicitamente o que as separa."""
    assert "VERBO" in _PROMPT_ROTEADOR
    assert "quero emitir minha nota" in _PROMPT_ROTEADOR  # o exemplo ambíguo
    assert "escolha a CONSULTA" in _PROMPT_ROTEADOR  # viés seguro


@pytest.mark.django_db
def test_roteador_usa_o_schema_tipado_e_o_modelo_barato(cliente, groq_dublado):
    groq_dublado("desconhecida")
    Orquestrador().processar("oi", cliente)

    construcao = _AgenteDublado.ultima_construcao
    assert construcao["output_type"] is IntencaoClassificada
    assert "llama-3.1-8b-instant" in construcao["modelo"]


# ---------------------------------------------------------------------------
# 3. LLM fora do ar não derruba a mensagem
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_groq_fora_do_ar_cai_no_fallback_deterministico(cliente, groq_dublado):
    """Propriedade documentada do produto (requisitos §10.5): se a IA cair, o
    atendimento degrada, não para."""
    _RESPOSTA_COMBINADA["intencao"] = RuntimeError("groq indisponível")

    resposta = Orquestrador().processar("qual meu estoque?", cliente)

    assert "estoque" in resposta.lower()
    assert "Não consegui" not in resposta


# ---------------------------------------------------------------------------
# 4. O guard determinístico vale nos dois caminhos
# ---------------------------------------------------------------------------
_EXTRACAO_COMPLETA = DadosNotaExtraidos(
    tomador="Carlos", valor=200.0, descricao_servico="Consultoria"
)


@pytest.mark.django_db
def test_llm_nunca_decide_o_cnae(cliente, groq_dublado):
    """Regra inviolável da arquitetura: CNAE vem do cadastro, nunca do modelo.

    O dublê de extração devolve tomador/valor/descrição — tudo que o LLM pode
    propor. O CNAE do payload tem que vir do `Cliente`, mesmo assim.
    """
    cliente.cnae_padrao = "6201-5/01"
    cliente.save()

    groq_dublado("emitir_nota", extracao=_EXTRACAO_COMPLETA)
    Orquestrador().processar("emite uma nota de 200 reais pro Carlos, consultoria", cliente)

    intencao = Intencao.objects.get(tipo_acao="emitir_nfse")
    assert intencao.payload["cnae"] == "6201-5/01"
    assert intencao.payload["cnpj_prestador"] == cliente.cnpj  # também do cadastro
    assert intencao.payload["valor"] == 200.0  # este sim veio do LLM


@pytest.mark.django_db
def test_sem_cnae_no_cadastro_a_emissao_e_barrada(cliente, groq_dublado):
    """Nem com o LLM extraindo tudo certo: sem CNAE cadastrado, não emite."""
    cliente.cnae_padrao = ""
    cliente.save()

    groq_dublado("emitir_nota", extracao=_EXTRACAO_COMPLETA)
    resposta = Orquestrador().processar("emite nota de 200 pro Carlos", cliente)

    assert "CNAE" in resposta
    # A coleta é encerrada (cadastro incompleto não se resolve respondendo),
    # e nada chega a um estado emitível.
    assert not Intencao.objects.exclude(estado=Intencao.Estado.CANCELADO).exists()


@pytest.mark.django_db
def test_extracao_incompleta_pergunta_em_vez_de_inventar(cliente, groq_dublado):
    """LLM que não achou o valor não pode virar nota com valor zero."""
    groq_dublado(
        "emitir_nota", extracao=DadosNotaExtraidos(tomador="Carlos", descricao_servico="Consultoria")
    )
    resposta = Orquestrador().processar("emite uma nota pro Carlos", cliente)

    assert "valor" in resposta.lower()
    # A coleta fica em RECEBIDO guardando tomador/descrição (ver
    # tests/test_coleta_em_partes.py). O que não pode é virar nota com valor
    # zero — ou seja, avançar para além de RECEBIDO.
    assert not Intencao.objects.exclude(estado=Intencao.Estado.RECEBIDO).exists()
