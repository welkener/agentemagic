"""
Transcrição de áudio (voz do WhatsApp) — D6 do MVP (voice-first, paridade com
a aposta Zucchetti/BNDES em áudio→nota; ver `docs/magicbi-analise-disrupcao.md`).

Usa o Whisper hospedado na própria Groq (`whisper-large-v3-turbo`) — mesma
conta/chave já usada para roteamento/extração, sem outro fornecedor.
Sem `GROQ_API_KEY`, transcrição não roda (devolve None); a task chamadora
trata isso pedindo pro cliente escrever — nunca falha em silêncio.
"""
from __future__ import annotations

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

_MODELO_TRANSCRICAO = "whisper-large-v3-turbo"

_EXTENSAO_POR_MIME = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/amr": "amr",
}


def transcrever(audio_bytes: bytes, mime_type: str) -> str | None:
    """Ponto único de transcrição — despacha para o transcritor configurado.

    Existe como seam por causa de uma pendência jurídica concreta: hoje o áudio
    (a VOZ do cliente) vai para o Groq, nos EUA. Se o parecer considerar voz
    dado biométrico, trocar `settings.TRANSCRITOR_AUDIO` por uma implementação
    local resolve sem tocar em mais nada — o contrato é
    `(audio_bytes, mime_type) -> str | None`.
    """
    from django.utils.module_loading import import_string

    caminho = getattr(
        settings, "TRANSCRITOR_AUDIO", "apps.channel_whatsapp.transcricao.transcrever_groq"
    )
    return import_string(caminho)(audio_bytes, mime_type)


def transcrever_groq(audio_bytes: bytes, mime_type: str) -> str | None:
    if not getattr(settings, "GROQ_API_KEY", ""):
        return None

    extensao = _EXTENSAO_POR_MIME.get(mime_type.split(";")[0].strip(), "ogg")
    try:
        from groq import Groq

        cliente = Groq(api_key=settings.GROQ_API_KEY)
        resultado = cliente.audio.transcriptions.create(
            file=(f"audio.{extensao}", audio_bytes),
            model=_MODELO_TRANSCRICAO,
        )
        return resultado.text.strip() or None
    except Exception:
        logger.warning("groq_transcricao_falhou")
        return None
