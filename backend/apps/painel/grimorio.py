"""
Grimório — a aplicação de trabalho do contador (DEC-12).

Não é o admin com tema. É superfície própria, com URLs próprias
(`/grimorio/`), montada em torno de uma pergunta que changelist nenhuma
responde: **o que exige você agora.** O admin continua existindo e continua
escapado por tenant, mas como backoffice — cadastro, exceção, equipe Magic BI.

A divisão de responsabilidade segue a que já existia: as contas ficam em
`metricas.py` (sem request, sem template, testáveis sozinhas) e aqui só há
montagem de contexto. Tudo é somente leitura — nenhuma destas telas cria fonte
de verdade nova. Ação de escrita (aprovar nota, renovar certificado) leva para
o admin, que é onde a máquina de estados e a auditoria já estão ligadas.

**O ponto sensível é o escopo.** Sair do admin significa sair de
`EscopoEscritorioMixin`, que era quem filtrava `get_queryset`. Aqui o escopo
vem de três camadas independentes, e é de propósito que sejam três:

1. `EscopoGrimorioMixin` recusa quem não tem vínculo (403, não tela vazia);
2. toda consulta passa por `metricas.py`, que aplica `escopo_do_usuario`;
3. a RLS no Postgres devolve zero linhas se as duas primeiras falharem.

`tests/test_grimorio.py` verifica as três — inclusive uma view escrita errado
de propósito, sem o mixin, para provar que a terceira camada segura sozinha.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.views.generic import TemplateView

from apps.painel import apresentacao, branding, metricas
from apps.tenants.escopo import escopo_do_usuario


class EscopoGrimorioMixin(LoginRequiredMixin):
    """Exige login, `is_staff` e vínculo com um escritório.

    Recusar com 403 em vez de mostrar a tela vazia é decisão de produto: um
    contador meio-provisionado que vê "0 clientes" acha que perdeu a carteira e
    abre chamado. O 403 diz que falta acesso, que é o que de fato falta.

    Superuser da Magic BI passa sem vínculo — enxerga a plataforma inteira, o
    mesmo contrato do admin (`apps/tenants/escopo.py`).
    """

    login_url = "/entrar/"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.is_staff:
            raise PermissionDenied("O Grimório é a área do contador.")
        irrestrito, escritorio = escopo_do_usuario(request.user)
        if not irrestrito and escritorio is None:
            raise PermissionDenied(
                "Sua conta ainda não está vinculada a um escritório. "
                "Fale com a Magic BI."
            )
        self.irrestrito = irrestrito
        self.escritorio = escritorio
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["marca"] = branding.marca_do_usuario(self.request.user)
        contexto["secao"] = getattr(self, "secao", "")
        contexto["total_pendencias"] = len(metricas.pendencias(self.request.user))
        return contexto


class HojeView(EscopoGrimorioMixin, TemplateView):
    """A home. Fila de trabalho, não resumo do mês.

    O contador com 200 empresas não abre o sistema para saber quantas notas
    saíram — abre para saber o que travou. O resumo do mês existe, mas embaixo:
    número que não pede ação não disputa o topo da tela.
    """

    template_name = "grimorio/hoje.html"
    secao = "hoje"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        pendentes = metricas.pendencias(usuario)
        carteira = metricas.carteira(usuario)

        contexto["pendencias"] = pendentes
        contexto["criticas"] = [p for p in pendentes if p.critica]
        contexto["atencoes"] = [p for p in pendentes if not p.critica]
        serie = metricas.serie_mensal(usuario)
        contexto["serie"] = serie
        contexto["grafico"] = apresentacao.sparkline(serie["quantidades"])
        contexto["clientes_ativos"] = len(carteira)
        contexto["notas_mes"] = sum(l.notas_mes for l in carteira)
        contexto["faturamento_mes"] = sum(
            (l.faturamento_mes or 0) for l in carteira
        )
        contexto["escada"] = metricas.uso_da_escada(usuario)
        return contexto


class CarteiraView(EscopoGrimorioMixin, TemplateView):
    """Uma linha por empresa, ordenada por faturamento — com filtro por
    situação, que é como o contador realmente navega ("me mostra quem está
    perto do teto")."""

    template_name = "grimorio/carteira.html"
    secao = "carteira"

    FILTROS = {
        "todos": "Todos",
        "teto": "Perto do teto",
        "pendencia": "Com pendência",
        "sem_canal": "WhatsApp não vinculado",
    }

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        linhas = metricas.carteira(self.request.user)

        filtro = self.request.GET.get("filtro", "todos")
        if filtro not in self.FILTROS:
            filtro = "todos"
        if filtro == "teto":
            linhas = [l for l in linhas if l.uso_teto.aplicavel and l.uso_teto.situacao != "tranquilo"]
        elif filtro == "pendencia":
            linhas = [l for l in linhas if l.cadastro_faltante]
        elif filtro == "sem_canal":
            linhas = [l for l in linhas if not l.sessao_ativa]

        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            termo = busca.lower()
            linhas = [
                l for l in linhas if termo in l.cliente.nome.lower() or termo in l.cliente.cnpj
            ]

        contexto["linhas"] = linhas
        contexto["filtros"] = self.FILTROS
        contexto["filtro_ativo"] = filtro
        contexto["busca"] = busca
        return contexto


class EmpresaView(EscopoGrimorioMixin, TemplateView):
    """A empresa inteira num lugar: números, notas, integrações e histórico.

    É a tela que substitui abrir cinco changelists diferentes filtrando pelo
    mesmo cliente — que é o que o contador faz hoje.
    """

    template_name = "grimorio/empresa.html"
    secao = "carteira"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        ficha = metricas.ficha_da_empresa(self.request.user, kwargs["cliente_id"])
        if ficha is None:
            # Fora do escopo e inexistente respondem igual. Distinguir os dois
            # (404 vs 403) contaria ao contador que aquele cliente existe em
            # outra carteira — vazamento pequeno, mas vazamento.
            raise Http404("Empresa não encontrada nesta carteira.")
        contexto["ficha"] = ficha
        contexto["grafico"] = apresentacao.sparkline(ficha.serie["faturamentos"])
        contexto["pendencias_da_empresa"] = [
            p
            for p in metricas.pendencias(self.request.user)
            if p.cliente and p.cliente.pk == ficha.cliente.pk
        ]
        return contexto


class DocumentosView(EscopoGrimorioMixin, TemplateView):
    """Notas emitidas agrupadas por mês, com protocolo, chave e DANFSE."""

    template_name = "grimorio/documentos.html"
    secao = "documentos"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["grupos"] = metricas.documentos(self.request.user)
        return contexto


class IntegracoesView(EscopoGrimorioMixin, TemplateView):
    """O que está ligado e o que falta ligar, por empresa — certificado, ERP e
    canal. É a tela do onboarding: enquanto houver linha vermelha aqui, aquele
    cliente não opera por inteiro."""

    template_name = "grimorio/integracoes.html"
    secao = "integracoes"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        linhas = metricas.integracoes(self.request.user)
        contexto["linhas"] = linhas
        contexto["completas"] = [l for l in linhas if not l.pendencias]
        contexto["incompletas"] = [l for l in linhas if l.pendencias]
        return contexto
