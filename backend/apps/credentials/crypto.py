"""
Criptografia de campo para segredos guardados localmente (fase MVP/piloto).

Implementação de "cofre" para este estágio: os segredos (tokens OAuth,
client_secret dos apps Conta Azul/Bling) ficam cifrados em repouso com Fernet
(AES-128-CBC + HMAC), chave fora do banco (`FIELD_ENCRYPTION_KEY`, variável de
ambiente/cofre do processo). Isto substitui o AWS Secrets Manager + KMS
("Sigillum") só até o deploy em nuvem (Fase 1 do cronograma) — a troca é só
de backend de armazenamento, o contrato do model não muda.

Nunca logar/serializar o valor decifrado; ele só deve existir em memória pelo
tempo da chamada à API externa.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class ErroChaveDeCifraAusente(Exception):
    """FIELD_ENCRYPTION_KEY não configurada — não é seguro persistir segredos."""


def _fernet():
    from cryptography.fernet import Fernet

    chave = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
    if not chave:
        raise ErroChaveDeCifraAusente(
            "FIELD_ENCRYPTION_KEY ausente — gere uma com "
            "`Fernet.generate_key()` e configure no .env antes de salvar segredos."
        )
    return Fernet(chave.encode() if isinstance(chave, str) else chave)


def cifrar(texto_puro: str) -> bytes:
    if not texto_puro:
        return b""
    return _fernet().encrypt(texto_puro.encode("utf-8"))


def decifrar(dados_cifrados: bytes) -> str:
    if not dados_cifrados:
        return ""
    return _fernet().decrypt(bytes(dados_cifrados)).decode("utf-8")


class CampoTextoCifrado(models.BinaryField):
    """TextField que cifra/decifra de forma transparente com Fernet."""

    def get_prep_value(self, value):
        if value is None:
            return value
        if isinstance(value, bytes):
            return value  # já cifrado (ex.: carregado do banco)
        return cifrar(value)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return ""
        return decifrar(bytes(value))

    def to_python(self, value):
        if value is None or isinstance(value, str):
            return value or ""
        return decifrar(bytes(value))
