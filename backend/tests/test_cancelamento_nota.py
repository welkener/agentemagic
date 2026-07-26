"""Consulta e cancelamento de nota já emitida (ponto 5 do levantamento de lacunas).

Regra de ouro do fluxo: **o cliente nunca cancela sozinho pelo WhatsApp**.
Cancelar documento fiscal tem efeito contábil e prazo legal, então o pedido
nasce em AGUARDANDO_APROVACAO e quem decide é o contador, na mesma fila onde
ele já aprova emissão.
"""
import pytest

from apps.agents.agente_nf.models import Intencao
from apps.agents.agente_nf.services import (
    ErroCancelamento,
    confirmar_cancelamento,
    confirmar_emissao,
    solicitar_cancelamento,
)
from apps.core.orchestrator import Orquestrador


@pytest.fixture
def nota_emitida(cliente):
    intencao = Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="canc-teste-001",
        tipo_acao="emitir_nfse",
        payload={
            "cnpj_prestador": cliente.cnpj,
            "cnae": cliente.cnae_padrao,
            "valor": 480.0,
            "descricao_servico": "Consultoria",
            "tomador": "João",
        },
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )
    confirmar_emissao(intencao, motivo="teste")
    intencao.refresh_from_db()
    return intencao


# ---------------------------------------------------------------------------
# Classificação — as duas intenções estavam inalcançáveis antes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mensagem,esperado",
    [
        ("quais notas eu emiti?", "consultar_nota"),
        ("minhas notas", "consultar_nota"),
        ("cadê minha nota fiscal?", "consultar_nota"),
        ("quero cancelar a nota", "cancelar_nota"),
        ("cancelamento de nfse", "cancelar_nota"),
        ("emite uma nota de 300 reais pro Pedro", "emitir_nota"),
        ("emitir nfs-e", "emitir_nota"),
        # Ambíguo de verdade: "minha" é palavra de consulta, mas o verbo manda.
        ("quero emitir minha nota", "emitir_nota"),
        ("quero cancelar minha nota emitida ontem", "cancelar_nota"),
    ],
)
def test_classificador_distingue_emitir_consultar_e_cancelar(mensagem, esperado):
    """Todas contêm 'nota' — sem ordem correta, as três viravam emitir_nota."""
    assert Orquestrador()._classificar_por_palavra_chave(mensagem) == esperado


# ---------------------------------------------------------------------------
# Consulta (Tier 0)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cliente_consulta_as_proprias_notas(cliente, nota_emitida):
    resposta = Orquestrador().processar("quais notas eu emiti?", cliente)
    assert nota_emitida.protocolo in resposta
    assert "480.00" in resposta


@pytest.mark.django_db
def test_consulta_sem_nota_nenhuma_nao_quebra(cliente):
    resposta = Orquestrador().processar("minhas notas", cliente)
    assert "ainda não tem nenhuma nota" in resposta


# ---------------------------------------------------------------------------
# Cancelamento — cliente pede, contador decide
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_cliente_pede_cancelamento_e_vai_pro_contador(cliente, nota_emitida):
    resposta = Orquestrador().processar("quero cancelar a nota", cliente)

    assert "contador" in resposta
    assert nota_emitida.protocolo in resposta

    pedido = Intencao.objects.get(tipo_acao="cancelar_nfse")
    assert pedido.estado == Intencao.Estado.AGUARDANDO_APROVACAO
    assert pedido.intencao_original == nota_emitida

    # A nota NÃO foi cancelada só porque o cliente pediu.
    nota_emitida.refresh_from_db()
    assert nota_emitida.cancelada is False


@pytest.mark.django_db
def test_tier_maximo_alto_nao_libera_cancelamento_direto(cliente, nota_emitida):
    """Nem perfil Tier 3 cancela sozinho — a trava é do fluxo, não do tier."""
    cliente.perfil.tier_maximo = 3
    cliente.perfil.save()

    Orquestrador().processar("cancelar minha nota", cliente)

    nota_emitida.refresh_from_db()
    assert nota_emitida.cancelada is False
    assert Intencao.objects.get(tipo_acao="cancelar_nfse").estado == (
        Intencao.Estado.AGUARDANDO_APROVACAO
    )


