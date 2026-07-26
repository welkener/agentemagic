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

    def normalizar(self, recurso: str, payload: dict) -> dict | None:
        """Traduz o payload da API pra forma canônica (`adapters/normalizacao.py`).

        Padrão: identidade — o mock já produz a forma canônica, que é o próprio
        contrato. Adaptador real sobrescreve. Devolver `None` significa "não sei
        traduzir isto", e vira `PAYLOAD_NAO_MAPEADO` — nunca um chute.
        """
        return payload

    def cancelar(self, documento: str, referencia: str, motivo: str, ctx) -> ResultadoAcao:  # Tier 3
        """Cancela um documento já emitido. Padrão: não suportado.

        Concreto (não abstrato) de propósito: adaptador de ERP não cancela
        documento fiscal, e transformar isto em `@abstractmethod` obrigaria
        todos eles a escrever um `pass` sem sentido. Quem cancela sobrescreve
        (`nfse_mock`, `nfse_nacional`); o resto degrada num erro padronizado,
        que o núcleo já sabe tratar.
        """
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")
