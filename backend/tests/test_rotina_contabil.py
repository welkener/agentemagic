"""
A rotina contábil — guias, obrigações, certidões e folha.

**A asserção que mais importa neste arquivo é sobre o que o sistema NÃO faz.**
Sem registro daquela competência, a resposta é "ainda não tenho" — nunca um
valor calculado, estimado ou repetido do mês anterior. Um DAS inventado é pior
que nenhum: o cliente paga errado, e paga confiando.

A segunda é sobre competência: quando o mês não está claro, a conversa pergunta
em vez de chutar. Errar a competência não deixa a resposta imprecisa — deixa
falsa, porque o valor de um mês apresentado como o de outro é simplesmente
errado.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.agents import ferramentas
from apps.agents.contexto import SessionContext
from apps.rotina import consultas
from apps.rotina.models import Certidao, Folha, Guia, Obrigacao
from apps.tenants import rls


def ctx_de(cliente):
    return SessionContext.da_conversa(cliente=cliente)


def competencia(meses_atras: int = 0) -> str:
    hoje = timezone.localdate()
    indice = hoje.year * 12 + (hoje.month - 1) - meses_atras
    return f"{indice // 12:04d}-{indice % 12 + 1:02d}"


# ---------------------------------------------------------------------------
# Leitura de competência
# ---------------------------------------------------------------------------
class TestCompetencia:
    HOJE = date(2026, 8, 10)

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("me manda o DAS de julho", "2026-07"),
            ("DAS de 07/2026", "2026-07"),
            ("guia de 7/26", "2026-07"),
            ("o das desse mês", "2026-08"),
            ("e o do mês passado?", "2026-07"),
            ("quanto foi em janeiro", "2026-01"),
        ],
    )
    def test_le_o_que_esta_claro(self, texto, esperado):
        assert consultas.interpretar_competencia(texto, hoje=self.HOJE) == esperado

    def test_mes_que_ainda_nao_chegou_e_do_ano_passado(self):
        """"me manda o DAS de dezembro" em agosto não é sobre o dezembro que vem."""
        assert consultas.interpretar_competencia("DAS de dezembro", hoje=self.HOJE) == "2025-12"

    @pytest.mark.parametrize(
        "texto", ["preciso do DAS", "manda a guia aí", "quanto eu devo?", ""]
    )
    def test_nao_chuta_quando_o_mes_nao_esta_claro(self, texto):
        assert consultas.interpretar_competencia(texto, hoje=self.HOJE) is None


# ---------------------------------------------------------------------------
# A regra central: não inventar
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestNaoInventa:
    def test_sem_registro_a_resposta_e_ainda_nao_tenho(self, cliente):
        resposta = ferramentas.executar(
            "consultar_das", ctx_de(cliente), "me manda o DAS de julho"
        )

        assert "ainda não tenho" in resposta.lower()
        # E nenhum número aparece — nem como exemplo, nem como estimativa.
        assert "R$" not in resposta

    def test_nao_repete_o_valor_da_competencia_anterior(self, cliente):
        """A tentação óbvia, e o erro mais caro: o DAS varia com o faturamento
        e com a data de pagamento. Repetir o mês anterior é inventar com cara
        de fonte."""
        with rls.escopo_irrestrito():
            Guia.objects.create(
                cliente=cliente,
                tipo=Guia.Tipo.DAS,
                competencia=competencia(1),
                valor=Decimal("71.60"),
                vencimento=timezone.localdate(),
            )

        resposta = ferramentas.executar(
            "consultar_das", ctx_de(cliente), "o DAS desse mês"
        )

        assert "71,60" not in resposta and "71.60" not in resposta
        assert "ainda não tenho" in resposta.lower()

    def test_mes_ambiguo_pergunta_em_vez_de_adivinhar(self, cliente):
        resposta = ferramentas.executar("consultar_das", ctx_de(cliente), "preciso do DAS")

        assert "de qual mês" in resposta.lower()


# ---------------------------------------------------------------------------
# Guias
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestGuias:
    @pytest.fixture
    def das(self, cliente):
        with rls.escopo_irrestrito():
            return Guia.objects.create(
                cliente=cliente,
                tipo=Guia.Tipo.DAS,
                competencia=competencia(),
                valor=Decimal("75.30"),
                vencimento=timezone.localdate() + timedelta(days=5),
                codigo_barras="85800000000-7 75300000000-1",
                url_documento="https://exemplo.test/das.pdf",
            )

    def test_traz_valor_vencimento_e_linha_digitavel(self, cliente, das):
        """A linha digitável está ali porque o cliente paga pelo aplicativo do
        banco — obrigá-lo a abrir o PDF é uma etapa a mais no celular."""
        resposta = ferramentas.executar(
            "consultar_das", ctx_de(cliente), "o DAS desse mês"
        )

        assert "75.30" in resposta or "75,30" in resposta
        assert das.vencimento.strftime("%d/%m/%Y") in resposta
        assert "85800000000-7" in resposta

    def test_avisa_quando_ja_venceu(self, cliente):
        with rls.escopo_irrestrito():
            Guia.objects.create(
                cliente=cliente,
                tipo=Guia.Tipo.DAS,
                competencia=competencia(),
                valor=Decimal("75.30"),
                vencimento=timezone.localdate() - timedelta(days=4),
            )

        resposta = ferramentas.executar("consultar_das", ctx_de(cliente), "DAS desse mês")
        assert "venceu há 4 dia" in resposta.lower()

    def test_segunda_via_sem_tipo_claro_lista_as_abertas(self, cliente, das):
        """"preciso da segunda via" quase sempre quer dizer "o que eu devo?"."""
        resposta = ferramentas.executar(
            "segunda_via_guia", ctx_de(cliente), "preciso da segunda via"
        )

        assert "em aberto" in resposta.lower()
        assert "DAS" in resposta

    def test_sem_guia_aberta_diz_que_esta_tudo_certo(self, cliente):
        resposta = ferramentas.executar(
            "segunda_via_guia", ctx_de(cliente), "quais guias estão em aberto?"
        )
        assert "nenhuma guia em aberto" in resposta.lower()


# ---------------------------------------------------------------------------
# Obrigações
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestObrigacoes:
    def test_separa_o_que_depende_do_cliente(self, cliente):
        """A distinção que muda o comportamento dele: só numa das linhas ele
        precisa fazer alguma coisa. Sem isso, "pendente" vira ruído."""
        with rls.escopo_irrestrito():
            Obrigacao.objects.create(
                cliente=cliente,
                tipo=Obrigacao.Tipo.DCTFWEB,
                competencia=competencia(),
                prazo=timezone.localdate() + timedelta(days=10),
            )
            Obrigacao.objects.create(
                cliente=cliente,
                tipo=Obrigacao.Tipo.ESOCIAL,
                competencia=competencia(),
                prazo=timezone.localdate() + timedelta(days=6),
                pendente_com_o_cliente=True,
                observacao="falta enviar a folha de ponto",
            )

        resposta = ferramentas.executar("status_obrigacoes", ctx_de(cliente), "tá tudo em dia?")

        assert "depende de você" in resposta
        assert "folha de ponto" in resposta

    def test_marca_a_atrasada(self, cliente):
        with rls.escopo_irrestrito():
            Obrigacao.objects.create(
                cliente=cliente,
                tipo=Obrigacao.Tipo.EFD,
                competencia=competencia(1),
                prazo=timezone.localdate() - timedelta(days=3),
            )

        resposta = ferramentas.executar("status_obrigacoes", ctx_de(cliente), "minhas obrigações")
        assert "ATRASADA" in resposta

    def test_sem_quadro_lancado_oferece_o_chamado(self, cliente):
        """Melhor oferecer o caminho do que dar um "não sei" seco."""
        resposta = ferramentas.executar("status_obrigacoes", ctx_de(cliente), "tá tudo em dia?")

        assert "ainda não tenho" in resposta.lower()
        assert "chamado" in resposta.lower()


# ---------------------------------------------------------------------------
# Certidões e folha
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCertidoesEFolha:
    def test_certidao_vencida_aparece_marcada(self, cliente):
        with rls.escopo_irrestrito():
            Certidao.objects.create(
                cliente=cliente,
                tipo=Certidao.Tipo.FEDERAL,
                situacao=Certidao.Situacao.NEGATIVA,
                emitida_em=timezone.localdate() - timedelta(days=200),
                valida_ate=timezone.localdate() - timedelta(days=20),
            )

        resposta = ferramentas.executar("listar_certidoes", ctx_de(cliente), "minhas certidões")
        assert "vencida" in resposta.lower()

    def test_folha_em_processamento_nao_e_folha_fechada(self, cliente):
        """São estados diferentes, e dizer "não tenho" para uma folha que existe
        e ainda não fechou seria impreciso na direção que gera ligação."""
        with rls.escopo_irrestrito():
            Folha.objects.create(
                cliente=cliente, competencia=competencia(), funcionarios=3
            )

        resposta = ferramentas.executar("consultar_folha", ctx_de(cliente), "a folha fechou?")
        assert "processamento" in resposta.lower()

    def test_folha_fechada_traz_o_resumo(self, cliente):
        with rls.escopo_irrestrito():
            Folha.objects.create(
                cliente=cliente,
                competencia=competencia(),
                funcionarios=4,
                total_bruto=Decimal("12000.00"),
                total_encargos=Decimal("3600.00"),
                fechada_em=timezone.localdate(),
            )

        resposta = ferramentas.executar("consultar_folha", ctx_de(cliente), "a folha fechou?")

        assert "4" in resposta
        assert "12.000,00" in resposta or "12000.00" in resposta

    def test_a_folha_nunca_traz_salario_individual(self, cliente):
        """Salário de funcionário é dado sensível de terceiro, que não é titular
        da relação com o Magic BI — por isso o modelo guarda só o resumo."""
        campos = {c.name for c in Folha._meta.get_fields()}
        assert not {"salarios", "funcionario", "cpf"} & campos


# ---------------------------------------------------------------------------
# Escopo
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_rotina_de_uma_empresa_nao_alcanca_a_de_outra(cliente, escritorio):
    from apps.clients.models import Cliente, Perfil

    with rls.escopo_irrestrito():
        vizinha = Cliente.objects.create(
            escritorio=escritorio,
            cnpj="99999999000199",
            nome="Empresa Vizinha",
            email_contato="v@example.com",
            ativo=True,
        )
        Perfil.objects.create(cliente=vizinha, tier_maximo=1)
        Guia.objects.create(
            cliente=vizinha,
            tipo=Guia.Tipo.DAS,
            competencia=competencia(),
            valor=Decimal("999.99"),
            vencimento=timezone.localdate(),
        )

    resposta = ferramentas.executar("consultar_das", ctx_de(cliente), "o DAS desse mês")

    assert "999" not in resposta
    assert "ainda não tenho" in resposta.lower()


# ---------------------------------------------------------------------------
# A tela do contador — a contraparte obrigatória das ferramentas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTelaDaRotina:
    @pytest.fixture
    def contador(self, cliente):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from apps.tenants.models import MembroEscritorio

        with rls.escopo_irrestrito():
            usuario = get_user_model().objects.create_user(
                username="contador.rotina", email="c@example.com", is_staff=True
            )
            usuario.user_permissions.set(Permission.objects.all())
            MembroEscritorio.objects.create(
                usuario=usuario, escritorio=cliente.escritorio
            )
        return usuario

    def test_mostra_guia_vencida_e_obrigacao_atrasada(self, client, cliente, contador):
        with rls.escopo_irrestrito():
            Guia.objects.create(
                cliente=cliente,
                tipo=Guia.Tipo.DAS,
                competencia=competencia(1),
                valor=Decimal("71.60"),
                vencimento=timezone.localdate() - timedelta(days=5),
            )
            Obrigacao.objects.create(
                cliente=cliente,
                tipo=Obrigacao.Tipo.DCTFWEB,
                competencia=competencia(1),
                prazo=timezone.localdate() - timedelta(days=2),
            )
        client.force_login(contador)

        corpo = client.get("/grimorio/rotina/").content.decode()

        assert "vencida" in corpo
        assert "atrasada" in corpo
        assert cliente.nome in corpo

    def test_nao_mostra_o_que_ja_foi_pago(self, client, cliente, contador):
        """A tela existe para ser esvaziada — listar o que já saiu afogaria o
        que falta."""
        with rls.escopo_irrestrito():
            Guia.objects.create(
                cliente=cliente,
                tipo=Guia.Tipo.DAS,
                competencia=competencia(1),
                valor=Decimal("71.60"),
                vencimento=timezone.localdate(),
                situacao=Guia.Situacao.PAGA,
            )
        client.force_login(contador)

        corpo = client.get("/grimorio/rotina/").content.decode()
        assert "Nenhuma guia em aberto" in corpo

    def test_nao_mostra_a_rotina_do_escritorio_vizinho(self, client, cliente, contador):
        from apps.clients.models import Cliente as ModeloCliente
        from apps.tenants.models import Escritorio

        with rls.escopo_irrestrito():
            outro = Escritorio.objects.create(nome="Vizinho", slug="vizinho", ativo=True)
            alheio = ModeloCliente.objects.create(
                escritorio=outro,
                cnpj="88888888000188",
                nome="Empresa do Vizinho",
                email_contato="v@example.com",
                ativo=True,
            )
            Guia.objects.create(
                cliente=alheio,
                tipo=Guia.Tipo.DAS,
                competencia=competencia(),
                valor=Decimal("4321.00"),
                vencimento=timezone.localdate(),
            )
        client.force_login(contador)

        corpo = client.get("/grimorio/rotina/").content.decode()

        assert "Empresa do Vizinho" not in corpo
        assert "4.321,00" not in corpo
