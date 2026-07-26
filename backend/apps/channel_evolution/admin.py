"""Admin da configuração Evolution — SÓ TESTE LOCAL (ver apps.py). Campo de
api_key nunca reexibe o valor salvo (write-only), mesmo padrão do resto do
projeto (`apps.credentials.admin`)."""
from django import forms
from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from apps.tenants.escopo import EscopoEscritorioMixin

from .models import ConfiguracaoEvolution
from .services import testar_conexao


class ConfiguracaoEvolutionForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Em branco = mantém o valor atual.",
    )

    class Meta:
        model = ConfiguracaoEvolution
        fields = ["escritorio", "nome", "base_url", "instancia", "api_key", "ativo"]

    def save(self, commit=True):
        instancia = super().save(commit=False)
        nova_chave = self.cleaned_data.get("api_key")
        if nova_chave:
            instancia.api_key = nova_chave
        if commit:
            instancia.save()
        return instancia


@admin.register(ConfiguracaoEvolution)
class ConfiguracaoEvolutionAdmin(EscopoEscritorioMixin, ModelAdmin):
    campo_escritorio = "escritorio"
    form = ConfiguracaoEvolutionForm
    list_display = ("nome", "escritorio", "instancia", "base_url", "ativo", "atualizado_em")
    list_filter = ("ativo",)
    actions = ["testar_conexao_action"]

    @admin.action(description="Testar conexão com a instância")
    def testar_conexao_action(self, request, queryset):
        # Testa a instância de cada configuração selecionada, no escritório
        # dela — antes isto sempre batia na "configuração ativa" global.
        for config in queryset:
            ok, mensagem = testar_conexao(config.escritorio)
            self.message_user(
                request,
                f"{config.instancia}: {mensagem}",
                level=messages.SUCCESS if ok else messages.WARNING,
            )
