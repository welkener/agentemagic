"""Admin da configuração Evolution — SÓ TESTE LOCAL (ver apps.py). Campo de
api_key nunca reexibe o valor salvo (write-only), mesmo padrão do resto do
projeto (`apps.credentials.admin`)."""
from django import forms
from django.contrib import admin, messages
from unfold.admin import ModelAdmin

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
        fields = ["nome", "base_url", "instancia", "api_key", "ativo"]

    def save(self, commit=True):
        instancia = super().save(commit=False)
        nova_chave = self.cleaned_data.get("api_key")
        if nova_chave:
            instancia.api_key = nova_chave
        if commit:
            instancia.save()
        return instancia


@admin.register(ConfiguracaoEvolution)
class ConfiguracaoEvolutionAdmin(ModelAdmin):
    form = ConfiguracaoEvolutionForm
    list_display = ("nome", "instancia", "base_url", "ativo", "atualizado_em")
    list_filter = ("ativo",)
    actions = ["testar_conexao_action"]

    @admin.action(description="Testar conexão com a instância")
    def testar_conexao_action(self, request, queryset):
        ok, mensagem = testar_conexao()
        self.message_user(request, mensagem, level=messages.SUCCESS if ok else messages.WARNING)
