"""
Teto de gasto por tenant, com degradação antes do corte (DEC-08 item 3).

**Por que degradar em vez de cortar direto.** O escritório que estourar o
orçamento num pico do dia 8 não pode simplesmente parar de atender a carteira
dele — o cliente final não tem nada a ver com a fatura do contador, e um
atendimento que emudece é pior comercialmente do que um que responde com menos
sofisticação. A escada é:

| Modo | Quando | O que muda |
|---|---|---|
| `normal` | abaixo de 80% do limite | modelo bom na extração, modelo barato no roteador |
| `degradado` | entre 80% e 100% | **a extração cai para o modelo barato** |
| `cortado` | acima de 100% | nenhuma chamada ao modelo: só T0 e palavra-chave |

Mesmo `cortado` continua atendendo — o determinístico responde saudação, menu e
as intenções de alta confiança, e o fallback por palavra-chave cobre o resto.
O que se perde é precisão de roteamento e extração de campos, não o serviço.

**O corte é invisível para a empresa cliente e visível para o contador.** Dizer
"seu escritório estourou o limite" a um MEI expõe a relação comercial de
terceiros e não lhe dá ação nenhuma. O sinal vai para a trilha e para a tela de
Operação do Grimório, que é onde alguém pode agir.

**Limite nulo é o padrão e significa "sem teto".** Ligar um teto por omissão
faria escritórios existentes pararem de responder no dia do deploy — a decisão
de limitar tem que ser de quem paga.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

logger = structlog.get_logger(__name__)

MODO_NORMAL = "normal"
MODO_DEGRADADO = "degradado"
MODO_CORTADO = "cortado"

def _fracao_degradacao() -> Decimal:
    """Fração do limite em que a degradação começa.

    Lida a cada chamada, e não uma vez no import: constante de módulo
    congelaria o valor no momento em que o processo subiu, e mudar a variável
    de ambiente passaria a exigir restart — além de tornar o ajuste invisível
    para qualquer teste que sobrescreva `settings`.
    """
    return Decimal(str(getattr(settings, "ORCAMENTO_FRACAO_DEGRADACAO", "0.8")))

# Por quanto tempo o modo fica em cache. Curto porque a decisão é por mensagem;
# longo o bastante para que um pico de mensagens não vire um pico de agregações.
# O erro possível aqui é gastar até 60s a mais depois de cruzar o teto, o que em
# ordem de grandeza é fração de centavo.
_TTL_CACHE_SEGUNDOS = 60


def _inicio_do_mes(agora=None):
    agora = agora or timezone.now()
    return agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def gasto_do_mes(escritorio, agora=None) -> Decimal:
    """Soma do que este escritório consumiu de LLM na competência corrente.

    Competência de calendário, não janela móvel de 30 dias: é assim que o
    escritório lê a própria fatura, e um teto que zera num dia diferente do
    fechamento seria impossível de conferir.
    """
    from apps.observabilidade.models import ConsumoLLM

    total = ConsumoLLM.objects.filter(
        escritorio=escritorio, momento__gte=_inicio_do_mes(agora)
    ).aggregate(total=Sum("custo_brl"))["total"]
    return total or Decimal("0")


def limite_do_escritorio(escritorio) -> Decimal | None:
    limite = getattr(escritorio, "limite_gasto_mensal_brl", None)
    return Decimal(limite) if limite else None


def modo(escritorio, agora=None) -> str:
    """Modo de operação do tenant agora. Cacheado por um minuto.

    Sem limite configurado devolve `normal` sem tocar o banco — que é o caminho
    de todo escritório até alguém decidir o contrário, e não vale uma agregação
    por mensagem.
    """
    if escritorio is None:
        return MODO_NORMAL
    limite = limite_do_escritorio(escritorio)
    if limite is None:
        return MODO_NORMAL

    chave = f"orcamento:modo:{escritorio.pk}:{_inicio_do_mes(agora):%Y-%m}"
    memorizado = cache.get(chave)
    if memorizado is not None:
        return memorizado

    gasto = gasto_do_mes(escritorio, agora)
    if gasto >= limite:
        atual = MODO_CORTADO
    elif gasto >= limite * _fracao_degradacao():
        atual = MODO_DEGRADADO
    else:
        atual = MODO_NORMAL

    if atual != MODO_NORMAL:
        logger.warning(
            "orcamento_tenant_em_degradacao",
            escritorio_id=escritorio.pk,
            modo=atual,
            gasto_brl=str(gasto),
            limite_brl=str(limite),
        )

    cache.set(chave, atual, _TTL_CACHE_SEGUNDOS)
    return atual


def esquecer(escritorio, agora=None) -> None:
    """Invalida o modo em cache — para o admin e para os testes.

    Existe porque mudar o limite no painel e continuar degradado por um minuto é
    confuso justamente no momento em que alguém está tentando destravar o
    atendimento.
    """
    if escritorio is None:
        return
    cache.delete(f"orcamento:modo:{escritorio.pk}:{_inicio_do_mes(agora):%Y-%m}")


def situacao(escritorio, agora=None) -> dict:
    """Retrato para a tela de Operação: gasto, limite, modo e percentual."""
    limite = limite_do_escritorio(escritorio)
    gasto = gasto_do_mes(escritorio, agora)
    percentual = int(gasto / limite * 100) if limite else 0
    return {
        "gasto": gasto,
        "limite": limite,
        "modo": modo(escritorio, agora),
        "percentual": percentual,
        "desde": _inicio_do_mes(agora),
        "proxima_virada": _inicio_do_mes(agora) + timedelta(days=32),
    }
