"""Canal Evolution API — SÓ TESTE LOCAL (apps/channel_evolution/apps.py).

Mesma pipeline do canal oficial (apps.channel_whatsapp.pipeline), só troca
quem recebe/envia — estes testes cobrem a parte específica: parsing do
payload messages.upsert, filtro de eco (fromMe) e de grupos, e idempotência
compartilhada com o canal Meta.
"""
import json

import pytest

from apps.audit.models import Auditoria
from apps.channel_whatsapp.models import MensagemProcessada

URL = "/webhook/evolution"


def _payload_mensagem(message_id="3EB0TESTE001", telefone="5511999998888", texto="qual meu estoque?", from_me=False):
    return {
        "event": "messages.upsert",
        "instance": "teste",
        "data": {
            "key": {"remoteJid": f"{telefone}@s.whatsapp.net", "fromMe": from_me, "id": message_id},
            "message": {"conversation": texto},
            "messageTimestamp": 1751900000,
            "pushName": "Cliente Teste",
        },
    }


def _post(client, payload):
    return client.post(URL, data=json.dumps(payload), content_type="application/json")


@pytest.mark.django_db
def test_mensagem_valida_enfileira_e_processa(client, cliente):
    resposta = _post(client, _payload_mensagem())
    assert resposta.status_code == 200

    assert MensagemProcessada.objects.filter(message_id="3EB0TESTE001").exists()
    eventos = list(Auditoria.objects.values_list("evento", flat=True))
    assert "whatsapp_mensagem_recebida" in eventos
    assert "whatsapp_resposta_enviada" in eventos


@pytest.mark.django_db
def test_eco_de_mensagem_propria_e_ignorado(client, cliente):
    resposta = _post(client, _payload_mensagem(from_me=True))
    assert resposta.status_code == 200
    assert MensagemProcessada.objects.count() == 0


@pytest.mark.django_db
def test_mensagem_de_grupo_e_ignorada(client, cliente):
    payload = _payload_mensagem()
    payload["data"]["key"]["remoteJid"] = "123456-789@g.us"
    resposta = _post(client, payload)
    assert resposta.status_code == 200
    assert MensagemProcessada.objects.count() == 0


@pytest.mark.django_db
def test_evento_diferente_de_messages_upsert_e_ignorado(client):
    resposta = _post(client, {"event": "connection.update", "data": {}})
    assert resposta.status_code == 200
    assert MensagemProcessada.objects.count() == 0


@pytest.mark.django_db
def test_message_id_duplicado_e_ignorado(client, cliente):
    payload = _payload_mensagem(message_id="3EB0DUPLICADA")
    assert _post(client, payload).status_code == 200
    eventos_apos_primeira = Auditoria.objects.count()

    assert _post(client, payload).status_code == 200
    assert MensagemProcessada.objects.filter(message_id="3EB0DUPLICADA").count() == 1
    assert Auditoria.objects.count() == eventos_apos_primeira


@pytest.mark.django_db
def test_apikey_invalida_e_rejeitada_quando_configurada(client, settings):
    settings.EVOLUTION_API_KEY = "chave-certa"
    payload = _payload_mensagem()
    payload["apikey"] = "chave-errada"
    resposta = _post(client, payload)
    assert resposta.status_code == 403
    assert MensagemProcessada.objects.count() == 0


@pytest.mark.django_db
def test_payload_invalido_nao_quebra(client):
    resposta = client.post(URL, data=b"nao e json", content_type="application/json")
    assert resposta.status_code == 200


@pytest.mark.django_db
def test_texto_extendido_tambem_e_lido(client, cliente):
    payload = _payload_mensagem(texto="")
    payload["data"]["message"] = {"extendedTextMessage": {"text": "quanto tenho a receber?"}}
    resposta = _post(client, payload)
    assert resposta.status_code == 200
    assert MensagemProcessada.objects.exists()
