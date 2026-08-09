"""
Medição de custo e teto de gasto por tenant — DEC-08, itens 2 e 3.

A DEC-08 diz, sobre o critério de R$ 0,60/cliente/mês: *"hoje o número não
existe"*. Estes testes são o que o faz existir e o que impede que ele volte a
ser estimativa — em especial o de que **modelo sem preço cadastrado não vira
custo zero em silêncio**, que é a forma silenciosa de o gate passar por engano.

O teto de gasto é testado pelo que ele **não** faz: nem no modo cortado o
atendimento para. Escritório acima do orçamento continua respondendo pelo
determinístico, porque o cliente final não tem nada a ver com a fatura do
contador dele.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.agents import llm
from apps.agents.contexto import SessionContext
from apps.audit.models import Auditoria
from apps.core.orchestrator import Orquestrador
from apps.observabilidade import orcamento, precos
from apps.observabilidade.models import ConsumoLLM
from apps.painel import metricas
from apps.tenants import rls


def ctx_de(cliente, **kwargs):
    return SessionContext.da_conversa(cliente=cliente, **kwargs)


# ---------------------------------------------------------------------------
# Preço
# ---------------------------------------------------------------------------
class TestPreco:
    def test_calcula_a_partir_da_tabela_e_da_cotacao(self, settings):
        settings.PRECOS_LLM = {"modelo-x": {"entrada": "1.00", "saida": "2.00"}}
        settings.COTACAO_USD_BRL = "5.00"

        # 1M de entrada = US$ 1; 1M de saída = US$ 2; total US$ 3 → R$ 15.
        custo = precos.custo_brl("modelo-x", tokens_entrada=1_000_000, tokens_saida=1_000_000)
        assert custo == Decimal("15.000000")

    def test_aceita_o_identificador_com_prefixo_do_provedor(self, settings):
        """O orquestrador chama `groq:modelo` (formato do Pydantic AI) e a tabela
        é escrita com o nome do modelo. Sem normalizar, o prefixo esquecido
        viraria custo zero."""
        settings.PRECOS_LLM = {"modelo-x": {"entrada": "1.00", "saida": "1.00"}}
        assert precos.custo_brl("groq:modelo-x", tokens_entrada=1000, tokens_saida=0) is not None

    def test_token_servido_do_cache_custa_metade(self, settings):
        settings.PRECOS_LLM = {"m": {"entrada": "1.00", "saida": "0"}}
        settings.COTACAO_USD_BRL = "1.00"

        cheio = precos.custo_brl("m", tokens_entrada=1_000_000, tokens_saida=0)
        metade = precos.custo_brl(
            "m", tokens_entrada=1_000_000, tokens_saida=0, tokens_cache_leitura=1_000_000
        )
        assert metade == cheio / 2

    def test_modelo_sem_preco_devolve_none_em_vez_de_zero(self, settings):
        """Zero somaria zero na fatura e o gate de R$ 0,60 passaria por engano —
        que é exatamente o erro que a medição existe para impedir."""
        settings.PRECOS_LLM = {}
        assert precos.custo_brl("modelo-que-ninguem-cadastrou", tokens_entrada=999, tokens_saida=9) is None

    def test_os_modelos_que_o_produto_usa_estao_na_tabela(self):
        """O par que o `llm.py` chama de verdade. Um modelo trocado no código e
        esquecido na tabela é custo invisível a partir do deploy seguinte."""
        for etapa in (llm.ETAPA_ROTEADOR, llm.ETAPA_EXTRACAO):
            for modo in (orcamento.MODO_NORMAL, orcamento.MODO_DEGRADADO):
                modelo = llm._ESCADA[etapa][modo]
                assert precos.custo_brl(modelo, tokens_entrada=1000, tokens_saida=10) is not None, (
                    f"{modelo} não tem preço em settings.PRECOS_LLM"
                )


# ---------------------------------------------------------------------------
# Registro de consumo
# ---------------------------------------------------------------------------
class _Uso:
    input_tokens = 1500
    output_tokens = 40
    cache_read_tokens = 0
    requests = 1
    tool_calls = 0


class _Resultado:
    def __init__(self, saida):
        self.output = saida

    def usage(self):
        return _Uso()


@pytest.fixture
def agente_dublado(monkeypatch, settings):
    """Troca o `Agent` do Pydantic AI e devolve o que o teste mandar."""
    settings.GROQ_API_KEY = "chave-de-teste"
    combinado = {"saida": None, "erro": None}

    class Dublê:
        def __init__(self, modelo, output_type=None, system_prompt=None, **kwargs):
            self.output_type = output_type

        def run_sync(self, mensagem):
            if combinado["erro"] is not None:
                raise combinado["erro"]
            return _Resultado(combinado["saida"] or self.output_type(intencao="desconhecida"))

    import pydantic_ai

    monkeypatch.setattr(pydantic_ai, "Agent", Dublê)
    return combinado


@pytest.mark.django_db
class TestRegistroDeConsumo:
    def test_chamada_ao_modelo_vira_linha_de_consumo(self, cliente, agente_dublado):
        Orquestrador().processar("e aí, me explica uma coisa", cliente)

        linha = ConsumoLLM.objects.get()
        assert linha.escritorio_id == cliente.escritorio_id
        assert linha.cliente_id == cliente.pk
        assert linha.etapa == ConsumoLLM.Etapa.ROTEADOR
        assert linha.tokens_entrada == 1500
        assert linha.tokens_saida == 40
        assert linha.custo_brl > 0
        assert linha.latencia_ms >= 0

    def test_chamada_que_falhou_tambem_e_gravada(self, cliente, agente_dublado):
        """Registrar só o sucesso faria o custo real parecer menor justamente
        nos dias ruins — e um provedor instável ficaria barato no relatório."""
        agente_dublado["erro"] = RuntimeError("provedor fora do ar")

        Orquestrador().processar("me explica uma coisa aí", cliente)

        linha = ConsumoLLM.objects.get()
        assert "provedor fora do ar" in linha.erro

    def test_t0_nao_gera_consumo(self, cliente, agente_dublado):
        """Saudação é o volume que a DEC-08 quer de graça. Se gerasse linha, a
        conta de custo por cliente incluiria o que não custou nada."""
        Orquestrador().processar("bom dia", cliente)
        assert not ConsumoLLM.objects.exists()

    def test_a_trilha_registra_latencia_e_camada_de_toda_mensagem(self, cliente, agente_dublado):
        """O p95 do gate precisa incluir as mensagens baratas: calculado só
        sobre as que chamaram o modelo, ele sairia pior que a experiência real."""
        Orquestrador().processar("bom dia", cliente)

        evento = Auditoria.objects.get(evento="orquestrador_mensagem_processada")
        assert evento.dados["camada"] == "t0"
        assert evento.dados["chamadas_llm"] == 0
        assert isinstance(evento.dados["latencia_ms"], int)


# ---------------------------------------------------------------------------
# Teto de gasto
# ---------------------------------------------------------------------------
def _gastar(escritorio, valor, cliente=None):
    with rls.escopo_irrestrito():
        ConsumoLLM.objects.create(
            escritorio=escritorio,
            cliente=cliente,
            etapa=ConsumoLLM.Etapa.ROTEADOR,
            modelo="groq:llama-3.1-8b-instant",
            custo_brl=Decimal(str(valor)),
        )
    orcamento.esquecer(escritorio)


@pytest.mark.django_db
class TestTetoDeGasto:
    def test_sem_limite_configurado_o_modo_e_normal(self, escritorio):
        """Padrão de propósito: ligar teto por omissão faria escritórios que já
        operam pararem de responder no dia do deploy."""
        _gastar(escritorio, "999.00")
        assert orcamento.modo(escritorio) == orcamento.MODO_NORMAL

    def test_oitenta_por_cento_do_limite_degrada(self, escritorio):
        escritorio.limite_gasto_mensal_brl = Decimal("10.00")
        escritorio.save()
        _gastar(escritorio, "8.50")

        assert orcamento.modo(escritorio) == orcamento.MODO_DEGRADADO

    def test_limite_atingido_corta_o_modelo(self, escritorio):
        escritorio.limite_gasto_mensal_brl = Decimal("10.00")
        escritorio.save()
        _gastar(escritorio, "10.00")

        assert orcamento.modo(escritorio) == orcamento.MODO_CORTADO

    def test_a_degradacao_troca_o_modelo_da_extracao_e_nao_o_do_roteador(self, escritorio):
        """O roteador já roda no modelo mais barato — degradá-lo não economiza
        nada. Quem cai é a extração, e o fluxo já sabe lidar com campo faltando:
        pergunta de novo."""
        escritorio.limite_gasto_mensal_brl = Decimal("10.00")
        escritorio.save()

        normal_extracao = llm.modelo_para(llm.ETAPA_EXTRACAO, escritorio)
        normal_roteador = llm.modelo_para(llm.ETAPA_ROTEADOR, escritorio)

        _gastar(escritorio, "8.50")
        assert llm.modelo_para(llm.ETAPA_EXTRACAO, escritorio) != normal_extracao
        assert llm.modelo_para(llm.ETAPA_ROTEADOR, escritorio) == normal_roteador

    def test_cortado_nao_chama_modelo_nenhum(self, escritorio):
        escritorio.limite_gasto_mensal_brl = Decimal("1.00")
        escritorio.save()
        _gastar(escritorio, "5.00")

        assert llm.modelo_para(llm.ETAPA_ROTEADOR, escritorio) is None
        assert llm.modelo_para(llm.ETAPA_EXTRACAO, escritorio) is None

    def test_atendimento_continua_mesmo_cortado(self, cliente, agente_dublado):
        """A propriedade que importa: teto estourado degrada, não emudece.

        O cliente final não tem nada a ver com a fatura do escritório — e já
        existe fallback testado para "sem LLM", que é o mesmo caminho.
        """
        escritorio = cliente.escritorio
        escritorio.limite_gasto_mensal_brl = Decimal("1.00")
        escritorio.save()
        _gastar(escritorio, "5.00")

        resposta = Orquestrador().processar("qual meu estoque?", cliente)

        assert "estoque" in resposta.lower()
        assert not ConsumoLLM.objects.filter(cliente=cliente).exists()

    def test_a_mensagem_cortada_e_marcada_como_fallback_na_trilha(self, cliente, agente_dublado):
        escritorio = cliente.escritorio
        escritorio.limite_gasto_mensal_brl = Decimal("1.00")
        escritorio.save()
        _gastar(escritorio, "5.00")

        Orquestrador().processar("me dá um panorama aí", cliente)

        evento = Auditoria.objects.filter(evento="orquestrador_mensagem_processada").first()
        assert evento.dados["camada"] == "fallback"

    def test_o_cliente_final_nunca_ouve_falar_do_teto(self, cliente, agente_dublado):
        """Dizer "seu escritório estourou o limite" expõe a relação comercial de
        terceiros e não dá ação nenhuma a um MEI. O sinal vai para a tela de
        Operação, que é onde alguém pode agir."""
        escritorio = cliente.escritorio
        escritorio.limite_gasto_mensal_brl = Decimal("1.00")
        escritorio.save()
        _gastar(escritorio, "5.00")

        resposta = Orquestrador().processar("qual meu estoque?", cliente)

        for palavra in ("limite", "teto", "orçamento", "gasto"):
            assert palavra not in resposta.lower()


# ---------------------------------------------------------------------------
# As contas que a tela de Operação mostra
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestMetricasDeOperacao:
    def test_consumo_do_mes_soma_por_modelo_e_por_cliente(self, contador_com_carteira):
        usuario, escritorio, cliente = contador_com_carteira
        _gastar(escritorio, "0.10", cliente)
        _gastar(escritorio, "0.05", cliente)
        _gastar(escritorio, "0.02")  # sem empresa resolvida ainda

        consumo = metricas.consumo_do_mes(usuario)

        assert consumo.total_brl == Decimal("0.170000")
        assert consumo.chamadas == 3
        # A conversa que ainda não identificou a empresa custa ao escritório e
        # não entra na atribuição por cliente — inventar o dono seria pior.
        assert consumo.por_cliente == [(cliente.nome, Decimal("0.150000"))]

    def test_o_criterio_de_aceite_olha_o_pior_cliente_e_nao_a_media(
        self, contador_com_carteira
    ):
        """Média abaixo do teto com um cliente acima não é critério atingido: é
        critério atingido em quase todo mundo, que é outra frase."""
        usuario, escritorio, cliente = contador_com_carteira
        _gastar(escritorio, "0.90", cliente)

        consumo = metricas.consumo_do_mes(usuario)
        assert consumo.dentro_do_criterio is False

    def test_ferramentas_mais_usadas_sai_ordenada_por_frequencia(
        self, contador_com_carteira
    ):
        """O "tool calls por tenant" da DEC-08, na forma que decide alguma coisa:
        capacidade cara e nunca usada sai do prompt e economiza token em toda
        mensagem."""
        from apps.agents import ferramentas as catalogo

        _, _, cliente = contador_com_carteira
        for _ in range(3):
            catalogo.executar("consultar_nota", ctx_de(cliente), "minhas notas")
        catalogo.executar("consultar_faturamento_acumulado", ctx_de(cliente), "quanto faturei")

        usuario = contador_com_carteira[0]
        assert metricas.ferramentas_mais_usadas(usuario) == [
            ("consultar_nota", 3),
            ("consultar_faturamento_acumulado", 1),
        ]

    def test_p95_espera_amostra_suficiente(self, contador_com_carteira):
        """Percentil sobre cinco mensagens é o maior valor de cinco."""
        usuario, escritorio, cliente = contador_com_carteira
        _registrar_latencias(cliente, [100] * 5)
        assert metricas.latencia_p95(usuario) is None

    def test_p95_devolve_uma_latencia_que_de_fato_aconteceu(self, contador_com_carteira):
        """Sem interpolação: um número real é mais fácil de investigar do que
        uma média ponderada entre duas medições."""
        usuario, escritorio, cliente = contador_com_carteira
        _registrar_latencias(cliente, list(range(1, 101)))

        p95 = metricas.latencia_p95(usuario)
        assert p95 == 95


def _registrar_latencias(cliente, valores):
    from apps.audit.services import registrar

    with rls.escopo_irrestrito():
        for valor in valores:
            registrar(
                "orquestrador_mensagem_processada",
                {"intencao": "conversa", "camada": "t0", "latencia_ms": valor},
                cliente=cliente,
            )


@pytest.fixture
def contador_com_carteira(db, cliente):
    """Um contador logado no escritório do `cliente` — o escopo das métricas."""
    from django.contrib.auth import get_user_model

    from apps.tenants.models import MembroEscritorio

    with rls.escopo_irrestrito():
        usuario = get_user_model().objects.create_user(
            username="contador.operacao", email="c@example.com", is_staff=True
        )
        MembroEscritorio.objects.create(usuario=usuario, escritorio=cliente.escritorio)
    return usuario, cliente.escritorio, cliente
