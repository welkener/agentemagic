from django.contrib import admin
from unfold.admin import ModelAdmin, StackedInline

from apps.painel.escopo import EscopoEscritorioMixin

from .models import Cliente, Perfil


class PerfilInline(StackedInline):
    model = Perfil
    extra = 0


@admin.register(Cliente)
class ClienteAdmin(EscopoEscritorioMixin, ModelAdmin):
    campo_escritorio = "escritorio"  # o Cliente É a raiz do tenant
    list_display = ("nome", "escritorio", "cnpj", "telefone_whatsapp", "cnae_padrao", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "cnpj", "telefone_whatsapp")
    inlines = [PerfilInline]

    def get_list_filter(self, request):
        # Filtrar por escritório só faz sentido pra equipe Magic BI — e o
        # filtro lateral LISTA os escritórios, então pro contador ele seria
        # um vazamento da carteira de parceiros (nomes dos concorrentes).
        if request.user.is_superuser:
            return (*self.list_filter, "escritorio")
        return self.list_filter


@admin.register(Perfil)
class PerfilAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "persona", "tier_maximo", "ferramentas_habilitadas")
    list_filter = ("tier_maximo",)
