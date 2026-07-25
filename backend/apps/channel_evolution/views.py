"""
Webhook da Evolution API — canal de TESTE LOCAL apenas (ver apps.py desta
app). Recebe o evento `messages.upsert`, ignora eco de mensagens que a
própria Magic BI enviou (`fromMe`) e mensagens de grupo (só atende 1:1,
igual ao canal oficial), e reaproveita a mesma idempotência por
`message_id` do canal Meta (`apps.channel_whatsapp.models.MensagemProcessada`
— o formato do ID nunca colide entre os dois canais).
"""
import json

import structlog
from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.channel_whatsapp.models import MensagemProcessada

from .tasks import processar_mensagem_evolution

logger = structlog.get_logger(__name__)


def _autorizado(payload: dict) -> bool:
    """Sem EVOLUTION_API_KEY configurada (dev sem o valor ainda definido),
    não há o que conferir — aceita e loga. Configurada, tem que bater."""
    chave_esperada = settings.EVOLUTION_API_KEY
    if not chave_esperada:
        return True
    return payload.get("apikey") == chave_esperada


@method_decorator(csrf_exempt, name="dispatch")
class WebhookEvolutionView(View):
    def post(self, request):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("evolution_webhook_payload_invalido")
            return JsonResponse({"status": "ignorado"})

        if not _autorizado(payload):
            logger.warning("evolution_webhook_apikey_invalida")
            return JsonResponse({"status": "nao_autorizado"}, status=403)

        if payload.get("event") != "messages.upsert":
            return JsonResponse({"status": "ignorado"})

        dados = payload.get("data") or {}
        chave = dados.get("key") or {}

        if chave.get("fromMe"):
            return JsonResponse({"status": "ignorado"})  # eco do que a própria Magic BI enviou

        remote_jid = chave.get("remoteJid", "")
        if not remote_jid.endswith("@s.whatsapp.net"):
            return JsonResponse({"status": "ignorado"})  # grupo ou formato inesperado — só 1:1

        message_id = chave.get("id")
        telefone = remote_jid.split("@", 1)[0]
        mensagem = dados.get("message") or {}
        texto = mensagem.get("conversation") or (mensagem.get("extendedTextMessage") or {}).get("text", "")

        if not message_id or not texto:
            return JsonResponse({"status": "ignorado"})

        _, criado = MensagemProcessada.objects.get_or_create(
            message_id=message_id, defaults={"telefone": telefone}
        )
        if not criado:
            logger.info("evolution_mensagem_duplicada", message_id=message_id)
            return JsonResponse({"status": "recebido"})

        processar_mensagem_evolution.delay(message_id, telefone, texto)
        return JsonResponse({"status": "recebido"})
