"""
Numeração sequencial da DPS (`nDPS`), por prestador + série.

Não é contador cosmético: a Sefin recusa número repetido e **pula** de número
vira pergunta do fisco. Por isso o próximo número sai de uma linha travada no
banco (`select_for_update`), e não de um `count()` — dois webhooks do WhatsApp
chegando junto (o Celery roda concorrente) gerariam o mesmo número com
`count()`, e a segunda nota seria recusada.

O número é consumido mesmo que a emissão falhe depois. Isso é de propósito:
reaproveitar número de tentativa rejeitada arrisca duplicar sequência se a
rejeição tiver sido só de transporte e a Sefin já tiver registrado a DPS.
"""
from django.db import models, transaction


class SerieDps(models.Model):
    """Último `nDPS` usado por prestador+série."""

    cliente = models.ForeignKey(
        "clients.Cliente", on_delete=models.CASCADE, related_name="series_dps"
    )
    serie = models.CharField(max_length=5, default="1")
    ultimo_numero = models.PositiveBigIntegerField(default=0)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "série da DPS"
        verbose_name_plural = "séries da DPS"
        constraints = [
            models.UniqueConstraint(fields=["cliente", "serie"], name="serie_dps_unica_por_cliente")
        ]

    def __str__(self):
        return f"{self.cliente} — série {self.serie} (último nº {self.ultimo_numero})"


@transaction.atomic
def proximo_numero(cliente) -> int:
    """Reserva e devolve o próximo `nDPS`. Seguro sob concorrência."""
    serie = (cliente.serie_dps or "1").strip()
    registro, _ = SerieDps.objects.get_or_create(cliente=cliente, serie=serie)
    # Relê com trava: sem isto, duas tasks Celery simultâneas leem o mesmo
    # `ultimo_numero` e emitem duas DPS com o mesmo número.
    registro = SerieDps.objects.select_for_update().get(pk=registro.pk)
    registro.ultimo_numero += 1
    registro.save(update_fields=["ultimo_numero", "atualizado_em"])
    return registro.ultimo_numero
