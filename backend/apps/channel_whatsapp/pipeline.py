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

from apps.agents.contexto import SessionContext
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
    canal: str = "whatsapp",
    documento_fn: "Callable[[str], tuple[bytes, str, str] | None] | None" = None,
) -> str:
    # O tenant vem do número/instância que RECEBEU a mensagem (resolvido na
    # view), não do remetente — então o telefone só é procurado dentro da
    # carteira daquele escritório. Sem isso, dois escritórios com o mesmo
    # CNPJ/telefone na carteira se cruzariam.
    # `por_telefone` (e não filtro por string crua) porque o WhatsApp entrega o
    # número brasileiro ora com o nono dígito, ora sem — ver apps/clients/telefone.py.
    usuario = Usuario.objects.por_telefone(telefone, escritorio=escritorio)

    origem = "texto"

    # Documento (foto, PDF) vem ANTES do áudio de propósito: os dois chegam como
    # mídia, e quem decide qual é o caso é o canal, que sabe o `type` da
    # mensagem. Tentar transcrever um PDF devolveria vazio e o cliente ouviria
    # "não entendi o áudio" depois de mandar a nota fiscal.
    if documento_fn is not None and media_id:
        origem = "documento"
        resposta = _receber_documento(
            documento_fn, media_id, message_id, telefone, usuario, escritorio, canal
        )
        enviar_fn(telefone, resposta)
        return resposta

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
    elif cliente is None:
        # Número que não bate com usuário nenhum da carteira deste escritório.
        # Não há contexto a montar — e é aqui, e não dentro do orquestrador, que
        # isso fica visível: sem cliente não existe escopo, e sem escopo nenhuma
        # ferramenta pode rodar.
        resposta = Orquestrador().processar(texto, None)
    else:
        # O contexto é montado AQUI, no canal, e é o único caminho pelo qual
        # escopo entra nas ferramentas (DEC-05). O escritório vem do número que
        # RECEBEU; a pessoa, do número que escreveu; a empresa, da desambiguação.
        # Nenhum dos três sai do texto da mensagem.
        ctx = SessionContext.da_conversa(
            cliente=cliente,
            usuario=usuario,
            escritorio=escritorio or cliente.escritorio,
            canal=canal,
            wa_id=telefone,
            message_id=message_id,
        )
        resposta = Orquestrador().processar(texto, ctx=ctx)

    enviado = enviar_fn(telefone, resposta)
    registrar(
        "whatsapp_resposta_enviada" if enviado else "whatsapp_resposta_falhou",
        {"message_id": message_id, "telefone": telefone, "resposta": resposta},
        cliente=cliente,
    )
    return resposta


def _receber_documento(
    documento_fn, media_id, message_id, telefone, usuario, escritorio, canal
) -> str:
    """Guarda o arquivo que o cliente mandou e devolve o recibo.

    **Não passa pelo orquestrador**, e é deliberado: não há nada a rotear. O
    cliente mandou um arquivo, o sistema guarda e diz que guardou. Fazer a
    mensagem atravessar a escada de modelo gastaria um LLM para concluir o
    óbvio — e, pior, deixaria o desfecho depender de uma classificação que pode
    errar.

    Empresa não resolvida (número desconhecido, ou pessoa com carteira múltipla
    sem foco) devolve orientação em vez de guardar: sem saber de quem é o
    documento, guardá-lo seria pôr nota fiscal na pasta de alguém no chute.
    """
    from apps.core import desambiguacao
    from apps.documentos import services as documentos

    cliente = desambiguacao.empresa_em_foco(usuario)
    if cliente is None:
        registrar(
            "documento_sem_empresa_em_foco",
            {"message_id": message_id, "telefone": telefone},
            cliente=None,
        )
        return (
            "Recebi seu arquivo, mas ainda não sei de qual empresa ele é. 📎\n\n"
            "Me diga o nome da empresa e mande de novo, por favor."
        )

    baixado = documento_fn(media_id)
    if baixado is None:
        return "Não consegui baixar esse arquivo 😕 Pode mandar de novo?"

    conteudo, nome_arquivo, tipo_mime = baixado
    ctx = SessionContext.da_conversa(
        cliente=cliente,
        usuario=usuario,
        escritorio=escritorio or cliente.escritorio,
        canal=canal,
        wa_id=telefone,
        message_id=message_id,
    )
    try:
        documento, novo = documentos.receber(
            ctx=ctx, conteudo=conteudo, nome_arquivo=nome_arquivo, tipo_mime=tipo_mime
        )
    except documentos.ErroDeRecebimento as erro:
        return str(erro)

    registrar(
        "whatsapp_resposta_enviada",
        {"message_id": message_id, "telefone": telefone, "resposta": "recibo de documento"},
        cliente=cliente,
    )
    return documentos.recibo(documento, novo)
