"""Task Celery do canal Evolution — mesma pipeline do canal oficial (ver
apps/channel_whatsapp/pipeline.py), só troca quem envia. Sem transcrição de
áudio nesta v1 (texto é o suficiente pro teste local; voz já existe no canal
oficial — D6, `apps/channel_whatsapp/transcricao.py` — não duplicado aqui
para não expandir escopo do que é só uma ponte de teste)."""
from celery import shared_task

from apps.channel_whatsapp.pipeline import processar

from .services import enviar_mensagem


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def processar_mensagem_evolution(self, message_id: str, telefone: str, texto: str):
    return processar(message_id, telefone, texto, enviar_fn=enviar_mensagem)
