"""
A fila de revisão de documentos, dentro do admin.

**Por que ela mudou de casa.** Nasceu no Grimório, em `/grimorio/revisao/`, com
casca e CSS próprios. Isso deu duas superfícies web ao contador — dois menus,
dois logins, dois vocabulários — e o pedido do usuário (12/ago/2026) foi
explícito: uma coisa só, dentro do admin. É o mesmo movimento já feito em
26/jul com o `/painel/` solto, e a lição se repete: superfície separada custa
mais do que parece, e o custo aparece no usuário antes de aparecer no código.

O que **não** mudou, e é o ponto: as contas continuam em `metricas.py`, o
recebimento continua em `documentos/services.py`, e o gate continua em
`Documento.aplicar_extracao`. Migrar tela é trocar o template e a rota — se
tivesse exigido tocar em regra de negócio, a divisão anterior estaria errada.

**Três regras de escrita, as mesmas do resto:** só POST muda estado; o objeto
vem do escopo do contador e nunca do id da URL; e o ato entra na trilha com
quem clicou.
"""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.encoding import escape_uri_path
from django.views import View
from django.views.generic import TemplateView
from unfold.views import UnfoldModelAdminViewMixin

from apps.agents.contexto import SessionContext
from apps.audit.services import registrar
from apps.documentos import services as documentos_services
from apps.documentos.models import Documento
from apps.painel import metricas


def url_da_revisao() -> str:
    return reverse("admin:painel_revisao")


class RevisaoDocumentosView(UnfoldModelAdminViewMixin, TemplateView):
    """A fila que o cronograma marca como incortável, mais o que saiu dela.

    **Duas listas, e a segunda existe por causa da primeira.** Em cima, o que
    espera humano. Embaixo, o que a leitura automática resolveu sozinha —
    visível justamente porque ninguém o revisou. A extração encurta a lista de
    cima; a de baixo é o preço de encurtá-la sem pedir fé.

    E o formulário de envio no topo, que é a única porta de documento que não
    depende do WhatsApp do cliente.
    """

    title = "Revisão de documentos"
    permission_required = ("documentos.view_documento",)
    template_name = "painel/revisao.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        contexto.update(
            {
                "documentos": metricas.documentos_para_revisar(usuario),
                "lidos": metricas.documentos_lidos_pela_maquina(usuario),
                "tipos": [
                    (valor, rotulo)
                    for valor, rotulo in Documento.Tipo.choices
                    if valor != Documento.Tipo.DESCONHECIDO
                ],
                "clientes": metricas.clientes_para_escolher(usuario),
                "tamanho_maximo_mb": documentos_services.TAMANHO_MAXIMO // (1024 * 1024),
            }
        )
        return contexto


class ClassificarDocumentoView(View):
    """Classifica ou recusa um documento da fila — ou corrige o que a máquina leu.

    **Quem já passou por humano fica como está; o que a máquina classificou,
    não.** A diferença não é hierarquia entre pessoa e programa: é que o
    documento classificado por máquina nunca teve ninguém discordando dele, e a
    primeira pessoa a olhar precisa poder. Já o que outro contador decidiu tem
    autor, hora e trilha — desfazer isso por um formulário de lista seria apagar
    uma decisão sem que ninguém soubesse que ela existiu.
    """

    def post(self, request, pk):
        documento = metricas.documento_no_escopo(request.user, pk)
        if documento is None:
            raise Http404("Documento não encontrado nesta carteira.")

        destino = url_da_revisao()
        corrigindo_maquina = documento.classificado_por_maquina
        if not documento.aguardando and not corrigindo_maquina:
            messages.info(request, f"O documento {documento.protocolo} já foi revisado.")
            return HttpResponseRedirect(destino)

        tipo_anterior = documento.tipo

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
                # Discordância do humano com a leitura automática é a métrica que
                # diz se a extração pode crescer ou precisa encolher. Ela só
                # existe se for gravada no momento em que acontece.
                "corrigiu_leitura_automatica": corrigindo_maquina,
                "tipo_anterior": tipo_anterior if corrigindo_maquina else "",
            },
            cliente=documento.cliente,
        )
        if corrigindo_maquina:
            messages.success(request, f"{documento.protocolo}: corrigido para {rotulo}.")
        else:
            messages.success(request, f"{documento.protocolo}: {rotulo}.")
        return HttpResponseRedirect(destino)


