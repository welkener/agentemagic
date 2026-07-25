"""Dashboard do Grimório — visão consolidada para demonstração/homologação.

Só leitura: agrega dados que já existem (Intencao, Auditoria, SessaoWhatsapp,
Credencial) sem introduzir novas fontes de verdade. Autenticação reaproveita
o mesmo `auth.User` do Django admin — inclusive o login por Magic Link
(`django-sesame`, `apps/security/management/commands/enviar_link_contador.py`)
já leva direto pra cá (`LOGIN_REDIRECT_URL`).
"""
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.channel_evolution.models import configuracao_ativa
from apps.clients.models import Cliente
from apps.credentials.models import Credencial
from apps.security.models import SessaoWhatsapp

from .models import escritorio_ativo


@require_GET
@staff_member_required
def dashboard(request):
    agora = timezone.now()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    notas_concluidas = Intencao.objects.filter(estado=Intencao.Estado.CONCLUIDO)
    notas_recentes = notas_concluidas.order_by("-atualizado_em")[:10]

    escritorio = escritorio_ativo()
    contexto = {
        "escritorio": escritorio,
        "cor_primaria": escritorio.cor_primaria if escritorio else "#1a1a2e",
        "cor_acento": escritorio.cor_acento if escritorio else "#5B67C9",
        "canal_meta_configurado": bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID),
        "canal_evolution": configuracao_ativa(),
        "notas_hoje": notas_concluidas.filter(atualizado_em__gte=inicio_hoje).count(),
        "notas_mes": notas_concluidas.filter(atualizado_em__gte=inicio_mes).count(),
        "notas_recentes": notas_recentes,
        "notas_pendentes": Intencao.objects.filter(estado=Intencao.Estado.AGUARDANDO_APROVACAO).count(),
        "sessoes_ativas": SessaoWhatsapp.objects.filter(status=SessaoWhatsapp.Status.ATIVA).count(),
        "sessoes_pendentes": SessaoWhatsapp.objects.exclude(status=SessaoWhatsapp.Status.ATIVA).count(),
        "clientes_ativos": Cliente.objects.filter(ativo=True).count(),
        "certificados": Credencial.objects.filter(
            integracao="nfse_nacional",
            tipo__in=[Credencial.Tipo.CERTIFICADO_PSC, Credencial.Tipo.CERTIFICADO_PFX],
        ),
        "atividade_recente": Auditoria.objects.order_by("-id")[:15],
    }
    return render(request, "painel/dashboard.html", contexto)