@pytest.mark.django_db
def test_contador_aprova_e_a_nota_e_cancelada_de_fato(cliente, nota_emitida):
    pedido = solicitar_cancelamento(nota_emitida, motivo="cliente desistiu", origem="teste")
    resultado = confirmar_cancelamento(pedido, motivo="aprovado pelo contador")

    assert resultado.ok
    nota_emitida.refresh_from_db()
    assert nota_emitida.cancelada is True
    assert nota_emitida.protocolo_cancelamento.startswith("CANC-")
    # A nota continua CONCLUIDO: ela FOI emitida de verdade, e depois cancelada.
    assert nota_emitida.estado == Intencao.Estado.CONCLUIDO
    assert Intencao.objects.get(pk=pedido.pk).estado == Intencao.Estado.CONCLUIDO


@pytest.mark.django_db
def test_cancelamento_aparece_na_consulta_do_cliente(cliente, nota_emitida):
    pedido = solicitar_cancelamento(nota_emitida, motivo="erro no valor", origem="teste")
    confirmar_cancelamento(pedido, motivo="aprovado")

    resposta = Orquestrador().processar("quais notas eu emiti?", cliente)
    assert "CANCELADA" in resposta


# ---------------------------------------------------------------------------
# Guardas
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_nao_cancela_nota_que_nunca_foi_emitida(cliente):
    rascunho = Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="canc-teste-rascunho",
        tipo_acao="emitir_nfse",
        payload={"valor": 10.0},
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )
    with pytest.raises(ErroCancelamento, match="emitida com sucesso"):
        solicitar_cancelamento(rascunho, motivo="x", origem="teste")


@pytest.mark.django_db
def test_nao_cancela_duas_vezes(cliente, nota_emitida):
    pedido = solicitar_cancelamento(nota_emitida, motivo="x", origem="teste")
    confirmar_cancelamento(pedido, motivo="aprovado")
    nota_emitida.refresh_from_db()

    with pytest.raises(ErroCancelamento, match="já foi cancelada"):
        solicitar_cancelamento(nota_emitida, motivo="de novo", origem="teste")


@pytest.mark.django_db
def test_nao_abre_dois_pedidos_para_a_mesma_nota(cliente, nota_emitida):
    solicitar_cancelamento(nota_emitida, motivo="x", origem="teste")
    with pytest.raises(ErroCancelamento, match="em análise"):
        solicitar_cancelamento(nota_emitida, motivo="x", origem="teste")


@pytest.mark.django_db
def test_sefin_recusa_cancelamento_sem_justificativa(cliente, nota_emitida):
    """O mock reproduz a rejeição mais comum; o pedido tem que ir pra REJEITADO,
    não ficar preso em EMITINDO."""
    pedido = solicitar_cancelamento(nota_emitida, motivo="x", origem="teste")
    pedido.payload["motivo"] = ""  # simula o motivo perdido antes da Sefin
    pedido.save(update_fields=["payload"])

    resultado = confirmar_cancelamento(pedido, motivo="aprovado")

    assert resultado.ok is False
    pedido.refresh_from_db()
    assert pedido.estado == Intencao.Estado.REJEITADO
    nota_emitida.refresh_from_db()
    assert nota_emitida.cancelada is False  # nada foi marcado por engano


@pytest.mark.django_db
def test_pedido_de_cancelamento_e_auditado(cliente, nota_emitida):
    from apps.audit.models import Auditoria

    pedido = solicitar_cancelamento(nota_emitida, motivo="erro", origem="teste")
    confirmar_cancelamento(pedido, motivo="aprovado")

    transicoes = Auditoria.objects.filter(evento="intencao_fiscal_transicao")
    do_pedido = [a for a in transicoes if a.dados.get("intencao_id") == pedido.pk]
    assert [a.dados["para"] for a in do_pedido][-1] == Intencao.Estado.CONCLUIDO


