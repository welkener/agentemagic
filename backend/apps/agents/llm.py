"""
O único lugar do sistema que fala com um modelo de linguagem.

Existe para que três coisas sejam verdade por construção, e não por disciplina:

1. **Todo token consumido é medido** (DEC-08 item 2). Se houvesse dois caminhos
   até o modelo, o custo por tenant seria uma estimativa com asterisco — e o
   critério de aceite de R$ 0,60/cliente/mês pede número, não estimativa.
2. **O teto de gasto do tenant é respeitado** (DEC-08 item 3), porque a escolha
   do modelo acontece aqui e olha para o orçamento antes de chamar.
3. **Nenhum schema chega ao modelo sem passar pelo registry** (DEC-05) — este
   módulo chama `criar_agente`, que recusa schema não registrado.

**A escada de modelos por etapa.** Roteador e extração não têm a mesma
exigência: classificar entre nove intenções é trabalho de modelo pequeno,
enquanto extrair tomador/valor/descrição de uma frase corrida erra mais e custa
mais caro errar (nota fiscal com valor errado). Por isso a extração usa o modelo
maior enquanto há orçamento, e é ela — não o roteador — que cai primeiro quando
o teto aperta: o roteador já roda no modelo mais barato disponível, então
degradá-lo não economizaria nada.

**Falha de medição nunca derruba atendimento.** Mesmo princípio de
`observabilidade/alertas.py`: a resposta ao cliente já foi produzida; não
conseguir gravar quanto ela custou é problema de contabilidade interna, e
transformá-lo em erro de conversa seria trocar um prejuízo pequeno e conhecido
por um grande e visível.
"""
from __future__ import annotations

import contextvars
import time

import structlog
from django.conf import settings

from apps.agents.registry import criar_agente
from apps.observabilidade import orcamento, precos

logger = structlog.get_logger(__name__)

ETAPA_ROTEADOR = "roteador"
ETAPA_EXTRACAO = "extracao"

# Groq permanece no T1 (DEC-08 item 4). A troca de provedor para T2/T3 fica
# condicionada a medição — que é justamente o que este módulo passa a produzir.
_ESCADA = {
    ETAPA_ROTEADOR: {
        orcamento.MODO_NORMAL: "groq:llama-3.1-8b-instant",
        orcamento.MODO_DEGRADADO: "groq:llama-3.1-8b-instant",
        orcamento.MODO_CORTADO: None,
    },
    ETAPA_EXTRACAO: {
        orcamento.MODO_NORMAL: "groq:openai/gpt-oss-120b",
        # A degradação de verdade acontece aqui: campo extraído por modelo menor
        # erra mais, e o fluxo já sabe lidar com campo faltando — pergunta de
        # novo. Errar para o lado de perguntar é aceitável; parar não é.
        orcamento.MODO_DEGRADADO: "groq:llama-3.1-8b-instant",
        orcamento.MODO_CORTADO: None,
    },
}


# Quantas vezes o modelo foi chamado no atendimento da mensagem corrente.
#
# `ContextVar` e não atributo de instância porque quem precisa da resposta (o
# orquestrador, para rotular a camada na trilha) está a três chamadas de
# distância de quem sabe (`executar`), e passar o número de volta por essa
# cadeia obrigaria coleta, extração e confirmação a devolver tuplas só para
# carregá-lo. Contexto assíncrono ou thread separada não se misturam — é a
# propriedade que um contador de módulo não teria.
_CHAMADAS = contextvars.ContextVar("llm_chamadas", default=0)


def zerar_contador() -> None:
    """Começo de mensagem — chamado pelo orquestrador, uma vez por atendimento."""
    _CHAMADAS.set(0)


def chamadas_feitas() -> int:
    """Quantas chamadas ao modelo esta mensagem custou. Zero = resolvida no T0."""
    return _CHAMADAS.get()


