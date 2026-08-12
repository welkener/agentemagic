"""
Recebimento e revisão de documento — Sprint 4.

**A ordem escolhida é o assunto deste arquivo.** A fila de revisão foi construída
antes da leitura automática, e o pipeline funcionou ponta a ponta com 100% de
revisão humana antes de qualquer documento sair dela sozinho. Com a fase 2, o que
traz prova junto encurta a fila; o resto continua exatamente onde estava. O
caminho inverso — ligar a leitura primeiro e ir corrigindo — começaria por
lançamento automático de dado não conferido, que é o que o gate do sprint proíbe.

A leitura em si está sob teste em `test_extracao.py`. O que se prova aqui é o
gate: nada abaixo do limiar sai da fila, e o que sai continua visível e
contestável.

O storage é dublado. Testar contra um MinIO de verdade tornaria a suíte
dependente de um contêiner de pé — e o que precisa estar sob teste é a lógica de
quem chama, não o boto3: a ordem (arquivo antes da linha), o reenvio, o escopo e
a eliminação por LGPD.
"""
import hashlib

import pytest
from django.utils import timezone

from apps.agents.contexto import SessionContext
from apps.documentos import armazenamento, services
from apps.documentos.models import Documento
from apps.tenants import rls

CONTEUDO = b"%PDF-1.4 conteudo de teste da nota"


@pytest.fixture
def storage(monkeypatch):
    """Storage em memória, com a mesma interface do módulo real."""
    guardado: dict[str, bytes] = {}

    def falso_guardar(cliente, conteudo, nome_arquivo, tipo_mime=""):
        chave = armazenamento.chave_do_documento(cliente, nome_arquivo)
        bucket = armazenamento.bucket_do_escritorio(cliente.escritorio)
        guardado[f"{bucket}/{chave}"] = conteudo
        return armazenamento.ArquivoGuardado(
            bucket=bucket,
            chave=chave,
            tamanho=len(conteudo),
            hash_sha256=hashlib.sha256(conteudo).hexdigest(),
        )

    monkeypatch.setattr(armazenamento, "habilitado", lambda: True)
    monkeypatch.setattr(armazenamento, "guardar", falso_guardar)
    monkeypatch.setattr(
        armazenamento, "apagar", lambda b, c: guardado.pop(f"{b}/{c}", None)
    )
    monkeypatch.setattr(
        armazenamento, "url_temporaria", lambda b, c, segundos=300: f"https://storage.test/{b}/{c}"
    )
    monkeypatch.setattr(armazenamento, "baixar", lambda b, c: guardado[f"{b}/{c}"])
    # Padrão: storage com endereço público (AWS, ou MinIO publicado). O caso
    # contrário — o do servidor de hoje — tem teste próprio, que o inverte.
    monkeypatch.setattr(armazenamento, "alcancavel_pelo_navegador", lambda: True)
    return guardado


def ctx_de(cliente, **kwargs):
    return SessionContext.da_conversa(cliente=cliente, **kwargs)


# ---------------------------------------------------------------------------
# Endereçamento — bucket por tenant, prefixo por cliente
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestEndereco:
    def test_o_bucket_carrega_o_slug_do_escritorio(self, cliente):
        nome = armazenamento.bucket_do_escritorio(cliente.escritorio)
        assert nome.startswith("magicbi-")
        assert cliente.escritorio.slug in nome

    def test_o_nome_do_bucket_sobrevive_a_acento_e_espaco(self, escritorio):
        """Nome de bucket na AWS não aceita acento, maiúscula nem espaço — e o
        escritório se chama "Contábil São José" com frequência maior que zero."""
        escritorio.slug = "Contábil São José"
        assert armazenamento.bucket_do_escritorio(escritorio) == "magicbi-contabil-sao-jose"

    def test_a_chave_separa_por_cliente_ano_e_mes(self, cliente):
        chave = armazenamento.chave_do_documento(cliente, "nota fiscal.pdf")
        agora = timezone.now()
        assert chave.startswith(f"{cliente.pk}/{agora:%Y}/{agora:%m}/")
        assert chave.endswith(".pdf")


