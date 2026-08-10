"""
O contrato do Grimório no celular.

CSS não se testa em pytest, e o que quebra numa tela estreita raramente aparece
em asserção de conteúdo — as cinco armadilhas de 27/jul/2026 e a régua de meses
de 10/ago não quebraram teste nenhum, só apareceram na imagem. O que dá para
travar aqui é o **contrato entre template e folha de estilo**: se ele valer, o
CSS do celular funciona; se alguém o romper, o defeito aparece aqui e não no
telefone do contador no dia 8.

São duas regras, e as duas nasceram de defeito real:

1. **Toda célula de dado declara o rótulo da coluna** (`data-rotulo`). No celular
   a tabela vira cartão e o cabeçalho desaparece — sem o rótulo, "R$ 74.925,00"
   fica sozinho, sem dizer de quê. A exceção é a célula-título do cartão (a
   primeira da linha) e a da linha vazia (`colspan`).
2. **Nenhuma régua de rótulos volta a ser `style=` inline.** Foi assim que doze
   meses lado a lado num `flex` sem quebra fizeram a página inteira ficar mais
   larga que o aparelho: estilo inline não é alcançável por `@media`.
"""
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "apps/painel/templates/grimorio"

_RE_LINHA = re.compile(r"<tr\b", re.IGNORECASE)
_RE_CELULA = re.compile(r"<td\b[^>]*>", re.IGNORECASE)


def celulas_sem_rotulo(html: str) -> list[str]:
    """Células que precisariam de rótulo e não têm.

    Percorre linha a linha em vez de usar um parser: os templates têm tags do
    Django dentro dos atributos, e nenhum parser de HTML lida bem com isso. A
    contagem por `<tr>` é suficiente porque estes templates são escritos à mão e
    mantêm uma célula por linha de arquivo.
    """
    faltando = []
    posicao_na_linha = 0
    for linha in html.splitlines():
        if _RE_LINHA.search(linha):
            posicao_na_linha = 0
        for celula in _RE_CELULA.findall(linha):
            posicao_na_linha += 1
            primeira = posicao_na_linha == 1
            vazia = "colspan" in celula.lower()
            titulo = "titulo-cartao" in celula
            if primeira or vazia or titulo or "data-rotulo" in celula:
                continue
            faltando.append(f"{celula.strip()} (célula {posicao_na_linha})")
    return faltando


@pytest.mark.parametrize(
    "arquivo", sorted(p.name for p in TEMPLATES.glob("*.html"))
)
def test_toda_celula_de_dado_declara_o_rotulo_da_coluna(arquivo):
    html = (TEMPLATES / arquivo).read_text(encoding="utf-8")
    faltando = celulas_sem_rotulo(html)
    assert not faltando, (
        f"{arquivo}: célula sem `data-rotulo`. No celular a tabela vira cartão e "
        f"o cabeçalho some — sem o rótulo o valor aparece sozinho: {faltando}"
    )


@pytest.mark.parametrize(
    "arquivo", sorted(p.name for p in TEMPLATES.glob("*.html"))
)
def test_nenhuma_regua_de_rotulos_com_estilo_inline(arquivo):
    """`style="display:flex"` numa fileira de rótulos é inalcançável por `@media`.

    Regressão de 10/ago/2026: a régua de doze meses do gráfico estava assim, e
    era ela que fazia a página inteira exceder a largura do aparelho.
    """
    html = (TEMPLATES / arquivo).read_text(encoding="utf-8")
    # Só a fileira HORIZONTAL importa: `flex-direction: column` empilha e nunca
    # alarga a página. Flagrar coluna também seria ruído que ensina a ignorar
    # o teste.
    inline = [
        estilo
        for estilo in re.findall(r'style="[^"]*display\s*:\s*flex[^"]*"', html, re.IGNORECASE)
        if "column" not in estilo.lower()
    ]
    assert not inline, (
        f"{arquivo}: layout em `style=` inline não responde a `@media` — mova "
        f"para uma classe no grimorio.css: {inline}"
    )


def test_a_folha_de_estilo_tem_o_ponto_de_quebra_do_celular():
    """Guarda grossa e barata: se o bloco de celular sumir numa refatoração, as
    cinco telas voltam a ser desktop espremido, e nada mais acusaria."""
    css = (TEMPLATES.parent.parent / "static/grimorio/grimorio.css").read_text(
        encoding="utf-8"
    )
    assert "@media (max-width: 820px)" in css
    # A conversão de tabela em cartão depende destas duas: o cabeçalho some e o
    # rótulo passa a ser desenhado pelo `::before` a partir do atributo.
    assert "table.tabela thead { display: none; }" in css
    assert "content: attr(data-rotulo);" in css
