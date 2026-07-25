"""Custódia de certificado fiscal — 3 modos (PSC/.pfx/procuração), decisão 25/jul/2026.

Gera um .pfx autoassinado em memória (via `cryptography`) para testar a
extração de metadados sem depender de um certificado ICP-Brasil real.
"""
import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.audit.models import Auditoria
from apps.credentials.certificados import ErroCertificadoInvalido, extrair_metadados
from apps.credentials.models import Credencial
from apps.credentials.services import vincular_certificado_psc, vincular_certificado_pfx


def _gerar_pfx_teste(
    cnpj: str = "12345678000199",
    razao_social: str = "EMPRESA TESTE LTDA",
    senha: str = "senha-do-certificado",
    dias_validade: int = 365,
) -> bytes:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{razao_social}:{cnpj}")])
    agora = datetime.datetime.now(datetime.timezone.utc)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=1))
        .not_valid_after(agora + datetime.timedelta(days=dias_validade))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        name=b"teste",
        key=chave,
        cert=certificado,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode()),
    )


@pytest.fixture
def pfx_bytes():
    return _gerar_pfx_teste()


@pytest.fixture
def credencial(cliente):
    return Credencial.objects.create(cliente=cliente, integracao="nfse_nacional", tipo=Credencial.Tipo.PROCURACAO)


@pytest.fixture
def contador(db):
    return get_user_model().objects.create_superuser("contador2", "contador2@exemplo.com", "senha-teste-123")


# ---------------------------------------------------------------------------
# extrair_metadados
# ---------------------------------------------------------------------------
def test_extrair_metadados_le_cnpj_razao_e_validade(pfx_bytes):
    metadados = extrair_metadados(pfx_bytes, "senha-do-certificado")
    assert metadados.cnpj == "12345678000199"
    assert metadados.razao_social == "EMPRESA TESTE LTDA"
    assert metadados.validade > datetime.date.today()


def test_extrair_metadados_senha_errada_levanta_erro(pfx_bytes):
    with pytest.raises(ErroCertificadoInvalido):
        extrair_metadados(pfx_bytes, "senha-errada")


def test_extrair_metadados_arquivo_corrompido_levanta_erro():
    with pytest.raises(ErroCertificadoInvalido):
        extrair_metadados(b"isto nao e um pfx", "qualquer")


# ---------------------------------------------------------------------------
# services.vincular_certificado_pfx / vincular_certificado_psc
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_vincular_certificado_pfx_grava_cifrado_e_audita(credencial, pfx_bytes):
    vincular_certificado_pfx(credencial, pfx_bytes, "senha-do-certificado")

    credencial.refresh_from_db()
    assert credencial.tipo == Credencial.Tipo.CERTIFICADO_PFX
    assert credencial.certificado_cnpj == "12345678000199"
    assert credencial.certificado_validade > datetime.date.today()
    # Nunca em texto puro: o campo bruto no banco não é igual ao .pfx original.
    assert bytes(credencial.pfx_arquivo_cifrado) != pfx_bytes
    assert credencial.pfx_bytes == pfx_bytes
    assert credencial.pfx_senha == "senha-do-certificado"
    assert Auditoria.objects.filter(evento="certificado_pfx_vinculado", cliente=credencial.cliente).exists()


@pytest.mark.django_db
def test_vincular_certificado_pfx_senha_errada_nao_grava(credencial, pfx_bytes):
    with pytest.raises(ErroCertificadoInvalido):
        vincular_certificado_pfx(credencial, pfx_bytes, "senha-errada")
    credencial.refresh_from_db()
    assert credencial.tipo == Credencial.Tipo.PROCURACAO  # não mudou
    assert not credencial.pfx_arquivo_cifrado


@pytest.mark.django_db
def test_vincular_certificado_pfx_marca_divergencia_de_cnpj(cliente, pfx_bytes):
    cliente.cnpj = "00000000000000"
    cliente.save()
    credencial = Credencial.objects.create(cliente=cliente, integracao="nfse_nacional", tipo=Credencial.Tipo.PROCURACAO)

    vincular_certificado_pfx(credencial, pfx_bytes, "senha-do-certificado")

    assert credencial.certificado_cnpj_diverge is True


