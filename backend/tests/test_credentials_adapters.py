"""Testes de: cifra de campo (Credencial/AplicativoIntegracao) e o resolver
mock↔real de adapters. Nenhum teste aqui faz chamada de rede real — os
adaptadores reais só são exercitados até o ponto de "config ausente".
"""
import pytest

from apps.adapters.bling import BlingAdapter
from apps.adapters.conta_azul import ContaAzulAdapter
from apps.adapters.erp_mock import ErpMockAdapter
from apps.adapters.nfse_mock import NfseMockAdapter
from apps.adapters.nfse_nacional import NfseNacionalAdapter
from apps.adapters.resolver import resolver_adapter_erp, resolver_adapter_nfse
from apps.credentials.models import AplicativoIntegracao, Credencial


@pytest.mark.django_db
def test_credencial_cifra_e_decifra_o_valor(cliente):
    credencial = Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.OAUTH, integracao="conta_azul")
    credencial.valor = "refresh-token-secreto"
    credencial.save()

    credencial.refresh_from_db()
    assert credencial.valor == "refresh-token-secreto"
    # O que fica no banco não é o texto puro (o campo já vem decifrado aqui,
    # mas o valor bruto persistido é Fernet — confirma via SQL direto).
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT valor_cifrado FROM credentials_credencial WHERE id = %s", [credencial.id])
        bruto = bytes(cursor.fetchone()[0])
    assert b"refresh-token-secreto" not in bruto


@pytest.mark.django_db
def test_credencial_sem_valor_nao_quebra(cliente):
    credencial = Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.PROCURACAO, integracao="nfse_nacional")
    assert credencial.valor == ""


@pytest.mark.django_db
def test_aplicativo_integracao_cifra_client_secret():
    app = AplicativoIntegracao.objects.create(
        nome=AplicativoIntegracao.Nome.CONTA_AZUL,
        base_url="https://api.exemplo.com",
        client_id="abc123",
        ativo=True,
    )
    app.client_secret = "segredo-do-app"
    app.save()

    app.refresh_from_db()
    assert app.client_secret == "segredo-do-app"


@pytest.mark.django_db
def test_resolver_erp_sem_credencial_cai_no_mock(cliente):
    adapter = resolver_adapter_erp(cliente, integracoes_candidatas=["conta_azul"])
    assert isinstance(adapter, ErpMockAdapter)


@pytest.mark.django_db
def test_resolver_erp_sem_candidatas_cai_no_mock(cliente):
    adapter = resolver_adapter_erp(cliente, integracoes_candidatas=[])
    assert isinstance(adapter, ErpMockAdapter)


@pytest.mark.django_db
def test_resolver_erp_com_credencial_e_app_ativo_usa_real(cliente):
    AplicativoIntegracao.objects.create(
        nome=AplicativoIntegracao.Nome.CONTA_AZUL, base_url="https://api.exemplo.com", ativo=True
    )
    Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.OAUTH, integracao="conta_azul")

    adapter = resolver_adapter_erp(cliente, integracoes_candidatas=["conta_azul"])
    assert isinstance(adapter, ContaAzulAdapter)


@pytest.mark.django_db
def test_resolver_erp_com_credencial_mas_app_inativo_cai_no_mock(cliente):
    AplicativoIntegracao.objects.create(
        nome=AplicativoIntegracao.Nome.CONTA_AZUL, base_url="https://api.exemplo.com", ativo=False
    )
    Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.OAUTH, integracao="conta_azul")

    adapter = resolver_adapter_erp(cliente, integracoes_candidatas=["conta_azul"])
    assert isinstance(adapter, ErpMockAdapter)


@pytest.mark.django_db
def test_resolver_erp_prefere_bling_quando_so_bling_credenciado(cliente):
    AplicativoIntegracao.objects.create(nome=AplicativoIntegracao.Nome.BLING, base_url="https://bling.exemplo.com", ativo=True)
    Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.OAUTH, integracao="bling")

    adapter = resolver_adapter_erp(cliente, integracoes_candidatas=["conta_azul", "bling"])
    assert isinstance(adapter, BlingAdapter)


@pytest.mark.django_db
def test_resolver_nfse_sem_credencial_cai_no_mock(cliente):
    assert isinstance(resolver_adapter_nfse(cliente), NfseMockAdapter)


@pytest.mark.django_db
def test_resolver_nfse_com_credencial_e_app_usa_real(cliente):
    # Certificado em nuvem (PSC), não procuração — a API do ADN exige mTLS do
    # prestador (confirmado 12/jul/2026, ver docs/magicbi-custodia-fiscal.md §1).
    AplicativoIntegracao.objects.create(
        nome=AplicativoIntegracao.Nome.NFSE_NACIONAL, base_url="https://adn.exemplo.gov.br", ativo=True
    )
    Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.CERTIFICADO, integracao="nfse_nacional")

    assert isinstance(resolver_adapter_nfse(cliente), NfseNacionalAdapter)


@pytest.mark.django_db
def test_resolver_nfse_com_procuracao_nao_e_suficiente(cliente):
    """Procuração eletrônica não autoriza chamada de API — só CERTIFICADO conta."""
    AplicativoIntegracao.objects.create(
        nome=AplicativoIntegracao.Nome.NFSE_NACIONAL, base_url="https://adn.exemplo.gov.br", ativo=True
    )
    Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.PROCURACAO, integracao="nfse_nacional")

    assert isinstance(resolver_adapter_nfse(cliente), NfseMockAdapter)


@pytest.mark.django_db
def test_adapter_real_sem_configuracao_retorna_erro_padronizado(cliente):
    """Sem AplicativoIntegracao/Credencial, o adaptador real nunca tenta rede —
    devolve INTEGRACAO_NAO_CONFIGURADA de forma limpa."""
    adapter = ContaAzulAdapter()
    resultado = adapter.consultar("estoque", {}, {"cliente": cliente})
    assert resultado.ok is False
    assert resultado.erro_padronizado == "INTEGRACAO_NAO_CONFIGURADA"


@pytest.mark.django_db
def test_agente_erp_usa_adapter_real_quando_cliente_credenciado(cliente):
    """Ponta a ponta: perfil com 'conta_azul' habilitado + credencial → real
    (sem chamada de rede de verdade, só confirma que o AgenteErp escolheu certo)."""
    from apps.agents.agente_erp.services import AgenteErp

    cliente.perfil.ferramentas_habilitadas = ["conta_azul"]
    cliente.perfil.save()
    AplicativoIntegracao.objects.create(
        nome=AplicativoIntegracao.Nome.CONTA_AZUL, base_url="https://api.exemplo.com", ativo=True
    )
    Credencial.objects.create(cliente=cliente, tipo=Credencial.Tipo.OAUTH, integracao="conta_azul")

    resposta = AgenteErp().consultar(
        intencao="consultar_estoque", recurso="estoque", filtros={}, perfil=cliente.perfil, cliente=cliente
    )
    # Sem token_url/rede real, a renovação de token falha de forma tratada —
    # a resposta é a mensagem de erro amigável, não uma exceção.
    assert "Não consegui consultar" in resposta
