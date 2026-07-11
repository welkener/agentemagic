"""
Agente ERP (esqueleto Semana 1) — recebe uma intenção de consulta e chama o
adaptador de ERP pela interface única, respeitando o motor de tiers.

No piloto o agenteERP fica em Tier 0–1 (leitura + rascunho); a resposta em
linguagem natural rica entra na Semana 2 com o LLM.
"""
from __future__ import annotations

import structlog

from apps.adapters.erp_mock import ErpMockAdapter
from apps.audit.services import registrar
from apps.governance.tiers import tier_da_intencao, verificar_tier

logger = structlog.get_logger(__name__)


class AgenteErp:
    """Serviço que traduz intenções de consulta em chamadas ao adaptador."""

    def __init__(self, adapter=None):
        # O adaptador real (Conta Azul/Bling) é injetável; padrão = mock.
        self.adapter = adapter or ErpMockAdapter()

    def consultar(self, intencao: str, recurso: str, filtros: dict, perfil, cliente=None) -> str:
        """Executa uma consulta (Tier 0) e devolve resposta em texto simples."""
        tier = tier_da_intencao(intencao)
        if not verificar_tier(tier, perfil):
            return (
                "Essa operação não está liberada para o seu perfil no momento. "
                "Fale com seu contador para habilitá-la. 🙏"
            )

        ctx = {"cliente": cliente, "perfil": perfil}
        resultado = self.adapter.consultar(recurso, filtros, ctx)
        registrar(
            "agente_erp_consulta",
            {
                "intencao": intencao,
                "recurso": recurso,
                "filtros": filtros,
                "ok": resultado.ok,
                "erro": resultado.erro_padronizado,
            },
            cliente=cliente,
        )

        if not resultado.ok:
            logger.warning("consulta_erp_falhou", recurso=recurso, erro=resultado.erro_padronizado)
            return (
                "Não consegui consultar essa informação agora "
                f"(motivo: {resultado.erro_padronizado}). Pode tentar de novo?"
            )
        return self._formatar(recurso, resultado.dados)

    # ------------------------------------------------------------------
    # Formatação simples (Semana 1) — a resposta natural vem com o LLM (S2)
    # ------------------------------------------------------------------
    def _formatar(self, recurso: str, dados: dict) -> str:
        if recurso == "estoque":
            linhas = [
                f"• {i['produto']}: {i['quantidade']} (mín. {i['minimo']})"
                + (" ⚠ abaixo do mínimo" if i["quantidade"] < i["minimo"] else "")
                for i in dados["itens"]
            ]
            return "Seu estoque agora:\n" + "\n".join(linhas)

        if recurso == "pedidos":
            if "pedido" in dados:
                p = dados["pedido"]
                return (
                    f"Pedido {p['id']} — {p['cliente']}: R$ {p['total']:.2f} "
                    f"({p['status']}, {p['data']})"
                )
            linhas = [
                f"• {p['id']} — {p['cliente']}: R$ {p['total']:.2f} ({p['status']})"
                for p in dados["itens"]
            ]
            return "Seus pedidos:\n" + "\n".join(linhas)

        if recurso in ("contas_pagar", "contas_receber"):
            titulo = "a pagar" if recurso == "contas_pagar" else "a receber"
            abertas = [c for c in dados["itens"] if c["status"] == "aberta"]
            total = sum(c["valor"] for c in abertas)
            linhas = [
                f"• {c.get('fornecedor', c.get('cliente'))}: R$ {c['valor']:.2f} (vence {c['vencimento']})"
                for c in abertas
            ]
            return (
                f"Contas {titulo} em aberto (total R$ {total:.2f}):\n" + "\n".join(linhas)
            )

        if recurso == "fluxo_caixa":
            fc = dados["fluxo_caixa"]
            return (
                f"Fluxo de caixa 💰\n"
                f"Saldo atual: R$ {fc['saldo_atual']:.2f}\n"
                f"Entradas previstas (7d): R$ {fc['entradas_previstas_7d']:.2f}\n"
                f"Saídas previstas (7d): R$ {fc['saidas_previstas_7d']:.2f}\n"
                f"Saldo projetado (7d): R$ {fc['saldo_projetado_7d']:.2f}"
            )

        return str(dados)
