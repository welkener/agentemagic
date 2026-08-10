"""
O comando que popula a carteira de demonstração.

Um seeder parece código descartável e não é: ele escreve no **mesmo banco** que
a operação real, no **mesmo escritório**, e roda na véspera de uma reunião. Os
três testes que importam aqui são sobre o que ele não pode fazer.

O central é o terceiro: **a demonstração não pode escrever na trilha de
auditoria.** `Auditoria.cliente` é PROTECT e a trilha é encadeada por hash — se
o seeder auditasse, `--limpar` teria que apagar linhas de auditoria e quebraria
a cadeia que sustenta a emissão fiscal. Um dado de mentira não pode custar a
integridade do registro de verdade.
"""
import pytest
from django.core.management import call_command

from apps.agents.agente_nf.models import Intencao
from apps.atendimento.models import Solicitacao
from apps.audit.models import Auditoria
from apps.audit.services import verificar_cadeia
from apps.clients.models import Cliente
from apps.tenants import rls
from apps.tenants.models import Escritorio


def popular(escritorio, **extra):
    call_command(
        "popular_carteira_demo", escritorio=escritorio.slug, quantidade=6, **extra
    )


@pytest.fixture
def escritorio_unico(db):
    """Um escritório só — o cenário em que o comando aceita rodar sem `--escritorio`."""
    with rls.escopo_irrestrito():
        return Escritorio.objects.create(nome="Contábil Demo", slug="demo", ativo=True)


@pytest.mark.django_db
class TestSeederDeDemonstracao:
    def test_cria_empresas_marcadas_como_demonstracao(self, escritorio_unico):
        popular(escritorio_unico)

        with rls.escopo_irrestrito():
            criadas = Cliente.objects.filter(escritorio=escritorio_unico)
            assert criadas.count() == 6
            assert all(c.demonstracao for c in criadas)

    def test_a_demonstracao_nao_escreve_na_trilha_de_auditoria(self, escritorio_unico):
        """A invariante que torna `--limpar` possível sem quebrar a cadeia.

        Se um serviço auditado passar a ser usado pelo seeder, este teste fica
        vermelho — e é aqui que se descobre, não no rollback de uma demonstração.
        """
        with rls.escopo_irrestrito():
            antes = Auditoria.objects.count()
        popular(escritorio_unico)
        with rls.escopo_irrestrito():
            assert Auditoria.objects.count() == antes

    def test_limpar_remove_exatamente_o_que_criou(self, escritorio_unico):
        with rls.escopo_irrestrito():
            real = Cliente.objects.create(
                escritorio=escritorio_unico,
                cnpj="12312312000199",
                nome="Empresa de Verdade",
                email_contato="v@example.com",
                ativo=True,
            )
        popular(escritorio_unico)
        call_command("popular_carteira_demo", escritorio=escritorio_unico.slug, limpar=True)

        with rls.escopo_irrestrito():
            restantes = list(Cliente.objects.filter(escritorio=escritorio_unico))
            assert [c.pk for c in restantes] == [real.pk]
            # As tabelas-filhas caem por CASCADE — se sobrar órfão, a próxima
            # rodada de demonstração acumula lixo até alguém estranhar o total.
            assert not Intencao.objects.filter(cliente__demonstracao=True).exists()
            assert not Solicitacao.objects.filter(cliente__demonstracao=True).exists()

    def test_limpar_nao_quebra_a_cadeia_de_auditoria(self, escritorio_unico):
        """A prova prática da invariante anterior."""
        popular(escritorio_unico)
        call_command("popular_carteira_demo", escritorio=escritorio_unico.slug, limpar=True)

        with rls.escopo_irrestrito():
            assert verificar_cadeia() is True

    def test_rodar_duas_vezes_nao_duplica(self, escritorio_unico):
        """Semente fixa e `get_or_create` por CNPJ: a carteira é a mesma amanhã.

        Numa demonstração isso importa — o contador que voltar à tela precisa
        reencontrar as mesmas empresas, senão o sistema parece instável.
        """
        popular(escritorio_unico)
        popular(escritorio_unico)

        with rls.escopo_irrestrito():
            assert Cliente.objects.filter(escritorio=escritorio_unico).count() == 6

    def test_a_carteira_contem_os_casos_que_as_telas_existem_para_mostrar(
        self, escritorio_unico
    ):
        """Carteira só de casos tranquilos não prova que o alerta funciona.

        Com 40 empresas tem que haver pelo menos um estouro de teto, uma nota
        parada e uma rejeitada — senão a tela Hoje aparece vazia justamente na
        demonstração feita para mostrar que ela não fica.
        """
        call_command(
            "popular_carteira_demo", escritorio=escritorio_unico.slug, quantidade=40
        )
        with rls.escopo_irrestrito():
            assert Intencao.objects.filter(
                estado=Intencao.Estado.AGUARDANDO_APROVACAO
            ).exists()
            assert Intencao.objects.filter(estado=Intencao.Estado.REJEITADO).exists()
            assert Solicitacao.objects.exists()

    def test_recusa_rodar_quando_ha_mais_de_um_escritorio_ativo(self, escritorio_unico):
        """Semear no tenant errado é o erro que só se descobre na frente do
        cliente — o comando prefere parar e pedir o slug."""
        from django.core.management.base import CommandError

        with rls.escopo_irrestrito():
            Escritorio.objects.create(nome="Outro", slug="outro", ativo=True)

        with pytest.raises(CommandError, match="mais de um escritório ativo"):
            call_command("popular_carteira_demo", quantidade=2)
