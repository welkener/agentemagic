"""
Ferramentas da rotina contábil — o que o escritório de fato entrega (Sprint 3).

São as primeiras capacidades do produto **Hermes** (o escritório) e não do
Fiscus/Lumen (a empresa): guia, obrigação, certidão e folha são assunto do
contador, e é por elas que o cliente liga no dia 15.

Todas são Tier 0 — leitura pura — e nenhuma depende de ERP. Isso importa: são as
que funcionam no dia um de um escritório novo, antes de qualquer integração.

**A regra que atravessa as cinco**: sem registro, a resposta é "ainda não tenho".
O módulo `rotina/consultas.py` carrega o porquê; aqui basta dizer que nenhuma
delas calcula, estima ou repete a competência anterior.
"""
from __future__ import annotations

from apps.agents.ferramentas.base import registrar_ferramenta
from apps.rotina import consultas
from apps.rotina.models import Guia


@registrar_ferramenta(
    "consultar_das",
    descricao="informar o valor e o vencimento do DAS do Simples de um mês",
    exemplos=("quanto é o DAS desse mês?", "me manda o DAS de julho"),
)
def consultar_das(ctx, mensagem: str) -> str:
    competencia = consultas.interpretar_competencia(mensagem)
    if competencia is None:
        # Perguntar o mês custa uma volta de conversa; chutar erra a resposta
        # inteira — e o cliente anota o valor errado.
        return (
            "De qual mês? 🗓️ Me diga assim: *DAS de julho* ou *DAS de 07/2026*."
        )
    return consultas.guia_do_mes(ctx.cliente, Guia.Tipo.DAS, competencia)


@registrar_ferramenta(
    "segunda_via_guia",
    descricao="mandar a segunda via de uma guia (DAS, DARF, GPS, FGTS ou ISS)",
    exemplos=("preciso da segunda via do DAS", "quais guias estão em aberto?"),
)
def segunda_via_guia(ctx, mensagem: str) -> str:
    """Uma guia específica quando o tipo e o mês estão claros; a lista de
    abertas quando não estão.

    Cair na lista em vez de pedir esclarecimento é escolha de produto: "quais
    guias eu tenho em aberto" é a pergunta por trás de "preciso da segunda via"
    na maioria das vezes, e a lista já responde as duas.
    """
    texto = (mensagem or "").lower()
    tipo = next(
        (
            valor
            for valor, _rotulo in Guia.Tipo.choices
            if valor in texto or valor.upper() in (mensagem or "")
        ),
        None,
    )
    competencia = consultas.interpretar_competencia(mensagem)
    if tipo and competencia:
        return consultas.guia_do_mes(ctx.cliente, tipo, competencia)
    return consultas.guias_em_aberto(ctx.cliente)


@registrar_ferramenta(
    "status_obrigacoes",
    descricao="dizer quais declarações estão entregues, pendentes ou atrasadas",
    exemplos=("minhas obrigações estão em dia?", "falta entregar alguma declaração?"),
)
def status_obrigacoes(ctx, mensagem: str) -> str:
    return consultas.status_das_obrigacoes(ctx.cliente)


@registrar_ferramenta(
    "listar_certidoes",
    descricao="mostrar as certidões negativas e até quando valem",
    exemplos=("minhas certidões estão válidas?", "preciso da certidão negativa"),
)
def listar_certidoes(ctx, mensagem: str) -> str:
    return consultas.certidoes(ctx.cliente)


@registrar_ferramenta(
    "consultar_folha",
    descricao="informar o resumo da folha de pagamento de um mês",
    exemplos=("a folha desse mês fechou?", "quanto deu a folha de julho?"),
)
def consultar_folha(ctx, mensagem: str) -> str:
    competencia = consultas.interpretar_competencia(mensagem)
    if competencia is None:
        # Sem mês dito, a corrente é o palpite certo: quem pergunta "a folha
        # fechou?" está perguntando da folha que está fechando agora.
        competencia = consultas.competencia_atual()
    return consultas.folha_do_mes(ctx.cliente, competencia)
