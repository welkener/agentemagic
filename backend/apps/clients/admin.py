from django.contrib import admin, messages
from django.urls import path
from django.utils.html import format_html
from unfold.admin import ModelAdmin, StackedInline, TabularInline

from apps.fiscal.dps import conferir_cadastro
from apps.tenants.escopo import EscopoEscritorioMixin

from .models import Cliente, Perfil, Usuario, VinculoUsuarioCliente
from .receita import ErroConsultaCnpj, consultar_cnpj

# Campos que a Receita preenche. Separados dos de julgamento fiscal justamente
# pra deixar claro, no formulário, o que é dado público e o que é decisão.
CAMPOS_DA_RECEITA = (
    "nome",
    "codigo_municipio_ibge",
    "cnae_padrao",
    "opcao_simples_nacional",
    "data_inicio_atividade",
)


class PerfilInline(StackedInline):
    model = Perfil
    extra = 0


class VinculoInline(TabularInline):
    """Quem fala por esta empresa no WhatsApp (DEC-03).

    Inline, e não tela separada, porque a pergunta ("quem é esse número?")
    sempre aparece com a empresa na frente — nunca isolada.
    """

    model = VinculoUsuarioCliente
    extra = 0
    autocomplete_fields = ("usuario",)
    fields = ("usuario", "papel", "principal", "ativo")
    verbose_name = "usuário do WhatsApp"
    verbose_name_plural = "usuários do WhatsApp (quem fala por esta empresa)"


@admin.register(Usuario)
class UsuarioAdmin(EscopoEscritorioMixin, ModelAdmin):
    campo_escritorio = "escritorio"
    list_display = ("telefone_whatsapp", "nome", "escritorio", "empresas", "ativo")
    list_filter = ("ativo",)
    search_fields = ("telefone_whatsapp", "nome")

    @admin.display(description="empresas")
    def empresas(self, obj):
        nomes = [c.nome for c in obj.clientes_ativos()]
        if not nomes:
            return "—"
        if len(nomes) == 1:
            return nomes[0]
        # Várias empresas é o caso que o DEC-03 veio permitir — merece destaque
        # na lista, porque é ele que faz o agente perguntar antes de agir.
        return format_html("<strong>{}</strong>", " · ".join(nomes))


