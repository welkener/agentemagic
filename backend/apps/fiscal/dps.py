"""
Monta, assina e empacota a DPS (Declaração de Prestação de Serviços).

A API da NFS-e Nacional **não aceita JSON**: o corpo é
`{"dpsXmlGZipB64": "<XML da DPS assinado, gzip, base64>"}`. Até 26/jul/2026 o
adapter mandava `{"dps": {...}}` — um placeholder que nunca poderia funcionar.
Este módulo produz o artefato de verdade.

O que é evidência e o que não é
-------------------------------
A estrutura vem dos bindings do **XSD oficial** (`nfelib.nfse.bindings.v1_0`),
gerados do schema publicado — não de doc lida por cima. Foi ele que revelou
duas coisas que o cadastro do MVP não tinha:

- **`cTribNac` (`[0-9]{6}`) não é o CNAE.** O CNAE (`5611-2/01`) é
  classificação de atividade econômica; a DPS quer o código da lista nacional
  de serviços (LC 116). O campo que o projeto vinha protegendo com cuidado
  ("nunca inferido pelo LLM") era o campo errado para a nota.
- **`cLocEmi` (`[0-9]{7}`), `serie`, `nDPS` e `dCompet` são obrigatórios** e
  não existiam no `Cliente`.

Por isso `montar_dps` **recusa** a emissão listando exatamente o que falta, em
vez de preencher com valor plausível. Numa DPS, campo errado não dá erro de
schema — dá nota emitida errada, com efeito tributário real.

O que ainda NÃO é verificável aqui
----------------------------------
Assinatura e empacotamento são verificáveis offline (e têm teste com
certificado gerado na hora). O que só a Produção Restrita valida é se a Sefin
aceita o documento — cadastro no ADN segue pendente, ver
`docs/magicbi-ondas-desenvolvimento.md` §2.
"""
from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

# Ambiente da DPS (tpAmb): 1 = produção, 2 = homologação/Produção Restrita.
TPAMB_PRODUCAO = 1
TPAMB_HOMOLOGACAO = 2

# tpEmit: 1 = emissão pelo próprio prestador.
TPEMIT_PRESTADOR = 1

NAMESPACE_NFSE = "http://www.sped.fazenda.gov.br/nfse"


class ErroDpsIncompleta(Exception):
    """Cadastro insuficiente para montar uma DPS válida.

    Carrega a lista de campos faltantes para que a mensagem ao contador diga o
    que fazer, em vez de só "erro ao emitir".
    """

    def __init__(self, faltantes: list[str]):
        self.faltantes = faltantes
        super().__init__("Cadastro incompleto para emitir NFS-e: " + ", ".join(faltantes))


@dataclass
class DpsMontada:
    xml: bytes
    id_dps: str
    numero: int
    serie: str


# ---------------------------------------------------------------------------
# Validação de cadastro — antes de qualquer XML
# ---------------------------------------------------------------------------
_CAMPOS_OBRIGATORIOS_CLIENTE = (
    ("cnpj", "CNPJ do prestador"),
    ("codigo_municipio_ibge", "código IBGE do município (cLocEmi)"),
    ("codigo_tributacao_nacional", "código de tributação nacional (cTribNac, 6 dígitos)"),
)


def conferir_cadastro(cliente) -> list[str]:
    """Devolve a lista de campos de cadastro que impedem a emissão. Vazia = ok."""
    faltantes = [
        rotulo
        for campo, rotulo in _CAMPOS_OBRIGATORIOS_CLIENTE
        if not (getattr(cliente, campo, "") or "").strip()
    ]

    ibge = (getattr(cliente, "codigo_municipio_ibge", "") or "").strip()
    if ibge and (len(ibge) != 7 or not ibge.isdigit()):
        faltantes.append("código IBGE do município deve ter 7 dígitos")

    ctrib = (getattr(cliente, "codigo_tributacao_nacional", "") or "").strip()
    if ctrib and (len(ctrib) != 6 or not ctrib.isdigit()):
        # Erro provável de quem confunde com CNAE — a mensagem diz isso.
        faltantes.append(
            "código de tributação nacional deve ter 6 dígitos numéricos "
            "(não é o CNAE — é o código da lista nacional de serviços)"
        )

    return faltantes


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------
def montar_dps(cliente, payload: dict, numero: int, ambiente: int = TPAMB_HOMOLOGACAO) -> bytes:
    """Monta o XML (ainda NÃO assinado) da DPS a partir do payload interno.

    `numero` é o `nDPS` — sequencial por prestador+série, resolvido fora daqui
    (`numeracao.proximo_numero`), porque envolve trava de banco.
    """
    from nfelib.nfse.bindings.v1_0.dps_v1_00 import Dps
    from nfelib.nfse.bindings.v1_0.tipos_complexos_v1_00 import TcinfDps
    from xsdata.formats.dataclass.serializers import XmlSerializer
    from xsdata.formats.dataclass.serializers.config import SerializerConfig

    faltantes = conferir_cadastro(cliente)
    if faltantes:
        raise ErroDpsIncompleta(faltantes)

    valor = payload.get("valor")
    descricao = (payload.get("descricao_servico") or "").strip()
    if valor is None or not descricao:
        raise ErroDpsIncompleta(
            [c for c, ok in (("valor do serviço", valor is not None), ("descrição do serviço", descricao)) if not ok]
        )

    agora = timezone.localtime()
    serie = (cliente.serie_dps or "1").strip()
    id_dps = montar_id(cliente, serie, numero)

    inf = TcinfDps(
        tpAmb=ambiente,
        dhEmi=_xml_datetime(agora),
        verAplic="MagicBI-1.0",
        serie=serie,
        nDPS=str(numero),
        dCompet=_xml_date(agora.date()),
        tpEmit=TPEMIT_PRESTADOR,
        cLocEmi=cliente.codigo_municipio_ibge,
        prest=_prestador(cliente),
        toma=_tomador(payload),
        serv=_servico(cliente, descricao),
        valores=_valores(valor),
        Id=id_dps,
    )

    serializer = XmlSerializer(config=SerializerConfig(indent=None, xml_declaration=True))
    xml = serializer.render(Dps(infDPS=inf, versao="1.00"), ns_map={None: NAMESPACE_NFSE})
    return xml.encode("utf-8")


