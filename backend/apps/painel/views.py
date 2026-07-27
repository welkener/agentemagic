"""Grimório — o painel do contador, dentro do admin.

Decisão 26/jul/2026: o `/painel/` era página HTML solta, fora do django-unfold —
duas superfícies web, dois visuais. Virou o índice do admin.

Decisão 27/jul/2026 (feedback: "queremos uma plataforma igual aos concorrentes
pra os contadores analisarem os dados, notas, clientes, integração,
documentos"): uma superfície só resolveu a incoerência visual, mas o que havia
era um bom *admin* — contadores e listas. O que o contador compara com o
concorrente é a camada **analítica**: evolução do faturamento, quem está
perto do teto do MEI, o que falta ligar em cada cliente. Changelist não faz
isso, por mais bonito que esteja.

Então o Grimório passa a ter, além do dashboard, três páginas próprias —
Carteira, Integrações e Documentos — construídas com `UnfoldModelAdminViewMixin`
para herdar header/sidebar/tema do Unfold em vez de virarem outra ilha de CSS.
As contas ficam em `metricas.py`; aqui só há montagem de contexto.

Continua tudo **somente leitura**: nenhuma dessas páginas cria fonte de verdade
nova, e toda query passa pelo escopo de tenant do admin.
"""
import json
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from unfold.views import UnfoldModelAdminViewMixin

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.channel_evolution.models import configuracao_ativa
from apps.clients.models import Cliente
from apps.credentials.models import Credencial
from apps.fiscal import teto_mei
from apps.painel import metricas
from apps.security.models import SessaoWhatsapp
from apps.tenants.escopo import escopo_do_usuario

# Cor única por gráfico: cada um tem uma série só, então não há identidade a
# distinguir — repetir o indigo do tema é o certo. Paleta categórica (várias
# séries no mesmo gráfico) exigiria validação de contraste/daltonismo; aqui
# não existe esse caso, e inventar cores diferentes só decoraria.
COR_PRIMARIA = "#4f46e5"  # indigo-600, mesma família do UNFOLD["COLORS"]["primary"]
COR_PRIMARIA_SUAVE = "rgba(79, 70, 229, 0.12)"


def _dataset_linha(rotulos, valores, nome):
    """Dados no formato que o Chart.js do Unfold lê via `data-value`.

    Marca fina (2px) e ponto de 4px seguem a mesma regra do resto do tema: o
    dado é o protagonista, a decoração recua.
    """
    return json.dumps(
        {
            "labels": rotulos,
            "datasets": [
                {
                    "label": nome,
                    "data": valores,
                    "borderColor": COR_PRIMARIA,
                    "backgroundColor": COR_PRIMARIA_SUAVE,
                    "borderWidth": 2,
                    "pointRadius": 4,
                    "pointHoverRadius": 6,
                    "tension": 0.3,
                    "fill": True,
                }
            ],
        }
    )


