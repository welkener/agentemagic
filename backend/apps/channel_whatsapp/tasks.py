"""Tarefas Celery do canal WhatsApp — aqui ficam as chamadas lentas (LLM, ERP).

A lógica de processamento em si vive em `pipeline.py` (compartilhada com o
canal de teste local Evolution) — esta task só liga os pontos: quem envia
(`services.enviar_mensagem`, Cloud API oficial) e como transcreve
(`transcricao.transcrever` sobre `services.baixar_midia`).
"""
from celery import shared_task

from .pipeline import processar
from .services import baixar_midia, enviar_mensagem
from .transcricao import transcrever


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem(self, message_id: str, telefone: str, texto: str, media_id: str | None = None):
    """Processa uma mensagem recebida: orquestra a resposta e envia de volta.

    A idempotência por `message_id` já foi garantida na view (o registro em
    MensagemProcessada acontece antes do enfileiramento).
    """
    return processar(
        message_id,
        telefone,
        texto,
        enviar_fn=enviar_mensagem,
        transcrever_fn=_transcrever_audio,
        media_id=media_id,
    )


def _transcrever_audio(media_id: str) -> str | None:
    baixado = baixar_midia(media_id)
    if baixado is None:
        return None
    audio_bytes, mime_type = baixado
    return transcrever(audio_bytes, mime_type)
