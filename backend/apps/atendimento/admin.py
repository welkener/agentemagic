"""Backoffice das solicitações. A fila de trabalho mesmo fica no Grimório."""
from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.atendimento.models import Solicitacao
from apps.tenants.escopo import EscopoEscritorioMixin


@admin.register(Solicitacao)
class SolicitacaoAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("protocolo", "cliente", "tipo", "assunto", "estado", "criado_em")
    list_filter = ("tipo", "estado", "criado_em")
    search_fields = ("protocolo", "assunto", "cliente__nome")
    # Tudo que o cliente escreveu é leitura: o texto dele é prova do que foi
    # pedido, e editá-lo apagaria a diferença entre "o cliente pediu X" e "alguém
    # aqui entendeu X". O que a equipe muda é o estado.
    readonly_fields = (
        "protocolo", "cliente", "usuario", "tipo", "assunto", "descricao",
        "preferencia_data", "canal", "criado_em", "atualizado_em",
    )
    actions = ["marcar_resolvida"]

    @admin.action(description="Marcar como resolvida")
    def marcar_resolvida(self, request, queryset):
        for solicitacao in queryset:
            solicitacao.resolver()
