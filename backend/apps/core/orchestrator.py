"""
Orquestrador determinístico (Opção A da arquitetura) — Semana 2.

Regra inviolável: o LLM PROPÕE, o núcleo determinístico DECIDE e EXECUTA.

Roteamento e extração de campos usam Pydantic AI sobre **Groq** (barato/rápido,
docs/magicbi-hermes-comunicador.md §7):
- roteador de intenção: `llama-3.1-8b-instant`
- extração de campos da nota: `openai/gpt-oss-120b` (schema Pydantic tipado,
  com retry automático de validação pelo próprio Pydantic AI)

Guard de saída: o LLM nunca decide CNAE/alíquota — vem do cadastro do cliente
(`Cliente.cnae_padrao`), nunca inferido pelo modelo.

Sem `GROQ_API_KEY`, ou se a chamada falhar, o orquestrador cai no roteamento
determinístico por palavra-chave — fallback documentado em
docs/requisitos-dev-piloto-rotina.md §10.5 ("Se a API de IA cair").
"""
from __future__ import annotations

import uuid
from typing import Literal

import structlog
from django.conf import settings
from pydantic import BaseModel, Field

from apps.adapters.nfse_mock import NfseMockAdapter
from apps.agents.agente_erp.services import AgenteErp
from apps.agents.agente_nf.models import Intencao
from apps.audit.services import registrar
from apps.governance.tiers import tier_da_intencao, verificar_tier

logger = structlog.get_logger(__name__)

_MODELO_ROTEADOR = "groq:llama-3.1-8b-instant"
_MODELO_EXTRACAO = "groq:openai/gpt-oss-120b"

_PALAVRAS_NOTA = ("nota", "nfse", "nfs-e", "emitir", "emite")
_PALAVRAS_CONFIRMACAO = ("sim", "confirmar", "confirmo", "pode emitir", "ok", "👍", "✅")
_PALAVRAS_CANCELAMENTO = ("não", "nao", "cancelar", "cancela", "❌")

# Palavras-chave → (nome da intenção no catálogo de tiers, recurso do agenteERP)
_REGRAS_ERP = [
    (("estoque",), ("consultar_estoque", "estoque")),
    (("pedido", "pedidos"), ("consultar_pedido", "pedidos")),
    (("receber",), ("consultar_contas_receber", "contas_receber")),
    (("pagar",), ("consultar_contas_pagar", "contas_pagar")),
    (("caixa", "fluxo"), ("consultar_fluxo_caixa", "fluxo_caixa")),
]

_INTENCOES_VALIDAS = ("emitir_nota", *[nome for _, (nome, _) in _REGRAS_ERP], "desconhecida")


class IntencaoClassificada(BaseModel):
    """Saída tipada do roteador — o núcleo decide o que fazer com cada valor."""

    intencao: Literal[
        "emitir_nota",
        "consultar_estoque",
        "consultar_pedido",
        "consultar_contas_receber",
        "consultar_contas_pagar",
        "consultar_fluxo_caixa",
        "desconhecida",
    ]


class DadosNotaExtraidos(BaseModel):
    """Campos extraídos da mensagem para emitir NFS-e — nunca inclui CNAE/alíquota."""

    tomador: str | None = Field(None, description="Nome de quem recebeu o serviço")
    valor: float | None = Field(None, description="Valor do serviço em reais")
    descricao_servico: str | None = Field(None, description="Descrição do serviço prestado")


def _groq_disponivel() -> bool:
    return bool(getattr(settings, "GROQ_API_KEY", ""))


