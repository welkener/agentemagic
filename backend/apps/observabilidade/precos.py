"""
Quanto custa uma chamada ao modelo, em reais.

Os preços ficam em `settings` (`PRECOS_LLM`, `COTACAO_USD_BRL`) e não constantes
aqui pelo mesmo motivo do teto do MEI: são números do mundo, não do sistema.
Provedor reajusta, o dólar anda, e nenhum dos dois deveria exigir release.

**O que este módulo garante e o que ele não garante.** Garante que um modelo sem
preço cadastrado não vire custo zero em silêncio — custo zero somaria zero na
fatura e o gate de R$ 0,60/cliente/mês passaria por engano, que é exatamente o
erro que a medição existe para impedir. Modelo desconhecido devolve `None`, o
consumo é gravado assim mesmo (os tokens são a verdade) e o alerta aparece no
log estruturado.

Não garante centavo exato. Cotação é a do dia da chamada, arredondada para cima,
e o provedor fatura em dólar com o câmbio dele. A conta serve para decidir preço
e teto de gasto, não para conferir a fatura do cartão.
"""
from __future__ import annotations

from decimal import Decimal

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)

_UM_MILHAO = Decimal("1000000")


def _tabela() -> dict:
    return getattr(settings, "PRECOS_LLM", {}) or {}


def _cotacao() -> Decimal:
    return Decimal(str(getattr(settings, "COTACAO_USD_BRL", "5.10")))


def _preco_do_modelo(modelo: str) -> dict | None:
    """Aceita o identificador com ou sem o prefixo do provedor.

    O orquestrador chama `groq:llama-3.1-8b-instant` (formato do Pydantic AI) e a
    tabela de preços é escrita com o nome do modelo. Normalizar aqui evita que a
    tabela precise repetir cada modelo duas vezes — e que um esquecimento no
    prefixo vire custo zero.
    """
    tabela = _tabela()
    if modelo in tabela:
        return tabela[modelo]
    _, _, sem_prefixo = modelo.partition(":")
    return tabela.get(sem_prefixo)


def custo_brl(
    modelo: str,
    *,
    tokens_entrada: int,
    tokens_saida: int,
    tokens_cache_leitura: int = 0,
) -> Decimal | None:
    """Custo da chamada em reais, ou None se o modelo não tem preço cadastrado.

    `tokens_cache_leitura` sai da conta cheia e entra pela metade: é assim que o
    provedor cobra entrada servida do cache. Contá-los duas vezes (no total e no
    desconto) inflaria o custo justamente do caminho que a DEC-08 quer incentivar.
    """
    preco = _preco_do_modelo(modelo)
    if preco is None:
        logger.warning(
            "preco_de_modelo_desconhecido",
            modelo=modelo,
            aviso="consumo gravado sem custo — cadastre em settings.PRECOS_LLM",
        )
        return None

    entrada_cheia = max(int(tokens_entrada) - int(tokens_cache_leitura), 0)
    usd = (
        Decimal(entrada_cheia) * Decimal(str(preco["entrada"]))
        + Decimal(int(tokens_cache_leitura)) * Decimal(str(preco["entrada"])) / 2
        + Decimal(int(tokens_saida)) * Decimal(str(preco["saida"]))
    ) / _UM_MILHAO

    return (usd * _cotacao()).quantize(Decimal("0.000001"))
