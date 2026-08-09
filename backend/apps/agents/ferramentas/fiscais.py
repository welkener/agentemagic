"""
Ferramentas de nota fiscal e de acompanhamento do teto.

Os handlers são finos de propósito: a condução da conversa mora em
`agente_nf/conversa.py`, que é onde a máquina de estados e a auditoria já estão
ligadas. Aqui só existe o que faz uma capacidade ser uma capacidade — nome,
descrição na voz do produto, tier e o texto de recusa quando o perfil não
alcança.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from apps.agents.agente_nf import conversa
from apps.agents.agente_nf.models import Intencao
from apps.agents.ferramentas.base import registrar_ferramenta
from apps.fiscal import teto_mei


@registrar_ferramenta(
    "emitir_nota",
    descricao="emitir uma nova nota fiscal de serviço (NFS-e)",
    entrada=conversa.DadosNotaExtraidos,
    recusa=(
        "Emissão de nota fiscal ainda não está liberada para o seu "
        "perfil. Fale com seu contador para habilitar. 🙏"
    ),
    exemplos=("emite uma nota de 500 pro João", "preciso fazer uma nfs-e"),
)
def emitir_nota(ctx, mensagem: str) -> str:
    return conversa.iniciar_emissao(ctx, mensagem)


@registrar_ferramenta(
    "consultar_nota",
    descricao="listar as notas fiscais já emitidas",
    recusa=(
        "Consulta de notas ainda não está liberada para o seu perfil. "
        "Fale com seu contador para habilitá-la. 🙏"
    ),
    exemplos=("quais notas eu emiti?", "cadê minha nota de ontem?"),
)
def consultar_nota(ctx, mensagem: str) -> str:
    return conversa.consultar_notas(ctx)


@registrar_ferramenta(
    "cancelar_nota",
    descricao="pedir o cancelamento de uma nota já emitida",
    exemplos=("cancela a nota que saiu errada", "quero anular a última nfs-e"),
    # O catálogo classifica `cancelar_nota` como Tier 3, e continua certo: o ato
    # de cancelar documento fiscal é destrutivo. Só que **não é isto que esta
    # ferramenta faz** — ela abre um pedido para o contador, que decide. Conferir
    # o tier aqui faria o perfil comum (Tier 1) ouvir "não liberado" ao tentar
    # avisar que uma nota saiu errada, e o cliente sem canal para corrigir um
    # erro fiscal é um problema pior que o que a trava evitaria.
    tier_conferido=False,
)
def cancelar_nota(ctx, mensagem: str) -> str:
    return conversa.pedir_cancelamento(ctx, mensagem)


def faturamento_no_ano(cliente, ano: int) -> Decimal:
    """Soma das notas emitidas por aqui no ano — a mesma regra do painel.

    Mora neste módulo, e não em `apps/fiscal`, porque a consulta precisa de
    `agente_nf.Intencao`: pôr o import lá dentro faria o motor fiscal depender do
    agente, que é justamente a dependência que o Sprint 3 vai testar que não
    existe. `teto_mei.avaliar` continua puro, recebendo o número pronto.
    """
    emitidas = Q(estado=Intencao.Estado.CONCLUIDO) & Q(tipo_acao="emitir_nfse")
    total = (
        Intencao.objects.filter(cliente=cliente)
        .filter(emitidas, atualizado_em__year=ano)
        .aggregate(total=Sum("valor"))["total"]
    )
    return Decimal(total or 0)


@registrar_ferramenta(
    "consultar_faturamento_acumulado",
    descricao="dizer quanto a empresa já faturou no ano e quanto falta para o teto",
    exemplos=("quanto eu já faturei esse ano?", "tô perto do limite do MEI?"),
)
def consultar_faturamento_acumulado(ctx, mensagem: str) -> str:
    """Radar de teto na voz do cliente.

    ⚠ O aviso de parcialidade não é rodapé decorativo. Esta conta só enxerga
    nota emitida **por aqui**; quem emitiu pela prefeitura no ano passado tem um
    número maior do que o que vai ler. Omitir isso daria ao MEI exatamente a
    falsa segurança que o desenquadramento retroativo pune.
    """
    ano = date.today().year
    uso = teto_mei.avaliar(ctx.cliente, faturamento_no_ano(ctx.cliente, ano), ano)

    if not uso.aplicavel:
        # ME/EPP tem outro limite, e ele ainda não está implementado. Dizer o
        # faturamento e parar é honesto; aplicar o teto do MEI a quem não é MEI
        # seria dar um alarme falso com cara de número oficial.
        return (
            f"No ano de {ano} você emitiu R$ {uso.faturamento:.2f} em notas por aqui. 📊\n\n"
            "O limite anual do Simples para a sua empresa é diferente do MEI — "
            "seu contador acompanha esse número junto com o que foi emitido por fora."
        )

    linhas = [
        f"Faturamento de {ano} 📊",
        "",
        f"Emitido por aqui: R$ {uso.faturamento:.2f}",
        f"Teto do MEI{' (proporcional à abertura)' if uso.proporcional else ''}: R$ {uso.teto:.2f}",
        f"Você usou {uso.percentual}% — ainda cabem R$ {uso.restante:.2f}.",
    ]

    recado = {
        "atencao": "Já passou de 70% do teto. Vale conversar com seu contador sobre o ano. 🟡",
        "critico": "Passou de 90% do teto. Fale com seu contador esta semana. 🟠",
        "estourado": (
            "Você passou do teto. O desenquadramento vale a partir de janeiro do "
            "ano que vem — seu contador precisa saber agora. 🔴"
        ),
        "estourado_grave": (
            "Você passou do teto em mais de 20%, e nesse caso o desenquadramento "
            "é retroativo. Fale com seu contador hoje. 🔴"
        ),
    }.get(uso.situacao)
    if recado:
        linhas += ["", recado]

    if uso.parcial:
        linhas += [
            "",
            "⚠️ Essa conta só inclui as notas emitidas pelo Magic BI. "
            "Se você emitiu nota por fora, o valor real é maior.",
        ]
    return "\n".join(linhas)
