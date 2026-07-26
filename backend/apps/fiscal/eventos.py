"""
Eventos da NFS-e Nacional — hoje só o de cancelamento (`e101101`).

Cancelar NFS-e **não é DELETE**: é um pedido de registro de evento
(`PedRegEvento`), XML próprio, assinado com o mesmo certificado da emissão e
enviado gzip+base64 por mTLS. Mesmo caminho da DPS, documento diferente.

O que o schema impôs (`pedRegEvento_v1.00.xsd` / `tiposEventos_v1.00.xsd`)
-------------------------------------------------------------------------
- **`chNFSe` é a chave de acesso de 50 dígitos** — *não* é o "protocolo" que o
  projeto vinha guardando. São coisas diferentes: o protocolo identifica o
  processamento, a chave identifica o documento. Sem a chave não há como
  cancelar, e por isso `Intencao` ganhou `chave_nfse`.
- **`Id` do infPedReg é `PRE[0-9]{59}`** = chNFSe(50) + código do evento(6) +
  sequencial do pedido(3).
- **`xDesc` é uma enumeração de valor único**: literalmente
  `"Cancelamento de NFS-e"`. Qualquer outro texto invalida o documento.
- **`cMotivo` só aceita 1, 2 ou 9.** Texto livre vai em `xMotivo`, que exige
  **no mínimo 15 caracteres** — "erro" não passa.
"""
from __future__ import annotations

from django.utils import timezone

from .dps import (
    NAMESPACE_NFSE,
    TPAMB_HOMOLOGACAO,
    _xml_datetime,
    assinar,  # noqa: F401  (reexportado — mesmo certificado, mesmo perfil de assinatura)
    empacotar,  # noqa: F401
)

CODIGO_EVENTO_CANCELAMENTO = "101101"
DESCRICAO_CANCELAMENTO = "Cancelamento de NFS-e"  # enumeração fixa no XSD

# `cMotivo` — os três únicos valores aceitos pelo schema (TSCodJustCanc).
MOTIVO_ERRO_EMISSAO = "1"
MOTIVO_SERVICO_NAO_PRESTADO = "2"
MOTIVO_OUTROS = "9"
MOTIVOS_VALIDOS = (MOTIVO_ERRO_EMISSAO, MOTIVO_SERVICO_NAO_PRESTADO, MOTIVO_OUTROS)

TAMANHO_MINIMO_JUSTIFICATIVA = 15  # TSMotivo


class ErroEventoInvalido(Exception):
    """Dados insuficientes ou inválidos para montar o pedido de evento."""


def montar_id_evento(chave_nfse: str, sequencial: int) -> str:
    """`PRE[0-9]{59}` = chNFSe(50) + código do evento(6) + sequencial(3)."""
    return (
        "PRE"
        + chave_nfse.zfill(50)
        + CODIGO_EVENTO_CANCELAMENTO
        + str(sequencial).zfill(3)
    )


def montar_cancelamento(
    cliente,
    chave_nfse: str,
    justificativa: str,
    codigo_motivo: str = MOTIVO_ERRO_EMISSAO,
    sequencial: int = 1,
    ambiente: int = TPAMB_HOMOLOGACAO,
) -> tuple[bytes, str]:
    """Monta o XML (não assinado) do pedido de cancelamento. Devolve `(xml, Id)`."""
    from nfelib.nfse.bindings.v1_0.ped_reg_evento_v1_00 import PedRegEvento
    from nfelib.nfse.bindings.v1_0.tipos_eventos_v1_00 import Te101101, TcinfPedReg
    from xsdata.formats.dataclass.serializers import XmlSerializer
    from xsdata.formats.dataclass.serializers.config import SerializerConfig

    chave = "".join(c for c in (chave_nfse or "") if c.isdigit())
    if len(chave) != 50:
        raise ErroEventoInvalido(
            "Cancelamento exige a chave de acesso da NFS-e (50 dígitos). "
            "O protocolo de emissão não serve — são identificadores diferentes."
        )

    if codigo_motivo not in MOTIVOS_VALIDOS:
        raise ErroEventoInvalido(
            f"Código de motivo inválido ({codigo_motivo}). O schema aceita apenas "
            f"{', '.join(MOTIVOS_VALIDOS)}."
        )

    texto = (justificativa or "").strip()
    if len(texto) < TAMANHO_MINIMO_JUSTIFICATIVA:
        # Recusar aqui é melhor que a Sefin recusar depois: a mensagem diz o
        # que fazer, e o pedido não chega a consumir sequencial de evento.
        raise ErroEventoInvalido(
            f"A justificativa precisa de pelo menos {TAMANHO_MINIMO_JUSTIFICATIVA} "
            f"caracteres (tem {len(texto)})."
        )

    id_evento = montar_id_evento(chave, sequencial)

    inf = TcinfPedReg(
        tpAmb=ambiente,
        verAplic="MagicBI-1.0",
        dhEvento=_xml_datetime(timezone.localtime()),
        CNPJAutor=cliente.cnpj,
        chNFSe=chave,
        nPedRegEvento=str(sequencial),
        e101101=Te101101(
            xDesc=DESCRICAO_CANCELAMENTO,
            cMotivo=codigo_motivo,
            xMotivo=texto[:255],
        ),
        Id=id_evento,
    )

    serializer = XmlSerializer(config=SerializerConfig(indent=None, xml_declaration=True))
    xml = serializer.render(
        PedRegEvento(infPedReg=inf, versao="1.00"), ns_map={None: NAMESPACE_NFSE}
    )
    return xml.encode("utf-8"), id_evento


def caminho_xsd_evento() -> str:
    import os

    import nfelib

    return os.path.join(
        os.path.dirname(nfelib.__file__), "nfse", "schemas", "v1_0", "pedRegEvento_v1.00.xsd"
    )


def validar_evento_contra_xsd(xml: bytes) -> list[str]:
    """Mesma lição da DPS: asserção de campo não pega bloco obrigatório ausente."""
    from lxml import etree

    schema = etree.XMLSchema(etree.parse(caminho_xsd_evento()))
    documento = etree.fromstring(xml)
    if schema.validate(documento):
        return []
    return [f"{e.message} (linha {e.line})" for e in schema.error_log]
