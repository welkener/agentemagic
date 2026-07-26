"""Escritorio/MembroEscritorio saem de `apps.painel` e viram `apps.tenants`.

Movimento de model entre apps com dado gravado: o **estado** do Django é
recriado aqui (`state_operations`) e removido do `painel` (0003), enquanto o
**banco** só renomeia as tabelas. Nenhuma linha é copiada nem recriada — se
isto fosse um CreateModel normal, o `painel_escritorio` existente seria
ignorado e a carteira de clientes ficaria apontando pra uma tabela órfã.

As FKs (`clients.Cliente.escritorio`, `channel_evolution`) não precisam de
operação de banco: no Postgres a constraint referencia a tabela por OID, então
ela acompanha o rename sozinha. Do lado do Django é só estado — ver
`clients/0005` e `channel_evolution/0003`.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.credentials.crypto


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("painel", "0002_multitenancy"),
        # O rename precisa ser a ÚLTIMA coisa a tocar `painel_escritorio`:
        # `clients/0004` faz backfill lendo essa tabela pelo nome antigo e
        # `channel_evolution/0002` cria FK pra ela. Sem estas duas âncoras o
        # grafo permite o rename primeiro e as duas quebram em banco novo.
        ("clients", "0004_cliente_escritorio"),
        ("channel_evolution", "0002_escritorio"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        "ALTER TABLE painel_escritorio RENAME TO tenants_escritorio;",
                        "ALTER TABLE painel_membroescritorio RENAME TO tenants_membroescritorio;",
                    ],
                    reverse_sql=[
                        "ALTER TABLE tenants_membroescritorio RENAME TO painel_membroescritorio;",
                        "ALTER TABLE tenants_escritorio RENAME TO painel_escritorio;",
                    ],
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="Escritorio",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("nome", models.CharField(help_text='Ex.: "Rotina Contábil".', max_length=120)),
                        (
                            "slug",
                            models.SlugField(
                                help_text="Identificador estável (aparece em log/auditoria, não muda quando o nome muda).",
                                max_length=60,
                                unique=True,
                            ),
                        ),
                        ("logo", models.ImageField(blank=True, null=True, upload_to="logos/")),
                        ("cor_primaria", models.CharField(default="#1a1a2e", help_text="Hex, ex.: #000066. Usada no cabeçalho do painel.", max_length=7)),
                        ("cor_acento", models.CharField(default="#5B67C9", help_text="Hex — links e destaques do painel.", max_length=7)),
                        (
                            "ativo",
                            models.BooleanField(
                                default=True,
                                help_text="Escritório habilitado. Inativo não recebe mensagem nem aparece no roteamento.",
                            ),
                        ),
                        (
                            "whatsapp_phone_number_id",
                            models.CharField(
                                blank=True,
                                default="",
                                help_text=(
                                    "ID do número no WhatsApp Cloud API (Meta) deste escritório. "
                                    "É por ele que o webhook descobre de quem é a mensagem recebida."
                                ),
                                max_length=32,
                            ),
                        ),
                        (
                            "whatsapp_token_cifrado",
                            apps.credentials.crypto.CampoTextoCifrado(
                                blank=True,
                                help_text="Token permanente da Cloud API do escritório, cifrado em repouso.",
                                null=True,
                            ),
                        ),
                        ("atualizado_em", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "verbose_name": "escritório (tenant)",
                        "verbose_name_plural": "escritórios (tenants)",
                    },
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
                                to="tenants.escritorio",
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
                migrations.AddConstraint(
                    model_name="escritorio",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("whatsapp_phone_number_id", ""), _negated=True),
                        fields=("whatsapp_phone_number_id",),
                        name="escritorio_phone_number_id_unico",
                    ),
                ),
            ],
        ),
    ]
