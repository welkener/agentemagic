"""Testes do vínculo de sessão wa_id↔CNPJ, Magic Link e 2FA (apps/security)."""
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.clients.models import Cliente, Perfil
from apps.core.orchestrator import DadosNotaExtraidos, Orquestrador
from apps.security.models import Codigo2FA, SessaoWhatsapp, TokenMagicLink
from apps.security.services import (
    enviar_magic_link,
    exige_2fa,
    gerar_codigo_2fa,
    gerar_magic_link,
    sessao_ativa,
    validar_magic_link,
    verificar_codigo_2fa,
)


@pytest.fixture
def cliente_sem_sessao(db):
    """Cliente recém-provisionado, ainda sem vínculo wa_id↔CNPJ validado."""
    c = Cliente.objects.create(
        cnpj="98765432000111",
        nome="Oficina do Zé",
        telefone_whatsapp="5511988887777",
        email_contato="ze@oficina.example.com",
        cnae_padrao="4520-0/01",
        ativo=True,
    )
    Perfil.objects.create(cliente=c, tier_maximo=1, ferramentas_habilitadas=["nfse_mock"])
    return c


# ---------------------------------------------------------------------------
# sessao_ativa
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_sessao_ativa_falsa_sem_registro(cliente_sem_sessao):
    assert sessao_ativa(cliente_sem_sessao) is False


def test_sessao_ativa_falsa_para_cliente_none():
    assert sessao_ativa(None) is False


@pytest.mark.django_db
def test_sessao_expirada_e_marcada_e_bloqueia(cliente_sem_sessao):
    agora = timezone.now()
    SessaoWhatsapp.objects.create(
        cliente=cliente_sem_sessao,
        wa_id=cliente_sem_sessao.telefone_whatsapp,
        status=SessaoWhatsapp.Status.ATIVA,
        validado_em=agora - timedelta(days=10),
        expira_em=agora - timedelta(days=3),
    )
    assert sessao_ativa(cliente_sem_sessao) is False
    sessao = SessaoWhatsapp.objects.get(cliente=cliente_sem_sessao)
    assert sessao.status == SessaoWhatsapp.Status.EXPIRADA


@pytest.mark.django_db
def test_wa_id_divergente_bloqueia_sessao(cliente_sem_sessao):
    agora = timezone.now()
    SessaoWhatsapp.objects.create(
        cliente=cliente_sem_sessao,
        wa_id="5511900000000",  # número diferente do cadastro atual
        status=SessaoWhatsapp.Status.ATIVA,
        validado_em=agora,
        expira_em=agora + timedelta(days=7),
    )
    assert sessao_ativa(cliente_sem_sessao) is False
    sessao = SessaoWhatsapp.objects.get(cliente=cliente_sem_sessao)
    assert sessao.status == SessaoWhatsapp.Status.BLOQUEADA


# ---------------------------------------------------------------------------
# Magic Link
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_magic_link_ativa_sessao(cliente_sem_sessao):
    link = gerar_magic_link(cliente_sem_sessao, cliente_sem_sessao.telefone_whatsapp)
    token = link.rsplit("/validar/", 1)[1].rstrip("/")

    ok, mensagem = validar_magic_link(token)
    assert ok is True
    sessao = SessaoWhatsapp.objects.get(cliente=cliente_sem_sessao)
    assert sessao.status == SessaoWhatsapp.Status.ATIVA
    assert sessao_ativa(cliente_sem_sessao) is True


@pytest.mark.django_db
def test_magic_link_nao_pode_ser_reusado(cliente_sem_sessao):
    link = gerar_magic_link(cliente_sem_sessao, cliente_sem_sessao.telefone_whatsapp)
    token = link.rsplit("/validar/", 1)[1].rstrip("/")

    validar_magic_link(token)
    ok, mensagem = validar_magic_link(token)
    assert ok is False
    assert "já foi usado" in mensagem


@pytest.mark.django_db
def test_magic_link_expirado_e_rejeitado(cliente_sem_sessao, settings):
    settings.MAGICLINK_TTL_MINUTOS = 15
    link = gerar_magic_link(cliente_sem_sessao, cliente_sem_sessao.telefone_whatsapp)
    token = link.rsplit("/validar/", 1)[1].rstrip("/")

    registro = TokenMagicLink.objects.get(cliente=cliente_sem_sessao)
    registro.expira_em = timezone.now() - timedelta(minutes=1)
    registro.save(update_fields=["expira_em"])

    ok, mensagem = validar_magic_link(token)
    assert ok is False
    assert "expirou" in mensagem


@pytest.mark.django_db
def test_enviar_magic_link_manda_email(cliente_sem_sessao):
    ok = enviar_magic_link(cliente_sem_sessao, cliente_sem_sessao.telefone_whatsapp)
    assert ok is True
    assert len(mail.outbox) == 1
    assert cliente_sem_sessao.email_contato in mail.outbox[0].to


@pytest.mark.django_db
def test_enviar_magic_link_sem_email_degrada(cliente_sem_sessao):
    cliente_sem_sessao.email_contato = ""
    cliente_sem_sessao.save()
    ok = enviar_magic_link(cliente_sem_sessao, cliente_sem_sessao.telefone_whatsapp)
    assert ok is False
    assert len(mail.outbox) == 0


