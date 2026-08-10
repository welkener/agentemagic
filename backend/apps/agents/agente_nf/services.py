"""
Execução da confirmação Tier 1 (seção 15 dos requisitos): transiciona a
`Intencao` e chama o adaptador NFS-e. Única fonte de verdade para "a
emissão foi confirmada" — usada tanto pelo orquestrador (cliente responde
*sim* no WhatsApp) quanto pelo admin (contador aprova na fila do Grimório
mínimo, `admin.py`). Cada chamador só formata a mensagem no seu canal.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from apps.adapters.resolver import resolver_adapter_nfse
from apps.observabilidade.alertas import alertar_rejeicao_fiscal

from .models import Intencao


@dataclass
class ResultadoConfirmacao:
    ok: bool
    protocolo: str | None = None
    danfse_url: str | None = None
    erro: str | None = None


def confirmar_emissao(
    intencao: Intencao,
    motivo: str,
    *,
    origem: str = "",
    wa_id: str = "",
    usuario: str = "",
    referencia: str = "",
    exigiu_2fa: bool = False,
) -> ResultadoConfirmacao:
    """Levanta `TransicaoInvalida` (via `transicionar`) se a intenção não
    estiver em AGUARDANDO_APROVACAO — nenhum chamador pula a máquina de estados.

    Os parâmetros de autoria são opcionais e têm padrão porque a assinatura
    antiga tem vários chamadores; o que não é opcional é o **registro**: a
    `Confirmacao` é gravada de qualquer jeito, com a origem deduzida quando não
    vier explícita. Deixar de gravar quando o chamador esquece o argumento
    devolveria o problema que a tabela veio resolver — nota sem confirmação
    consultável.
    """
    _registrar_confirmacao(
        intencao,
        origem=origem,
        wa_id=wa_id,
        usuario=usuario,
        referencia=referencia,
        exigiu_2fa=exigiu_2fa,
    )
    intencao.transicionar(Intencao.Estado.EMITINDO, motivo=motivo)
    nfse = resolver_adapter_nfse(intencao.cliente)
    resultado = nfse.emitir("nfse", intencao.payload, {"cliente": intencao.cliente})

    if resultado.ok:
        intencao.protocolo = resultado.dados.get("protocolo") or ""
        intencao.danfse_url = resultado.dados.get("danfse_url") or ""
        intencao.chave_nfse = resultado.dados.get("chave_nfse") or ""
        intencao.save(update_fields=["protocolo", "danfse_url", "chave_nfse"])
        intencao.transicionar(Intencao.Estado.CONCLUIDO, motivo="emissão autorizada")
        return ResultadoConfirmacao(ok=True, protocolo=intencao.protocolo, danfse_url=intencao.danfse_url)

    intencao.transicionar(Intencao.Estado.REJEITADO, motivo=resultado.erro_padronizado or "rejeicao")
    # Quem consegue corrigir é o contador — e até aqui ninguém o avisava.
    alertar_rejeicao_fiscal(
        intencao,
        resultado.erro_padronizado,
        detalhe=str((resultado.dados or {}).get("mensagem_sefin", "")),
    )
    return ResultadoConfirmacao(ok=False, erro=resultado.erro_padronizado)


def _registrar_confirmacao(
    intencao: Intencao, *, origem: str, wa_id: str, usuario: str, referencia: str,
    exigiu_2fa: bool,
) -> None:
    """Grava o ato de autorizar. Sempre — mesmo sem argumento nenhum.

    Sem `origem` explícita, deduz pelo que veio: login preenchido é painel,
    número é WhatsApp. O palpite é conservador e fica registrado como tal em vez
    de a linha simplesmente não existir.
    """
    from apps.agents.agente_nf.models import Confirmacao

    if not origem:
        origem = (
            Confirmacao.Origem.CONTADOR_PAINEL
            if usuario
            else Confirmacao.Origem.CLIENTE_WHATSAPP
        )
    Confirmacao.objects.create(
        intencao=intencao,
        origem=origem,
        wa_id=wa_id or "",
        usuario=usuario or "",
        referencia=referencia or "",
        exigiu_2fa=exigiu_2fa,
    )


def cancelar_emissao(intencao: Intencao, motivo: str) -> None:
    """Desiste de uma emissão que AINDA NÃO saiu (nada foi para a Sefin)."""
    intencao.transicionar(Intencao.Estado.CANCELADO, motivo=motivo)


# ---------------------------------------------------------------------------
# Cancelamento de nota JÁ EMITIDA — fluxo diferente do acima
# ---------------------------------------------------------------------------
class ErroCancelamento(Exception):
    """Pedido de cancelamento inválido (nota não cancelável, pedido duplicado)."""


def solicitar_cancelamento(nota: Intencao, motivo: str, origem: str) -> Intencao:
    """Cria o PEDIDO de cancelamento — não cancela nada ainda.

    Cancelar documento fiscal é Tier 3 (destrutivo) e tem efeito contábil e
    prazo legal. No MVP nenhum perfil de cliente executa isso sozinho pelo
    WhatsApp, **independente do `tier_maximo`**: o pedido nasce em
    AGUARDANDO_APROVACAO e quem decide é o contador, na mesma fila onde ele já
    aprova emissão. O catálogo de tiers mantém `cancelar_nota: 3` como o nível
    de risco documentado para quando/se a execução direta for liberada.
    """
    if not nota.pode_ser_cancelada:
        if nota.cancelada:
            raise ErroCancelamento("Essa nota já foi cancelada.")
        raise ErroCancelamento("Só é possível cancelar nota que foi emitida com sucesso.")

    if not (motivo or "").strip():
        raise ErroCancelamento("A Sefin exige justificativa para cancelar.")

    pendente = nota.pedidos_cancelamento.filter(
        estado__in=[
            Intencao.Estado.RECEBIDO,
            Intencao.Estado.VALIDANDO,
            Intencao.Estado.AGUARDANDO_APROVACAO,
            Intencao.Estado.EMITINDO,
        ]
    ).first()
    if pendente is not None:
        raise ErroCancelamento("Já existe um pedido de cancelamento em análise para essa nota.")

    pedido = Intencao.objects.create(
        cliente=nota.cliente,
        chave_idempotencia=f"cancelar-{nota.pk}-{nota.pedidos_cancelamento.count() + 1}",
        tipo_acao="cancelar_nfse",
        intencao_original=nota,
        payload={"protocolo": nota.protocolo, "motivo": motivo, "origem": origem},
    )
    pedido.transicionar(Intencao.Estado.VALIDANDO, motivo=f"pedido de cancelamento ({origem})")
    pedido.transicionar(
        Intencao.Estado.AGUARDANDO_APROVACAO, motivo="aguardando decisão do contador"
    )
    return pedido


def confirmar_cancelamento(
    pedido: Intencao, motivo: str, *, usuario: str = ""
) -> ResultadoConfirmacao:
    """Executa o cancelamento na Sefin. Só o contador chega aqui.

    Registra a confirmação pelo mesmo motivo da emissão: cancelar é ato
    irreversível de documento fiscal, e "quem autorizou" precisa sair de uma
    consulta, não de leitura da trilha.
    """
    if pedido.tipo_acao != "cancelar_nfse" or pedido.intencao_original is None:
        raise ErroCancelamento("Esta intenção não é um pedido de cancelamento.")

    nota = pedido.intencao_original
    _registrar_confirmacao(
        pedido,
        origem="contador_painel" if usuario else "equipe_admin",
        wa_id="",
        usuario=usuario,
        referencia="",
        exigiu_2fa=False,
    )
    pedido.transicionar(Intencao.Estado.EMITINDO, motivo=motivo)

    nfse = resolver_adapter_nfse(nota.cliente)
    # A CHAVE, não o protocolo: o evento de cancelamento identifica o
    # documento pela chave de acesso de 50 dígitos (apps/fiscal/eventos.py).
    resultado = nfse.cancelar(
        "nfse",
        nota.chave_nfse,
        pedido.payload.get("motivo", ""),
        {"cliente": nota.cliente},
    )

    if resultado.ok:
        nota.cancelada_em = timezone.now()
        nota.protocolo_cancelamento = resultado.dados.get("protocolo_cancelamento") or ""
        nota.save(update_fields=["cancelada_em", "protocolo_cancelamento", "atualizado_em"])
        pedido.protocolo = nota.protocolo_cancelamento
        pedido.save(update_fields=["protocolo", "atualizado_em"])
        pedido.transicionar(Intencao.Estado.CONCLUIDO, motivo="cancelamento aceito pela Sefin")
        return ResultadoConfirmacao(ok=True, protocolo=nota.protocolo_cancelamento)

    pedido.transicionar(
        Intencao.Estado.REJEITADO, motivo=resultado.erro_padronizado or "rejeicao"
    )
    alertar_rejeicao_fiscal(
        pedido,
        resultado.erro_padronizado,
        detalhe=str((resultado.dados or {}).get("mensagem_sefin", "")),
    )
    return ResultadoConfirmacao(ok=False, erro=resultado.erro_padronizado)
