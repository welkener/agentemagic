"""
Login do painel (contador/Grimório) por Magic Link — django-sesame.

Caso de uso diferente do vínculo `wa_id↔CNPJ` do resto do app: aqui é login
de `auth.User` (contador da Rotina), não de `Cliente`. Sem tela de
"esqueci minha senha" self-service ainda (onboarding do painel continua
manual, como o resto do provisionamento no MVP) — quem administra o sistema
roda este comando para mandar o link.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from sesame.utils import get_query_string


class Command(BaseCommand):
    help = "Gera e envia por e-mail o Magic Link de login do painel para um usuário (contador)."

    def add_arguments(self, parser):
        parser.add_argument("username_ou_email", help="username ou e-mail do usuário do painel")

    def handle(self, *args, **options):
        User = get_user_model()
        identificador = options["username_ou_email"]
        busca = {"email": identificador} if "@" in identificador else {"username": identificador}
        try:
            usuario = User.objects.get(**busca)
        except User.DoesNotExist as exc:
            raise CommandError(f"Usuário '{identificador}' não encontrado.") from exc

        if not usuario.email:
            raise CommandError(f"Usuário '{usuario}' não tem e-mail cadastrado — não dá pra mandar o link.")

        base = getattr(settings, "PAINEL_BASE_URL", "http://localhost:8000")
        link = f"{base}{reverse('painel_login')}{get_query_string(usuario)}"
        minutos = settings.SESAME_MAX_AGE // 60

        send_mail(
            subject="Magic BI — acesso ao painel",
            message=(
                f"Olá, {usuario.get_username()}!\n\n"
                f"Acesse o painel com o link abaixo (expira em {minutos} minutos):\n{link}\n\n"
                "Se você não pediu isso, ignore este e-mail."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[usuario.email],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Link enviado para {usuario.email}."))
