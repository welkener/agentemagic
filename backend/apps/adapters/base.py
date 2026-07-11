"""
Contrato do adaptador — interface interna única (seção 4 da arquitetura).

Todo ERP/API fiscal implementa este contrato. O núcleo só conhece isto.
`ctx` carrega cliente, perfil, chave de idempotência e a credencial resolvida
do cofre. Erros são PADRONIZADOS pelo adaptador: o núcleo trata
"estoque não encontrado" igual, venha do Conta Azul ou do Bling.
"""
from abc import ABC, abstractmethod

from apps.core.resultado import ResultadoAcao

__all__ = ["AdapterBase", "ResultadoAcao"]


class AdapterBase(ABC):
    """Todo ERP/API fiscal implementa este contrato. O núcleo só conhece isto."""

    @abstractmethod
    def capacidades(self) -> set[str]:
        """Ex.: {'consultar_pedido','criar_rascunho_pedido','emitir_nfse'}."""

    @abstractmethod
    def consultar(self, recurso: str, filtros: dict, ctx) -> ResultadoAcao: ...   # Tier 0

    @abstractmethod
    def criar_rascunho(self, recurso: str, dados: dict, ctx) -> ResultadoAcao: ...  # Tier 1

    @abstractmethod
    def alterar(self, recurso: str, id_ext: str, mudancas: dict, ctx) -> ResultadoAcao: ...  # Tier 2-3

    @abstractmethod
    def emitir(self, documento: str, dados: dict, ctx) -> ResultadoAcao: ...       # Tier 1-3
