"""
ResultadoAcao — contrato único de retorno de toda ação executada por um
adaptador (seção 4 da arquitetura técnica).

O núcleo só conhece este formato: erros são padronizados pelo adaptador,
venham do mock, do Conta Azul ou da NFS-e Nacional.
"""
from dataclasses import dataclass


@dataclass
class ResultadoAcao:
    ok: bool
    dados: dict | None = None
    erro_padronizado: str | None = None   # ex.: "RECURSO_NAO_ENCONTRADO"
    referencia_externa: str | None = None  # id no sistema de destino
