"""
Admin dos segredos por integração — única interface prevista para digitar
client_id/client_secret/tokens no MVP (sem painel Grimório ainda). Os campos
de segredo nunca mostram o valor salvo (write-only): deixar em branco na
edição preserva o segredo cifrado atual.
"""
from django import forms
from django.contrib import admin

from .models import AplicativoIntegracao, Credencial


class CredencialForm(forms.ModelForm):
    valor = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Token/refresh token/identificador da procuração. Em branco = mantém o valor atual.",
    )

    class Meta:
        model = Credencial
        fields = ["cliente", "tipo", "integracao", "referencia_cofre", "valor", "expira_em"]

    def save(self, commit=True):
        instancia = super().save(commit=False)
        novo_valor = self.cleaned_data.get("valor")
        if novo_valor:
            instancia.valor = novo_valor
        if commit:
            instancia.save()
        return instancia


@admin.register(Credencial)
class CredencialAdmin(admin.ModelAdmin):
    form = CredencialForm
    list_display = ("cliente", "tipo", "integracao", "tem_valor", "expira_em", "atualizado_em")
    list_filter = ("tipo", "integracao")
    search_fields = ("cliente__nome", "cliente__cnpj", "integracao")

    @admin.display(boolean=True, description="tem segredo salvo")
    def tem_valor(self, obj):
        return bool(obj.valor_cifrado)


class AplicativoIntegracaoForm(forms.ModelForm):
    client_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Em branco = mantém o valor atual.",
    )

    class Meta:
        model = AplicativoIntegracao
        fields = ["nome", "ambiente", "base_url", "token_url", "client_id", "client_secret", "ativo"]

    def save(self, commit=True):
        instancia = super().save(commit=False)
        novo_valor = self.cleaned_data.get("client_secret")
        if novo_valor:
            instancia.client_secret = novo_valor
        if commit:
            instancia.save()
        return instancia


@admin.register(AplicativoIntegracao)
class AplicativoIntegracaoAdmin(admin.ModelAdmin):
    form = AplicativoIntegracaoForm
    list_display = ("nome", "ambiente", "base_url", "ativo", "atualizado_em")
    list_filter = ("nome", "ambiente", "ativo")
