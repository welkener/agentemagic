"""
Orquestrador determinístico (Opção A da arquitetura).

Regra inviolável: o LLM PROPÕE, o núcleo determinístico DECIDE e EXECUTA.

**O que sobrou aqui depois do Sprint 2.** Sessão, continuações em aberto, a
escada de modelo (DEC-08) e o despacho para o catálogo de ferramentas. O que
saiu: a condução de cada assunto, que virou `apps/agents/ferramentas/` (o
catálogo) e `apps/agents/agente_nf/conversa.py` (o fluxo da nota). O `if/elif`
de nove intenções que morava neste arquivo era o lugar onde tier, roteamento e
execução se misturavam — e onde uma ferramenta nova teria que ser lembrada em
três pontos diferentes.

**A escada de modelo**, em ordem:

1. `t0` — determinístico (`core/t0.py`): saudação, menu e intenção de alta
   confiança. Instantâneo e grátis; meta de 40% das mensagens.
2. `t1` — Groq, roteando pelo prompt do tenant (`agents/prompt.py`), com o
   schema restrito ao que aquele cliente pode executar.
3. `fallback` — palavra-chave permissiva, só quando não há modelo: sem chave,
   provedor fora do ar ou tenant acima do teto de gasto.

O guard de saída continua: o LLM nunca decide CNAE/alíquota — vem do cadastro do
cliente, nunca inferido pelo modelo (ver `agente_nf/conversa.iniciar_emissao`).
"""
from __future__ import annotations

import re
import time

import structlog

from apps.agents import ferramentas, llm, prompt as prompt_tenant
from apps.agents.agente_nf import conversa
from apps.agents.contexto import SessionContext
from apps.audit.services import registrar
from apps.core import t0
from apps.security.services import enviar_magic_link, sessao_ativa

logger = structlog.get_logger(__name__)

_PALAVRAS_NOTA = ("nota", "nfse", "nfs-e", "emitir", "emite")

# Sobre nota — as três intenções (emitir/consultar/cancelar) contêm a palavra
# "nota", então a desambiguação é por regex de palavra INTEIRA, não substring:
# "emiti" é prefixo de "emitir", e com `in` "emitir nfs-e" virava consulta.
_RE_CANCELAR_NOTA = re.compile(r"\b(cancelar|cancela|cancelamento|anular|anula)\b")
_RE_VERBO_EMITIR = re.compile(r"\b(emitir|emite|emita|emitindo|gerar|gera|fazer|faz)\b")
_RE_CONSULTAR_NOTA = re.compile(
    r"\b(quais|quantas|minhas|minha|consultar|consulta|ver|listar|lista|"
    r"emiti|emitida|emitidas|emitidos|cade|cadê)\b"
)

# Palavras-chave → ferramenta, para o fallback sem modelo. Continua existindo
# separado do T0 estrito de propósito: aqui o chute é aceitável porque não há
# alternativa; lá não é, porque há.
_REGRAS_ERP = (
    (("estoque",), "consultar_estoque"),
    (("pedido", "pedidos", "relatório", "relatorio", "venda", "vendas"), "consultar_pedido"),
    (("receber",), "consultar_contas_receber"),
    (("pagar",), "consultar_contas_pagar"),
    (("caixa", "fluxo"), "consultar_fluxo_caixa"),
)


# Tudo que o roteador pode emitir — derivado do catálogo, não repetido à mão.
# A lista escrita a dedo já divergiu uma vez do `CATALOGO_TIERS` e duas consultas
# legítimas responderam "não liberado" em produção por uma semana. Agora só há
# uma fonte, e `tests/test_governance.py` cruza as duas.
_INTENCOES_VALIDAS = (*ferramentas.nomes(), "desconhecida")


