"""Backoffice da rotina. A tela de trabalho do contador fica no Grimório."""
from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.rotina.models import Certidao, Folha, Guia, Obrigacao
from apps.tenants.escopo import EscopoEscritorioMixin


@admin.register(Guia)
class GuiaAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "tipo", "competencia", "valor", "vencimento", "situacao")
    list_filter = ("tipo", "situacao", "vencimento")
    search_fields = ("cliente__nome", "cliente__cnpj", "competencia")
    date_hierarchy = "vencimento"


@admin.register(Obrigacao)
class ObrigacaoAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = (
        "cliente", "tipo", "competencia", "prazo", "situacao", "pendente_com_o_cliente"
    )
    list_filter = ("tipo", "situacao", "pendente_com_o_cliente", "prazo")
    search_fields = ("cliente__nome", "cliente__cnpj", "competencia")


@admin.register(Certidao)
class CertidaoAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "tipo", "situacao", "emitida_em", "valida_ate")
    list_filter = ("tipo", "situacao", "valida_ate")
    search_fields = ("cliente__nome", "cliente__cnpj")


@admin.register(Folha)
class FolhaAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = (
        "cliente", "competencia", "funcionarios", "total_bruto", "fechada_em"
    )
    list_filter = ("competencia",)
    search_fields = ("cliente__nome", "cliente__cnpj", "competencia")
