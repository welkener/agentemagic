"""
Abertura de chamado e pedido de atendimento.

Duas regras governam este módulo, e as duas são sobre **não prometer demais**:

1. **O protocolo é gerado no núcleo**, nunca pelo modelo. Número de protocolo
   inventado por LLM é a pior espécie de alucinação: o cliente anota, cobra por
   ele e ninguém encontra.
2. **Data preferida não é horário confirmado.** O sistema não conhece a agenda
   do escritório, então tudo que ele pode registrar é a preferência do cliente.
   A resposta diz isso com todas as letras — "vou passar para a equipe", não
   "agendado para quinta".

A leitura de data é deliberadamente pobre: hoje, amanhã, depois de amanhã e
data explícita. O que não casar vira `None`, e o escritório combina o horário.
Adivinhar "semana que vem" custaria erro numa direção em que errar é caro (o
cliente aparece no dia errado) e barato de evitar (uma pergunta a mais).
"""
from __future__ import annotations

import re
import secrets
from datetime import date, timedelta

from django.db import IntegrityError
from django.utils import timezone

from apps.atendimento.models import Solicitacao
from apps.audit.services import registrar

_PREFIXO = {
    Solicitacao.Tipo.CHAMADO: "CH",
    Solicitacao.Tipo.ATENDIMENTO: "AT",
}

# Sem vogais e sem os dígitos que se confundem com letra (0/O, 1/I): o protocolo
# é lido em voz alta ao telefone e digitado de volta. Tirar a ambiguidade custa
# alguns bits de entropia e evita a chamada "não acho esse protocolo".
_ALFABETO = "23456789BCDFGHJKLMNPQRSTVWXZ"

_RE_DATA = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b")


def _sufixo(tamanho: int = 6) -> str:
    return "".join(secrets.choice(_ALFABETO) for _ in range(tamanho))


def gerar_protocolo(tipo: str, quando=None) -> str:
    """`CH-20260809-K7X2WB` — legível, ordenável por data, sem revelar volume.

    O sufixo é aleatório e não sequencial de propósito: protocolo sequencial
    conta ao cliente quantos chamados o escritório recebe, que é informação
    comercial do escritório.
    """
    dia = (quando or timezone.localdate()).strftime("%Y%m%d")
    return f"{_PREFIXO.get(tipo, 'SO')}-{dia}-{_sufixo()}"


def interpretar_data(texto: str, hoje: date | None = None) -> date | None:
    """Data preferida dita na mensagem, ou None quando não está clara.

    Ano ausente resolve para o próximo dia que ainda não passou — "dia 3" em
    30/dez é janeiro, não um pedido de atendimento no passado.
    """
    hoje = hoje or timezone.localdate()
    minusculo = texto.lower()

    if "depois de amanhã" in minusculo or "depois de amanha" in minusculo:
        return hoje + timedelta(days=2)
    if "amanhã" in minusculo or "amanha" in minusculo:
        return hoje + timedelta(days=1)
    if "hoje" in minusculo:
        return hoje

    achado = _RE_DATA.search(texto)
    if achado is None:
        return None

    dia, mes, ano = achado.group(1), achado.group(2), achado.group(3)
    try:
        if ano:
            completo = int(ano) if len(ano) == 4 else 2000 + int(ano)
            return date(completo, int(mes), int(dia))
        candidata = date(hoje.year, int(mes), int(dia))
        return candidata if candidata >= hoje else date(hoje.year + 1, int(mes), int(dia))
    except ValueError:
        # "32/13" — o cliente escreveu algo que não é data. Melhor não registrar
        # preferência nenhuma do que registrar uma inventada.
        return None


def abrir(
    *,
    ctx,
    tipo: str,
    assunto: str,
    descricao: str = "",
    preferencia_data: date | None = None,
) -> Solicitacao:
    """Cria a solicitação com protocolo único e registra na trilha.

    A colisão de protocolo é improvável (28⁶) e tratada mesmo assim: um
    `IntegrityError` propagado aqui viraria "não consegui registrar seu chamado"
    para quem já está com problema.
    """
    for _ in range(5):
        protocolo = gerar_protocolo(tipo)
        try:
            solicitacao = Solicitacao.objects.create(
                cliente=ctx.cliente,
                usuario=ctx.usuario,
                tipo=tipo,
                assunto=assunto[:120],
                descricao=descricao,
                preferencia_data=preferencia_data,
                canal=ctx.canal,
                protocolo=protocolo,
            )
            break
        except IntegrityError:
            continue
    else:  # pragma: no cover - cinco colisões seguidas não é caminho previsto
        raise IntegrityError("não foi possível gerar protocolo único de solicitação")

    registrar(
        "solicitacao_aberta",
        {
            "protocolo": solicitacao.protocolo,
            "tipo": tipo,
            "assunto": assunto,
            # `mensagem` é campo pessoal e entra cifrado por titular na trilha —
            # ver `audit/conteudo.CAMPOS_PESSOAIS`.
            "mensagem": descricao,
            **ctx.para_trilha(),
        },
        cliente=ctx.cliente,
    )
    return solicitacao


def abertas_do_escritorio(escritorio):
    """Fila do contador: o que ainda não foi resolvido, mais antigo primeiro.

    Ordem crescente e não decrescente porque esta lista existe para ser
    esvaziada — o chamado que envelhece é o que precisa aparecer no topo.
    """
    return (
        Solicitacao.objects.filter(
            cliente__escritorio=escritorio, estado__in=[Solicitacao.Estado.ABERTA, Solicitacao.Estado.EM_ANDAMENTO]
        )
        .select_related("cliente", "usuario")
        .order_by("criado_em")
    )