@pytest.mark.django_db
def test_vincular_certificado_psc_grava_e_audita(credencial):
    vincular_certificado_psc(credencial, "birdid", "dono@empresa.example.com")

    credencial.refresh_from_db()
    assert credencial.tipo == Credencial.Tipo.CERTIFICADO_PSC
    assert credencial.psc_provedor == "birdid"
    assert credencial.psc_identificador == "dono@empresa.example.com"
    assert Auditoria.objects.filter(evento="certificado_psc_vinculado", cliente=credencial.cliente).exists()


# ---------------------------------------------------------------------------
# Admin — upload real via multipart, ponta a ponta
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_admin_cria_credencial_pfx_com_upload(client, contador, cliente, pfx_bytes):
    client.force_login(contador)
    url = reverse("admin:credentials_credencial_add")
    arquivo = SimpleUploadedFile("certificado.pfx", pfx_bytes, content_type="application/x-pkcs12")

    resposta = client.post(
        url,
        {
            "cliente": cliente.id,
            "tipo": Credencial.Tipo.CERTIFICADO_PFX,
            "integracao": "nfse_nacional",
            "referencia_cofre": "",
            "valor": "",
            "pfx_arquivo": arquivo,
            "pfx_senha": "senha-do-certificado",
            "psc_provedor": "",
            "psc_identificador": "",
        },
    )

    assert resposta.status_code == 302, resposta.context["adminform"].form.errors if resposta.status_code == 200 else None
    credencial = Credencial.objects.get(cliente=cliente, integracao="nfse_nacional")
    assert credencial.tipo == Credencial.Tipo.CERTIFICADO_PFX
    assert credencial.certificado_cnpj == "12345678000199"
    assert credencial.pfx_bytes == pfx_bytes


@pytest.mark.django_db
def test_admin_rejeita_pfx_com_senha_errada(client, contador, cliente, pfx_bytes):
    client.force_login(contador)
    url = reverse("admin:credentials_credencial_add")
    arquivo = SimpleUploadedFile("certificado.pfx", pfx_bytes, content_type="application/x-pkcs12")

    resposta = client.post(
        url,
        {
            "cliente": cliente.id,
            "tipo": Credencial.Tipo.CERTIFICADO_PFX,
            "integracao": "nfse_nacional",
            "referencia_cofre": "",
            "valor": "",
            "pfx_arquivo": arquivo,
            "pfx_senha": "senha-errada",
            "psc_provedor": "",
            "psc_identificador": "",
        },
    )

    assert resposta.status_code == 200  # re-renderiza o form com erro, não redireciona
    assert not Credencial.objects.filter(cliente=cliente, integracao="nfse_nacional").exists()


@pytest.mark.django_db
def test_admin_exige_arquivo_pfx_quando_tipo_e_pfx(client, contador, cliente):
    client.force_login(contador)
    url = reverse("admin:credentials_credencial_add")

    resposta = client.post(
        url,
        {
            "cliente": cliente.id,
            "tipo": Credencial.Tipo.CERTIFICADO_PFX,
            "integracao": "nfse_nacional",
            "referencia_cofre": "",
            "valor": "",
            "pfx_senha": "",
            "psc_provedor": "",
            "psc_identificador": "",
        },
    )

    assert resposta.status_code == 200
    assert not Credencial.objects.filter(cliente=cliente, integracao="nfse_nacional").exists()


@pytest.mark.django_db
def test_admin_cria_credencial_psc_sem_upload(client, contador, cliente):
    client.force_login(contador)
    url = reverse("admin:credentials_credencial_add")

    resposta = client.post(
        url,
        {
            "cliente": cliente.id,
            "tipo": Credencial.Tipo.CERTIFICADO_PSC,
            "integracao": "nfse_nacional",
            "referencia_cofre": "",
            "valor": "",
            "pfx_senha": "",
            "psc_provedor": "birdid",
            "psc_identificador": "dono@empresa.example.com",
        },
    )

    assert resposta.status_code == 302
    credencial = Credencial.objects.get(cliente=cliente, integracao="nfse_nacional")
    assert credencial.tipo == Credencial.Tipo.CERTIFICADO_PSC
    assert credencial.psc_provedor == "birdid"