# ---------------------------------------------------------------------------
# Gate no orquestrador
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_orquestrador_bloqueia_sem_sessao_e_manda_magic_link(cliente_sem_sessao):
    resposta = Orquestrador().processar("oi", cliente_sem_sessao)
    assert "sessão expirou" in resposta.lower() or "valida" in resposta.lower()
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_orquestrador_processa_normalmente_com_sessao_ativa(cliente):
    resposta = Orquestrador().processar("qual meu estoque?", cliente)
    assert "estoque" in resposta.lower()


# ---------------------------------------------------------------------------
# 2FA
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_exige_2fa_respeita_threshold_do_perfil(cliente):
    cliente.perfil.valor_2fa_acima_de = 1000
    cliente.perfil.save()
    intencao = Intencao.objects.create(
        cliente=cliente, chave_idempotencia="k1", payload={"valor": 1500.0}
    )
    assert exige_2fa(intencao) is True

    intencao.payload = {"valor": 500.0}
    assert exige_2fa(intencao) is False


@pytest.mark.django_db
def test_exige_2fa_desligado_por_padrao(cliente):
    intencao = Intencao.objects.create(
        cliente=cliente, chave_idempotencia="k2", payload={"valor": 999999.0}
    )
    assert exige_2fa(intencao) is False


@pytest.mark.django_db
def test_fluxo_completo_com_2fa_acima_do_threshold(cliente):
    cliente.perfil.valor_2fa_acima_de = 1000
    cliente.perfil.save()

    orq = Orquestrador()
    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="Empresa Grande", valor=5000.0, descricao_servico="Consultoria"
    )

    resposta = orq.processar("emite nota de 5000 pra Empresa Grande", cliente, message_id="wamid.2fa")
    assert "Confirma a emissão" in resposta

    resposta = orq.processar("sim", cliente)
    assert "código" in resposta.lower()
    assert len(mail.outbox) == 1

    # A mensagem tem o formato "... código é 123456 (válido por..." — extrai os 6 dígitos.
    import re

    codigo = re.search(r"\b\d{6}\b", mail.outbox[0].body).group()

    resposta = orq.processar(codigo, cliente)
    assert "Nota emitida com sucesso" in resposta
    intencao = Intencao.objects.get(chave_idempotencia="nfse-wamid.2fa")
    assert intencao.estado == Intencao.Estado.CONCLUIDO


@pytest.mark.django_db
def test_2fa_codigo_errado_demais_vezes_cancela(cliente):
    cliente.perfil.valor_2fa_acima_de = 1000
    cliente.perfil.save()

    orq = Orquestrador()
    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="Empresa Grande", valor=5000.0, descricao_servico="Consultoria"
    )
    orq.processar("emite nota de 5000 pra Empresa Grande", cliente, message_id="wamid.2fa-errado")
    orq.processar("sim", cliente)

    for _ in range(Codigo2FA.LIMITE_TENTATIVAS):
        resposta = orq.processar("000000", cliente)

    assert "cancelei" in resposta.lower()
    intencao = Intencao.objects.get(chave_idempotencia="nfse-wamid.2fa-errado")
    assert intencao.estado == Intencao.Estado.CANCELADO


@pytest.mark.django_db
def test_2fa_codigo_expirado_cancela(cliente):
    cliente.perfil.valor_2fa_acima_de = 1000
    cliente.perfil.save()

    orq = Orquestrador()
    orq._extrair_dados_nota = lambda mensagem: DadosNotaExtraidos(
        tomador="Empresa Grande", valor=5000.0, descricao_servico="Consultoria"
    )
    orq.processar("emite nota de 5000 pra Empresa Grande", cliente, message_id="wamid.2fa-exp")
    orq.processar("sim", cliente)

    codigo_pendente = Codigo2FA.objects.get(intencao__chave_idempotencia="nfse-wamid.2fa-exp")
    codigo_pendente.expira_em = timezone.now() - timedelta(minutes=1)
    codigo_pendente.save(update_fields=["expira_em"])

    resposta = orq.processar("123456", cliente)
    assert "expirou" in resposta.lower()
    intencao = Intencao.objects.get(chave_idempotencia="nfse-wamid.2fa-exp")
    assert intencao.estado == Intencao.Estado.CANCELADO


@pytest.mark.django_db
def test_gerar_codigo_2fa_sem_email_degrada(cliente):
    cliente.email_contato = ""
    cliente.save()
    intencao = Intencao.objects.create(
        cliente=cliente, chave_idempotencia="k3", payload={"valor": 1500.0}
    )
    registro = gerar_codigo_2fa(intencao)
    assert registro is None
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_verificar_codigo_2fa_hash_nao_bate(cliente):
    intencao = Intencao.objects.create(
        cliente=cliente, chave_idempotencia="k4", payload={"valor": 1500.0}
    )
    registro = gerar_codigo_2fa(intencao)
    assert verificar_codigo_2fa(registro, "000000") is False
    registro.refresh_from_db()
    assert registro.tentativas == 1
