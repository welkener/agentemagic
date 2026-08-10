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


class CadastroFiscalView(EscopoGrimorioMixin, TemplateView):
    """Completa o que falta para a empresa emitir — e só isso.

    Terceira camada da costura. O formulário do admin tem trinta campos e não
    diz quais três impedem a emissão; aqui a tela mostra exatamente os que
    `fiscal.dps.conferir_cadastro` aponta, com a explicação de cada um. A
    informação já existia — ficava a duas telas de distância de quem precisava
    dela.

    **A validação é a mesma da emissão, e de propósito.** Os valores são
    atribuídos ao objeto em memória e submetidos a `conferir_cadastro` antes de
    qualquer gravação: se ela reprovar, nada é salvo. Escrever uma segunda regra
    de "cadastro completo" aqui criaria a chance de a tela dizer "pronto" e a
    emissão continuar falhando — que é pior que não ter a tela.

    O CNPJ não é editável nesta tela. Ele identifica a empresa e casa com o
    certificado; trocá-lo é outra operação, com outras consequências, e continua
    no admin.
    """

    template_name = "grimorio/cadastro.html"
    secao = "carteira"

    CAMPOS = (
        (
            "codigo_municipio_ibge",
            "Código IBGE do município",
            "7 dígitos. É o município onde o serviço é prestado (cLocEmi na DPS).",
        ),
        (
            "codigo_tributacao_nacional",
            "Código de tributação nacional",
            "6 dígitos. NÃO é o CNAE — é o código da lista nacional de serviços "
            "(cTribNac). Confundir os dois é o erro mais comum aqui.",
        ),
        (
            "cnae_padrao",
            "CNAE de serviço",
            "Usado como padrão nas notas desta empresa. Nunca vem do modelo de "
            "IA: sai daqui, do cadastro.",
        ),
    )

    def _cliente(self):
        cliente = metricas.cliente_no_escopo(self.request.user, self.kwargs["cliente_id"])
        if cliente is None:
            raise Http404("Empresa não encontrada nesta carteira.")
        return cliente

    def get_context_data(self, **kwargs):
        from apps.fiscal.dps import conferir_cadastro

        contexto = super().get_context_data(**kwargs)
        cliente = self._cliente()
        contexto["cliente"] = cliente
        contexto["campos"] = [
            (nome, rotulo, ajuda, getattr(cliente, nome, "") or "")
            for nome, rotulo, ajuda in self.CAMPOS
        ]
        contexto["faltantes"] = conferir_cadastro(cliente)
        return contexto

    def post(self, request, cliente_id):
        from apps.fiscal.dps import conferir_cadastro

        cliente = self._cliente()
        alterados = []
        for nome, _rotulo, _ajuda in self.CAMPOS:
            novo = (request.POST.get(nome) or "").strip()
            if novo != (getattr(cliente, nome, "") or ""):
                setattr(cliente, nome, novo)
                alterados.append(nome)

        # A mesma função que a emissão usa, sobre o objeto ainda não salvo.
        # Regra própria aqui deixaria a tela dizer "pronto" com a emissão ainda
        # falhando — pior que não ter a tela.
        problemas = conferir_cadastro(cliente)
        if problemas:
            for problema in problemas:
                messages.error(request, f"Ainda falta: {problema}.")
            return HttpResponseRedirect(request.path)

        if alterados:
            cliente.save(update_fields=alterados)
            registrar(
                "cadastro_fiscal_atualizado",
                {"campos": alterados, "por": request.user.get_username(), "origem": "grimorio"},
                cliente=cliente,
            )
            messages.success(request, "Cadastro completo. A empresa já pode emitir.")
        else:
            messages.info(request, "Nada mudou — o cadastro já estava assim.")
        return HttpResponseRedirect(reverse("grimorio:empresa", args=[cliente.pk]))


