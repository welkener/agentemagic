"""Dashboard do Grimório (/painel/) — visão de demonstração/homologação."""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.agents.agente_nf.models import Intencao
from apps.agents.agente_nf.services import confirmar_emissao
from apps.channel_evolution.models import ConfiguracaoEvolution
from apps.credentials.models import Credencial
from apps.painel.models import Escritorio

URL = "/painel/"


@pytest.fixture
def contador(db):
    return get_user_model().objects.create_user(
        username="contador.painel", email="painel@rotina.example.com", is_staff=True
    )


@pytest.fixture
def nota_emitida(cliente):
    intencao = Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="painel-teste-001",
        tipo_acao="emitir_nfse",
        payload={
            "cnpj_prestador": cliente.cnpj,
            "cnae": cliente.cnae_padrao,
            "valor": 750.0,
            "descricao_servico": "Consultoria",
            "tomador": "Maria",
        },
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )
    confirmar_emissao(intencao, motivo="teste do painel")
    intencao.refresh_from_db()
    return intencao


def test_raiz_redireciona_pro_painel(client):
    resposta = client.get("/")
    assert resposta.status_code == 302
    assert resposta.url == "/painel/"


@pytest.mark.django_db
def test_anonimo_e_redirecionado_pro_login(client):
    resposta = client.get(URL)
    assert resposta.status_code == 302
    assert "/admin/login" in resposta.url


@pytest.mark.django_db
def test_usuario_nao_staff_e_redirecionado(client):
    get_user_model().objects.create_user(username="cliente_comum", password="x", is_staff=False)
    client.login(username="cliente_comum", password="x")
    resposta = client.get(URL)
    assert resposta.status_code == 302


@pytest.mark.django_db
def test_contador_ve_o_dashboard(client, contador):
    client.force_login(contador)
    resposta = client.get(URL)
    assert resposta.status_code == 200
    assert "Grimório" in resposta.content.decode()


@pytest.mark.django_db
def test_nota_emitida_aparece_no_dashboard(client, contador, nota_emitida):
    client.force_login(contador)
    resposta = client.get(URL)
    corpo = resposta.content.decode()
    assert nota_emitida.protocolo in corpo
    assert resposta.context["notas_hoje"] == 1


@pytest.mark.django_db
def test_sem_configuracao_evolution_mostra_aviso(client, contador):
    client.force_login(contador)
    resposta = client.get(URL)
    assert resposta.context["canal_evolution"] is None
    assert "sem configuração ativa" in resposta.content.decode()


@pytest.mark.django_db
def test_com_configuracao_evolution_ativa_mostra_instancia(client, contador):
    ConfiguracaoEvolution.objects.create(
        nome="teste", base_url="https://evolution.example.com", instancia="rotina-teste", ativo=True
    )
    client.force_login(contador)
    resposta = client.get(URL)
    assert "rotina-teste" in resposta.content.decode()


@pytest.mark.django_db
def test_sem_escritorio_cadastrado_usa_marca_generica_magic_bi(client, contador):
    client.force_login(contador)
    resposta = client.get(URL)
    corpo = resposta.content.decode()
    assert "Grimório — Magic BI" in corpo
    assert "Rotina Contábil" not in corpo  # nunca hardcoded


@pytest.mark.django_db
def test_escritorio_ativo_troca_a_marca_do_painel(client, contador):
    Escritorio.objects.create(
        nome="Contabilidade Exemplo", cor_primaria="#123456", cor_acento="#abcdef", ativo=True
    )
    client.force_login(contador)
    resposta = client.get(URL)
    corpo = resposta.content.decode()
    assert "Contabilidade Exemplo" in corpo
    assert "#123456" in corpo


@pytest.mark.django_db
def test_certificado_divergente_mostra_alerta(client, contador, cliente):
    cliente.cnpj = "00000000000000"
    cliente.save()
    Credencial.objects.create(
        cliente=cliente,
        integracao="nfse_nacional",
        tipo=Credencial.Tipo.CERTIFICADO_PSC,
        certificado_cnpj="11111111000199",
    )
    client.force_login(contador)
    resposta = client.get(URL)
    assert "CNPJ diverge" in resposta.content.decode()
