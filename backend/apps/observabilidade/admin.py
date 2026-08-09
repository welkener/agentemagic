"""
Consumo de LLM no backoffice — leitura, e só.

A tela de trabalho do contador é a Operação do Grimório, que agrega. Este admin
existe para o caso oposto: a equipe Magic BI investigando *uma* chamada
específica ("por que esta mensagem custou tanto?"), que agregado nenhum responde.

Tudo é somente leitura porque a tabela é base de cálculo de fatura. Editar uma
linha aqui seria editar quanto o cliente deve — e sem deixar rastro, já que esta
tabela não é encadeada como a trilha de auditoria.
"""
from django.contrib import admin
from unfold.admin import ModelAdmin

from apps.observabilidade.models import ConsumoLLM
from apps.tenants.escopo import EscopoEscritorioMixin


@admin.register(ConsumoLLM)
class ConsumoLLMAdmin(EscopoEscritorioMixin, ModelAdmin):
    campo_escritorio = "escritorio"

    list_display = (
        "momento", "escritorio", "cliente", "etapa", "modelo",
        "tokens_entrada", "tokens_saida", "latencia_ms", "custo_brl", "erro",
    )
    list_filter = ("etapa", "modelo", "momento")
    search_fields = ("cliente__nome", "modelo", "erro")
    date_hierarchy = "momento"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Nem para a equipe: apagar consumo apagaria a base da fatura do mês.
        # Encerrar um tenant é desativar o escritório, não limpar o histórico.
        return False
