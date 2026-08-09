"""
Tenancy de três níveis: escritório → empresa → usuário (DEC-03).

O caso que originou tudo: numa carteira de mil empresas, o telefone que atende
duas ou três delas não é exceção. O modelo antigo — telefone como campo do
`Cliente`, único por escritório — **não representava isso e ainda impedia o
cadastro**. O sintoma aparecia longe da causa: no atendimento, como "não te
reconheço", sem erro em log nenhum.

O que estes testes protegem, em ordem de gravidade se quebrar:

1. **Ninguém fala pela empresa errada.** Com vários vínculos, o agente pergunta
   e só age depois da resposta. Errar aqui é nota fiscal em CNPJ errado.
2. **O menu não vaza carteira.** A lista de empresas só aparece para número com
   sessão validada.
3. **Cadastrar o número de um colega não concede autoridade fiscal** — cada
   número valida a própria sessão.
4. **O nono dígito continua resolvido** (`test_telefone_nono_digito.py` cobre a
   função pura; aqui é o efeito no cadastro).
"""
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.clients.models import Cliente, Perfil, Usuario, VinculoUsuarioCliente
from apps.core import desambiguacao
from apps.security.models import EmpresaEmFoco, SessaoWhatsapp
from apps.tenants import rls
from apps.tenants.models import Escritorio

TELEFONE = "5511988887777"


def _empresa(escritorio, cnpj, nome, *, com_sessao=True, telefone=None):
    with rls.escopo_irrestrito():
        cliente = Cliente.objects.create(
            escritorio=escritorio, cnpj=cnpj, nome=nome, ativo=True
        )
        Perfil.objects.create(cliente=cliente, persona="lumen", tier_maximo=1)
        if telefone:
            cliente.vincular_usuario(telefone, principal=True)
        if com_sessao:
            agora = timezone.now()
            SessaoWhatsapp.objects.create(
                cliente=cliente,
                wa_id=telefone or TELEFONE,
                status=SessaoWhatsapp.Status.ATIVA,
                validado_em=agora,
                expira_em=agora + timedelta(days=7),
            )
    return cliente


@pytest.fixture
def duas_empresas(db, escritorio):
    """Um número, duas empresas — o cenário que o DEC-03 veio permitir."""
    padaria = _empresa(escritorio, "11111111000191", "Padaria Aurora", telefone=TELEFONE)
    oficina = _empresa(escritorio, "22222222000192", "Oficina Bela Vista", telefone=TELEFONE)
    return Usuario.objects.por_telefone(TELEFONE), padaria, oficina


# ---------------------------------------------------------------------------
# Cadastro
# ---------------------------------------------------------------------------
class TestCadastro:
    def test_um_numero_vira_uma_pessoa_com_dois_vinculos(self, duas_empresas):
        usuario, padaria, oficina = duas_empresas

        assert usuario is not None
        assert set(usuario.clientes_ativos()) == {padaria, oficina}
        assert usuario.vinculos.count() == 2

    def test_numero_e_gravado_na_forma_canonica(self, db, escritorio):
        """A canonicalização na gravação é o que torna a unicidade real."""
        usuario = Usuario.objects.create(
            escritorio=escritorio, telefone_whatsapp="559991332604"
        )

        usuario.refresh_from_db()
        assert usuario.telefone_whatsapp == "5599991332604"

    def test_telefone_do_cliente_e_somente_leitura(self, duas_empresas):
        """Atribuir tem que explodir, não virar fonte de verdade paralela."""
        _, padaria, _ = duas_empresas

        assert padaria.telefone_whatsapp == TELEFONE
        with pytest.raises(AttributeError):
            padaria.telefone_whatsapp = "5511900000000"

    def test_vinculo_nao_atravessa_escritorio(self, db, escritorio, duas_empresas):
        """Vínculo cruzando tenant seria vazamento pela porta do cadastro."""
        usuario, _, _ = duas_empresas
        with rls.escopo_irrestrito():
            outro = Escritorio.objects.create(nome="Concorrente", slug="conc-dec03", ativo=True)
            alheia = Cliente.objects.create(
                escritorio=outro, cnpj="33333333000193", nome="Alheia", ativo=True
            )

            with pytest.raises(ValidationError):
                VinculoUsuarioCliente.objects.create(usuario=usuario, cliente=alheia)

    def test_um_principal_por_empresa(self, db, escritorio):
        empresa = _empresa(escritorio, "44444444000194", "Loja", telefone=TELEFONE)
        empresa.vincular_usuario("5511911112222", principal=True)

        principais = empresa.vinculos.filter(principal=True)
        assert principais.count() == 1
        assert principais.first().usuario.telefone_whatsapp == "5511911112222"

    def test_mesmo_numero_duas_vezes_nao_duplica_vinculo(self, db, escritorio):
        empresa = _empresa(escritorio, "55555555000195", "Mercearia", telefone=TELEFONE)
        empresa.vincular_usuario(TELEFONE, papel=VinculoUsuarioCliente.Papel.SOCIO)

        assert empresa.vinculos.count() == 1
        assert empresa.vinculos.first().papel == VinculoUsuarioCliente.Papel.SOCIO

    def test_numero_invalido_e_recusado(self, db, escritorio):
        empresa = _empresa(escritorio, "66666666000196", "Bar", com_sessao=False)
        with pytest.raises(ValidationError):
            empresa.vincular_usuario("não é telefone")

    def test_numero_unico_por_escritorio(self, db, escritorio):
        Usuario.objects.create(escritorio=escritorio, telefone_whatsapp=TELEFONE)
        with pytest.raises(IntegrityError):
            Usuario.objects.create(escritorio=escritorio, telefone_whatsapp=TELEFONE)


