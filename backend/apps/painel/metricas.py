"""
Camada analítica do Grimório — as contas que o contador olha.

Separado de `views.py` de propósito: aqui não há request, template nem
componente de UI, só queries que devolvem números. É o que permite testar o
"faturamento de julho" sem subir uma página, e reaproveitar a mesma conta no
dashboard, na carteira e (depois) num relatório exportado.

Toda função recebe `usuario` e aplica o MESMO escopo de tenant do admin
(`apps/tenants/escopo.py`). Isso não é zelo redundante: um agregado é
justamente onde o vazamento passa despercebido — uma listagem errada mostra o
nome do cliente do concorrente e alguém nota; um total errado só mostra um
número maior, e ninguém desconfia.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Max, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
from apps.audit.models import Auditoria
from apps.clients.models import Cliente
from apps.credentials.models import Credencial
from apps.fiscal import teto_mei
from apps.fiscal.dps import conferir_cadastro
from apps.painel import apresentacao
from apps.security.models import SessaoWhatsapp
from apps.tenants.escopo import escopo_do_usuario

MESES_PT = [
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
]

ZERO = Coalesce(
    Sum("valor"), Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)
)


def _escopar(qs, usuario, campo="cliente__escritorio"):
    """Aplica o escopo de tenant. Sem vínculo → queryset vazio, nunca tudo."""
    irrestrito, escritorio = escopo_do_usuario(usuario)
    if irrestrito:
        return qs
    if escritorio is None:
        return qs.none()
    return qs.filter(**{campo: escritorio})


def notas_emitidas(usuario):
    """Notas que existem de fato: emissão concluída, não pedido de cancelamento.

    Excluir `tipo_acao='cancelar_nfse'` importa — o pedido de cancelamento é uma
    `Intencao` própria que também chega a CONCLUIDO. Contá-lo dobraria a nota no
    total e, pior, somaria o valor dela duas vezes no faturamento.
    """
    return _escopar(
        Intencao.objects.filter(
            estado=Intencao.Estado.CONCLUIDO, tipo_acao="emitir_nfse"
        ),
        usuario,
    )


# ---------------------------------------------------------------------------
# Séries temporais (gráficos do dashboard)
# ---------------------------------------------------------------------------
def serie_mensal(usuario, meses: int = 12) -> dict:
    """Notas e faturamento por mês, do mês mais antigo ao atual.

    Meses sem nota aparecem como zero em vez de sumir: um gráfico que pula
    junho dá a impressão de que junho não existiu, quando o fato — nenhuma nota
    naquele mês — é exatamente o que o contador precisa enxergar.
    """
    hoje = timezone.localdate()
    # Aritmética em índice de mês, não em dias: recuar "31 dias por mês" erra
    # sempre que o intervalo passa por um mês de 28/30 dias, e o erro aparece
    # como um mês a mais ou a menos no gráfico — o tipo de defeito que ninguém
    # nota olhando, só contando.
    indice = hoje.year * 12 + (hoje.month - 1) - (meses - 1)
    primeiro_mes = date(indice // 12, indice % 12 + 1, 1)

    linhas = (
        notas_emitidas(usuario)
        .filter(atualizado_em__date__gte=primeiro_mes)
        .annotate(mes=TruncMonth("atualizado_em"))
        .values("mes")
        .annotate(quantidade=Count("id"), faturamento=ZERO)
        .order_by("mes")
    )
    por_mes = {
        (linha["mes"].year, linha["mes"].month): linha
        for linha in linhas
        if linha["mes"] is not None
    }

    rotulos, quantidades, faturamentos = [], [], []
    for passo in range(meses):
        cursor = date((indice + passo) // 12, (indice + passo) % 12 + 1, 1)
        linha = por_mes.get((cursor.year, cursor.month))
        rotulos.append(f"{MESES_PT[cursor.month - 1]}/{str(cursor.year)[2:]}")
        quantidades.append(linha["quantidade"] if linha else 0)
        faturamentos.append(float(linha["faturamento"]) if linha else 0.0)

    return {"rotulos": rotulos, "quantidades": quantidades, "faturamentos": faturamentos}


def distribuicao_por_estado(usuario) -> list[tuple[str, int]]:
    """Quantas intenções em cada estado — onde o fluxo está represado."""
    contagem = dict(
        _escopar(Intencao.objects.all(), usuario)
        .values_list("estado")
        .annotate(total=Count("id"))
    )
    return [
        (rotulo, contagem.get(valor, 0))
        for valor, rotulo in Intencao.Estado.choices
        if contagem.get(valor, 0)
    ]


@dataclass
class UsoDaEscada:
    """Quanto do atendimento o T0 resolveu sem LLM (DEC-08).

    O DEC-08 mira 40% e diz explicitamente que sem medição não há como saber se
    o critério de R$ 0,60/cliente/mês foi atingido — "hoje o número não existe".
    Passa a existir: cada mensagem grava a camada que a resolveu, e esta função
    lê a trilha. Estimativa nenhuma no meio.
    """

    total: int
    t0: int
    t1: int
    fallback: int

    @property
    def taxa_t0(self) -> int:
        """Percentual inteiro — o painel não ganha nada com a casa decimal."""
        return round(self.t0 * 100 / self.total) if self.total else 0

    @property
    def atingiu_a_meta(self) -> bool:
        return self.taxa_t0 >= META_T0

    @property
    def sem_dados(self) -> bool:
        """Menos que isso é ruído: 2 de 3 mensagens dariam 67% e não significam
        nada. A tela diz "ainda medindo" em vez de exibir um número falso."""
        return self.total < MINIMO_PARA_MEDIR


META_T0 = 40  # DEC-08
MINIMO_PARA_MEDIR = 20


def uso_da_escada(usuario, dias: int = 30) -> UsoDaEscada:
    """Distribuição das mensagens por camada nos últimos `dias`.

    Só conta mensagem que chegou a ser roteada. Mensagem de número não
    cadastrado nem gera este evento, e incluí-la inflaria o denominador com
    tráfego que camada nenhuma atendeu.
    """
    desde = timezone.now() - timedelta(days=dias)
    eventos = _escopar(
        Auditoria.objects.filter(
            evento="orquestrador_mensagem_processada", criado_em__gte=desde
        ),
        usuario,
    )
    contagem = {"t0": 0, "t1": 0, "fallback": 0}
    for dados in eventos.values_list("dados", flat=True).iterator(chunk_size=1000):
        # `camada` só existe em evento gravado depois de 09/ago/2026. Mensagem
        # anterior conta como t1, que é onde ela de fato foi resolvida.
        camada = (dados or {}).get("camada", "t1")
        if camada in contagem:
            contagem[camada] += 1
    return UsoDaEscada(total=sum(contagem.values()), **contagem)


# ---------------------------------------------------------------------------
# Carteira — uma linha por cliente
# ---------------------------------------------------------------------------
@dataclass
class LinhaCarteira:
    cliente: Cliente
    notas_ano: int
    faturamento_ano: Decimal
    notas_mes: int
    faturamento_mes: Decimal
    uso_teto: teto_mei.UsoDoTeto
    cadastro_faltante: list[str]
    sessao: "SessaoWhatsapp | None"
    ultima_nota: "date | None"

    @property
    def visual_teto(self) -> dict:
        """Cor/ícone/rótulo do radar — ver apps/painel/apresentacao.py."""
        return apresentacao.visual_do_teto(self.uso_teto)

    @property
    def pronto_para_emitir(self) -> bool:
        return not self.cadastro_faltante

    @property
    def sessao_ativa(self) -> bool:
        return self.sessao is not None and self.sessao.status == SessaoWhatsapp.Status.ATIVA


def carteira(usuario, ano: int | None = None) -> list[LinhaCarteira]:
    """A carteira do escritório com os números que decidem uma conversa.

    Uma query agregada por cliente em vez de N+1: com 200 MEIs na carteira, a
    versão ingênua faria 600 consultas para desenhar uma tabela.
    """
    ano = ano or timezone.localdate().year
    inicio_ano = date(ano, 1, 1)
    inicio_mes = timezone.localdate().replace(day=1)

    emitidas = Q(intencoes_fiscais__estado=Intencao.Estado.CONCLUIDO) & Q(
        intencoes_fiscais__tipo_acao="emitir_nfse"
    )

    clientes = (
        _escopar(Cliente.objects.all(), usuario, campo="escritorio")
        .select_related("escritorio", "sessao_whatsapp")
        .annotate(
            notas_ano=Count(
                "intencoes_fiscais",
                filter=emitidas & Q(intencoes_fiscais__atualizado_em__date__gte=inicio_ano),
            ),
            faturamento_ano=Coalesce(
                Sum(
                    "intencoes_fiscais__valor",
                    filter=emitidas & Q(intencoes_fiscais__atualizado_em__date__gte=inicio_ano),
                ),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            notas_mes=Count(
                "intencoes_fiscais",
                filter=emitidas & Q(intencoes_fiscais__atualizado_em__date__gte=inicio_mes),
            ),
            faturamento_mes=Coalesce(
                Sum(
                    "intencoes_fiscais__valor",
                    filter=emitidas & Q(intencoes_fiscais__atualizado_em__date__gte=inicio_mes),
                ),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )
        .order_by("-faturamento_ano", "nome")
    )

    ultimas = dict(
        notas_emitidas(usuario)
        .values_list("cliente_id")
        .annotate(ultima=Max("atualizado_em"))
        .order_by()
    )

    return [
        LinhaCarteira(
            cliente=c,
            notas_ano=c.notas_ano,
            faturamento_ano=c.faturamento_ano,
            notas_mes=c.notas_mes,
            faturamento_mes=c.faturamento_mes,
            uso_teto=teto_mei.avaliar(c, c.faturamento_ano, ano),
            cadastro_faltante=conferir_cadastro(c),
            sessao=getattr(c, "sessao_whatsapp", None),
            ultima_nota=ultimas.get(c.id),
        )
        for c in clientes
    ]


def alertas_de_teto(usuario) -> list[LinhaCarteira]:
    """Só quem exige ação: MEI em atenção, crítico ou já estourado."""
    return [
        linha
        for linha in carteira(usuario)
        if linha.uso_teto.aplicavel and linha.uso_teto.situacao != "tranquilo"
    ]


# ---------------------------------------------------------------------------
# Integrações — o que está ligado, e o que falta ligar
# ---------------------------------------------------------------------------
@dataclass
class LinhaIntegracao:
    cliente: Cliente
    certificado: "Credencial | None"
    credenciais_erp: list
    sessao: "SessaoWhatsapp | None"

    @property
    def certificado_vencido(self) -> bool:
        dias = self.dias_para_vencer
        return dias is not None and dias < 0

    @property
    def dias_para_vencer(self) -> "int | None":
        """Certificado A1 vale 1 ano — e vencer sem aviso para a emissão do
        cliente inteiro. O número aqui é o que permite avisar antes."""
        validade = getattr(self.certificado, "certificado_validade", None)
        if validade is None:
            return None
        return (validade - timezone.localdate()).days

    @property
    def pendencias(self) -> list[str]:
        """O que impede este cliente de operar por inteiro — em português de contador."""
        faltas = []
        if self.certificado is None:
            faltas.append("sem certificado digital")
        elif self.certificado_vencido:
            faltas.append("certificado vencido")
        if not self.credenciais_erp:
            faltas.append("sem ERP conectado")
        if self.sessao is None or self.sessao.status != SessaoWhatsapp.Status.ATIVA:
            faltas.append("WhatsApp não vinculado")
        return faltas


def integracoes(usuario) -> list[LinhaIntegracao]:
    credenciais = _escopar(Credencial.objects.select_related("cliente"), usuario)
    por_cliente: dict[int, list[Credencial]] = {}
    for credencial in credenciais:
        por_cliente.setdefault(credencial.cliente_id, []).append(credencial)

    tipos_certificado = {Credencial.Tipo.CERTIFICADO_PSC, Credencial.Tipo.CERTIFICADO_PFX}
    linhas = []
    for cliente in (
        _escopar(Cliente.objects.all(), usuario, campo="escritorio")
        .select_related("sessao_whatsapp")
        .order_by("nome")
    ):
        do_cliente = por_cliente.get(cliente.id, [])
        linhas.append(
            LinhaIntegracao(
                cliente=cliente,
                certificado=next((c for c in do_cliente if c.tipo in tipos_certificado), None),
                credenciais_erp=[c for c in do_cliente if c.tipo not in tipos_certificado],
                sessao=getattr(cliente, "sessao_whatsapp", None),
            )
        )
    return linhas


# ---------------------------------------------------------------------------
# Pendências — "o que exige você agora"
# ---------------------------------------------------------------------------
# A tela inicial do Grimório não é um resumo do mês: é uma fila de trabalho. O
# contador com 200 empresas não quer saber quantas notas saíram, quer saber
# quais três coisas travam hoje. Por isso as pendências vêm de fontes diferentes
# unificadas numa lista só, ordenada por urgência — não por tabela de origem.
#
# `severidade` não é enfeite: define a ordem e o sinal visual. "Crítica" é o que
# tem prazo ou já falhou; "atenção" é o que vai virar crítico se ninguém agir.
CRITICA, ATENCAO = "critica", "atencao"

# Um certificado A1 vale um ano e derruba a emissão do cliente inteiro quando
# vence. Trinta dias é o que dá para renovar sem correria — abaixo disso o
# assunto deixa de ser aviso e vira tarefa.
DIAS_ALERTA_CERTIFICADO = 30


@dataclass
class Pendencia:
    severidade: str
    categoria: str
    titulo: str
    detalhe: str
    cliente: "Cliente | None" = None
    acao_rotulo: str = ""
    acao_url: str = ""

    @property
    def critica(self) -> bool:
        return self.severidade == CRITICA


def pendencias(usuario) -> list[Pendencia]:
    """Tudo que exige ação do contador, de todas as fontes, em uma fila só."""
    itens: list[Pendencia] = []

    # --- Notas paradas esperando decisão humana ---------------------------
    aguardando = (
        _escopar(
            Intencao.objects.filter(estado=Intencao.Estado.AGUARDANDO_APROVACAO), usuario
        )
        .select_related("cliente")
        .order_by("criado_em")
    )
    for intencao in aguardando:
        cancelamento = intencao.tipo_acao == "cancelar_nfse"
        itens.append(
            Pendencia(
                severidade=CRITICA,
                categoria="cancelamento" if cancelamento else "nota",
                titulo=(
                    "Pedido de cancelamento aguardando você"
                    if cancelamento
                    # Cancelamento nunca é decidido pelo cliente (regra de
                    # `_pedir_cancelamento`): se está aqui, é porque só o
                    # contador pode resolver.
                    else "Nota aguardando aprovação"
                ),
                detalhe=apresentacao.resumo_da_intencao(intencao),
                cliente=intencao.cliente,
                acao_rotulo="Analisar",
                acao_url=f"/admin/agente_nf/intencao/{intencao.pk}/change/",
            )
        )

    # --- Notas que a prefeitura recusou -----------------------------------
    rejeitadas = (
        _escopar(Intencao.objects.filter(estado=Intencao.Estado.REJEITADO), usuario)
        .select_related("cliente")
        .order_by("-atualizado_em")[:20]
    )
    for intencao in rejeitadas:
        itens.append(
            Pendencia(
                severidade=CRITICA,
                categoria="rejeicao",
                titulo="Nota rejeitada",
                detalhe=apresentacao.resumo_da_intencao(intencao),
                cliente=intencao.cliente,
                acao_rotulo="Ver motivo",
                acao_url=f"/admin/agente_nf/intencao/{intencao.pk}/change/",
            )
        )

    # --- Integrações: certificado e canal ---------------------------------
    for linha in integracoes(usuario):
        dias = linha.dias_para_vencer
        if linha.certificado is None:
            itens.append(
                Pendencia(
                    severidade=ATENCAO,
                    categoria="certificado",
                    titulo="Sem certificado digital",
                    detalhe="Não emite nota até vincular um certificado.",
                    cliente=linha.cliente,
                    acao_rotulo="Vincular",
                    acao_url="/admin/credentials/credencial/add/",
                )
            )
        elif dias is not None and dias < 0:
            itens.append(
                Pendencia(
                    severidade=CRITICA,
                    categoria="certificado",
                    titulo="Certificado vencido",
                    detalhe=f"Venceu há {abs(dias)} dia(s). A emissão está parada.",
                    cliente=linha.cliente,
                    acao_rotulo="Renovar",
                    acao_url=f"/admin/credentials/credencial/{linha.certificado.pk}/change/",
                )
            )
        elif dias is not None and dias <= DIAS_ALERTA_CERTIFICADO:
            itens.append(
                Pendencia(
                    severidade=ATENCAO,
                    categoria="certificado",
                    titulo="Certificado vence em breve",
                    detalhe=f"Faltam {dias} dia(s).",
                    cliente=linha.cliente,
                    acao_rotulo="Renovar",
                    acao_url=f"/admin/credentials/credencial/{linha.certificado.pk}/change/",
                )
            )

    # --- Radar de teto do MEI ---------------------------------------------
    for linha in carteira(usuario):
        uso = linha.uso_teto
        if uso.aplicavel and uso.situacao != "tranquilo":
            estourou = uso.situacao.startswith("estourado")
            # Quem já passou do teto não tem "quanto falta" — tem quanto
            # excedeu. Dizer "faltam R$ 0,00 para o limite" a quem estourou é
            # pior que não dizer nada: some justamente a informação que decide
            # a conversa com o cliente (e, acima de 20%, o desenquadramento é
            # retroativo).
            excedente = uso.faturamento - uso.teto
            itens.append(
                Pendencia(
                    severidade=CRITICA if estourou else ATENCAO,
                    categoria="teto",
                    titulo=f"Teto do MEI: {linha.visual_teto['rotulo']}",
                    detalhe=(
                        f"{uso.percentual}% do teto — "
                        + (
                            f"ultrapassou em {apresentacao.moeda(excedente)}."
                            if estourou
                            else f"faltam {apresentacao.moeda(uso.restante)} para o limite."
                        )
                    ),
                    cliente=linha.cliente,
                    acao_rotulo="Ver empresa",
                    acao_url=f"/grimorio/empresa/{linha.cliente.pk}/",
                )
            )
        if linha.cadastro_faltante:
            itens.append(
                Pendencia(
                    severidade=ATENCAO,
                    categoria="cadastro",
                    titulo="Cadastro incompleto para emitir",
                    detalhe="Falta: " + ", ".join(linha.cadastro_faltante) + ".",
                    cliente=linha.cliente,
                    acao_rotulo="Completar",
                    acao_url=f"/admin/clients/cliente/{linha.cliente.pk}/change/",
                )
            )
        if not linha.sessao_ativa:
            itens.append(
                Pendencia(
                    severidade=ATENCAO,
                    categoria="canal",
                    titulo="WhatsApp não vinculado",
                    detalhe="O cliente não consegue falar com o agente até validar o número.",
                    cliente=linha.cliente,
                    acao_rotulo="Ver empresa",
                    acao_url=f"/grimorio/empresa/{linha.cliente.pk}/",
                )
            )

    # Crítico primeiro. Dentro do mesmo nível, a ordem de inserção preserva o
    # agrupamento por assunto — o contador resolve "todos os certificados" de
    # uma vez, e não pulando de tema em tema.
    itens.sort(key=lambda p: 0 if p.critica else 1)
    return itens


# ---------------------------------------------------------------------------
# Ficha da empresa — tudo de um cliente num lugar só
# ---------------------------------------------------------------------------
@dataclass
class Ficha:
    cliente: Cliente
    linha: LinhaCarteira
    integracao: "LinhaIntegracao | None"
    notas: list
    eventos: list
    serie: dict
    # Quem fala por esta empresa no WhatsApp (DEC-03). Vive na ficha porque a
    # pergunta "quem é esse número que mandou emitir a nota" é a primeira que o
    # contador faz quando algo sai errado.
    vinculos: list


def ficha_da_empresa(usuario, cliente_id: int) -> "Ficha | None":
    """A empresa inteira numa tela: números, notas, integrações e histórico.

    Devolve `None` quando o cliente não pertence ao escopo — e é assim que a
    view vira 404. Nunca se pergunta "existe?" antes de "é meu?": a resposta
    "existe, mas não é seu" já é informação sobre a carteira do vizinho.
    """
    cliente = (
        _escopar(Cliente.objects.all(), usuario, campo="escritorio")
        .filter(pk=cliente_id)
        .select_related("escritorio")
        .first()
    )
    if cliente is None:
        return None

    linha = next((l for l in carteira(usuario) if l.cliente.pk == cliente.pk), None)
    integracao = next((i for i in integracoes(usuario) if i.cliente.pk == cliente.pk), None)

    notas = list(
        _escopar(Intencao.objects.filter(cliente=cliente), usuario).order_by("-atualizado_em")[:25]
    )
    eventos = list(
        _escopar(Auditoria.objects.filter(cliente=cliente), usuario).order_by("-criado_em")[:30]
    )
    vinculos = list(
        cliente.vinculos.filter(ativo=True, usuario__ativo=True)
        .select_related("usuario")
        .order_by("-principal", "usuario__nome", "pk")
    )
    return Ficha(
        cliente=cliente,
        linha=linha,
        integracao=integracao,
        notas=notas,
        eventos=eventos,
        serie=serie_mensal_do_cliente(usuario, cliente),
        vinculos=vinculos,
    )


def serie_mensal_do_cliente(usuario, cliente, meses: int = 12) -> dict:
    """Mesma série do dashboard, restrita a uma empresa."""
    hoje = timezone.localdate()
    indice = hoje.year * 12 + (hoje.month - 1) - (meses - 1)
    primeiro_mes = date(indice // 12, indice % 12 + 1, 1)

    linhas = (
        notas_emitidas(usuario)
        .filter(cliente=cliente, atualizado_em__date__gte=primeiro_mes)
        .annotate(mes=TruncMonth("atualizado_em"))
        .values("mes")
        .annotate(quantidade=Count("id"), faturamento=ZERO)
    )
    por_mes = {
        (l["mes"].year, l["mes"].month): l for l in linhas if l["mes"] is not None
    }
    rotulos, quantidades, faturamentos = [], [], []
    for passo in range(meses):
        cursor = date((indice + passo) // 12, (indice + passo) % 12 + 1, 1)
        linha = por_mes.get((cursor.year, cursor.month))
        rotulos.append(f"{MESES_PT[cursor.month - 1]}/{str(cursor.year)[2:]}")
        quantidades.append(linha["quantidade"] if linha else 0)
        faturamentos.append(float(linha["faturamento"]) if linha else 0.0)
    return {"rotulos": rotulos, "quantidades": quantidades, "faturamentos": faturamentos}


# ---------------------------------------------------------------------------
# Documentos fiscais — os artefatos de cada nota
# ---------------------------------------------------------------------------
def documentos(usuario, limite: int = 200) -> "OrderedDict[str, list[Intencao]]":
    """Notas emitidas agrupadas por mês, com protocolo/chave/DANFSE.

    "Documento" aqui é a nota autorizada e o que ela deixou como rastro. Ainda
    **não** existe DANFSE em PDF nem guarda do XML assinado — a tela mostra o
    que há (protocolo, chave de acesso, link) e diz o que falta, em vez de
    exibir um botão de download que não baixa nada.
    """
    grupos: "OrderedDict[str, list[Intencao]]" = OrderedDict()
    consulta = (
        notas_emitidas(usuario)
        .select_related("cliente")
        .order_by("-atualizado_em")[:limite]
    )
    for nota in consulta:
        chave = f"{MESES_PT[nota.atualizado_em.month - 1]}/{nota.atualizado_em.year}"
        grupos.setdefault(chave, []).append(nota)
    return grupos
