"""Tarefas Celery do canal WhatsApp — aqui ficam as chamadas lentas (LLM, ERP)."""
import structlog
from celery import shared_task

from apps.audit.services import registrar
from apps.clients.models import Cliente
from apps.core.orchestrator import Orquestrador

from .services import baixar_midia, enviar_mensagem
from .transcricao import transcrever

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem(self, message_id: str, telefone: str, texto: str, media_id: str | None = None):
    """Processa uma mensagem recebida: orquestra a resposta e envia de volta.

    A idempotência por `message_id` já foi garantida na view (o registro em
    MensagemProcessada acontece antes do enfileiramento). `media_id` (D6):
    mensagem de voz — baixa e transcreve antes de seguir o pipeline normal de
    texto; sem sucesso na transcrição, pede pro cliente escrever em vez de
    travar ou inventar conteúdo.
    """
    cliente = Cliente.objects.filter(
        telefone_whatsapp=telefone, ativo=True
    ).first()

    origem = "texto"
    if media_id:
        origem = "audio"
        texto = _transcrever_audio(media_id) or ""
        if not texto:
            resposta = "Não consegui entender o áudio 😕 Pode escrever a mensagem?"
            registrar(
                "whatsapp_transcricao_falhou", {"message_id": message_id, "media_id": media_id}, cliente=cliente
            )
            enviar_mensagem(telefone, resposta)
            registrar(
                "whatsapp_resposta_enviada",
                {"message_id": message_id, "telefone": telefone, "resposta": resposta},
                cliente=cliente,
            )
            return resposta

    registrar(
        "whatsapp_mensagem_recebida",
        {"message_id": message_id, "telefone": telefone, "texto": texto, "origem": origem},
        cliente=cliente,
    )

    resposta = Orquestrador().processar(texto, cliente, message_id=message_id)

    enviado = enviar_mensagem(telefone, resposta)
    registrar(
        "whatsapp_resposta_enviada" if enviado else "whatsapp_resposta_falhou",
        {"message_id": message_id, "telefone": telefone, "resposta": resposta},
        cliente=cliente,
    )
    return resposta


def _transcrever_audio(media_id: str) -> str | None:
    baixado = baixar_midia(media_id)
    if baixado is None:
        return None
    audio_bytes, mime_type = baixado
    return transcrever(audio_bytes, mime_type)
