"""
Grimório — a aplicação do contador fora do admin (DEC-12).

O que estes testes cobrem, em ordem de importância:

1. **Escopo.** Sair do admin é sair de `EscopoEscritorioMixin`, que era quem
   filtrava `get_queryset`. Se o isolamento não valer aqui, a aplicação nova é
   um vazamento com layout bonito.
2. **Acesso.** Quem não tem vínculo recebe 403, não tela vazia — contador que vê
   "0 clientes" acha que perdeu a carteira.
3. **Conteúdo.** Cada tela mostra o dado certo E esconde o do vizinho. A
   asserção positiva não é decoração: sem ela, uma página quebrada ou vazia
   passaria como se estivesse isolando.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.clients.models import Cliente, Perfil
from apps.credentials.models import Credencial
from apps.security.models import SessaoWhatsapp
from apps.tenants import rls
from apps.tenants.models import Escritorio, MembroEscritorio

HOJE = "/grimorio/"
CARTEIRA = "/grimorio/carteira/"
DOCUMENTOS = "/grimorio/documentos/"
INTEGRACOES = "/grimorio/integracoes/"


@pytest.fixture
def duas_carteiras(db):
    """Dois escritórios com uma empresa cada — o cenário que revela vazamento."""
    with rls.escopo_irrestrito():
        a = Escritorio.objects.create(nome="Contábil Alfa", slug="alfa", ativo=True)
        b = Escritorio.objects.create(nome="Contábil Beta", slug="beta", ativo=True)
        clientes = {}
        for escritorio, nome, telefone in (
            (a, "Padaria do Alfa", "5511900000001"),
            (b, "Oficina do Beta", "5511900000002"),
        ):
            cliente = Cliente.objects.create(
                escritorio=escritorio,
                cnpj=f"1111111100{escritorio.pk:04d}",
                nome=nome,
                telefone_whatsapp=telefone,
                email_contato="c@example.com",
                cnae_padrao="6201-5/01",
                codigo_municipio_ibge="3550308",
                codigo_tributacao_nacional="010101",
                ativo=True,
            )
            Perfil.objects.create(cliente=cliente, tier_maximo=1)
            SessaoWhatsapp.objects.create(
                cliente=cliente,
                wa_id=telefone,
                status=SessaoWhatsapp.Status.ATIVA,
                validado_em=timezone.now(),
                expira_em=timezone.now() + timezone.timedelta(days=7),
            )
            clientes[escritorio.slug] = cliente
    return a, b, clientes["alfa"], clientes["beta"]


def _contador(escritorio, username):
    with rls.escopo_irrestrito():
        usuario = get_user_model().objects.create_user(
            username=username, email=f"{username}@example.com", is_staff=True
        )
        usuario.user_permissions.set(Permission.objects.all())
        MembroEscritorio.objects.create(usuario=usuario, escritorio=escritorio)
    return usuario


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_anonimo_vai_para_o_login(client):
    resposta = client.get(HOJE)
    assert resposta.status_code == 302
    assert "/entrar/" in resposta["Location"]


@pytest.mark.django_db
def test_usuario_sem_vinculo_recebe_403_e_nao_tela_vazia(client, duas_carteiras):
    """Padrão seguro que também é bom produto: 403 diz "falta acesso"; a tela
    vazia faria o contador achar que perdeu a carteira e abrir chamado."""
    with rls.escopo_irrestrito():
        orfao = get_user_model().objects.create_user(username="orfao", is_staff=True)
        orfao.user_permissions.set(Permission.objects.all())
    client.force_login(orfao)

    assert client.get(HOJE).status_code == 403


@pytest.mark.django_db
def test_usuario_nao_staff_nao_entra(client, duas_carteiras):
    with rls.escopo_irrestrito():
        comum = get_user_model().objects.create_user(username="cliente.final")
    client.force_login(comum)

    assert client.get(HOJE).status_code == 403


@pytest.mark.django_db
def test_superuser_da_plataforma_entra_sem_vinculo(client, duas_carteiras):
    with rls.escopo_irrestrito():
        equipe = get_user_model().objects.create_superuser(
            username="magicbi", email="e@x.com", password="x"
        )
    client.force_login(equipe)

    corpo = client.get(CARTEIRA).content.decode()
    assert "Padaria do Alfa" in corpo
    assert "Oficina do Beta" in corpo


# ---------------------------------------------------------------------------
# Escopo — a razão de este arquivo existir
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("url", [HOJE, CARTEIRA, DOCUMENTOS, INTEGRACOES])
def test_cada_tela_mostra_o_proprio_e_esconde_o_vizinho(client, duas_carteiras, url):
    a, _, cliente_a, cliente_b = duas_carteiras
    with rls.escopo_irrestrito():
        for cliente in (cliente_a, cliente_b):
            Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"grim-{cliente.pk}",
                tipo_acao="emitir_nfse",
                payload={"valor": 500.0, "tomador": f"Tomador {cliente.pk}"},
                valor=500,
                estado=Intencao.Estado.CONCLUIDO,
            )
    client.force_login(_contador(a, "contador.alfa"))

    resposta = client.get(url)
    assert resposta.status_code == 200, url
    corpo = resposta.content.decode()
    assert cliente_a.nome in corpo, f"{url}: sumiu o dado do próprio escritório"
    assert cliente_b.nome not in corpo, f"{url}: VAZOU dado do escritório vizinho"


@pytest.mark.django_db
def test_ficha_de_empresa_do_vizinho_responde_404(client, duas_carteiras):
    """404, e não 403: distinguir os dois contaria ao contador que aquele
    cliente existe em outra carteira. Vazamento pequeno, mas vazamento."""
    a, _, _, cliente_b = duas_carteiras
    client.force_login(_contador(a, "contador.alfa"))

    resposta = client.get(f"/grimorio/empresa/{cliente_b.pk}/")
    assert resposta.status_code == 404
    assert cliente_b.nome not in resposta.content.decode()


@pytest.mark.django_db
def test_ficha_da_propria_empresa_abre(client, duas_carteiras):
    a, _, cliente_a, _ = duas_carteiras
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(f"/grimorio/empresa/{cliente_a.pk}/").content.decode()
    assert cliente_a.nome in corpo
    # Na tela o CNPJ sai com máscara — contador lê CNPJ o dia inteiro e
    # reconhece pelo formato; sem pontuação precisa contar dígito.
    d = cliente_a.cnpj
    assert f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" in corpo


@pytest.mark.django_db
def test_rls_segura_sozinha_se_a_view_esquecer_o_escopo(client, duas_carteiras):
    """A terceira camada, testada sem as outras duas.

    Simula o erro real: alguém escreve uma consulta nova sem passar por
    `metricas.py` e sem filtrar por escritório. Dentro do escopo do tenant, a
    RLS devolve só a carteira certa — o esquecimento vira resultado menor, nunca
    o dado do vizinho.
    """
    a, _, _, _ = duas_carteiras
    with rls.escopo_de_tenant(a.pk):
        nomes = set(Cliente.objects.values_list("nome", flat=True))  # sem filtro!
    assert nomes == {"Padaria do Alfa"}


# ---------------------------------------------------------------------------
# Conteúdo: a fila de trabalho é o produto desta tela
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_hoje_lista_nota_aguardando_aprovacao_como_critica(client, duas_carteiras):
    a, _, cliente_a, _ = duas_carteiras
    with rls.escopo_irrestrito():
        Intencao.objects.create(
            cliente=cliente_a,
            chave_idempotencia="grim-pendente",
            tipo_acao="emitir_nfse",
            payload={"valor": 1240.0, "tomador": "Fornecedor X"},
            valor=1240,
            estado=Intencao.Estado.AGUARDANDO_APROVACAO,
        )
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(HOJE).content.decode()
    assert "Exige você agora" in corpo
    assert "Nota aguardando aprovação" in corpo
    assert "Fornecedor X" in corpo
    assert "R$ 1.240,00" in corpo  # formato pt-BR, não "1,240.00"


@pytest.mark.django_db
def test_hoje_sem_pendencia_diz_isso_em_vez_de_tabela_vazia(client, db):
    """Estado vazio com texto, não tabela em branco.

    Escritório sem carteira: é o primeiro dia de um tenant novo, e é justamente
    quando a tela vazia assusta. Note que "sem pendência" é mais raro do que
    parece — cliente sem certificado já gera uma —, então o cenário aqui é um
    escritório recém-provisionado de propósito.
    """
    with rls.escopo_irrestrito():
        novo = Escritorio.objects.create(nome="Contábil Nova", slug="nova", ativo=True)
    client.force_login(_contador(novo, "contador.novo"))

    corpo = client.get(HOJE).content.decode()
    assert "Nada exige você agora" in corpo
    assert "Nenhuma nota emitida no período" in corpo


@pytest.mark.django_db
def test_quem_estourou_o_teto_ve_quanto_excedeu_e_nao_quanto_falta(client, duas_carteiras):
    """"Faltam R$ 0,00 para o limite" era o que a tela dizia a quem já
    ultrapassou — some justamente o número que decide a conversa com o cliente.
    Achado em screenshot, não em teste."""
    a, _, cliente_a, _ = duas_carteiras
    with rls.escopo_irrestrito():
        cliente_a.opcao_simples_nacional = Cliente.OpcaoSimplesNacional.MEI
        cliente_a.save()
        Intencao.objects.create(
            cliente=cliente_a,
            chave_idempotencia="grim-estouro",
            tipo_acao="emitir_nfse",
            payload={"valor": 90000.0, "tomador": "Cliente Grande"},
            valor=90000,
            estado=Intencao.Estado.CONCLUIDO,
        )
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(HOJE).content.decode()
    assert "ultrapassou em" in corpo
    assert "faltam R$ 0,00" not in corpo


@pytest.mark.django_db
def test_contagem_de_itens_criticos_escreve_certo_no_plural(client, duas_carteiras):
    """`pluralize` recebe o SUFIXO, não a palavra: "item" + ",ns" saía
    "itemns" na tela. Cosmético, mas é a primeira coisa que o contador lê."""
    a, _, cliente_a, _ = duas_carteiras
    with rls.escopo_irrestrito():
        for i in range(2):
            Intencao.objects.create(
                cliente=cliente_a,
                chave_idempotencia=f"grim-plural-{i}",
                tipo_acao="emitir_nfse",
                payload={"valor": 100.0, "tomador": "X"},
                valor=100,
                estado=Intencao.Estado.AGUARDANDO_APROVACAO,
            )
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(HOJE).content.decode()
    assert "itens" in corpo
    assert "itemns" not in corpo


@pytest.mark.django_db
def test_certificado_vencido_aparece_como_critico(client, duas_carteiras):
    a, _, cliente_a, _ = duas_carteiras
    with rls.escopo_irrestrito():
        Credencial.objects.create(
            cliente=cliente_a,
            integracao="nfse_nacional",
            tipo=Credencial.Tipo.CERTIFICADO_PSC,
            psc_provedor="birdid",
            psc_identificador="id-1",
            certificado_validade=timezone.localdate() - timezone.timedelta(days=3),
        )
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(HOJE).content.decode()
    assert "Certificado vencido" in corpo
    assert "A emissão está parada" in corpo


@pytest.mark.django_db
def test_carteira_filtra_por_situacao(client, duas_carteiras):
    a, _, cliente_a, _ = duas_carteiras
    client.force_login(_contador(a, "contador.alfa"))

    # O cliente do Alfa tem sessão ativa, então não entra no filtro "sem_canal".
    corpo = client.get(f"{CARTEIRA}?filtro=sem_canal").content.decode()
    assert cliente_a.nome not in corpo
    assert "Nenhuma empresa neste filtro" in corpo


@pytest.mark.django_db
def test_carteira_busca_por_cnpj(client, duas_carteiras):
    a, _, cliente_a, _ = duas_carteiras
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(f"{CARTEIRA}?q={cliente_a.cnpj}").content.decode()
    assert cliente_a.nome in corpo


@pytest.mark.django_db
def test_busca_nao_atravessa_escritorio(client, duas_carteiras):
    """Busca é o caminho clássico de vazamento: filtra depois do escopo, nunca
    antes. Procurar pelo CNPJ do vizinho não pode revelar que ele existe."""
    a, _, _, cliente_b = duas_carteiras
    client.force_login(_contador(a, "contador.alfa"))

    corpo = client.get(f"{CARTEIRA}?q={cliente_b.cnpj}").content.decode()
    assert cliente_b.nome not in corpo


# ---------------------------------------------------------------------------
# Marca — o produto é SaaS, nada de cliente escrito no template
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cada_contador_ve_a_marca_do_proprio_escritorio(client, duas_carteiras):
    a, b, _, _ = duas_carteiras

    client.force_login(_contador(a, "contador.alfa"))
    corpo = client.get(HOJE).content.decode()
    assert "Contábil Alfa" in corpo
    assert "Contábil Beta" not in corpo

    client.force_login(_contador(b, "contador.beta"))
    corpo = client.get(HOJE).content.decode()
    assert "Contábil Beta" in corpo
    assert "Contábil Alfa" not in corpo


@pytest.mark.django_db
def test_nenhum_nome_de_cliente_esta_escrito_no_template(duas_carteiras):
    """Regra registrada em 25/jul/2026, quando "Rotina Contábil" apareceu
    hardcoded num template: este produto é multi-tenant, e marca de um parceiro
    escrita no código aparece no painel de todos os outros."""
    from pathlib import Path

    templates = Path(__file__).resolve().parent.parent / "apps/painel/templates/grimorio"
    proibidos = ("Rotina", "rotinacontabil")
    for arquivo in templates.rglob("*.html"):
        conteudo = arquivo.read_text(encoding="utf-8")
        for termo in proibidos:
            assert termo not in conteudo, f"{arquivo.name} tem '{termo}' escrito no template"


# ---------------------------------------------------------------------------
# Formatação — erram feio em pt-BR se ninguém olhar
# ---------------------------------------------------------------------------
def test_moeda_usa_formato_brasileiro():
    from apps.painel.apresentacao import moeda

    assert moeda(1240.5) == "R$ 1.240,50"
    assert moeda(31647.52) == "R$ 31.647,52"
    assert moeda(0) == "R$ 0,00"
    assert moeda(None) == "—"


def test_grafico_de_serie_zerada_nao_estoura():
    """Carteira nova tem série toda zerada, e `max - min` daria divisão por
    zero. Vira reta na base, que é a leitura correta."""
    from apps.painel.apresentacao import sparkline

    grafico = sparkline([0, 0, 0])
    assert grafico["vazio"] is True
    assert grafico["pontos"]


def test_grafico_sem_dados_nenhum_nao_estoura():
    from apps.painel.apresentacao import sparkline

    assert sparkline([])["vazio"] is True