# ---------------------------------------------------------------------------
# "De qual empresa você quer falar?"
# ---------------------------------------------------------------------------
class TestDesambiguacao:
    def test_uma_empresa_so_nao_pergunta_nada(self, db, escritorio):
        empresa = _empresa(escritorio, "77777777000197", "Única", telefone=TELEFONE)
        usuario = Usuario.objects.por_telefone(TELEFONE)

        resolucao = desambiguacao.resolver(usuario, "quais notas eu emiti?")

        assert resolucao.cliente == empresa
        assert resolucao.resposta is None

    def test_duas_empresas_perguntam_antes_de_agir(self, duas_empresas):
        """O ponto do módulo inteiro: na dúvida, perguntar."""
        usuario, padaria, oficina = duas_empresas

        resolucao = desambiguacao.resolver(usuario, "emite uma nota de 500 pro João")

        assert resolucao.cliente is None, "não pode escolher empresa sozinho"
        assert "Padaria Aurora" in resolucao.resposta
        assert "Oficina Bela Vista" in resolucao.resposta

    def test_menu_sai_em_ordem_alfabetica(self, duas_empresas):
        """A ordem é estável entre duas mensagens — o número que a pessoa digita
        depende disso. Alfabética por ser a que o humano consegue prever."""
        usuario, padaria, oficina = duas_empresas

        assert usuario.clientes_ativos() == [oficina, padaria]

    def test_resposta_pelo_numero_fixa_a_empresa(self, duas_empresas):
        usuario, _, oficina = duas_empresas
        desambiguacao.resolver(usuario, "emite uma nota")  # dispara o menu

        resolucao = desambiguacao.resolver(usuario, "1")

        assert resolucao.cliente == oficina
        # Confirma pelo NOME: é o que permite a pessoa perceber se os índices
        # andaram entre o menu e a resposta.
        assert "Oficina Bela Vista" in resolucao.resposta

    def test_empresa_fixada_vale_para_as_mensagens_seguintes(self, duas_empresas):
        usuario, padaria, _ = duas_empresas
        desambiguacao.resolver(usuario, "emite uma nota")
        desambiguacao.resolver(usuario, "2")  # Padaria Aurora

        resolucao = desambiguacao.resolver(usuario, "emite uma nota de 500 pro João")

        assert resolucao.cliente == padaria
        assert resolucao.resposta is None, "não pergunta de novo no meio da conversa"

    def test_numero_fora_da_lista_volta_para_o_menu(self, duas_empresas):
        usuario, _, _ = duas_empresas
        desambiguacao.resolver(usuario, "oi")

        resolucao = desambiguacao.resolver(usuario, "7")

        assert resolucao.cliente is None
        assert "Não entendi" in resolucao.resposta

    def test_escolha_por_nome_tambem_vale(self, duas_empresas):
        usuario, _, oficina = duas_empresas
        desambiguacao.resolver(usuario, "oi")

        assert desambiguacao.resolver(usuario, "oficina").cliente == oficina

    def test_nome_ambiguo_volta_para_o_menu(self, db, escritorio):
        _empresa(escritorio, "88888888000198", "Padaria Centro", telefone=TELEFONE)
        _empresa(escritorio, "99999999000199", "Padaria Sul", telefone=TELEFONE)
        usuario = Usuario.objects.por_telefone(TELEFONE)
        desambiguacao.resolver(usuario, "oi")

        resolucao = desambiguacao.resolver(usuario, "padaria")

        assert resolucao.cliente is None
        assert "Não entendi" in resolucao.resposta

    def test_pedido_explicito_reabre_o_menu(self, duas_empresas):
        usuario, padaria, _ = duas_empresas
        desambiguacao.resolver(usuario, "oi")
        desambiguacao.resolver(usuario, "1")

        resolucao = desambiguacao.resolver(usuario, "trocar empresa")

        assert resolucao.cliente is None
        assert "Padaria Aurora" in resolucao.resposta

    def test_nome_de_empresa_no_meio_da_frase_nao_troca_o_foco(self, duas_empresas):
        """"emite nota pra Oficina" tem o nome do TOMADOR, não do prestador.

        Inferir empresa pelo conteúdo da mensagem produziria o erro exatamente
        na mensagem que emite documento fiscal.
        """
        usuario, padaria, _ = duas_empresas
        desambiguacao.resolver(usuario, "oi")
        desambiguacao.resolver(usuario, "2")  # fixou a Padaria

        resolucao = desambiguacao.resolver(usuario, "emite uma nota de 300 pra Oficina Bela Vista")

        assert resolucao.cliente == padaria
        assert resolucao.resposta is None

    def test_foco_expira(self, duas_empresas, settings):
        usuario, padaria, _ = duas_empresas
        desambiguacao.resolver(usuario, "oi")
        desambiguacao.resolver(usuario, "2")

        foco = EmpresaEmFoco.objects.get(usuario=usuario)
        EmpresaEmFoco.objects.filter(pk=foco.pk).update(
            atualizado_em=timezone.now() - timedelta(minutes=desambiguacao.TTL_MINUTOS + 1)
        )

        resolucao = desambiguacao.resolver(usuario, "emite uma nota de 500")

        assert resolucao.cliente is None, "conversa fria não herda a empresa anterior"
        assert "Padaria Aurora" in resolucao.resposta

    def test_vinculo_removido_derruba_o_foco(self, duas_empresas):
        """Empresa em foco que sai da carteira não pode virar escolha herdada:
        pergunta de novo em vez de cair na primeira da lista."""
        usuario, padaria, oficina = duas_empresas
        desambiguacao.resolver(usuario, "oi")
        desambiguacao.resolver(usuario, "2")  # fixou a Padaria

        with rls.escopo_irrestrito():
            # Uma terceira entra para que ainda reste ambiguidade depois da saída.
            _empresa(escritorio=padaria.escritorio, cnpj="70707070000170", nome="Zelia Ltda", telefone=TELEFONE)
            padaria.vinculos.filter(usuario=usuario).update(ativo=False)

        resolucao = desambiguacao.resolver(usuario, "emite uma nota")

        assert resolucao.cliente is None
        assert "Oficina Bela Vista" in resolucao.resposta
        assert "Padaria Aurora" not in resolucao.resposta

    def test_numero_sem_cadastro_nao_resolve_nada(self, db):
        assert desambiguacao.resolver(None, "oi") == desambiguacao.Resolucao()


# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------
class TestSegurancaDoMenu:
    def test_menu_so_lista_empresa_com_sessao_validada(self, db, escritorio):
        """Listar a carteira para número não validado seria entregar informação
        a quem o gate de segurança ainda não reconheceu."""
        validada = _empresa(escritorio, "10101010000110", "Com Sessão", telefone=TELEFONE)
        _empresa(
            escritorio, "20202020000120", "Sem Sessão", com_sessao=False, telefone=TELEFONE
        )
        usuario = Usuario.objects.por_telefone(TELEFONE)

        resolucao = desambiguacao.resolver(usuario, "oi")

        # Sobrou uma só validada → resolve direto, sem menu e sem citar a outra.
        assert resolucao.cliente == validada
        assert resolucao.resposta is None

    def test_nenhuma_validada_cai_na_revalidacao(self, db, escritorio):
        _empresa(escritorio, "30303030000130", "A", com_sessao=False, telefone=TELEFONE)
        _empresa(escritorio, "40404040000140", "B", com_sessao=False, telefone=TELEFONE)
        usuario = Usuario.objects.por_telefone(TELEFONE)

        resolucao = desambiguacao.resolver(usuario, "oi")

        # Devolve uma empresa sem menu: quem responde é o orquestrador, pedindo
        # a revalidação — mesmo caminho de sempre para uma empresa só.
        assert resolucao.cliente is not None
        assert resolucao.resposta is None

    def test_numero_de_colega_nao_herda_a_sessao(self, db, escritorio):
        """Cadastrar o telefone de um colega não pode, sozinho, conceder
        autoridade fiscal sobre a empresa."""
        from apps.security.services import sessao_ativa

        empresa = _empresa(escritorio, "50505050000150", "Loja", telefone=TELEFONE)
        empresa.vincular_usuario("5511900001111", papel=VinculoUsuarioCliente.Papel.RH)

        assert sessao_ativa(empresa, TELEFONE) is True
        empresa.sessao_whatsapp.refresh_from_db()

    def test_escrita_de_numero_nao_validado_bloqueia(self, db, escritorio):
        from apps.security.services import sessao_ativa

        empresa = _empresa(escritorio, "60606060000160", "Loja", telefone=TELEFONE)
        empresa.vincular_usuario("5511900001111", papel=VinculoUsuarioCliente.Papel.RH)

        assert sessao_ativa(empresa, "5511900001111") is False


