"""
Grimório — a aplicação de trabalho do contador (DEC-12).

Não é o admin com tema. É superfície própria, com URLs próprias
(`/grimorio/`), montada em torno de uma pergunta que changelist nenhuma
responde: **o que exige você agora.** O admin continua existindo e continua
escapado por tenant, mas como backoffice — cadastro, exceção, equipe Magic BI.

A divisão de responsabilidade segue a que já existia: as contas ficam em
`metricas.py` (sem request, sem template, testáveis sozinhas) e aqui só há
montagem de contexto.

**Deixou de ser somente leitura em 10/ago/2026, e por camadas.** Até então toda
ação levava para o `/admin/` — outra cara, outra navegação, vocabulário de
programador — e o gate do Sprint 2b ("o contador cumpre um dia sem abrir o
/admin/") não tinha como fechar. A primeira escrita foi **resolver chamado**,
escolhida por ser sem efeito fiscal, sem custo para o cliente e trivialmente
reversível.

O que **não** muda: aprovar e cancelar nota continuam passando pelos serviços
auditados de `agents/agente_nf` quando chegarem aqui. A máquina de estados
fiscal não ganha atalho por conveniência de tela — o que muda é onde fica o
botão, nunca o caminho que ele percorre.

Toda escrita desta área obedece a três regras, e `tests/test_grimorio_acoes.py`
as cobra: só POST muda estado; o objeto vem do escopo do contador e nunca do id
da URL; e o ato entra na trilha com quem clicou.

**O ponto sensível é o escopo.** Sair do admin significa sair de
`EscopoEscritorioMixin`, que era quem filtrava `get_queryset`. Aqui o escopo
vem de três camadas independentes, e é de propósito que sejam três:

1. `EscopoGrimorioMixin` recusa quem não tem vínculo (403, não tela vazia);
2. toda consulta passa por `metricas.py`, que aplica `escopo_do_usuario`;
3. a RLS no Postgres devolve zero linhas se as duas primeiras falharem.

`tests/test_grimorio.py` verifica as três — inclusive uma view escrita errado
de propósito, sem o mixin, para provar que a terceira camada segura sozinha.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.audit.services import registrar
from apps.observabilidade import orcamento
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
        # Chamados abertos pela conversa entram na fila de hoje: o cliente ouviu
        # "a equipe já está vendo" quando abriu, e a promessa precisa ter dono
        # visível deste lado. Ver apps/atendimento/models.py.
        contexto["solicitacoes"] = metricas.solicitacoes_abertas(usuario)
        return contexto


class OperacaoView(EscopoGrimorioMixin, TemplateView):
    """Consumo, custo e latência do atendimento por IA.

    É a tela que o Sprint 2 alimenta e a única do Grimório voltada ao
    **escritório como negócio**, não à carteira dele: o que se decide aqui é
    preço e teto de gasto. Por isso ela mostra o pior cliente do mês em vez da
    média — o critério de aceite é por cliente/mês, e a média esconde
    exatamente o caso que estoura.
    """

    template_name = "grimorio/operacao.html"
    secao = "operacao"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        contexto["consumo"] = metricas.consumo_do_mes(usuario)
        contexto["escada"] = metricas.uso_da_escada(usuario)
        contexto["p95"] = metricas.latencia_p95(usuario)
        contexto["ferramentas"] = metricas.ferramentas_mais_usadas(usuario)
        contexto["p95_maximo"] = metricas.P95_MAXIMO_MS
        contexto["custo_maximo"] = metricas.CUSTO_MAXIMO_POR_CLIENTE_MES
        # O orçamento é por escritório, então superuser da Magic BI (que enxerga
        # a plataforma inteira) não tem um só para mostrar. Deixar em branco é
        # mais honesto que somar o teto de todos os tenants num número que não
        # governa nada.
        contexto["orcamento"] = (
            orcamento.situacao(self.escritorio) if self.escritorio is not None else None
        )
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


class ResolverSolicitacaoView(EscopoGrimorioMixin, View):
    """Fecha um chamado sem sair do Grimório — a primeira escrita desta área.

    **Por que ela existe.** Até aqui o Grimório era somente leitura e toda ação
    levava para o `/admin/`: outra cara, outra navegação, vocabulário de
    programador. No meio de uma tarefa isso é a costura que o concorrente não
    tem, e o gate do Sprint 2b ("o contador cumpre um dia sem abrir o /admin/")
    depende de fechá-la.

    **Por onde ela começa, e por quê.** Por chamado, que é a ação sem efeito
    fiscal, sem custo para o cliente e trivialmente reversível — reabrir é
    mudar um campo. Aprovar e cancelar nota ficam para depois e continuarão
    passando pelos mesmos serviços auditados: a máquina de estados fiscal não
    ganha atalho por causa de conveniência de tela.

    **O que esta view garante, e que qualquer escrita futura aqui precisa
    repetir:** só POST muda estado (link não altera dado — um `GET` que resolve
    chamado é resolvido pelo pré-carregador do navegador); o objeto vem do
    escopo do contador, nunca do id da URL; e o ato entra na trilha com quem
    clicou.
    """

    def post(self, request, pk):
        solicitacao = metricas.solicitacao_no_escopo(request.user, pk)
        if solicitacao is None:
            # 404 e não 403: ver `metricas.solicitacao_no_escopo`.
            raise Http404("Chamado não encontrado nesta carteira.")

        if solicitacao.aberta:
            solicitacao.resolver(por=request.user)
            registrar(
                "solicitacao_resolvida",
                {
                    "protocolo": solicitacao.protocolo,
                    "tipo": solicitacao.tipo,
                    "por": request.user.get_username(),
                    "origem": "grimorio",
                },
                cliente=solicitacao.cliente,
            )
            messages.success(
                request,
                f"Chamado {solicitacao.protocolo} marcado como resolvido.",
            )
        else:
            # Dois cliques no mesmo botão, ou dois contadores ao mesmo tempo.
            # Não é erro: o desfecho pretendido já é o atual.
            messages.info(
                request, f"O chamado {solicitacao.protocolo} já estava resolvido."
            )

        # Volta para onde o contador estava. `next` é conferido contra as rotas
        # do próprio Grimório — aceitar qualquer URL aqui seria um redirecionador
        # aberto, e este formulário fica atrás de login mas dentro de um domínio
        # que o cliente final também acessa.
        destino = request.POST.get("next") or ""
        if not destino.startswith("/grimorio/"):
            destino = reverse("grimorio:hoje")
        return HttpResponseRedirect(destino)


class VincularCertificadoView(EscopoGrimorioMixin, TemplateView):
    """Sobe o certificado A1 da empresa sem sair do Grimório.

    Segunda escrita desta área, e a primeira que lida com **segredo**. Por isso
    ela não implementa nada: o POST chama
    `credentials.services.vincular_certificado_pfx`, o mesmo serviço que o admin
    usa há semanas — que valida o `.pfx`, extrai CNPJ e validade, cifra arquivo e
    senha e registra na trilha. Duplicar esse caminho para ganhar uma tela seria
    criar um segundo lugar onde a senha de um certificado digital pode ser
    tratada errado, e o custo desse erro é alto demais para a economia que gera.

    **A senha nunca sai daqui**: não vai para mensagem, não vai para log, não vai
    para a trilha e não volta preenchida no formulário. O único destino dela é o
    serviço do cofre.

    **CNPJ divergente avisa, não bloqueia** — a mesma regra do admin. A AC pode
    fugir do padrão de CN e matriz/filial dividem raiz; decidir por bloqueio aqui
    seria mudar comportamento fiscal por conveniência de tela. O que muda é a
    ênfase: na tela do contador o aviso é impossível de não ver, porque emitir
    com certificado de outro CNPJ assina a nota como outra empresa.
    """

    template_name = "grimorio/certificado.html"
    secao = "integracoes"

    # Um .pfx real tem alguns KB. O limite existe para que um upload enorme não
    # seja lido inteiro na memória do contêiner, que é compartilhado.
    TAMANHO_MAXIMO = 1024 * 1024

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        cliente = metricas.cliente_no_escopo(self.request.user, kwargs["cliente_id"])
        if cliente is None:
            raise Http404("Empresa não encontrada nesta carteira.")
        contexto["cliente"] = cliente
        contexto["credencial"] = (
            cliente.credenciais.filter(tipo="certificado_pfx").order_by("-atualizado_em").first()
        )
        return contexto

    def post(self, request, cliente_id):
        from apps.credentials.certificados import ErroCertificadoInvalido
        from apps.credentials.models import Credencial
        from apps.credentials.services import vincular_certificado_pfx

        cliente = metricas.cliente_no_escopo(request.user, cliente_id)
        if cliente is None:
            raise Http404("Empresa não encontrada nesta carteira.")

        arquivo = request.FILES.get("pfx")
        senha = request.POST.get("senha") or ""
        if arquivo is None or not senha:
            messages.error(request, "Envie o arquivo .pfx e a senha dele.")
            return HttpResponseRedirect(request.path)
        if arquivo.size > self.TAMANHO_MAXIMO:
            messages.error(request, "Arquivo grande demais para ser um certificado .pfx.")
            return HttpResponseRedirect(request.path)

        credencial, _ = Credencial.objects.get_or_create(
            cliente=cliente,
            tipo=Credencial.Tipo.CERTIFICADO_PFX,
            defaults={"integracao": "nfse_nacional", "referencia_cofre": f"pfx/{cliente.pk}"},
        )
        try:
            metadados = vincular_certificado_pfx(credencial, arquivo.read(), senha)
        except ErroCertificadoInvalido as erro:
            # A mensagem do serviço distingue "senha errada" de "arquivo
            # inválido", e essa diferença é o que o contador precisa para saber
            # se pede a senha de novo ou o arquivo de novo.
            messages.error(request, f"Certificado não aceito: {erro}")
            return HttpResponseRedirect(request.path)

        if credencial.certificado_cnpj_diverge:
            messages.warning(
                request,
                f"Certificado vinculado, mas o CNPJ dele ({metadados.cnpj}) é "
                f"diferente do cadastro ({cliente.cnpj}). Confira antes de emitir — "
                f"nota assinada com certificado de outro CNPJ sai em nome da outra "
                f"empresa.",
            )
        else:
            messages.success(
                request,
                f"Certificado vinculado. Válido até "
                f"{metadados.validade.strftime('%d/%m/%Y')}.",
            )
        return HttpResponseRedirect(
            reverse("grimorio:empresa", args=[cliente.pk])
        )
