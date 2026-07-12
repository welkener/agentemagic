from django.contrib import admin

from .models import Intencao


@admin.register(Intencao)
class IntencaoAdmin(admin.ModelAdmin):
    """Só leitura — estado muda unicamente via `transicionar()` (audita a transição)."""

    list_display = ("id", "cliente", "tipo_acao", "estado", "criado_em", "atualizado_em")
    list_filter = ("estado", "tipo_acao")
    search_fields = ("cliente__nome", "cliente__cnpj", "chave_idempotencia")
    readonly_fields = [f.name for f in Intencao._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
