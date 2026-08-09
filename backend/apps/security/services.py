"""
Serviços do vínculo de sessão `wa_id ↔ CNPJ`, Magic Link e 2FA — spec em
`docs/magicbi-seguranca-sessao.md`.

Regra de composição com o resto do núcleo: este módulo só decide QUEM está
falando. O que essa pessoa pode fazer continua sendo decidido por
`apps.governance` (tiers). As duas camadas são independentes.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

import jwt
import structlog
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.audit.services import registrar
from apps.clients.telefone import mesmo_numero

from .models import Codigo2FA, SessaoWhatsapp, TokenMagicLink

logger = structlog.get_logger(__name__)

_ALGORITMO = "HS256"


class ErroEmailAusente(Exception):
    """Cliente sem e-mail cadastrado — não é seguro mandar Magic Link/2FA pelo WhatsApp."""


def _chave_assinatura() -> str:
    return getattr(settings, "MAGICLINK_SIGNING_KEY", "") or settings.SECRET_KEY


# ---------------------------------------------------------------------------
# Sessão — verificação a cada mensagem recebida
# ---------------------------------------------------------------------------
def sessao_ativa(cliente, wa_id: str | None = None) -> bool:
    """True se o cliente tem sessão ATIVA, não expirada e com wa_id consistente.

    `wa_id` é o número de **quem está escrevendo agora**. Passe-o sempre que
    houver mensagem: com o nível `usuario` (DEC-03) uma empresa tem vários
    números autorizados, e comparar a sessão com "o telefone da empresa" deixou
    de significar alguma coisa — o certo é comparar com quem digitou.

    Sem `wa_id` (telas do Grimório, relatórios), a checagem de número é pulada e
    valem só status e vencimento. É leitura de estado, não porta de entrada.

    **Limite conhecido:** a sessão é uma por empresa. Se o sócio validar e depois
    o financeiro escrever, o financeiro recebe o pedido de validação e, ao
    validar, assume a sessão. Falha para o lado seguro (pede identidade em vez
    de conceder), mas duas pessoas da mesma empresa não ficam ativas ao mesmo
    tempo. Sessão por par (usuário, empresa) é o passo seguinte.

    Expira/bloqueia a sessão em caso de vencimento ou divergência de número —
    nunca falha em silêncio, sempre grava o motivo na auditoria.
    """
    if cliente is None:
        return False
    try:
        sessao = cliente.sessao_whatsapp
    except SessaoWhatsapp.DoesNotExist:
        return False

    if sessao.status != SessaoWhatsapp.Status.ATIVA:
        return False

    # Comparação pela forma canônica, não pela string: `5599991332604` e
    # `559991332604` são o MESMO assinante (nono dígito, ver
    # apps/clients/telefone.py). Comparando cru, uma simples troca de grafia no
    # cadastro bloquearia a sessão do cliente como se fosse clonagem — e a
    # anticlonagem continua estrita, porque igualdade canônica só aproxima
    # grafias do mesmo número, nunca números diferentes.
    if wa_id and sessao.wa_id and not mesmo_numero(sessao.wa_id, wa_id):
        # Quem escreve não é quem validou. Nunca herda a sessão automaticamente
        # — é a proteção contra clonagem e troca de número, e é também o que
        # impede que cadastrar o telefone de um colega conceda, sozinho,
        # autoridade fiscal sobre a empresa.
        sessao.status = SessaoWhatsapp.Status.BLOQUEADA
        sessao.save(update_fields=["status", "atualizado_em"])
        registrar(
            "sessao_whatsapp_wa_id_divergente_bloqueada",
            {"wa_id_sessao": sessao.wa_id, "wa_id_atual": wa_id},
            cliente=cliente,
        )
        return False

    if sessao.expira_em and sessao.expira_em <= timezone.now():
        sessao.status = SessaoWhatsapp.Status.EXPIRADA
        sessao.save(update_fields=["status", "atualizado_em"])
        registrar("sessao_whatsapp_expirada", {}, cliente=cliente)
        return False

    return True


def expirar_sessoes_vencidas() -> int:
    """Varre sessões ativas vencidas e expira — não depende só da checagem por mensagem.

    Chamado por uma tarefa Celery periódica (ver `apps/security/tasks.py`).
    """
    vencidas = SessaoWhatsapp.objects.filter(
        status=SessaoWhatsapp.Status.ATIVA, expira_em__lte=timezone.now()
    )
    total = 0
    for sessao in vencidas:
        sessao.status = SessaoWhatsapp.Status.EXPIRADA
        sessao.save(update_fields=["status", "atualizado_em"])
        registrar("sessao_whatsapp_expirada", {"via": "job_periodico"}, cliente=sessao.cliente)
        total += 1
    return total


# ---------------------------------------------------------------------------
# Magic Link
# ---------------------------------------------------------------------------
def gerar_magic_link(cliente, wa_id: str) -> str:
    """Cria um token de uso único (padrão 15 min) e devolve a URL de validação."""
    ttl = timedelta(minutes=getattr(settings, "MAGICLINK_TTL_MINUTOS", 15))
    expira_em = timezone.now() + ttl
    jti = uuid.uuid4().hex

    TokenMagicLink.objects.create(cliente=cliente, wa_id=wa_id, jti=jti, expira_em=expira_em)

    payload = {"cliente_id": cliente.id, "wa_id": wa_id, "jti": jti, "exp": expira_em}
    token = jwt.encode(payload, _chave_assinatura(), algorithm=_ALGORITMO)

    base = getattr(settings, "PAINEL_BASE_URL", "http://localhost:8000")
    return f"{base}/security/validar/{token}/"


def enviar_magic_link(cliente, wa_id: str) -> bool:
    """Gera e envia o Magic Link por e-mail. Nunca pelo WhatsApp (§3 do doc de segurança).

    False se o cliente não tem e-mail cadastrado — degrada avisando o
    contador em vez de travar ou usar um canal inseguro.
    """
    if not cliente.email_contato:
        registrar("magic_link_sem_email_cadastrado", {}, cliente=cliente)
        logger.warning("magic_link_sem_email_cadastrado", cliente_id=cliente.id)
        return False

    link = gerar_magic_link(cliente, wa_id)
    send_mail(
        subject="Magic BI — valide seu acesso",
        message=(
            f"Olá, {cliente.nome}!\n\n"
            f"Clique no link para validar seu acesso ao Magic BI (expira em "
            f"{getattr(settings, 'MAGICLINK_TTL_MINUTOS', 15)} minutos):\n{link}\n\n"
            "Se você não pediu isso, ignore este e-mail."
        ),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@magicbi.local"),
        recipient_list=[cliente.email_contato],
        fail_silently=False,
    )
    registrar("magic_link_enviado", {"wa_id": wa_id}, cliente=cliente)
    return True


def validar_magic_link(token: str) -> tuple[bool, str]:
    """Valida o token e ativa/renova a `SessaoWhatsapp`. Devolve (ok, mensagem)."""
    try:
        claims = jwt.decode(token, _chave_assinatura(), algorithms=[_ALGORITMO])
    except jwt.ExpiredSignatureError:
        return False, "Este link expirou. Peça um novo pelo WhatsApp."
    except jwt.InvalidTokenError:
        return False, "Link inválido."

    jti = claims.get("jti")
    registro = TokenMagicLink.objects.filter(jti=jti).first()
    if registro is None:
        return False, "Link inválido ou já processado."
    if registro.usado_em is not None:
        registrar("magic_link_reuso_bloqueado", {"jti": jti}, cliente=registro.cliente)
        return False, "Este link já foi usado. Peça um novo pelo WhatsApp."
    if registro.expira_em <= timezone.now():
        return False, "Este link expirou. Peça um novo pelo WhatsApp."

    registro.usado_em = timezone.now()
    registro.save(update_fields=["usado_em"])

    ttl = timedelta(days=getattr(settings, "SESSAO_TTL_DIAS", 7))
    agora = timezone.now()
    SessaoWhatsapp.objects.update_or_create(
        cliente=registro.cliente,
        defaults={
            "wa_id": registro.wa_id,
            "status": SessaoWhatsapp.Status.ATIVA,
            "validado_em": agora,
            "expira_em": agora + ttl,
        },
    )
    registrar("sessao_whatsapp_ativada", {"wa_id": registro.wa_id}, cliente=registro.cliente)
    return True, "Sessão validada! Pode voltar para a conversa no WhatsApp. 🔓"


# ---------------------------------------------------------------------------
# 2FA por código avulso — só para ações acima do threshold do perfil
# ---------------------------------------------------------------------------
def _hash_codigo(codigo: str) -> str:
    return hashlib.sha256(codigo.encode("utf-8")).hexdigest()


def exige_2fa(intencao) -> bool:
    """True se o valor da intenção passa o threshold configurado no perfil do cliente."""
    perfil = getattr(intencao.cliente, "perfil", None)
    limite = getattr(perfil, "valor_2fa_acima_de", None)
    if limite is None:
        return False
    valor = intencao.payload.get("valor")
    return valor is not None and float(valor) > float(limite)


def gerar_codigo_2fa(intencao) -> Codigo2FA | None:
    """Gera e envia (por e-mail) um código de 6 dígitos para confirmar a intenção.

    None se o cliente não tem e-mail cadastrado — quem chama decide a
    mensagem de degradação (nunca manda o código pelo WhatsApp).
    """
    cliente = intencao.cliente
    if not cliente.email_contato:
        registrar("codigo_2fa_sem_email_cadastrado", {"intencao_id": intencao.id}, cliente=cliente)
        return None

    codigo = f"{secrets.randbelow(1_000_000):06d}"
    ttl = timedelta(minutes=getattr(settings, "CODIGO_2FA_TTL_MINUTOS", 5))
    registro = Codigo2FA.objects.create(
        cliente=cliente,
        intencao=intencao,
        codigo_hash=_hash_codigo(codigo),
        expira_em=timezone.now() + ttl,
    )
    send_mail(
        subject="Magic BI — código de confirmação",
        message=f"Seu código de confirmação é {codigo} (válido por {ttl.seconds // 60} minutos).",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@magicbi.local"),
        recipient_list=[cliente.email_contato],
        fail_silently=False,
    )
    registrar("codigo_2fa_enviado", {"intencao_id": intencao.id}, cliente=cliente)
    return registro


def verificar_codigo_2fa(codigo_pendente: Codigo2FA, tentativa: str) -> bool:
    """Confere o código informado; incrementa tentativas e expira o registro em caso de acerto."""
    if codigo_pendente.usado_em is not None or codigo_pendente.expira_em <= timezone.now():
        return False

    if _hash_codigo(tentativa.strip()) != codigo_pendente.codigo_hash:
        codigo_pendente.tentativas += 1
        codigo_pendente.save(update_fields=["tentativas"])
        registrar(
            "codigo_2fa_tentativa_invalida",
            {"intencao_id": codigo_pendente.intencao_id, "tentativas": codigo_pendente.tentativas},
            cliente=codigo_pendente.cliente,
        )
        return False

    codigo_pendente.usado_em = timezone.now()
    codigo_pendente.save(update_fields=["usado_em"])
    registrar("codigo_2fa_confirmado", {"intencao_id": codigo_pendente.intencao_id}, cliente=codigo_pendente.cliente)
    return True