class SemOrcamento(RuntimeError):
    """O tenant estourou o teto do mês — nenhuma chamada ao modelo agora.

    Quem chama trata como indisponibilidade e cai no determinístico, exatamente
    como faria se o Groq estivesse fora do ar. É de propósito que os dois
    caminhos sejam o mesmo: já existe fallback testado para "sem LLM", e criar um
    segundo para "sem dinheiro" dobraria a superfície sem dobrar a garantia.
    """


def disponivel() -> bool:
    """Há chave de API configurada. Sem ela, nada aqui é tentado."""
    return bool(getattr(settings, "GROQ_API_KEY", ""))


def modelo_para(etapa: str, escritorio) -> str | None:
    """Modelo que este tenant pode usar nesta etapa agora, ou None se cortado."""
    return _ESCADA[etapa][orcamento.modo(escritorio)]


def executar(*, ctx, etapa: str, output_type, system_prompt: str, mensagem: str):
    """Chama o modelo, mede e devolve a saída tipada.

    Levanta `SemOrcamento` quando o tenant está cortado e propaga qualquer erro
    do provedor — os dois casos já têm tratamento no orquestrador, que cai no
    roteamento determinístico.
    """
    modelo = modelo_para(etapa, ctx.escritorio)
    if modelo is None:
        raise SemOrcamento(
            f"escritório {ctx.escritorio_id} acima do teto de gasto do mês; "
            f"etapa {etapa} respondida sem modelo."
        )

    agente = criar_agente(modelo, output_type=output_type, system_prompt=system_prompt)

    # Contado antes de chamar: a mensagem custou uma tentativa mesmo que o
    # provedor responda com erro, e é isso que a trilha precisa refletir.
    _CHAMADAS.set(_CHAMADAS.get() + 1)
    inicio = time.perf_counter()
    try:
        resultado = agente.run_sync(mensagem)
    except Exception as exc:
        # A linha é gravada mesmo assim: a chamada consumiu tempo e, em erro de
        # validação com retry, tokens. Registrar só o sucesso faria o custo real
        # aparecer menor do que é justamente nos dias ruins.
        _registrar(
            ctx=ctx,
            etapa=etapa,
            modelo=modelo,
            uso=None,
            latencia_ms=_decorrido_ms(inicio),
            erro=f"{type(exc).__name__}: {exc}"[:200],
        )
        raise

    _registrar(
        ctx=ctx,
        etapa=etapa,
        modelo=modelo,
        uso=resultado.usage(),
        latencia_ms=_decorrido_ms(inicio),
    )
    return resultado.output


def _decorrido_ms(inicio: float) -> int:
    return int((time.perf_counter() - inicio) * 1000)


def _registrar(*, ctx, etapa, modelo, uso, latencia_ms, erro: str = "") -> None:
    from apps.observabilidade.models import ConsumoLLM

    entrada = int(getattr(uso, "input_tokens", 0) or 0)
    saida = int(getattr(uso, "output_tokens", 0) or 0)
    cache_leitura = int(getattr(uso, "cache_read_tokens", 0) or 0)

    custo = precos.custo_brl(
        modelo,
        tokens_entrada=entrada,
        tokens_saida=saida,
        tokens_cache_leitura=cache_leitura,
    )

    try:
        ConsumoLLM.objects.create(
            escritorio=ctx.escritorio,
            cliente=ctx.cliente,
            etapa=etapa,
            modelo=modelo,
            tokens_entrada=entrada,
            tokens_saida=saida,
            tokens_cache_leitura=cache_leitura,
            requisicoes=int(getattr(uso, "requests", 0) or 0) or 1,
            tool_calls=int(getattr(uso, "tool_calls", 0) or 0),
            latencia_ms=latencia_ms,
            custo_brl=custo or 0,
            erro=erro,
        )
    except Exception as exc:  # pragma: no cover - defesa, não caminho previsto
        logger.warning(
            "consumo_llm_nao_registrado",
            erro=str(exc),
            etapa=etapa,
            modelo=modelo,
            escritorio_id=ctx.escritorio_id,
        )
