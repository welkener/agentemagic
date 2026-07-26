"""
Agente ERP — recebe uma intenção de consulta e chama o adaptador de ERP pela
interface única, respeitando o motor de tiers.

O adaptador é resolvido por cliente (`resolver.py`): real (Conta Azul/Bling)
se houver credencial OAuth ativa para uma das `ferramentas_habilitadas` do
perfil, mock caso contrário — nunca falha silenciosamente, a escolha vai pro
log estruturado. Passar `adapter=` no construtor força um adaptador fixo
(usado pelos testes).
"""
from __future__ import annotations

import structlog

from apps.adapters.resolver import resolver_adapter_erp
from apps.audit.services import registrar
from apps.governance.tiers import tier_da_intencao, verificar_tier

logger = structlog.get_logger(__name__)

_INTEGRACOES_ERP_CONHECIDAS = ("conta_azul", "bling")


class AgenteErp:
    """Serviço que traduz intenções de consulta em chamadas ao adaptador."""

    def __init__(self, adapter=None):
        # Se injetado (testes), este adaptador é usado para todo cliente.
        self._adapter_fixo = adapter

    def _resolver_adapter(self, perfil, cliente):
        if self._adapter_fixo is not None:
            return self._adapter_fixo
        candidatas = [
            f for f in getattr(perfil, "ferramentas_habilitadas", []) if f in _INTEGRACOES_ERP_CONHECIDAS
        ]
        return resolver_adapter_erp(cliente, candidatas)

    def consultar(self, intencao: str, recurso: str, filtros: dict, perfil, cliente=None) -> str:
        """Executa uma consulta (Tier 0) e devolve resposta em texto simples."""
        tier = tier_da_intencao(intencao)
        if not verificar_tier(tier, perfil):
            return (
                "Essa operação não está liberada para o seu perfil no momento. "
                "Fale com seu contador para habilitá-la. 🙏"
            )

        adapter = self._resolver_adapter(perfil, cliente)
        ctx = {"cliente": cliente, "perfil": perfil}
        resultado = adapter.consultar(recurso, filtros, ctx)
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
            if resultado.erro_padronizado == "PAYLOAD_NAO_MAPEADO":
                # O ERP respondeu; nós é que ainda não sabemos ler o formato
                # dele. Dizer "tente de novo" seria mentira — tentar de novo
                # dá exatamente no mesmo.
                return (
                    "Consegui falar com seu ERP, mas ainda não sei ler o formato "
                    "da resposta dele para essa consulta. Já registrei para o time "
                    "ajustar — enquanto isso, seu contador consegue ver por lá. 🙏"
                )
            return (
                "Não consegui consultar essa informação agora "
                f"(motivo: {resultado.erro_padronizado}). Pode tentar de novo?"
            )
        try:
            return self._formatar(recurso, resultado.dados)
        except (KeyError, TypeError):
            # Adaptador real com payload que a formatação ainda não conhece —
            # ⚠ mapear o formato oficial da API antes de ativar em produção
            # (ContaAzulAdapter/BlingAdapter só normalizam erros por ora, não
            # o formato dos dados — ver apps/adapters/oauth2.py).
            logger.warning("formatacao_erp_incompativel", recurso=recurso)
            return "Consegui os dados, mas ainda não sei formatar essa resposta direito. Já registrei para ajustar."

    # ------------------------------------------------------------------
    # Formatação simples (Semana 1) — a resposta natural vem com o LLM (S2)
    # ------------------------------------------------------------------
    def _formatar(self, recurso: str, dados: dict) -> str:
        if recurso == "estoque":
            # `minimo` é opcional: o Bling não expõe estoque mínimo no saldo
            # (ver adapters/normalizacao.py). Sem o dado, o aviso "abaixo do
            # mínimo" some — inventar um limite seria dar ao cliente uma
            # informação de negócio que ninguém cadastrou.
            linhas = []
            for i in dados["itens"]:
                minimo = i.get("minimo")
                linha = f"• {i['produto']}: {i['quantidade']}"
                if minimo is not None:
                    linha += f" (mín. {minimo})"
                    if i["quantidade"] < minimo:
                        linha += " ⚠ abaixo do mínimo"
                linhas.append(linha)
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
