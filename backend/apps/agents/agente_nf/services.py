"""
Execução da confirmação Tier 1 (seção 15 dos requisitos): transiciona a
`Intencao` e chama o adaptador NFS-e. Única fonte de verdade para "a
emissão foi confirmada" — usada tanto pelo orquestrador (cliente responde
*sim* no WhatsApp) quanto pelo admin (contador aprova na fila do Grimório
mínimo, `admin.py`). Cada chamador só formata a mensagem no seu canal.
"""
from __future__ import annotations

from dataclasses import dataclass

from apps.adapters.resolver import resolver_adapter_nfse

from .models import Intencao


@dataclass
class ResultadoConfirmacao:
    ok: bool
    protocolo: str | None = None
    danfse_url: str | None = None
    erro: str | None = None


def confirmar_emissao(intencao: Intencao, motivo: str) -> ResultadoConfirmacao:
    """Levanta `TransicaoInvalida` (via `transicionar`) se a intenção não
    estiver em AGUARDANDO_APROVACAO — nenhum chamador pula a máquina de estados."""
    intencao.transicionar(Intencao.Estado.EMITINDO, motivo=motivo)
    nfse = resolver_adapter_nfse(intencao.cliente)
    resultado = nfse.emitir("nfse", intencao.payload, {"cliente": intencao.cliente})

    if resultado.ok:
        intencao.transicionar(Intencao.Estado.CONCLUIDO, motivo="emissão autorizada")
        return ResultadoConfirmacao(
            ok=True, protocolo=resultado.dados.get("protocolo"), danfse_url=resultado.dados.get("danfse_url")
        )

    intencao.transicionar(Intencao.Estado.REJEITADO, motivo=resultado.erro_padronizado or "rejeicao")
    return ResultadoConfirmacao(ok=False, erro=resultado.erro_padronizado)


def cancelar_emissao(intencao: Intencao, motivo: str) -> None:
    intencao.transicionar(Intencao.Estado.CANCELADO, motivo=motivo)
