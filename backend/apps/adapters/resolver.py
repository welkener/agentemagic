"""
Escolhe o adaptador real quando o cliente está credenciado para aquela
integração; cai no mock (dev/demo) quando não está. Nunca falha
silenciosamente — a escolha vai pro log estruturado.

"Novo cliente credenciado = escrita real sem tocar no núcleo" — é a mesma
arquitetura de adaptadores testada no MVP (Conta Azul + Bling), só que a
troca mock→real agora é automática por cliente, não por deploy.
"""
from __future__ import annotations

import structlog

from apps.credentials.models import AplicativoIntegracao, Credencial

from .bling import BlingAdapter
from .conta_azul import ContaAzulAdapter
from .erp_mock import ErpMockAdapter
from .nfse_mock import NfseMockAdapter
from .nfse_nacional import NfseNacionalAdapter

logger = structlog.get_logger(__name__)

_ADAPTERS_ERP_REAIS = {
    "conta_azul": ContaAzulAdapter,
    "bling": BlingAdapter,
}


def resolver_adapter_erp(cliente, integracoes_candidatas=()):
    """Primeira integração candidata credenciada e ativa vence; senão, mock.

    `integracoes_candidatas` normalmente vem de `Perfil.ferramentas_habilitadas`.
    """
    for integracao in integracoes_candidatas:
        adapter_cls = _ADAPTERS_ERP_REAIS.get(integracao)
        if adapter_cls is None:
            continue
        credenciado = Credencial.objects.filter(
            cliente=cliente, integracao=integracao, tipo=Credencial.Tipo.OAUTH
        ).exists()
        app_ativo = AplicativoIntegracao.objects.filter(nome=integracao, ativo=True).exists()
        if credenciado and app_ativo:
            logger.info("adapter_erp_real_selecionado", integracao=integracao, cliente_id=getattr(cliente, "id", None))
            return adapter_cls()

    logger.info("adapter_erp_mock_selecionado", cliente_id=getattr(cliente, "id", None))
    return ErpMockAdapter()


def resolver_adapter_nfse(cliente):
    credenciado = Credencial.objects.filter(
        cliente=cliente, integracao="nfse_nacional", tipo=Credencial.Tipo.PROCURACAO
    ).exists()
    app_ativo = AplicativoIntegracao.objects.filter(nome="nfse_nacional", ativo=True).exists()

    if credenciado and app_ativo:
        logger.info("adapter_nfse_real_selecionado", cliente_id=getattr(cliente, "id", None))
        return NfseNacionalAdapter()

    logger.info("adapter_nfse_mock_selecionado", cliente_id=getattr(cliente, "id", None))
    return NfseMockAdapter()
