"""DPS da NFS-e Nacional: montagem pelo XSD oficial, assinatura e empacotamento.

O certificado é **gerado no próprio teste** (auto-assinado, `cryptography`), o
que torna assinatura e mTLS verificáveis offline — sem depender de um `.pfx`
real no repositório nem de conta na Produção Restrita. O que estes testes NÃO
provam é que a Sefin aceita o documento; isso só o cadastro no ADN valida.
"""
import base64
import datetime as dt
import gzip

import pytest
from lxml import etree

from apps.fiscal import dps as mod_dps
from apps.fiscal.numeracao import SerieDps, proximo_numero

NS = {"n": mod_dps.NAMESPACE_NFSE, "ds": "http://www.w3.org/2000/09/xmldsig#"}


# ---------------------------------------------------------------------------
# Certificado de teste (auto-assinado) em memória
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pfx_de_teste():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PADARIA TESTE:12345678000190")])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(days=1))
        .not_valid_after(agora + dt.timedelta(days=365))
        .sign(chave, hashes.SHA256())
    )
    senha = "senha-de-teste"
    pfx = serialization.pkcs12.serialize_key_and_certificates(
        name=b"teste",
        key=chave,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode()),
    )
    return pfx, senha


@pytest.fixture
def cliente_fiscal(cliente):
    """Cliente com o cadastro fiscal COMPLETO (o que a DPS exige de verdade)."""
    cliente.codigo_municipio_ibge = "3550308"  # São Paulo
    cliente.codigo_tributacao_nacional = "010101"
    cliente.inscricao_municipal = "1234567"
    cliente.regime_tributario = 1
    cliente.serie_dps = "1"
    cliente.save()
    return cliente


PAYLOAD = {"valor": 750.0, "descricao_servico": "Consultoria contábil", "tomador": "Maria Silva"}


# ---------------------------------------------------------------------------
# O achado: o cadastro do MVP era insuficiente
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cliente_sem_cadastro_fiscal_nao_emite_e_diz_o_que_falta(cliente):
    """Recusar dizendo o quê é diferente de recusar. O contador precisa saber
    o que preencher — não descobrir por tentativa."""
    with pytest.raises(mod_dps.ErroDpsIncompleta) as erro:
        mod_dps.montar_dps(cliente, PAYLOAD, numero=1)

    faltantes = " ".join(erro.value.faltantes)
    assert "IBGE" in faltantes
    assert "cTribNac" in faltantes or "tributação nacional" in faltantes


@pytest.mark.django_db
def test_cnae_no_lugar_do_codigo_de_tributacao_e_recusado(cliente_fiscal):
    """O erro mais provável de quem conhece o cadastro antigo: usar o CNAE.

    CNAE é `5611-2/01`; `cTribNac` é `[0-9]{6}`. São classificações diferentes,
    e a mensagem de erro diz isso explicitamente. (O CNAE com 7 dígitos nem
    chega ao banco — `max_length=6` já barra —, então a checagem roda sobre o
    objeto em memória, que é o caminho de quem edita e salva pelo admin.)
    """
    cliente_fiscal.codigo_tributacao_nacional = "5611-2"  # cara de CNAE, 6 chars
    faltantes = mod_dps.conferir_cadastro(cliente_fiscal)
    assert "não é o CNAE" in " ".join(faltantes)


@pytest.mark.django_db
def test_ibge_com_tamanho_errado_e_recusado(cliente_fiscal):
    cliente_fiscal.codigo_municipio_ibge = "3550"
    cliente_fiscal.save()
    with pytest.raises(mod_dps.ErroDpsIncompleta):
        mod_dps.montar_dps(cliente_fiscal, PAYLOAD, numero=1)


