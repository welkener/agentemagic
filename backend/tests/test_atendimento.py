"""
Chamados e pedidos de atendimento abertos pela conversa.

São as duas ferramentas de escrita do Sprint 2 que não dependem de ERP — as
primeiras que um escritório novo tem funcionando no dia um. O que estes testes
protegem é sobretudo o que elas **não** prometem:

- protocolo vem do núcleo, nunca do modelo (protocolo alucinado é o pior tipo:
  o cliente anota, cobra por ele e ninguém encontra);
- data preferida não é horário confirmado — o sistema não conhece a agenda do
  escritório;
- e o chamado aparece na fila do contador, senão "a equipe já está vendo" é
  mentira que o cliente descobre pelo silêncio.
"""
from datetime import date

import pytest

from apps.agents import ferramentas
from apps.agents.contexto import SessionContext
from apps.atendimento import services as atendimento
from apps.atendimento.models import Solicitacao
from apps.audit.conteudo import CONTEUDO_ELIMINADO, eliminar_conteudo_do_titular
from apps.painel import metricas
from apps.tenants import rls


def ctx_de(cliente, **kwargs):
    return SessionContext.da_conversa(cliente=cliente, **kwargs)


# ---------------------------------------------------------------------------
# Protocolo
# ---------------------------------------------------------------------------
class TestProtocolo:
    def test_formato_legivel_e_ordenavel_por_data(self):
        protocolo = atendimento.gerar_protocolo(
            Solicitacao.Tipo.CHAMADO, quando=date(2026, 8, 9)
        )
        assert protocolo.startswith("CH-20260809-")

    def test_nao_usa_caracteres_que_se_confundem_ao_telefone(self):
        """O protocolo é lido em voz alta e digitado de volta. `0`/`O` e `1`/`I`
        custam a ligação "não acho esse protocolo"."""
        sufixo = atendimento.gerar_protocolo(Solicitacao.Tipo.CHAMADO).split("-")[-1]
        assert not set(sufixo) & set("O0I1AEIOU")

    def test_nao_e_sequencial(self):
        """Protocolo sequencial conta ao cliente quantos chamados o escritório
        recebe — informação comercial do escritório."""
        emitidos = {atendimento.gerar_protocolo(Solicitacao.Tipo.CHAMADO) for _ in range(20)}
        assert len(emitidos) == 20


# ---------------------------------------------------------------------------
# Leitura de data
# ---------------------------------------------------------------------------
class TestLeituraDeData:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("pode ser hoje?", date(2026, 8, 9)),
            ("amanhã de manhã", date(2026, 8, 10)),
            ("amanha cedo", date(2026, 8, 10)),
            ("depois de amanhã", date(2026, 8, 11)),
            ("dia 15/09", date(2026, 9, 15)),
            ("15/09/2027", date(2027, 9, 15)),
        ],
    )
    def test_le_o_que_esta_claro(self, texto, esperado):
        assert atendimento.interpretar_data(texto, hoje=date(2026, 8, 9)) == esperado

    @pytest.mark.parametrize(
        "texto", ["semana que vem", "qualquer dia desses", "quando der", "32/13", ""]
    )
    def test_nao_adivinha_o_que_nao_esta_claro(self, texto):
        """Errar aqui é caro (o cliente aparece no dia errado) e barato de
        evitar (uma pergunta a mais)."""
        assert atendimento.interpretar_data(texto, hoje=date(2026, 8, 9)) is None

    def test_dia_sem_ano_que_ja_passou_vai_para_o_proximo(self):
        """"dia 3" em 30/dez é janeiro, não um atendimento no passado."""
        assert atendimento.interpretar_data("dia 3/1", hoje=date(2026, 12, 30)) == date(
            2027, 1, 3
        )