class ArquivoDocumentoView(View):
    """Entrega o arquivo a quem tem escopo para vê-lo.

    Por dois caminhos, e o que decide é se o storage tem endereço que o
    navegador alcança:

    - **tem** (AWS, ou MinIO publicado): redirect para URL assinada de validade
      curta. Megabytes de PDF não atravessam a aplicação, e a validade é curta
      de propósito — link permanente de extrato bancário vaza no primeiro
      encaminhamento de WhatsApp e nunca mais é possível recolher;
    - **não tem** (o servidor de hoje, onde o MinIO não publica porta): o
      arquivo sai por aqui. Custa banda e memória do Django, e ainda assim é o
      certo — a alternativa é um botão "Abrir" que não abre.

    Nos dois casos a permissão é conferida **antes**: assinatura não é
    autorização, e o redirect só acontece depois de o escopo bater.
    """

    def get(self, request, pk):
        from apps.documentos import armazenamento

        documento = metricas.documento_no_escopo(request.user, pk)
        if documento is None:
            raise Http404("Documento não encontrado nesta carteira.")
        try:
            if armazenamento.alcancavel_pelo_navegador():
                return HttpResponseRedirect(
                    armazenamento.url_temporaria(documento.bucket, documento.chave)
                )
            resposta = HttpResponse(
                armazenamento.baixar(documento.bucket, documento.chave),
                content_type=documento.tipo_mime or "application/octet-stream",
            )
            # `inline`: o contador está conferindo, não arquivando — abrir a nota
            # na aba e voltar para a fila é o gesto, baixar para a pasta não.
            resposta["Content-Disposition"] = (
                f'inline; filename="{escape_uri_path(documento.nome_arquivo)}"'
            )
            return resposta
        except armazenamento.ErroDeArmazenamento as erro:
            messages.error(request, f"Não consegui abrir o arquivo: {erro}")
            return HttpResponseRedirect(url_da_revisao())


class EnviarDocumentoView(View):
    """O contador põe um arquivo no sistema pela própria tela.

    **Existia um buraco no formato de U.** O modelo já previa `Origem.PAINEL`
    desde o primeiro dia, e nada preenchia esse valor: a única porta de entrada
    era o WhatsApp do cliente. Enquanto o número não está conectado, o produto
    inteiro fica sem como receber um documento — e mesmo com ele conectado, o
    caso mais banal do escritório não tinha caminho: a nota que chegou por
    e-mail, o boleto entregue impresso, o XML baixado do portal da SEFAZ.

    Passa exatamente pelo mesmo `services.receber` do WhatsApp — mesma leitura,
    mesmo gate, mesma trilha, mesmo reconhecimento de duplicata. Uma segunda
    implementação "só para o painel" seria o começo de dois comportamentos
    diferentes para o mesmo ato, e a divergência apareceria justamente onde
    ninguém procura: no arquivo que entrou pelo caminho menos usado.
    """

    def post(self, request):
        destino = url_da_revisao()

        # O id vem de um `<select>`, mas quem posta o formulário decide o que
        # manda. `filter(pk="banana")` levanta ValueError, e um 500 aqui seria
        # relatado como "o painel quebrou ao enviar nota".
        try:
            cliente_id = int(request.POST.get("cliente") or 0)
        except (TypeError, ValueError):
            cliente_id = 0

        cliente = metricas.cliente_no_escopo(request.user, cliente_id)
        if cliente is None:
            messages.error(request, "Escolha uma empresa da sua carteira.")
            return HttpResponseRedirect(destino)

        arquivo = request.FILES.get("arquivo")
        if arquivo is None:
            messages.error(request, "Nenhum arquivo veio junto.")
            return HttpResponseRedirect(destino)

        try:
            documento, novo = documentos_services.receber(
                ctx=SessionContext.da_conversa(cliente=cliente, canal="painel"),
                conteudo=arquivo.read(),
                nome_arquivo=arquivo.name,
                tipo_mime=arquivo.content_type or "",
                origem=Documento.Origem.PAINEL,
            )
        except documentos_services.ErroDeRecebimento as erro:
            messages.error(request, str(erro))
            return HttpResponseRedirect(destino)

        registrar(
            "documento_recebido_no_painel",
            {
                "protocolo": documento.protocolo,
                "por": request.user.get_username(),
                "metodo_leitura": (documento.dados_extraidos or {}).get("metodo", ""),
                "confianca": documento.confianca,
            },
            cliente=cliente,
        )

        if not novo:
            messages.info(request, f"Esse arquivo já estava aqui: {documento.protocolo}.")
        else:
            messages.success(request, recado_do_envio(documento))
        return HttpResponseRedirect(destino)


def recado_do_envio(documento) -> str:
    """O que a tela responde depois do upload.

    Diz o que a leitura conseguiu e o que ela não conseguiu, na mesma frase. O
    contador que sobe um arquivo e lê só "enviado" não sabe se precisa voltar
    para classificar — e é justamente essa dúvida que a fila existe para não
    criar.
    """
    resumo = (documento.dados_extraidos or {}).get("resumo") or ""
    if documento.classificado_por_maquina:
        return (
            f"{documento.protocolo}: {resumo or documento.get_tipo_display()} — "
            "li sozinho e já classifiquei."
        )
    if resumo:
        return f"{documento.protocolo}: {resumo}. Está na fila para você conferir."
    return f"{documento.protocolo}: recebido. Não consegui ler — está na fila."
