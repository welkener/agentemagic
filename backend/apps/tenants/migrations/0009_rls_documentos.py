"""Liga a RLS na tabela de documentos (Sprint 4).

Lista congelada, como nas anteriores — ver `0006_rls_sprint2` para o porquê.
"""
from django.db import migrations

from apps.tenants import rls

TABELAS = ["documentos_documento"]


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0008_rls_confirmacao"),
        ("documentos", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="\n".join(
                linha for tabela in TABELAS for linha in rls.sql_para_tabela(tabela)
            ),
            reverse_sql=rls.sql_reverso(TABELAS),
        ),
    ]
