from django.contrib import admin

from .models import Cliente, Perfil


class PerfilInline(admin.StackedInline):
    model = Perfil
    extra = 0


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "telefone_whatsapp", "cnae_padrao", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "cnpj", "telefone_whatsapp")
    inlines = [PerfilInline]


@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    list_display = ("cliente", "persona", "tier_maximo", "ferramentas_habilitadas")
    list_filter = ("tier_maximo",)
