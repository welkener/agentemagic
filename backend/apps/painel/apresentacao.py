"""Tradução de estado de domínio para sinal visual.

Vive no `painel` (camada de apresentação), não em `apps/fiscal`: o teto do MEI
é regra fiscal e não deve saber que existe Tailwind. Aqui é o único lugar que
sabe as duas coisas.

Regra que o mapa abaixo garante: **nenhum estado é comunicado só por cor.**
Cada um traz ícone e rótulo escrito, porque em torno de 8% dos homens não
distingue verde de vermelho — e o indicador em questão é justamente aquele em
que ler errado custa desenquadramento retroativo.
"""

VISUAL_TETO = {
    "tranquilo": {
        "texto": "text-green-600 dark:text-green-400",
        "barra": "bg-green-600 dark:bg-green-500",
        "icone": "check_circle",
        "rotulo": "dentro do teto",
        "variante": "success",
    },
    "atencao": {
        "texto": "text-amber-600 dark:text-amber-400",
        "barra": "bg-amber-500",
        "icone": "warning",
        "rotulo": "atenção",
        "variante": "warning",
    },
    "critico": {
        "texto": "text-orange-600 dark:text-orange-400",
        "barra": "bg-orange-500",
        "icone": "priority_high",
        "rotulo": "quase no teto",
        "variante": "warning",
    },
    "estourado": {
        "texto": "text-red-600 dark:text-red-400",
        "barra": "bg-red-600 dark:bg-red-500",
        "icone": "error",
        "rotulo": "acima do teto",
        "variante": "danger",
    },
    "estourado_grave": {
        "texto": "text-red-700 dark:text-red-500",
        "barra": "bg-red-700 dark:bg-red-500",
        "icone": "dangerous",
        # Passar de 20% acima muda a consequência jurídica: o desenquadramento
        # deixa de valer a partir de janeiro e passa a ser retroativo. O rótulo
        # diz isso, porque "acima do teto" faria os dois casos parecerem iguais.
        "rotulo": "acima em +20% — desenquadramento retroativo",
        "variante": "danger",
    },
}


def visual_do_teto(uso) -> dict:
    return VISUAL_TETO[uso.situacao]


def moeda(valor) -> str:
    """`Decimal("1240.5")` → `"R$ 1.240,50"`.

    Escrito à mão porque as duas alternativas óbvias erram em pt-BR: o
    `intcomma` do humanize lê a vírgula decimal como separador de grupo e
    devolve "31,647,50", e `USE_THOUSAND_SEPARATOR` sozinho não põe o "R$".
    """
    if valor is None:
        return "—"
    inteiro = f"{float(valor):,.2f}"
    return "R$ " + inteiro.replace(",", "@").replace(".", ",").replace("@", ".")


def moeda_fina(valor, casas: int = 4) -> str:
    """Valor em reais com casas suficientes para não virar zero.

    O custo de IA por cliente/mês é da ordem de centavos, às vezes de frações
    deles. Formatado com duas casas, "R$ 0,00" seria a resposta para quase toda
    linha da tela de Operação — e um zero na coluna de custo lê como "de graça",
    que é a conclusão errada bem no número que sustenta o preço do produto.
    """
    if valor is None:
        return "—"
    inteiro = f"{float(valor):,.{casas}f}"
    return "R$ " + inteiro.replace(",", "@").replace(".", ",").replace("@", ".")


def sparkline(valores, largura: int = 560, altura: int = 150, margem: int = 12) -> dict:
    """Caminhos SVG de uma série — sem biblioteca de gráfico.

    Uma série só, sem interação e sem legenda: importar Chart.js para isto seria
    peso e uma dependência externa (o CSP do deploy e o disco do servidor
    agradecem). Devolve `linha`, `area` e os pontos já em coordenadas.

    Série toda zerada é o caso que quebra o desenho ingênuo: `max - min` daria
    zero e a divisão estouraria. Aqui ela vira uma reta na base, que é a leitura
    correta — "não houve nota nenhuma", e não "gráfico indisponível".
    """
    valores = [float(v or 0) for v in valores]
    if not valores:
        return {"linha": "", "area": "", "pontos": [], "vazio": True}

    topo = max(valores)
    util_x = largura - 2 * margem
    util_y = altura - 2 * margem
    passo = util_x / (len(valores) - 1) if len(valores) > 1 else 0

    pontos = []
    for i, valor in enumerate(valores):
        x = margem + i * passo
        # `topo or 1` cobre a série inteiramente zerada.
        y = altura - margem - (valor / (topo or 1)) * util_y
        pontos.append({"x": round(x, 1), "y": round(y, 1), "valor": valor})

    linha = "M " + " L ".join(f"{p['x']},{p['y']}" for p in pontos)
    area = (
        f"M {pontos[0]['x']},{altura - margem} "
        + " ".join(f"L {p['x']},{p['y']}" for p in pontos)
        + f" L {pontos[-1]['x']},{altura - margem} Z"
    )
    return {
        "linha": linha,
        "area": area,
        "pontos": pontos,
        "largura": largura,
        "altura": altura,
        "vazio": topo == 0,
    }


def resumo_da_intencao(intencao) -> str:
    """Uma linha que identifica a nota para quem vai decidir sobre ela.

    Tomador e valor, que é o que o contador precisa para reconhecer o caso sem
    abrir o registro. Payload incompleto é normal aqui (nota em coleta), então
    cada parte só entra se existir — resumo com "None" seria pior que resumo
    curto.
    """
    payload = intencao.payload or {}
    partes = []
    if payload.get("tomador"):
        partes.append(str(payload["tomador"]))
    if intencao.valor is not None:
        partes.append(moeda(intencao.valor))
    elif payload.get("valor") is not None:
        partes.append(moeda(payload["valor"]))
    if payload.get("descricao_servico"):
        partes.append(str(payload["descricao_servico"]))
    return " · ".join(partes) if partes else "sem dados preenchidos"