# ---------------------------------------------------------------------------
# Endereço público — os dois lados do host
# ---------------------------------------------------------------------------
class TestEnderecoPublico:
    """Este bloco existe por causa de um defeito que só a produção mostrou.

    A primeira prova no servidor guardou, listou e baixou o arquivo — tudo
    verde — e mesmo assim o botão "Abrir" do Grimório estava quebrado: a URL
    assinada saía com `http://minio:9000`, que resolve dentro do compose e em
    lugar nenhum além dele. O teste com storage dublado não podia pegar isso,
    porque o dublê devolve a string que mandarem. Estes rodam contra a função de
    verdade.
    """

    def test_minio_sem_endereco_publico_nao_e_alcancavel(self, settings):
        settings.S3_ENDPOINT = "http://minio:9000"
        settings.S3_ENDPOINT_PUBLICO = ""
        assert armazenamento.alcancavel_pelo_navegador() is False

    def test_minio_publicado_e_alcancavel(self, settings):
        settings.S3_ENDPOINT = "http://minio:9000"
        settings.S3_ENDPOINT_PUBLICO = "https://arquivos.exemplo.com.br"
        assert armazenamento.alcancavel_pelo_navegador() is True

    def test_na_aws_e_alcancavel_sem_configurar_nada(self, settings):
        """A migração não deve exigir lembrar deste ajuste: sem endpoint, a URL
        assinada já é pública."""
        settings.S3_ENDPOINT = ""
        settings.S3_ENDPOINT_PUBLICO = ""
        settings.S3_USAR_AWS = True
        assert armazenamento.alcancavel_pelo_navegador() is True

    def test_sem_storage_nenhum_nao_ha_o_que_alcancar(self, settings):
        settings.S3_ENDPOINT = ""
        settings.S3_ENDPOINT_PUBLICO = ""
        settings.S3_USAR_AWS = False
        assert armazenamento.alcancavel_pelo_navegador() is False

    def test_a_url_assinada_usa_o_host_publico_e_expira_em_5_minutos(self, settings):
        """Assinatura v4 embute o host na conta: assinar contra `minio:9000` e
        pedir noutro endereço devolve 403. Por isso a assinatura é feita contra
        o endereço público, não traduzida depois."""
        settings.S3_ENDPOINT = "http://minio:9000"
        settings.S3_ENDPOINT_PUBLICO = "https://arquivos.exemplo.com.br"
        settings.S3_ACCESS_KEY = "chave"
        settings.S3_SECRET_KEY = "segredo"

        url = armazenamento.url_temporaria("magicbi-teste", "1/2026/08/nota.pdf")

        assert url.startswith("https://arquivos.exemplo.com.br/magicbi-teste/")
        assert "X-Amz-Expires=300" in url
        assert "X-Amz-Signature=" in url


