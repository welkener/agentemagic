"""Tarefas Celery do canal WhatsApp — aqui ficam as chamadas lentas (LLM, ERP)."""
import structlog
from celery import shared_task

from apps.audit.services import registrar
from apps.clients.models import Cliente
from apps.core.orchestrator import Orquestrador

from .services import enviar_mensagem

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem(self, message_id: str, telefone: str, texto: str):
    """Processa uma mensagem recebida: orquestra a resposta e envia de volta.

    A idempotência por `message_id` já foi garantida na view (o registro em
    MensagemProcessada acontece antes do enfileiramento).
    """
    cliente = Cliente.objects.filter(
        telefone_whatsapp=telefone, ativo=True
    ).first()

    registrar(
        "whatsapp_mensagem_recebida",
        {"message_id": message_id, "telefone": telefone, "texto": texto},
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
