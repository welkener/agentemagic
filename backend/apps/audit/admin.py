from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.tenants.escopo import EscopoEscritorioMixin

from .models import Auditoria


@admin.register(Auditoria)
class AuditoriaAdmin(EscopoEscritorioMixin, ModelAdmin):
    """Só leitura — trilha append-only (o model já bloqueia update/delete)."""

    list_display = ("id", "criado_em", "evento", "cliente", "hash_atual")
    list_filter = ("evento",)
    search_fields = ("evento", "cliente__nome", "cliente__cnpj", "hash_atual")
    readonly_fields = [f.name for f in Auditoria._meta.fields] + ["conteudo"]

    @admin.display(description="conteúdo (decifrado)")
    def conteudo(self, obj):
        """`dados` guarda o conteúdo pessoal cifrado com a chave do titular —
        sem isto o admin mostraria só o token."""
        return obj.dados_revelados

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
