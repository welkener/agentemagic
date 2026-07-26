"""Repontar a FK de `painel.Escritorio` para `tenants.Escritorio`.

Só estado: a coluna `escritorio_id` não muda e, no Postgres, a constraint segue
a tabela renomeada por OID — não há nada pra fazer no banco.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0004_cliente_escritorio"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="cliente",
                    name="escritorio",
                    field=models.ForeignKey(
                        help_text="Escritório contábil dono desta carteira.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="clientes",
                        to="tenants.escritorio",
                    ),
                ),
            ],
        ),
    ]