# ---------------------------------------------------------------------------
# Recebimento
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRecebimento:
    def test_guarda_o_arquivo_e_registra_o_documento(self, cliente, storage):
        documento, novo = services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="nota.pdf",
            tipo_mime="application/pdf",
        )

        assert novo is True
        assert documento.situacao == Documento.Situacao.AGUARDANDO_REVISAO
        assert documento.confianca == 0
        assert storage[f"{documento.bucket}/{documento.chave}"] == CONTEUDO

    def test_reenvio_do_mesmo_arquivo_nao_duplica_a_fila(self, cliente, storage):
        """O cliente que não viu o recibo manda de novo — e não pode encher a
        fila do contador com a mesma nota."""
        primeiro, _ = services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="nota.pdf"
        )
        segundo, novo = services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="nota-copia.pdf"
        )

        assert novo is False
        assert segundo.pk == primeiro.pk
        with rls.escopo_irrestrito():
            assert Documento.objects.filter(cliente=cliente).count() == 1

    def test_o_recibo_diz_o_que_ainda_NAO_aconteceu(self, cliente, storage):
        """Prometer lançamento aqui seria prometer o que só vem depois da
        revisão — e o cliente que acredita nisso para de cobrar."""
        documento, novo = services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="nota.pdf"
        )
        recibo = services.recibo(documento, novo)

        assert documento.protocolo in recibo
        assert "conferir" in recibo.lower()
        assert "lançad" not in recibo.lower()

    def test_arquivo_grande_demais_e_recusado_com_orientacao(self, cliente, storage):
        with pytest.raises(services.ErroDeRecebimento, match="grande demais"):
            services.receber(
                ctx=ctx_de(cliente),
                conteudo=b"x" * (services.TAMANHO_MAXIMO + 1),
                nome_arquivo="video.mp4",
            )

    def test_sem_storage_configurado_recusa_em_vez_de_perder(self, cliente, monkeypatch):
        """Aceitar e perder é o pior desfecho: quem manda a nota e ouve "recebi"
        precisa que isso seja verdade."""
        monkeypatch.setattr(armazenamento, "habilitado", lambda: False)

        with pytest.raises(services.ErroDeRecebimento, match="não consigo guardar"):
            services.receber(
                ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="nota.pdf"
            )

        with rls.escopo_irrestrito():
            assert not Documento.objects.filter(cliente=cliente).exists()

    def test_o_xml_da_nfe_sai_da_fila_e_o_recibo_diz_o_que_foi_lido(
        self, cliente, storage
    ):
        """O caminho completo da fase 2: chega XML, sai documento classificado.

        E o cliente é informado do que foi entendido — é ele, não o contador,
        quem sabe na hora se mandou o arquivo errado."""
        from tests.test_extracao import CHAVE_DE_TERCEIRO, xml_de

        documento, novo = services.receber(
            ctx=ctx_de(cliente),
            conteudo=xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            nome_arquivo="nfe.xml",
            tipo_mime="text/xml",
        )

        assert documento.situacao == Documento.Situacao.CLASSIFICADO
        assert documento.tipo == Documento.Tipo.NOTA_ENTRADA
        assert documento.confianca == 100
        assert documento.classificado_por_maquina is True
        # Ninguém revisou — e o campo continua vazio, que é o que permite achar
        # depois o que a máquina decidiu sozinha.
        assert documento.revisado_por_id is None

        recibo = services.recibo(documento, novo)
        assert "Distribuidora Norte" in recibo
        assert "R$ 1.240,00" in recibo
        assert "nota de entrada" in recibo.lower()

    def test_arquivo_ilegivel_continua_esperando_humano(self, cliente, storage):
        """O caso mais comum, e o que a fase 2 não muda."""
        documento, _ = services.receber(
            ctx=ctx_de(cliente), conteudo=b"\xff\xd8\xff\xe0jpeg", nome_arquivo="nota.jpg",
            tipo_mime="image/jpeg",
        )

        assert documento.situacao == Documento.Situacao.AGUARDANDO_REVISAO
        assert documento.confianca == 0
        # Registra que tentou e não conseguiu — diferente de nunca ter tentado.
        assert documento.dados_extraidos["metodo"] == "nenhum"

    def test_a_trilha_guarda_quem_decidiu_o_tipo(self, cliente, storage):
        """Pergunta que aparece quando um lançamento sai errado: foi a máquina
        ou foi alguém? Só se responde se for gravado na hora."""
        from apps.audit.models import Auditoria
        from tests.test_extracao import CHAVE_DE_TERCEIRO, xml_de

        services.receber(
            ctx=ctx_de(cliente),
            conteudo=xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            nome_arquivo="nfe.xml", tipo_mime="text/xml",
        )

        with rls.escopo_irrestrito():
            evento = Auditoria.objects.get(evento="documento_recebido")
        assert evento.dados["metodo_leitura"] == "xml_nfe"
        assert evento.dados["confianca"] == 100

    def test_o_nome_do_arquivo_entra_cifrado_na_trilha(self, cliente, storage):
        """"extrato-joao-silva.pdf" é dado do titular. A trilha cifra o campo
        `mensagem` por titular — é por isso que o nome vai por ele."""
        from apps.audit.models import Auditoria

        services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="extrato-joao.pdf"
        )

        with rls.escopo_irrestrito():
            evento = Auditoria.objects.get(evento="documento_recebido")
        assert "extrato-joao.pdf" not in str(evento.dados)


