"""Eliminação de dados do titular sem quebrar a trilha imutável.

O que estes testes protegem é a propriedade que torna a coisa toda possível:
**destruir a chave elimina o conteúdo sem alterar um byte da linha**, então a
cadeia de hash continua verificando e a auditoria continua provando que o evento
aconteceu.
"""
import pytest
from django.core.management import call_command

from apps.audit.conteudo import (
    CONTEUDO_ELIMINADO,
    MARCA_CIFRADO,
    ChaveConteudo,
    eliminar_conteudo_do_titular,
)
from apps.audit.models import Auditoria
from apps.audit.services import registrar, verificar_cadeia


@pytest.mark.django_db
def test_conteudo_pessoal_e_gravado_cifrado(cliente):
    registro = registrar(
        "whatsapp_mensagem_recebida",
        {"message_id": "wamid.1", "telefone": "5511999998888", "texto": "emite nota de 300"},
        cliente=cliente,
    )
    bruto = Auditoria.objects.get(pk=registro.pk).dados

    # O texto e o telefone não estão em claro no banco...
    assert bruto["texto"].startswith(MARCA_CIFRADO)
    assert bruto["telefone"].startswith(MARCA_CIFRADO)
    assert "emite nota de 300" not in str(bruto)
    # ...mas o que é auditoria (não conteúdo pessoal) segue legível.
    assert bruto["message_id"] == "wamid.1"


@pytest.mark.django_db
def test_conteudo_e_recuperavel_enquanto_a_chave_existe(cliente):
    registro = registrar(
        "orquestrador_mensagem_processada",
        {"mensagem": "qual meu estoque?", "intencao": "consultar_estoque"},
        cliente=cliente,
    )
    revelado = Auditoria.objects.get(pk=registro.pk).dados_revelados

    assert revelado["mensagem"] == "qual meu estoque?"
    assert revelado["intencao"] == "consultar_estoque"


# ---------------------------------------------------------------------------
# A propriedade central
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_eliminar_apaga_o_conteudo_e_a_cadeia_continua_valida(cliente):
    """Direito de eliminação (art. 18, VI) sem sacrificar a auditoria fiscal."""
    for i in range(3):
        registrar(
            "whatsapp_mensagem_recebida",
            {"message_id": f"m{i}", "texto": f"mensagem secreta {i}"},
            cliente=cliente,
        )
    assert verificar_cadeia() is True

    afetadas = eliminar_conteudo_do_titular(cliente)

    assert afetadas == 3
    assert verificar_cadeia() is True, "a cadeia NÃO pode quebrar com a eliminação"
    for registro in Auditoria.objects.filter(cliente=cliente):
        assert registro.dados_revelados.get("texto") == CONTEUDO_ELIMINADO
        assert "mensagem secreta" not in str(registro.dados_revelados)


@pytest.mark.django_db
def test_as_linhas_continuam_existindo_apos_a_eliminacao(cliente):
    """A trilha segue provando QUE o evento ocorreu — só o conteúdo some."""
    registrar("whatsapp_mensagem_recebida", {"texto": "algo"}, cliente=cliente)
    eliminar_conteudo_do_titular(cliente)

    registro = Auditoria.objects.filter(cliente=cliente).first()
    assert registro is not None
    assert registro.evento == "whatsapp_mensagem_recebida"
    assert registro.hash_atual  # a prova continua lá


@pytest.mark.django_db
def test_eliminacao_de_um_titular_nao_afeta_outro(cliente, escritorio):
    from apps.clients.models import Cliente

    outro = Cliente.objects.create(
        escritorio=escritorio, cnpj="55666777000188", nome="Outro", telefone_whatsapp="5511911112222"
    )
    registrar("whatsapp_mensagem_recebida", {"texto": "segredo do A"}, cliente=cliente)
    registrar("whatsapp_mensagem_recebida", {"texto": "segredo do B"}, cliente=outro)

    eliminar_conteudo_do_titular(cliente)

    do_outro = Auditoria.objects.filter(cliente=outro).first()
    assert do_outro.dados_revelados["texto"] == "segredo do B"  # intacto