# ---------------------------------------------------------------------------
# Montagem — XML real, campos nos lugares certos
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_dps_montada_tem_os_campos_obrigatorios_do_xsd(cliente_fiscal):
    xml = mod_dps.montar_dps(cliente_fiscal, PAYLOAD, numero=42)
    raiz = etree.fromstring(xml)

    def texto(caminho):
        achado = raiz.xpath(caminho, namespaces=NS)
        return achado[0].text if achado else None

    assert texto("//n:cLocEmi") == "3550308"
    assert texto("//n:cTribNac") == "010101"
    assert texto("//n:nDPS") == "42"
    assert texto("//n:serie") == "1"
    assert texto("//n:tpAmb") == str(mod_dps.TPAMB_HOMOLOGACAO)
    assert texto("//n:xDescServ") == "Consultoria contábil"
    assert texto("//n:vServ") == "750.00"
    assert texto("//n:CNPJ") == cliente_fiscal.cnpj


@pytest.mark.django_db
def test_valor_vira_decimal_com_duas_casas(cliente_fiscal):
    """`float` cru viraria notação científica e furaria o pattern do schema."""
    xml = mod_dps.montar_dps(cliente_fiscal, {**PAYLOAD, "valor": 1234.5}, numero=1)
    assert b"<vServ>1234.50</vServ>" in xml


@pytest.mark.django_db
def test_ambiente_de_producao_e_explicito(cliente_fiscal):
    """Padrão é homologação — produção só quando alguém pede de propósito."""
    padrao = mod_dps.montar_dps(cliente_fiscal, PAYLOAD, numero=1)
    assert f"<tpAmb>{mod_dps.TPAMB_HOMOLOGACAO}</tpAmb>".encode() in padrao

    producao = mod_dps.montar_dps(
        cliente_fiscal, PAYLOAD, numero=2, ambiente=mod_dps.TPAMB_PRODUCAO
    )
    assert f"<tpAmb>{mod_dps.TPAMB_PRODUCAO}</tpAmb>".encode() in producao


@pytest.mark.django_db
def test_tomador_sem_documento_e_valido(cliente_fiscal):
    """Serviço a pessoa física sem CPF é caso real — inventar documento pra
    "completar" a nota seria falsificá-la."""
    xml = mod_dps.montar_dps(cliente_fiscal, {**PAYLOAD, "tomador": "Balcão"}, numero=1)
    assert b"Bal" in xml  # o nome entrou
    raiz = etree.fromstring(xml)
    toma = raiz.xpath("//n:toma", namespaces=NS)[0]
    assert toma.xpath("n:CPF", namespaces=NS) == []


# ---------------------------------------------------------------------------
# Assinatura — o que o .pfx em custódia destrava
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_dps_assinada_tem_signature_valida(cliente_fiscal, pfx_de_teste):
    pfx, senha = pfx_de_teste
    xml = mod_dps.montar_dps(cliente_fiscal, PAYLOAD, numero=7)
    id_dps = mod_dps.montar_id(cliente_fiscal, "1", 7)

    assinado = mod_dps.assinar(xml, pfx, senha, id_dps)
    raiz = etree.fromstring(assinado)

    assert raiz.xpath("//ds:Signature", namespaces=NS), "sem bloco de assinatura"
    assert raiz.xpath("//ds:SignatureValue", namespaces=NS)[0].text
    assert raiz.xpath("//ds:X509Certificate", namespaces=NS), "certificado não embutido"
    # A referência tem que apontar pro Id do infDPS, senão assina "nada".
    uri = raiz.xpath("//ds:Reference/@URI", namespaces=NS)[0]
    assert uri == f"#{id_dps}"


@pytest.mark.django_db
def test_senha_errada_do_pfx_falha_alto(cliente_fiscal, pfx_de_teste):
    pfx, _ = pfx_de_teste
    xml = mod_dps.montar_dps(cliente_fiscal, PAYLOAD, numero=1)
    with pytest.raises(Exception):
        mod_dps.assinar(xml, pfx, "senha-errada", mod_dps.montar_id(cliente_fiscal, "1", 1))


# ---------------------------------------------------------------------------
# Empacotamento e transporte
# ---------------------------------------------------------------------------
def test_empacotar_produz_gzip_base64_reversivel():
    """`dpsXmlGZipB64` — o formato que o ADN espera, não JSON."""
    original = b"<Dps>teste</Dps>"
    empacotado = mod_dps.empacotar(original)
    assert gzip.decompress(base64.b64decode(empacotado)) == original


