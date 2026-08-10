"""Backoffice dos documentos. A fila de trabalho fica no Grimório."""
from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.documentos.models import Documento
from apps.tenants.escopo import EscopoEscritorioMixin


@admin.register(Documento)
class DocumentoAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = (
        "protocolo", "cliente", "tipo", "situacao", "nome_arquivo", "criado_em"
    )
    list_filter = ("situacao", "tipo", "origem", "criado_em")
    search_fields = ("protocolo", "nome_arquivo", "cliente__nome", "hash_sha256")
    # O endereço no storage e o hash são fatos do arquivo recebido — editá-los
    # faria a linha apontar para outro objeto sem que nada acusasse.
    readonly_fields = (
        "protocolo", "cliente", "usuario", "bucket", "chave", "nome_arquivo",
        "tipo_mime", "tamanho", "hash_sha256", "origem", "criado_em",
    )