# ---------------------------------------------------------------------------
# O gate do Sprint 4
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestOGate:
    """*Documento com baixa confiança nunca vira lançamento automático.*

    O gate do sprint, exercido diretamente contra `aplicar_extracao` em vez de
    por um caminho de integração. É de propósito: se um dia alguém acrescentar
    um degrau novo à escada — OCR, modelo de visão, o que for —, ele vai passar
    por esta função, e é aqui que a regra precisa continuar valendo, mesmo que
    nenhum teste de integração cubra o degrau novo.
    """

    @pytest.fixture
    def documento(self, cliente, storage):
        doc, _ = services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="qualquer.pdf"
        )
        return doc

    def test_um_ponto_abaixo_do_limiar_nao_sai_da_fila(self, documento):
        from apps.documentos.extracao import LIMIAR_SEM_REVISAO, Extracao

        documento.aplicar_extracao(
            Extracao(
                tipo="boleto",
                confianca=LIMIAR_SEM_REVISAO - 1,
                metodo="algum_degrau_futuro",
                dados={"valor": "9999.00"},
                resumo="boleto de R$ 9.999,00",
            )
        )

        documento.refresh_from_db()
        assert documento.situacao == Documento.Situacao.AGUARDANDO_REVISAO
        # O tipo também não é preenchido: campo já respondido é campo que o
        # contador de plantão aceita sem olhar, e um chute exibido em formulário
        # é um chute vestido de fato.
        assert documento.tipo == Documento.Tipo.DESCONHECIDO
        # O que foi lido fica guardado — vira contexto para quem revisa, não
        # decisão. É a diferença entre informar e concluir.
        assert documento.dados_extraidos["valor"] == "9999.00"
        assert documento.confianca == LIMIAR_SEM_REVISAO - 1

    def test_exatamente_no_limiar_sai(self, documento):
        from apps.documentos.extracao import LIMIAR_SEM_REVISAO, Extracao

        documento.aplicar_extracao(
            Extracao(tipo="boleto", confianca=LIMIAR_SEM_REVISAO, metodo="linha_digitavel")
        )

        documento.refresh_from_db()
        assert documento.situacao == Documento.Situacao.CLASSIFICADO
        assert documento.tipo == Documento.Tipo.BOLETO

    def test_tipo_que_nao_existe_no_cadastro_nao_classifica(self, documento):
        """Confiança alta com tipo inventado não abre a porta: a escada e o
        modelo precisam concordar sobre o vocabulário, e quando não concordam
        quem decide é o humano."""
        from apps.documentos.extracao import Extracao

        documento.aplicar_extracao(
            Extracao(tipo="nota_fiscal_de_servico_municipal", confianca=100, metodo="x")
        )

        documento.refresh_from_db()
        assert documento.situacao == Documento.Situacao.AGUARDANDO_REVISAO

    def test_nada_lido_nao_mexe_na_situacao(self, documento):
        from apps.documentos.extracao import NADA

        documento.aplicar_extracao(NADA)

        documento.refresh_from_db()
        assert documento.aguardando
        assert documento.confianca == 0


# ---------------------------------------------------------------------------
# Recebimento pelo WhatsApp
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPeloWhatsApp:
    def _processar(self, cliente, storage, telefone=None):
        from apps.channel_whatsapp.pipeline import processar

        enviadas = []
        return (
            processar(
                message_id="wamid.doc1",
                telefone=telefone or cliente.telefone_whatsapp,
                texto="",
                enviar_fn=lambda tel, txt: enviadas.append(txt) or True,
                media_id="media-1",
                escritorio=cliente.escritorio,
                documento_fn=lambda mid: (CONTEUDO, "nota.pdf", "application/pdf"),
            ),
            enviadas,
        )

    def test_foto_na_conversa_vira_documento_com_recibo(self, cliente, storage):
        resposta, enviadas = self._processar(cliente, storage)

        assert "Recebi seu documento" in resposta
        assert enviadas and enviadas[0] == resposta
        with rls.escopo_irrestrito():
            assert Documento.objects.filter(cliente=cliente).count() == 1

    def test_numero_desconhecido_nao_guarda_no_chute(self, cliente, storage):
        """Sem saber de quem é o arquivo, guardá-lo seria pôr nota fiscal na
        pasta de alguém no escuro."""
        resposta, _ = self._processar(cliente, storage, telefone="5511900009999")

        assert "não sei de qual empresa" in resposta
        with rls.escopo_irrestrito():
            assert not Documento.objects.exists()

    def test_documento_nao_gasta_o_modelo(self, cliente, storage, settings):
        """Não há nada a rotear: o cliente mandou um arquivo e o sistema guarda.
        Passar isso pela escada de modelo gastaria um LLM para concluir o óbvio
        — e deixaria o desfecho depender de uma classificação que pode errar."""
        from apps.observabilidade.models import ConsumoLLM

        settings.GROQ_API_KEY = "chave-de-teste"
        self._processar(cliente, storage)

        with rls.escopo_irrestrito():
            assert not ConsumoLLM.objects.exists()


