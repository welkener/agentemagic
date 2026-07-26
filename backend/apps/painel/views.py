"""Dashboard do Grimório — a **home do admin**, não uma página separada.

Decisão 26/jul/2026 (feedback: "não entendi o painel sendo que já vamos ter o
admin do django, pq não deixa o painel como se fosse dashboard"): o `/painel/`
era uma página HTML solta, com CSS próprio, fora do django-unfold — duas
superfícies web, dois visuais, dois lugares pra procurar a mesma coisa. Ele
nunca teve CRUD (sempre foi leitura pura), então não havia o que separar: o
lugar natural dele é o índice do admin.

Como funciona agora: `DASHBOARD_CALLBACK` (config/settings.py → UNFOLD) chama
`dashboard_callback` a cada render de `/admin/`, e `templates/admin/index.html`
desenha os cards com os componentes do Unfold, acima da lista de apps.

Continua só leitura: agrega dados que já existem (Intencao, Auditoria,
SessaoWhatsapp, Credencial) sem introduzir novas fontes de verdade.
"""
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.channel_evolution.models import configuracao_ativa
from apps.clients.models import Cliente
from apps.credentials.models import Credencial
from apps.security.models import SessaoWhatsapp


def metricas(request=None) -> dict:
    """Números e listas do dashboard. Separado do callback pra ser testável sozinho."""
    agora = timezone.now()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    notas_concluidas = Intencao.objects.filter(estado=Intencao.Estado.CONCLUIDO)
    fila_url = reverse("admin:agente_nf_intencao_changelist")

    return {
        "canal_meta_configurado": bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID),
        "canal_evolution": configuracao_ativa(),
        "notas_hoje": notas_concluidas.filter(atualizado_em__gte=inicio_hoje).count(),
        "notas_mes": notas_concluidas.filter(atualizado_em__gte=inicio_mes).count(),
        "notas_recentes": notas_concluidas.order_by("-atualizado_em")[:10],
        "notas_pendentes": Intencao.objects.filter(
            estado=Intencao.Estado.AGUARDANDO_APROVACAO
        ).count(),
        "sessoes_ativas": SessaoWhatsapp.objects.filter(
            status=SessaoWhatsapp.Status.ATIVA
        ).count(),
        "sessoes_pendentes": SessaoWhatsapp.objects.exclude(
            status=SessaoWhatsapp.Status.ATIVA
        ).count(),
        "clientes_ativos": Cliente.objects.filter(ativo=True).count(),
        "certificados": Credencial.objects.filter(
            integracao="nfse_nacional",
            tipo__in=[Credencial.Tipo.CERTIFICADO_PSC, Credencial.Tipo.CERTIFICADO_PFX],
        ).select_related("cliente"),
        "atividade_recente": Auditoria.objects.select_related("cliente").order_by("-id")[:15],
        "fila_aprovacao_url": fila_url,
        "auditoria_url": reverse("admin:audit_auditoria_changelist"),
        "credenciais_url": reverse("admin:credentials_credencial_changelist"),
        "evolution_url": reverse("admin:channel_evolution_configuracaoevolution_changelist"),
        "evolution_add_url": reverse("admin:channel_evolution_configuracaoevolution_add"),
    }


def dashboard_callback(request, context: dict) -> dict:
    """Hook do django-unfold — injeta as métricas no contexto de `admin/index.html`."""
    context.update(metricas(request))
    return context
