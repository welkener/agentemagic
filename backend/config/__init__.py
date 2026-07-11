"""Projeto Magic BI — garante que o app Celery seja carregado com o Django."""
from .celery import app as celery_app

__all__ = ("celery_app",)
