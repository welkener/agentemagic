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
from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, Max, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.agents.agente_nf.models import Intencao
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
