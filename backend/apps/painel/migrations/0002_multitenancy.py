"""Escritorio vira raiz de tenant: slug, canal WhatsApp próprio e membros.

O `slug` entra em três passos (adiciona nulo → preenche → torna obrigatório e
único) porque a instalação que já está no ar tem um `Escritorio` gravado — um
`AddField` unique direto quebraria nele.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils.text import slugify

import apps.credentials.crypto


def preencher_slugs(apps, schema_editor):
    Escritorio = apps.get_model("painel", "Escritorio")
    usados = set()
    for escritorio in Escritorio.objects.all().order_by("id"):
        base = slugify(escritorio.nome)[:50] or f"escritorio-{escritorio.pk}"
        slug, n = base, 2
        while slug in usados:
            slug = f"{base}-{n}"
            n += 1
        usados.add(slug)
        escritorio.slug = slug
        escritorio.save(update_fields=["slug"])


def ativar_escritorios_existentes(apps, schema_editor):
    """`ativo` mudou de sentido: era "é este que aparece no branding", agora é
    "escritório habilitado". Quem já existia continua habilitado."""
    Escritorio = apps.get_model("painel", "Escritorio")
    Escritorio.objects.update(ativo=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("painel", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="escritorio",
            name="slug",
            # db_index=False de propósito: SlugField indexa por padrão, e o
            # AlterField abaixo (unique=True) recria o índice `_like` — sem
            # isto o Postgres reclama de DuplicateTable.
            field=models.SlugField(max_length=60, null=True, db_index=False),
        ),
        migrations.RunPython(preencher_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="escritorio",
            name="slug",
            field=models.SlugField(
                help_text="Identificador estável (aparece em log/auditoria, não muda quando o nome muda).",
                max_length=60,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="escritorio",
            name="ativo",
            field=models.BooleanField(
                default=True,
                help_text="Escritório habilitado. Inativo não recebe mensagem nem aparece no roteamento.",
            ),
        ),
        migrations.RunPython(ativar_escritorios_existentes, migrations.RunPython.noop),
        migrations.AddField(
            model_name="escritorio",
            name="whatsapp_phone_number_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "ID do número no WhatsApp Cloud API (Meta) deste escritório. "
                    "É por ele que o webhook descobre de quem é a mensagem recebida."
                ),
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="escritorio",
            name="whatsapp_token_cifrado",
            field=apps.credentials.crypto.CampoTextoCifrado(
                blank=True,
                help_text="Token permanente da Cloud API do escritório, cifrado em repouso.",
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="escritorio",
            constraint=models.UniqueConstraint(
                condition=models.Q(("whatsapp_phone_number_id", ""), _negated=True),
                fields=("whatsapp_phone_number_id",),
                name="escritorio_phone_number_id_unico",
            ),
        ),
        migrations.CreateModel(
            name="MembroEscritorio",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "escritorio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="membros",
                        to="painel.escritorio",
                    ),
                ),
                (
                    "usuario",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="membro_escritorio",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "membro de escritório",
                "verbose_name_plural": "membros de escritório",
            },
        ),
    ]
