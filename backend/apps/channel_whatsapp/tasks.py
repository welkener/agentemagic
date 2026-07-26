"""Tarefas Celery do canal WhatsApp — aqui ficam as chamadas lentas (LLM, ERP).

A lógica de processamento em si vive em `pipeline.py` (compartilhada com o
canal de teste local Evolution) — esta task só liga os pontos: quem envia
(`services.enviar_mensagem`, Cloud API oficial) e como transcreve
(`transcricao.transcrever` sobre `services.baixar_midia`).

Multi-tenant: a task recebe `escritorio_id` (resolvido na view pelo número que
recebeu a mensagem) e amarra envio, download de mídia e busca do cliente àquele
escritório. O id viaja em vez do objeto porque o payload do Celery é JSON.
"""
from celery import shared_task

from apps.tenants.models import Escritorio

from .pipeline import processar
from .services import baixar_midia, enviar_mensagem
from .transcricao import transcrever


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem(
    self,
    message_id: str,
    telefone: str,
    texto: str,
    media_id: str | None = None,
    escritorio_id: int | None = None,
):
    """Processa uma mensagem recebida: orquestra a resposta e envia de volta.

    A idempotência por `message_id` já foi garantida na view (o registro em
    MensagemProcessada acontece antes do enfileiramento).
    """
    escritorio = Escritorio.objects.filter(pk=escritorio_id).first() if escritorio_id else None

    return processar(
        message_id,
        telefone,
        texto,
        enviar_fn=lambda tel, txt: enviar_mensagem(tel, txt, escritorio=escritorio),
        transcrever_fn=lambda mid: _transcrever_audio(mid, escritorio),
        media_id=media_id,
        escritorio=escritorio,
    )


def _transcrever_audio(media_id: str, escritorio=None) -> str | None:
    baixado = baixar_midia(media_id, escritorio=escritorio)
    if baixado is None:
        return None
    audio_bytes, mime_type = baixado
    return transcrever(audio_bytes, mime_type)
