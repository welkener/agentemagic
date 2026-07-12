"""Testes do serviço de confirmação (agente_nf/services.py) e da fila de
aprovação do contador no Django admin — o "Grimório mínimo" antes do painel
React (Semana 5)."""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.agents.agente_nf.models import Intencao, TransicaoInvalida
from apps.agents.agente_nf.services import cancelar_emissao, confirmar_emissao
from apps.audit.models import Auditoria


@pytest.fixture
def intencao_aguardando(cliente):
    return Intencao.objects.create(
        cliente=cliente,
        chave_idempotencia="admin-teste-001",
        tipo_acao="emitir_nfse",
        payload={
            "cnpj_prestador": cliente.cnpj,
            "cnae": cliente.cnae_padrao,
            "valor": 500.0,
            "descricao_servico": "Corte de cabelo",
            "tomador": "João",
        },
        estado=Intencao.Estado.AGUARDANDO_APROVACAO,
    )


@pytest.fixture
def contador(db):
    return get_user_model().objects.create_superuser("contador", "contador@exemplo.com", "senha-teste-123")


@pytest.mark.django_db
def test_confirmar_emissao_com_sucesso_usa_o_mock(intencao_aguardando):
    resultado = confirmar_emissao(intencao_aguardando, motivo="teste")
    assert resultado.ok is True
    assert resultado.protocolo
    intencao_aguardando.refresh_from_db()
    assert intencao_aguardando.estado == Intencao.Estado.CONCLUIDO


@pytest.mark.django_db
def test_confirmar_emissao_fora_de_aguardando_aprovacao_levanta_erro(intencao_aguardando):
    intencao_aguardando.transicionar(Intencao.Estado.EMITINDO)
    intencao_aguardando.transicionar(Intencao.Estado.CONCLUIDO)
    with pytest.raises(TransicaoInvalida):
        confirmar_emissao(intencao_aguardando, motivo="teste")


@pytest.mark.django_db
def test_cancelar_emissao(intencao_aguardando):
    cancelar_emissao(intencao_aguardando, motivo="teste")
    intencao_aguardando.refresh_from_db()
    assert intencao_aguardando.estado == Intencao.Estado.CANCELADO


@pytest.mark.django_db
def test_admin_aprova_e_emite(client, contador, intencao_aguardando):
    client.force_login(contador)
    url = reverse("admin:agente_nf_intencao_changelist")
    resposta = client.post(
        url, {"action": "aprovar_e_emitir", "_selected_action": [str(intencao_aguardando.id)]}, follow=True
    )
    assert resposta.status_code == 200

    intencao_aguardando.refresh_from_db()
    assert intencao_aguardando.estado == Intencao.Estado.CONCLUIDO
    assert Auditoria.objects.filter(
        evento="intencao_fiscal_transicao", dados__motivo__icontains="aprovado via admin"
    ).exists()


@pytest.mark.django_db
def test_admin_rejeita_pendente(client, contador, intencao_aguardando):
    client.force_login(contador)
    url = reverse("admin:agente_nf_intencao_changelist")
    resposta = client.post(
        url, {"action": "rejeitar_pendentes", "_selected_action": [str(intencao_aguardando.id)]}, follow=True
    )
    assert resposta.status_code == 200

    intencao_aguardando.refresh_from_db()
    assert intencao_aguardando.estado == Intencao.Estado.CANCELADO


@pytest.mark.django_db
def test_admin_ignora_intencao_fora_do_estado_esperado(client, contador, intencao_aguardando):
    intencao_aguardando.transicionar(Intencao.Estado.CANCELADO)
    client.force_login(contador)
    url = reverse("admin:agente_nf_intencao_changelist")
    resposta = client.post(
        url, {"action": "aprovar_e_emitir", "_selected_action": [str(intencao_aguardando.id)]}, follow=True
    )
    assert resposta.status_code == 200
    intencao_aguardando.refresh_from_db()
    assert intencao_aguardando.estado == Intencao.Estado.CANCELADO  # não mudou


@pytest.mark.django_db
def test_admin_nao_permite_editar_campos_diretamente(client, contador, intencao_aguardando):
    client.force_login(contador)
    url = reverse("admin:agente_nf_intencao_change", args=[intencao_aguardando.id])
    resposta = client.get(url)
    # has_change_permission=False — Django mostra a tela em modo só-leitura
    # (todos os campos em readonly_fields), sem botão de salvar.
    assert resposta.status_code == 200
    assert b'name="_save"' not in resposta.content
