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
from django.urls import reverse
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


# ---------------------------------------------------------------------------
# Vincular certificado — a primeira escrita do Grimório que lida com SEGREDO
# ---------------------------------------------------------------------------
@pytest.fixture
def pfx():
    """Certificado autoassinado em memória, com o CNPJ da empresa 'alfa'."""
    from tests.test_credencial_certificado import _gerar_pfx_teste

    return _gerar_pfx_teste(cnpj="11111111000111", senha="senha-do-certificado")


def enviar_certificado(client, cliente, conteudo, senha, nome="cert.pfx"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        f"/grimorio/empresa/{cliente.pk}/certificado/",
        {"pfx": SimpleUploadedFile(nome, conteudo), "senha": senha},
    )


@pytest.mark.django_db
class TestVincularCertificado:
    def test_vincula_e_grava_validade(self, client, dois_escritorios_com_chamado, pfx):
        from apps.credentials.models import Credencial

        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        resposta = enviar_certificado(client, cliente, pfx, "senha-do-certificado")

        assert resposta.status_code == 302
        with rls.escopo_irrestrito():
            credencial = Credencial.objects.get(cliente=cliente)
        assert credencial.certificado_validade is not None
        assert credencial.certificado_cnpj == cliente.cnpj

    def test_a_senha_nunca_aparece_em_lugar_nenhum(
        self, client, dois_escritorios_com_chamado, pfx
    ):
        """A regra que justifica esta tela chamar o serviço do cofre em vez de
        implementar guarda própria: o segredo tem um destino só."""
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        enviar_certificado(client, cliente, pfx, "senha-do-certificado")

        with rls.escopo_irrestrito():
            trilha = " ".join(
                str(e.dados) for e in Auditoria.objects.all()
            )
        assert "senha-do-certificado" not in trilha

        corpo = client.get(f"/grimorio/empresa/{cliente.pk}/certificado/").content.decode()
        assert "senha-do-certificado" not in corpo

    def test_senha_errada_nao_grava_nada(self, client, dois_escritorios_com_chamado, pfx):
        """O `.pfx` é aberto ANTES de tocar o banco — senha errada não deixa
        credencial pela metade."""
        from apps.credentials.models import Credencial

        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        resposta = enviar_certificado(client, cliente, pfx, "senha-errada")

        assert resposta.status_code == 302
        with rls.escopo_irrestrito():
            credencial = Credencial.objects.get(cliente=cliente)
        assert credencial.certificado_validade is None
        assert not credencial.pfx_arquivo_cifrado

    def test_arquivo_grande_demais_e_recusado(
        self, client, dois_escritorios_com_chamado
    ):
        """Um `.pfx` tem alguns KB. O limite impede que um upload enorme seja
        lido inteiro na memória de um contêiner compartilhado."""
        from apps.credentials.models import Credencial

        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        enviar_certificado(client, cliente, b"x" * (2 * 1024 * 1024), "qualquer")

        with rls.escopo_irrestrito():
            assert not Credencial.objects.filter(cliente=cliente).exists()

    def test_cnpj_divergente_avisa_e_nao_bloqueia(
        self, client, dois_escritorios_com_chamado
    ):
        """Mesma regra do admin — a AC pode fugir do padrão de CN e matriz/filial
        dividem raiz. O que muda é a ênfase: emitir com certificado de outro CNPJ
        assina a nota em nome da outra empresa, e o contador tem que ver isso."""
        from tests.test_credencial_certificado import _gerar_pfx_teste

        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))
        outro = _gerar_pfx_teste(cnpj="99999999000199", senha="senha-do-certificado")

        resposta = enviar_certificado(client, cliente, outro, "senha-do-certificado")
        corpo = client.get(resposta["Location"]).content.decode()

        assert "diferente do cadastro" in corpo.lower() or "99.999.999" in corpo

    def test_nao_vincula_em_empresa_de_outro_escritorio(
        self, client, dois_escritorios_com_chamado, pfx
    ):
        escritorio_alfa, _, _ = dois_escritorios_com_chamado["alfa"]
        _, cliente_beta, _ = dois_escritorios_com_chamado["beta"]
        client.force_login(_contador(escritorio_alfa, "contador.alfa"))

        resposta = enviar_certificado(client, cliente_beta, pfx, "senha-do-certificado")

        assert resposta.status_code == 404
        from apps.credentials.models import Credencial

        with rls.escopo_irrestrito():
            assert not Credencial.objects.filter(cliente=cliente_beta).exists()

    def test_a_fila_leva_a_ficha_da_empresa_ja_escolhida(
        self, client, dois_escritorios_com_chamado
    ):
        """O botão mais frequente da fila — uma linha por empresa sem
        certificado. O que ele nunca pode fazer é largar o contador numa tela
        onde ele precise escolher o cliente na mão: escolher errado vincula o
        certificado de uma empresa a outra.

        **A decisão de PARA ONDE ele leva mudou em 12/ago/2026** e este teste
        mudou junto. Antes apontava para a ficha do Grimório e afirmava, aqui
        mesmo, que NÃO devia ir ao admin. Com a fusão pedida pelo usuário —
        uma superfície só — o destino certo passou a ser a ficha do admin. O
        que continua valendo, e é o que o teste protege de verdade, é que o
        link chega com a empresa já resolvida.
        """
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        corpo = client.get(reverse("admin:index")).content.decode()

        assert f"/admin/clients/cliente/{cliente.pk}/change/" in corpo
        # A asserção-espelho ("não aparece /credencial/add/") foi retirada, e
        # não por conveniência: o índice do admin lista "Adicionar" para todo
        # model, então procurá-la na página inteira deixou de dizer qualquer
        # coisa sobre o botão da fila. Um teste que sempre falha por um motivo
        # que não é o testado é pior que teste nenhum — some com o sinal.


