"""
O nono dígito do celular brasileiro — o defeito achado testando com número real.

Em 27/jul/2026, com a instância Evolution já conectada: o cliente foi cadastrado
como `5599991332604` e o WhatsApp entregou o JID como `559991332604`. A
comparação de string crua não bateu, o gate de sessão recusou e o cliente
recebeu "não te reconheço" — sem erro em log nenhum, porque do ponto de vista do
código estava tudo certo: era mesmo um número desconhecido.

Estes testes fixam as duas metades do conserto:
1. achar quem escreveu por qualquer grafia (`Usuario.objects.por_telefone`);
2. **não** bloquear a sessão por divergência quando as grafias são equivalentes —
   se isso regredir, a anticlonagem passa a derrubar cliente legítimo.

Atualizado em 09/ago/2026 (DEC-03): a busca por telefone mudou de dono. O número
saiu de `Cliente` e virou `Usuario`, então quem responde "de quem é este número"
é o manager do usuário. A lógica de nono dígito em si (`clients/telefone.py`)
não mudou uma linha — e é isso que estes testes continuam protegendo.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.clients import telefone as tel
from apps.clients.models import Cliente, Perfil, Usuario
from apps.security.models import SessaoWhatsapp
from apps.security.services import sessao_ativa
from apps.tenants.models import Escritorio

COM_NONO = "5599991332604"
SEM_NONO = "559991332604"


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------
class TestNormalizacao:
    def test_reconhece_as_duas_grafias_como_o_mesmo_numero(self):
        assert tel.mesmo_numero(COM_NONO, SEM_NONO)
        assert tel.mesmo_numero(SEM_NONO, COM_NONO)

    def test_numeros_diferentes_continuam_diferentes(self):
        """O conserto não pode afrouxar a anticlonagem."""
        assert not tel.mesmo_numero(COM_NONO, "5599991332605")
        assert not tel.mesmo_numero(COM_NONO, "5511991332604")  # outro DDD

    def test_aceita_sujeira_de_formatacao(self):
        assert tel.mesmo_numero("+55 (99) 99133-2604", SEM_NONO)
        assert tel.mesmo_numero(f"{SEM_NONO}@s.whatsapp.net", COM_NONO)

    def test_canonico_devolve_a_forma_com_nono_digito(self):
        assert tel.canonico(SEM_NONO) == COM_NONO
        assert tel.canonico(COM_NONO) == COM_NONO

    def test_telefone_fixo_nao_ganha_nono_digito(self):
        """Fixo começa em 2–5. Inserir o 9 criaria um celular que não existe."""
        fixo = "551133334444"
        assert tel.canonico(fixo) == fixo
        assert tel.variantes(fixo) == [fixo]

    def test_numero_internacional_passa_intacto(self):
        eua = "12125551234"
        assert tel.canonico(eua) == eua
        assert tel.variantes(eua) == [eua]

    def test_vazio_nao_casa_com_nada(self):
        assert not tel.mesmo_numero("", "")
        assert not tel.mesmo_numero(None, COM_NONO)
        assert tel.variantes("") == []


# ---------------------------------------------------------------------------
# Busca do cliente
# ---------------------------------------------------------------------------
@pytest.fixture
def cliente_com_nono(db, escritorio):
    c = Cliente.objects.create(
        escritorio=escritorio,
        cnpj="11222333000181",
        nome="Padaria do Nono Dígito",
        telefone_whatsapp=COM_NONO,
    )
    Perfil.objects.create(cliente=c, persona="lumen", tier_maximo=1)
    return c


def _empresas(numero, escritorio=None):
    """Empresas que este número atende — o que o pipeline realmente pergunta."""
    usuario = Usuario.objects.por_telefone(numero, escritorio=escritorio)
    return usuario.clientes_ativos() if usuario else []


class TestBuscaPorTelefone:
    def test_acha_pela_grafia_sem_nono(self, cliente_com_nono):
        """O caso exato que falhou em produção."""
        assert _empresas(SEM_NONO) == [cliente_com_nono]

    def test_acha_pela_grafia_exata(self, cliente_com_nono):
        assert _empresas(COM_NONO) == [cliente_com_nono]

    def test_acha_a_partir_do_jid_cru_do_whatsapp(self, cliente_com_nono):
        assert _empresas(f"{SEM_NONO}@s.whatsapp.net") == [cliente_com_nono]

    def test_numero_desconhecido_nao_casa(self, cliente_com_nono):
        assert Usuario.objects.por_telefone("5511987654321") is None

    def test_cliente_inativo_nao_casa(self, cliente_com_nono):
        """A pessoa continua existindo; a empresa é que sai da lista.

        Distinção que o modelo antigo não conseguia fazer — e que importa: o
        sócio que fecha uma empresa e mantém outra não pode sumir do sistema.
        """
        cliente_com_nono.ativo = False
        cliente_com_nono.save()

        assert Usuario.objects.por_telefone(SEM_NONO) is not None
        assert _empresas(SEM_NONO) == []

    def test_nao_atravessa_escritorio(self, cliente_com_nono, db):
        """A normalização não pode virar um caminho novo de vazamento entre tenants."""
        outro = Escritorio.objects.create(nome="Outro", slug="outro-nono", ativo=True)

        assert Usuario.objects.por_telefone(SEM_NONO, escritorio=outro) is None
        assert _empresas(SEM_NONO, escritorio=cliente_com_nono.escritorio) == [
            cliente_com_nono
        ]

    def test_as_duas_grafias_viram_a_mesma_pessoa(self, cliente_com_nono, escritorio):
        """Antes, cadastrar as duas grafias criava dois clientes distintos e a
        busca tinha de desempatar por grafia exata — regra frágil, e o teste
        anterior só congelava o desempate.

        Agora o número é canonicalizado na gravação: as duas grafias são o mesmo
        `Usuario`, e o segundo cadastro não vira ambiguidade, vira o caso do
        DEC-03 — uma pessoa que responde por duas empresas.
        """
        segunda = Cliente.objects.create(
            escritorio=escritorio,
            cnpj="99888777000166",
            nome="Segunda empresa do mesmo dono",
            telefone_whatsapp=SEM_NONO,
        )
        Perfil.objects.create(cliente=segunda, persona="lumen", tier_maximo=1)

        assert Usuario.objects.filter(escritorio=escritorio).count() == 1
        assert set(_empresas(SEM_NONO)) == {cliente_com_nono, segunda}
        assert set(_empresas(COM_NONO)) == {cliente_com_nono, segunda}


# ---------------------------------------------------------------------------
# Gate de sessão — a metade que mais dói se regredir
# ---------------------------------------------------------------------------
class TestSessaoNaoBloqueiaPorGrafia:
    def _sessao(self, cliente, wa_id):
        agora = timezone.now()
        return SessaoWhatsapp.objects.create(
            cliente=cliente,
            wa_id=wa_id,
            status=SessaoWhatsapp.Status.ATIVA,
            validado_em=agora,
            expira_em=agora + timedelta(days=7),
        )

    def test_sessao_validada_sem_nono_continua_ativa(self, cliente_com_nono):
        """Validou com uma grafia, escreve com a outra: é a mesma pessoa."""
        sessao = self._sessao(cliente_com_nono, SEM_NONO)

        assert sessao_ativa(cliente_com_nono, COM_NONO) is True

        sessao.refresh_from_db()
        assert sessao.status == SessaoWhatsapp.Status.ATIVA

    def test_numero_de_verdade_diferente_ainda_bloqueia(self, cliente_com_nono):
        """A proteção contra clonagem/troca de número tem que seguir de pé."""
        sessao = self._sessao(cliente_com_nono, "5511912345678")

        assert sessao_ativa(cliente_com_nono, COM_NONO) is False

        sessao.refresh_from_db()
        assert sessao.status == SessaoWhatsapp.Status.BLOQUEADA


# ---------------------------------------------------------------------------
# Ponta a ponta pelo webhook
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_webhook_responde_a_mensagem_com_grafia_divergente(client, cliente_com_nono, monkeypatch):
    """Cadastro com nono dígito + mensagem entregue sem ele = tem que responder.

    É o cenário completo do defeito: antes, isto devolvia a negação de sessão.
    """
    import json

    agora = timezone.now()
    SessaoWhatsapp.objects.create(
        cliente=cliente_com_nono,
        wa_id=COM_NONO,
        status=SessaoWhatsapp.Status.ATIVA,
        validado_em=agora,
        expira_em=agora + timedelta(days=7),
    )

    enviadas = []
    monkeypatch.setattr(
        "apps.channel_evolution.tasks.enviar_mensagem",
        lambda telefone, texto, **kw: enviadas.append((telefone, texto)) or True,
        raising=False,
    )

    payload = {
        "event": "messages.upsert",
        "instance": "teste-nono",
        "data": {
            "key": {"remoteJid": f"{SEM_NONO}@s.whatsapp.net", "fromMe": False, "id": "3EBNONO01"},
            "message": {"conversation": "quais notas eu emiti?"},
            "messageTimestamp": 1785000000,
            "pushName": "Cliente",
        },
    }
    resposta = client.post(
        "/webhook/evolution", data=json.dumps(payload), content_type="application/json"
    )

    assert resposta.status_code == 200
    from apps.audit.models import Auditoria

    eventos = list(
        Auditoria.objects.filter(cliente=cliente_com_nono).values_list("evento", flat=True)
    )
    # O cliente foi RECONHECIDO: a mensagem entrou vinculada a ele, em vez de
    # cair como remetente desconhecido.
    assert "whatsapp_mensagem_recebida" in eventos
