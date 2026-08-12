"""
A porta da frente do Grimório.

**O defeito que originou este arquivo.** `/entrar/` apontava direto para a
`LoginView` do django-sesame, que só sabe validar um token na query string —
sem token ela responde **403**. O contador que digitasse o endereço do painel,
ou cuja sessão expirasse, batia num 403 sem explicação e sem caminho, e a única
saída era um link por e-mail que depende de um SMTP ainda não configurado. O
produto ficava sem porta, e trabalhar exigia entrar pelo `/admin/login/` —
exatamente o backoffice de programador que o Grimório existe para evitar
(DEC-12).

Achado em produção, no primeiro teste do usuário. Nenhum teste pegava porque
todos entravam por `force_login`, que pula a tela inteira. É o mesmo padrão do
botão "Abrir" que apontava para `http://minio:9000`: o caminho que ninguém
exercita é o que quebra na frente de quem importa.
"""
import pytest
from django.contrib.auth import get_user_model

from apps.tenants.models import MembroEscritorio
from apps.tenants.rls import escopo_irrestrito

SENHA = "senha-de-teste-9Kx"


@pytest.fixture
def contador(db, escritorio):
    with escopo_irrestrito():
        usuario = get_user_model().objects.create_user(
            username="contador.entrada",
            email="contador@example.com",
            password=SENHA,
            is_staff=True,
        )
        MembroEscritorio.objects.create(usuario=usuario, escritorio=escritorio)
    return usuario


@pytest.mark.django_db
class TestAPorta:
    def test_quem_chega_sem_token_ve_um_formulario_e_nao_um_403(self, client):
        """O teste que faltava. Era 403 — sem explicação e sem caminho."""
        resposta = client.get("/entrar/")

        assert resposta.status_code == 200
        corpo = resposta.content.decode()
        assert 'name="senha"' in corpo
        assert "csrfmiddlewaretoken" in corpo

    def test_a_marca_e_do_escritorio_e_nao_esta_escrita_no_template(
        self, client, escritorio
    ):
        """SaaS multi-tenant: uma cor ou nome fixo aqui apareceria no painel de
        todos os outros escritórios."""
        corpo = client.get("/entrar/").content.decode()

        assert escritorio.nome in corpo

    def test_entrar_com_senha_leva_ao_grimorio(self, client, contador):
        resposta = client.post(
            "/entrar/", {"usuario": "contador.entrada", "senha": SENHA}
        )

        assert resposta.status_code == 302
        assert resposta["Location"] == "/grimorio/"
        assert client.get("/grimorio/revisao/").status_code == 200

    def test_o_next_leva_de_volta_para_onde_o_contador_queria_ir(self, client, contador):
        resposta = client.post(
            "/entrar/",
            {
                "usuario": "contador.entrada",
                "senha": SENHA,
                "next": "/grimorio/revisao/",
            },
        )

        assert resposta["Location"] == "/grimorio/revisao/"

    def test_next_para_fora_do_site_e_ignorado(self, client, contador):
        """`?next=` sem validação é redirecionamento aberto: o link passa pelo
        domínio legítimo e joga a pessoa em outro, já autenticada e confiante."""
        resposta = client.post(
            "/entrar/",
            {
                "usuario": "contador.entrada",
                "senha": SENHA,
                "next": "https://site-do-atacante.example.com/",
            },
        )

        assert resposta["Location"] == "/grimorio/"

    def test_senha_errada_nao_diz_qual_metade_falhou(self, client, contador):
        """Dizer "usuário não existe" entrega ao atacante metade da resposta."""
        resposta = client.post(
            "/entrar/", {"usuario": "contador.entrada", "senha": "errada"}, follow=True
        )
        inexistente = client.post(
            "/entrar/", {"usuario": "nao-existe", "senha": "errada"}, follow=True
        )

        assert "não conferem" in resposta.content.decode()
        assert "não conferem" in inexistente.content.decode()

    def test_quem_nao_e_staff_nao_entra_no_grimorio(self, client, db):
        with escopo_irrestrito():
            get_user_model().objects.create_user(
                username="curioso", password=SENHA, is_staff=False
            )

        resposta = client.post(
            "/entrar/", {"usuario": "curioso", "senha": SENHA}, follow=True
        )

        assert "não tem acesso" in resposta.content.decode()
        assert client.get("/grimorio/").status_code in (302, 403)

    def test_quem_ja_entrou_nao_ve_a_tela_de_novo(self, client, contador):
        client.force_login(contador)

        resposta = client.get("/entrar/?next=/grimorio/revisao/")

        assert resposta.status_code == 302
        assert resposta["Location"] == "/grimorio/revisao/"

    def test_a_tela_protegida_manda_para_a_porta_e_nao_para_o_admin(self, client):
        resposta = client.get("/grimorio/revisao/")

        assert resposta.status_code == 302
        assert resposta["Location"].startswith("/entrar/")


