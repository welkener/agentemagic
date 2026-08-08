"""
Liga a Row Level Security em toda tabela de domínio.

Depende da última migração de cada app dono de tabela na policy: sem isso o
Django pode rodar esta antes da tabela existir, e o `ALTER TABLE` falha no
deploy — não no desenvolvimento, onde tudo já está criado.

O SQL vem de `apps/tenants/rls.py` em vez de estar escrito aqui: o mapa de
tabelas é consultado também pelos testes, e duas cópias divergiriam na primeira
tabela nova.
"""
from django.db import migrations

from apps.tenants import rls


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_membroescritorio_responsavel"),
        ("clients", "0008_painel_analitico"),
        ("credentials", "0003_credencial_certificado_cnpj_and_more"),
        ("security", "0001_initial"),
        ("audit", "0002_chave_conteudo"),
        ("fiscal", "0001_initial"),
        ("agente_nf", "0005_painel_analitico"),
        ("core", "0001_initial"),
        ("channel_evolution", "0003_escritorio_tenants"),
        ("channel_whatsapp", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=rls.sql_completo(), reverse_sql=rls.sql_reverso()),
    ]
