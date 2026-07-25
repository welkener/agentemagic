"""Task Celery do canal Evolution — mesma pipeline do canal oficial (ver
apps/channel_whatsapp/pipeline.py). D6 (voz) reaproveita
`apps.channel_whatsapp.transcricao` (mesmo modelo Whisper/Groq) — só a forma
de baixar o binário muda (ver `services.baixar_midia`)."""
from celery import shared_task

from apps.channel_whatsapp.pipeline import processar
from apps.channel_whatsapp.transcricao import transcrever

from .services import baixar_midia, enviar_mensagem


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem_evolution(self, message_id: str, telefone: str, texto: str, media_id: str | None = None):
    return processar(
        message_id,
        telefone,
        texto,
        enviar_fn=enviar_mensagem,
        transcrever_fn=_transcrever_audio,
        media_id=media_id,
    )


def _transcrever_audio(message_id: str) -> str | None:
    baixado = baixar_midia(message_id)
    if baixado is None:
        return None
    audio_bytes, mime_type = baixado
    return transcrever(audio_bytes, mime_type)