# ---------------------------------------------------------------------------
# Completar cadastro fiscal
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCadastroFiscal:
    def rota(self, cliente):
        return f"/grimorio/empresa/{cliente.pk}/cadastro/"

    def test_salva_e_libera_a_emissao(self, client, dois_escritorios_com_chamado):
        from apps.fiscal.dps import conferir_cadastro

        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))
        assert conferir_cadastro(cliente)  # começa incompleto

        resposta = client.post(
            self.rota(cliente),
            {
                "codigo_municipio_ibge": "3550308",
                "codigo_tributacao_nacional": "010101",
                "cnae_padrao": "6201-5/01",
            },
        )

        assert resposta.status_code == 302
        cliente.refresh_from_db()
        assert conferir_cadastro(cliente) == []

    def test_valor_invalido_nao_grava_nada(self, client, dois_escritorios_com_chamado):
        """A validação é a MESMA da emissão (`conferir_cadastro`), rodada sobre o
        objeto ainda não salvo. Regra própria aqui deixaria a tela dizer "pronto"
        com a emissão ainda falhando — pior que não ter a tela."""
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(
            self.rota(cliente),
            {
                "codigo_municipio_ibge": "355",          # curto demais
                "codigo_tributacao_nacional": "010101",
                "cnae_padrao": "6201-5/01",
            },
        )

        cliente.refresh_from_db()
        assert cliente.codigo_municipio_ibge != "355"

    def test_o_erro_explica_a_confusao_mais_comum(
        self, client, dois_escritorios_com_chamado
    ):
        """Quem preenche o cTribNac com o CNAE é o caso mais frequente — a
        mensagem precisa dizer isso, não só "inválido"."""
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(
            self.rota(cliente),
            {
                "codigo_municipio_ibge": "3550308",
                "codigo_tributacao_nacional": "6201501",  # 7 dígitos: é CNAE
                "cnae_padrao": "6201-5/01",
            },
        )
        corpo = client.get(self.rota(cliente)).content.decode()
        assert "não é o CNAE" in corpo or "nao e o CNAE" in corpo

    def test_registra_quem_mexeu_no_cadastro(self, client, dois_escritorios_com_chamado):
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(
            self.rota(cliente),
            {
                "codigo_municipio_ibge": "3550308",
                "codigo_tributacao_nacional": "010101",
                "cnae_padrao": "6201-5/01",
            },
        )

        with rls.escopo_irrestrito():
            evento = Auditoria.objects.get(evento="cadastro_fiscal_atualizado")
        assert evento.dados["por"] == "contador.alfa"
        assert "codigo_municipio_ibge" in evento.dados["campos"]

    def test_nao_edita_cadastro_de_outro_escritorio(
        self, client, dois_escritorios_com_chamado
    ):
        escritorio_alfa, _, _ = dois_escritorios_com_chamado["alfa"]
        _, cliente_beta, _ = dois_escritorios_com_chamado["beta"]
        client.force_login(_contador(escritorio_alfa, "contador.alfa"))

        resposta = client.post(
            self.rota(cliente_beta), {"codigo_municipio_ibge": "3550308"}
        )

        assert resposta.status_code == 404
        cliente_beta.refresh_from_db()
        assert cliente_beta.codigo_municipio_ibge != "3550308"

    def test_o_cnpj_nao_e_editavel_por_aqui(self, client, dois_escritorios_com_chamado):
        """O CNPJ identifica a empresa e casa com o certificado — trocá-lo é
        outra operação, com outras consequências."""
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))
        original = cliente.cnpj

        client.post(
            self.rota(cliente),
            {
                "cnpj": "99999999000199",
                "codigo_municipio_ibge": "3550308",
                "codigo_tributacao_nacional": "010101",
                "cnae_padrao": "6201-5/01",
            },
        )

        cliente.refresh_from_db()
        assert cliente.cnpj == original

    def test_a_fila_leva_ao_cadastro_da_empresa_certa(
        self, client, dois_escritorios_com_chamado
    ):
        """Mesma inversão de 12/ago/2026: o destino agora é o admin. O que o
        teste guarda é o invariante — a empresa vai resolvida no link."""
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        corpo = client.get(reverse("admin:index")).content.decode()

        assert f"/admin/clients/cliente/{cliente.pk}/change/" in corpo


