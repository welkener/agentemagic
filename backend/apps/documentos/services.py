"""
Recebimento de documento: guarda o arquivo e cria o registro, nessa ordem.

**A ordem importa e é a única decisão real deste módulo.** Primeiro o objeto vai
para o storage, depois nasce a linha no banco. Se invertesse, uma falha no meio
deixaria um `Documento` apontando para um arquivo que não existe — e o contador
abriria a fila de revisão para encontrar um link quebrado, sem saber se o
cliente mandou algo ou não.

Na ordem certa, a falha no meio deixa um objeto órfão no bucket: invisível,
barato e varrível depois. Entre perder a referência e desperdiçar bytes, a
escolha é fácil.

**Reenvio do mesmo arquivo não vira documento novo.** O cliente que manda a
mesma foto duas vezes — porque não viu o recibo, porque o WhatsApp reenviou —
não pode encher a fila do contador com duplicata. O `hash_sha256` reconhece,
e a resposta diz que já estava lá.

**A leitura vem depois do registro, e nunca derruba o recebimento.** O documento
existe assim que a linha nasce; o que `extracao` consegue provar sobre ele é
melhoria em cima disso. Se a leitura falhar — PDF corrompido, XML malformado,
biblioteca com bug —, o pior desfecho é um documento na fila do contador, que é
exatamente onde ele estaria se a fase 2 não existisse. Falhar aqui e devolver
erro ao cliente seria perder o que já está guardado por causa de um extra.
"""
from __future__ import annotations

import secrets

import structlog
from django.db import IntegrityError

from apps.audit.services import registrar
from apps.documentos import armazenamento, extracao
from apps.documentos.models import Documento

logger = structlog.get_logger(__name__)

# Teto do arquivo aceito. Foto de nota tem alguns MB; acima disso é quase certo
# vídeo ou engano, e ler 50 MB na memória de um contêiner compartilhado com
# outros projetos é maneira barata de derrubar tudo.
TAMANHO_MAXIMO = 12 * 1024 * 1024

_ALFABETO = "23456789BCDFGHJKLMNPQRSTVWXZ"


class ErroDeRecebimento(Exception):
    """O documento não pôde ser aceito. A mensagem é dita ao cliente."""


def _protocolo() -> str:
    return "DOC-" + "".join(secrets.choice(_ALFABETO) for _ in range(8))


def receber(
    *,
    ctx,
    conteudo: bytes,
    nome_arquivo: str,
    tipo_mime: str = "",
    origem: str = Documento.Origem.WHATSAPP,
) -> tuple[Documento, bool]:
    """Guarda e registra. Devolve `(documento, é_novo)`.

    `é_novo` falso significa reenvio do mesmo arquivo — quem chama usa isso para
    responder "já tinha recebido" em vez de "recebi", que é a diferença entre o
    cliente ficar tranquilo e o cliente mandar uma terceira vez.
    """
    if not conteudo:
        raise ErroDeRecebimento("O arquivo chegou vazio. Pode mandar de novo?")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroDeRecebimento(
            "Esse arquivo é grande demais para eu guardar. "
            "Se for um vídeo, me mande uma foto ou o PDF."
        )
    if not armazenamento.habilitado():
        # Recusar é melhor que aceitar e perder: quem manda a nota e ouve
        # "recebi" precisa que isso seja verdade.
        raise ErroDeRecebimento(
            "Ainda não consigo guardar arquivos por aqui. Já avisei seu contador — "
            "por enquanto, envie por e-mail para ele. 🙏"
        )

    import hashlib

    digest = hashlib.sha256(conteudo).hexdigest()
    ja_existe = Documento.objects.filter(
        cliente=ctx.cliente, hash_sha256=digest
    ).first()
    if ja_existe is not None:
        return ja_existe, False

    # Storage primeiro — ver o cabeçalho.
    guardado = armazenamento.guardar(ctx.cliente, conteudo, nome_arquivo, tipo_mime)

    for _ in range(5):
        try:
            documento = Documento.objects.create(
                cliente=ctx.cliente,
                usuario=ctx.usuario,
                origem=origem,
                bucket=guardado.bucket,
                chave=guardado.chave,
                nome_arquivo=nome_arquivo[:200],
                tipo_mime=tipo_mime[:100],
                tamanho=guardado.tamanho,
                hash_sha256=guardado.hash_sha256,
                protocolo=_protocolo(),
            )
            break
        except IntegrityError:
            continue
    else:  # pragma: no cover - cinco colisões de protocolo não é caminho previsto
        raise ErroDeRecebimento("Não consegui registrar o documento. Tente de novo.")

    lido = extracao.extrair(conteudo, nome_arquivo, tipo_mime, ctx.cliente)
    documento.aplicar_extracao(lido)

    registrar(
        "documento_recebido",
        {
            "protocolo": documento.protocolo,
            "tamanho": guardado.tamanho,
            "tipo_mime": tipo_mime,
            # Como foi lido e com quanta certeza entram na trilha porque é a
            # pergunta que aparece quando um lançamento sai errado: quem decidiu
            # o tipo deste documento, a máquina ou alguém?
            "metodo_leitura": lido.metodo,
            "confianca": lido.confianca,
            "tipo": documento.tipo,
            # O nome do arquivo pode conter dado do titular ("extrato-joao.pdf"),
            # e por isso vai como `mensagem` — campo que a trilha cifra por
            # titular (ver `audit/conteudo.CAMPOS_PESSOAIS`).
            "mensagem": nome_arquivo,
            **ctx.para_trilha(),
        },
        cliente=ctx.cliente,
    )
    return documento, True


