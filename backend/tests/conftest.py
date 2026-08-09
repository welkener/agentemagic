"""Fixtures compartilhadas dos testes do MVP."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.agents.agente_nf import conversa
from apps.agents.prompt import esquecer_cache
from apps.clients.models import Cliente, Perfil
from apps.tenants import rls
from apps.tenants.models import Escritorio
from apps.security.models import SessaoWhatsapp


@pytest.fixture(autouse=True)
def rls_ativa(request):
    """Põe TODO teste que toca o banco sob Row Level Security (DEC-04).

    Sem isto a suíte roda como dono das tabelas, e dono ignora policy: os testes
    ficariam verdes provando nada. Foi verificado antes de escrever qualquer
    policy — `postgres` tem `usesuper` E `rolbypassrls`, e nem
    `FORCE ROW LEVEL SECURITY` o alcança.

    A consequência é deliberada e é o ponto: um caminho de produção que esqueça
    de declarar o tenant **não vê linha nenhuma aqui**, e o teste fica vermelho —
    em vez de a falha aparecer com cliente na frente.

    **Montar cenário é irrestrito; exercitar o produto não é.** O teste cria dado
    de dois escritórios ao mesmo tempo — coisa que nenhum tenant pode fazer —, e
    por isso o modo de montagem começa ligado. O que devolve o rigor é que as
    duas portas de entrada de produção saem dele sozinhas: `escopo_de_tenant`
    desliga o irrestrito ao entrar (operar em nome de um escritório e enxergar a
    plataforma são afirmações contraditórias), e o `task_prerun` do Celery o
    desliga sempre. Então uma requisição ou uma task dentro do teste vale como a
    de verdade, e uma que esqueça de declarar o tenant fica **vermelha aqui**.
    """
    if not {"db", "transactional_db", "django_db_setup"} & set(request.fixturenames):
        yield
        return

    request.getfixturevalue("db")  # garante que a transação do teste já abriu
    rls.assumir_papel_restrito()
    rls.definir_irrestrito(True)
    yield


@pytest.fixture(autouse=True)
def caches_derivados_limpos():
    """Zera o que o Sprint 2 passou a cachear entre mensagens.

    Dois caches, os dois com a mesma armadilha: guardam uma decisão tomada a
    partir do banco (o catálogo de ferramentas do tenant, o modo de orçamento
    dele) e sobrevivem ao rollback da transação do teste. Sem limpar, um teste
    que configura um limite de gasto contamina o seguinte — e a falha aparece
    como flakiness dependente da ordem, que é a mais cara de diagnosticar.
    """
    esquecer_cache()
    from django.core.cache import cache

    cache.clear()
    yield
    esquecer_cache()
    cache.clear()


@pytest.fixture
def extrair_fixo(monkeypatch):
    """Substitui a extração de campos da nota por um valor fixo.

    A extração é a única chamada ao modelo do fluxo de nota. Dublar aqui — e não
    a classe do orquestrador, como antes do Sprint 2 — segue o lugar onde ela
    passou a morar (`agente_nf/conversa.py`); o que os testes afirmam sobre a
    conversa continua idêntico.
    """

    def aplicar(**campos):
        dados = conversa.DadosNotaExtraidos(**campos)
        monkeypatch.setattr(conversa, "extrair_dados_nota", lambda ctx, mensagem: dados)
        return dados

    return aplicar


@pytest.fixture
def escritorio(db):
    """Escritório contábil dono da carteira — raiz de tenant (ver apps/tenants/models.py)."""
    with rls.escopo_irrestrito():
        return Escritorio.objects.create(nome="Rotina Contábil", slug="rotina", ativo=True)


@pytest.fixture
def cliente(db, escritorio):
    """Padaria Estrela — empresa exemplo com perfil Tier 1 e sessão já validada.

    Representa o estado normal de um cliente já onboardado — os testes que
    exercitam o próprio gate de sessão (apps/security) usam clientes à parte,
    sem `SessaoWhatsapp` ativa (ver tests/test_security.py).
    """
    with rls.escopo_irrestrito():
        c = Cliente.objects.create(
            escritorio=escritorio,
            cnpj="12345678000190",
            nome="Padaria Estrela Ltda",
            telefone_whatsapp="5511999998888",
            email_contato="dono@padariaestrela.example.com",
            cnae_padrao="5611-2/01",
            ativo=True,
        )
        Perfil.objects.create(
            cliente=c,
            persona="lumen",
            ferramentas_habilitadas=["erp_mock", "nfse_mock"],
            tier_maximo=1,
        )
        agora = timezone.now()
        SessaoWhatsapp.objects.create(
            cliente=c,
            wa_id=c.telefone_whatsapp,
            status=SessaoWhatsapp.Status.ATIVA,
            validado_em=agora,
            expira_em=agora + timedelta(days=7),
        )
    return c
