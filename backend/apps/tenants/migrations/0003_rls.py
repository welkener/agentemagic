"""
Liga a Row Level Security em toda tabela de domínio.

Depende da última migração de cada app dono de tabela na policy: sem isso o
Django pode rodar esta antes da tabela existir, e o `ALTER TABLE` falha no
deploy — não no desenvolvimento, onde tudo já está criado.

O SQL vem de `apps/tenants/rls.py` em vez de estar escrito aqui: o mapa de
tabelas é consultado também pelos testes, e duas cópias do *predicado*
divergiriam na primeira tabela nova.

A **lista** de tabelas, porém, é congelada abaixo — e essa distinção custou um
`migrate` quebrado. Enquanto esta era a única migração de RLS, renderizar o mapa
vivo parecia equivalente; assim que o DEC-03 acrescentou `clients_usuario` ao
mapa, o SQL desta migração passou a citar uma tabela que só nasce três
migrações adiante. Num banco já criado nada acusou. Num banco limpo, o deploy
parou. Cada migração descreve o dia dela; quem cobre o presente é
`tests/test_rls.py`, que exige policy no banco para toda tabela declarada.
"""
from django.db import migrations

from apps.tenants import rls

TABELAS_EM_08_AGO_2026 = (
    "tenants_escritorio",
    "tenants_membroescritorio",
    "clients_cliente",
    "channel_evolution_configuracaoevolution",
    "clients_perfil",
    "credentials_credencial",
    "security_sessaowhatsapp",
    "security_tokenmagiclink",
    "security_codigo2fa",
    "fiscal_seriedps",
    "agente_nf_intencao",
    "audit_chaveconteudo",
    "audit_auditoria",
)


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
        migrations.RunSQL(
            sql=rls.sql_completo(TABELAS_EM_08_AGO_2026),
            reverse_sql=rls.sql_reverso(TABELAS_EM_08_AGO_2026),
        ),
    ]