@pytest.mark.django_db
def test_eliminacao_e_irreversivel(cliente):
    registrar("whatsapp_mensagem_recebida", {"texto": "some pra sempre"}, cliente=cliente)
    eliminar_conteudo_do_titular(cliente)

    chave = ChaveConteudo.objects.get(cliente=cliente)
    assert chave.destruida
    assert bytes(chave.chave_cifrada) == b""  # não há backup — é o ponto


@pytest.mark.django_db
def test_mensagem_sem_cliente_identificado_nao_grava_conteudo(db):
    """Número não cadastrado é justamente o caso sem chave de titular — então o
    conteúdo é omitido, não gravado em claro."""
    registro = registrar("whatsapp_mensagem_recebida", {"texto": "de numero estranho"}, cliente=None)
    assert registro.dados["texto"] == CONTEUDO_ELIMINADO


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_comando_exige_confirmacao_explicita(cliente, capsys):
    registrar("whatsapp_mensagem_recebida", {"texto": "ainda aqui"}, cliente=cliente)

    call_command("eliminar_dados_titular", cliente.cnpj)  # sem --confirmar

    assert "nada feito" in capsys.readouterr().out
    assert Auditoria.objects.filter(cliente=cliente).first().dados_revelados["texto"] == "ainda aqui"


@pytest.mark.django_db
def test_comando_registra_a_propria_eliminacao_na_trilha(cliente):
    """É o que prova, depois, que o pedido foi atendido e quando."""
    registrar("whatsapp_mensagem_recebida", {"texto": "x"}, cliente=cliente)
    call_command("eliminar_dados_titular", cliente.cnpj, "--confirmar")

    assert Auditoria.objects.filter(evento="conteudo_pessoal_eliminado", cliente=cliente).exists()
    assert verificar_cadeia() is True


@pytest.mark.django_db
def test_expurgo_desligado_por_padrao_nao_apaga_nada(settings, cliente, capsys):
    """Definir prazo de retenção é decisão jurídica — o padrão é reter."""
    from apps.channel_whatsapp.models import MensagemProcessada

    settings.RETENCAO_MENSAGENS_PROCESSADAS_DIAS = None
    MensagemProcessada.objects.create(message_id="antiga", telefone="5511999998888")

    call_command("expurgar_dados")

    assert MensagemProcessada.objects.count() == 1
    assert "Nenhum prazo configurado" in capsys.readouterr().out


@pytest.mark.django_db
def test_expurgo_apaga_quando_o_prazo_e_definido(settings):
    from datetime import timedelta

    from django.utils import timezone

    from apps.channel_whatsapp.models import MensagemProcessada

    settings.RETENCAO_MENSAGENS_PROCESSADAS_DIAS = 30
    antiga = MensagemProcessada.objects.create(message_id="antiga", telefone="5511999998888")
    MensagemProcessada.objects.filter(pk=antiga.pk).update(
        recebido_em=timezone.now() - timedelta(days=31)
    )
    MensagemProcessada.objects.create(message_id="recente", telefone="5511999997777")

    call_command("expurgar_dados")

    assert list(MensagemProcessada.objects.values_list("message_id", flat=True)) == ["recente"]


def test_transcricao_e_plugavel(settings):
    """Se o parecer considerar voz dado biométrico, trocar o transcritor não
    exige mexer em mais nada."""
    from apps.channel_whatsapp import transcricao

    chamou = {}

    def falso(audio, mime):
        chamou["sim"] = (len(audio), mime)
        return "transcrito localmente"

    settings.TRANSCRITOR_AUDIO = "tests.test_lgpd_eliminacao._transcritor_falso"
    globals()["_transcritor_falso"] = falso

    assert transcricao.transcrever(b"audio", "audio/ogg") == "transcrito localmente"
    assert chamou["sim"] == (5, "audio/ogg")


_transcritor_falso = None  # preenchido pelo teste acima