def recibo(documento: Documento, novo: bool) -> str:
    """O que o cliente lê depois de mandar o arquivo.

    Diz o que aconteceu e o que NÃO aconteceu: o documento foi guardado, e ainda
    não foi lançado. Prometer lançamento aqui seria prometer o que só acontece
    depois da revisão — e o cliente que acredita nisso para de cobrar.

    **Repetir a leitura em voz alta é uma conferência, não enfeite.** Quando a
    chave de acesso foi lida, o cliente vê "NF-e 4471 de Distribuidora X,
    R$ 1.240,00" — e é ele, não o contador, quem sabe na hora se mandou o
    arquivo errado. É a checagem mais barata do sistema, feita por quem tem o
    contexto, no único instante em que ele está olhando.
    """
    lido = (documento.dados_extraidos or {}).get("resumo") or ""
    if not lido:
        frase_leitura = ""
    elif documento.classificado_por_maquina:
        frase_leitura = f"Li: {lido}.\n\n"
    else:
        # Leitura sem prova recebe verbo de dúvida. O resumo já traz a ressalva
        # dentro dele (`extracao._com_ressalva`), e "Li:" na frente desmentiria.
        frase_leitura = f"Pelo que vi, {lido}.\n\n"

    if not novo:
        return (
            f"Esse arquivo eu já tinha recebido 👍\n"
            f"Protocolo: {documento.protocolo}\n\n"
            f"{frase_leitura}"
            "Já está com o seu contador."
        )

    if documento.classificado_por_maquina:
        return (
            "Recebi seu documento 📎\n"
            f"Protocolo: {documento.protocolo}\n\n"
            f"{frase_leitura}"
            f"Já registrei como {documento.get_tipo_display().lower()}. "
            "Se não for isso, me avise que seu contador corrige."
        )

    return (
        "Recebi seu documento 📎\n"
        f"Protocolo: {documento.protocolo}\n\n"
        f"{frase_leitura}"
        "Seu contador vai conferir e classificar. Te aviso se faltar alguma coisa."
    )


def fila_de_revisao(escritorio):
    """O que espera olho humano, mais antigo primeiro.

    Ordem crescente porque esta fila existe para ser esvaziada — e o documento
    que envelhece é o que precisa aparecer no topo.
    """
    return (
        Documento.objects.filter(
            cliente__escritorio=escritorio,
            situacao=Documento.Situacao.AGUARDANDO_REVISAO,
        )
        .select_related("cliente", "usuario")
        .order_by("criado_em")
    )
