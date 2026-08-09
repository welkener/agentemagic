"""
T0 na frente do LLM (DEC-08) — a camada determinística deixou de ser emergência.

Antes: Groq atendia primeiro, palavra-chave era rede de queda. Agora o T0
atende primeiro e o LLM só vê o que sobrou. A inversão vale dinheiro e segundos
no pico dos dias 5–10, quando o volume é justamente mensagem repetitiva.

O risco da inversão tem nome, e é o que a maior parte destes testes protege:
**um determinístico açodado emite nota fiscal por engano.** Por isso o
classificador do T0 é estrito — cala-se na dúvida — enquanto o de fallback
continua permissivo, porque ele só roda com o LLM fora do ar, e lá chutar é
melhor que recusar.

O último teste mede a cobertura do T0 sobre um corpus. Ele **não** é o gate do
Sprint 1: aquele pede 100 mensagens reais, e as reais estão cifradas por titular
(`apps/audit/conteudo.py`), fora do alcance de uma suíte. O que o número aqui
diz é que a camada cobre a rotina que sabemos existir; o número de verdade sai
da trilha em produção, pelo campo `camada` gravado em cada mensagem.
"""
import pytest

from apps.audit.models import Auditoria
from apps.core import t0
from apps.core.orchestrator import Orquestrador


# ---------------------------------------------------------------------------
# Respostas prontas
# ---------------------------------------------------------------------------
class TestRespostaPronta:
    @pytest.mark.parametrize(
        "mensagem",
        ["oi", "Olá", "bom dia", "BOA TARDE", "e aí", "menu", "ajuda", "o que você faz?"],
    )
    def test_saudacao_e_menu_saem_prontos(self, mensagem):
        assert "Lumen" in (t0.responder(mensagem) or "")

    @pytest.mark.parametrize("mensagem", ["obrigado", "Obrigada!", "vlw", "valeu", "👍"])
    def test_agradecimento_tem_resposta_curta(self, mensagem):
        resposta = t0.responder(mensagem)
        assert resposta is not None
        assert "Lumen" not in resposta, "agradecimento não é hora de repetir o menu"

    @pytest.mark.parametrize("mensagem", ["tchau", "até mais", "flw"])
    def test_despedida_encerra(self, mensagem):
        assert t0.responder(mensagem) is not None

    @pytest.mark.parametrize(
        "mensagem",
        [
            "emite uma nota de 500 pro João",
            "quanto eu faturei esse mês?",
            "preciso cancelar aquela nota de ontem",
            "",
        ],
    )
    def test_o_que_precisa_de_dado_nao_tem_resposta_pronta(self, mensagem):
        """Resposta fixa nunca pode tocar dado do cliente — isso passa pela
        intenção, que é escopada por tenant e auditada."""
        assert t0.responder(mensagem) is None


# ---------------------------------------------------------------------------
# Classificação estrita
# ---------------------------------------------------------------------------
class TestClassificacaoEstrita:
    @pytest.mark.parametrize(
        "mensagem,esperado",
        [
            ("emite uma nota de 500 pro João", "emitir_nota"),
            ("preciso fazer uma nfs-e", "emitir_nota"),
            ("quero tirar uma nota", "emitir_nota"),
            ("quais notas eu emiti?", "consultar_nota"),
            ("minhas últimas notas", "consultar_nota"),
            ("cadê a nota de ontem", "consultar_nota"),
            ("cancela a nota que saiu errada", "cancelar_nota"),
            ("quero anular a última nfs-e", "cancelar_nota"),
            ("como está meu estoque?", "consultar_estoque"),
            ("o que eu tenho a receber", "consultar_contas_receber"),
            ("minhas contas a pagar", "consultar_contas_pagar"),
            ("me mostra o fluxo de caixa", "consultar_fluxo_caixa"),
            ("relatório de vendas", "consultar_pedido"),
        ],
    )
    def test_inequivoco_e_classificado(self, mensagem, esperado):
        assert t0.classificar(mensagem) == esperado

    @pytest.mark.parametrize(
        "mensagem",
        [
            "minha nota",  # consultar ou emitir? o verbo não está lá
            "sobre aquela nota",
            "nota",
            "e a nota do mês passado, hein",
            "preciso resolver aquilo do mês",
            "meu contador falou pra eu te procurar",
            "e aí, como é que faz?",
        ],
    )
    def test_ambiguo_sobe_para_o_llm(self, mensagem):
        """`None` é a resposta certa aqui, e é a mais importante do módulo.

        Se o T0 chutasse "emitir_nota" em "minha nota", uma mensagem vaga
        passaria a abrir emissão de documento fiscal sem o LLM sequer ver.
        """
        assert t0.classificar(mensagem) is None

    def test_cancelar_ganha_de_emitir_que_ganha_de_consultar(self):
        """A ordem de precedência é a mesma do fallback — nunca duas regras
        diferentes para a mesma frase em dois lugares do sistema."""
        assert t0.classificar("quero cancelar minha nota emitida") == "cancelar_nota"
        assert t0.classificar("quero emitir minha nota") == "emitir_nota"

    def test_acento_e_caixa_nao_mudam_nada(self):
        assert t0.classificar("QUAIS NOTAS EU EMITI?") == "consultar_nota"
        assert t0.classificar("me mostra o fluxo de caixa") == t0.classificar(
            "Me mostra o FLUXO DE CAIXA!"
        )


