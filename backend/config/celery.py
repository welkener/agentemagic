"""Configuração do Celery — fila de tarefas assíncronas (broker/result: Redis)."""
import os

from celery import Celery
from celery.signals import task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("magicbi")

# Toda configuração CELERY_* vem do settings do Django.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks.py automaticamente em todos os apps instalados.
app.autodiscover_tasks()


# ---------------------------------------------------------------------------
# Escopo de tenant no worker (DEC-04)
# ---------------------------------------------------------------------------
# É aqui que a maioria das implementações de RLS deixa o buraco: a requisição
# HTTP ganha middleware e o worker não, então a task roda como dono da tabela e
# enxerga a plataforma inteira. Como a task é justamente quem faz o trabalho
# pesado — processar mensagem, emitir nota —, o buraco fica no lugar pior.
#
# A task declara o tenant no argumento `escritorio_id`, que a view já resolve
# pelo número que recebeu a mensagem. Task sem esse argumento roda SEM escopo e
# não enxerga nada — de propósito: é ruído visível em teste, em vez de leitura
# silenciosa do tenant errado.
_VARIAVEL = "escritorio_id"

# Escopo de quem chamou, para devolver no fim. Guardar e restaurar — em vez de
# simplesmente resetar — importa nos dois mundos, por motivos diferentes:
# no worker o anterior é vazio e restaurar equivale a resetar; num teste com
# Celery eager a task roda DENTRO da transação do teste, e um `RESET` cru
# apagaria o escopo que montou o cenário. O sintoma disso é traiçoeiro: as
# consultas seguintes do teste voltam vazias, e parece que a task não gravou
# nada quando na verdade ela gravou e o leitor é que ficou sem escopo.
_ESCOPO_ANTERIOR: dict[str, tuple] = {}


@task_prerun.connect
def _abrir_escopo(task_id=None, task=None, args=None, kwargs=None, **_):
    from apps.tenants import rls

    _ESCOPO_ANTERIOR[task_id] = rls.escopo_atual()

    rls.assumir_papel_restrito()
    # Task nunca roda irrestrita por herança. Manutenção que varre a plataforma
    # inteira (expirar sessões vencidas, por exemplo) declara isso no corpo, com
    # `escopo_irrestrito()` e um motivo escrito ao lado.
    rls.definir_irrestrito(False)
    escritorio_id = (kwargs or {}).get(_VARIAVEL)
    if escritorio_id:
        rls.definir_tenant(escritorio_id)


@task_postrun.connect
def _fechar_escopo(task_id=None, task=None, **_):
    """Devolve o escopo de quem chamou.

    No worker isso é obrigatório por outro motivo: fora de transação o `SET` é
    de sessão e a conexão sobrevive entre tasks — sem restaurar, a próxima task
    herdaria o tenant da anterior, que é exatamente o vazamento que a RLS
    deveria impedir.
    """
    from apps.tenants import rls

    rls.restaurar_escopo(_ESCOPO_ANTERIOR.pop(task_id, None))
