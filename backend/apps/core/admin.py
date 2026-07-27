"""
Admin do aprendizado do roteador.

Duas telas que fecham um ciclo:

1. **Exemplos de intenção** — o que o roteador lê. Cadastrar aqui vale na
   mensagem seguinte, sem deploy.
2. **Revisar não entendidas** — a fila de trabalho: mensagens que caíram em
   `desconhecida`, com a mensagem seguinte do mesmo cliente ao lado. Quando o
   cliente reformula e a segunda tentativa acerta, o rótulo da segunda vale
   para a primeira — é exemplo rotulado produzido pelo próprio uso, de graça.

Sem a tela 2, a tela 1 depende de alguém lembrar do que deu errado. Com ela, a
lista chega pronta.
"""
from django.contrib import admin, messages
from django.urls import path
from django.views.generic import TemplateView
from unfold.admin import ModelAdmin
from unfold.views import UnfoldModelAdminViewMixin

from apps.audit.conteudo import revelar
from apps.audit.models import Auditoria
from apps.core.models import ExemploIntencao
from apps.tenants.escopo import escopo_do_usuario


class RevisarNaoEntendidasView(UnfoldModelAdminViewMixin, TemplateView):
    """Mensagens classificadas como `desconhecida`, com o que veio depois."""

    title = "Revisar mensagens não entendidas"
    permission_required = ("core.view_exemplointencao",)
    template_name = "core/revisar_nao_entendidas.html"
    LIMITE = 50

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        irrestrito, escritorio = escopo_do_usuario(self.request.user)

        qs = Auditoria.objects.filter(evento="orquestrador_mensagem_processada")
        if not irrestrito:
            qs = qs.filter(cliente__escritorio=escritorio) if escritorio else qs.none()

        # Materializa em ordem para conseguir olhar a mensagem SEGUINTE do mesmo
        # cliente — é ela que carrega o rótulo que faltava na primeira.
        registros = list(qs.select_related("cliente").order_by("-id")[: self.LIMITE * 6])
        registros.reverse()

        seguinte_por_cliente: dict[int, dict] = {}
        linhas = []
        for registro in reversed(registros):
            dados = revelar(registro.dados or {}, registro.cliente)
            cliente_id = registro.cliente_id
            proxima = seguinte_por_cliente.get(cliente_id)

            if dados.get("intencao") == "desconhecida" and dados.get("mensagem"):
                linhas.append(
                    {
                        "quando": registro.criado_em,
                        "cliente": registro.cliente,
                        "mensagem": dados.get("mensagem"),
                        "reformulacao": (proxima or {}).get("mensagem"),
                        "intencao_da_reformulacao": (proxima or {}).get("intencao"),
                        "ja_virou_exemplo": ExemploIntencao.objects.filter(
                            frase__iexact=(dados.get("mensagem") or "").strip()
                        ).exists(),
                    }
                )
            seguinte_por_cliente[cliente_id] = dados

        contexto.update(
            {
                "linhas": linhas[: self.LIMITE],
                "total": len(linhas),
                "exemplos_cadastrados": ExemploIntencao.objects.filter(ativo=True).count(),
                "add_url": "/admin/core/exemplointencao/add/",
            }
        )
        return contexto


@admin.register(ExemploIntencao)
class ExemploIntencaoAdmin(ModelAdmin):
    list_display = ("frase", "intencao", "ativo", "criado_em")
    list_filter = ("intencao", "ativo")
    search_fields = ("frase", "observacao")
    actions = ["ativar", "desativar"]

    def get_urls(self):
        # Antes de `super()`: a lista do ModelAdmin termina num catch-all
        # `<path:object_id>/` que engoliria esta rota.
        return [
            path(
                "revisar/",
                self.admin_site.admin_view(RevisarNaoEntendidasView.as_view(model_admin=self)),
                name="core_revisar_nao_entendidas",
            ),
        ] + super().get_urls()

    @admin.action(description="Ativar (passa a valer no roteador)")
    def ativar(self, request, queryset):
        self.message_user(request, f"{queryset.update(ativo=True)} exemplo(s) ativado(s).")

    @admin.action(description="Desativar (sai do prompt, registro fica)")
    def desativar(self, request, queryset):
        self.message_user(
            request, f"{queryset.update(ativo=False)} exemplo(s) desativado(s).", messages.WARNING
        )
