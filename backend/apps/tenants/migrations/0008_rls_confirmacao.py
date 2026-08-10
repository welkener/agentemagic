"""Liga a RLS na tabela de confirmações (Sprint 3).

Lista congelada, como nas anteriores — ver `0006_rls_sprint2` para o porquê.
"""
from django.db import migrations

from apps.tenants import rls

TABELAS = ["agente_nf_confirmacao"]


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0007_rls_rotina"),
        ("agente_nf", "0006_confirmacao"),
    ]

    operations = [
        migrations.RunSQL(
            sql="\n".join(
                linha for tabela in TABELAS for linha in rls.sql_para_tabela(tabela)
            ),
            reverse_sql=rls.sql_reverso(TABELAS),
        ),
    ]
