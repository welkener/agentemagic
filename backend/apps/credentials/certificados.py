"""
Leitura de metadados de certificados digitais A1 (.pfx/PKCS#12) — usa
`cryptography` (já é dependência do projeto, mantida pela PyCA, não um
projeto de terceiros sem afiliação oficial). Deliberadamente NÃO usa
`brans-nfe` para isto: abrir a chave privada de um cliente é sensível demais
para depender de uma lib não-oficial só pra extrair 3 campos.

Só lê metadados (CNPJ, razão social, validade) para conferência no admin —
nunca usa a chave privada aqui. A assinatura de documentos fiscais em si
(quando o modelo de custódia permitir) é responsabilidade de outro módulo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


class ErroCertificadoInvalido(Exception):
    """Senha errada, arquivo corrompido, ou .pfx sem certificado utilizável."""


@dataclass
class MetadadosCertificado:
    cnpj: str
    razao_social: str
    validade: date


def extrair_metadados(pfx_bytes: bytes, senha: str) -> MetadadosCertificado:
    """Abre o .pfx só para ler o certificado público — nunca persiste a chave aqui."""
    try:
        _, certificado, _ = pkcs12.load_key_and_certificates(pfx_bytes, senha.encode("utf-8"))
    except ValueError as exc:
        raise ErroCertificadoInvalido("Senha inválida ou arquivo .pfx corrompido.") from exc
    except Exception as exc:  # noqa: BLE001 — biblioteca externa, erro não tipado
        raise ErroCertificadoInvalido(f"Erro ao ler o certificado: {exc}") from exc

    if certificado is None:
        raise ErroCertificadoInvalido("Nenhum certificado encontrado no arquivo .pfx.")

    cnpj, razao_social = _extrair_identidade_icp_brasil(certificado)
    return MetadadosCertificado(
        cnpj=cnpj,
        razao_social=razao_social,
        validade=certificado.not_valid_after_utc.date(),
    )


def _extrair_identidade_icp_brasil(certificado) -> tuple[str, str]:
    """Certificados e-CNPJ ICP-Brasil costumam trazer "RAZAO SOCIAL:CNPJ" no
    Common Name — best-effort: se não achar o padrão, devolve string vazia em
    vez de falhar (o admin confere manualmente contra `Cliente.cnpj` antes de
    ativar a credencial; nunca bloqueia o upload por causa de uma AC que foge
    do padrão comum)."""
    try:
        cn = certificado.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except IndexError:
        return "", ""

    match = re.search(r"\b\d{14}\b", cn)
    cnpj = match.group() if match else ""
    razao_social = cn.split(":")[0].strip() if ":" in cn else cn
    return cnpj, razao_social
