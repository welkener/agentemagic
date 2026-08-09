"""
Estende a RLS às tabelas do nível `usuario` (DEC-03).

Tabela nova nasce **sem policy**: o `ENABLE ROW LEVEL SECURITY` é por tabela, e
o `ALTER DEFAULT PRIVILEGES` da 0003 só cuida do GRANT. Sem esta migração o
`clients_usuario` seria a única tabela da carteira legível de ponta a ponta por
qualquer tenant — e nada acusaria, porque o escopo de aplicação continuaria
filtrando certo. `tests/test_rls.py` falha se uma tabela declarada em
`rls.TABELAS` não tiver policy no banco; é ele que transforma esse esquecimento
em teste vermelho em vez de vazamento.
"""
from django.db import migrations

from apps.tenants import rls

TABELAS_NOVAS = (
    "clients_usuario",
    "clients_vinculousuariocliente",
    "security_empresaemfoco",
)


def _ligar() -> str:
    linhas = []
    for tabela in TABELAS_NOVAS:
        linhas.extend(rls.sql_para_tabela(tabela))
    return "\n".join(linhas)


def _desligar() -> str:
    linhas = []
    for tabela in TABELAS_NOVAS:
        linhas.append(f"DROP POLICY IF EXISTS isolamento_tenant ON {tabela};")
        linhas.append(f"ALTER TABLE {tabela} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(linhas)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0003_rls"),
        ("clients", "0009_nivel_usuario"),
        ("security", "0002_empresa_em_foco"),
    ]

    operations = [
        migrations.RunSQL(sql=_ligar(), reverse_sql=_desligar()),
    ]
