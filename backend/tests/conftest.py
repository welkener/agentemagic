"""Fixtures compartilhadas dos testes do MVP."""
import pytest

from apps.clients.models import Cliente, Perfil


@pytest.fixture
def cliente(db):
    """Padaria Estrela — empresa exemplo com perfil Tier 1."""
    c = Cliente.objects.create(
        cnpj="12345678000190",
        nome="Padaria Estrela Ltda",
        telefone_whatsapp="5511999998888",
        cnae_padrao="5611-2/01",
        ativo=True,
    )
    Perfil.objects.create(
        cliente=c,
        persona="lumen",
        ferramentas_habilitadas=["erp_mock", "nfse_mock"],
        tier_maximo=1,
    )
    return c
