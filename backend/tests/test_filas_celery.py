"""
Filas do Celery por prioridade (DEC-10).

Pico nos dias 5–10 (DAS, folha) e 15–20. Com fila única, um lote de OCR de 300
documentos entra na frente da confirmação de uma emissão fiscal com prazo legal,
e nenhum aumento de worker desfaz ordem de chegada.

O teste que importa é o terceiro: **toda task registrada precisa de rota
declarada.** Sem ele, a próxima task nasce na fila padrão sem ninguém decidir
nada, e o sintoma aparece meses depois, num dia 8, como lentidão que ninguém
liga à causa. Mesma ideia do teste de tabelas da RLS: a decisão é obrigatória,
o esquecimento é vermelho.
"""
import pytest
from django.conf import settings

from config.celery import app as celery_app

FILAS = {"fiscal", "documento", "conversa"}


def _rotas():
    return settings.CELERY_TASK_ROUTES


def _tasks_do_projeto():
    """Tasks nossas, sem as internas do Celery (`celery.chord`, `celery.map`…)."""
    celery_app.loader.import_default_modules()
    return sorted(nome for nome in celery_app.tasks if nome.startswith("apps."))


def _fila_de(nome_da_task) -> str | None:
    """Resolve a rota do mesmo jeito que o Celery: glob simples, prefixo ou nome
    exato. Reimplementado aqui porque `app.amqp.router` exige broker no ar."""
    for padrao, destino in _rotas().items():
        casou = (
            nome_da_task.startswith(padrao[:-1])
            if padrao.endswith("*")
            else nome_da_task == padrao
        )
        if casou:
            return destino["queue"]
    return None


def test_as_tres_filas_estao_declaradas():
    assert set(d["queue"] for d in _rotas().values()) <= FILAS
    assert settings.CELERY_TASK_DEFAULT_QUEUE in FILAS


def test_existe_task_registrada():
    """Guarda contra o teste seguinte passar por lista vazia."""
    assert _tasks_do_projeto(), "nenhuma task encontrada — o autodiscover quebrou?"


def test_toda_task_tem_fila_decidida():
    sem_rota = [nome for nome in _tasks_do_projeto() if _fila_de(nome) is None]
    assert not sem_rota, (
        f"tasks sem fila declarada: {sem_rota}. Acrescente uma rota em "
        "CELERY_TASK_ROUTES (config/settings.py) decidindo entre fiscal, "
        "documento e conversa — cair na fila padrão é decisão por omissão."
    )


def test_mensagem_de_whatsapp_vai_para_conversa():
    assert _fila_de("apps.channel_whatsapp.tasks.processar_mensagem") == "conversa"
    assert (
        _fila_de("apps.channel_evolution.tasks.processar_mensagem_evolution") == "conversa"
    )


def test_worker_nao_pre_carrega_lote():
    """Prefetch alto desfaz a separação: a mensagem que chegou depois espera o
    lote inteiro acabar, mesmo com worker livre."""
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1


def test_ack_depois_de_executar():
    """Worker que morre no meio de uma emissão devolve a task à fila. Só é
    seguro porque a idempotência por `message_id` impede a duplicata."""
    assert settings.CELERY_TASK_ACKS_LATE is True
