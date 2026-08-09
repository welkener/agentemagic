"""
"De qual empresa você quer falar?" — DEC-03.

Um telefone pode falar por várias empresas: o sócio de duas, o financeiro de
três lojas, o contador terceirizado. Antes do nível `usuario` isso nem cabia no
cadastro; agora cabe, e aparece uma pergunta que não existia — **por qual delas
esta mensagem fala.**

A regra é uma só e vale para tudo aqui: **na dúvida, perguntar.** Errar a
empresa não é confusão de tela, é nota fiscal no CNPJ errado, com retificação,
prazo e cliente irritado. Uma mensagem a mais é sempre mais barata.

Como o estado funciona (`security.EmpresaEmFoco`):

    sem registro / vencido  →  manda o menu, grava `cliente = NULL`
    cliente = NULL          →  a próxima mensagem é lida como resposta ao menu
    cliente preenchido      →  é dessa empresa que se está falando

O terceiro estado expira (`FOCO_EMPRESA_TTL_MINUTOS`). Conversa que esfriou e
voltou horas depois provavelmente mudou de assunto — e possivelmente de empresa.

**O que este módulo deliberadamente NÃO faz:** adivinhar a empresa pelo conteúdo
da mensagem. "emite uma nota pra Padaria do João" tem um nome de empresa dentro,
e ele é o *tomador*, não o prestador. Trocar de empresa exige pedido explícito
("trocar empresa") ou resposta a um menu — nunca inferência.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.conf import settings
from django.utils import timezone

from apps.audit.services import registrar

logger = structlog.get_logger(__name__)

# Uma hora: tempo de uma conversa com pausa para o almoço, não de um dia de
# trabalho. Curto o bastante para que a empresa em foco ainda seja a que a
# pessoa tem em mente.
TTL_MINUTOS = getattr(settings, "FOCO_EMPRESA_TTL_MINUTOS", 60)

# Pedido explícito de troca. Determinístico e estreito de propósito: qualquer
# tentativa de inferir a empresa pelo assunto da mensagem produziria falso
# positivo justamente na mensagem que emite nota.
_RE_TROCAR = re.compile(
    r"\b(trocar|mudar|outra|muda|troca)\s+(de\s+)?empresas?\b|^\s*empresas?\s*$",
    re.IGNORECASE,
)
_RE_SO_DIGITOS = re.compile(r"^\D*(\d{1,2})\D*$")


@dataclass(frozen=True)
class Resolucao:
    """Resultado da resolução de escopo de uma mensagem.

    `resposta` preenchida significa **pare aqui**: a mensagem foi consumida pela
    escolha de empresa e não deve seguir para o orquestrador. É a diferença
    entre "responder ao menu" e "pedir nota".
    """

    cliente: object | None = None
    resposta: str | None = None


def resolver(usuario, mensagem: str) -> Resolucao:
    """Decide por qual empresa esta mensagem fala.

    `usuario` None (número não cadastrado) devolve resolução vazia — quem
    responde por número desconhecido continua sendo o orquestrador, num lugar
    só.
    """
    if usuario is None:
        return Resolucao()

    todas = usuario.clientes_ativos()
    if not todas:
        return Resolucao()

    # O menu diz nomes de empresa em voz alta. Se a sessão daquele número não
    # está validada — número clonado, `wa_id` trocado —, listar a carteira é
    # entregar informação a quem o gate de segurança justamente ainda não
    # reconheceu. Então o menu só oferece o que já está validado; se nada
    # estiver, devolve a primeira e deixa o orquestrador pedir a revalidação,
    # que é exatamente o que acontece hoje com uma empresa só.
    empresas = [c for c in todas if _sessao_ok(c, usuario.telefone_whatsapp)]
    if not empresas:
        return Resolucao(cliente=todas[0])

    if len(empresas) == 1:
        # Caso esmagadoramente comum. Nenhum estado, nenhuma pergunta — e o
        # foco antigo é limpo para que ninguém volte a ele se um vínculo for
        # removido depois.
        _limpar(usuario)
        return Resolucao(cliente=empresas[0])

    foco = _foco_vigente(usuario)

    if foco is not None and foco.cliente_id is None:
        # Menu no ar: esta mensagem é a resposta.
        escolhida = _interpretar_escolha(mensagem, empresas)
        if escolhida is None:
            return Resolucao(resposta=_menu(empresas, repetindo=True))
        _fixar(usuario, escolhida, motivo="resposta_ao_menu")
        return Resolucao(
            cliente=escolhida,
            resposta=(
                f"Certo — falando da *{escolhida.nome}*. 👍\n"
                "O que você precisa?\n\n"
                "_Para falar de outra, é só dizer «trocar empresa»._"
            ),
        )

    if _RE_TROCAR.search(mensagem):
        _perguntar(usuario)
        return Resolucao(resposta=_menu(empresas))

    if foco is not None and foco.cliente_id is not None:
        vigente = next((e for e in empresas if e.pk == foco.cliente_id), None)
        if vigente is not None:
            return Resolucao(cliente=vigente)
        # O vínculo caiu enquanto a conversa estava aberta. Pergunta de novo em
        # vez de escolher a primeira da lista.
        logger.info("foco_empresa_invalidado", usuario=usuario.pk, cliente=foco.cliente_id)

    _perguntar(usuario)
    return Resolucao(resposta=_menu(empresas))


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------
def _menu(empresas, repetindo: bool = False) -> str:
    abertura = (
        "Não entendi qual empresa. 🤔 Responda com o *número* da lista:"
        if repetindo
        else (
            "Vi que você responde por mais de uma empresa. "
            "De qual delas vamos falar? Responda com o *número*:"
        )
    )
    linhas = [abertura, ""]
    for indice, empresa in enumerate(empresas, start=1):
        linhas.append(f"*{indice}* — {empresa.nome}")
    return "\n".join(linhas)


def _interpretar_escolha(mensagem: str, empresas):
    """Número do menu, pedaço do nome ou CNPJ. Nada disso casando → None.

    A ordem de `empresas` vem de `Usuario.clientes_ativos`, que ordena por nome
    e pk — estável entre duas mensagens. Ainda assim a escolha é **confirmada
    pelo nome** na resposta: se uma empresa entrar na carteira entre o menu e a
    resposta, os índices andam, e quem percebe é a pessoa lendo o nome de volta.
    """
    texto = (mensagem or "").strip()
    if not texto:
        return None

    casa_indice = _RE_SO_DIGITOS.match(texto)
    if casa_indice:
        indice = int(casa_indice.group(1))
        if 1 <= indice <= len(empresas):
            return empresas[indice - 1]
        return None

    digitos = re.sub(r"\D", "", texto)
    if len(digitos) >= 8:
        por_cnpj = [e for e in empresas if e.cnpj and digitos in e.cnpj]
        if len(por_cnpj) == 1:
            return por_cnpj[0]
        return None

    alvo = texto.casefold()
    por_nome = [e for e in empresas if alvo in e.nome.casefold()]
    # Duas empresas casando ("Padaria Centro" e "Padaria Sul" para "padaria")
    # é ambiguidade, não escolha — volta para o menu.
    return por_nome[0] if len(por_nome) == 1 else None


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
def _sessao_ok(cliente, wa_id: str) -> bool:
    # Import tardio: `security.services` alcança `clients`, e `clients` é quem
    # define o usuário que chega até aqui.
    from apps.security.services import sessao_ativa

    return sessao_ativa(cliente, wa_id)


def empresa_em_foco(usuario):
    """Empresa vigente sem tocar em nada — para registrar evento de mensagem
    que não chegou a ser interpretada (áudio ilegível, por exemplo).

    Devolve a única empresa quando só existe uma; None quando há várias e
    nenhuma foi escolhida ainda, porque nesse caso não há dono a atribuir.
    """
    if usuario is None:
        return None
    empresas = usuario.clientes_ativos()
    if len(empresas) == 1:
        return empresas[0]
    foco = _foco_vigente(usuario)
    if foco is None or foco.cliente_id is None:
        return None
    return next((e for e in empresas if e.pk == foco.cliente_id), None)


def _foco_vigente(usuario):
    from apps.security.models import EmpresaEmFoco

    foco = EmpresaEmFoco.objects.filter(usuario=usuario).first()
    if foco is None:
        return None
    if foco.atualizado_em < timezone.now() - timedelta(minutes=TTL_MINUTOS):
        foco.delete()
        return None
    return foco


def _perguntar(usuario) -> None:
    from apps.security.models import EmpresaEmFoco

    EmpresaEmFoco.objects.update_or_create(usuario=usuario, defaults={"cliente": None})


def _fixar(usuario, cliente, *, motivo: str) -> None:
    from apps.security.models import EmpresaEmFoco

    EmpresaEmFoco.objects.update_or_create(usuario=usuario, defaults={"cliente": cliente})
    # Vai para a trilha: numa auditoria de "quem mandou emitir esta nota", saber
    # que o número N estava falando pela empresa X naquele momento é parte da
    # resposta — e é uma escolha da pessoa, não do sistema.
    registrar(
        "empresa_em_foco_definida",
        {"usuario": usuario.pk, "telefone": usuario.telefone_whatsapp, "motivo": motivo},
        cliente=cliente,
    )


def _limpar(usuario) -> None:
    from apps.security.models import EmpresaEmFoco

    EmpresaEmFoco.objects.filter(usuario=usuario).delete()
