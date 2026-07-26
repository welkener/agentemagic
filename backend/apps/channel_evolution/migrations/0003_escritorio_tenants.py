"""Repontar a FK de `painel.Escritorio` para `tenants.Escritorio` (só estado —
ver apps/tenants/migrations/0001_initial.py)."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channel_evolution", "0002_escritorio"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="configuracaoevolution",
                    name="escritorio",
                    field=models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Escritório dono desta instância. Em branco = configuração global de "
                            "bootstrap (só faz sentido em instalação de um tenant só)."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configuracoes_evolution",
                        to="tenants.escritorio",
                    ),
                ),
            ],
        ),
    ]
