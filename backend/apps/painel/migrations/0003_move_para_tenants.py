"""Tira Escritorio/MembroEscritorio do estado do `painel` — as tabelas já foram
renomeadas em `tenants/0001` (só estado aqui, nenhuma operação de banco).

`painel` fica sendo o que o nome diz: apresentação (dashboard e branding).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("painel", "0002_multitenancy"),
        ("tenants", "0001_initial"),
        # As FKs precisam já apontar pro `tenants` antes do model sumir daqui.
        ("clients", "0005_cliente_escritorio_tenants"),
        ("channel_evolution", "0003_escritorio_tenants"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="escritorio", name="escritorio_phone_number_id_unico"
                ),
                migrations.DeleteModel(name="MembroEscritorio"),
                migrations.DeleteModel(name="Escritorio"),
            ],
        ),
    ]