def _dataset_barra(rotulos, valores, nome):
    return json.dumps(
        {
            "labels": rotulos,
            "datasets": [
                {
                    "label": nome,
                    "data": valores,
                    "backgroundColor": COR_PRIMARIA,
                    "borderRadius": 4,  # extremidade arredondada, ancorada na base
                    "borderSkipped": False,
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Dashboard (índice do admin)
# ---------------------------------------------------------------------------
def metricas_do_dashboard(request=None) -> dict:
    """Números e listas do dashboard. Separado do callback pra ser testável sozinho."""
    usuario = getattr(request, "user", None)
    agora = timezone.now()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_mes = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    irrestrito, escritorio = escopo_do_usuario(usuario)

    def escopar(qs, campo="cliente__escritorio"):
        if irrestrito:
            return qs
        if escritorio is None:
            return qs.none()
        return qs.filter(**{campo: escritorio})

    notas = metricas.notas_emitidas(usuario)
    serie = metricas.serie_mensal(usuario)
    alertas_teto = metricas.alertas_de_teto(usuario)

    faturamento_mes = sum(
        (n.valor or 0) for n in notas.filter(atualizado_em__gte=inicio_mes)
    )

    canal_meta = (
        escritorio.canal_whatsapp_configurado
        if escritorio is not None
        else bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)
    )

    return {
        "escritorio": escritorio,
        "canal_meta_configurado": canal_meta,
        "canal_evolution": configuracao_ativa(escritorio),
        "notas_hoje": notas.filter(atualizado_em__gte=inicio_hoje).count(),
        "notas_mes": notas.filter(atualizado_em__gte=inicio_mes).count(),
        "faturamento_mes": faturamento_mes,
        "notas_recentes": notas.select_related("cliente").order_by("-atualizado_em")[:10],
        "notas_pendentes": escopar(
            Intencao.objects.filter(estado=Intencao.Estado.AGUARDANDO_APROVACAO)
        ).count(),
        # Rejeição fiscal exige ação humana. O e-mail avisa
        # (apps/observabilidade/alertas.py), mas e-mail se perde — o número aqui
        # é a rede de segurança de quem só abre o painel.
        "rejeicoes_24h": escopar(
            Intencao.objects.filter(
                estado=Intencao.Estado.REJEITADO, atualizado_em__gte=agora - timedelta(hours=24)
            )
        ).count(),
        "sessoes_ativas": escopar(
            SessaoWhatsapp.objects.filter(status=SessaoWhatsapp.Status.ATIVA)
        ).count(),
        "sessoes_pendentes": escopar(
            SessaoWhatsapp.objects.exclude(status=SessaoWhatsapp.Status.ATIVA)
        ).count(),
        "clientes_ativos": escopar(
            Cliente.objects.filter(ativo=True), campo="escritorio"
        ).count(),
        "certificados": escopar(
            Credencial.objects.filter(
                integracao="nfse_nacional",
                tipo__in=[Credencial.Tipo.CERTIFICADO_PSC, Credencial.Tipo.CERTIFICADO_PFX],
            )
        ).select_related("cliente"),
        "atividade_recente": escopar(
            Auditoria.objects.select_related("cliente")
        ).order_by("-id")[:15],
        # --- camada analítica -------------------------------------------
        # Dois gráficos, não um com dois eixos: reais e unidades têm escalas
        # diferentes, e sobrepor as duas curvas num eixo só faz a menor virar
        # uma linha reta colada no zero — o erro de leitura mais comum em
        # dashboard financeiro.
        "grafico_faturamento": _dataset_linha(
            serie["rotulos"], serie["faturamentos"], "Faturamento (R$)"
        ),
        "grafico_notas": _dataset_barra(
            serie["rotulos"], serie["quantidades"], "Notas emitidas"
        ),
        "alertas_teto": alertas_teto[:5],
        "total_alertas_teto": len(alertas_teto),
        # --- links -------------------------------------------------------
        "fila_aprovacao_url": reverse("admin:agente_nf_intencao_changelist"),
        "auditoria_url": reverse("admin:audit_auditoria_changelist"),
        "credenciais_url": reverse("admin:credentials_credencial_changelist"),
        "evolution_url": reverse("admin:channel_evolution_configuracaoevolution_changelist"),
        "evolution_add_url": reverse("admin:channel_evolution_configuracaoevolution_add"),
        "carteira_url": reverse("admin:painel_carteira"),
        "integracoes_url": reverse("admin:painel_integracoes"),
        "documentos_url": reverse("admin:painel_documentos"),
    }


# Nome antigo mantido: `metricas()` era importado pelos testes e pelo callback
# antes do módulo `metricas.py` existir. Renomear sem alias quebraria quem
# importa, e o alias custa uma linha.
def dashboard_callback(request, context: dict) -> dict:
    """Hook do django-unfold — injeta as métricas no contexto de `admin/index.html`."""
    context.update(metricas_do_dashboard(request))
    return context


# ---------------------------------------------------------------------------
# Páginas próprias do Grimório
# ---------------------------------------------------------------------------
class PaginaDoGrimorio(UnfoldModelAdminViewMixin, TemplateView):
    """Base das páginas analíticas — herda header, sidebar e tema do Unfold.

    `permission_required` é o mesmo `view_` do model correspondente: quem pode
    ver a listagem pode ver a análise dela. Não é um segundo sistema de
    permissão, é o mesmo — e o conteúdo ainda passa pelo escopo de tenant.
    """

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["usuario_irrestrito"], contexto["escritorio"] = escopo_do_usuario(
            self.request.user
        )
        return contexto


class CarteiraView(PaginaDoGrimorio):
    """Uma linha por cliente, com faturamento, radar de teto e o que falta."""

    title = "Carteira de clientes"
    permission_required = ("clients.view_cliente",)
    template_name = "painel/carteira.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        linhas = metricas.carteira(self.request.user)
        contexto.update(
            {
                "linhas": linhas,
                "total_faturamento": sum(linha.faturamento_ano for linha in linhas),
                "total_notas": sum(linha.notas_ano for linha in linhas),
                "incompletos": sum(1 for linha in linhas if not linha.pronto_para_emitir),
                "em_risco": sum(
                    1
                    for linha in linhas
                    if linha.uso_teto.aplicavel and linha.uso_teto.situacao != "tranquilo"
                ),
                # String, não int: com USE_THOUSAND_SEPARATOR o template
                # localizaria o ano e escreveria "2.026".
                "ano": str(timezone.localdate().year),
                "teto_anual": teto_mei.teto_anual(),
            }
        )
        return contexto


class IntegracoesView(PaginaDoGrimorio):
    """O que está ligado em cada cliente — canal, ERP e certificado."""

    title = "Integrações"
    permission_required = ("credentials.view_credencial",)
    template_name = "painel/integracoes.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        linhas = metricas.integracoes(self.request.user)
        contexto.update(
            {
                "linhas": linhas,
                "com_pendencia": sum(1 for linha in linhas if linha.pendencias),
                "vencendo": [
                    linha
                    for linha in linhas
                    if linha.dias_para_vencer is not None and linha.dias_para_vencer <= 30
                ],
                "canal_evolution": configuracao_ativa(contexto.get("escritorio")),
                "credenciais_url": reverse("admin:credentials_credencial_changelist"),
                "credencial_add_url": reverse("admin:credentials_credencial_add"),
            }
        )
        return contexto


class DocumentosView(PaginaDoGrimorio):
    """Notas emitidas e seus artefatos fiscais, agrupadas por mês."""

    title = "Documentos fiscais"
    permission_required = ("agente_nf.view_intencao",)
    template_name = "painel/documentos.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        grupos = metricas.documentos(self.request.user)
        contexto.update(
            {
                "grupos": grupos,
                "total": sum(len(notas) for notas in grupos.values()),
            }
        )
        return contexto