class Orquestrador:
    """Núcleo de decisão: resolve perfil, aplica tier e despacha ao subagente."""

    def __init__(self):
        self._agente_erp = AgenteErp()
        self._nfse = NfseMockAdapter()

    def processar(self, mensagem: str, cliente, message_id: str | None = None) -> str:
        """Processa uma mensagem do WhatsApp e devolve o texto de resposta.

        `cliente` pode ser None (número desconhecido). `message_id` alimenta a
        chave de idempotência de qualquer escrita fiscal disparada por esta
        mensagem (retry do Celery não duplica uma emissão).
        """
        if cliente is None:
            return (
                "Olá! Ainda não encontrei seu cadastro aqui na Magic BI. "
                "Fale com a Rotina Contábil para ativar seu atendimento. 😊"
            )

        perfil = getattr(cliente, "perfil", None)

        pendente = (
            Intencao.objects.filter(cliente=cliente, estado=Intencao.Estado.AGUARDANDO_APROVACAO)
            .order_by("-criado_em")
            .first()
        )
        if pendente is not None:
            return self._resolver_confirmacao(pendente, mensagem)

        intencao_nome = self._classificar_intencao(mensagem)
        registrar(
            "orquestrador_mensagem_processada",
            {"mensagem": mensagem, "intencao": intencao_nome},
            cliente=cliente,
        )

        if intencao_nome == "emitir_nota":
            return self._iniciar_emissao(mensagem, cliente, perfil, message_id)

        for _, (nome, recurso) in _REGRAS_ERP:
            if nome == intencao_nome:
                return self._agente_erp.consultar(
                    intencao=intencao_nome,
                    recurso=recurso,
                    filtros={},
                    perfil=perfil,
                    cliente=cliente,
                )

        return (
            "Oi! Eu sou o Lumen, assistente da Magic BI. 💫\n"
            "Posso te ajudar com: consulta de estoque, pedidos, contas a pagar/"
            "receber, fluxo de caixa e emissão de notas fiscais. É só perguntar!"
        )

    # ------------------------------------------------------------------
    # Roteamento de intenção
    # ------------------------------------------------------------------
    def _classificar_intencao(self, mensagem: str) -> str:
        if _groq_disponivel():
            try:
                return self._classificar_via_groq(mensagem)
            except Exception:
                logger.warning("groq_roteador_indisponivel_fallback_palavra_chave")
        return self._classificar_por_palavra_chave(mensagem)

    def _classificar_via_groq(self, mensagem: str) -> str:
        from pydantic_ai import Agent

        agent = Agent(
            _MODELO_ROTEADOR,
            output_type=IntencaoClassificada,
            system_prompt=(
                "Você classifica a intenção de mensagens de clientes de um "
                "assistente fiscal/financeiro no WhatsApp. Responda apenas com "
                "a intenção mais provável, entre as opções do schema."
            ),
        )
        resultado = agent.run_sync(mensagem)
        return resultado.output.intencao

    def _classificar_por_palavra_chave(self, mensagem: str) -> str:
        texto = mensagem.lower()
        if any(p in texto for p in _PALAVRAS_NOTA):
            return "emitir_nota"
        for palavras, (nome, _recurso) in _REGRAS_ERP:
            if any(p in texto for p in palavras):
                return nome
        return "desconhecida"

    # ------------------------------------------------------------------
    # Emissão de NFS-e (Fiscus) — fluxo de 2 turnos com confirmação Tier 1
    # ------------------------------------------------------------------
    def _iniciar_emissao(self, mensagem: str, cliente, perfil, message_id: str | None) -> str:
        tier = tier_da_intencao("emitir_nota")
        if perfil is None or not verificar_tier(tier, perfil):
            return (
                "Emissão de nota fiscal ainda não está liberada para o seu "
                "perfil. Fale com seu contador para habilitar. 🙏"
            )

        dados = self._extrair_dados_nota(mensagem)
        faltantes = [
            campo
            for campo, valor in (
                ("tomador", dados.tomador),
                ("valor", dados.valor),
                ("descrição do serviço", dados.descricao_servico),
            )
            if not valor
        ]
        if faltantes:
            return (
                "Quase lá! Ainda preciso de: " + ", ".join(faltantes) + ". "
                "Pode me mandar de novo com esses dados? 🧾"
            )

        if not cliente.cnae_padrao:
            return (
                "Seu cadastro ainda não tem o CNAE de serviço configurado. "
                "Fale com seu contador na Rotina para completar o cadastro "
                "antes da primeira emissão. 🙏"
            )

        payload = {
            "cnpj_prestador": cliente.cnpj,
            "cnae": cliente.cnae_padrao,
            "valor": dados.valor,
            "descricao_servico": dados.descricao_servico,
            "tomador": dados.tomador,
        }
        chave = f"nfse-{message_id}" if message_id else f"nfse-{cliente.id}-{uuid.uuid4().hex[:12]}"

        intencao, criada = Intencao.objects.get_or_create(
            chave_idempotencia=chave,
            defaults={"cliente": cliente, "tipo_acao": "emitir_nfse", "payload": payload},
        )
        if not criada:
            # Reprocessamento (retry do Celery) da mesma mensagem — não duplica.
            return self._mensagem_para_intencao_existente(intencao)

        intencao.transicionar(Intencao.Estado.VALIDANDO, motivo="campos extraídos e CNAE do cadastro")
        intencao.transicionar(Intencao.Estado.AGUARDANDO_APROVACAO, motivo="aguardando confirmação Tier 1")

        return (
            "Confirma a emissão desta nota? 🧾\n"
            f"Tomador: {dados.tomador}\n"
            f"Valor: R$ {dados.valor:.2f}\n"
            f"Serviço: {dados.descricao_servico}\n"
            "Responda *sim* para emitir ou *não* para cancelar."
        )

    def _resolver_confirmacao(self, intencao: Intencao, mensagem: str) -> str:
        texto = mensagem.lower().strip()
        if any(p in texto for p in _PALAVRAS_CANCELAMENTO):
            intencao.transicionar(Intencao.Estado.CANCELADO, motivo="cliente cancelou")
            return "Combinado, cancelei a emissão. Se precisar, é só chamar de novo. 👍"

        if not any(p in texto for p in _PALAVRAS_CONFIRMACAO):
            return (
                "Não entendi. Responda *sim* para confirmar a emissão da nota "
                "pendente ou *não* para cancelar."
            )

        intencao.transicionar(Intencao.Estado.EMITINDO, motivo="cliente confirmou")
        resultado = self._nfse.emitir("nfse", intencao.payload, {"cliente": intencao.cliente})

        if resultado.ok:
            intencao.transicionar(Intencao.Estado.CONCLUIDO, motivo="emissão autorizada")
            return (
                "Nota emitida com sucesso! 🎉\n"
                f"Protocolo: {resultado.dados['protocolo']}\n"
                f"DANFSE: {resultado.dados['danfse_url']}"
            )

        intencao.transicionar(Intencao.Estado.REJEITADO, motivo=resultado.erro_padronizado or "rejeicao")
        return (
            "A nota foi rejeitada pela Sefin 😕 "
            f"(motivo: {resultado.erro_padronizado}). Ajusto os dados e você confirma de novo?"
        )

    def _mensagem_para_intencao_existente(self, intencao: Intencao) -> str:
        if intencao.estado == Intencao.Estado.AGUARDANDO_APROVACAO:
            return (
                "Essa nota já está aguardando sua confirmação. Responda *sim* "
                "para emitir ou *não* para cancelar."
            )
        if intencao.estado == Intencao.Estado.CONCLUIDO:
            return "Essa nota já foi emitida anteriormente. Se precisar da 2ª via, fale com seu contador."
        if intencao.estado in (Intencao.Estado.REJEITADO, Intencao.Estado.CANCELADO):
            return "Essa tentativa de emissão já foi encerrada. Me manda os dados de novo se quiser tentar outra vez."
        return "Já estou processando essa emissão, só um instante. 🙏"

    # ------------------------------------------------------------------
    # Extração de campos da nota
    # ------------------------------------------------------------------
    def _extrair_dados_nota(self, mensagem: str) -> DadosNotaExtraidos:
        if _groq_disponivel():
            try:
                return self._extrair_via_groq(mensagem)
            except Exception:
                logger.warning("groq_extracao_indisponivel_fallback_menu")
        return DadosNotaExtraidos()

    def _extrair_via_groq(self, mensagem: str) -> DadosNotaExtraidos:
        from pydantic_ai import Agent

        agent = Agent(
            _MODELO_EXTRACAO,
            output_type=DadosNotaExtraidos,
            system_prompt=(
                "Extraia tomador, valor (em reais) e descrição do serviço da "
                "mensagem do cliente. Nunca invente CNAE ou alíquota — isso não "
                "é decisão sua. Deixe um campo nulo se não estiver claro na mensagem."
            ),
        )
        resultado = agent.run_sync(mensagem)
        return resultado.output
