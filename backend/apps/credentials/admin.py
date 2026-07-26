"""
Admin dos segredos por integração — única interface prevista para digitar
client_id/client_secret/tokens no MVP (sem painel Grimório ainda). Os campos
de segredo nunca mostram o valor salvo (write-only): deixar em branco na
edição preserva o segredo cifrado atual.

Custódia de certificado (25/jul/2026): o formulário mostra campos diferentes
conforme o `tipo` escolhido — upload de `.pfx` + senha para
CERTIFICADO_PFX, provedor/identificador para CERTIFICADO_PSC. O .pfx é
aberto e validado já em `clean()` (antes de qualquer gravação) — senha
errada ou arquivo corrompido nunca chega a tocar o banco, o form só
re-renderiza com o erro.
"""
from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin

from .certificados import ErroCertificadoInvalido, extrair_metadados
from .models import AplicativoIntegracao, Credencial
from .services import vincular_certificado_psc, vincular_certificado_pfx


class CredencialForm(forms.ModelForm):
    valor = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Token/refresh token/identificador da procuração. Em branco = mantém o valor atual.",
    )
    pfx_arquivo = forms.FileField(
        required=False,
        label="Arquivo .pfx",
        help_text="Só para tipo 'Certificado digital — arquivo .pfx'. Em branco = mantém o arquivo atual.",
    )
    pfx_senha = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Senha do .pfx",
        help_text="Obrigatória junto com o upload do arquivo — nunca fica em texto puro no banco.",
    )

    class Meta:
        model = Credencial
        fields = [
            "cliente",
            "tipo",
            "integracao",
            "referencia_cofre",
            "valor",
            "expira_em",
            "psc_provedor",
            "psc_identificador",
            "pfx_arquivo",
            "pfx_senha",
        ]

    def clean(self):
        limpo = super().clean()
        tipo = limpo.get("tipo")

        if tipo == Credencial.Tipo.CERTIFICADO_PFX:
            arquivo = limpo.get("pfx_arquivo")
            senha = limpo.get("pfx_senha") or ""
            tem_arquivo_salvo = bool(self.instance.pk and self.instance.pfx_arquivo_cifrado)

            if arquivo:
                if not senha:
                    raise forms.ValidationError("Informe a senha do .pfx junto com o arquivo.")
                conteudo = arquivo.read()
                try:
                    # Valida abrindo o .pfx de verdade ANTES de gravar qualquer
                    # coisa — senha errada nunca resulta em escrita parcial no banco.
                    extrair_metadados(conteudo, senha)
                except ErroCertificadoInvalido as exc:
                    raise forms.ValidationError(str(exc)) from exc
                self._pfx_bytes = conteudo
                self._pfx_senha = senha
            elif not tem_arquivo_salvo:
                raise forms.ValidationError("Envie o arquivo .pfx para este tipo de credencial.")

        if tipo == Credencial.Tipo.CERTIFICADO_PSC:
            if not limpo.get("psc_provedor") or not limpo.get("psc_identificador"):
                raise forms.ValidationError(
                    "Preencha provedor e identificador do PSC para este tipo de credencial."
                )

        return limpo

    def save(self, commit=True):
        instancia = super().save(commit=False)
        novo_valor = self.cleaned_data.get("valor")
        if novo_valor:
            instancia.valor = novo_valor
        if commit:
            instancia.save()
        return instancia


@admin.register(Credencial)
class CredencialAdmin(ModelAdmin):
    form = CredencialForm
    list_display = (
        "cliente",
        "tipo",
        "integracao",
        "tem_valor",
        "certificado_cnpj",
        "certificado_validade",
        "alerta_cnpj_divergente",
        "expira_em",
        "atualizado_em",
    )
    list_filter = ("tipo", "integracao")
    search_fields = ("cliente__nome", "cliente__cnpj", "integracao")

    @admin.display(boolean=True, description="tem segredo salvo")
    def tem_valor(self, obj):
        return bool(obj.valor_cifrado)

    @admin.display(boolean=True, description="CNPJ do certificado bate?")
    def alerta_cnpj_divergente(self, obj):
        if not obj.certificado_cnpj:
            return None
        return not obj.certificado_cnpj_diverge

    def save_model(self, request, obj, form, change):
        # A validação real do .pfx (abrir com a senha) já aconteceu em
        # form.clean() — aqui só persiste. `vincular_*` faz o próprio save()
        # da credencial com os campos de certificado preenchidos.
        if obj.tipo == Credencial.Tipo.CERTIFICADO_PFX and hasattr(form, "_pfx_bytes"):
            super().save_model(request, obj, form, change)
            vincular_certificado_pfx(obj, form._pfx_bytes, form._pfx_senha)
        elif obj.tipo == Credencial.Tipo.CERTIFICADO_PSC:
            super().save_model(request, obj, form, change)
            vincular_certificado_psc(
                obj,
                form.cleaned_data.get("psc_provedor", ""),
                form.cleaned_data.get("psc_identificador", ""),
            )
        else:
            super().save_model(request, obj, form, change)


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
class AplicativoIntegracaoAdmin(ModelAdmin):
    form = AplicativoIntegracaoForm
    list_display = ("nome", "ambiente", "base_url", "ativo", "atualizado_em")
    list_filter = ("nome", "ambiente", "ativo")
