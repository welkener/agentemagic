"""
Alertas de negócio — o contador precisa saber, e ninguém avisava.

Até aqui, uma nota rejeitada pela Sefin virava uma linha de auditoria e uma
mensagem pro cliente no WhatsApp. O **contador** — que é quem responde
tecnicamente pela nota e quem consegue corrigir — só descobria se abrisse o
admin por conta própria.

Isso não é caso pra error tracker. Rejeição fiscal não é exceção: é um desfecho
normal do fluxo que por acaso é crítico pro negócio. Sentry nunca ia capturar,
porque nada quebrou.

Princípio inegociável deste módulo: **alerta nunca derruba a operação.** A nota
já foi rejeitada; falhar o envio do e-mail não pode desfazer a transição de
estado nem estourar pra cima. Toda falha aqui é engolida e logada.
"""
from __future__ import annotations

import structlog
from django.conf import settings
from django.core.mail import send_mail

from apps.audit.services import registrar

logger = structlog.get_logger(__name__)

# Erros que valem alerta. Ausente daqui = falha transitória (rede, rate limit),
# que o retry resolve e que não exige ninguém acordar.
ERROS_QUE_EXIGEM_ACAO = {
    "REJEITADA_SEFIN": "A Sefin recusou o documento.",
    "REJEITADA_CNAE_AUSENTE": "Faltou o código de tributação no cadastro do cliente.",
    "REJEITADA_MOTIVO_AUSENTE": "O cancelamento foi enviado sem justificativa válida.",
    "CADASTRO_FISCAL_INCOMPLETO": "O cadastro fiscal do cliente está incompleto.",
    "CERTIFICADO_INDISPONIVEL": "O certificado digital não está utilizável.",
    "AUTH_EXPIRADA": "A credencial de acesso expirou.",
    "EVENTO_INVALIDO": "O pedido de evento foi montado com dado inválido.",
}


def destinatarios_do_cliente(cliente) -> list[str]:
    """E-mails de quem pode agir: os membros do escritório dono da carteira.

    Prefere o **responsável**; se não houver, avisa todo mundo do escritório.
    Nunca cai no e-mail do cliente final: ele não corrige cadastro fiscal nem
    certificado, e receber isso só geraria ansiedade sem ação possível.
    """
    escritorio = getattr(cliente, "escritorio", None)
    if escritorio is None:
        return []

    membros = escritorio.membros.select_related("usuario")
    responsaveis = [m.usuario.email for m in membros if m.responsavel and m.usuario.email]
    if responsaveis:
        return responsaveis
    return [m.usuario.email for m in membros if m.usuario.email]


def alertar_rejeicao_fiscal(intencao, erro: str | None, detalhe: str = "") -> bool:
    """Avisa o escritório que um documento fiscal foi recusado.

    Devolve True se algum e-mail saiu. Não levanta exceção: ver docstring do
    módulo.
    """
    if not erro or erro not in ERROS_QUE_EXIGEM_ACAO:
        # Falha transitória: o retry cuida. Alertar aqui viraria ruído, e alerta
        # que vira ruído é alerta que ninguém lê quando importa.
        return False

    cliente = intencao.cliente
    explicacao = ERROS_QUE_EXIGEM_ACAO[erro]
    acao = "cancelamento" if intencao.tipo_acao == "cancelar_nfse" else "emissão"
    valor = intencao.payload.get("valor")

    # A trilha registra o alerta mesmo que o e-mail falhe — é o que permite
    # auditar depois "o escritório foi avisado?".
    registrar(
        "rejeicao_fiscal_alertada",
        {"intencao_id": intencao.pk, "erro": erro, "tipo_acao": intencao.tipo_acao},
        cliente=cliente,
    )
    logger.warning(
        "rejeicao_fiscal",
        intencao_id=intencao.pk,
        cliente_id=cliente.pk,
        erro=erro,
        tipo_acao=intencao.tipo_acao,
    )

    emails = destinatarios_do_cliente(cliente)
    if not emails:
        logger.error("rejeicao_fiscal_sem_destinatario", intencao_id=intencao.pk)
        return False

    base = getattr(settings, "PAINEL_BASE_URL", "http://localhost:8000")
    corpo = (
        f"A {acao} de NFS-e do cliente {cliente.nome} (CNPJ {cliente.cnpj}) foi recusada.\n\n"
        f"Motivo: {explicacao}\n"
        f"Código: {erro}\n"
        + (f"Detalhe: {detalhe}\n" if detalhe else "")
        + (f"Valor: R$ {valor:.2f}\n" if isinstance(valor, (int, float)) else "")
        + f"\nAbrir no painel: {base}/admin/agente_nf/intencao/{intencao.pk}/change/\n\n"
        "O cliente já foi avisado pelo WhatsApp de que não deu certo, mas quem "
        "consegue corrigir é você."
    )

    try:
        send_mail(
            subject=f"[Magic BI] {acao.capitalize()} recusada — {cliente.nome}",
            message=corpo,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=emails,
            fail_silently=False,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — ver docstring: alerta não derruba operação
        logger.error("alerta_rejeicao_falhou", intencao_id=intencao.pk, erro=str(exc))
        return False
