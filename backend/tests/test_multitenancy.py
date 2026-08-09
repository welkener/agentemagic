"""Auditoria de vazamento entre escritórios (tenants).

Não testa "a feature funciona" — testa que **o escritório A não alcança o B**,
por cada superfície que existe hoje: banco, webhook, admin (listagem, URL
direta, dropdown de formulário), dashboard e branding.

O cenário é sempre o pior caso de propósito: dois escritórios com um cliente
de **mesmo CNPJ e mesmo telefone**. Se o isolamento depender de os dados serem
distintos, ele não é isolamento.
"""
import hashlib
import hmac
import json

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.clients.models import Cliente, Perfil, Usuario
from apps.credentials.models import Credencial
from apps.tenants.models import Escritorio, MembroEscritorio

URL_WEBHOOK = "/webhook/whatsapp"
TELEFONE = "5511900000001"
CNPJ = "11111111000111"


# ---------------------------------------------------------------------------
# Cenário: dois escritórios, clientes homônimos
# ---------------------------------------------------------------------------
@pytest.fixture
def dois_escritorios(db):
    a = Escritorio.objects.create(
        nome="Escritório A", slug="escritorio-a", ativo=True, whatsapp_phone_number_id="1000001"
    )
    b = Escritorio.objects.create(
        nome="Escritório B", slug="escritorio-b", ativo=True, whatsapp_phone_number_id="2000002"
    )
    return a, b


def _cria_cliente(escritorio, nome):
    c = Cliente.objects.create(
        escritorio=escritorio,
        cnpj=CNPJ,
        nome=nome,
        telefone_whatsapp=TELEFONE,
        email_contato="contato@example.com",
        cnae_padrao="6201-5/01",
        ativo=True,
    )
    Perfil.objects.create(cliente=c, tier_maximo=1, ferramentas_habilitadas=["erp_mock", "nfse_mock"])
    return c


@pytest.fixture
def carteiras(dois_escritorios):
    a, b = dois_escritorios
    return a, b, _cria_cliente(a, "Cliente do A"), _cria_cliente(b, "Cliente do B")


def _contador(escritorio, username):
    usuario = get_user_model().objects.create_user(
        username=username, email=f"{username}@example.com", is_staff=True
    )
    # Sem permissões de model o admin esconde tudo por outro motivo, o que
    # mascararia o teste de isolamento — daí o superuser=False + all perms.
    from django.contrib.auth.models import Permission

    usuario.user_permissions.set(Permission.objects.all())
    MembroEscritorio.objects.create(usuario=usuario, escritorio=escritorio)
    return usuario


# ---------------------------------------------------------------------------
# Banco — unicidade é POR escritório, não global
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_dois_escritorios_podem_ter_o_mesmo_cnpj_e_telefone(carteiras):
    _, _, cliente_a, cliente_b = carteiras
    assert cliente_a.cnpj == cliente_b.cnpj
    assert cliente_a.telefone_whatsapp == cliente_b.telefone_whatsapp
    assert cliente_a.pk != cliente_b.pk


@pytest.mark.django_db
def test_mesmo_escritorio_nao_duplica_cnpj(dois_escritorios):
    a, _ = dois_escritorios
    _cria_cliente(a, "Primeiro")
    with pytest.raises(IntegrityError), transaction.atomic():
        Cliente.objects.create(
            escritorio=a, cnpj=CNPJ, nome="Duplicado", telefone_whatsapp="5511999999999"
        )


@pytest.mark.django_db
def test_mesmo_telefone_em_duas_empresas_do_mesmo_escritorio(dois_escritorios):
    """O caso que a modelagem antiga PROIBIA — e que é rotina (DEC-03).

    Até 08/ago/2026 o telefone era campo do `Cliente`, único por escritório, e
    esta mesma situação levantava `IntegrityError`. O sócio de duas empresas, o
    financeiro de três lojas e o contador terceirizado simplesmente não cabiam
    no cadastro; o atendimento falhava depois, em silêncio.

    Agora as duas empresas existem, e quem é único é o **usuário**: um número,
    uma pessoa, duas empresas. Escolher entre elas é o que
    `apps/core/desambiguacao.py` faz — perguntando, nunca adivinhando.
    """
    a, _ = dois_escritorios
    primeira = _cria_cliente(a, "Primeiro")
    segunda = Cliente.objects.create(
        escritorio=a, cnpj="99999999000199", nome="Segunda empresa", telefone_whatsapp=TELEFONE
    )

    usuarios = Usuario.objects.filter(escritorio=a)
    assert usuarios.count() == 1, "o mesmo número não pode virar duas pessoas"
    assert set(usuarios.first().clientes_ativos()) == {primeira, segunda}