def montar_id(cliente, serie: str, numero: int) -> str:
    """`Id` do infDPS — a NT define composição própria; até confirmar contra a
    Produção Restrita, usamos um identificador estável e único por
    prestador+série+número, que é o que o XMLDSig precisa referenciar."""
    return f"DPS{cliente.cnpj}{serie.zfill(5)}{str(numero).zfill(15)}"


def _xml_datetime(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + momento.strftime("%z")[-2:]


def _xml_date(dia: date) -> str:
    return dia.strftime("%Y-%m-%d")


def _prestador(cliente):
    from nfelib.nfse.bindings.v1_0.tipos_complexos_v1_00 import TcinfoPrestador

    return TcinfoPrestador(
        CNPJ=cliente.cnpj,
        IM=cliente.inscricao_municipal or None,
        xNome=cliente.nome[:300],
    )


def _tomador(payload: dict):
    from nfelib.nfse.bindings.v1_0.tipos_complexos_v1_00 import TcinfoPessoa

    # Tomador só com nome: a DPS aceita tomador não identificado (sem CNPJ/CPF)
    # — é o caso comum de serviço a pessoa física no balcão. Inventar um
    # documento para "completar" seria falsificar a nota.
    return TcinfoPessoa(xNome=(payload.get("tomador") or "")[:300] or None)


def _servico(cliente, descricao: str):
    from nfelib.nfse.bindings.v1_0.tipos_complexos_v1_00 import Tccserv, TclocPrest, Tcserv

    return Tcserv(
        locPrest=TclocPrest(cLocPrestacao=cliente.codigo_municipio_ibge),
        cServ=Tccserv(
            cTribNac=cliente.codigo_tributacao_nacional,
            xDescServ=descricao[:2000],
        ),
    )


def _valores(valor):
    from nfelib.nfse.bindings.v1_0.tipos_complexos_v1_00 import TcinfoValores, TcvservPrest

    return TcinfoValores(vServPrest=TcvservPrest(vServ=_decimal(valor)))


def _decimal(valor) -> str:
    """A DPS quer decimal com 2 casas e ponto — `float` cru vira `1e-05` e
    quebra o pattern do schema."""
    return f"{float(valor):.2f}"


# ---------------------------------------------------------------------------
# Assinatura (XMLDSig) e empacotamento
# ---------------------------------------------------------------------------
def assinar(xml: bytes, pfx_bytes: bytes, senha: str, id_dps: str) -> bytes:
    """Assina o `infDPS` com o certificado ICP-Brasil (enveloped, RSA-SHA256).

    É aqui que o `.pfx` em custódia (`apps/credentials`) destrava o que o
    modelo PSC remoto não destravava: com a chave privada em mãos, assinar é
    local e não depende de nenhum provedor externo.
    """
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
    from lxml import etree
    from signxml import (
        CanonicalizationMethod,
        DigestAlgorithm,
        SignatureConstructionMethod,
        SignatureMethod,
        XMLSigner,
    )

    chave, certificado, _ = pkcs12.load_key_and_certificates(pfx_bytes, senha.encode())
    if chave is None or certificado is None:
        raise ValueError("Certificado .pfx sem chave privada ou sem certificado.")

    raiz = etree.fromstring(xml)

    # Perfil de assinatura da NFS-e: enveloped, RSA-SHA256, C14N 1.0 **sem**
    # exclusive canonicalization — é o mesmo perfil dos documentos fiscais
    # brasileiros (NF-e/NFS-e). Trocar qualquer um dos três faz a Sefin
    # recusar por assinatura inválida, mesmo com certificado correto.
    assinador = XMLSigner(
        method=SignatureConstructionMethod.enveloped,
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0,
    )
    assinado = assinador.sign(
        raiz,
        key=chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        cert=certificado.public_bytes(Encoding.PEM),
        reference_uri=f"#{id_dps}",
    )
    return etree.tostring(assinado, xml_declaration=True, encoding="utf-8")


def empacotar(xml_assinado: bytes) -> str:
    """`dpsXmlGZipB64` — gzip + base64, que é o formato que o ADN espera."""
    return base64.b64encode(gzip.compress(xml_assinado)).decode("ascii")


def extrair_pem_para_mtls(pfx_bytes: bytes, senha: str) -> tuple[bytes, bytes]:
    """(certificado PEM, chave PEM) — o par que o `httpx` usa em `cert=`.

    A auth do ADN/Sefin é **mTLS**, não Bearer. Sem isto o transporte não sai
    do lugar, por mais correto que o XML esteja.
    """
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12

    chave, certificado, _ = pkcs12.load_key_and_certificates(pfx_bytes, senha.encode())
    if chave is None or certificado is None:
        raise ValueError("Certificado .pfx sem chave privada ou sem certificado.")
    return (
        certificado.public_bytes(Encoding.PEM),
        chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
    )
