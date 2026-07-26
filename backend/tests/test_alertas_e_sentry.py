"""Alertas de rejeição fiscal e raspagem do payload do Sentry.

Duas coisas diferentes de propósito no mesmo arquivo, porque a confusão entre
elas é justamente o ponto: **rejeição da Sefin não é exceção.** Nada quebra, o
fluxo segue, e por isso nenhum error tracker avisaria o contador — que é quem
consegue corrigir.
"""
import pytest
from django.core import mail

from apps.agents.agente_nf.models import Intencao
from apps.agents.agente_nf.services import confirmar_emissao
from apps.audit.models import Auditoria
from apps.observabilidade import sentry
from apps.observabilidade.alertas import alertar_rejeicao_fiscal, destinatarios_do_cliente
from apps.tenants.models import MembroEscritorio


@pytest.fixture
def contador_do_escritorio(db, escritorio):
    from django.contrib.auth import get_user_model

    usuario = get_user_model().objects.create_user(
        username="resp", email="responsavel@escritorio.example.com", is_staff=True
    )
    MembroEscritorio.objects.create(usuario=usuario, escritorio=escritorio, responsavel=True)
    return usuario


@pytest.fixture
def intencao_pendente(cliente):
    return Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="alerta-001",
        tipo_acao="emitir_nfse",
        payload={"valor": 900.0, "descricao_servico": "Serviço", "tomador": "Ana"},
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )


# ---------------------------------------------------------------------------
# Quem é avisado
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_alerta_vai_para_o_responsavel_do_escritorio(cliente, contador_do_escritorio, intencao_pendente):
    assert alertar_rejeicao_fiscal(intencao_pendente, "REJEITADA_SEFIN", detalhe="CNAE inválido")

    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert email.to == ["responsavel@escritorio.example.com"]
    assert cliente.nome in email.body
    assert "CNAE inválido" in email.body
    assert f"/admin/agente_nf/intencao/{intencao_pendente.pk}/change/" in email.body


@pytest.mark.django_db
def test_alerta_nunca_vai_para_o_cliente_final(cliente, contador_do_escritorio, intencao_pendente):
    """O cliente não corrige cadastro fiscal nem certificado — receber isso só
    geraria ansiedade sem ação possível."""
    cliente.email_contato = "dono@cliente.example.com"
    cliente.save()

    alertar_rejeicao_fiscal(intencao_pendente, "CADASTRO_FISCAL_INCOMPLETO")
    assert "dono@cliente.example.com" not in mail.outbox[0].to


@pytest.mark.django_db
def test_sem_responsavel_avisa_todos_os_membros(cliente, escritorio, intencao_pendente):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    for i in (1, 2):
        u = User.objects.create_user(username=f"m{i}", email=f"m{i}@x.com", is_staff=True)
        MembroEscritorio.objects.create(usuario=u, escritorio=escritorio, responsavel=False)

    alertar_rejeicao_fiscal(intencao_pendente, "REJEITADA_SEFIN")
    assert sorted(mail.outbox[0].to) == ["m1@x.com", "m2@x.com"]


# ---------------------------------------------------------------------------
# Quando NÃO alertar — alerta que vira ruído ninguém lê quando importa
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_falha_transitoria_nao_gera_alerta(cliente, contador_do_escritorio, intencao_pendente):
    """`INDISPONIVEL`/`RATE_LIMIT` o retry resolve — acordar alguém é ruído."""
    assert alertar_rejeicao_fiscal(intencao_pendente, "INDISPONIVEL") is False
    assert alertar_rejeicao_fiscal(intencao_pendente, "RATE_LIMIT") is False
    assert mail.outbox == []


@pytest.mark.django_db
def test_erro_ausente_nao_gera_alerta(cliente, contador_do_escritorio, intencao_pendente):
    assert alertar_rejeicao_fiscal(intencao_pendente, None) is False
    assert mail.outbox == []