def test_extrai_par_pem_para_mtls(pfx_de_teste):
    """A auth do ADN é mTLS (`cert=`), não Bearer — sem o par PEM não sai do lugar."""
    pfx, senha = pfx_de_teste
    cert_pem, chave_pem = mod_dps.extrair_pem_para_mtls(pfx, senha)
    assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert b"PRIVATE KEY" in chave_pem


# ---------------------------------------------------------------------------
# Numeração — o ponto onde concorrência estraga nota fiscal
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_numeracao_e_sequencial_por_cliente_e_serie(cliente_fiscal):
    assert [proximo_numero(cliente_fiscal) for _ in range(3)] == [1, 2, 3]
    assert SerieDps.objects.get(cliente=cliente_fiscal, serie="1").ultimo_numero == 3


@pytest.mark.django_db
def test_series_diferentes_tem_contadores_independentes(cliente_fiscal):
    assert proximo_numero(cliente_fiscal) == 1
    cliente_fiscal.serie_dps = "2"
    cliente_fiscal.save()
    assert proximo_numero(cliente_fiscal) == 1  # série nova recomeça
    assert SerieDps.objects.filter(cliente=cliente_fiscal).count() == 2


@pytest.mark.django_db
def test_numero_nao_e_reaproveitado_entre_clientes(cliente_fiscal, escritorio):
    from apps.clients.models import Cliente

    outro = Cliente.objects.create(
        escritorio=escritorio, cnpj="99999999000199", nome="Outro", telefone_whatsapp="5511900000009"
    )
    assert proximo_numero(cliente_fiscal) == 1
    assert proximo_numero(outro) == 1  # sequência é POR prestador
    assert proximo_numero(cliente_fiscal) == 2


# ---------------------------------------------------------------------------
# Adapter — os caminhos de recusa que o contador encontra na prática
# ---------------------------------------------------------------------------
@pytest.fixture
def app_nfse(db):
    from apps.credentials.models import AplicativoIntegracao

    return AplicativoIntegracao.objects.create(
        nome="nfse_nacional",
        ambiente="homologacao",
        base_url="https://adn.producaorestrita.nfse.gov.br",
        ativo=True,
    )


@pytest.mark.django_db
def test_adapter_recusa_modo_psc_em_vez_de_emitir_sem_assinatura(cliente_fiscal, app_nfse):
    """Assinatura remota via PSC segue bloqueada — melhor recusar alto do que
    mandar documento fiscal sem assinatura válida."""
    from apps.adapters.nfse_nacional import NfseNacionalAdapter
    from apps.credentials.models import Credencial

    Credencial.objects.create(
        cliente=cliente_fiscal,
        integracao="nfse_nacional",
        tipo=Credencial.Tipo.CERTIFICADO_PSC,
        psc_provedor="birdid",
        psc_identificador="teste",
    )
    dados = {**PAYLOAD, "cnpj_prestador": cliente_fiscal.cnpj, "cnae": "6201-5/01"}
    resultado = NfseNacionalAdapter().emitir("nfse", dados, {"cliente": cliente_fiscal})

    assert resultado.ok is False
    assert resultado.erro_padronizado == "CERTIFICADO_INDISPONIVEL"


@pytest.mark.django_db
def test_adapter_recusa_cadastro_fiscal_incompleto_dizendo_o_que_falta(cliente, app_nfse):
    """Cliente com cadastro antigo (só CNAE) não emite — e a resposta carrega
    a lista, pra virar mensagem acionável ao contador."""
    from apps.adapters.nfse_nacional import NfseNacionalAdapter
    from apps.credentials.models import Credencial

    Credencial.objects.create(
        cliente=cliente,
        integracao="nfse_nacional",
        tipo=Credencial.Tipo.CERTIFICADO_PFX,
    )
    dados = {**PAYLOAD, "cnpj_prestador": cliente.cnpj, "cnae": "6201-5/01"}
    resultado = NfseNacionalAdapter().emitir("nfse", dados, {"cliente": cliente})

    assert resultado.ok is False
    assert resultado.erro_padronizado == "CADASTRO_FISCAL_INCOMPLETO"
    assert resultado.dados["faltantes"], "tem que dizer o que falta, não só recusar"
