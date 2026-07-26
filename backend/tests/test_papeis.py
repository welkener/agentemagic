"""Papéis dentro do escritório: Grupo do Django (o quê) + bit `responsavel` (quem convida).

O foco aqui é **escalada de privilégio**: deixar o responsável mexer em
`MembroEscritorio` é dar a ele a chave do isolamento. Cada trava de
`apps/tenants/admin.py` tem um teste que tenta furá-la.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.tenants.models import Escritorio, MembroEscritorio, grupo_do_escritorio
from apps.tenants.permissoes import permissoes_base

URL_MEMBROS = "/admin/tenants/membroescritorio/"


@pytest.fixture
def escritorio_provisionado(db):
    call_command(
        "provisionar_escritorio",
        "Contabilidade Aurora",
        "--contador",
        "aurora.chefe",
        "--email",
        "chefe@aurora.example.com",
        "--phone-number-id",
        "5000001",
    )
    escritorio = Escritorio.objects.get(slug="contabilidade-aurora")
    return escritorio, get_user_model().objects.get(username="aurora.chefe")


@pytest.fixture
def escritorio_vizinho(db):
    vizinho = Escritorio.objects.create(nome="Escritório Vizinho", slug="vizinho", ativo=True)
    usuario = get_user_model().objects.create_user(username="vizinho.contador", is_staff=True)
    MembroEscritorio.objects.create(usuario=usuario, escritorio=vizinho, responsavel=True)
    return vizinho, usuario


# ---------------------------------------------------------------------------
# Provisionamento
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_provisionar_cria_grupo_com_permissoes_e_responsavel(escritorio_provisionado):
    escritorio, chefe = escritorio_provisionado
    grupo = grupo_do_escritorio(escritorio)

    assert grupo.name == "escritorio:contabilidade-aurora"
    assert grupo.permissions.exists()
    assert grupo in chefe.groups.all()
    assert chefe.is_staff and not chefe.is_superuser
    assert not chefe.has_usable_password()  # acesso é por Magic Link
    assert chefe.membro_escritorio.responsavel is True


@pytest.mark.django_db
def test_baseline_nao_da_permissao_de_usuario_nem_de_model_da_plataforma(db):
    """As duas exclusões que impedem escalada e vazamento de plataforma."""
    codenames = {p.codename for p in permissoes_base()}
    apps_permitidas = {p.content_type.app_label for p in permissoes_base()}

    assert "auth" not in apps_permitidas, "permissão de auth.User = escalada de privilégio"
    assert not any("aplicativointegracao" in c for c in codenames)
    assert not any("membroescritorio" in c for c in codenames)
    # E precisa dar o essencial, senão o contador loga e não faz nada.
    assert "view_intencao" in codenames
    assert "change_cliente" in codenames


# ---------------------------------------------------------------------------
# Quem enxerga a tela de equipe
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_responsavel_ve_a_tela_de_equipe(client, escritorio_provisionado):
    _, chefe = escritorio_provisionado
    client.force_login(chefe)
    assert client.get(URL_MEMBROS).status_code == 200


@pytest.mark.django_db
def test_membro_comum_nao_ve_a_tela_de_equipe(client, escritorio_provisionado):
    """Só quem é responsável administra a equipe — o grupo não dá isso."""
    escritorio, _ = escritorio_provisionado
    comum = get_user_model().objects.create_user(username="aurora.junior", is_staff=True)
    comum.groups.add(grupo_do_escritorio(escritorio))
    MembroEscritorio.objects.create(usuario=comum, escritorio=escritorio, responsavel=False)

    client.force_login(comum)
    assert client.get(URL_MEMBROS).status_code == 403
    # ...mas o grupo continua deixando ele trabalhar:
    assert client.get("/admin/clients/cliente/").status_code == 200


# ---------------------------------------------------------------------------
# Convite de colega
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_responsavel_convida_colega_no_formato_certo(client, escritorio_provisionado):
    escritorio, chefe = escritorio_provisionado
    client.force_login(chefe)

    resposta = client.post(
        f"{URL_MEMBROS}add/",
        {"username": "aurora.colega", "email": "colega@aurora.example.com"},
    )
    assert resposta.status_code in (200, 302)

    colega = get_user_model().objects.get(username="aurora.colega")
    assert colega.is_staff and not colega.is_superuser
    assert not colega.has_usable_password()
    assert grupo_do_escritorio(escritorio) in colega.groups.all()
    assert colega.membro_escritorio.escritorio == escritorio
    assert colega.membro_escritorio.responsavel is False


@pytest.mark.django_db
def test_responsavel_nao_convida_para_o_escritorio_do_vizinho(
    client, escritorio_provisionado, escritorio_vizinho
):
    """O escritório não é campo do formulário — vem do vínculo de quem convida."""
    escritorio, chefe = escritorio_provisionado
    vizinho, _ = escritorio_vizinho
    client.force_login(chefe)

    client.post(
        f"{URL_MEMBROS}add/",
        {"username": "invasor", "email": "x@x.com", "escritorio": vizinho.pk},
    )

    membro = MembroEscritorio.objects.get(usuario__username="invasor")
    assert membro.escritorio == escritorio  # o `escritorio` do POST foi ignorado
    assert membro.escritorio != vizinho


@pytest.mark.django_db
def test_responsavel_nao_puxa_superuser_da_magic_bi_pro_escritorio(
    client, escritorio_provisionado
):
    """Seria virar administrador da conta de quem administra a plataforma."""
    _, chefe = escritorio_provisionado
    get_user_model().objects.create_superuser(username="equipe.magicbi", email="e@x.com", password="x")
    client.force_login(chefe)

    resposta = client.post(
        f"{URL_MEMBROS}add/", {"username": "equipe.magicbi", "email": "e@x.com"}
    )
    assert resposta.status_code == 200  # re-renderiza com erro, não redireciona
    assert not MembroEscritorio.objects.filter(usuario__username="equipe.magicbi").exists()


@pytest.mark.django_db
def test_responsavel_nao_rouba_membro_de_outro_escritorio(
    client, escritorio_provisionado, escritorio_vizinho
):
    _, chefe = escritorio_provisionado
    vizinho, contador_vizinho = escritorio_vizinho
    client.force_login(chefe)

    client.post(f"{URL_MEMBROS}add/", {"username": contador_vizinho.username, "email": "x@x.com"})

    contador_vizinho.refresh_from_db()
    assert contador_vizinho.membro_escritorio.escritorio == vizinho  # continua onde estava


@pytest.mark.django_db
def test_responsavel_nao_ve_a_equipe_do_vizinho(client, escritorio_provisionado, escritorio_vizinho):
    _, chefe = escritorio_provisionado
    _, contador_vizinho = escritorio_vizinho
    client.force_login(chefe)

    corpo = client.get(URL_MEMBROS).content.decode()
    assert chefe.username in corpo
    assert contador_vizinho.username not in corpo


@pytest.mark.django_db
def test_responsavel_nao_pode_se_remover_e_deixar_o_escritorio_orfao(
    client, escritorio_provisionado
):
    _, chefe = escritorio_provisionado
    client.force_login(chefe)

    membro = chefe.membro_escritorio
    resposta = client.post(f"{URL_MEMBROS}{membro.pk}/delete/", {"post": "yes"})
    assert resposta.status_code in (403, 302)
    assert MembroEscritorio.objects.filter(pk=membro.pk).exists()


# ---------------------------------------------------------------------------
# Fronteira com auth.User e com o próprio escritório
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_responsavel_nao_alcanca_o_admin_de_usuarios(client, escritorio_provisionado):
    """Quem edita auth.User edita is_superuser — o baseline não dá essa permissão."""
    _, chefe = escritorio_provisionado
    client.force_login(chefe)

    assert client.get("/admin/auth/user/").status_code == 403
    assert client.get("/admin/auth/group/").status_code == 403


@pytest.mark.django_db
def test_slug_do_escritorio_e_readonly_pro_contador(client, escritorio_provisionado):
    """Trocar o slug renomearia o grupo esperado e desligaria a equipe inteira
    das próprias permissões, sem nenhum aviso."""
    escritorio, chefe = escritorio_provisionado
    client.force_login(chefe)

    resposta = client.get(f"/admin/tenants/escritorio/{escritorio.pk}/change/")
    assert resposta.status_code == 200
    assert "slug" in resposta.context["adminform"].readonly_fields
