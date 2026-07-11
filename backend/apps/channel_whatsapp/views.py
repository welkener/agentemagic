"""
Webhook do WhatsApp Cloud API.

- GET: handshake de verificação do Meta (hub.mode / hub.verify_token /
  hub.challenge).
- POST: valida a assinatura `X-Hub-Signature-256` (HMAC-SHA256 com o
  META_APP_SECRET), responde 200 IMEDIATAMENTE e joga o processamento pesado
  para o Celery. Idempotência por `message_id`: duplicatas são ignoradas.
"""
import hashlib
import hmac
import json

import structlog
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import MensagemProcessada
from .tasks import processar_mensagem

logger = structlog.get_logger(__name__)


def _assinatura_valida(corpo: bytes, cabecalho: str | None) -> bool:
    """Confere o HMAC-SHA256 do corpo cru contra o X-Hub-Signature-256."""
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(
        settings.META_APP_SECRET.encode("utf-8"), corpo, hashlib.sha256
    ).hexdigest()
    recebido = cabecalho.removeprefix("sha256=")
    return hmac.compare_digest(esperado, recebido)


@method_decorator(csrf_exempt, name="dispatch")
class WebhookWhatsAppView(View):
    """Recepção do WhatsApp: ack rápido, fila Celery, idempotência."""

    # ------------------------------------------------------------------
    # GET — verificação do webhook (feita uma vez, no painel do Meta)
    # ------------------------------------------------------------------
    def get(self, request):
        modo = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        desafio = request.GET.get("hub.challenge", "")
        if modo == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
            return HttpResponse(desafio, content_type="text/plain")
        return HttpResponseForbidden("Token de verificação inválido.")

    # ------------------------------------------------------------------
    # POST — recepção de mensagens
    # ------------------------------------------------------------------
    def post(self, request):
        assinatura = request.headers.get("X-Hub-Signature-256")
        if not _assinatura_valida(request.body, assinatura):
            logger.warning("webhook_assinatura_invalida")
            return HttpResponseForbidden("Assinatura inválida.")

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # 200 mesmo assim: o Meta reenvia em caso de erro, e payload
            # malformado não melhora com retry.
            logger.warning("webhook_payload_invalido")
            return JsonResponse({"status": "ignorado"})

        for mensagem in self._extrair_mensagens(payload):
            message_id = mensagem.get("id")
            if not message_id:
                continue
            telefone = mensagem.get("from", "")
            texto = (mensagem.get("text") or {}).get("body", "")

            # Idempotência: só a primeira chegada de cada message_id enfileira.
            _, criado = MensagemProcessada.objects.get_or_create(
                message_id=message_id, defaults={"telefone": telefone}
            )
            if not criado:
                logger.info("webhook_mensagem_duplicada", message_id=message_id)
                continue

            processar_mensagem.delay(message_id, telefone, texto)

        # Ack imediato — o processamento pesado ficou na fila.
        return JsonResponse({"status": "recebido"})

    @staticmethod
    def _extrair_mensagens(payload: dict):
        """Percorre entry[].changes[].value.messages[] do payload do Meta."""
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                yield from change.get("value", {}).get("messages", []) or []