# ---------------------------------------------------------------------------
# Alerta não pode derrubar a operação
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_falha_no_envio_nao_propaga(monkeypatch, cliente, contador_do_escritorio, intencao_pendente):
    """A nota já foi rejeitada; e-mail quebrado não pode desfazer nada."""

    def explodir(*a, **kw):
        raise RuntimeError("servidor de e-mail fora do ar")

    monkeypatch.setattr("apps.observabilidade.alertas.send_mail", explodir)

    assert alertar_rejeicao_fiscal(intencao_pendente, "REJEITADA_SEFIN") is False  # não levanta


@pytest.mark.django_db
def test_escritorio_sem_email_cadastrado_nao_quebra(cliente, intencao_pendente):
    assert destinatarios_do_cliente(cliente) == []
    assert alertar_rejeicao_fiscal(intencao_pendente, "REJEITADA_SEFIN") is False


# ---------------------------------------------------------------------------
# Trilha — dá pra auditar depois "o escritório foi avisado?"
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_alerta_fica_na_trilha_de_auditoria(cliente, contador_do_escritorio, intencao_pendente):
    alertar_rejeicao_fiscal(intencao_pendente, "REJEITADA_SEFIN")
    assert Auditoria.objects.filter(evento="rejeicao_fiscal_alertada", cliente=cliente).exists()


# ---------------------------------------------------------------------------
# Integração: rejeição real no fluxo dispara o alerta
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rejeicao_real_da_emissao_avisa_o_contador(cliente, contador_do_escritorio):
    """Sem CNAE, o mock rejeita — e agora o contador fica sabendo."""
    intencao = Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="alerta-integracao",
        tipo_acao="emitir_nfse",
        payload={"cnpj_prestador": cliente.cnpj, "valor": 100.0},  # sem cnae
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )
    resultado = confirmar_emissao(intencao, motivo="teste")

    assert resultado.ok is False
    assert len(mail.outbox) == 1
    assert "recusada" in mail.outbox[0].subject


# ---------------------------------------------------------------------------
# Sentry — o payload não pode carregar dado fiscal pra fora
# ---------------------------------------------------------------------------
def test_campos_sensiveis_sao_raspados_por_nome():
    evento = {
        "extra": {
            "cnpj": "12345678000190",
            "tomador": "Maria Silva",
            "valor": 1500.0,
            "erro_padronizado": "REJEITADA_SEFIN",  # este PODE sair — serve pra depurar
        }
    }
    limpo = sentry.before_send(evento, None)

    assert limpo["extra"]["cnpj"] == sentry.RASPADO
    assert limpo["extra"]["tomador"] == sentry.RASPADO
    assert limpo["extra"]["valor"] == sentry.RASPADO
    assert limpo["extra"]["erro_padronizado"] == "REJEITADA_SEFIN"


def test_documento_dentro_de_texto_livre_tambem_e_raspado():
    """O dado costuma vazar dentro da mensagem da exceção, onde não há nome de
    campo nenhum pra filtrar."""
    evento = {"message": "falha ao emitir para o CNPJ 12345678000190 (chave 3" + "3" * 49 + ")"}
    limpo = sentry.before_send(evento, None)

    assert "12345678000190" not in limpo["message"]
    assert sentry.RASPADO in limpo["message"]


def test_raspagem_desce_em_estrutura_aninhada():
    evento = {"contexts": {"intencao": {"payload": [{"cpf": "12345678909"}]}}}
    limpo = sentry.before_send(evento, None)
    assert limpo["contexts"]["intencao"]["payload"][0]["cpf"] == sentry.RASPADO


def test_falha_na_raspagem_descarta_o_evento():
    """Se não dá pra garantir o que sairia, não sai. Descartar é melhor que
    vazar dado fiscal por causa de um bug do scrubber."""

    class Explosivo(dict):
        def items(self):
            raise RuntimeError("boom")

    assert sentry.before_send(Explosivo(), None) is None


def test_sentry_fica_desligado_sem_dsn():
    """Ligar adiciona um subprocessador de dado fiscal — tem que ser ato
    consciente de quem configura o deploy, não efeito de instalar dependência."""
    assert sentry.configurar("", "teste") is False
