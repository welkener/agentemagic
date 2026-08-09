"""
Ferramentas que levam o cliente até uma pessoa do escritório.

São as duas capacidades do Sprint 2 que **escrevem sem depender de ERP nenhum** —
e por isso são as primeiras que qualquer escritório novo tem funcionando no dia
um, antes de qualquer integração.

O valor delas é menos óbvio que o das consultas e maior: hoje, quando o agente
não sabe responder, a conversa termina em "fale com seu contador", que é um beco.
O cliente que já está no WhatsApp precisa abrir outro canal, e o escritório não
fica sabendo que ele tentou. Com estas duas, o beco vira fila — com protocolo do
lado do cliente e uma linha na tela Hoje do lado do contador.
"""
from __future__ import annotations

from apps.agents.ferramentas.base import registrar_ferramenta
from apps.atendimento import services as atendimento
from apps.atendimento.models import Solicitacao


def _resumo(mensagem: str, padrao: str) -> str:
    """Primeira linha da mensagem como assunto, ou um rótulo genérico.

    Montado por regra, não pelo modelo: assunto de chamado é o que o contador lê
    na fila para priorizar, e um resumo alucinado priorizaria errado. Recortar a
    frase do cliente é pior redação e informação verdadeira.
    """
    limpa = " ".join(mensagem.split())
    if not limpa:
        return padrao
    return limpa[:117] + "..." if len(limpa) > 120 else limpa


@registrar_ferramenta(
    "abrir_chamado",
    descricao="abrir um chamado para a equipe do escritório quando eu não resolvo sozinho",
    exemplos=("preciso falar com o contador", "tenho um problema com a folha"),
)
def abrir_chamado(ctx, mensagem: str) -> str:
    solicitacao = atendimento.abrir(
        ctx=ctx,
        tipo=Solicitacao.Tipo.CHAMADO,
        assunto=_resumo(mensagem, "Chamado aberto pelo WhatsApp"),
        descricao=mensagem,
    )
    return (
        "Abri um chamado com a equipe do seu escritório 📩\n\n"
        f"Protocolo: {solicitacao.protocolo}\n\n"
        "Eles já estão vendo e respondem por aqui mesmo. "
        "Guarde o protocolo caso precise cobrar. 🙏"
    )


@registrar_ferramenta(
    "agendar_atendimento",
    descricao="registrar um pedido de conversa com o contador, com a data que eu prefiro",
    exemplos=("quero marcar uma reunião", "posso falar com o contador amanhã?"),
)
def agendar_atendimento(ctx, mensagem: str) -> str:
    """Registra a **preferência** de data — nunca confirma horário.

    O sistema não conhece a agenda do escritório. Responder "agendado para
    quinta" seria assumir uma disponibilidade que ele não tem meio de checar, e
    o cliente que aparece no dia errado perde a viagem por causa de uma frase
    escrita para soar resolutiva.
    """
    preferida = atendimento.interpretar_data(mensagem)
    solicitacao = atendimento.abrir(
        ctx=ctx,
        tipo=Solicitacao.Tipo.ATENDIMENTO,
        assunto=_resumo(mensagem, "Pedido de atendimento pelo WhatsApp"),
        descricao=mensagem,
        preferencia_data=preferida,
    )

    quando = (
        f"Anotei sua preferência para *{preferida.strftime('%d/%m')}*.\n"
        if preferida
        else "Eles vão combinar o melhor horário com você.\n"
    )
    return (
        "Pedido de atendimento registrado 📅\n\n"
        f"Protocolo: {solicitacao.protocolo}\n"
        f"{quando}\n"
        "Quem confirma o horário é a equipe do escritório — te avisam por aqui."
    )