@pytest.mark.django_db
def test_mesmo_escritorio_nao_duplica_o_usuario_do_telefone(dois_escritorios):
    """A unicidade não sumiu — mudou de lugar, e agora vale de verdade.

    No modelo antigo "5511999999999" e "551199999999" passavam pela constraint
    por serem strings diferentes, apesar de serem o mesmo assinante. O número é
    canonicalizado antes de gravar, então as duas grafias colidem.
    """
    a, _ = dois_escritorios
    _cria_cliente(a, "Primeiro")
    with pytest.raises(IntegrityError), transaction.atomic():
        Usuario.objects.create(escritorio=a, telefone_whatsapp=TELEFONE)


# ---------------------------------------------------------------------------
# Webhook — o número que RECEBEU decide o tenant
# ---------------------------------------------------------------------------
def _payload(phone_number_id, message_id, telefone=TELEFONE, texto="qual meu estoque?"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": telefone,
                                    "timestamp": "1751900000",
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _post(client, payload):
    corpo = json.dumps(payload).encode()
    mac = hmac.new(settings.META_APP_SECRET.encode(), corpo, hashlib.sha256)
    return client.post(
        URL_WEBHOOK,
        data=corpo,
        content_type="application/json",
        headers={"X-Hub-Signature-256": f"sha256={mac.hexdigest()}"},
    )


@pytest.mark.django_db
def test_mensagem_cai_no_cliente_do_escritorio_dono_do_numero(client, carteiras):
    """O caso que quebraria tudo: mesmo telefone nas duas carteiras.

    Se o roteamento olhasse só o remetente, a mensagem cairia no primeiro
    cliente que casasse — metade das vezes no escritório errado, com o
    certificado errado. É o número receptor que desempata.
    """
    a, b, cliente_a, cliente_b = carteiras

    assert _post(client, _payload(a.whatsapp_phone_number_id, "wamid.A1")).status_code == 200
    assert set(Auditoria.objects.values_list("cliente_id", flat=True)) == {cliente_a.pk}

    assert _post(client, _payload(b.whatsapp_phone_number_id, "wamid.B1")).status_code == 200
    assert Auditoria.objects.filter(cliente=cliente_b).exists()


@pytest.mark.django_db
def test_numero_desconhecido_com_varios_tenants_nao_processa(client, carteiras):
    """Com 2+ escritórios não há fallback: sem número casado, descarta."""
    resposta = _post(client, _payload("9999999", "wamid.X1"))
    assert resposta.status_code == 200  # ack pro Meta, mas...
    assert Auditoria.objects.count() == 0  # ...nada foi processado


@pytest.mark.django_db
def test_instalacao_de_um_tenant_so_continua_funcionando(client, cliente):
    """Compat: com um escritório só, número sem casar cai nele (não há pra onde vazar)."""
    assert _post(client, _payload("", "wamid.C1", telefone=cliente.telefone_whatsapp)).status_code == 200
    assert Auditoria.objects.filter(cliente=cliente).exists()


# ---------------------------------------------------------------------------
# Admin — listagem, URL direta e formulário
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_contador_nao_ve_cliente_de_outro_escritorio_na_listagem(client, carteiras):
    a, _, cliente_a, cliente_b = carteiras
    client.force_login(_contador(a, "contador.a"))

    corpo = client.get("/admin/clients/cliente/").content.decode()
    assert cliente_a.nome in corpo
    assert cliente_b.nome not in corpo


@pytest.mark.django_db
def test_contador_nao_abre_objeto_de_outro_escritorio_por_url(client, carteiras):
    """Esconder da lista não basta — o id é adivinhável.

    Fora do queryset, o admin trata como inexistente: redireciona pro índice
    (302), não 404. O que importa é nunca devolver 200 com o dado do vizinho.
    """
    a, _, _, cliente_b = carteiras
    client.force_login(_contador(a, "contador.a"))

    for sufixo in ("change", "delete", "history"):
        resposta = client.get(f"/admin/clients/cliente/{cliente_b.pk}/{sufixo}/")
        assert resposta.status_code != 200, sufixo
        assert cliente_b.nome not in resposta.content.decode(), sufixo


@pytest.mark.django_db
def test_isolamento_vale_em_todas_as_superficies_penduradas_no_cliente(client, carteiras):
    """Cada listagem tem que mostrar o dado de A **e** esconder o de B.

    A asserção positiva não é decoração: sem ela, uma página que quebrasse ou
    voltasse vazia passaria no teste como se estivesse isolando.
    """
    from apps.security.models import SessaoWhatsapp

    a, b, cliente_a, cliente_b = carteiras

    for cliente in (cliente_a, cliente_b):
        Credencial.objects.create(
            cliente=cliente,
            integracao="nfse_nacional",
            tipo=Credencial.Tipo.CERTIFICADO_PSC,
            psc_provedor="birdid",
            psc_identificador=f"id-{cliente.pk}",
        )
        Intencao.objects.create(
            cliente=cliente,
            chave_idempotencia=f"chave-{cliente.pk}",
            tipo_acao="emitir_nfse",
            payload={"valor": 100.0},
            estado=Intencao.Estado.AGUARDANDO_APROVACAO,
        )
        Auditoria.objects.create(evento="teste_isolamento", dados={}, cliente=cliente)
        SessaoWhatsapp.objects.create(
            cliente=cliente, wa_id=cliente.telefone_whatsapp, status=SessaoWhatsapp.Status.ATIVA
        )

    client.force_login(_contador(a, "contador.a"))

    for url in (
        "/admin/credentials/credencial/",
        "/admin/agente_nf/intencao/",
        "/admin/audit/auditoria/",
        "/admin/security/sessaowhatsapp/",
    ):
        resposta = client.get(url)
        assert resposta.status_code == 200, url
        corpo = resposta.content.decode()
        assert cliente_a.nome in corpo, f"{url}: sumiu o dado do próprio escritório"
        assert cliente_b.nome not in corpo, f"{url}: VAZOU dado do escritório vizinho"


@pytest.mark.django_db
def test_filtro_lateral_nao_lista_os_outros_escritorios(client, carteiras):
    """O filtro por escritório lista os escritórios — pro contador isso seria
    entregar a carteira de parceiros (os concorrentes dele) da Magic BI."""
    a, b, _, _ = carteiras
    client.force_login(_contador(a, "contador.a"))
    assert b.nome not in client.get("/admin/clients/cliente/").content.decode()


@pytest.mark.django_db
def test_dropdown_do_formulario_nao_oferece_cliente_de_outro_escritorio(client, carteiras):
    """get_queryset esconde da lista; sem limitar o FK, o contador ainda
    conseguiria CRIAR credencial apontando pro cliente do vizinho."""
    a, _, cliente_a, cliente_b = carteiras
    client.force_login(_contador(a, "contador.a"))

    resposta = client.get("/admin/credentials/credencial/add/")
    escolhas = resposta.context["adminform"].form.fields["cliente"].queryset
    assert list(escolhas) == [cliente_a]
    assert cliente_b not in escolhas


@pytest.mark.django_db
def test_staff_sem_vinculo_nao_ve_nada(client, carteiras):
    """Padrão seguro: usuário meio-provisionado não pode significar acesso total."""
    _, _, cliente_a, cliente_b = carteiras
    usuario = get_user_model().objects.create_user(username="orfao", is_staff=True)
    from django.contrib.auth.models import Permission

    usuario.user_permissions.set(Permission.objects.all())
    client.force_login(usuario)

    corpo = client.get("/admin/clients/cliente/").content.decode()
    assert cliente_a.nome not in corpo
    assert cliente_b.nome not in corpo


@pytest.mark.django_db
def test_superuser_da_magic_bi_ve_a_plataforma_inteira(client, carteiras):
    _, _, cliente_a, cliente_b = carteiras
    equipe = get_user_model().objects.create_superuser(username="magicbi", email="e@x.com", password="x")
    client.force_login(equipe)

    corpo = client.get("/admin/clients/cliente/").content.decode()
    assert cliente_a.nome in corpo
    assert cliente_b.nome in corpo


@pytest.mark.django_db
def test_app_de_integracao_e_da_plataforma_nao_do_escritorio(client, carteiras):
    """AplicativoIntegracao é o app OAuth da Magic BI, compartilhado — contador não mexe."""
    a, _, _, _ = carteiras
    client.force_login(_contador(a, "contador.a"))
    assert client.get("/admin/credentials/aplicativointegracao/").status_code == 403


@pytest.mark.django_db
def test_contador_nao_ve_o_escritorio_do_vizinho(client, carteiras):
    a, b, _, _ = carteiras
    client.force_login(_contador(a, "contador.a"))

    corpo = client.get("/admin/tenants/escritorio/").content.decode()
    assert a.nome in corpo
    assert b.nome not in corpo

    resposta = client.get(f"/admin/tenants/escritorio/{b.pk}/change/")
    assert resposta.status_code != 200
    assert b.nome not in resposta.content.decode()


# ---------------------------------------------------------------------------
# Dashboard e branding
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_dashboard_conta_so_o_proprio_escritorio(client, carteiras):
    """O agregado não pode revelar o que a listagem esconde."""
    a, b, cliente_a, cliente_b = carteiras
    for cliente, n in ((cliente_a, 1), (cliente_b, 5)):
        for i in range(n):
            Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"dash-{cliente.pk}-{i}",
                tipo_acao="emitir_nfse",
                payload={"valor": 10.0},
                estado=Intencao.Estado.AGUARDANDO_APROVACAO,
            )

    client.force_login(_contador(a, "contador.a"))
    contexto = client.get("/admin/").context
    assert contexto["notas_pendentes"] == 1  # não 6
    assert contexto["clientes_ativos"] == 1


@pytest.mark.django_db
def test_cada_contador_ve_a_marca_do_proprio_escritorio(client, carteiras):
    a, b, _, _ = carteiras

    client.force_login(_contador(a, "contador.a"))
    assert "Escritório A" in client.get("/admin/").content.decode()

    client.force_login(_contador(b, "contador.b"))
    corpo = client.get("/admin/").content.decode()
    assert "Escritório B" in corpo
    assert "Escritório A" not in corpo