# ---------------------------------------------------------------------------
# Decidir nota — a única escrita que toca documento fiscal
# ---------------------------------------------------------------------------
@pytest.fixture
def nota_pendente(dois_escritorios_com_chamado):
    """Uma nota aguardando decisão em cada escritório."""
    from apps.agents.agente_nf.models import Intencao

    pendentes = {}
    with rls.escopo_irrestrito():
        for slug, (_, cliente, _) in dois_escritorios_com_chamado.items():
            pendentes[slug] = Intencao.objects.create(
                cliente=cliente,
                chave_idempotencia=f"decidir-{slug}",
                tipo_acao="emitir_nfse",
                payload={
                    "cnpj_prestador": cliente.cnpj,
                    "cnae": "6201-5/01",
                    "valor": 500.0,
                    "descricao_servico": "Consultoria",
                    "tomador": "Tomador Ltda",
                },
                valor=500,
                estado=Intencao.Estado.AGUARDANDO_APROVACAO,
            )
    return pendentes


@pytest.mark.django_db
class TestDecidirNota:
    def test_a_fila_leva_a_nota_exata_que_espera_decisao(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        """Mesma inversão de 12/ago/2026. O invariante que sobrevive: o link
        abre A nota parada, não uma listagem onde ela precisa ser procurada."""
        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        corpo = client.get(reverse("admin:index")).content.decode()

        assert f"/admin/agente_nf/intencao/{nota.pk}/change/" in corpo

    def test_a_tela_mostra_o_que_sera_emitido(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        """Conferência de verdade: tomador, valor, serviço e o CNAE do cadastro —
        que é o campo que o modelo de IA nunca decide."""
        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        corpo = client.get(f"/grimorio/notas/{nota.pk}/").content.decode()

        assert "Tomador Ltda" in corpo
        assert "Consultoria" in corpo
        assert "6201-5/01" in corpo

    def test_aprovar_emite_pelo_servico_auditado(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        from apps.agents.agente_nf.models import Intencao

        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(f"/grimorio/notas/{nota.pk}/decidir/", {"acao": "aprovar"})

        nota.refresh_from_db()
        assert nota.estado in (Intencao.Estado.CONCLUIDO, Intencao.Estado.REJEITADO)
        # A máquina de estados continua sendo a mesma: a transição foi registrada
        # na trilha encadeada, com quem decidiu.
        with rls.escopo_irrestrito():
            motivos = [
                (e.dados or {}).get("motivo", "")
                for e in Auditoria.objects.filter(evento="intencao_fiscal_transicao")
            ]
        assert any("contador.alfa" in m for m in motivos)

    def test_recusar_nao_manda_nada_para_a_prefeitura(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        from apps.agents.agente_nf.models import Intencao

        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(f"/grimorio/notas/{nota.pk}/decidir/", {"acao": "recusar"})

        nota.refresh_from_db()
        assert nota.estado == Intencao.Estado.CANCELADO
        assert not nota.protocolo

    def test_get_nao_decide(self, client, dois_escritorios_com_chamado, nota_pendente):
        from apps.agents.agente_nf.models import Intencao

        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        resposta = client.get(f"/grimorio/notas/{nota.pk}/decidir/")

        assert resposta.status_code == 405
        nota.refresh_from_db()
        assert nota.estado == Intencao.Estado.AGUARDANDO_APROVACAO

    def test_nao_decide_nota_de_outro_escritorio(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        """Aqui não é ler demais: é emitir documento fiscal em nome de terceiro."""
        from apps.agents.agente_nf.models import Intencao

        escritorio_alfa, _, _ = dois_escritorios_com_chamado["alfa"]
        nota_beta = nota_pendente["beta"]
        client.force_login(_contador(escritorio_alfa, "contador.alfa"))

        assert client.get(f"/grimorio/notas/{nota_beta.pk}/").status_code == 404
        assert (
            client.post(
                f"/grimorio/notas/{nota_beta.pk}/decidir/", {"acao": "aprovar"}
            ).status_code
            == 404
        )
        nota_beta.refresh_from_db()
        assert nota_beta.estado == Intencao.Estado.AGUARDANDO_APROVACAO

    def test_decidir_duas_vezes_avisa_em_vez_de_repetir(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        """Dois contadores na mesma fila, ou o cliente confirmando pelo WhatsApp
        com esta tela aberta. É corrida, não erro — e emitir duas vezes seria o
        estrago que a checagem de estado evita."""
        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        client.post(f"/grimorio/notas/{nota.pk}/decidir/", {"acao": "recusar"})
        resposta = client.post(f"/grimorio/notas/{nota.pk}/decidir/", {"acao": "aprovar"})

        assert resposta.status_code == 302
        nota.refresh_from_db()
        from apps.agents.agente_nf.models import Intencao

        assert nota.estado == Intencao.Estado.CANCELADO

    def test_nota_ja_decidida_nao_mostra_os_botoes(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        escritorio, _, _ = dois_escritorios_com_chamado["alfa"]
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))
        client.post(f"/grimorio/notas/{nota.pk}/decidir/", {"acao": "recusar"})

        corpo = client.get(f"/grimorio/notas/{nota.pk}/").content.decode()

        assert "Sua decisão" not in corpo

    def test_avisa_antes_do_clique_quando_o_cadastro_impede_emitir(
        self, client, dois_escritorios_com_chamado, nota_pendente
    ):
        """Sem isto o contador descobria a falha DEPOIS de aprovar — com o susto
        de achar que tinha emitido. A checagem é a mesma da emissão."""
        escritorio, cliente, _ = dois_escritorios_com_chamado["alfa"]
        with rls.escopo_irrestrito():
            cliente.codigo_municipio_ibge = ""
            cliente.save(update_fields=["codigo_municipio_ibge"])
        nota = nota_pendente["alfa"]
        client.force_login(_contador(escritorio, "contador.alfa"))

        corpo = client.get(f"/grimorio/notas/{nota.pk}/").content.decode()

        assert "Aprovar vai falhar" in corpo
        assert f"/grimorio/empresa/{cliente.pk}/cadastro/" in corpo
