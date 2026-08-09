"""Filtros do Grimório — formatação que o template não deve improvisar."""
from django import template

from apps.painel import apresentacao

register = template.Library()


@register.filter
def moeda(valor):
    """`1240.5` → `R$ 1.240,50`.

    Existe porque as duas saídas prontas erram em pt-BR: `intcomma` do humanize
    lê a vírgula decimal como separador de grupo e devolve "31,647,50", e
    `USE_THOUSAND_SEPARATOR` sozinho não põe o símbolo.
    """
    return apresentacao.moeda(valor)


@register.filter
def cnpj(valor):
    """`44555666000154` → `44.555.666/0001-54`.

    O banco guarda só dígitos (é o que a Receita e a DPS usam); a máscara é
    assunto de tela. Contador lê CNPJ o dia inteiro e reconhece pelo formato —
    sem pontuação, ele precisa contar os dígitos.
    """
    digitos = "".join(c for c in str(valor or "") if c.isdigit())
    if len(digitos) != 14:
        return valor or "—"  # CPF, cadastro incompleto ou lixo: mostra como está
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


@register.filter
def ano_cru(valor):
    """Ano sem separador de milhar.

    `USE_THOUSAND_SEPARATOR = True` está ligado no projeto (valores em reais são
    ilegíveis sem ele) e localiza qualquer inteiro — inclusive anos, que viram
    "2.026". Converter para texto é o que impede isso.
    """
    return str(valor)
