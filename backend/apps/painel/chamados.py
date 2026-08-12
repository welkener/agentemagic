"""
Resolver um chamado direto da fila, sem abrir o registro.

**A ação existe porque a viagem custava caro.** Antes ela levava para a página
de edição do chamado — outra tela, outro carregamento, e a volta manual para a
fila. No meio de uma tarefa isso é a costura que faz a fila envelhecer: o
contador adia o clique porque o clique custa três.

Começou por chamado, e não por nota, por ser a ação **sem efeito fiscal, sem
custo para o cliente e trivialmente reversível** — reabrir é mudar um campo.
Aprovar e cancelar nota continuam passando pelos serviços auditados de
`agents/agente_nf`: a máquina de estados fiscal não ganha atalho por
conveniência de tela.

Três regras, e qualquer escrita futura de fila precisa repetir: só POST muda
estado (um `GET` que resolve chamado é resolvido pelo pré-carregador do
navegador); o objeto vem do escopo do contador, nunca do id da URL; e o ato
entra na trilha com quem clicou.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.views import View

from apps.audit.services import registrar
from apps.painel import metricas


class ResolverSolicitacaoView(View):
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
                    "origem": "painel",
                },
                cliente=solicitacao.cliente,
            )
            messages.success(
                request, f"Chamado {solicitacao.protocolo} marcado como resolvido."
            )
        else:
            # Dois cliques no mesmo botão, ou dois contadores ao mesmo tempo.
            # Não é erro: o desfecho pretendido já é o atual.
            messages.info(
                request, f"O chamado {solicitacao.protocolo} já estava resolvido."
            )

        # Volta para onde o contador estava. `next` é conferido contra o próprio
        # admin — aceitar qualquer URL aqui seria um redirecionador aberto, e
        # este formulário fica atrás de login mas dentro de um domínio que o
        # cliente final também acessa.
        destino = request.POST.get("next") or ""
        if not destino.startswith("/admin/"):
            destino = reverse("admin:index")
        return HttpResponseRedirect(destino)