# ---------------------------------------------------------------------------
# Revisão no Grimório
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRevisao:
    @pytest.fixture
    def contador(self, cliente):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from apps.tenants.models import MembroEscritorio

        with rls.escopo_irrestrito():
            usuario = get_user_model().objects.create_user(
                username="contador.revisao", email="c@example.com", is_staff=True
            )
            usuario.user_permissions.set(Permission.objects.all())
            MembroEscritorio.objects.create(
                usuario=usuario, escritorio=cliente.escritorio
            )
        return usuario

    @pytest.fixture
    def documento(self, cliente, storage):
        doc, _ = services.receber(
            ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="nota.pdf"
        )
        return doc

    def test_a_fila_mostra_o_que_espera(self, client, contador, documento):
        client.force_login(contador)
        corpo = client.get("/grimorio/revisao/").content.decode()

        assert documento.protocolo in corpo
        assert "nota.pdf" in corpo

    def test_classificar_tira_da_fila(self, client, contador, documento):
        client.force_login(contador)

        client.post(
            f"/grimorio/revisao/{documento.pk}/classificar/",
            {"acao": "classificar", "tipo": Documento.Tipo.NOTA_ENTRADA},
        )

        documento.refresh_from_db()
        assert documento.situacao == Documento.Situacao.CLASSIFICADO
        assert documento.revisado_por_id == contador.pk
        # A fila fica vazia. Procurar o protocolo no HTML seria enganoso — ele
        # aparece na mensagem de confirmação, que é o comportamento certo.
        assert "Nenhum documento aguardando" in client.get(
            "/grimorio/revisao/"
        ).content.decode()

    def test_recusar_registra_o_motivo(self, client, contador, documento):
        client.force_login(contador)

        client.post(
            f"/grimorio/revisao/{documento.pk}/classificar/",
            {"acao": "recusar", "motivo": "foto ilegível"},
        )

        documento.refresh_from_db()
        assert documento.situacao == Documento.Situacao.RECUSADO
        assert "ilegível" in documento.observacao

    def test_get_nao_classifica(self, client, contador, documento):
        client.force_login(contador)
        resposta = client.get(f"/grimorio/revisao/{documento.pk}/classificar/")

        assert resposta.status_code == 405
        documento.refresh_from_db()
        assert documento.aguardando

    def test_o_arquivo_sai_por_url_assinada_e_nao_pelo_django(
        self, client, contador, documento
    ):
        """Megabytes de PDF não atravessam a aplicação — o que passa por aqui é
        a checagem de escopo, não o arquivo."""
        client.force_login(contador)
        resposta = client.get(f"/grimorio/revisao/{documento.pk}/arquivo/")

        assert resposta.status_code == 302
        assert resposta["Location"].startswith("https://storage.test/")

    def test_storage_sem_endereco_publico_serve_o_arquivo_pelo_django(
        self, client, contador, documento, monkeypatch
    ):
        """O caso do servidor de hoje: o MinIO não publica porta, e a URL
        assinada apontaria para `http://minio:9000` — um host que só existe
        dentro da rede do compose. Redirecionar para lá entrega ao contador um
        botão que não abre. Servir pelo Django custa banda; é o preço certo."""
        monkeypatch.setattr(armazenamento, "alcancavel_pelo_navegador", lambda: False)
        client.force_login(contador)

        resposta = client.get(f"/grimorio/revisao/{documento.pk}/arquivo/")

        assert resposta.status_code == 200
        assert resposta.content == CONTEUDO
        assert "inline" in resposta["Content-Disposition"]

    def test_o_que_a_maquina_leu_aparece_para_conferencia(
        self, client, contador, cliente, storage
    ):
        """Classificação automática que ninguém consegue listar é classificação
        que ninguém consegue auditar — e quem responde perante o fisco continua
        sendo o contador."""
        from tests.test_extracao import CHAVE_DE_TERCEIRO, xml_de

        lido, _ = services.receber(
            ctx=ctx_de(cliente), conteudo=xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            nome_arquivo="nfe.xml", tipo_mime="text/xml",
        )
        client.force_login(contador)
        corpo = client.get("/grimorio/revisao/").content.decode()

        assert "Lidos sozinhos" in corpo
        assert lido.protocolo in corpo
        assert "Distribuidora Norte" in corpo

    def test_o_contador_pode_discordar_da_maquina(
        self, client, contador, cliente, storage
    ):
        """O documento classificado por máquina nunca teve ninguém discordando
        dele, e a primeira pessoa a olhar precisa poder."""
        from apps.audit.models import Auditoria
        from tests.test_extracao import CHAVE_DE_TERCEIRO, xml_de

        lido, _ = services.receber(
            ctx=ctx_de(cliente), conteudo=xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            nome_arquivo="nfe.xml", tipo_mime="text/xml",
        )
        client.force_login(contador)

        client.post(
            f"/grimorio/revisao/{lido.pk}/classificar/",
            {"acao": "classificar", "tipo": Documento.Tipo.OUTRO},
        )

        lido.refresh_from_db()
        assert lido.tipo == Documento.Tipo.OUTRO
        assert lido.revisado_por_id == contador.pk
        # Deixa de ser da máquina: já tem autor, e some da lista de conferência.
        assert lido.classificado_por_maquina is False

        with rls.escopo_irrestrito():
            evento = Auditoria.objects.get(evento="documento_classificado")
        # A taxa de discordância é o que diz se a fase 2 pode crescer ou precisa
        # encolher — e ela só existe se for gravada quando acontece.
        assert evento.dados["corrigiu_leitura_automatica"] is True
        assert evento.dados["tipo_anterior"] == Documento.Tipo.NOTA_ENTRADA

    def test_decisao_de_outro_humano_nao_se_desfaz_pela_lista(
        self, client, contador, documento
    ):
        """A permissão de corrigir é da leitura automática, não de tudo: o que
        outro contador decidiu tem autor, hora e trilha, e desfazer isso por um
        formulário de lista apagaria uma decisão sem ninguém saber que existiu."""
        client.force_login(contador)
        client.post(
            f"/grimorio/revisao/{documento.pk}/classificar/",
            {"acao": "classificar", "tipo": Documento.Tipo.NOTA_ENTRADA},
        )

        resposta = client.post(
            f"/grimorio/revisao/{documento.pk}/classificar/",
            {"acao": "classificar", "tipo": Documento.Tipo.CONTRATO},
            follow=True,
        )

        documento.refresh_from_db()
        assert documento.tipo == Documento.Tipo.NOTA_ENTRADA
        assert "já foi revisado" in resposta.content.decode()

    def test_o_contador_envia_o_arquivo_que_chegou_por_email(
        self, client, contador, cliente, storage
    ):
        """A porta de entrada que faltava.

        `Origem.PAINEL` existia no modelo desde o primeiro dia e nada a
        preenchia: só o WhatsApp do cliente punha documento no sistema. Com o
        número desconectado, isso deixava o produto sem como receber nada.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        from tests.test_extracao import CHAVE_DE_TERCEIRO, xml_de

        client.force_login(contador)
        arquivo = SimpleUploadedFile(
            "nota-do-email.xml",
            xml_de(CHAVE_DE_TERCEIRO, "99887766000155"),
            content_type="text/xml",
        )

        resposta = client.post(
            "/grimorio/revisao/enviar/",
            {"cliente": cliente.pk, "arquivo": arquivo},
            follow=True,
        )

        with rls.escopo_irrestrito():
            documento = Documento.objects.get(nome_arquivo="nota-do-email.xml")
        assert documento.origem == Documento.Origem.PAINEL
        assert documento.cliente_id == cliente.pk
        # Mesma leitura do WhatsApp, porque é o mesmo `receber` — uma segunda
        # implementação "só para o painel" divergiria no caminho menos usado.
        assert documento.confianca == 100
        assert documento.tipo == Documento.Tipo.NOTA_ENTRADA
        assert "Distribuidora Norte" in resposta.content.decode()

    def test_o_envio_diz_quando_nao_conseguiu_ler(
        self, client, contador, cliente, storage
    ):
        """Quem sobe um arquivo e lê só "enviado" não sabe se precisa voltar
        para classificar — e é essa dúvida que a fila existe para não criar."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(contador)
        resposta = client.post(
            "/grimorio/revisao/enviar/",
            {
                "cliente": cliente.pk,
                "arquivo": SimpleUploadedFile("scan.jpg", b"nada", content_type="image/jpeg"),
            },
            follow=True,
        )

        corpo = resposta.content.decode()
        assert "Não consegui ler" in corpo
        assert "está na fila" in corpo

    def test_nao_envia_para_empresa_de_outra_carteira(
        self, client, contador, storage, escritorio
    ):
        """Porta de escrita nova é onde escopo costuma vazar: o id do cliente
        vem de um `<select>`, mas quem posta o formulário decide o que manda."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.clients.models import Cliente, Perfil
        from apps.tenants.models import Escritorio

        with rls.escopo_irrestrito():
            outro = Escritorio.objects.create(nome="Vizinho2", slug="vizinho2", ativo=True)
            alheio = Cliente.objects.create(
                escritorio=outro, cnpj="77777777000177", nome="Empresa Alheia",
                email_contato="a@example.com", ativo=True,
            )
            Perfil.objects.create(cliente=alheio, tier_maximo=1)
        client.force_login(contador)

        client.post(
            "/grimorio/revisao/enviar/",
            {
                "cliente": alheio.pk,
                "arquivo": SimpleUploadedFile("x.pdf", CONTEUDO, content_type="application/pdf"),
            },
        )

        with rls.escopo_irrestrito():
            assert not Documento.objects.filter(cliente=alheio).exists()

    def test_id_de_cliente_invalido_nao_derruba_a_tela(
        self, client, contador, storage
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        client.force_login(contador)
        resposta = client.post(
            "/grimorio/revisao/enviar/",
            {
                "cliente": "banana",
                "arquivo": SimpleUploadedFile("x.pdf", CONTEUDO, content_type="application/pdf"),
            },
            follow=True,
        )

        assert resposta.status_code == 200
        assert "Escolha uma empresa" in resposta.content.decode()

    def test_get_no_envio_nao_cria_nada(self, client, contador):
        client.force_login(contador)

        assert client.get("/grimorio/revisao/enviar/").status_code == 405

    def test_nao_revisa_documento_de_outro_escritorio(
        self, client, contador, storage, escritorio
    ):
        from apps.clients.models import Cliente, Perfil
        from apps.tenants.models import Escritorio

        with rls.escopo_irrestrito():
            outro = Escritorio.objects.create(nome="Vizinho", slug="vizinho", ativo=True)
            alheio = Cliente.objects.create(
                escritorio=outro, cnpj="88888888000188", nome="Empresa Vizinha",
                email_contato="v@example.com", ativo=True,
            )
            Perfil.objects.create(cliente=alheio, tier_maximo=1)
        doc, _ = services.receber(
            ctx=ctx_de(alheio), conteudo=b"outro arquivo", nome_arquivo="sigilo.pdf"
        )
        client.force_login(contador)

        assert client.post(
            f"/grimorio/revisao/{doc.pk}/classificar/", {"acao": "recusar"}
        ).status_code == 404
        assert client.get(f"/grimorio/revisao/{doc.pk}/arquivo/").status_code == 404
        doc.refresh_from_db()
        assert doc.aguardando


# ---------------------------------------------------------------------------
# LGPD
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_eliminacao_do_titular_apaga_o_objeto_no_storage(cliente, storage):
    """O caso mais delicado da eliminação: o conteúdo não está no banco.

    Apagar só a linha deixaria a nota fiscal e o extrato do titular intactos no
    bucket, apontados por ninguém — o pior tipo de sobra, porque some do
    inventário e permanece no disco.
    """
    from apps.audit.conteudo import eliminar_conteudo_do_titular

    documento, _ = services.receber(
        ctx=ctx_de(cliente), conteudo=CONTEUDO, nome_arquivo="extrato.pdf"
    )
    endereco = f"{documento.bucket}/{documento.chave}"
    assert endereco in storage

    eliminar_conteudo_do_titular(cliente)

    assert endereco not in storage
    with rls.escopo_irrestrito():
        assert not Documento.objects.filter(cliente=cliente).exists()
