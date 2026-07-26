from django import forms
from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .escopo import EscopoEscritorioMixin, SomentePlataformaMixin
from .models import Escritorio, MembroEscritorio


class EscritorioForm(forms.ModelForm):
    whatsapp_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Token da Cloud API",
        help_text="Token permanente do WhatsApp Cloud API deste escritório. Em branco = mantém o atual.",
    )

    class Meta:
        model = Escritorio
        fields = [
            "nome",
            "slug",
            "logo",
            "cor_primaria",
            "cor_acento",
            "ativo",
            "whatsapp_phone_number_id",
            "whatsapp_token",
        ]

    def save(self, commit=True):
        instancia = super().save(commit=False)
        novo = self.cleaned_data.get("whatsapp_token")
        if novo:
            instancia.whatsapp_token = novo
        if commit:
            instancia.save()
        return instancia


@admin.register(Escritorio)
class EscritorioAdmin(EscopoEscritorioMixin, ModelAdmin):
    """O contador enxerga e edita só o próprio escritório (marca, cores, canal).

    Criar/apagar escritório é ato de venda da plataforma — só a equipe Magic BI
    (superuser). Um contador conseguir criar escritório seria conseguir criar
    tenant; conseguir apagar seria arrastar junto a carteira de clientes.
    """

    campo_escritorio = "pk"
    form = EscritorioForm
    prepopulated_fields = {"slug": ("nome",)}
    list_display = (
        "nome",
        "preview_logo",
        "slug",
        "canal_whatsapp",
        "cor_primaria",
        "cor_acento",
        "ativo",
        "atualizado_em",
    )
    list_filter = ("ativo",)

    @admin.display(description="logo")
    def preview_logo(self, obj):
        if not obj.logo:
            return "—"
        return format_html('<img src="{}" style="height: 32px;">', obj.logo.url)

    @admin.display(boolean=True, description="WhatsApp próprio")
    def canal_whatsapp(self, obj):
        return obj.canal_whatsapp_configurado

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(MembroEscritorio)
class MembroEscritorioAdmin(SomentePlataformaMixin, ModelAdmin):
    """Quem pertence a qual escritório — só a equipe Magic BI mexe.

    É esta tabela que decide o que cada contador enxerga (`apps/painel/escopo.py`),
    então deixá-la editável pelo próprio contador anularia o isolamento.
    O provisionamento de contador segue manual no MVP, igual ao Magic Link
    (`apps/security/management/commands/enviar_link_contador.py`).
    """

    list_display = ("usuario", "escritorio", "criado_em")
    list_filter = ("escritorio",)
    search_fields = ("usuario__username", "usuario__email", "escritorio__nome")
