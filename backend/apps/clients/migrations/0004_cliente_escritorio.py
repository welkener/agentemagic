"""Cliente passa a pertencer a um Escritorio (raiz de multi-tenancy).

Backfill: a instalação que já está no ar tem clientes sem dono. Todos vão pro
escritório existente (ou pra um criado na hora, se não houver nenhum) — nunca
some cliente, e o resultado é exatamente o comportamento single-tenant de
antes, só que agora explícito no schema.

`cnpj`/`telefone_whatsapp` deixam de ser únicos globalmente e passam a ser
únicos por escritório (decisão 26/jul/2026 — ver apps/clients/models.py).
"""
import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def vincular_clientes_ao_escritorio(apps, schema_editor):
    Cliente = apps.get_model("clients", "Cliente")
    Escritorio = apps.get_model("painel", "Escritorio")

    if not Cliente.objects.exists():
        return

    escritorio = Escritorio.objects.filter(ativo=True).order_by("id").first()
    if escritorio is None:
        escritorio = Escritorio.objects.order_by("id").first()
    if escritorio is None:
        escritorio = Escritorio.objects.create(
            nome="Magic BI (escritório padrão)",
            slug=slugify("Magic BI escritorio padrao")[:60],
            ativo=True,
        )
    Cliente.objects.filter(escritorio__isnull=True).update(escritorio=escritorio)


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0003_cliente_email_contato_perfil_valor_2fa_acima_de"),
        ("painel", "0002_multitenancy"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="escritorio",
            field=models.ForeignKey(
                help_text="Escritório contábil dono desta carteira.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clientes",
                to="painel.escritorio",
            ),
        ),
        migrations.RunPython(vincular_clientes_ao_escritorio, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="cliente",
            name="escritorio",
            field=models.ForeignKey(
                help_text="Escritório contábil dono desta carteira.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clientes",
                to="painel.escritorio",
            ),
        ),
        # Unicidade global -> por escritório.
        migrations.AlterField(
            model_name="cliente",
            name="cnpj",
            field=models.CharField(max_length=14),
        ),
        migrations.AlterField(
            model_name="cliente",
            name="telefone_whatsapp",
            field=models.CharField(
                help_text="Número no formato internacional, ex.: 5511999998888", max_length=20
            ),
        ),
        migrations.AddConstraint(
            model_name="cliente",
            constraint=models.UniqueConstraint(
                fields=("escritorio", "cnpj"), name="cliente_cnpj_unico_por_escritorio"
            ),
        ),
        migrations.AddConstraint(
            model_name="cliente",
            constraint=models.UniqueConstraint(
                fields=("escritorio", "telefone_whatsapp"),
                name="cliente_telefone_unico_por_escritorio",
            ),
        ),
    ]