class NotaView(EscopoGrimorioMixin, TemplateView):
    """A nota inteira numa tela, e a decisão sobre ela.

    Última camada da costura, e a única que toca documento fiscal. Duas escolhas
    de desenho, ambas deliberadas:

    **Tela de conferência em vez de aprovar direto da fila.** Um clique na lista
    é mais rápido e é o que o contador vai querer no dia 8 — e é exatamente por
    isso que não é o padrão: com doze notas na fila e valores parecidos,
    aprovar a linha errada é fácil, silencioso e irreversível (a nota vai para a
    prefeitura). O agente pede "confirma?" ao cliente antes de emitir pelo mesmo
    motivo; seria estranho o contador ter menos conferência que ele. Se depois
    ficar claro que a tela atrapalha, dá para somar um atalho na fila — o
    caminho inverso, tirar a conferência de quem já se acostumou a clicar, é bem
    mais caro.

    **Uma decisão, não duas.** "Aprovar" despacha por `tipo_acao`: emissão vira
    `confirmar_emissao`, pedido de cancelamento vira `confirmar_cancelamento`.
    É a mesma escolha que o admin já fazia — o contador decide "aprovo isto", e
    o sistema sabe o que "isto" é. Obrigá-lo a saber de antemão que tipo de item
    está olhando só criaria a chance de escolher a ação errada.

    Nada de fiscal é reimplementado aqui: os dois serviços são os mesmos que o
    admin chama, com a máquina de estados e a trilha encadeada intactas.
    """

    template_name = "grimorio/nota.html"
    secao = "documentos"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        intencao = metricas.intencao_no_escopo(self.request.user, kwargs["pk"])
        if intencao is None:
            raise Http404("Nota não encontrada nesta carteira.")
        contexto["intencao"] = intencao
        contexto["cancelamento"] = intencao.tipo_acao == "cancelar_nfse"
        contexto["decidivel"] = intencao.estado == "AGUARDANDO_APROVACAO"
        contexto["historico"] = metricas.historico_da_intencao(self.request.user, intencao)
        # Aprovar com cadastro incompleto falha na montagem da DPS, e o contador
        # só descobria depois de clicar. Mesma função que a emissão usa — a tela
        # não decide o que é "completo", ela pergunta a quem já sabia.
        from apps.fiscal.dps import conferir_cadastro

        contexto["bloqueios"] = (
            conferir_cadastro(intencao.cliente) if not contexto["cancelamento"] else []
        )
        return contexto


class DecidirNotaView(EscopoGrimorioMixin, View):
    """Aprova ou recusa o que está aguardando decisão do contador.

    O `motivo` que vai para a transição nomeia a pessoa e a origem: meses
    depois, "quem autorizou esta nota" tem que ter resposta, e "aprovado" sozinho
    não é resposta.
    """

    def post(self, request, pk):
        from apps.agents.agente_nf.services import (
            ErroCancelamento,
            cancelar_emissao,
            confirmar_cancelamento,
            confirmar_emissao,
        )

        intencao = metricas.intencao_no_escopo(request.user, pk)
        if intencao is None:
            raise Http404("Nota não encontrada nesta carteira.")

        destino = reverse("grimorio:nota", args=[intencao.pk])
        if intencao.estado != "AGUARDANDO_APROVACAO":
            # Dois contadores na mesma fila, ou o cliente confirmando pelo
            # WhatsApp enquanto esta tela estava aberta. Não é erro — é corrida.
            messages.info(
                request,
                "Esta nota já foi decidida — o estado atual é "
                f"{intencao.get_estado_display()}.",
            )
            return HttpResponseRedirect(destino)

        motivo = f"decidido no Grimório por {request.user.get_username()}"

        if request.POST.get("acao") == "recusar":
            cancelar_emissao(intencao, motivo=motivo)
            messages.success(request, "Recusado. Nada foi enviado à prefeitura.")
            return HttpResponseRedirect(destino)

        try:
            autor = request.user.get_username()
            if intencao.tipo_acao == "cancelar_nfse":
                resultado = confirmar_cancelamento(intencao, motivo=motivo, usuario=autor)
            else:
                resultado = confirmar_emissao(
                    intencao,
                    motivo=motivo,
                    origem="contador_painel",
                    usuario=autor,
                )
        except ErroCancelamento as erro:
            messages.error(request, str(erro))
            return HttpResponseRedirect(destino)

        if resultado.ok:
            messages.success(
                request,
                f"Aprovado. Protocolo {resultado.protocolo}."
                if resultado.protocolo
                else "Aprovado.",
            )
        else:
            # A recusa vem da prefeitura, não do sistema — e a diferença importa
            # para o contador saber se corrige o cadastro ou o pedido.
            messages.error(request, f"A prefeitura recusou: {resultado.erro}")
        return HttpResponseRedirect(destino)


