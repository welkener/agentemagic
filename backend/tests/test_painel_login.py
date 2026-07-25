"""Login do painel (contador) por Magic Link via django-sesame."""
import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import CommandError, call_command


@pytest.fixture
def contador(db):
    return get_user_model().objects.create_user(
        username="contador.rotina", email="contador@rotinacontabil.example.com", is_staff=True
    )


@pytest.mark.django_db
def test_enviar_link_contador_manda_email_com_link_valido(contador):
    call_command("enviar_link_contador", contador.email)

    assert len(mail.outbox) == 1
    corpo = mail.outbox[0].body
    assert "/entrar/?sesame=" in corpo


@pytest.mark.django_db
def test_link_do_email_loga_o_contador(client, contador):
    call_command("enviar_link_contador", contador.username)

    corpo = mail.outbox[0].body
    linha_do_link = next(linha for linha in corpo.splitlines() if "/entrar/?sesame=" in linha)
    caminho = linha_do_link.split("localhost:8000", 1)[1]

    resposta = client.get(caminho)
    assert resposta.status_code == 302  # LoginView loga e redireciona pro LOGIN_REDIRECT_URL

    resposta_seguida = client.get(caminho, follow=True)
    assert resposta_seguida.wsgi_request.user == contador


@pytest.mark.django_db
def test_enviar_link_usuario_inexistente_falha():
    with pytest.raises(CommandError):
        call_command("enviar_link_contador", "ninguem@example.com")


@pytest.mark.django_db
def test_enviar_link_usuario_sem_email_falha(db):
    usuario = get_user_model().objects.create_user(username="sememail")
    with pytest.raises(CommandError):
        call_command("enviar_link_contador", "sememail")