@admin.register(Cliente)
class ClienteAdmin(EscopoEscritorioMixin, ModelAdmin):
    campo_escritorio = "escritorio"  # o Cliente É a raiz do tenant
    list_display = (
        "nome",
        "escritorio",
        "cnpj",
        "telefone_whatsapp",
        "pronto_para_emitir",
        "ativo",
        "demonstracao",
        "criado_em",
    )
    # `demonstracao` no filtro porque empresa fictícia e real dividem o mesmo
    # escritório — e a primeira coisa que alguém faz ao desconfiar de um número
    # é querer ver a lista sem as fictícias.
    list_filter = ("ativo", "demonstracao")
    # `telefone_whatsapp` saiu da busca porque deixou de ser coluna do banco
    # (DEC-03): quem procura por número usa a tela de usuários do WhatsApp, que
    # é onde o número passou a morar.
    search_fields = ("nome", "cnpj")
    inlines = [PerfilInline, VinculoInline]
    actions = ["buscar_na_receita"]

    fieldsets = (
        (None, {"fields": ("escritorio", "nome", "cnpj", "email_contato", "ativo")}),
        (
            "Preenchido pela consulta pública (ação “Buscar dados na Receita”)",
            {
                "fields": (
                    "codigo_municipio_ibge",
                    "cnae_padrao",
                    "opcao_simples_nacional",
                    "data_inicio_atividade",
                ),
                "description": (
                    "Dados do cadastro federal. Use a ação na listagem para puxar "
                    "automaticamente a partir do CNPJ."
                ),
            },
        ),
        (
            "Decisão do contador — a Receita não tem estes",
            {
                "fields": (
                    "codigo_tributacao_nacional",
                    "inscricao_municipal",
                    "regime_especial_tributacao",
                    "iss_tributacao",
                    "iss_retencao",
                    "aliquota_iss",
                    "serie_dps",
                ),
                "description": (
                    "⚠ <b>cTribNac não é o CNAE.</b> CNAE classifica atividade econômica; "
                    "o código de tributação nacional classifica o <i>serviço</i> (lista da "
                    "LC 116). Sem ele a nota não sai — e com o valor errado ela sai errada."
                ),
            },
        ),
    )

    def get_urls(self):
        """A página Carteira pendura aqui — é a visão analítica DESTE model.

        Antes de `super()` de propósito: a lista de URLs do ModelAdmin termina
        num catch-all `<path:object_id>/`, que engoliria "carteira" e tentaria
        abrir um cliente com esse id.
        """
        from apps.painel.views import CarteiraView

        return [
            path(
                "carteira/",
                self.admin_site.admin_view(CarteiraView.as_view(model_admin=self)),
                name="painel_carteira",
            ),
        ] + super().get_urls()

    def get_list_filter(self, request):
        # Filtrar por escritório só faz sentido pra equipe Magic BI — e o
        # filtro lateral LISTA os escritórios, então pro contador ele seria
        # um vazamento da carteira de parceiros (nomes dos concorrentes).
        if request.user.is_superuser:
            return (*self.list_filter, "escritorio")
        return self.list_filter

    @admin.display(description="pronto para emitir?")
    def pronto_para_emitir(self, obj):
        """Antes, o cadastro incompleto só aparecia na primeira emissão — e
        aparecia pro cliente, no WhatsApp. Aqui o contador vê antes."""
        faltantes = conferir_cadastro(obj)
        if not faltantes:
            return format_html('<span style="color:#1e7a34;">✅ sim</span>')
        return format_html(
            '<span style="color:#b4600b;" title="{}">⚠ falta {}</span>',
            "; ".join(faltantes),
            f"{len(faltantes)} campo{'s' if len(faltantes) > 1 else ''}",
        )

    @admin.action(description="Buscar dados na Receita (preenche o que for público)")
    def buscar_na_receita(self, request, queryset):
        for cliente in queryset:
            try:
                dados = consultar_cnpj(cliente.cnpj)
            except ErroConsultaCnpj as exc:
                self.message_user(request, f"{cliente.cnpj}: {exc}", level=messages.ERROR)
                continue

            if not dados.ativa:
                # Não bloqueia — pode ser baixa recente e o contador saber o que
                # faz —, mas emitir nota de empresa inativa é problema real.
                self.message_user(
                    request,
                    f"{cliente.cnpj}: situação cadastral “{dados.situacao_cadastral}” — confira antes de emitir.",
                    level=messages.WARNING,
                )

            cliente.nome = dados.razao_social or cliente.nome
            cliente.codigo_municipio_ibge = dados.codigo_municipio_ibge or cliente.codigo_municipio_ibge
            cliente.cnae_padrao = dados.cnae_padrao or cliente.cnae_padrao
            cliente.opcao_simples_nacional = dados.opcao_simples_nacional
            cliente.data_inicio_atividade = (
                dados.data_inicio_atividade or cliente.data_inicio_atividade
            )
            if dados.email and not cliente.email_contato:
                cliente.email_contato = dados.email
            cliente.save()

            restantes = conferir_cadastro(cliente)
            if restantes:
                self.message_user(
                    request,
                    f"{cliente.nome}: dados públicos preenchidos. Ainda falta você definir: "
                    + "; ".join(restantes),
                    level=messages.WARNING,
                )
            else:
                self.message_user(request, f"{cliente.nome}: cadastro completo ✅")


@admin.register(Perfil)
class PerfilAdmin(EscopoEscritorioMixin, ModelAdmin):
    list_display = ("cliente", "persona", "tier_maximo", "ferramentas_habilitadas")
    list_filter = ("tier_maximo",)
