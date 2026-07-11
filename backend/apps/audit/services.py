"""Serviços de auditoria: registro encadeado e verificação da cadeia."""
from django.db import transaction

from .models import Auditoria, calcular_hash


def registrar(evento: str, dados: dict, cliente=None) -> Auditoria:
    """Insere um registro de auditoria encadeado ao anterior.

    O hash do último registro vira o `hash_anterior` do novo — formando uma
    cadeia verificável. A transação evita corrida entre dois registros.
    """
    with transaction.atomic():
        anterior = (
            Auditoria.objects.select_for_update(of=("self",))
            .order_by("-id")
            .first()
            if _suporta_lock()
            else Auditoria.objects.order_by("-id").first()
        )
        hash_anterior = anterior.hash_atual if anterior else ""
        registro = Auditoria(
            cliente=cliente,
            evento=evento,
            dados=dados,
            hash_anterior=hash_anterior,
            hash_atual=calcular_hash(
                evento, dados, cliente.id if cliente else None, hash_anterior
            ),
        )
        registro.save()
        return registro


def verificar_cadeia() -> bool:
    """Recalcula os hashes de toda a trilha; False se a cadeia foi violada."""
    hash_anterior = ""
    for registro in Auditoria.objects.order_by("id"):
        if registro.hash_anterior != hash_anterior:
            return False
        esperado = calcular_hash(
            registro.evento,
            registro.dados,
            registro.cliente_id,
            hash_anterior,
        )
        if registro.hash_atual != esperado:
            return False
        hash_anterior = registro.hash_atual
    return True


def _suporta_lock() -> bool:
    """sqlite não suporta SELECT ... FOR UPDATE; Postgres sim."""
    from django.db import connection

    return connection.features.has_select_for_update
