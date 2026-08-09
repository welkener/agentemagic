"""Task Celery do canal Evolution — mesma pipeline do canal oficial (ver
apps/channel_whatsapp/pipeline.py). D6 (voz) reaproveita
`apps.channel_whatsapp.transcricao` (mesmo modelo Whisper/Groq) — só a forma
de baixar o binário muda (ver `services.baixar_midia`).

Multi-tenant igual ao canal oficial: o `escritorio_id` vem da view (resolvido
pela instância que recebeu a mensagem) e amarra envio, mídia e busca do cliente.
"""
from celery import shared_task

from apps.channel_whatsapp.pipeline import processar
from apps.channel_whatsapp.transcricao import transcrever
from apps.tenants.models import Escritorio

from .services import baixar_midia, enviar_mensagem


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem_evolution(
    self,
    message_id: str,
    telefone: str,
    texto: str,
    media_id: str | None = None,
    escritorio_id: int | None = None,
):
    escritorio = Escritorio.objects.filter(pk=escritorio_id).first() if escritorio_id else None

    return processar(
        message_id,
        telefone,
        texto,
        enviar_fn=lambda tel, txt: enviar_mensagem(tel, txt, escritorio=escritorio),
        transcrever_fn=lambda mid: _transcrever_audio(mid, escritorio),
        media_id=media_id,
        escritorio=escritorio,
        # O canal fica no contexto e, daí, no chamado que o cliente abrir. A
        # Evolution é canal de TESTE (ver apps/channel_evolution/apps.py) —
        # confundi-la com produção na trilha faria um chamado de ensaio parecer
        # um pedido real de cliente na fila do contador.
        canal="evolution",
    )


def _transcrever_audio(message_id: str, escritorio=None) -> str | None:
    baixado = baixar_midia(message_id, escritorio=escritorio)
    if baixado is None:
        return None
    audio_bytes, mime_type = baixado
    return transcrever(audio_bytes, mime_type)
