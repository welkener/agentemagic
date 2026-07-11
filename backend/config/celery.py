"""Configuração do Celery — fila de tarefas assíncronas (broker/result: Redis)."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("magicbi")

# Toda configuração CELERY_* vem do settings do Django.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks.py automaticamente em todos os apps instalados.
app.autodiscover_tasks()
