"""
As respostas da rotina contábil, na voz do cliente.

**A regra que governa este módulo inteiro:** quando não há registro, a resposta é
*"ainda não tenho"*. Nunca um valor calculado, estimado ou deduzido de
competência anterior.

Parece óbvio escrito assim, e não é na hora de programar: é tentador responder
"o DAS costuma ser R$ 71,60" quando o registro do mês ainda não chegou. Mas um
DAS inventado é pior que nenhum — o cliente paga errado, e paga confiando. O
mesmo vale para prazo de obrigação e validade de certidão: melhor dizer que não
sei do que dizer uma data que o cliente vai anotar na agenda.

A leitura de competência também é deliberadamente pobre — mês/ano explícito,
"esse mês" e "mês passado". "Julho" sem ano funciona só porque o ano corrente é
o palpite certo em quase todo pedido; qualquer coisa mais ambígua devolve `None`
e a conversa pergunta de qual mês se trata.
"""
from __future__ import annotations

import re
from datetime import date

from django.utils import timezone

from apps.rotina.models import Certidao, Folha, Guia, Obrigacao

_MESES = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "março": 3, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6,
    "julho": 7, "jul": 7, "agosto": 8, "ago": 8, "setembro": 9, "set": 9,
    "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
}

_RE_NUMERICA = re.compile(r"\b(0?[1-9]|1[0-2])\s*[/\-]\s*(\d{4}|\d{2})\b")


def competencia_atual(hoje: date | None = None) -> str:
    hoje = hoje or timezone.localdate()
    return f"{hoje.year:04d}-{hoje.month:02d}"


def _deslocar(hoje: date, meses: int) -> str:
    indice = hoje.year * 12 + (hoje.month - 1) + meses
    return f"{indice // 12:04d}-{indice % 12 + 1:02d}"


def interpretar_competencia(texto: str, hoje: date | None = None) -> str | None:
    """`AAAA-MM` dito na mensagem, ou None quando não está claro.

    Devolver None é um desfecho de primeira classe aqui: a conversa pergunta de
    qual mês se trata. Chutar a competência erra o mês inteiro da resposta — e
    "o DAS é R$ 71,60" com o mês errado é uma informação falsa, não imprecisa.
    """
    hoje = hoje or timezone.localdate()
    minusculo = (texto or "").lower()

    if "mês passado" in minusculo or "mes passado" in minusculo:
        return _deslocar(hoje, -1)
    if "esse mês" in minusculo or "este mês" in minusculo or "deste mês" in minusculo:
        return competencia_atual(hoje)

    achado = _RE_NUMERICA.search(minusculo)
    if achado:
        mes, ano = int(achado.group(1)), achado.group(2)
        completo = int(ano) if len(ano) == 4 else 2000 + int(ano)
        return f"{completo:04d}-{mes:02d}"

    for nome, numero in _MESES.items():
        if re.search(rf"\b{nome}\b", minusculo):
            # Ano ausente: o corrente. Se o mês citado ainda não chegou, o
            # pedido é quase certamente do ano passado — "me manda o DAS de
            # dezembro" em fevereiro não é sobre dezembro que vem.
            ano = hoje.year if numero <= hoje.month else hoje.year - 1
            return f"{ano:04d}-{numero:02d}"
    return None


def _rotulo(competencia: str) -> str:
    ano, mes = competencia.split("-")
    nomes = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    return f"{nomes[int(mes) - 1]}/{ano}"


def _linha_da_guia(guia: Guia) -> list[str]:
    linhas = [
        f"*{guia.get_tipo_display()}* — {_rotulo(guia.competencia)}",
        f"Valor: R$ {guia.valor:.2f}",
        f"Vencimento: {guia.vencimento.strftime('%d/%m/%Y')}",
    ]
    if guia.situacao == Guia.Situacao.PAGA:
        linhas.append("Situação: paga ✅")
    elif guia.vencida:
        linhas.append(f"⚠️ Venceu há {abs(guia.dias_para_vencer)} dia(s).")
    elif guia.dias_para_vencer <= 3:
        linhas.append(f"Vence em {guia.dias_para_vencer} dia(s).")
    if guia.codigo_barras:
        linhas.append(f"Linha digitável:\n{guia.codigo_barras}")
    if guia.url_documento:
        linhas.append(f"PDF: {guia.url_documento}")
    return linhas


def guia_do_mes(cliente, tipo: str, competencia: str) -> str:
    """Uma guia específica, ou o aviso honesto de que ela não chegou."""
    guia = Guia.objects.filter(
        cliente=cliente, tipo=tipo, competencia=competencia
    ).first()
    rotulo_tipo = dict(Guia.Tipo.choices)[tipo]

    if guia is None:
        # A frase importa: "ainda não tenho" diz que o sistema conhece a
        # pergunta e não tem a resposta. "Não encontrei" soaria como se o
        # cliente tivesse pedido algo inexistente.
        return (
            f"Ainda não tenho a {rotulo_tipo} de {_rotulo(competencia)} por aqui. 🗓️\n\n"
            "Assim que seu contador lançar, eu te aviso — ou você pode pedir de novo "
            "mais perto do vencimento."
        )
    return "\n".join(_linha_da_guia(guia))


