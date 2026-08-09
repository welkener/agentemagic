"""
Liga a RLS nas duas tabelas que o Sprint 2 criou.

Mesma disciplina da `0004`: a lista é **congelada aqui**, não lida do mapa vivo
de `rls.TABELAS`. Renderizar o mapa atual faria esta migração citar, num deploy
futuro, tabelas que ainda não existem no momento em que ela roda — foi
exatamente o defeito que a `0003` teve e que só aparecia em banco limpo, ou
seja, no servidor novo e nunca em desenvolvimento.

Quem garante que nenhuma tabela ficou de fora do conjunto das migrações é
`tests/test_rls.py`, comparando o mapa vivo com as policies que o banco tem.
"""
from django.db import migrations

from apps.tenants import rls

TABELAS_DO_SPRINT_2 = [
    # Chamados e pedidos de atendimento abertos pela conversa.
    "atendimento_solicitacao",
    # Consumo de IA por tenant — a base de cálculo do teto de gasto e da fatura.
    "observabilidade_consumollm",
]


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0005_escritorio_limite_gasto_mensal_brl"),
        ("atendimento", "0001_initial"),
        ("observabilidade", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="\n".join(
                linha
                for tabela in TABELAS_DO_SPRINT_2
                for linha in rls.sql_para_tabela(tabela)
            ),
            reverse_sql=rls.sql_reverso(TABELAS_DO_SPRINT_2),
        ),
    ]
