from django.contrib import admin
from django.utils.html import format_html

from .models import Escritorio


@admin.register(Escritorio)
class EscritorioAdmin(admin.ModelAdmin):
    list_display = ("nome", "preview_logo", "cor_primaria", "cor_acento", "ativo", "atualizado_em")
    list_filter = ("ativo",)

    @admin.display(description="logo")
    def preview_logo(self, obj):
        if not obj.logo:
            return "—"
        return format_html('<img src="{}" style="height: 32px;">', obj.logo.url)
