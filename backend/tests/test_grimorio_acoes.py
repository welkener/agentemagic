"""
As ações de escrita do Grimório.

Até 10/ago/2026 esta área era somente leitura, e todo botão levava para o
`/admin/`. Abrir escrita aqui é a mudança mais arriscada que o Grimório sofreu:
a proteção que valia por construção ("não escreve, logo não estraga") deixou de
existir, e passa a depender de regra explícita.

São três regras, e cada teste abaixo existe para uma delas:

1. **Só POST muda estado.** Link que altera dado é acionado pelo pré-carregador
   do navegador, por antivírus corporativo e por qualquer coisa que resolva
   visitar a URL — o chamado fecharia sozinho.
2. **O objeto vem do escopo do contador, nunca do id da URL.** É a mesma regra
   da ficha da empresa, e aqui ela vale mais: ler dado do vizinho é vazamento,
   escrever no dado do vizinho é estrago.
3. **O ato entra na trilha com quem clicou.** Num escritório com quatro
   contadores, "quem fechou este chamado" é a primeira pergunta quando o cliente
   diz que ninguém respondeu.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.atendimento.models import Solicitacao
from apps.audit.models import Auditoria
from apps.clients.models import Cliente, Perfil
from apps.security.models import SessaoWhatsapp
from apps.tenants import rls
from apps.tenants.models import Escritorio, MembroEscritorio


@pytest.fixture
def dois_escritorios_com_chamado(db):
    """Dois escritórios, um chamado aberto em cada — o cenário que revela estrago."""
    with rls.escopo_irrestrito():
        criados = {}
        for slug, nome, cnpj in (
            ("alfa", "Contábil Alfa", "11111111000111"),
            ("beta", "Contábil Beta", "22222222000122"),
        ):
            escritorio = Escritorio.objects.create(nome=nome, slug=slug, ativo=True)
            cliente = Cliente.objects.create(
                escritorio=escritorio,
                cnpj=cnpj,
                nome=f"Empresa da {nome}",
                email_contato="e@example.com",
                cnae_padrao="6201-5/01",
                ativo=True,
            )
            Perfil.objects.create(cliente=cliente, tier_maximo=1)
            SessaoWhatsapp.objects.create(
                cliente=cliente,
                wa_id="5511900000000",
                status=SessaoWhatsapp.Status.ATIVA,
                validado_em=timezone.now(),
                expira_em=timezone.now() + timezone.timedelta(days=7),
            )
            chamado = Solicitacao.objects.create(
                cliente=cliente,
                assunto=f"chamado da {nome}",
                descricao="preciso de ajuda",
                protocolo=f"CH-TESTE-{slug.upper()}",
            )
            criados[slug] = (escritorio, cliente, chamado)
    return criados


def _contador(escritorio, username):
    with rls.escopo_irrestrito():
        usuario = get_user_model().objects.create_user(
            username=username, email=f"{username}@example.com", is_staff=True
        )
        usuario.user_permissions.set(Permission.objects.all())
        MembroEscritorio.objects.create(usuario=usuario, escritorio=escritorio)
    return usuario


def url(chamado):
    return f"/grimorio/chamados/{chamado.pk}/resolver/"


@pytest.mark.django_db
class TestResolverChamado:
    def test_o_contador_fecha_sem_sair_do_grimorio(self, client, dois_escritorios_com_chamado):
        escritorio, _, chamado = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        resposta = client.post(url(chamado), {"next": "/grimorio/"})

        assert resposta.status_code == 302
        assert resposta["Location"] == "/grimorio/"
        chamado.refresh_from_db()
        assert chamado.estado == Solicitacao.Estado.RESOLVIDA
        assert chamado.resolvido_em is not None

    def test_registra_quem_fechou(self, client, dois_escritorios_com_chamado):
        """No modelo e na trilha. O campo serve ao dia a dia; a trilha, à
        auditoria — e as duas perguntas são feitas por gente diferente."""
        escritorio, cliente, chamado = dois_escritorios_com_chamado["alfa"]
        contador = _contador(escritorio, "contador.alfa")
        client.force_login(contador)

        client.post(url(chamado))

        chamado.refresh_from_db()
        assert chamado.resolvido_por_id == contador.pk

        with rls.escopo_irrestrito():
            evento = Auditoria.objects.get(evento="solicitacao_resolvida")
        assert evento.dados["por"] == "contador.alfa"
        assert evento.dados["origem"] == "grimorio"
        assert evento.cliente_id == cliente.pk

    def test_get_nao_muda_nada(self, client, dois_escritorios_com_chamado):
        """Link que altera dado é acionado pelo pré-carregador do navegador e
        por antivírus corporativo — o chamado fecharia sozinho."""
        escritorio, _, chamado = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        resposta = client.get(url(chamado))

        assert resposta.status_code == 405
        chamado.refresh_from_db()
        assert chamado.estado == Solicitacao.Estado.ABERTA

    def test_nao_fecha_chamado_de_outro_escritorio(
        self, client, dois_escritorios_com_chamado
    ):
        """Ler dado do vizinho é vazamento; escrever no dado dele é estrago."""
        escritorio_alfa, _, _ = dois_escritorios_com_chamado["alfa"]
        _, _, chamado_beta = dois_escritorios_com_chamado["beta"]
        client.force_login(_contador(escritorio_alfa, "contador.alfa"))

        resposta = client.post(url(chamado_beta))

        # 404 e não 403: distinguir os dois contaria que aquele chamado existe
        # em outra carteira.
        assert resposta.status_code == 404
        chamado_beta.refresh_from_db()
        assert chamado_beta.estado == Solicitacao.Estado.ABERTA

    def test_sem_vinculo_nao_entra(self, client, dois_escritorios_com_chamado):
        _, _, chamado = dois_escritorios_com_chamado["alfa"]
        with rls.escopo_irrestrito():
            orfao = get_user_model().objects.create_user(username="orfao", is_staff=True)
        client.force_login(orfao)

        assert client.post(url(chamado)).status_code == 403

    def test_anonimo_vai_para_o_login(self, client, dois_escritorios_com_chamado):
        _, _, chamado = dois_escritorios_com_chamado["alfa"]
        resposta = client.post(url(chamado))
        assert resposta.status_code == 302
        assert "/entrar/" in resposta["Location"]

    def test_fechar_duas_vezes_nao_e_erro(self, client, dois_escritorios_com_chamado):
        """Dois cliques no botão, ou dois contadores ao mesmo tempo. O desfecho
        pretendido já é o atual — tratar como erro só assustaria."""
        escritorio, _, chamado = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(url(chamado))
        resposta = client.post(url(chamado))

        assert resposta.status_code == 302
        with rls.escopo_irrestrito():
            assert Auditoria.objects.filter(evento="solicitacao_resolvida").count() == 1

    def test_destino_de_volta_nao_sai_do_grimorio(
        self, client, dois_escritorios_com_chamado
    ):
        """`next` é conferido: aceitar qualquer URL faria deste formulário um
        redirecionador aberto, num domínio que o cliente final também acessa."""
        escritorio, _, chamado = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        resposta = client.post(url(chamado), {"next": "https://exemplo-malicioso.test/"})

        assert resposta["Location"] == "/grimorio/"

    def test_o_chamado_some_da_fila_depois_de_resolvido(
        self, client, dois_escritorios_com_chamado
    ):
        """O efeito que o contador vê. Sem isto, a fila mostraria o mesmo item
        para sempre e ele deixaria de confiar nela."""
        escritorio, _, chamado = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        antes = client.get("/grimorio/").content.decode()
        assert chamado.assunto in antes

        client.post(url(chamado), {"next": "/grimorio/"})

        depois = client.get("/grimorio/").content.decode()
        assert chamado.assunto not in depois


@pytest.mark.django_db
def test_a_fila_do_hoje_resolve_sem_mandar_ninguem_para_o_admin(
    client, dois_escritorios_com_chamado
):
    """O gate do Sprint 2b, na parte que já dá para verificar.

    O botão principal da fila de chamados precisa apontar para o próprio
    Grimório. "Ver detalhe" continua indo ao admin de propósito — ler o texto
    inteiro é caso menos frequente —, mas a ação comum não pode exigir a viagem.
    """
    escritorio, _, chamado = dois_escritorios_com_chamado["alfa"]
    client.force_login(_contador(escritorio, "contador.alfa"))

    corpo = client.get("/grimorio/").content.decode()

    assert f'action="/grimorio/chamados/{chamado.pk}/resolver/"' in corpo
    assert "csrfmiddlewaretoken" in corpo
