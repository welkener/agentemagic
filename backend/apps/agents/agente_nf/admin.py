"""
Fila de aprovação do contador — "Grimório mínimo" (painel React vem na
Semana 5 do MVP; até lá, o Django admin cobre o requisito "primeira emissão
de cada cliente com aprovação do contador"). Sem edição direta de campos —
as duas ações abaixo são a única forma de mudar o estado por aqui, e passam
pela mesma máquina de estados/auditoria do fluxo por WhatsApp
(`apps/agents/agente_nf/services.py`).
"""
from django.contrib import admin, messages
from django.urls import path
from unfold.admin import ModelAdmin

from apps.tenants.escopo import EscopoEscritorioMixin

from .models import Intencao, TransicaoInvalida
from .services import cancelar_emissao, confirmar_cancelamento, confirmar_emissao


@admin.register(Intencao)
class IntencaoAdmin(EscopoEscritorioMixin, ModelAdmin):
    """Duplica como a visão "notas emitidas" pra demonstração/homologação —
    filtrar por `estado=CONCLUIDO` já mostra protocolo/DANFSE de cada nota
    real emitida (mock ou adapter real, o campo é preenchido nos dois casos
    desde que `confirmar_emissao` rode — ver `services.py`)."""

    list_display = (
        "id",
        "cliente",
        "tipo_acao",
        "estado",
        "valor_formatado",
        "protocolo",
        "situacao_fiscal",
        "criado_em",
    )
    list_filter = ("estado", "tipo_acao")
    search_fields = ("cliente__nome", "cliente__cnpj", "chave_idempotencia", "protocolo")
    readonly_fields = [f.name for f in Intencao._meta.fields]
    actions = ["aprovar_e_emitir", "rejeitar_pendentes"]

    def get_urls(self):
        """Página Documentos fiscais — antes de `super()` por causa do
        catch-all `<path:object_id>/` (ver nota em apps/clients/admin.py)."""
        from apps.painel.views import DocumentosView

        return [
            path(
                "documentos/",
                self.admin_site.admin_view(DocumentosView.as_view(model_admin=self)),
                name="painel_documentos",
            ),
        ] + super().get_urls()

    @admin.display(description="valor", ordering="valor")
    def valor_formatado(self, obj):
        """Lê o campo desnormalizado, não o payload: assim a coluna ordena no
        banco (`ordering`), e ordenar por valor é metade do uso de uma lista
        de notas."""
        return f"R$ {obj.valor:.2f}" if obj.valor is not None else "—"

    @admin.display(description="situação fiscal")
    def situacao_fiscal(self, obj):
        """Nota cancelada continua CONCLUIDO (ela foi emitida de verdade) —
        sem esta coluna, o contador não distinguiria uma da outra na lista."""
        if obj.tipo_acao == "cancelar_nfse":
            alvo = obj.intencao_original
            return f"cancelar nota {alvo.protocolo}" if alvo else "cancelar (nota ausente)"
        if obj.cancelada:
            return f"CANCELADA em {obj.cancelada_em:%d/%m/%Y}"
        return "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Aprovar (emite a nota, ou cancela se for pedido de cancelamento)")
    def aprovar_e_emitir(self, request, queryset):
        """Uma ação só, que despacha pelo `tipo_acao`.

        Duas ações separadas obrigariam o contador a saber de antemão que tipo
        de item ele selecionou — e escolher a errada só daria erro. O que ele
        decide é "aprovo isto", e o sistema sabe o que "isto" é.
        """

        def acao(intencao):
            motivo = f"aprovado via admin por {request.user}"

            if intencao.tipo_acao == "cancelar_nfse":
                resultado = confirmar_cancelamento(
                    intencao, motivo=motivo, usuario=str(request.user)
                )
                nota = intencao.intencao_original
                if resultado.ok:
                    return f"Nota {nota.protocolo} CANCELADA — protocolo {resultado.protocolo}."
                return f"Cancelamento da nota {nota.protocolo} rejeitado pela Sefin: {resultado.erro}."

            resultado = confirmar_emissao(
                intencao,
                motivo=motivo,
                origem="equipe_admin",
                usuario=str(request.user),
            )
            if resultado.ok:
                return f"Intenção {intencao.id} emitida — protocolo {resultado.protocolo}."
            return f"Intenção {intencao.id} rejeitada pela Sefin: {resultado.erro}."

        self._executar_por_intencao(request, queryset, acao)

    @admin.action(description="Rejeitar/cancelar pendentes")
    def rejeitar_pendentes(self, request, queryset):
        def acao(intencao):
            cancelar_emissao(intencao, motivo=f"cancelado via admin por {request.user}")
            return f"Intenção {intencao.id} cancelada."

        self._executar_por_intencao(request, queryset, acao)

    def _executar_por_intencao(self, request, queryset, acao):
        for intencao in queryset:
            if intencao.estado != Intencao.Estado.AGUARDANDO_APROVACAO:
                self.message_user(
                    request,
                    f"Intenção {intencao.id} ignorada — não está aguardando aprovação (estado: {intencao.estado}).",
                    level=messages.WARNING,
                )
                continue
            try:
                self.message_user(request, acao(intencao))
            except TransicaoInvalida as exc:
                self.message_user(request, f"Intenção {intencao.id}: {exc}", level=messages.ERROR)
