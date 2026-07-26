"""
Provisiona um escritório parceiro novo (tenant) e o primeiro contador dele.

É o "vender pro segundo escritório" em um comando: cria o `Escritorio`, o
`auth.User` do contador e o `MembroEscritorio` que amarra os dois. Sem o
vínculo o contador loga e não vê nada (padrão seguro — ver
`apps/tenants/escopo.py`), então os três passos andam juntos de propósito.

O contador nasce `is_staff=True` e **sem senha** — o acesso é por Magic Link
(`enviar_link_contador`), igual ao resto do projeto. Nunca `is_superuser`:
superuser é a equipe Magic BI e enxerga a plataforma inteira.

Exemplo:
    python manage.py provisionar_escritorio "Contabilidade Aurora" \\
        --contador aurora.chefe --email chefe@aurora.com.br \\
        --phone-number-id 123456789012345
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.tenants.models import Escritorio, MembroEscritorio, grupo_do_escritorio
from apps.tenants.permissoes import aplicar_permissoes_base


class Command(BaseCommand):
    help = "Cria um escritório parceiro (tenant) e o primeiro contador dele."

    def add_arguments(self, parser):
        parser.add_argument("nome", help='Nome do escritório, ex.: "Contabilidade Aurora".')
        parser.add_argument("--slug", default="", help="Identificador estável (padrão: derivado do nome).")
        parser.add_argument("--contador", required=True, help="username do primeiro contador.")
        parser.add_argument("--email", required=True, help="E-mail do contador (canal do Magic Link).")
        parser.add_argument(
            "--phone-number-id",
            default="",
            dest="phone_number_id",
            help="ID do número no WhatsApp Cloud API deste escritório (pode entrar depois pelo admin).",
        )
        parser.add_argument(
            "--sem-permissoes",
            action="store_true",
            dest="sem_permissoes",
            help="Cria o grupo vazio, pra montar as permissões do zero no admin.",
        )

    @transaction.atomic
    def handle(self, *args, **opcoes):
        User = get_user_model()
        nome = opcoes["nome"]
        slug = opcoes["slug"] or slugify(nome)[:60]

        if Escritorio.objects.filter(slug=slug).exists():
            raise CommandError(f"Já existe escritório com slug '{slug}'.")

        phone_number_id = opcoes["phone_number_id"]
        if phone_number_id and Escritorio.objects.filter(
            whatsapp_phone_number_id=phone_number_id
        ).exists():
            raise CommandError(
                f"O número '{phone_number_id}' já é de outro escritório — "
                "dois tenants no mesmo número tornariam o webhook ambíguo."
            )

        username = opcoes["contador"]
        if User.objects.filter(username=username).exists():
            raise CommandError(f"Usuário '{username}' já existe.")

        escritorio = Escritorio.objects.create(
            nome=nome, slug=slug, ativo=True, whatsapp_phone_number_id=phone_number_id
        )
        contador = User.objects.create_user(
            username=username, email=opcoes["email"], is_staff=True
        )
        contador.set_unusable_password()  # acesso é por Magic Link, não senha
        contador.save(update_fields=["password"])

        # O grupo carrega as PERMISSÕES (o que se faz); o MembroEscritorio
        # carrega o ESCOPO (sobre quais linhas). Os dois são necessários.
        grupo = grupo_do_escritorio(escritorio)
        if not opcoes["sem_permissoes"]:
            aplicar_permissoes_base(grupo)
        contador.groups.add(grupo)

        # O primeiro contador é responsável — senão o escritório nasce sem
        # ninguém que possa cadastrar os colegas.
        MembroEscritorio.objects.create(
            usuario=contador, escritorio=escritorio, responsavel=True
        )

        self.stdout.write(self.style.SUCCESS(f"Escritório '{nome}' criado (slug: {slug})."))
        self.stdout.write(
            f"Contador '{username}' vinculado como RESPONSÁVEL — vê só a carteira "
            f"deste escritório e pode cadastrar os colegas."
        )
        self.stdout.write(
            f"Permissões no grupo '{grupo.name}' "
            f"({grupo.permissions.count()} permissões) — ajuste lá, não no código."
        )
        if not phone_number_id:
            self.stdout.write(
                self.style.WARNING(
                    "Sem número do WhatsApp: cadastre o phone_number_id e o token no admin "
                    "(Escritórios) antes de plugar o canal — enquanto isso, com mais de um "
                    "escritório ativo, as mensagens deste tenant não são roteadas."
                )
            )
        self.stdout.write(f"Próximo passo: python manage.py enviar_link_contador {username}")
