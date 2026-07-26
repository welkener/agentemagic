"""Rotação da chave de cifra — o que faltava pra chamar isto de cofre.

Antes havia uma chave só, vinda de variável de ambiente. Duas consequências que
estes testes fecham:

1. **Vazou a chave = redigitar todo segredo à mão**, de todos os clientes. Não
   existia caminho de rotação.
2. A chave era legível por `docker inspect` / `/proc/<pid>/environ`.
"""
import pytest
from cryptography.fernet import Fernet, InvalidToken
from django.core.management import call_command

from apps.credentials import crypto
from apps.credentials.chaves import ErroChaveDeCifraAusente, chave_ativa, chaves_configuradas

CHAVE_A = Fernet.generate_key().decode()
CHAVE_B = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Origem da chave
# ---------------------------------------------------------------------------
def test_arquivo_tem_prioridade_sobre_variavel_de_ambiente(settings, tmp_path):
    """Arquivo é o formato de `docker secret`/`systemd LoadCredential` — não
    aparece em `docker inspect` nem em `/proc/environ`, que é o ponto."""
    arquivo = tmp_path / "chave.key"
    arquivo.write_text(CHAVE_A, encoding="utf-8")

    settings.FIELD_ENCRYPTION_KEY_FILE = str(arquivo)
    settings.FIELD_ENCRYPTION_KEY = CHAVE_B  # deve ser ignorada

    assert chave_ativa() == CHAVE_A


def test_quebra_de_linha_no_arquivo_nao_invalida_a_chave(settings, tmp_path):
    """Editor de texto põe `\\n` no fim. A chave Fernet é base64 estrito — sem
    `strip()` isso daria "Invalid key" num ponto onde ninguém quer depurar."""
    arquivo = tmp_path / "chave.key"
    arquivo.write_text(f"{CHAVE_A}\n", encoding="utf-8")
    settings.FIELD_ENCRYPTION_KEY_FILE = str(arquivo)

    assert chave_ativa() == CHAVE_A
    assert crypto.decifrar(crypto.cifrar("segredo")) == "segredo"


def test_arquivo_inexistente_falha_alto(settings):
    settings.FIELD_ENCRYPTION_KEY_FILE = "/caminho/que/nao/existe.key"
    with pytest.raises(ErroChaveDeCifraAusente, match="não deu para ler"):
        chaves_configuradas()


def test_sem_chave_nenhuma_recusa_cifrar(settings):
    settings.FIELD_ENCRYPTION_KEY_FILE = ""
    settings.FIELD_ENCRYPTION_KEY = ""
    with pytest.raises(ErroChaveDeCifraAusente):
        crypto.cifrar("segredo")


# ---------------------------------------------------------------------------
# Rotação sem downtime
# ---------------------------------------------------------------------------
def test_chave_antiga_continua_decifrando_durante_a_rotacao(settings):
    """É esta propriedade que torna a rotação segura: entre trocar a chave e
    recifrar tudo, o sistema não para."""
    settings.FIELD_ENCRYPTION_KEY_FILE = ""
    settings.FIELD_ENCRYPTION_KEY = CHAVE_A
    cifrado_com_a_antiga = crypto.cifrar("token-oauth-do-cliente")

    # Passo 1: nova chave na FRENTE, antiga permanece na lista.
    settings.FIELD_ENCRYPTION_KEY = f"{CHAVE_B},{CHAVE_A}"

    assert chave_ativa() == CHAVE_B
    assert crypto.decifrar(cifrado_com_a_antiga) == "token-oauth-do-cliente"
    # E o que for cifrado agora já sai com a nova.
    assert crypto.decifrar(crypto.cifrar("novo")) == "novo"


def test_remover_a_chave_antiga_cedo_demais_torna_o_segredo_ilegivel(settings):
    """O erro que o procedimento existe pra evitar — documentado por teste."""
    settings.FIELD_ENCRYPTION_KEY_FILE = ""
    settings.FIELD_ENCRYPTION_KEY = CHAVE_A
    cifrado = crypto.cifrar("segredo")

    settings.FIELD_ENCRYPTION_KEY = CHAVE_B  # antiga removida SEM recifrar
    with pytest.raises(InvalidToken):
        crypto.decifrar(cifrado)


def test_troca_de_chave_vale_sem_reiniciar_o_processo(settings):
    """`_fernet()` é reconstruído a cada chamada — rotação com o app no ar."""
    settings.FIELD_ENCRYPTION_KEY_FILE = ""
    settings.FIELD_ENCRYPTION_KEY = CHAVE_A
    assert chave_ativa() == CHAVE_A

    settings.FIELD_ENCRYPTION_KEY = f"{CHAVE_B},{CHAVE_A}"
    assert chave_ativa() == CHAVE_B  # sem restart


# ---------------------------------------------------------------------------
# O comando de rotação
# ---------------------------------------------------------------------------
@pytest.fixture
def credencial_cifrada(cliente, settings):
    from apps.credentials.models import Credencial

    settings.FIELD_ENCRYPTION_KEY_FILE = ""
    settings.FIELD_ENCRYPTION_KEY = CHAVE_A

    credencial = Credencial.objects.create(
        cliente=cliente, integracao="conta_azul", tipo=Credencial.Tipo.OAUTH
    )
    credencial.valor = "refresh-token-secreto"
    credencial.save()
    return credencial


@pytest.mark.django_db
def test_rotacionar_recifra_e_o_segredo_sobrevive(credencial_cifrada, settings):
    from apps.credentials.models import Credencial

    settings.FIELD_ENCRYPTION_KEY = f"{CHAVE_B},{CHAVE_A}"
    call_command("rotacionar_chave")

    # Agora a chave antiga pode sair — e o segredo continua legível.
    settings.FIELD_ENCRYPTION_KEY = CHAVE_B
    assert Credencial.objects.get(pk=credencial_cifrada.pk).valor == "refresh-token-secreto"


@pytest.mark.django_db
def test_conferir_nao_escreve_nada(credencial_cifrada, settings):
    from apps.credentials.models import Credencial

    settings.FIELD_ENCRYPTION_KEY = f"{CHAVE_B},{CHAVE_A}"
    call_command("rotacionar_chave", "--conferir")

    # Sem a antiga na lista, o registro NÃO deve abrir — prova que não recifrou.
    settings.FIELD_ENCRYPTION_KEY = CHAVE_B
    with pytest.raises(InvalidToken):
        _ = Credencial.objects.get(pk=credencial_cifrada.pk).valor


@pytest.mark.django_db
def test_rotacao_descobre_sozinha_os_models_com_segredo():
    """Lista fixa envelheceria calada: campo cifrado novo ficaria de fora da
    rotação e o segredo ficaria preso na chave antiga até alguém notar."""
    from apps.credentials.management.commands.rotacionar_chave import _models_com_segredo

    nomes = {m.__name__ for m, _ in _models_com_segredo()}
    # Os quatro que existem hoje — se alguém adicionar um quinto, ele entra sozinho.
    assert {"Credencial", "AplicativoIntegracao", "Escritorio", "ConfiguracaoEvolution"} <= nomes


@pytest.mark.django_db
def test_segredo_que_nenhuma_chave_abre_e_reportado_sem_abortar(credencial_cifrada, settings, capsys):
    """Abortar no primeiro ilegível deixaria a base metade rotacionada."""
    settings.FIELD_ENCRYPTION_KEY = CHAVE_B  # a chave que cifrou saiu da lista
    call_command("rotacionar_chave")

    saida = capsys.readouterr().out
    assert "NENHUMA chave abre" in saida
    assert "recadastrados à mão" in saida
