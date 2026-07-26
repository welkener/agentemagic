"""
De onde vem a chave de cifra, e como trocá-la sem perder o que já está cifrado.

Dois problemas concretos do desenho anterior, que este módulo resolve:

**1. A chave vinha só de variável de ambiente.** `FIELD_ENCRYPTION_KEY` entrava
pelo `environment:` do docker-compose, o que a torna legível por qualquer um com
acesso ao socket do Docker (`docker inspect`) ou ao host (`/proc/<pid>/environ`)
— inclusive por processos que não têm nada a ver com o app. Agora a origem
preferida é **arquivo** (`FIELD_ENCRYPTION_KEY_FILE`), que é o formato de
`docker secret` e `systemd LoadCredential`: não aparece em nenhum dos dois.

**2. Não existia rotação.** Uma chave só, e trocá-la tornava ilegível tudo que
já estava no banco. Na prática isso significa que, se a chave vazasse, a única
saída seria redigitar à mão todo token OAuth, todo `.pfx` e toda senha — de
todos os clientes. E também impedia migrar para um cofre depois, pelo mesmo
motivo.

O modelo agora é **uma chave ativa + N chaves antigas**: cifra sempre com a
ativa, decifra tentando a ativa e depois as antigas (`MultiFernet`). Isso torna
a rotação um processo **sem downtime e reversível**:

    1. gere a nova chave e coloque-a na FRENTE da lista (ativa);
    2. mantenha a antiga na lista — o que já está no banco continua legível;
    3. rode `python manage.py rotacionar_chave` para recifrar tudo;
    4. só então remova a antiga da lista.

Enquanto o passo 3 não termina, o sistema funciona normalmente. É essa
propriedade que faz a migração para AWS Secrets Manager (ou qualquer cofre) ser
depois uma troca de origem da chave, não um mutirão de redigitação.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings


class ErroChaveDeCifraAusente(Exception):
    """Nenhuma chave configurada — não é seguro persistir segredos."""


def _do_arquivo(caminho: str) -> str:
    """Lê a chave de um arquivo (docker secret / systemd credential).

    `strip()` porque editor de texto adiciona `\\n` no fim e a chave Fernet é
    base64 estrito — um `\\n` invisível daria "Invalid key" sem explicar o
    porquê, num ponto do sistema onde ninguém quer depurar.
    """
    try:
        return Path(caminho).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ErroChaveDeCifraAusente(
            f"FIELD_ENCRYPTION_KEY_FILE aponta para '{caminho}', que não deu para ler: {exc}"
        ) from exc


def chaves_configuradas() -> list[str]:
    """Todas as chaves, da ativa para as mais antigas.

    Ordem de origem (a primeira que existir vence):

    1. `FIELD_ENCRYPTION_KEY_FILE` — arquivo, o jeito recomendado;
    2. `FIELD_ENCRYPTION_KEY` — variável de ambiente, mantida por compatibilidade
       com a instalação que já está no ar.

    Em ambas, várias chaves podem vir separadas por vírgula: a **primeira é a
    ativa** (usada para cifrar), as demais só decifram.
    """
    caminho = getattr(settings, "FIELD_ENCRYPTION_KEY_FILE", "")
    bruto = _do_arquivo(caminho) if caminho else getattr(settings, "FIELD_ENCRYPTION_KEY", "")

    chaves = [parte.strip() for parte in str(bruto or "").split(",") if parte.strip()]
    if not chaves:
        raise ErroChaveDeCifraAusente(
            "Nenhuma chave de cifra configurada. Gere uma com "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "e aponte FIELD_ENCRYPTION_KEY_FILE para um arquivo contendo ela "
            "(preferido), ou defina FIELD_ENCRYPTION_KEY."
        )
    return chaves


def chave_ativa() -> str:
    """A chave com que se CIFRA. Rotacionar = colocar a nova na frente."""
    return chaves_configuradas()[0]