def guias_em_aberto(cliente, limite: int = 5) -> str:
    """O que está aberto, do que vence primeiro para o que vence depois."""
    abertas = list(
        Guia.objects.filter(cliente=cliente, situacao=Guia.Situacao.ABERTA)
        .order_by("vencimento")[:limite]
    )
    if not abertas:
        return "Você não tem nenhuma guia em aberto aqui. 👍"

    linhas = ["Guias em aberto 🧾", ""]
    for guia in abertas:
        marca = " ⚠️ vencida" if guia.vencida else ""
        linhas.append(
            f"• {guia.get_tipo_display()} {_rotulo(guia.competencia)} — "
            f"R$ {guia.valor:.2f} — vence {guia.vencimento.strftime('%d/%m')}{marca}"
        )
    return "\n".join(linhas)


def status_das_obrigacoes(cliente) -> str:
    """As acessórias da competência corrente e da anterior.

    Duas competências porque é assim que o calendário funciona: em agosto o que
    está em jogo é a obrigação de julho (prazo neste mês) e a de agosto (que
    ainda vai fechar).
    """
    hoje = timezone.localdate()
    janela = [competencia_atual(hoje), _deslocar(hoje, -1)]
    obrigacoes = list(
        Obrigacao.objects.filter(cliente=cliente, competencia__in=janela).order_by("prazo")
    )
    if not obrigacoes:
        return (
            "Ainda não tenho o quadro de obrigações destes meses por aqui. 🗓️\n\n"
            "Seu contador acompanha os prazos — se quiser, eu abro um chamado para ele."
        )

    linhas = ["Suas obrigações 📋", ""]
    for obrigacao in obrigacoes:
        if obrigacao.situacao == Obrigacao.Situacao.ENVIADA:
            estado = "entregue ✅"
        elif obrigacao.situacao == Obrigacao.Situacao.DISPENSADA:
            estado = "dispensada"
        elif obrigacao.atrasada:
            estado = f"⚠️ ATRASADA (prazo era {obrigacao.prazo.strftime('%d/%m')})"
        else:
            estado = f"pendente — prazo {obrigacao.prazo.strftime('%d/%m')}"
        linhas.append(
            f"• {obrigacao.get_tipo_display()} {_rotulo(obrigacao.competencia)}: {estado}"
        )
        if obrigacao.pendente_com_o_cliente and obrigacao.situacao == Obrigacao.Situacao.PENDENTE:
            # A distinção que muda o comportamento do cliente: só aqui ele
            # precisa fazer alguma coisa.
            linhas.append("  ↳ *depende de você*: " + (obrigacao.observacao or "fale com seu contador"))
    return "\n".join(linhas)


def certidoes(cliente) -> str:
    """Situação das certidões, com a vencida em primeiro."""
    registros = list(Certidao.objects.filter(cliente=cliente).order_by("valida_ate"))
    if not registros:
        return (
            "Ainda não tenho certidões suas registradas por aqui. 📄\n\n"
            "Se precisar de alguma para banco ou licitação, peça ao seu contador."
        )

    linhas = ["Suas certidões 📄", ""]
    for certidao in registros:
        if certidao.vencida:
            estado = f"⚠️ vencida em {certidao.valida_ate.strftime('%d/%m/%Y')}"
        else:
            estado = (
                f"válida até {certidao.valida_ate.strftime('%d/%m/%Y')} "
                f"({certidao.dias_para_vencer} dia(s))"
            )
        linhas.append(f"• {certidao.get_tipo_display()}: {certidao.get_situacao_display()} — {estado}")
        if certidao.url_documento:
            linhas.append(f"  {certidao.url_documento}")
    return "\n".join(linhas)


def folha_do_mes(cliente, competencia: str) -> str:
    """Resumo da folha. Nunca salário individual — ver `rotina.models.Folha`."""
    folha = Folha.objects.filter(cliente=cliente, competencia=competencia).first()
    if folha is None:
        return (
            f"Ainda não tenho a folha de {_rotulo(competencia)} por aqui. 👥\n\n"
            "Assim que seu contador fechar, ela aparece."
        )
    if not folha.fechada:
        return (
            f"A folha de {_rotulo(competencia)} está em processamento — "
            f"ainda não foi fechada. 👥"
        )
    return "\n".join(
        [
            f"Folha de {_rotulo(competencia)} 👥",
            "",
            f"Funcionários: {folha.funcionarios}",
            f"Total bruto: R$ {folha.total_bruto:.2f}",
            f"Encargos: R$ {folha.total_encargos:.2f}",
            f"Fechada em {folha.fechada_em.strftime('%d/%m/%Y')}",
        ]
    )