# ---------------------------------------------------------------------------
# As ferramentas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAbrirChamado:
    def test_cria_solicitacao_e_devolve_o_protocolo(self, cliente):
        resposta = ferramentas.executar(
            "abrir_chamado", ctx_de(cliente), "tenho um problema com a folha"
        )

        solicitacao = Solicitacao.objects.get()
        assert solicitacao.tipo == Solicitacao.Tipo.CHAMADO
        assert solicitacao.cliente == cliente
        assert solicitacao.estado == Solicitacao.Estado.ABERTA
        assert solicitacao.protocolo in resposta

    def test_o_assunto_e_recortado_da_frase_do_cliente(self, cliente):
        """Resumo alucinado priorizaria errado na fila do contador. Recortar é
        pior redação e informação verdadeira."""
        ferramentas.executar("abrir_chamado", ctx_de(cliente), "a guia do DAS veio errada")
        assert Solicitacao.objects.get().assunto == "a guia do DAS veio errada"

    def test_registra_quem_pediu_quando_a_pessoa_e_conhecida(self, cliente):
        """Sob DEC-03, "o sócio pediu" e "o financeiro pediu" levam o contador a
        respostas diferentes."""
        with rls.escopo_irrestrito():
            vinculo = cliente.vincular_usuario("5511977776666", nome="Financeiro")

        ferramentas.executar(
            "abrir_chamado", ctx_de(cliente, usuario=vinculo.usuario), "preciso de ajuda"
        )
        assert Solicitacao.objects.get().usuario_id == vinculo.usuario_id


@pytest.mark.django_db
class TestAgendarAtendimento:
    def test_registra_a_preferencia_de_data(self, cliente):
        ferramentas.executar(
            "agendar_atendimento", ctx_de(cliente), "posso falar com o contador amanhã?"
        )
        solicitacao = Solicitacao.objects.get()
        assert solicitacao.tipo == Solicitacao.Tipo.ATENDIMENTO
        assert solicitacao.preferencia_data is not None

    def test_nunca_confirma_horario(self, cliente):
        """O sistema não conhece a agenda do escritório. "Agendado para quinta"
        assumiria disponibilidade que ele não tem meio de checar, e quem perde a
        viagem é o cliente."""
        resposta = ferramentas.executar(
            "agendar_atendimento", ctx_de(cliente), "quero marcar amanhã"
        )
        assert "confirma o horário é a equipe" in resposta
        assert "agendado para" not in resposta.lower()

    def test_sem_data_clara_diz_que_a_equipe_combina(self, cliente):
        resposta = ferramentas.executar(
            "agendar_atendimento", ctx_de(cliente), "queria conversar qualquer dia desses"
        )
        assert Solicitacao.objects.get().preferencia_data is None
        assert "combinar o melhor horário" in resposta


# ---------------------------------------------------------------------------
# A promessa do outro lado
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestFilaDoContador:
    def test_solicitacao_aberta_aparece_para_o_escritorio_dono(self, cliente):
        from django.contrib.auth import get_user_model

        from apps.tenants.models import MembroEscritorio

        ferramentas.executar("abrir_chamado", ctx_de(cliente), "socorro")

        with rls.escopo_irrestrito():
            contador = get_user_model().objects.create_user(
                username="contador.fila", is_staff=True
            )
            MembroEscritorio.objects.create(usuario=contador, escritorio=cliente.escritorio)

        abertas = metricas.solicitacoes_abertas(contador)
        assert [s.cliente_id for s in abertas] == [cliente.pk]

    def test_resolvida_sai_da_fila(self, cliente):
        from django.contrib.auth import get_user_model

        from apps.tenants.models import MembroEscritorio

        ferramentas.executar("abrir_chamado", ctx_de(cliente), "socorro")
        Solicitacao.objects.get().resolver()

        with rls.escopo_irrestrito():
            contador = get_user_model().objects.create_user(
                username="contador.fila2", is_staff=True
            )
            MembroEscritorio.objects.create(usuario=contador, escritorio=cliente.escritorio)

        assert metricas.solicitacoes_abertas(contador) == []


# ---------------------------------------------------------------------------
# LGPD
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_eliminacao_do_titular_apaga_o_texto_do_chamado(cliente):
    """Tabela mutável não precisa de crypto-shredding — apagar é mais simples e
    mais verificável. O que não pode é o texto do titular ficar de fora do
    direito de eliminação porque nasceu numa tabela nova.
    """
    ferramentas.executar("abrir_chamado", ctx_de(cliente), "meu CPF é 123 e minha conta é X")
    assert Solicitacao.objects.get().descricao

    eliminar_conteudo_do_titular(cliente)

    solicitacao = Solicitacao.objects.get()
    assert solicitacao.descricao == ""
    assert solicitacao.assunto == CONTEUDO_ELIMINADO
    # O registro de que houve um chamado permanece — o que some é o conteúdo.
    assert solicitacao.protocolo
