"""Tarefa Celery periódica que expira sessões vencidas.

`sessao_ativa()` já expira sob demanda (lazy, na 1ª mensagem depois do
vencimento) — esta task é defesa em profundidade para sessões que nunca mais
recebem mensagem depois de vencer. Agendamento (Celery beat) entra na Onda 4
(deploy — `docs/magicbi-ondas-desenvolvimento.md`); por ora, task pronta para
ser chamada manualmente ou por um cron externo.
"""
import structlog
from celery import shared_task

from apps.tenants.rls import escopo_irrestrito

from .services import expirar_sessoes_vencidas

logger = structlog.get_logger(__name__)


@shared_task
def expirar_sessoes_vencidas_task() -> int:
    # Manutenção de plataforma: varre a base inteira, não a de um escritório —
    # não existe tenant em nome de quem ela rodaria. É uma das três exceções
    # legítimas ao escopo (ver `apps/tenants/rls.py::escopo_irrestrito`), e
    # precisa estar escrita aqui porque `task_prerun` recusa o irrestrito por
    # herança de propósito: task que enxerga tudo é decisão, nunca descuido.
    with escopo_irrestrito():
        total = expirar_sessoes_vencidas()
    if total:
        logger.info("sessoes_whatsapp_expiradas_job", total=total)
    return total
