"""
Fila de aprovação do contador — "Grimório mínimo" (painel React vem na
Semana 5 do MVP; até lá, o Django admin cobre o requisito "primeira emissão
de cada cliente com aprovação do contador"). Sem edição direta de campos —
as duas ações abaixo são a única forma de mudar o estado por aqui, e passam
pela mesma máquina de estados/auditoria do fluxo por WhatsApp
(`apps/agents/agente_nf/services.py`).
"""
from django.contrib import admin, messages

from .models import Intencao, TransicaoInvalida
from .services import cancelar_emissao, confirmar_emissao


@admin.register(Intencao)
class IntencaoAdmin(admin.ModelAdmin):
    list_display = ("id", "cliente", "tipo_acao", "estado", "criado_em", "atualizado_em")
    list_filter = ("estado", "tipo_acao")
    search_fields = ("cliente__nome", "cliente__cnpj", "chave_idempotencia")
    readonly_fields = [f.name for f in Intencao._meta.fields]
    actions = ["aprovar_e_emitir", "rejeitar_pendentes"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Aprovar e emitir (fila de aprovação do contador)")
    def aprovar_e_emitir(self, request, queryset):
        def acao(intencao):
            resultado = confirmar_emissao(intencao, motivo=f"aprovado via admin por {request.user}")
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
