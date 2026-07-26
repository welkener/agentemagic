from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.tenants.escopo import EscopoEscritorioMixin

from .models import Codigo2FA, SessaoWhatsapp, TokenMagicLink


@admin.register(SessaoWhatsapp)
class SessaoWhatsappAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "wa_id", "status", "validado_em", "expira_em")
    list_filter = ("status",)
    search_fields = ("cliente__nome", "cliente__cnpj", "wa_id")
    readonly_fields = [f.name for f in SessaoWhatsapp._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(TokenMagicLink)
class TokenMagicLinkAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "wa_id", "criado_em", "usado_em", "expira_em")
    search_fields = ("cliente__nome", "cliente__cnpj", "wa_id")
    readonly_fields = [f.name for f in TokenMagicLink._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Codigo2FA)
class Codigo2FAAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "intencao", "tentativas", "criado_em", "usado_em", "expira_em")
    search_fields = ("cliente__nome", "cliente__cnpj")
    readonly_fields = [f.name for f in Codigo2FA._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