class RotinaView(EscopoGrimorioMixin, TemplateView):
    """Guias, obrigações e certidões que exigem o contador.

    **A seção se chama "Guias e obrigações" na tela, e não pode se chamar
    "Rotina".** O nome do primeiro escritório cliente é Rotina Contábil, e a
    palavra como rótulo de menu apareceria como marca dele no painel de todos os
    outros tenants. O guarda de `tests/test_grimorio.py` pegou isso — e pegou
    também o comentário que eu tinha escrito no template para explicar a regra,
    porque ele varre o arquivo inteiro. Daí a explicação estar aqui.

    A palavra é substantivo comum e marca ao mesmo tempo; num SaaS multi-tenant
    a segunda leitura vence. O nome interno (`secao`, rota, app) continua
    `rotina`, minúsculo e nunca renderizado como texto.

    A contraparte obrigatória das ferramentas do Sprint 3: elas leem daqui, e
    sem alguém lançando o dado elas responderiam "ainda não tenho" para sempre.
    É a mesma lição dos chamados — capacidade que promete e não tem quem
    alimente do outro lado vira promessa vazia.

    Mostra só o que está aberto. A tela existe para ser esvaziada, e listar o que
    já foi pago ou entregue afogaria o que falta.
    """

    template_name = "grimorio/rotina.html"
    secao = "rotina"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(metricas.rotina_do_escritorio(self.request.user))
        contexto["dias_alerta_certidao"] = metricas.DIAS_ALERTA_CERTIDAO
        return contexto


class RevisaoDocumentosView(EscopoGrimorioMixin, TemplateView):
    """A fila de revisão de documentos — incortável em qualquer cenário.

    Sem ela o cliente manda a nota, ouve "recebi" e o arquivo fica num bucket
    que ninguém abre. É a promessa mais fácil de quebrar do produto inteiro, e
    a mais cara: o cliente para de cobrar porque acha que está resolvido.

    **Tudo passa por aqui, e é a ordem certa.** Não há OCR ainda, então 100% dos
    documentos exigem humano. Quando o OCR entrar, ele reduz o volume desta fila
    — o inverso, ligar o OCR primeiro e ir corrigindo, começaria por lançamento
    automático de dado não conferido, que é o que o gate do sprint proíbe.
    """

    template_name = "grimorio/documentos_revisao.html"
    secao = "revisao"

    def get_context_data(self, **kwargs):
        from apps.documentos.models import Documento

        contexto = super().get_context_data(**kwargs)
        contexto["documentos"] = metricas.documentos_para_revisar(self.request.user)
        contexto["tipos"] = Documento.Tipo.choices
        return contexto


class ClassificarDocumentoView(EscopoGrimorioMixin, View):
    """Classifica ou recusa um documento da fila.

    Mesmas três regras das outras escritas do Grimório: só POST muda estado, o
    objeto vem do escopo e não do id da URL, e o ato entra na trilha com quem
    clicou.
    """

    def post(self, request, pk):
        from apps.documentos.models import Documento

        documento = metricas.documento_no_escopo(request.user, pk)
        if documento is None:
            raise Http404("Documento não encontrado nesta carteira.")

        destino = reverse("grimorio:revisao_documentos")
        if not documento.aguardando:
            messages.info(
                request, f"O documento {documento.protocolo} já foi revisado."
            )
            return HttpResponseRedirect(destino)

        if request.POST.get("acao") == "recusar":
            motivo = (request.POST.get("motivo") or "").strip()
            documento.recusar(por=request.user, motivo=motivo)
            evento, rotulo = "documento_recusado", "recusado"
        else:
            tipo = request.POST.get("tipo") or Documento.Tipo.OUTRO
            if tipo not in dict(Documento.Tipo.choices):
                messages.error(request, "Tipo de documento desconhecido.")
                return HttpResponseRedirect(destino)
            documento.classificar(tipo, por=request.user)
            evento, rotulo = "documento_classificado", documento.get_tipo_display()

        registrar(
            evento,
            {
                "protocolo": documento.protocolo,
                "tipo": documento.tipo,
                "por": request.user.get_username(),
                "origem": "grimorio",
            },
            cliente=documento.cliente,
        )
        messages.success(request, f"{documento.protocolo}: {rotulo}.")
        return HttpResponseRedirect(destino)


class ArquivoDocumentoView(EscopoGrimorioMixin, View):
    """Redireciona para uma URL assinada de validade curta.

    O arquivo não passa pelo Django: o storage entrega direto. O que passa por
    aqui é a **checagem de escopo** — a assinatura não substitui a permissão,
    ela só evita que megabytes de PDF atravessem a aplicação.

    E a validade é curta de propósito: link permanente de extrato bancário vaza
    no primeiro encaminhamento de WhatsApp, e nunca mais é possível recolher.
    """

    def get(self, request, pk):
        from apps.documentos import armazenamento

        documento = metricas.documento_no_escopo(request.user, pk)
        if documento is None:
            raise Http404("Documento não encontrado nesta carteira.")
        try:
            url = armazenamento.url_temporaria(documento.bucket, documento.chave)
        except armazenamento.ErroDeArmazenamento as erro:
            messages.error(request, f"Não consegui abrir o arquivo: {erro}")
            return HttpResponseRedirect(reverse("grimorio:revisao_documentos"))
        return HttpResponseRedirect(url)
