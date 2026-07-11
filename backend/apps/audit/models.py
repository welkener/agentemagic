"""
Auditoria append-only com hash encadeado (inegociável mesmo no MVP).

Cada linha guarda o hash da linha anterior + o hash do próprio conteúdo
(sha256). Qualquer alteração retroativa quebra a cadeia — o que dá
verificabilidade à trilha exigida em sistema fiscal.
"""
import hashlib
import json

from django.db import models

from apps.clients.models import Cliente


class ErroAuditoriaImutavel(Exception):
    """Levantado ao tentar alterar ou apagar uma linha de auditoria."""


def calcular_hash(evento: str, dados: dict, cliente_id, hash_anterior: str) -> str:
    """Hash determinístico do registro: sha256(hash anterior + payload canônico)."""
    payload = json.dumps(
        {"evento": evento, "dados": dados, "cliente_id": cliente_id},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256((hash_anterior + payload).encode("utf-8")).hexdigest()


class Auditoria(models.Model):
    """Registro imutável de evento. Só INSERT — update/delete são proibidos."""

    criado_em = models.DateTimeField(auto_now_add=True)
    cliente = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.PROTECT,
        related_name="auditorias",
    )
    evento = models.CharField(max_length=64)
    dados = models.JSONField(default=dict)
    hash_anterior = models.CharField(max_length=64, blank=True, default="")
    hash_atual = models.CharField(max_length=64)

    class Meta:
        verbose_name = "auditoria"
        verbose_name_plural = "auditorias"
        ordering = ["id"]

    def save(self, *args, **kwargs):
        # Append-only: se a linha já tem pk, é uma tentativa de UPDATE.
        if self.pk is not None:
            raise ErroAuditoriaImutavel(
                "Auditoria é append-only: registros não podem ser alterados."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ErroAuditoriaImutavel(
            "Auditoria é append-only: registros não podem ser apagados."
        )

    def __str__(self):
        return f"[{self.id}] {self.evento}"
