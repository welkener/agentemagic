"""Campo `valor` desnormalizado do payload + backfill do histórico.

Sem o backfill, toda nota emitida antes desta migração entraria no painel com
valor nulo — o faturamento do ano apareceria como R$ 0,00 no dia seguinte ao
deploy, e o radar de teto diria "dentro do teto" para todo mundo. Um indicador
errado que parece certo é pior que indicador nenhum.
"""
from decimal import Decimal, InvalidOperation

from django.db import migrations, models


def preencher_valor(apps, schema_editor):
    Intencao = apps.get_model("agente_nf", "Intencao")

    # `.iterator()` + lotes: a tabela pode ter muitas linhas em produção e
    # carregar tudo em memória numa migração é o caminho conhecido pro deploy
    # morrer com OOM justamente quando há mais dado.
    lote, tamanho = [], 500
    for intencao in Intencao.objects.filter(valor__isnull=True).iterator(chunk_size=tamanho):
        bruto = (intencao.payload or {}).get("valor")
        if bruto is None or bruto == "":
            continue
        try:
            intencao.valor = Decimal(str(bruto)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            continue  # payload com valor não numérico fica nulo, não zerado
        lote.append(intencao)
        if len(lote) >= tamanho:
            Intencao.objects.bulk_update(lote, ["valor"])
            lote = []
    if lote:
        Intencao.objects.bulk_update(lote, ["valor"])


def limpar_valor(apps, schema_editor):
    """Reversão: nada a desfazer — a coluna inteira some no `AddField` reverso."""


class Migration(migrations.Migration):

    dependencies = [
        ('agente_nf', '0004_chave_nfse'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='intencao',
            options={'verbose_name': 'nota fiscal', 'verbose_name_plural': 'notas fiscais'},
        ),
        migrations.AddField(
            model_name='intencao',
            name='valor',
            field=models.DecimalField(blank=True, db_index=True, decimal_places=2, help_text="Derivado de payload['valor'] — não editar à mão.", max_digits=12, null=True),
        ),
        migrations.RunPython(preencher_valor, limpar_valor),
    ]
