"""
Núcleo de processamento de mensagem — compartilhado entre canais.

Extraído de `tasks.py` (25/jul/2026) para servir também o canal de teste
local (`apps.channel_evolution`, Evolution API self-hosted — nunca o canal
oficial de produção, ver `docs/magicbi-hermes-comunicador.md` §3 e
`docs/magicbi-ondas-desenvolvimento.md`). Cada canal só difere em como
recebe (webhook) e como envia (`enviar_fn`) — a idempotência já aconteceu
antes de chegar aqui (na view, por `message_id`); o resto (resolver
cliente, transcrever áudio, orquestrar, auditar) é idêntico.
"""
from __future__ import annotations

from typing import Callable

import structlog

from apps.audit.services import registrar
from apps.clients.models import Usuario
from apps.core import desambiguacao
from apps.core.orchestrator import Orquestrador

logger = structlog.get_logger(__name__)


def processar(
    message_id: str,
    telefone: str,
    texto: str,
    enviar_fn: Callable[[str, str], bool],
    transcrever_fn: Callable[[str], str | None] | None = None,
    media_id: str | None = None,
    escritorio=None,
) -> str:
    # O tenant vem do número/instância que RECEBEU a mensagem (resolvido na
    # view), não do remetente — então o telefone só é procurado dentro da
    # carteira daquele escritório. Sem isso, dois escritórios com o mesmo
    # CNPJ/telefone na carteira se cruzariam.
    # `por_telefone` (e não filtro por string crua) porque o WhatsApp entrega o
    # número brasileiro ora com o nono dígito, ora sem — ver apps/clients/telefone.py.
    usuario = Usuario.objects.por_telefone(telefone, escritorio=escritorio)

    origem = "texto"
    if media_id:
        origem = "audio"
        texto = transcrever_fn(media_id) if transcrever_fn is not None else None
        texto = texto or ""
        if not texto:
            resposta = "Não consegui entender o áudio 😕 Pode escrever a mensagem?"
            # Áudio ilegível não tem conteúdo para desambiguar empresa — a
            # trilha fica com a empresa em foco, se houver, e sem dono se não.
            cliente = desambiguacao.empresa_em_foco(usuario)
            registrar(
                "whatsapp_transcricao_falhou", {"message_id": message_id, "media_id": media_id}, cliente=cliente
            )
            enviar_fn(telefone, resposta)
            registrar(
                "whatsapp_resposta_enviada",
                {"message_id": message_id, "telefone": telefone, "resposta": resposta},
                cliente=cliente,
            )
            return resposta

    # Quem fala pode responder por mais de uma empresa (DEC-03). Enquanto não
    # se sabe qual, não há o que orquestrar: `resolucao.resposta` preenchida
    # significa que a mensagem foi consumida escolhendo a empresa.
    resolucao = desambiguacao.resolver(usuario, texto)
    cliente = resolucao.cliente

    registrar(
        "whatsapp_mensagem_recebida",
        {"message_id": message_id, "telefone": telefone, "texto": texto, "origem": origem},
        cliente=cliente,
    )

    if resolucao.resposta is not None:
        resposta = resolucao.resposta
    else:
        resposta = Orquestrador().processar(
            texto, cliente, message_id=message_id, wa_id=telefone
        )

    enviado = enviar_fn(telefone, resposta)
    registrar(
        "whatsapp_resposta_enviada" if enviado else "whatsapp_resposta_falhou",
        {"message_id": message_id, "telefone": telefone, "resposta": resposta},
        cliente=cliente,
    )
    return resposta
