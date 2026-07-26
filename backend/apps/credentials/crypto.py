"""
Criptografia de campo para os segredos guardados no banco.

Os segredos (tokens OAuth, `.pfx` e sua senha, `client_secret`, token do
WhatsApp) ficam cifrados em repouso com Fernet (AES-128-CBC + HMAC), chave fora
do banco. Um dump do Postgres, sozinho, não entrega nada.

**Rotação é de primeira classe** (26/jul/2026): cifra sempre com a chave ativa,
decifra com a ativa **ou** com qualquer chave antiga ainda listada
(`MultiFernet`). Antes havia uma chave só, e trocá-la tornava ilegível tudo que
já estava gravado — o que, na prática, significava que um vazamento obrigaria a
redigitar à mão todo segredo de todos os clientes. De onde vem a chave e como
rotacionar: `apps/credentials/chaves.py`.

Isto é o "cofre" desta fase. A troca para AWS Secrets Manager + KMS ("Sigillum",
seção 10 da arquitetura) muda só a **origem da chave** — o contrato dos models
não muda, e `Credencial.referencia_cofre` já existe para apontar o ARN.

Nunca logar/serializar o valor decifrado: ele só deve existir em memória pelo
tempo da chamada à API externa.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


from .chaves import ErroChaveDeCifraAusente, chaves_configuradas  # noqa: F401 (reexport)


def _fernet():
    """`MultiFernet` — cifra com a primeira chave, decifra com qualquer uma.

    Reconstruído a cada chamada de propósito: uma rotação feita com o processo
    no ar passa a valer na chamada seguinte, sem reiniciar o worker.
    """
    from cryptography.fernet import Fernet, MultiFernet

    return MultiFernet([Fernet(c.encode()) for c in chaves_configuradas()])


def cifrar(texto_puro: str) -> bytes:
    if not texto_puro:
        return b""
    return _fernet().encrypt(texto_puro.encode("utf-8"))


def decifrar(dados_cifrados: bytes) -> str:
    if not dados_cifrados:
        return ""
    return _fernet().decrypt(bytes(dados_cifrados)).decode("utf-8")


def cifrar_bytes(dados: bytes) -> bytes:
    """Igual a `cifrar()`, mas para conteúdo binário (ex.: upload de .pfx) —
    nunca assume utf-8."""
    if not dados:
        return b""
    return _fernet().encrypt(dados)


def decifrar_bytes(dados_cifrados: bytes) -> bytes:
    if not dados_cifrados:
        return b""
    return _fernet().decrypt(bytes(dados_cifrados))


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
