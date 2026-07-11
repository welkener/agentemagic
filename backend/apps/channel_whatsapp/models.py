"""Idempotência por message_id — mensagem repetida do Meta não processa duas vezes."""
from django.db import models


class MensagemProcessada(models.Model):
    """Marca de mensagem já recebida (o Meta reenvia webhooks em caso de dúvida)."""

    message_id = models.CharField(max_length=128, unique=True)
    telefone = models.CharField(max_length=20, blank=True, default="")
    recebido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "mensagem processada"
        verbose_name_plural = "mensagens processadas"

    def __str__(self):
        return self.message_id
