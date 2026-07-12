"""Envio/recepção de mídia pelo WhatsApp Cloud API (graph.facebook.com)."""
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


def baixar_midia(media_id: str) -> tuple[bytes, str] | None:
    """Baixa uma mídia (ex.: áudio) recebida no WhatsApp — dois passos da Graph
    API: resolve a URL temporária do `media_id`, depois baixa o binário.

    Sem WHATSAPP_TOKEN configurado (dev local sem app do Meta), não há como
    baixar mídia real — devolve None (dev/teste seguem offline).
    """
    token = settings.WHATSAPP_TOKEN
    if not token:
        logger.info("whatsapp_download_midia_indisponivel (sem WHATSAPP_TOKEN)", media_id=media_id)
        return None

    cabecalhos = {"Authorization": f"Bearer {token}"}
    try:
        resposta = httpx.get(
            f"https://graph.facebook.com/{VERSAO_GRAPH_API}/{media_id}", headers=cabecalhos, timeout=10.0
        )
        resposta.raise_for_status()
        info = resposta.json()

        binario = httpx.get(info["url"], headers=cabecalhos, timeout=30.0)
        binario.raise_for_status()
        return binario.content, info.get("mime_type", "audio/ogg")
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.error("whatsapp_download_midia_falhou", media_id=media_id, erro=str(exc))
        return None
