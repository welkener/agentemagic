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
