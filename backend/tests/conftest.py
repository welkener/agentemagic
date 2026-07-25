"""Fixtures compartilhadas dos testes do MVP."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.clients.models import Cliente, Perfil
from apps.security.models import SessaoWhatsapp


@pytest.fixture
def cliente(db):
    """Padaria Estrela — empresa exemplo com perfil Tier 1 e sessão já validada.

    Representa o estado normal de um cliente já onboardado — os testes que
    exercitam o próprio gate de sessão (apps/security) usam clientes à parte,
    sem `SessaoWhatsapp` ativa (ver tests/test_security.py).
    """
    c = Cliente.objects.create(
        cnpj="12345678000190",
        nome="Padaria Estrela Ltda",
        telefone_whatsapp="5511999998888",
        email_contato="dono@padariaestrela.example.com",
        cnae_padrao="5611-2/01",
        ativo=True,
    )
    Perfil.objects.create(
        cliente=c,
        persona="lumen",
        ferramentas_habilitadas=["erp_mock", "nfse_mock"],
        tier_maximo=1,
    )
    agora = timezone.now()
    SessaoWhatsapp.objects.create(
        cliente=c,
        wa_id=c.telefone_whatsapp,
        status=SessaoWhatsapp.Status.ATIVA,
        validado_em=agora,
        expira_em=agora + timedelta(days=7),
    )
    return c