class Orquestrador:
    """Núcleo de decisão: resolve contexto, aplica a escada e despacha."""

    def __init__(self):
        # Qual camada da escada resolveu a última mensagem: t0 | t1 | fallback.
        # Vai para a trilha e é o que permite medir a meta do DEC-08.
        self._camada = "t1"

    def processar(
        self,
        mensagem: str,
        cliente=None,
        message_id: str | None = None,
        wa_id: str | None = None,
        usuario=None,
        ctx: SessionContext | None = None,
    ) -> str:
        """Processa uma mensagem do WhatsApp e devolve o texto de resposta.

        `ctx` é o caminho novo, montado pelo canal (`pipeline.processar`), e é
        por ele que escopo entra nas ferramentas. Os parâmetros soltos continuam
        aceitos para chamadas internas e testes, que montam o contexto a partir
        do cliente — em produção quem sabe o escritório e a pessoa é o webhook,
        não este método.
        """
        if ctx is None:
            if cliente is None:
                return self._sem_cadastro()
            ctx = SessionContext.da_conversa(
                cliente=cliente, usuario=usuario, wa_id=wa_id or "", message_id=message_id
            )
        if ctx.cliente is None:
            return self._sem_cadastro()

        llm.zerar_contador()
        inicio = time.perf_counter()
        intencao, resposta = self._conduzir(ctx, mensagem)
        self._registrar(ctx, mensagem, intencao, inicio)
        return resposta

    # ------------------------------------------------------------------
    # Condução
    # ------------------------------------------------------------------
    def _conduzir(self, ctx: SessionContext, mensagem: str) -> tuple[str, str]:
        """Devolve `(rótulo para a trilha, resposta)`.

        A ordem dos ramos é a parte sensível, e cada um está onde está por um
        motivo que já custou um defeito: sessão antes de tudo; confirmação antes
        de roteamento (senão "sim" vira saudação); coleta antes do T0 ("100
        reais" sozinho não classifica em roteador nenhum).
        """
        if not sessao_ativa(ctx.cliente, ctx.telefone_de_quem_escreve):
            self._camada = "t0"
            return "revalidacao", self._exigir_revalidacao(ctx)

        pendente = conversa.confirmacao_pendente(ctx)
        if pendente is not None:
            resposta = conversa.resolver_confirmacao(ctx, pendente, mensagem)
            self._camada = self._camada_pelo_consumo()
            return "confirmacao", resposta

        em_coleta = conversa.coleta_em_aberto(ctx)
        if em_coleta is not None:
            retomada = conversa.continuar_coleta(
                ctx, em_coleta, mensagem, lambda texto: self._classificar_intencao(ctx, texto)
            )
            if retomada.reclassificar is None:
                self._camada = self._camada_pelo_consumo()
                return "coleta", retomada.resposta
            # O cliente mudou de assunto no meio da coleta. A intenção nova já
            # foi classificada lá dentro (`_classificar_intencao` rodou), então
            # `self._camada` já está certa — só falta executar.
            return retomada.reclassificar, self._despachar(ctx, retomada.reclassificar, mensagem)

        # T0 na frente (DEC-08): saudação, menu e agradecimento são volume puro
        # e não dependem de dado nenhum — pagar um LLM por eles é desperdício de
        # dinheiro e de segundos. Vem depois da coleta e da confirmação de
        # propósito: "ok" no meio de uma emissão é confirmação, não simpatia.
        pronta = t0.responder(mensagem)
        if pronta is not None:
            self._camada = "t0"
            return "conversa", pronta

        intencao = self._classificar_intencao(ctx, mensagem)
        return intencao, self._despachar(ctx, intencao, mensagem)

    def _despachar(self, ctx: SessionContext, intencao: str, mensagem: str) -> str:
        """Intenção → ferramenta do catálogo. Desconhecida vira o menu do cliente."""
        resposta = ferramentas.executar(intencao, ctx, mensagem)
        if resposta is None:
            return prompt_tenant.menu_de_capacidades(ctx)
        return resposta

    # ------------------------------------------------------------------
    # Roteamento de intenção
    # ------------------------------------------------------------------
    def _classificar_intencao(self, ctx: SessionContext, mensagem: str) -> str:
        """Escada de modelo: T0 estrito → Groq (T1) → palavra-chave permissiva.

        A camada que decidiu fica em `self._camada`, e não no retorno, para que
        o método continue devolvendo só a intenção — vários testes o substituem
        por um `lambda` de uma linha, e uma tupla os quebraria sem que o produto
        tivesse mudado.
        """
        estrita = t0.classificar(mensagem)
        if estrita is not None:
            self._camada = "t0"
            return estrita

        if llm.disponivel():
            try:
                intencao = self._classificar_via_groq(ctx, mensagem)
                self._camada = "t1"
                return intencao
            except llm.SemOrcamento as exc:
                # Teto de gasto do tenant estourado. Tratado como
                # indisponibilidade, e não como erro de conversa: o cliente final
                # não tem nada a ver com a fatura do escritório dele.
                logger.info("roteador_sem_orcamento_fallback_palavra_chave", motivo=str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("groq_roteador_indisponivel_fallback_palavra_chave", erro=str(exc))

        # Aqui o chute é aceitável — e é por isso que este classificador NÃO é o
        # do T0. Sem LLM, responder alguma coisa plausível vale mais que recusar;
        # com LLM disponível, deixar o palpite passar na frente dele emitiria
        # nota a partir de mensagem ambígua.
        self._camada = "fallback"
        return self._classificar_por_palavra_chave(mensagem)

    def _classificar_via_groq(self, ctx: SessionContext, mensagem: str) -> str:
        saida = llm.executar(
            ctx=ctx,
            etapa=llm.ETAPA_ROTEADOR,
            output_type=prompt_tenant.schema_para(ctx),
            system_prompt=prompt_tenant.system_prompt_para(ctx),
            mensagem=mensagem,
        )
        return saida.intencao

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
        for palavras, nome in _REGRAS_ERP:
            if any(p in texto for p in palavras):
                return nome
        return "desconhecida"

    # ------------------------------------------------------------------
    # Trilha e mensagens de plataforma
    # ------------------------------------------------------------------
    def _camada_pelo_consumo(self) -> str:
        """Camada dos ramos que não passam pelo roteador (confirmação, coleta).

        Ali não há classificação de intenção, então a pergunta que sobra é a que
        de fato importa para a meta do DEC-08: **esta mensagem custou um modelo?**
        Confirmação nunca custa; coleta custa quando a extração roda. Marcar os
        dois como `t1` por omissão — que era o efeito de não registrar nada —
        deixava a taxa de T0 pessimista, e pessimista errado é tão ruim quanto
        otimista errado quando o número decide arquitetura.
        """
        return "t1" if llm.chamadas_feitas() else "t0"

    def _registrar(self, ctx: SessionContext, mensagem: str, intencao: str, inicio: float) -> None:
        registrar(
            "orquestrador_mensagem_processada",
            {
                "mensagem": mensagem,
                "intencao": intencao,
                # `camada` torna a meta do DEC-08 ("T0 resolve ≥ 40%") verificável
                # com número real; `latencia_ms` é de onde sai o p95 do gate do
                # Sprint 2, e precisa cobrir TODA mensagem — inclusive as baratas,
                # senão o percentil sai calculado só sobre as caras.
                "camada": self._camada,
                "latencia_ms": int((time.perf_counter() - inicio) * 1000),
                "chamadas_llm": llm.chamadas_feitas(),
                **ctx.para_trilha(),
            },
            cliente=ctx.cliente,
        )

    def _sem_cadastro(self) -> str:
        return (
            "Olá! Ainda não encontrei seu cadastro aqui na Magic BI. "
            "Fale com a Rotina Contábil para ativar seu atendimento. 😊"
        )

    def _exigir_revalidacao(self, ctx: SessionContext) -> str:
        # O link revalida o número que está escrevendo — é ele que vai ficar
        # gravado como `wa_id` da sessão.
        enviado = enviar_magic_link(ctx.cliente, ctx.telefone_de_quem_escreve)
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
