"""
Aprendizado do roteador por exemplos — sem release.

Motivação (25/jul/2026): "envie um relatório das vendas" caía na resposta
genérica e a correção exigiu commit + deploy. Cada jeito novo de o cliente falar
virava trabalho de desenvolvedor.

O que estes testes protegem:
1. O exemplo cadastrado chega ao prompt do roteador (senão o admin é decorativo).
2. Prompt sem exemplo nenhum continua idêntico ao de antes — instalação nova não
   pode piorar por causa de um bloco vazio.
3. A tela de revisão só mostra o escritório de quem está olhando.
4. A autorização gravada na trilha aponta a mensagem e o número — é o que
   responde "quem autorizou esta nota" numa auditoria.
"""
import pytest
from django.contrib.auth import get_user_model

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.core.models import ExemploIntencao, exemplos_para_prompt
from apps.core.orchestrator import Orquestrador
from apps.tenants.models import Escritorio, MembroEscritorio

URL_REVISAO = "/admin/core/exemplointencao/revisar/"


@pytest.fixture
def superusuario(db):
    return get_user_model().objects.create_superuser(
        username="equipe.aprendizado", email="e@magicbi.example.com", password="x"
    )


class TestExemplosNoPrompt:
    def test_sem_exemplos_o_bloco_e_vazio(self, db):
        """Instalação nova não pode ganhar um cabeçalho solto sem itens."""
        assert exemplos_para_prompt() == ""

    def test_exemplo_cadastrado_aparece_agrupado_por_intencao(self, db):
        ExemploIntencao.objects.create(frase="me manda o relatório de vendas", intencao="consultar_pedido")
        ExemploIntencao.objects.create(frase="quanto vendi esse mês", intencao="consultar_pedido")
        ExemploIntencao.objects.create(frase="preciso de um recibo", intencao="emitir_nota")

        bloco = exemplos_para_prompt()

        assert "consultar_pedido:" in bloco
        assert "me manda o relatório de vendas" in bloco
        assert "quanto vendi esse mês" in bloco
        assert "emitir_nota:" in bloco
        # Uma linha por intenção, não uma por frase.
        assert bloco.count("- consultar_pedido:") == 1

    def test_exemplo_desativado_sai_do_prompt(self, db):
        ExemploIntencao.objects.create(
            frase="frase aposentada", intencao="consultar_pedido", ativo=False
        )

        assert "frase aposentada" not in exemplos_para_prompt()

    def test_o_roteador_recebe_os_exemplos(self, db, monkeypatch, settings):
        """O elo que faz o admin valer alguma coisa: sem isto, cadastrar exemplo
        não muda o comportamento e ninguém percebe."""
        settings.GROQ_API_KEY = "chave-de-teste"
        ExemploIntencao.objects.create(frase="me manda o relatório", intencao="consultar_pedido")

        prompts = []

        class AgenteFalso:
            def __init__(self, *args, **kwargs):
                prompts.append(kwargs.get("system_prompt", ""))

            def run_sync(self, mensagem):
                class R:
                    output = type("O", (), {"intencao": "consultar_pedido"})()

                return R()

        import pydantic_ai

        monkeypatch.setattr(pydantic_ai, "Agent", AgenteFalso)

        Orquestrador()._classificar_via_groq("me manda o relatório")

        assert prompts, "o roteador não chegou a montar o prompt"
        assert "me manda o relatório" in prompts[0]
        assert "consultar_pedido:" in prompts[0]


@pytest.mark.django_db
class TestTelaDeRevisao:
    def _mensagem(self, cliente, texto, intencao):
        from apps.audit.services import registrar

        registrar(
            "orquestrador_mensagem_processada",
            {"mensagem": texto, "intencao": intencao},
            cliente=cliente,
        )

    def test_lista_a_nao_entendida_com_a_reformulacao_ao_lado(
        self, client, superusuario, cliente
    ):
        """O par que gera o rótulo de graça."""
        self._mensagem(cliente, "manda aquele negócio das vendas", "desconhecida")
        self._mensagem(cliente, "relatório de vendas", "consultar_pedido")
        client.force_login(superusuario)

        conteudo = client.get(URL_REVISAO).content.decode()

        assert "manda aquele negócio das vendas" in conteudo
        assert "relatório de vendas" in conteudo
        assert "consultar_pedido" in conteudo

    def test_nao_mostra_conversa_de_outro_escritorio(self, client, cliente, db):
        """Mensagem de cliente é dado pessoal — a tela de aprendizado não pode
        virar uma janela para a carteira do concorrente."""
        from apps.clients.models import Cliente

        outro = Escritorio.objects.create(nome="Concorrente", slug="conc-aprend", ativo=True)
        alheio = Cliente.objects.create(
            escritorio=outro,
            cnpj="98765432000199",
            nome="Cliente Alheio",
            telefone_whatsapp="5511933333333",
        )
        self._mensagem(alheio, "segredo do concorrente", "desconhecida")
        self._mensagem(cliente, "coisa do meu cliente", "desconhecida")

        # Contador de verdade: staff COM vínculo, mas sem superuser — superuser
        # vê a plataforma inteira por definição e não testaria nada aqui.
        from django.contrib.auth.models import Permission

        contador = get_user_model().objects.create_user(
            username="contador.aprendizado", is_staff=True
        )
        contador.user_permissions.add(
            *Permission.objects.filter(codename="view_exemplointencao")
        )
        MembroEscritorio.objects.create(usuario=contador, escritorio=cliente.escritorio)
        client.force_login(contador)

        conteudo = client.get(URL_REVISAO).content.decode()

        assert "coisa do meu cliente" in conteudo
        assert "segredo do concorrente" not in conteudo


@pytest.mark.django_db
class TestEvidenciaDaAutorizacao:
    def test_motivo_registra_mensagem_e_numero(self, cliente, monkeypatch):
        """"cliente confirmou" sozinho não diz QUAL mensagem autorizou nem de
        qual número — e é isso que uma auditoria pede."""
        from apps.core.orchestrator import DadosNotaExtraidos

        monkeypatch.setattr(
            Orquestrador,
            "_extrair_dados_nota",
            lambda self, m: DadosNotaExtraidos(
                tomador="Joao", valor=100.0, descricao_servico="consultoria"
            ),
        )
        monkeypatch.setattr(Orquestrador, "_classificar_intencao", lambda self, m: "emitir_nota")

        agente = Orquestrador()
        agente.processar("emite nota de 100 pro Joao, consultoria", cliente, message_id="msg-abc")
        agente.processar("sim", cliente, message_id="msg-xyz")

        transicoes = [
            a
            for a in Auditoria.objects.filter(evento="intencao_fiscal_transicao")
            if a.dados.get("para") == Intencao.Estado.EMITINDO
        ]
        assert transicoes, "nenhuma transição de autorização registrada"
        motivo = transicoes[-1].dados["motivo"]
        assert "msg-xyz" in motivo
        assert cliente.telefone_whatsapp in motivo
