"""
Backoffice das solicitações — e a ação de resolver, usada direto da fila.

A changelist responde "onde está o chamado X"; a fila no dashboard responde "o
que exige você agora". A ação de fechar pendura aqui para que o contador não
precise abrir o registro só para marcar resolvido.
"""
from django.contrib import admin
from django.urls import path
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

    def get_urls(self):
        """Resolver chamado, chamado da fila do dashboard.

        Antes de `super()`: a lista do ModelAdmin termina num catch-all
        `<path:object_id>/`, que engoliria a rota abaixo.
        """
        from apps.painel.chamados import ResolverSolicitacaoView

        return [
            path(
                "<int:pk>/resolver/",
                self.admin_site.admin_view(ResolverSolicitacaoView.as_view()),
                name="painel_resolver_chamado",
            ),
        ] + super().get_urls()
