"""Envio de mensagem via Evolution API — canal de TESTE LOCAL apenas (ver apps.py)."""
import httpx
import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)


def enviar_mensagem(telefone: str, texto: str) -> bool:
    """Envia texto via `POST {base_url}/message/sendText/{instance}`.

    Sem EVOLUTION_BASE_URL/API_KEY/INSTANCE configurados, só loga (mesmo
    padrão de degradação do canal oficial — mantém o fluxo testável offline).
    """
    base_url = settings.EVOLUTION_BASE_URL
    api_key = settings.EVOLUTION_API_KEY
    instancia = settings.EVOLUTION_INSTANCE

    if not base_url or not api_key or not instancia:
        logger.info(
            "evolution_envio_simulado (sem EVOLUTION_BASE_URL/API_KEY/INSTANCE)",
            telefone=telefone,
            texto=texto,
        )
        return True

    url = f"{base_url.rstrip('/')}/message/sendText/{instancia}"
    try:
        resposta = httpx.post(
            url,
            json={"number": telefone, "text": texto},
            headers={"apikey": api_key},
            timeout=15.0,
        )
        resposta.raise_for_status()
        logger.info("evolution_mensagem_enviada", telefone=telefone)
        return True
    except httpx.HTTPError as exc:
        logger.error("evolution_envio_falhou", telefone=telefone, erro=str(exc))
        return False
