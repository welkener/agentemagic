"""
Vínculo de certificado fiscal — os 3 modos de custódia decididos em 25/jul/2026
(`docs/magicbi-custodia-fiscal.md`). Toda outorga/upload gera evento na
trilha de auditoria append-only (regra invariável do credenciamento, ver
`docs/magicbi-custodia-fiscal.md` §4) — antes desta mudança, o app
`credentials` não registrava nada em `apps.audit`.
"""
from __future__ import annotations

from apps.audit.services import registrar

from .certificados import extrair_metadados
from .crypto import cifrar, cifrar_bytes
from .models import Credencial


def vincular_certificado_pfx(credencial: Credencial, pfx_bytes: bytes, senha: str):
    """Valida o .pfx, extrai metadados e grava tudo cifrado na credencial.

    Levanta `ErroCertificadoInvalido` (de `certificados.py`) se a senha
    estiver errada ou o arquivo for inválido — quem chama decide como
    mostrar isso (o admin usa `ValidationError` no form).
    """
    metadados = extrair_metadados(pfx_bytes, senha)

    credencial.tipo = Credencial.Tipo.CERTIFICADO_PFX
    credencial.pfx_arquivo_cifrado = cifrar_bytes(pfx_bytes)
    credencial.pfx_senha_cifrada = cifrar(senha)
    credencial.certificado_cnpj = metadados.cnpj
    credencial.certificado_validade = metadados.validade
    credencial.save()

    registrar(
        "certificado_pfx_vinculado",
        {
            "credencial_id": credencial.id,
            "certificado_cnpj": metadados.cnpj,
            "certificado_validade": metadados.validade.isoformat(),
            "cnpj_diverge": credencial.certificado_cnpj_diverge,
        },
        cliente=credencial.cliente,
    )
    return metadados


def vincular_certificado_psc(credencial: Credencial, provedor: str, identificador: str) -> None:
    """Registra o vínculo com um certificado em nuvem (PSC).

    Só guarda a referência (provedor + identificador) — a chave privada
    nunca sai do PSC. Assinatura remota de fato ainda não está implementada
    (pendente confirmação de mTLS remoto por fornecedor, ver
    `docs/magicbi-ondas-desenvolvimento.md` §2); isto prepara o cadastro.
    """
    credencial.tipo = Credencial.Tipo.CERTIFICADO_PSC
    credencial.psc_provedor = provedor
    credencial.psc_identificador = identificador
    credencial.save()

    registrar(
        "certificado_psc_vinculado",
        {"credencial_id": credencial.id, "psc_provedor": provedor},
        cliente=credencial.cliente,
    )