# ---------------------------------------------------------------------------
# Evento de cancelamento (e101101) — XML assinado, validado contra o XSD
# ---------------------------------------------------------------------------
class TestEventoCancelamento:
    """`apps/fiscal/eventos.py` — o documento que vai pra Sefin."""

    @staticmethod
    def _cliente_fiscal(cliente):
        cliente.codigo_municipio_ibge = "3550308"
        cliente.codigo_tributacao_nacional = "010101"
        cliente.save()
        return cliente

    CHAVE = "3" * 50
    JUSTIFICATIVA = "Servico nao foi prestado ao tomador"

    @pytest.mark.django_db
    def test_evento_e_valido_contra_o_xsd_oficial(self, cliente):
        from apps.fiscal import eventos

        xml, _ = eventos.montar_cancelamento(
            self._cliente_fiscal(cliente), self.CHAVE, self.JUSTIFICATIVA
        )
        erros = eventos.validar_evento_contra_xsd(xml)
        assert not erros, "evento invalido: " + " | ".join(erros)

    @pytest.mark.django_db
    def test_id_do_evento_segue_o_pattern(self, cliente):
        """`PRE[0-9]{59}` = chNFSe(50) + código do evento(6) + sequencial(3)."""
        import re

        from apps.fiscal import eventos

        _, id_evento = eventos.montar_cancelamento(
            self._cliente_fiscal(cliente), self.CHAVE, self.JUSTIFICATIVA
        )
        assert re.fullmatch(r"PRE[0-9]{59}", id_evento), id_evento

    @pytest.mark.django_db
    def test_protocolo_no_lugar_da_chave_e_recusado(self, cliente):
        """O erro mais provável: mandar o protocolo de emissão, que não serve."""
        from apps.fiscal import eventos

        with pytest.raises(eventos.ErroEventoInvalido, match="50 dígitos"):
            eventos.montar_cancelamento(
                self._cliente_fiscal(cliente), "NFSE-ABC123", self.JUSTIFICATIVA
            )

    @pytest.mark.django_db
    def test_justificativa_curta_e_recusada_antes_da_sefin(self, cliente):
        """O XSD exige 15 caracteres — recusar aqui dá mensagem melhor."""
        from apps.fiscal import eventos

        with pytest.raises(eventos.ErroEventoInvalido, match="15"):
            eventos.montar_cancelamento(self._cliente_fiscal(cliente), self.CHAVE, "erro")

    @pytest.mark.django_db
    def test_codigo_de_motivo_fora_da_tabela_e_recusado(self, cliente):
        from apps.fiscal import eventos

        with pytest.raises(eventos.ErroEventoInvalido, match="motivo inválido"):
            eventos.montar_cancelamento(
                self._cliente_fiscal(cliente), self.CHAVE, self.JUSTIFICATIVA, codigo_motivo="7"
            )

    @pytest.mark.django_db
    def test_evento_assinado_continua_valido(self, cliente, tmp_path):
        import datetime as dt

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        from apps.fiscal import dps, eventos

        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TESTE")])
        agora = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder().subject_name(n).issuer_name(n)
            .public_key(k.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(agora - dt.timedelta(days=1))
            .not_valid_after(agora + dt.timedelta(days=365)).sign(k, hashes.SHA256())
        )
        pfx = serialization.pkcs12.serialize_key_and_certificates(
            b"t", k, cert, None, serialization.BestAvailableEncryption(b"x")
        )
        xml, id_evento = eventos.montar_cancelamento(
            self._cliente_fiscal(cliente), self.CHAVE, self.JUSTIFICATIVA
        )
        assinado = dps.assinar(xml, pfx, "x", id_evento)
        assert not eventos.validar_evento_contra_xsd(assinado)


@pytest.mark.django_db
def test_emissao_guarda_a_chave_de_acesso_nao_so_o_protocolo(cliente, nota_emitida):
    """Sem a chave, o cancelamento real é impossível — o protocolo não serve."""
    assert len(nota_emitida.chave_nfse) == 50
    assert nota_emitida.chave_nfse.isdigit()
    assert nota_emitida.chave_nfse != nota_emitida.protocolo
