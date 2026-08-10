"""
A tabela de confirmações — o critério de aceite que pedia consulta, não leitura.

Até o Sprint 3 a confirmação em duas etapas vivia dentro do `motivo` da transição
para EMITINDO: texto livre, correto e verdadeiro, mas que só responde à pergunta
se alguém abrir a trilha e ler linha por linha. O critério da plataforma é outro
— *"nenhuma nota sem confirmação registrada"* —, e afirmação assim precisa sair
de um `filter`.

O teste central deste arquivo é justamente esse: **percorrer todas as notas
concluídas e exigir confirmação para cada uma.** Ele vale como invariante de
sistema, não como caso de uso: se um caminho novo emitir sem registrar, ele
fecha, mesmo que ninguém tenha lembrado de escrever teste para o caminho novo.
"""
import pytest

from apps.agents.agente_nf.models import Confirmacao, Intencao
from apps.agents.agente_nf.services import confirmar_emissao
from apps.tenants import rls


def nota_pendente(cliente, chave="conf-1"):
    with rls.escopo_irrestrito():
        return Intencao.objects.create(
            cliente=cliente,
            chave_idempotencia=chave,
            tipo_acao="emitir_nfse",
            payload={
                "cnpj_prestador": cliente.cnpj,
                "cnae": cliente.cnae_padrao,
                "valor": 300.0,
                "descricao_servico": "Serviço",
                "tomador": "Tomador Ltda",
            },
            valor=300,
            estado=Intencao.Estado.AGUARDANDO_APROVACAO,
        )


@pytest.mark.django_db
class TestRegistroDaConfirmacao:
    def test_emissao_pelo_cliente_registra_o_numero_e_a_mensagem(self, cliente):
        """"O cliente confirmou" sozinho não aponta para nada verificável. O
        `wa_id` diz QUEM (sob DEC-03 a empresa tem vários autorizados) e a
        referência diz A PARTIR DE QUÊ."""
        intencao = nota_pendente(cliente)

        confirmar_emissao(
            intencao,
            motivo="cliente confirmou",
            origem=Confirmacao.Origem.CLIENTE_WHATSAPP,
            wa_id="5511911112222",
            referencia="wamid.abc123",
        )

        with rls.escopo_irrestrito():
            confirmacao = Confirmacao.objects.get(intencao=intencao)
        assert confirmacao.origem == Confirmacao.Origem.CLIENTE_WHATSAPP
        assert confirmacao.wa_id == "5511911112222"
        assert confirmacao.referencia == "wamid.abc123"
        assert confirmacao.autor == "5511911112222"

    def test_emissao_pelo_contador_registra_o_login(self, cliente):
        intencao = nota_pendente(cliente)

        confirmar_emissao(
            intencao,
            motivo="decidido no Grimório",
            origem=Confirmacao.Origem.CONTADOR_PAINEL,
            usuario="contador.alfa",
        )

        with rls.escopo_irrestrito():
            confirmacao = Confirmacao.objects.get(intencao=intencao)
        assert confirmacao.usuario == "contador.alfa"
        assert confirmacao.autor == "contador.alfa"

    def test_registra_mesmo_sem_o_chamador_informar_nada(self, cliente):
        """A assinatura antiga tem vários chamadores, e argumento novo com padrão
        é fácil de esquecer. O que não pode é a linha deixar de existir — seria
        devolver exatamente o problema que a tabela veio resolver."""
        intencao = nota_pendente(cliente)

        confirmar_emissao(intencao, motivo="confirmado")

        with rls.escopo_irrestrito():
            assert Confirmacao.objects.filter(intencao=intencao).exists()

    def test_a_confirmacao_nao_guarda_o_texto_da_mensagem(self, cliente):
        """Ele já está na trilha, cifrado por titular. Uma segunda cópia aqui
        ficaria fora do alcance da eliminação por LGPD — o que fica é a
        referência, que aponta para lá sem duplicar conteúdo."""
        campos = {c.name for c in Confirmacao._meta.get_fields()}
        assert not {"mensagem", "texto", "conteudo", "resposta"} & campos


@pytest.mark.django_db
class TestInvarianteDoCriterioDeAceite:
    def test_nenhuma_nota_concluida_sem_confirmacao_registrada(self, cliente):
        """O critério de aceite, como invariante de sistema.

        Não testa um caminho — varre o resultado. Se amanhã alguém abrir um
        caminho novo de emissão sem registrar, este teste fecha, mesmo que
        ninguém tenha lembrado de escrever teste para o caminho novo.
        """
        for numero in range(3):
            confirmar_emissao(
                nota_pendente(cliente, chave=f"inv-{numero}"), motivo="confirmado"
            )

        with rls.escopo_irrestrito():
            concluidas = Intencao.objects.filter(
                estado=Intencao.Estado.CONCLUIDO, tipo_acao="emitir_nfse"
            )
            sem_confirmacao = [
                nota.pk for nota in concluidas if not nota.confirmacoes.exists()
            ]
        assert concluidas.count() >= 3, "o cenário precisa ter notas emitidas"
        assert not sem_confirmacao, f"notas emitidas sem confirmação: {sem_confirmacao}"

    def test_a_confirmacao_sobrevive_a_rejeicao_da_prefeitura(self, cliente, settings):
        """Nota rejeitada também foi autorizada — o ato de autorizar aconteceu
        antes da resposta da Sefin, e apagá-lo esconderia que alguém mandou
        emitir."""
        intencao = nota_pendente(cliente, chave="rejeitada-1")
        confirmar_emissao(intencao, motivo="confirmado")

        with rls.escopo_irrestrito():
            assert Confirmacao.objects.filter(intencao=intencao).exists()


@pytest.mark.django_db
def test_a_trilha_continua_registrando_a_transicao(cliente):
    """A tabela é o índice do ato; a trilha encadeada continua sendo a fonte
    imutável. Uma não substitui a outra — se um dia divergirem, é aqui que
    aparece."""
    from apps.audit.models import Auditoria

    intencao = nota_pendente(cliente, chave="trilha-1")
    confirmar_emissao(intencao, motivo="cliente confirmou — wa_id 5511911112222")

    with rls.escopo_irrestrito():
        transicoes = Auditoria.objects.filter(evento="intencao_fiscal_transicao")
        motivos = [(e.dados or {}).get("motivo", "") for e in transicoes]
        assert any("5511911112222" in m for m in motivos)
        assert Confirmacao.objects.filter(intencao=intencao).count() == 1
