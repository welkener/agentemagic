"""
Liga a RLS nas quatro tabelas da rotina contábil (Sprint 3).

Mesma disciplina da `0004` e da `0006`: a lista é **congelada aqui**, não lida
do mapa vivo de `rls.TABELAS`. Renderizar o mapa atual faz uma migração antiga
citar tabela que ainda não existe no momento em que ela roda — defeito que só
aparece em banco limpo, ou seja, no servidor novo e nunca em desenvolvimento.
"""
from django.db import migrations

from apps.tenants import rls

TABELAS_DO_SPRINT_3 = [
    "rotina_guia",
    "rotina_obrigacao",
    "rotina_certidao",
    "rotina_folha",
]


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0006_rls_sprint2"),
        ("rotina", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="\n".join(
                linha
                for tabela in TABELAS_DO_SPRINT_3
                for linha in rls.sql_para_tabela(tabela)
            ),
            reverse_sql=rls.sql_reverso(TABELAS_DO_SPRINT_3),
        ),
    ]
