"""
Admin dos tenants — escritório e equipe.

O ponto sensível aqui é `MembroEscritorio`: é a tabela que decide todo o
isolamento (`escopo.py`). Deixar o responsável do escritório mexer nela é o que
faz um parceiro novo crescer sem depender da Magic BI — e é exatamente por isso
que ela precisa de trava explícita contra escalada de privilégio. As travas
estão em `ConvidarMembroForm.clean` e nos `has_*_permission` abaixo; cada uma
tem teste em `tests/test_papeis.py`.
"""
from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .escopo import EscopoEscritorioMixin, escopo_do_usuario
from .models import Escritorio, MembroEscritorio, grupo_do_escritorio


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

    def get_readonly_fields(self, request, obj=None):
        # O slug nomeia o Grupo de permissões do escritório
        # (`grupo_do_escritorio`); deixar o contador trocá-lo desligaria a
        # equipe inteira das próprias permissões sem aviso.
        if request.user.is_superuser:
            return ()
        return ("slug",)

    def get_prepopulated_fields(self, request, obj=None):
        # `prepopulated_fields` só vale para campo editável — com o slug
        # readonly (acima), o admin estoura KeyError ao renderizar.
        if request.user.is_superuser:
            return self.prepopulated_fields
        return {}


class ConvidarMembroForm(forms.ModelForm):
    """Cria o colega e o vínculo de uma vez.

    O responsável NÃO tem acesso ao admin de `auth.User` (seria escalada: quem
    edita User edita permissões e flags de superuser). Então o convite cria o
    usuário aqui, já com o formato certo: `is_staff`, sem senha utilizável
    (acesso por Magic Link) e no grupo de permissões do escritório.
    """

    username = forms.CharField(max_length=150, label="Usuário do colega")
    email = forms.EmailField(label="E-mail", help_text="Canal do Magic Link de acesso.")

    class Meta:
        model = MembroEscritorio
        fields = ["responsavel"]

    def __init__(self, *args, escritorio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._escritorio = escritorio

    def clean_username(self):
        username = self.cleaned_data["username"]
        User = get_user_model()
        existente = User.objects.filter(username=username).first()
        if existente is None:
            return username
        if existente.is_superuser:
            # Sem isto, o responsável poderia puxar um superuser da Magic BI
            # pro escritório dele e passar a administrar a conta da plataforma.
            raise forms.ValidationError("Esse usuário é da equipe Magic BI — fale com a plataforma.")
        if hasattr(existente, "membro_escritorio"):
            raise forms.ValidationError("Esse usuário já pertence a um escritório.")
        return username

    def save(self, commit=True):
        User = get_user_model()
        username = self.cleaned_data["username"]
        usuario, criado = User.objects.get_or_create(
            username=username, defaults={"email": self.cleaned_data["email"]}
        )
        if criado:
            usuario.is_staff = True
            usuario.set_unusable_password()  # acesso é por Magic Link
            usuario.save()
        usuario.groups.add(grupo_do_escritorio(self._escritorio))

        membro = super().save(commit=False)
        membro.usuario = usuario
        membro.escritorio = self._escritorio
        if commit:
            membro.save()
        return membro


@admin.register(MembroEscritorio)
class MembroEscritorioAdmin(EscopoEscritorioMixin, ModelAdmin):
    """Quem pertence a qual escritório.

    Superuser: controle total, qualquer escritório. Responsável: só a própria
    equipe, e pelo formulário de convite (nunca pelo admin de `auth.User`).
    Membro comum: não enxerga esta tela.
    """

    campo_escritorio = "escritorio"
    list_display = ("usuario", "escritorio", "responsavel", "criado_em")
    search_fields = ("usuario__username", "usuario__email", "escritorio__nome")

    def get_list_filter(self, request):
        # O filtro lateral listaria os escritórios — só faz sentido pra Magic BI.
        return ("escritorio", "responsavel") if request.user.is_superuser else ("responsavel",)

    # --- quem pode mexer ---------------------------------------------------
    def _e_responsavel(self, request) -> bool:
        membro = getattr(request.user, "membro_escritorio", None)
        return membro is not None and membro.responsavel

    def has_module_permission(self, request):
        return request.user.is_superuser or self._e_responsavel(request)

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or self._e_responsavel(request)

    def has_add_permission(self, request):
        return request.user.is_superuser or self._e_responsavel(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or self._e_responsavel(request)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if not self._e_responsavel(request):
            return False
        # Remover a si mesmo deixaria o escritório sem responsável e sem
        # ninguém pra recriar o vínculo — só a Magic BI destravaria.
        return obj is None or obj.usuario_id != request.user.pk

    # --- formulário --------------------------------------------------------
    def get_form(self, request, obj=None, **kwargs):
        if request.user.is_superuser:
            return super().get_form(request, obj, **kwargs)

        _, escritorio = escopo_do_usuario(request.user)
        if obj is not None:
            # Editar membro existente: só o bit de responsável, nada de trocar
            # de usuário ou de escritório.
            kwargs["fields"] = ["responsavel"]
            return super().get_form(request, obj, **kwargs)

        base = super().get_form(request, obj, form=ConvidarMembroForm, **kwargs)

        class FormComEscritorio(base):
            def __init__(self, *args, **kw):
                kw.setdefault("escritorio", escritorio)
                super().__init__(*args, **kw)

        return FormComEscritorio
