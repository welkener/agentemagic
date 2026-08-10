"""
Onde os documentos do cliente ficam guardados.

**A decisão que este módulo materializa:** o storage é falado pela API do S3, e
quem responde do outro lado é configuração. Hoje é um **MinIO** rodando em
contêiner no mesmo servidor; amanhã é a **AWS S3**. A troca é de variável de
ambiente — endpoint, chave, segredo, região —, e nenhuma linha de código muda.

Isso não é preciosismo de arquitetura, é o que evita o pior desfecho possível
para dado fiscal: ficar preso a um storage porque a aplicação aprendeu a falar
com ele de um jeito específico. Nota de entrada, extrato e contrato do cliente
não podem depender de uma migração arriscada para mudar de casa.

**Bucket por tenant, prefixo por cliente.** O caminho de um arquivo é
`magicbi-<slug-do-escritório>/<id-do-cliente>/<ano>/<mês>/<chave>`. Duas razões,
e a segunda é a que importa:

1. o bucket por escritório permite política de acesso, ciclo de vida e — se um
   dia for preciso — **entrega do acervo inteiro** de um tenant que saiu, sem
   varredura;
2. e permite que a permissão do storage repita a fronteira que o Postgres já
   guarda. Duas camadas independentes dizendo a mesma coisa é o mesmo desenho da
   RLS: quem esquecer de filtrar numa não vaza porque a outra segura.

**O que este módulo NÃO faz:** servir arquivo por URL pública. Toda leitura sai
por URL assinada com validade curta, gerada para quem já provou ter acesso. Um
extrato bancário com link permanente vaza no primeiro encaminhamento de
WhatsApp — e nunca mais é possível recolher.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

import structlog
from django.conf import settings
from django.utils import timezone

logger = structlog.get_logger(__name__)

# Validade da URL assinada. Curta de propósito: é o tempo de abrir o arquivo,
# não o de guardá-lo. Link que dura um dia é link que circula.
VALIDADE_URL_SEGUNDOS = 300


class ErroDeArmazenamento(RuntimeError):
    """Falha ao falar com o storage. Quem chama decide se tenta de novo."""


@dataclass(frozen=True)
class ArquivoGuardado:
    bucket: str
    chave: str
    tamanho: int
    hash_sha256: str

    @property
    def caminho(self) -> str:
        return f"{self.bucket}/{self.chave}"


def _config(nome: str, padrao: str = "") -> str:
    return getattr(settings, nome, padrao) or padrao


def habilitado() -> bool:
    """Há storage configurado.

    Sem ele o recebimento de documento é recusado com aviso, em vez de aceitar a
    foto e perdê-la — o cliente que manda a nota e ouve "recebi" precisa que
    isso seja verdade.
    """
    return bool(_config("S3_ENDPOINT") or _config("S3_USAR_AWS"))


def _cliente():
    import boto3
    from botocore.config import Config

    endpoint = _config("S3_ENDPOINT")
    return boto3.client(
        "s3",
        # Endpoint vazio = AWS. É exatamente aqui que a migração acontece: tirar
        # o `S3_ENDPOINT` do ambiente e pôr as credenciais da Amazon.
        endpoint_url=endpoint or None,
        aws_access_key_id=_config("S3_ACCESS_KEY"),
        aws_secret_access_key=_config("S3_SECRET_KEY"),
        region_name=_config("S3_REGIAO", "us-east-1"),
        config=Config(
            # `path` porque o MinIO não resolve bucket por subdomínio numa
            # instalação simples; a AWS aceita os dois. Deixar no padrão
            # (`virtual`) quebraria só contra o MinIO, e só em runtime.
            s3={"addressing_style": _config("S3_ESTILO_ENDERECO", "path")},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


def _sanitizar(texto: str) -> str:
    """Slug seguro para nome de bucket: minúsculo, sem acento, só letra e hífen."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    limpo = re.sub(r"[^a-z0-9-]+", "-", sem_acento.lower()).strip("-")
    return re.sub(r"-{2,}", "-", limpo)[:40] or "sem-nome"


def bucket_do_escritorio(escritorio) -> str:
    """Nome do bucket daquele tenant.

    Prefixo fixo para não colidir com bucket de outro sistema na mesma conta
    AWS — em MinIO local isso é irrelevante, na Amazon o namespace de bucket é
    global e a colisão é com o mundo inteiro.
    """
    prefixo = _config("S3_PREFIXO_BUCKET", "magicbi")
    return f"{prefixo}-{_sanitizar(getattr(escritorio, 'slug', '') or str(escritorio.pk))}"


