"""
Row Level Security — a rede embaixo do escopo de aplicação (DEC-04).

O que estes testes provam é diferente do que `test_multitenancy.py` prova. Lá, o
isolamento é verificado *pelas superfícies que existem*: admin, webhook,
dashboard. Aqui, verifica-se que ele vale **mesmo quando o código erra** — que é
o único cenário em que uma rede de segurança importa.

O caso central é `test_filtro_esquecido_nao_vaza`: uma consulta escrita errado,
sem tenant nenhum, do jeito que sai de quem está com pressa. Antes da RLS ela
devolveria a carteira do escritório vizinho e nada acusaria.
"""
import pytest
from django.db import connection

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.clients.models import Cliente
from apps.tenants import rls
from apps.tenants.models import Escritorio


@pytest.fixture
def dois_escritorios(db):
    """Cenário mínimo: duas carteiras, cada uma com um cliente."""
    with rls.escopo_irrestrito():
        a = Escritorio.objects.create(nome="Escritório A", slug="rls-a", ativo=True)
        b = Escritorio.objects.create(nome="Escritório B", slug="rls-b", ativo=True)
        for escritorio, nome in ((a, "Cliente do A"), (b, "Cliente do B")):
            Cliente.objects.create(
                escritorio=escritorio,
                cnpj=f"1111111100{escritorio.pk:04d}",
                nome=nome,
                telefone_whatsapp=f"55119000{escritorio.pk:05d}",
                ativo=True,
            )
    return a, b


# ---------------------------------------------------------------------------
# O papel — se ele puder ignorar policy, nada abaixo significa coisa alguma
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_papel_da_aplicacao_nao_pode_ignorar_rls():
    """`BYPASSRLS` no papel tornaria toda policy decorativa.

    Vale a checagem explícita porque o modo de falha é silencioso: as policies
    continuariam lá, o `SET ROLE` continuaria rodando, e o vazamento passaria
    despercebido até alguém comparar duas carteiras.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s",
            [rls.PAPEL_APLICACAO],
        )
        linha = cursor.fetchone()

    assert linha is not None, f"papel {rls.PAPEL_APLICACAO} não existe — a migração não rodou"
    bypassrls, superuser = linha
    assert not bypassrls, "papel com BYPASSRLS: toda policy vira decoração"
    assert not superuser, "papel superuser ignora RLS por definição"


@pytest.mark.django_db
def test_a_conexao_do_teste_esta_de_fato_sob_rls():
    """Guarda do próprio arsenal de testes.

    Se um dia o `SET LOCAL ROLE` do conftest parar de rodar, todos os testes
    abaixo continuariam VERDES — como dono da tabela, tudo é visível e nada é
    bloqueado. Verde por perda de rigor é o modo mais silencioso de uma suíte de
    segurança apodrecer.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] == rls.PAPEL_APLICACAO


# ---------------------------------------------------------------------------
# O gate do Sprint 1
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_sem_tenant_no_contexto_nao_ha_linha(dois_escritorios):
    """Sem escopo declarado, o banco não entrega nada — nem do próprio tenant.

    A falha aponta para o lado seguro de propósito: "não vejo nada" aparece no
    primeiro teste; "vejo demais" aparece num vazamento.
    """
    rls.definir_irrestrito(False)
    assert Cliente.objects.count() == 0
    assert Escritorio.objects.count() == 0


@pytest.mark.django_db
def test_filtro_esquecido_nao_vaza(dois_escritorios):
    """O gate: consulta SEM filtro de tenant, escrita errado de propósito.

    É a linha que sai de quem está com pressa — `Cliente.objects.all()`, sem
    `escritorio=`. Com o escopo declarado, ela devolve só a carteira certa; o
    esquecimento vira um resultado menor, nunca o dado do vizinho.
    """
    a, _ = dois_escritorios

    with rls.escopo_de_tenant(a.pk):
        nomes = set(Cliente.objects.values_list("nome", flat=True))  # sem filtro!

    assert nomes == {"Cliente do A"}


