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

import re
import uuid
from typing import Literal

import structlog
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, Field

from apps.agents.agente_erp.services import AgenteErp
from apps.agents.agente_nf.models import Intencao
from apps.agents.agente_nf.services import (
    ErroCancelamento,
    cancelar_emissao,
    confirmar_emissao,
    solicitar_cancelamento,
)
from apps.audit.services import registrar
from apps.governance.tiers import tier_da_intencao, verificar_tier
from apps.security.models import Codigo2FA
from apps.security.services import (
    enviar_magic_link,
    exige_2fa,
    gerar_codigo_2fa,
    sessao_ativa,
    verificar_codigo_2fa,
)

logger = structlog.get_logger(__name__)

_MODELO_ROTEADOR = "groq:llama-3.1-8b-instant"
_MODELO_EXTRACAO = "groq:openai/gpt-oss-120b"

_PALAVRAS_NOTA = ("nota", "nfse", "nfs-e", "emitir", "emite")
_PALAVRAS_CONFIRMACAO = ("sim", "confirmar", "confirmo", "pode emitir", "ok", "👍", "✅")
_PALAVRAS_CANCELAMENTO = ("não", "nao", "cancelar", "cancela", "❌")

# Palavras-chave → (nome da intenção no catálogo de tiers, recurso do agenteERP)
# "relatório"/"vendas" caem em pedidos — é a leitura mais próxima que o
# catálogo atual cobre pra um pedido genérico de relatório (ver
# docs/magicbi-ondas-desenvolvimento.md, achado do smoke test 25/jul/2026).
_REGRAS_ERP = [
    (("estoque",), ("consultar_estoque", "estoque")),
    (("pedido", "pedidos", "relatório", "relatorio", "venda", "vendas"), ("consultar_pedido", "pedidos")),
    (("receber",), ("consultar_contas_receber", "contas_receber")),
    (("pagar",), ("consultar_contas_pagar", "contas_pagar")),
    (("caixa", "fluxo"), ("consultar_fluxo_caixa", "fluxo_caixa")),
]

# Sobre nota — as três intenções (emitir/consultar/cancelar) contêm a palavra
# "nota", então a desambiguação é por regex de palavra INTEIRA, não substring:
# "emiti" é prefixo de "emitir", e com `in` "emitir nfs-e" virava consulta.
_RE_CANCELAR_NOTA = re.compile(r"\b(cancelar|cancela|cancelamento|anular|anula)\b")
_RE_VERBO_EMITIR = re.compile(r"\b(emitir|emite|emita|emitindo|gerar|gera|fazer|faz)\b")
_RE_CONSULTAR_NOTA = re.compile(
    r"\b(quais|quantas|minhas|minha|consultar|consulta|ver|listar|lista|"
    r"emiti|emitida|emitidas|emitidos|cade|cadê)\b"
)

_INTENCOES_VALIDAS = (
    "emitir_nota",
    "consultar_nota",
    "cancelar_nota",
    *[nome for _, (nome, _) in _REGRAS_ERP],
    "desconhecida",
)