def chave_do_documento(cliente, nome_arquivo: str, quando: datetime | None = None) -> str:
    """`<id-do-cliente>/<ano>/<mês>/<carimbo>-<nome>`.

    O ano e o mês no caminho não são enfeite: é o que permite regra de ciclo de
    vida por período (arquivar o que passou de cinco anos) sem precisar consultar
    o banco para saber a data de cada objeto.
    """
    quando = quando or timezone.now()
    seguro = _sanitizar(nome_arquivo.rsplit(".", 1)[0])
    extensao = nome_arquivo.rsplit(".", 1)[-1].lower() if "." in nome_arquivo else "bin"
    carimbo = quando.strftime("%d%H%M%S")
    return f"{cliente.pk}/{quando:%Y}/{quando:%m}/{carimbo}-{seguro}.{_sanitizar(extensao)}"


def garantir_bucket(escritorio) -> str:
    """Cria o bucket do tenant se ainda não existir. Idempotente."""
    from botocore.exceptions import ClientError

    nome = bucket_do_escritorio(escritorio)
    s3 = _cliente()
    try:
        s3.head_bucket(Bucket=nome)
        return nome
    except ClientError as erro:
        codigo = erro.response.get("Error", {}).get("Code", "")
        if codigo not in ("404", "NoSuchBucket", "NotFound"):
            raise ErroDeArmazenamento(f"não consegui checar o bucket {nome}: {erro}") from erro
    try:
        s3.create_bucket(Bucket=nome)
    except ClientError as erro:
        # Corrida entre dois workers criando o mesmo bucket não é erro.
        if erro.response.get("Error", {}).get("Code") not in (
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ):
            raise ErroDeArmazenamento(f"não consegui criar o bucket {nome}: {erro}") from erro
    logger.info("bucket_de_tenant_criado", bucket=nome)
    return nome


def guardar(cliente, conteudo: bytes, nome_arquivo: str, tipo_mime: str = "") -> ArquivoGuardado:
    """Grava o arquivo no bucket do escritório do cliente.

    Devolve o hash junto: é ele que responde depois "este arquivo é o mesmo que
    o cliente mandou?" e é o que permite detectar reenvio do mesmo documento sem
    comparar bytes de novo.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    if not habilitado():
        raise ErroDeArmazenamento("storage de documentos não configurado")

    bucket = garantir_bucket(cliente.escritorio)
    chave = chave_do_documento(cliente, nome_arquivo)
    digest = hashlib.sha256(conteudo).hexdigest()

    try:
        _cliente().put_object(
            Bucket=bucket,
            Key=chave,
            Body=conteudo,
            ContentType=tipo_mime or "application/octet-stream",
            # Metadado mínimo, e nenhum dado pessoal: o vínculo com o titular
            # mora no banco, sob RLS. Repeti-lo aqui criaria uma segunda cópia
            # fora do alcance da eliminação por LGPD.
            Metadata={"cliente": str(cliente.pk), "sha256": digest},
        )
    except (ClientError, BotoCoreError) as erro:
        raise ErroDeArmazenamento(f"não consegui guardar o arquivo: {erro}") from erro

    return ArquivoGuardado(
        bucket=bucket, chave=chave, tamanho=len(conteudo), hash_sha256=digest
    )


def url_temporaria(bucket: str, chave: str, segundos: int = VALIDADE_URL_SEGUNDOS) -> str:
    """URL assinada, de validade curta, para quem já provou ter acesso.

    Quem confere o acesso é a view que chama isto — a assinatura não substitui a
    checagem de escopo, ela só evita que o arquivo precise passar pelo Django.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return _cliente().generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": chave}, ExpiresIn=segundos
        )
    except (ClientError, BotoCoreError) as erro:
        raise ErroDeArmazenamento(f"não consegui gerar o link: {erro}") from erro


def baixar(bucket: str, chave: str) -> bytes:
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return _cliente().get_object(Bucket=bucket, Key=chave)["Body"].read()
    except (ClientError, BotoCoreError) as erro:
        raise ErroDeArmazenamento(f"não consegui ler o arquivo: {erro}") from erro


def apagar(bucket: str, chave: str) -> None:
    """Remove o objeto. Usado pela eliminação a pedido do titular (LGPD).

    Não levanta se o objeto já não existe: eliminar duas vezes tem que ser
    seguro, senão um retry deixa o processo de exclusão travado pela metade.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _cliente().delete_object(Bucket=bucket, Key=chave)
    except (ClientError, BotoCoreError) as erro:
        logger.warning("falha_ao_apagar_objeto", bucket=bucket, chave=chave, erro=str(erro))