@pytest.mark.django_db
def test_escopo_de_um_tenant_nao_alcanca_o_outro(dois_escritorios):
    a, b = dois_escritorios
    with rls.escopo_de_tenant(a.pk):
        assert Escritorio.objects.get().nome == "Escritório A"
    with rls.escopo_de_tenant(b.pk):
        assert Escritorio.objects.get().nome == "Escritório B"


@pytest.mark.django_db
def test_nao_da_para_escrever_para_o_tenant_vizinho(dois_escritorios):
    """Ler filtrado e escrever livre seria isolamento pela metade.

    A policy vale na escrita pelo mesmo predicado (o Postgres reaproveita o
    `USING` como `WITH CHECK`), então inserir para o vizinho é recusado.
    """
    from django.db import IntegrityError, InternalError, ProgrammingError, transaction

    a, b = dois_escritorios
    with rls.escopo_de_tenant(a.pk):
        with pytest.raises((ProgrammingError, IntegrityError, InternalError)):
            with transaction.atomic():
                Cliente.objects.create(
                    escritorio=b,
                    cnpj="99999999000199",
                    nome="Invasão",
                    telefone_whatsapp="5511900009999",
                )


@pytest.mark.django_db
def test_tabelas_penduradas_no_cliente_herdam_o_escopo(dois_escritorios):
    """Isolar `Cliente` e deixar a nota exposta não isolaria nada — a nota é que
    tem o valor. Vale para tudo que pendura no cliente."""
    a, b = dois_escritorios
    with rls.escopo_irrestrito():
        for escritorio in (a, b):
            cliente = Cliente.objects.get(escritorio=escritorio)
            Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"rls-{cliente.pk}",
                tipo_acao="emitir_nfse",
                payload={"valor": 100.0},
                estado=Intencao.Estado.AGUARDANDO_APROVACAO,
            )
            Auditoria.objects.create(evento="rls_teste", dados={}, cliente=cliente)

    with rls.escopo_de_tenant(a.pk):
        assert Intencao.objects.count() == 1
        assert Auditoria.objects.filter(evento="rls_teste").count() == 1
        assert Intencao.objects.get().cliente.nome == "Cliente do A"


# ---------------------------------------------------------------------------
# O worker — onde a RLS costuma ficar de fora
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_task_sem_escritorio_declarado_nao_enxerga_nada(dois_escritorios):
    """O canário do worker.

    O `task_prerun` assume o papel restrito e recusa o irrestrito por herança.
    Uma task que não declare `escritorio_id` roda sem escopo e não vê linha
    nenhuma — falha barulhenta aqui, em vez de leitura silenciosa do tenant
    errado em produção.
    """
    from celery import shared_task

    @shared_task
    def _contar_clientes():
        return Cliente.objects.count()

    assert _contar_clientes.apply().get() == 0


@pytest.mark.django_db
def test_task_com_escritorio_declarado_ve_so_a_carteira_dele(dois_escritorios):
    from celery import shared_task

    a, _ = dois_escritorios

    @shared_task
    def _listar(escritorio_id=None):
        return list(Cliente.objects.values_list("nome", flat=True))

    assert _listar.apply(kwargs={"escritorio_id": a.pk}).get() == ["Cliente do A"]


@pytest.mark.django_db
def test_task_devolve_o_escopo_de_quem_chamou(dois_escritorios):
    """O worker reusa a conexão entre tasks, então escopo que não é devolvido
    vaza para a task seguinte. Dentro de um teste o sintoma é outro e igualmente
    traiçoeiro: as consultas seguintes voltam vazias e parece que a task não
    gravou nada."""
    from celery import shared_task

    a, _ = dois_escritorios

    @shared_task
    def _nada(escritorio_id=None):
        return True

    _nada.apply(kwargs={"escritorio_id": a.pk}).get()

    # O cenário foi montado em modo irrestrito (conftest) — tem que continuar.
    assert Cliente.objects.count() == 2