# ---------------------------------------------------------------------------
# Ponta a ponta pelo canal
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPeloWebhook:
    """O caminho de verdade: webhook → pipeline → desambiguação → orquestrador.

    Testar só `desambiguacao.resolver` deixaria de fora justamente o ponto onde
    o pipeline decide entre responder o menu e chamar o orquestrador — que é a
    linha que impede uma nota de ser aberta antes de a empresa estar escolhida.
    """

    def _mandar(self, client, texto, monkeypatch, enviadas):
        import json

        monkeypatch.setattr(
            "apps.channel_evolution.tasks.enviar_mensagem",
            lambda telefone, texto, **kw: enviadas.append((telefone, texto)) or True,
            raising=False,
        )
        payload = {
            "event": "messages.upsert",
            "instance": "teste-dec03",
            "data": {
                "key": {
                    "remoteJid": f"{TELEFONE}@s.whatsapp.net",
                    "fromMe": False,
                    "id": f"3EB{len(enviadas)}{abs(hash(texto)) % 10_000}",
                },
                "message": {"conversation": texto},
                "messageTimestamp": 1785000000,
                "pushName": "Cliente",
            },
        }
        resposta = client.post(
            "/webhook/evolution", data=json.dumps(payload), content_type="application/json"
        )
        assert resposta.status_code == 200
        return enviadas[-1][1] if enviadas else ""

    def test_menu_chega_ao_cliente_e_nada_e_emitido(
        self, client, duas_empresas, monkeypatch
    ):
        from apps.agents.agente_nf.models import Intencao

        enviadas = []
        resposta = self._mandar(
            client, "emite uma nota de 500 pro João", monkeypatch, enviadas
        )

        assert "Padaria Aurora" in resposta
        assert "Oficina Bela Vista" in resposta
        assert not Intencao.objects.exists(), (
            "abriu emissão antes de saber de qual empresa — é o erro que este "
            "sprint inteiro existe para impedir"
        )

    def test_depois_de_escolher_a_conversa_segue_normal(
        self, client, duas_empresas, monkeypatch
    ):
        _, padaria, _ = duas_empresas
        enviadas = []
        self._mandar(client, "quais notas eu emiti?", monkeypatch, enviadas)

        confirmacao = self._mandar(client, "2", monkeypatch, enviadas)
        assert "Padaria Aurora" in confirmacao

        seguinte = self._mandar(client, "quais notas eu emiti?", monkeypatch, enviadas)
        assert "nota" in seguinte.lower()
        assert "Oficina Bela Vista" not in seguinte
