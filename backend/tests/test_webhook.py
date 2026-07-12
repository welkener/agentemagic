"""Testes do webhook do WhatsApp: handshake, assinatura, fila e idempotência."""
import hashlib
import hmac
import json

import pytest
from django.conf import settings

from apps.audit.models import Auditoria
from apps.channel_whatsapp.models import MensagemProcessada

URL = "/webhook/whatsapp"


def _payload_mensagem(message_id="wamid.TESTE001", telefone="5511999998888", texto="oi"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": telefone,
                                    "timestamp": "1751900000",
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _assinar(corpo: bytes) -> str:
    mac = hmac.new(settings.META_APP_SECRET.encode(), corpo, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _payload_audio(message_id="wamid.AUDIO001", telefone="5511999998888", media_id="MEDIA123"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": telefone,
                                    "timestamp": "1751900000",
                                    "type": "audio",
                                    "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _post_assinado(client, payload):
    corpo = json.dumps(payload).encode()
    return client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": _assinar(corpo)},
    )


def test_handshake_get_valido(client):
    resposta = client.get(
        URL,
        {
            "hub.mode": "subscribe",
            "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "12345",
        },
    )
    assert resposta.status_code == 200
    assert resposta.content == b"12345"


def test_handshake_get_token_errado(client):
    resposta = client.get(
        URL,
        {"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "x"},
    )
    assert resposta.status_code == 403


@pytest.mark.django_db
def test_post_assinatura_invalida_retorna_403(client):
    corpo = json.dumps(_payload_mensagem()).encode()
    resposta = client.post(
        URL,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert resposta.status_code == 403
    assert MensagemProcessada.objects.count() == 0


@pytest.mark.django_db
def test_post_sem_assinatura_retorna_403(client):
    corpo = json.dumps(_payload_mensagem()).encode()
    resposta = client.post(URL, data=corpo, content_type="application/json")
    assert resposta.status_code == 403


@pytest.mark.django_db
def test_post_valido_enfileira_e_processa(client, cliente):
    resposta = _post_assinado(client, _payload_mensagem(texto="qual meu estoque?"))
    assert resposta.status_code == 200

    # A mensagem foi registrada (idempotência) e a task rodou (CELERY eager).
    assert MensagemProcessada.objects.filter(message_id="wamid.TESTE001").exists()
    eventos = list(Auditoria.objects.values_list("evento", flat=True))
    assert "whatsapp_mensagem_recebida" in eventos
    assert "whatsapp_resposta_enviada" in eventos


@pytest.mark.django_db
def test_message_id_duplicado_e_ignorado(client, cliente):
    payload = _payload_mensagem(message_id="wamid.DUPLICADA")
    assert _post_assinado(client, payload).status_code == 200
    eventos_apos_primeira = Auditoria.objects.count()

    # Reenvio do Meta com o mesmo message_id: nada é reprocessado.
    assert _post_assinado(client, payload).status_code == 200
    assert MensagemProcessada.objects.filter(message_id="wamid.DUPLICADA").count() == 1
    assert Auditoria.objects.count() == eventos_apos_primeira


@pytest.mark.django_db
def test_mensagem_de_audio_sem_groq_pede_para_escrever(client, cliente):
    """D6: sem WHATSAPP_TOKEN/GROQ_API_KEY (settings_test), a transcrição não
    roda de verdade — o fluxo degrada pedindo pro cliente escrever, sem travar."""
    resposta = _post_assinado(client, _payload_audio())
    assert resposta.status_code == 200

    assert MensagemProcessada.objects.filter(message_id="wamid.AUDIO001").exists()
    eventos = list(Auditoria.objects.values_list("evento", "dados"))
    nomes_eventos = [e for e, _ in eventos]
    assert "whatsapp_transcricao_falhou" in nomes_eventos
    assert "whatsapp_mensagem_recebida" not in nomes_eventos  # nunca chegou a processar texto vazio

    resposta_enviada = next(dados for evento, dados in eventos if evento == "whatsapp_resposta_enviada")
    assert "áudio" in resposta_enviada["resposta"]