# ---------------------------------------------------------------------------
# Integração com o orquestrador
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestNaEscada:
    def test_camada_vai_para_a_trilha(self, cliente):
        """Sem isto a meta do DEC-08 continuaria sendo estimativa."""
        Orquestrador().processar("como está meu estoque?", cliente)

        evento = (
            Auditoria.objects.filter(evento="orquestrador_mensagem_processada")
            .order_by("-criado_em")
            .first()
        )
        assert evento.dados["camada"] == "t0"

    def test_sem_groq_o_ambiguo_cai_no_fallback_permissivo(self, cliente):
        """`settings_test` não tem chave: mensagem vaga sobre nota não pode
        ficar sem resposta só porque o T0 se calou."""
        Orquestrador().processar("minha nota", cliente)

        evento = (
            Auditoria.objects.filter(evento="orquestrador_mensagem_processada")
            .order_by("-criado_em")
            .first()
        )
        assert evento.dados["camada"] == "fallback"

    def test_confirmacao_pendente_ganha_do_t0(self, cliente, monkeypatch):
        """"ok" no meio de uma emissão é confirmação, não simpatia.

        É a razão de o T0 entrar DEPOIS da confirmação e da coleta: um "beleza"
        respondido com "que bom ter ajudado" deixaria a nota pendurada.
        """
        from apps.core.orchestrator import DadosNotaExtraidos

        monkeypatch.setattr(
            Orquestrador,
            "_extrair_dados_nota",
            lambda self, m: DadosNotaExtraidos(
                tomador="Joao", valor=100.0, descricao_servico="consultoria"
            ),
        )
        agente = Orquestrador()
        agente.processar("emite nota de 100 pro Joao, consultoria", cliente)

        resposta = agente.processar("ok", cliente)

        assert "que bom ter ajudado" not in resposta.lower()


# ---------------------------------------------------------------------------
# Cobertura
# ---------------------------------------------------------------------------
# Corpus escrito a partir do que a operação já viu: as mensagens do teste com
# número real de 27/jul, o vocabulário do smoke test e as variações que o
# aprendizado do roteador (`core.ExemploIntencao`) foi cadastrando. É amostra
# de conveniência, não amostra estatística — está aqui para flagrar regressão
# de cobertura, não para declarar a meta batida.
CORPUS = [
    "oi", "bom dia", "boa tarde", "menu", "ajuda", "o que você faz?",
    "obrigado", "valeu", "vlw", "tchau", "👍",
    "emite uma nota de 500 pro João", "preciso fazer uma nfs-e",
    "quero tirar uma nota de 1200 de consultoria",
    "quais notas eu emiti?", "minhas últimas notas", "cadê a nota de ontem",
    "cancela a nota que saiu errada",
    "como está meu estoque?", "estoque", "o que eu tenho a receber",
    "minhas contas a pagar", "me mostra o fluxo de caixa",
    "relatório de vendas", "quanto eu vendi esse mês",
    # A metade que o T0 tem de recusar — e que precisa estar no corpus, senão a
    # taxa mede um universo escolhido para dar certo.
    "minha nota", "sobre aquela nota", "e aí, como é que faz?",
    "meu contador falou pra eu te procurar", "preciso resolver aquilo do mês",
    "500 reais", "pro João", "consultoria",
    "quanto eu já faturei no ano?", "tô perto do teto do MEI?",
    "esqueci meu login", "vocês emitem nota de produto também?",
]

META_DEC08 = 0.40


def test_cobertura_do_t0_no_corpus_conhecido():
    resolvidas = sum(
        1 for m in CORPUS if t0.responder(m) is not None or t0.classificar(m) is not None
    )
    taxa = resolvidas / len(CORPUS)
    assert taxa >= META_DEC08, (
        f"T0 resolveu {taxa:.0%} do corpus ({resolvidas}/{len(CORPUS)}); "
        f"o DEC-08 mira {META_DEC08:.0%}. Se a queda for proposital, ajuste a meta "
        "junto com a decisão — não só este número."
    )
