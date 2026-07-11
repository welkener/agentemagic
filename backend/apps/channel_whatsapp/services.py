"""Envio de mensagens pelo WhatsApp Cloud API (graph.facebook.com)."""
import httpx
import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

VERSAO_GRAPH_API = "v20.0"


def enviar_mensagem(telefone: str, texto: str) -> bool:
    """Envia texto para um número via Cloud API.

    Se o token/número não estiverem configurados (dev local sem app do Meta),
    apenas loga a resposta em vez de enviar — mantém o fluxo funcionando
    offline. Retorna True se o envio (ou o log) ocorreu sem erro.
    """
    token = settings.WHATSAPP_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID

    if not token or not phone_id:
        logger.info(
            "whatsapp_envio_simulado (sem WHATSAPP_TOKEN/PHONE_NUMBER_ID)",
            telefone=telefone,
            texto=texto,
        )
        return True

    url = f"https://graph.facebook.com/{VERSAO_GRAPH_API}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }
    try:
        resposta = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        resposta.raise_for_status()
        logger.info("whatsapp_mensagem_enviada", telefone=telefone)
        return True
    except httpx.HTTPError as exc:
        logger.error("whatsapp_envio_falhou", telefone=telefone, erro=str(exc))
        return False