class IntencaoClassificada(BaseModel):
    """Saída tipada do roteador — o núcleo decide o que fazer com cada valor."""

    intencao: Literal[
        "emitir_nota",
        "consultar_nota",
        "cancelar_nota",
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

        if not sessao_ativa(cliente):
            return self._exigir_revalidacao(cliente)

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

        if intencao_nome == "consultar_nota":
            return self._consultar_notas(cliente, perfil)

        if intencao_nome == "cancelar_nota":
            return self._pedir_cancelamento(mensagem, cliente)

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
            except Exception as exc:
                logger.warning("groq_roteador_indisponivel_fallback_palavra_chave", erro=str(exc))
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
        # Falou em nota: decidir QUAL das três intenções. A ordem é o que
        # resolve os casos ambíguos de verdade:
        #   1. cancelar ganha de tudo ("quero cancelar minha nota");
        #   2. verbo de emissão ganha da consulta ("emitir MINHA nota" é
        #      emissão, apesar do "minha", que é palavra de consulta);
        #   3. sobrou palavra de consulta → consulta ("minhas notas");
        #   4. default emitir — preserva o comportamento anterior.
        if any(p in texto for p in _PALAVRAS_NOTA):
            if _RE_CANCELAR_NOTA.search(texto):
                return "cancelar_nota"
            if _RE_VERBO_EMITIR.search(texto):
                return "emitir_nota"
            if _RE_CONSULTAR_NOTA.search(texto):
                return "consultar_nota"
            return "emitir_nota"
        for palavras, (nome, _recurso) in _REGRAS_ERP:
            if any(p in texto for p in palavras):
                return nome
        return "desconhecida"

    # ------------------------------------------------------------------
    # Notas já emitidas — consulta (Tier 0) e cancelamento (Tier 3)
    # ------------------------------------------------------------------
    def _consultar_notas(self, cliente, perfil) -> str:
        tier = tier_da_intencao("consultar_nota")
        if perfil is None or not verificar_tier(tier, perfil):
            return (
                "Consulta de notas ainda não está liberada para o seu perfil. "
                "Fale com seu contador para habilitá-la. 🙏"
            )

        notas = Intencao.objects.filter(
            cliente=cliente, tipo_acao="emitir_nfse", estado=Intencao.Estado.CONCLUIDO
        ).order_by("-atualizado_em")[:5]

        if not notas:
            return "Você ainda não tem nenhuma nota emitida por aqui. 🧾"

        linhas = ["Suas últimas notas emitidas 🧾", ""]
        for nota in notas:
            valor = nota.payload.get("valor")
            valor_txt = f"R$ {valor:.2f}" if isinstance(valor, (int, float)) else "—"
            status = " ❌ CANCELADA" if nota.cancelada else ""
            linhas.append(
                f"• {nota.atualizado_em.strftime('%d/%m/%Y')} — {valor_txt} — "
                f"{nota.payload.get('tomador') or 'sem tomador'}{status}"
            )
            linhas.append(f"  protocolo {nota.protocolo or '—'}")
            if nota.danfse_url and not nota.cancelada:
                linhas.append(f"  DANFSE: {nota.danfse_url}")
        return "\n".join(linhas)

    def _pedir_cancelamento(self, mensagem: str, cliente) -> str:
        """Cliente NUNCA cancela sozinho — o pedido vai pro contador.

        Cancelar documento fiscal tem efeito contábil e prazo legal; a decisão
        é de quem responde tecnicamente por ela. Ver `solicitar_cancelamento`
        em `agents/agente_nf/services.py` pro porquê isso não depende do
        `tier_maximo` do perfil.
        """
        nota = (
            Intencao.objects.filter(
                cliente=cliente,
                tipo_acao="emitir_nfse",
                estado=Intencao.Estado.CONCLUIDO,
                cancelada_em__isnull=True,
            )
            .order_by("-atualizado_em")
            .first()
        )
        if nota is None:
            return "Não encontrei nenhuma nota emitida que possa ser cancelada. 🤔"

        try:
            solicitar_cancelamento(nota, motivo=mensagem.strip(), origem="whatsapp")
        except ErroCancelamento as exc:
            return f"{exc} 🙏"

        valor = nota.payload.get("valor")
        valor_txt = f"R$ {valor:.2f}" if isinstance(valor, (int, float)) else "—"
        return (
            "Registrei seu pedido de cancelamento 📩\n\n"
            f"Nota: {nota.protocolo}\n"
            f"Valor: {valor_txt}\n"
            f"Emitida em: {nota.atualizado_em.strftime('%d/%m/%Y')}\n\n"
            "Cancelamento de nota fiscal precisa passar pelo seu contador — "
            "ele foi avisado e vai analisar. Te aviso assim que houver resposta. 🙏"
        )

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
        codigo_pendente = (
            Codigo2FA.objects.filter(intencao=intencao, usado_em__isnull=True)
            .order_by("-criado_em")
            .first()
        )
        if codigo_pendente is not None:
            return self._resolver_2fa(intencao, codigo_pendente, mensagem)

        texto = mensagem.lower().strip()
        if any(p in texto for p in _PALAVRAS_CANCELAMENTO):
            cancelar_emissao(intencao, motivo="cliente cancelou")
            return "Combinado, cancelei a emissão. Se precisar, é só chamar de novo. 👍"

        if not any(p in texto for p in _PALAVRAS_CONFIRMACAO):
            return (
                "Não entendi. Responda *sim* para confirmar a emissão da nota "
                "pendente ou *não* para cancelar."
            )

        if exige_2fa(intencao):
            registro = gerar_codigo_2fa(intencao)
            if registro is None:
                return (
                    "Essa emissão passa do limite de segurança do seu perfil e "
                    "precisa de um código extra, mas não encontrei um e-mail "
                    "cadastrado pra te mandar. Fale com seu contador na Rotina "
                    "pra completar o cadastro. 🙏"
                )
            return (
                "Por segurança, essa emissão passa do limite configurado pro "
                "seu perfil. Te mandei um código de 6 dígitos por e-mail — "
                "só responder aqui com o código. 🔒"
            )

        return self._emitir(intencao)

    def _resolver_2fa(self, intencao: Intencao, codigo_pendente: Codigo2FA, mensagem: str) -> str:
        if codigo_pendente.expira_em <= timezone.now():
            cancelar_emissao(intencao, motivo="código 2FA expirou")
            return "O código expirou. Cancelei a emissão por segurança — pode iniciar de novo."

        if verificar_codigo_2fa(codigo_pendente, mensagem):
            return self._emitir(intencao)

        if codigo_pendente.tentativas >= Codigo2FA.LIMITE_TENTATIVAS:
            cancelar_emissao(intencao, motivo="2FA excedeu o número de tentativas")
            return "Código incorreto demais vezes. Cancelei a emissão por segurança — pode iniciar de novo."

        return "Código inválido. Confere o e-mail e tenta de novo. 🔒"

    def _emitir(self, intencao: Intencao) -> str:
        resultado = confirmar_emissao(intencao, motivo="cliente confirmou")

        if resultado.ok:
            return (
                "Nota emitida com sucesso! 🎉\n"
                f"Protocolo: {resultado.protocolo}\n"
                f"DANFSE: {resultado.danfse_url}"
            )

        return (
            "A nota foi rejeitada pela Sefin 😕 "
            f"(motivo: {resultado.erro}). Ajusto os dados e você confirma de novo?"
        )

    def _exigir_revalidacao(self, cliente) -> str:
        enviado = enviar_magic_link(cliente, cliente.telefone_whatsapp)
        if enviado:
            return (
                "Sua sessão expirou por segurança. Te mandei um link de "
                "validação por e-mail — clique nele e volte aqui pra "
                "continuar. 🔒"
            )
        return (
            "Sua sessão expirou por segurança e não encontrei um e-mail "
            "cadastrado pra te mandar o link. Fale com seu contador na "
            "Rotina pra revalidar. 🙏"
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
            except Exception as exc:
                logger.warning("groq_extracao_indisponivel_fallback_menu", erro=str(exc))
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
