"""Camada analítica do Grimório — radar de teto, séries mensais e as 3 páginas.

O que estes testes protegem, em ordem de gravidade se quebrar:

1. **Isolamento entre escritórios nos AGREGADOS.** Uma listagem vazada mostra o
   nome do cliente de outro contador e alguém percebe; um total vazado só mostra
   um número maior, e ninguém desconfia. É o teste mais importante do arquivo.
2. **O valor da nota não sumir da soma.** O campo é derivado do payload; se a
   sincronização quebrar, o faturamento cai em silêncio e o radar de teto passa
   a dizer "tranquilo" para quem estourou.
3. As páginas responderem 200 para quem tem permissão e 403 para quem não tem.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.clients.models import Cliente, Perfil
from apps.fiscal import teto_mei
from apps.painel import metricas
from apps.tenants.models import Escritorio, MembroEscritorio


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------
def emitir(cliente, valor, quando=None, chave=None):
    """Cria uma nota já CONCLUIDA. `quando` sobrescreve `atualizado_em`."""
    nota = Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia=chave or f"nota-{cliente.pk}-{valor}-{timezone.now().timestamp()}",
        tipo_acao="emitir_nfse",
        payload={"valor": valor, "tomador": "Fulano", "descricao_servico": "Serviço"},
        estado=Intencao.Estado.CONCLUIDO,
    )
    if quando is not None:
        # `auto_now` ignora atribuição — só update() escapa dele.
        Intencao.objects.filter(pk=nota.pk).update(atualizado_em=quando)
        nota.refresh_from_db()
    return nota


@pytest.fixture
def mei(db, escritorio):
    cliente = Cliente.objects.create(
        escritorio=escritorio,
        cnpj="11222333000181",
        nome="Ana Paula Cabeleireira MEI",
        telefone_whatsapp="5511900000001",
        opcao_simples_nacional=Cliente.OpcaoSimplesNacional.MEI,
    )
    Perfil.objects.create(cliente=cliente, persona="lumen", tier_maximo=1)
    return cliente


@pytest.fixture
def contador(db, escritorio):
    usuario = get_user_model().objects.create_user(
        username="contador.analitico", email="c@rotina.example.com", is_staff=True
    )
    MembroEscritorio.objects.create(usuario=usuario, escritorio=escritorio)
    return usuario


@pytest.fixture
def superusuario(db):
    return get_user_model().objects.create_superuser(
        username="equipe.magicbi", email="equipe@magicbi.example.com", password="x"
    )


# ---------------------------------------------------------------------------
# 1. Valor derivado do payload
# ---------------------------------------------------------------------------
class TestValorDerivado:
    def test_valor_e_preenchido_na_criacao(self, cliente):
        nota = emitir(cliente, 750.0)
        assert nota.valor == Decimal("750.00")

    def test_valor_em_string_tambem_entra_na_soma(self, cliente):
        """O LLM devolve ora número, ora string — os dois têm que somar igual.

        Este é o caso que motivou a coluna: um cast de JSON falharia aqui e
        tiraria a nota do total sem erro nenhum.
        """
        nota = emitir(cliente, "1250.50")
        assert nota.valor == Decimal("1250.50")

    def test_payload_sem_valor_fica_nulo_e_nao_zero(self, cliente):
        nota = Intencao.objects.create(
            cliente=cliente, chave_idempotencia="sem-valor", payload={"tomador": "X"}
        )
        assert nota.valor is None

    def test_valor_sobrevive_a_transicao_de_estado(self, cliente):
        """`transicionar()` salva com `update_fields` estreito — se o campo não
        entrasse nessa lista, a nota perderia o valor exatamente ao ser emitida."""
        nota = Intencao.objects.create(
            cliente=cliente,
            chave_idempotencia="transicao-valor",
            payload={"valor": 300},
            estado=Intencao.Estado.RECEBIDO,
        )
        Intencao.objects.filter(pk=nota.pk).update(valor=None)  # simula base antiga
        nota.refresh_from_db()

        nota.transicionar(Intencao.Estado.VALIDANDO)

        nota.refresh_from_db()
        assert nota.valor == Decimal("300.00")


# ---------------------------------------------------------------------------
# 2. Radar de teto do MEI
# ---------------------------------------------------------------------------
class TestTetoMei:
    def test_nao_se_aplica_a_quem_nao_e_mei(self, cliente):
        uso = teto_mei.avaliar(cliente, Decimal("500000.00"))
        assert uso.aplicavel is False

    @pytest.mark.parametrize(
        "faturamento,esperado",
        [
            ("10000.00", "tranquilo"),
            ("60000.00", "atencao"),  # 74%
            ("75000.00", "critico"),  # 92%
            ("85000.00", "estourado"),  # acima do teto, dentro dos 20%
            ("100000.00", "estourado_grave"),  # acima de R$ 97.200
        ],
    )
    def test_faixas(self, mei, faturamento, esperado):
        uso = teto_mei.avaliar(mei, Decimal(faturamento), ano=2026)
        assert uso.situacao == esperado

    def test_teto_proporcional_no_ano_de_abertura(self, mei):
        """Abriu em julho → 6 meses ativos → metade do teto.

        Sem isso, um MEI aberto em julho que faturou R$ 45 mil apareceria como
        'tranquilo' (55%) quando na verdade já passou do teto proporcional dele.
        """
        mei.data_inicio_atividade = date(2026, 7, 1)
        mei.save()

        uso = teto_mei.avaliar(mei, Decimal("45000.00"), ano=2026)

        assert uso.proporcional is True
        assert uso.teto == Decimal("40500.00")  # 81.000 / 12 * 6
        assert uso.situacao == "estourado"

    def test_abertura_em_ano_anterior_usa_teto_cheio(self, mei):
        mei.data_inicio_atividade = date(2019, 3, 10)
        mei.save()

        uso = teto_mei.avaliar(mei, Decimal("45000.00"), ano=2026)

        assert uso.proporcional is False
        assert uso.teto == Decimal("81000.00")

    def test_sempre_marcado_como_parcial(self, mei):
        """Só enxergamos nota emitida pelo Magic BI — a tela precisa dizer isso."""
        assert teto_mei.avaliar(mei, Decimal("1000.00")).parcial is True


# ---------------------------------------------------------------------------
# 3. Métricas
# ---------------------------------------------------------------------------
class TestMetricas:
    def test_pedido_de_cancelamento_nao_conta_como_nota(self, cliente, superusuario):
        """O pedido de cancelamento é uma Intencao própria que também chega a
        CONCLUIDO. Contá-lo dobraria a nota e somaria o valor duas vezes."""
        nota = emitir(cliente, 500.0, chave="orig-1")
        Intencao.objects.create(
            cliente=cliente,
            chave_idempotencia="cancel-1",
            tipo_acao="cancelar_nfse",
            intencao_original=nota,
            payload={"valor": 500.0, "motivo": "erro"},
            estado=Intencao.Estado.CONCLUIDO,
        )

        assert metricas.notas_emitidas(superusuario).count() == 1

    def test_serie_mensal_preenche_meses_sem_nota_com_zero(self, cliente, superusuario):
        hoje = timezone.now()
        emitir(cliente, 100.0, quando=hoje, chave="s-1")

        serie = metricas.serie_mensal(superusuario, meses=6)

        assert len(serie["rotulos"]) == 6
        assert len(serie["faturamentos"]) == 6
        assert serie["faturamentos"][-1] == 100.0
        assert serie["faturamentos"][0] == 0.0  # mês vazio aparece, não some

    def test_carteira_agrega_por_cliente(self, mei, superusuario):
        emitir(mei, 1000.0, chave="c-1")
        emitir(mei, 2500.0, chave="c-2")

        linha = next(l for l in metricas.carteira(superusuario) if l.cliente == mei)

        assert linha.notas_ano == 2
        assert linha.faturamento_ano == Decimal("3500.00")
        assert linha.uso_teto.aplicavel is True

    def test_integracoes_lista_pendencias_em_portugues(self, mei, superusuario):
        linha = next(l for l in metricas.integracoes(superusuario) if l.cliente == mei)

        assert "sem certificado digital" in linha.pendencias
        assert "sem ERP conectado" in linha.pendencias
        assert "WhatsApp não vinculado" in linha.pendencias

    def test_documentos_agrupa_por_mes(self, cliente, superusuario):
        emitir(cliente, 100.0, quando=timezone.now(), chave="d-1")
        emitir(cliente, 200.0, quando=timezone.now() - timedelta(days=60), chave="d-2")

        grupos = metricas.documentos(superusuario)

        assert len(grupos) == 2


# ---------------------------------------------------------------------------
# 4. Isolamento entre escritórios — o teste que mais importa
# ---------------------------------------------------------------------------
class TestIsolamentoNosAgregados:
    @pytest.fixture
    def outro_escritorio_com_nota(self, db):
        outro = Escritorio.objects.create(nome="Concorrente", slug="concorrente", ativo=True)
        cliente = Cliente.objects.create(
            escritorio=outro,
            cnpj="99888777000166",
            nome="Cliente do Concorrente",
            telefone_whatsapp="5511911111111",
            opcao_simples_nacional=Cliente.OpcaoSimplesNacional.MEI,
        )
        emitir(cliente, 90000.0, chave="conc-1")
        return outro

    def test_faturamento_nao_soma_nota_de_outro_escritorio(
        self, mei, contador, outro_escritorio_com_nota
    ):
        emitir(mei, 1000.0, chave="meu-1")

        serie = metricas.serie_mensal(contador)

        # R$ 90 mil do concorrente não podem aparecer em nenhum mês da série.
        assert sum(serie["faturamentos"]) == 1000.0

    def test_carteira_nao_lista_cliente_de_outro_escritorio(
        self, mei, contador, outro_escritorio_com_nota
    ):
        nomes = [linha.cliente.nome for linha in metricas.carteira(contador)]

        assert "Cliente do Concorrente" not in nomes

    def test_usuario_sem_vinculo_nao_ve_nada(self, mei, db):
        """Staff meio-provisionado nunca pode significar 'acesso total'."""
        avulso = get_user_model().objects.create_user(
            username="staff.sem.vinculo", is_staff=True
        )
        emitir(mei, 1000.0, chave="sv-1")

        assert metricas.carteira(avulso) == []
        assert sum(metricas.serie_mensal(avulso)["faturamentos"]) == 0.0

    def test_alertas_de_teto_respeitam_o_escopo(
        self, mei, contador, outro_escritorio_com_nota
    ):
        """O cliente do concorrente está com 90 mil (estourado) — não pode
        aparecer no radar de ninguém além do escritório dele."""
        alertas = metricas.alertas_de_teto(contador)

        assert all(linha.cliente.escritorio.slug == "rotina" for linha in alertas)


# ---------------------------------------------------------------------------
# 5. As páginas respondem
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "/admin/clients/cliente/carteira/",
        "/admin/credentials/credencial/integracoes/",
        "/admin/agente_nf/intencao/documentos/",
    ],
)
class TestPaginas:
    def test_abrem_para_a_equipe(self, client, superusuario, mei, url):
        emitir(mei, 1234.56, chave=f"pag-{url}")
        client.force_login(superusuario)

        resposta = client.get(url)

        assert resposta.status_code == 200

    def test_recusam_anonimo(self, client, url):
        resposta = client.get(url)

        # admin_view redireciona pro login em vez de entregar a página
        assert resposta.status_code in (301, 302)
        assert "/admin/login/" in resposta["Location"] or "/entrar/" in resposta["Location"]

    def test_nao_colidem_com_a_rota_de_edicao(self, client, superusuario, mei, url):
        """As URLs próprias vêm ANTES do catch-all `<path:object_id>/` do admin.

        Se a ordem inverter, "carteira" vira um id de objeto e a página some
        num 404 — sem erro nenhum no log que explique.
        """
        client.force_login(superusuario)

        assert client.get(url).status_code == 200


class TestRotulosRenderizam:
    """O componente de rótulo do Unfold imprime `{{ text }}` — ele NÃO renderiza
    o conteúdo entre as tags. Passar o texto como filho produz uma pílula
    colorida **vazia**: o layout parece certo, a informação sumiu. Aconteceu
    nas três páginas e só apareceu em screenshot, porque status 200 continuava.
    """

    def test_carteira_mostra_o_texto_dos_rotulos(self, client, superusuario, mei):
        client.force_login(superusuario)

        conteudo = client.get("/admin/clients/cliente/carteira/").content.decode()

        assert "falta cadastro" in conteudo or "pronto para emitir" in conteudo
        assert "WhatsApp" in conteudo

    def test_integracoes_mostra_o_texto_dos_rotulos(self, client, superusuario, mei):
        client.force_login(superusuario)

        conteudo = client.get("/admin/credentials/credencial/integracoes/").content.decode()

        assert "sem sessão" in conteudo

    def test_documentos_mostra_o_texto_dos_rotulos(self, client, superusuario, mei):
        emitir(mei, 100.0, chave="rot-doc")
        client.force_login(superusuario)

        conteudo = client.get("/admin/agente_nf/intencao/documentos/").content.decode()

        assert "autorizada" in conteudo

    def test_ano_nao_sai_com_separador_de_milhar(self, client, superusuario, mei):
        """Com USE_THOUSAND_SEPARATOR ligado, `{{ ano }}` como int vira "2.026"."""
        client.force_login(superusuario)
        ano = timezone.localdate().year

        conteudo = client.get("/admin/clients/cliente/carteira/").content.decode()

        assert str(ano) in conteudo
        assert f"{ano // 1000}.{ano % 1000:03d}" not in conteudo


class TestPaginaCarteiraIsolada:
    def test_contador_nao_ve_cliente_de_outro_escritorio_na_pagina(
        self, client, contador, mei, db
    ):
        outro = Escritorio.objects.create(nome="Outro", slug="outro", ativo=True)
        Cliente.objects.create(
            escritorio=outro,
            cnpj="55444333000122",
            nome="Segredo Industrial Ltda",
            telefone_whatsapp="5511922222222",
        )
        contador.user_permissions.add(*Permission.objects.filter(codename="view_cliente"))
        client.force_login(contador)

        conteudo = client.get("/admin/clients/cliente/carteira/").content.decode()

        assert "Ana Paula" in conteudo
        assert "Segredo Industrial" not in conteudo