# ---------------------------------------------------------------------------
# O ovo-e-galinha: resolver o tenant precisa acontecer ANTES de haver escopo
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_vinculo_do_contador_e_resolvido_mesmo_sem_escopo(dois_escritorios):
    """Reproduz o bug que a suíte inteira deixou passar.

    `MembroEscritorio` também tem policy. Se `escopo_do_usuario` a consultasse
    já sob RLS, o vínculo pareceria não existir, o middleware não declararia
    tenant nenhum e todo contador legítimo levaria 403 — que foi exatamente o
    que apareceu no primeiro screenshot do Grimório.

    A suíte não pegava porque a fixture deixa o modo de montagem ligado; aqui
    ele é desligado de propósito, para valer como produção.
    """
    from django.contrib.auth import get_user_model

    from apps.tenants.escopo import escopo_do_usuario
    from apps.tenants.models import MembroEscritorio

    a, _ = dois_escritorios
    with rls.escopo_irrestrito():
        contador = get_user_model().objects.create_user(username="ovo.galinha", is_staff=True)
        MembroEscritorio.objects.create(usuario=contador, escritorio=a)

    rls.definir_irrestrito(False)  # como numa requisição de verdade

    irrestrito, escritorio = escopo_do_usuario(contador)
    assert not irrestrito
    assert escritorio is not None, "vínculo sumiu sob RLS — contador levaria 403"
    assert escritorio.pk == a.pk


@pytest.mark.django_db
def test_grimorio_abre_para_o_contador_em_condicao_de_producao(client, dois_escritorios):
    """O mesmo bug, pela porta da frente: requisição HTTP com o modo de
    montagem desligado antes de entrar."""
    from django.contrib.auth import get_user_model

    from apps.tenants.models import MembroEscritorio

    a, _ = dois_escritorios
    with rls.escopo_irrestrito():
        contador = get_user_model().objects.create_user(username="contador.producao", is_staff=True)
        MembroEscritorio.objects.create(usuario=contador, escritorio=a)
    client.force_login(contador)

    rls.definir_irrestrito(False)

    resposta = client.get("/grimorio/")
    assert resposta.status_code == 200, "contador legítimo recusado sob RLS real"
    assert "Cliente do A" in resposta.content.decode() or "Operação de hoje" in resposta.content.decode()


# ---------------------------------------------------------------------------
# Cobertura: tabela nova é decisão, não efeito colateral
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_toda_tabela_de_dominio_tem_policy_ou_motivo_escrito():
    """Falha quando aparece tabela nova sem decisão sobre isolamento.

    É o teste que envelhece bem: sem ele, o app criado daqui a três meses nasce
    sem policy e ninguém percebe, porque nada quebra — só passa a enxergar tudo.
    """
    from django.apps import apps

    NOSSOS = {
        "clients", "credentials", "agente_nf", "audit", "security",
        "channel_whatsapp", "channel_evolution", "tenants", "core", "painel",
        "fiscal", "adapters", "governance", "observabilidade",
    }
    tabelas = {
        m._meta.db_table for m in apps.get_models() if m._meta.app_label in NOSSOS
    }
    decididas = set(rls.TABELAS) | set(rls.SEM_TENANT)

    assert not (tabelas - decididas), (
        "tabela de domínio sem decisão de isolamento: "
        f"{sorted(tabelas - decididas)}. Some a `TABELAS` (com o caminho até o "
        "escritório) ou a `SEM_TENANT` (com o motivo) em apps/tenants/rls.py."
    )


@pytest.mark.django_db
def test_toda_tabela_com_policy_realmente_tem_a_policy_no_banco():
    """O mapa em Python pode divergir do que a migração aplicou — por exemplo se
    alguém acrescentar a tabela ao dicionário e esquecer a migração."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_policies WHERE policyname = 'isolamento_tenant'"
        )
        no_banco = {linha[0] for linha in cursor.fetchall()}

    faltando = set(rls.TABELAS) - no_banco
    assert not faltando, f"declaradas em rls.TABELAS mas sem policy no banco: {sorted(faltando)}"


@pytest.mark.django_db
def test_rls_esta_ligada_em_todas_as_tabelas_declaradas():
    """Policy criada em tabela com RLS desligada não filtra nada."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relname FROM pg_class WHERE relrowsecurity AND relname = ANY(%s)",
            [list(rls.TABELAS)],
        )
        ligadas = {linha[0] for linha in cursor.fetchall()}

    assert set(rls.TABELAS) - ligadas == set()
