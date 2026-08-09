"""
DEC-03 — o telefone deixa de ser campo da empresa e passa a ser do usuário.

Três passos, nesta ordem, numa migração só para que não exista estado
intermediário em que o número já saiu de `Cliente` e ainda não chegou em
`Usuario`:

1. cria `Usuario` e `VinculoUsuarioCliente`;
2. copia cada `Cliente.telefone_whatsapp` para um usuário canônico, marcando o
   vínculo como principal;
3. só então derruba o campo e a constraint antigos.

**Esta migração não tem volta automática, e isso é decisão.** A primeira versão
tinha um reverso que devolvia o telefone do vínculo principal a `Cliente`.
Ensaiando contra uma cópia do banco de desenvolvimento, ele quebrou: o Django
recria a coluna antes de rodar qualquer `RunPython`, e uma coluna `NOT NULL` sem
dado — seguida da constraint de unicidade, que colidiria em todas as linhas
vazias — não passa. Não dava para consertar mudando a ordem: em reverso as
operações rodam de trás para frente, e o preenchimento sempre cairia depois do
esquema.

Pior que isso, o reverso *seria* destrutivo mesmo se rodasse: várias pessoas por
empresa não cabem num campo só, então tudo além do principal seria descartado em
silêncio. Então o reverso recusa, com mensagem dizendo o que fazer — restaurar
backup. Falha barulhenta é melhor que `IntegrityError` no meio de um rollback de
madrugada, e muito melhor que perda silenciosa.
"""
from django.db import migrations, models
import django.db.models.deletion

from apps.clients import telefone as telefone_br


def telefone_para_usuario(apps, schema_editor):
    Cliente = apps.get_model("clients", "Cliente")
    Usuario = apps.get_model("clients", "Usuario")
    Vinculo = apps.get_model("clients", "VinculoUsuarioCliente")

    # `iterator` porque uma carteira grande não precisa caber na memória, e a
    # migração roda em servidor que já está apertado de recurso.
    for cliente in Cliente.objects.exclude(telefone_whatsapp="").iterator(chunk_size=500):
        # A canonicalização acontece aqui e não no `save()`: model histórico de
        # migração não carrega os métodos da classe atual.
        canonico = telefone_br.canonico(cliente.telefone_whatsapp)
        if not canonico:
            continue
        usuario, _ = Usuario.objects.get_or_create(
            escritorio_id=cliente.escritorio_id,
            telefone_whatsapp=canonico,
            defaults={"nome": "", "ativo": True},
        )
        Vinculo.objects.get_or_create(
            usuario=usuario,
            cliente=cliente,
            defaults={"papel": "responsavel", "principal": True, "ativo": True},
        )


def recusar_reversao(apps, schema_editor):
    """Roda como PRIMEIRA operação do reverso — é a última na ida.

    Precisa estar no fim da lista justamente por isso: em reverso o Django
    executa de trás para frente, então uma guarda no começo só falaria depois de
    o esquema já ter sido desfeito.
    """
    raise RuntimeError(
        "clients.0009 não tem reversão automática.\n"
        "O telefone deixou de ser campo de Cliente e virou Usuario, que pode "
        "atender várias empresas — devolvê-lo a uma coluna só descartaria todo "
        "vínculo além do principal, sem aviso.\n"
        "Para voltar de verdade: restaure o backup anterior ao deploy."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0003_rls"),
        ("clients", "0008_painel_analitico"),
    ]

    operations = [
        migrations.CreateModel(
            name="Usuario",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "telefone_whatsapp",
                    models.CharField(
                        help_text=(
                            "Número no formato internacional, ex.: 5511999998888. Gravado sempre "
                            "na forma canônica (celular brasileiro COM o nono dígito) — é o que "
                            "faz a unicidade valer de fato."
                        ),
                        max_length=20,
                    ),
                ),
                (
                    "nome",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Como esta pessoa é chamada. Opcional — o número é que identifica.",
                        max_length=120,
                    ),
                ),
                ("ativo", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "escritorio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="usuarios_whatsapp",
                        to="tenants.escritorio",
                    ),
                ),
            ],
            options={
                "verbose_name": "usuário do WhatsApp",
                "verbose_name_plural": "usuários do WhatsApp",
            },
        ),
        migrations.CreateModel(
            name="VinculoUsuarioCliente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "papel",
                    models.CharField(
                        choices=[
                            ("responsavel", "Responsável"),
                            ("socio", "Sócio"),
                            ("financeiro", "Financeiro"),
                            ("rh", "RH / Departamento pessoal"),
                            ("contador", "Contador"),
                        ],
                        default="responsavel",
                        max_length=20,
                    ),
                ),
                (
                    "principal",
                    models.BooleanField(
                        default=False,
                        help_text="Contato principal da empresa — é o número que aparece nas telas.",
                    ),
                ),
                ("ativo", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "cliente",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vinculos",
                        to="clients.cliente",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vinculos",
                        to="clients.usuario",
                    ),
                ),
            ],
            options={
                "verbose_name": "vínculo com empresa",
                "verbose_name_plural": "vínculos com empresas",
            },
        ),
        migrations.AddField(
            model_name="usuario",
            name="clientes",
            field=models.ManyToManyField(
                related_name="usuarios",
                through="clients.VinculoUsuarioCliente",
                to="clients.cliente",
            ),
        ),
        migrations.AddConstraint(
            model_name="usuario",
            constraint=models.UniqueConstraint(
                fields=("escritorio", "telefone_whatsapp"),
                name="usuario_telefone_unico_por_escritorio",
            ),
        ),
        migrations.AddConstraint(
            model_name="vinculousuariocliente",
            constraint=models.UniqueConstraint(
                fields=("usuario", "cliente"), name="vinculo_usuario_cliente_unico"
            ),
        ),
        migrations.RunPython(telefone_para_usuario, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="cliente",
            name="cliente_telefone_unico_por_escritorio",
        ),
        migrations.RemoveField(
            model_name="cliente",
            name="telefone_whatsapp",
        ),
        # Guarda do reverso — ver o cabeçalho. Última na ida para ser a primeira
        # na volta, antes de qualquer coisa ser desfeita.
        migrations.RunPython(migrations.RunPython.noop, recusar_reversao),
    ]