@pytest.mark.django_db
class TestForcaBruta:
    """Formulário de senha exposto na internet sem limite é convite."""

    def test_tentativas_demais_travam_a_conta_por_uma_janela(self, client, contador):
        from django.core.cache import cache

        from apps.painel import entrada

        cache.clear()
        for _ in range(entrada.TENTATIVAS_POR_JANELA):
            client.post("/entrar/", {"usuario": "contador.entrada", "senha": "errada"})

        # A senha CERTA também é recusada — é o que distingue um limite de um
        # contador de erros: depois do teto, a porta não abre nem para quem sabe.
        resposta = client.post(
            "/entrar/", {"usuario": "contador.entrada", "senha": SENHA}, follow=True
        )

        assert "Tentativas demais" in resposta.content.decode()
        cache.clear()

    def test_acertar_a_senha_zera_o_contador(self, client, contador):
        from django.core.cache import cache

        cache.clear()
        client.post("/entrar/", {"usuario": "contador.entrada", "senha": "errada"})
        client.post("/entrar/", {"usuario": "contador.entrada", "senha": SENHA})
        client.logout()

        resposta = client.post(
            "/entrar/", {"usuario": "contador.entrada", "senha": SENHA}
        )

        assert resposta["Location"] == "/grimorio/"
        cache.clear()

    def test_cache_fora_do_ar_nao_tranca_ninguem(self, client, contador, monkeypatch):
        """Falha aberta, e é escolha consciente: contador sem acesso ao painel em
        dia de fechamento é dano certo; força bruta sem contador é dano
        possível. O alarme na trilha vale mais que a tranca."""
        from apps.painel import entrada

        def morto(*_a, **_k):
            raise RuntimeError("redis fora do ar")

        monkeypatch.setattr(entrada.cache, "get", morto)
        monkeypatch.setattr(entrada.cache, "add", morto)

        resposta = client.post(
            "/entrar/", {"usuario": "contador.entrada", "senha": SENHA}
        )

        assert resposta["Location"] == "/grimorio/"


@pytest.mark.django_db
class TestMagicLink:
    """O caminho antigo, que não pode ter sido quebrado pelo novo."""

    def test_o_token_do_sesame_continua_entrando(self, client, contador):
        from sesame.utils import get_query_string

        resposta = client.get(f"/entrar/{get_query_string(contador)}")

        assert resposta.status_code == 302
        assert client.get("/grimorio/").status_code == 200

    def test_token_invalido_continua_sendo_recusado(self, client, contador):
        assert client.get("/entrar/?sesame=token-inventado").status_code == 403

    def test_sem_smtp_a_tela_nao_promete_o_que_nao_pode_cumprir(
        self, client, contador, settings
    ):
        """Um botão que responde "enviei" sem enviar é pior que um botão
        explicado: o contador fica esperando um e-mail que não vem."""
        settings.EMAIL_HOST = ""

        corpo = client.get("/entrar/").content.decode()
        assert "não está configurado" in corpo

        resposta = client.post(
            "/entrar/",
            {"acao": "link", "usuario": "contador.entrada"},
            follow=True,
        )
        assert "não está configurado" in resposta.content.decode()

    def test_com_smtp_o_link_sai_e_a_resposta_nao_revela_quem_existe(
        self, client, contador, settings
    ):
        from django.core import mail

        settings.EMAIL_HOST = "smtp.exemplo.com"
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

        pedido = client.post(
            "/entrar/", {"acao": "link", "usuario": "contador.entrada"}, follow=True
        )
        assert len(mail.outbox) == 1
        assert "/entrar/" in mail.outbox[0].body

        # Conta que não existe recebe exatamente a mesma resposta — senão a tela
        # vira um verificador de quem trabalha no escritório.
        inexistente = client.post(
            "/entrar/", {"acao": "link", "usuario": "fantasma"}, follow=True
        )
        assert len(mail.outbox) == 1
        assert pedido.content.decode().count("o link chega em instantes") == 1
        assert "o link chega em instantes" in inexistente.content.decode()
