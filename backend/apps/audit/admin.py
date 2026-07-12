from django.contrib import admin

from .models import Auditoria


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    """Só leitura — trilha append-only (o model já bloqueia update/delete)."""

    list_display = ("id", "criado_em", "evento", "cliente", "hash_atual")
    list_filter = ("evento",)
    search_fields = ("evento", "cliente__nome", "cliente__cnpj", "hash_atual")
    readonly_fields = [f.name for f in Auditoria._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
