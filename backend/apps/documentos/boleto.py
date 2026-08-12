"""
A linha digitável do boleto — 47 dígitos que também se conferem sozinhos.

Mesma ideia da chave de acesso (`chave_nfe.py`), aplicada ao outro documento que
mais chega pela conversa. A linha digitável carrega **banco, vencimento e valor**
e traz quatro dígitos verificadores: um por campo, em módulo 10, mais o
verificador geral do código de barras, em módulo 11. Ler errado não passa
despercebido — a conta não fecha.

Isso importa porque valor e vencimento de boleto são exatamente os dois campos
onde errar custa dinheiro de verdade: pagar a menos gera juros, pagar tarde gera
multa, e nos dois casos quem explica é o contador. Um OCR que lê "R$ 1.240,00"
sem verificador nenhum não deveria alimentar um lançamento; estes 47 dígitos,
sim.

**O fator de vencimento tem uma pegadinha de calendário.** Ele conta dias desde
07/10/1997 e só tem quatro casas, então esgotou em 21/02/2025, quando a FEBRABAN
mandou voltar a 1000. Hoje convivem boletos das duas eras, e o mesmo fator pode
significar duas datas — a diferença é de mais de vinte anos. Aqui as duas são
calculadas e só sobra a que cai numa janela plausível; se as duas caírem, ou
nenhuma, o vencimento volta como `None`. Boleto com data errada é pior que
boleto sem data: um o contador confere, o outro ele acredita.

**Boleto de arrecadação (concessionária, tributo) não é lido aqui.** Ele tem 48
dígitos, layout próprio e às vezes valor zerado. Aplicar a regra do boleto
bancário nele produziria número — e número errado. Ele é reconhecido e
devolvido como "não sei ler", que é uma resposta melhor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# As duas eras do fator de vencimento. A segunda existe porque a primeira
# esgotou as quatro casas em 21/02/2025 (fator 9999).
BASE_PRIMEIRA_ERA = date(1997, 10, 7)
BASE_SEGUNDA_ERA = date(2025, 2, 22)  # aqui o fator voltou a 1000
FATOR_REINICIO = 1000

# Janela de plausibilidade para desempatar as duas eras. Larga o bastante para
# boleto vencido há tempo e para carnê de longo prazo; estreita o bastante para
# que a era errada caia fora por vinte anos de diferença.
ANOS_PARA_TRAS = 6
ANOS_PARA_FRENTE = 4

PADRAO_NO_TEXTO = re.compile(r"\b\d[\d\s.]{45,60}\d\b")


class BoletoNaoBancario(ValueError):
    """Arrecadação/concessionária: 48 dígitos, outra regra. Não chutamos."""


@dataclass(frozen=True)
class Boleto:
    """Uma linha digitável já conferida nos quatro dígitos verificadores."""

    linha: str
    banco: str
    valor: Decimal | None
    vencimento: date | None

    @property
    def sem_valor_impresso(self) -> bool:
        """Boleto em branco no valor — comum em cobrança com valor a combinar."""
        return self.valor is None or self.valor == 0

    def como_dados(self) -> dict:
        return {
            "linha_digitavel": self.linha,
            "banco": self.banco,
            "valor": str(self.valor) if self.valor is not None else None,
            "vencimento": self.vencimento.isoformat() if self.vencimento else None,
        }


def apenas_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _mod10(campo: str) -> int:
    """Pesos 2 e 1 alternados da direita para a esquerda; produto > 9 soma os algarismos."""
    soma = 0
    peso = 2
    for digito in reversed(campo):
        produto = int(digito) * peso
        soma += produto - 9 if produto > 9 else produto
        peso = 1 if peso == 2 else 2
    return (10 - soma % 10) % 10


def _mod11_barra(base: str) -> int:
    """Verificador geral do código de barras: 0, 10 e 11 viram 1 (regra FEBRABAN)."""
    soma = 0
    peso = 2
    for digito in reversed(base):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = 11 - soma % 11
    return 1 if resto in (0, 10, 11) else resto


def data_do_fator(fator: int, hoje: date | None = None) -> date | None:
    """A data de vencimento, se ela for determinável sem chutar a era.

    Fator zero é o combinado para "sem vencimento" e volta `None` — não é falha.
    """
    if fator <= 0:
        return None

    hoje = hoje or date.today()
    inicio = date(hoje.year - ANOS_PARA_TRAS, hoje.month, 1)
    fim = date(hoje.year + ANOS_PARA_FRENTE, hoje.month, 1)

    candidatas = [BASE_PRIMEIRA_ERA + timedelta(days=fator)]
    if fator >= FATOR_REINICIO:
        candidatas.append(BASE_SEGUNDA_ERA + timedelta(days=fator - FATOR_REINICIO))

    plausiveis = [d for d in candidatas if inicio <= d <= fim]
    # Duas plausíveis é empate genuíno — a diferença entre as eras é de anos, e
    # se as duas couberem na janela não há como decidir sem inventar critério.
    return plausiveis[0] if len(plausiveis) == 1 else None


def interpretar(bruto: str, hoje: date | None = None) -> Boleto | None:
    """O boleto conferido, ou `None` se qualquer verificador falhar."""
    linha = apenas_digitos(bruto)

    if len(linha) == 48 and linha.startswith("8"):
        raise BoletoNaoBancario("boleto de arrecadação — layout diferente")
    if len(linha) != 47:
        return None

    if _mod10(linha[0:9]) != int(linha[9]):
        return None
    if _mod10(linha[10:20]) != int(linha[20]):
        return None
    if _mod10(linha[21:31]) != int(linha[31]):
        return None

    # O código de barras é remontado porque o verificador geral é calculado
    # sobre ELE, não sobre a linha digitável: os campos estão em outra ordem.
    campo_livre = linha[4:9] + linha[10:20] + linha[21:31]
    barra_sem_dv = linha[0:4] + linha[33:47] + campo_livre
    if _mod11_barra(barra_sem_dv) != int(linha[32]):
        return None

    centavos = int(linha[37:47])
    return Boleto(
        linha=linha,
        banco=linha[0:3],
        # `scaleb` e não divisão: dividir por 100 devolve `Decimal('1240')` para
        # valor redondo, e esse é o número que vira string no JSON do documento.
        # "1240" ao lado de "1240.00" na mesma coluna é o tipo de inconsistência
        # que ninguém nota até alguém somar as duas com ferramentas diferentes.
        valor=Decimal(centavos).scaleb(-2) if centavos else None,
        vencimento=data_do_fator(int(linha[33:37]), hoje=hoje),
    )


def interpretar_barra(bruto: str, hoje: date | None = None) -> Boleto | None:
    """Os 44 dígitos do código de barras — que **não** são a linha digitável.

    São os mesmos dados em outra ordem, e é por isso que existem os dois: a linha
    digitável foi desenhada para ser lida por gente (campos curtos, um dígito
    verificador por campo, para o caixa perceber o erro de digitação na hora), e
    o código de barras para ser lido por máquina (um verificador só, sobre tudo).

    A leitora óptica devolve esta forma. Convertê-la para a linha digitável antes
    de conferir seria dar uma volta para chegar ao mesmo lugar — o verificador
    geral é calculado exatamente sobre estes 44 dígitos.

    Layout: banco(3) moeda(1) **DV(1)** fator(4) valor(10) campo livre(25).
    """
    barra = apenas_digitos(bruto)
    if len(barra) != 44:
        return None

    if _mod11_barra(barra[0:4] + barra[5:]) != int(barra[4]):
        return None

    centavos = int(barra[9:19])
    return Boleto(
        linha=_linha_da_barra(barra),
        banco=barra[0:3],
        valor=Decimal(centavos).scaleb(-2) if centavos else None,
        vencimento=data_do_fator(int(barra[5:9]), hoje=hoje),
    )


def _linha_da_barra(barra: str) -> str:
    """Remonta a linha digitável a partir do código de barras.

    Não é conferência — a conferência já aconteceu no verificador geral. É para
    que o documento guarde sempre o mesmo formato, venha de onde vier: o mesmo
    boleto lido do papel e lido do código de barras precisa produzir a mesma
    string, senão o reconhecimento de duplicata deixa de funcionar justamente
    quando o cliente manda a foto e o PDF do mesmo título.
    """
    livre = barra[19:44]
    campos = [barra[0:4] + livre[0:5], livre[5:15], livre[15:25]]
    return "".join(c + str(_mod10(c)) for c in campos) + barra[4] + barra[5:19]


def procurar_no_texto(texto: str, hoje: date | None = None) -> Boleto | None:
    """A primeira linha digitável válida do texto.

    Como na chave de acesso, quem decide é o verificador e não o formato: a
    linha aparece impressa com pontos e espaços em posições que variam por banco,
    então o candidato é solto e a aritmética filtra.
    """
    for candidato in PADRAO_NO_TEXTO.finditer(texto or ""):
        digitos = apenas_digitos(candidato.group())
        # A janela pode ter capturado dígitos vizinhos; testa cada recorte de 47.
        for inicio in range(0, len(digitos) - 46):
            try:
                boleto = interpretar(digitos[inicio:inicio + 47], hoje=hoje)
            except BoletoNaoBancario:
                return None
            if boleto is not None:
                return boleto
    return None
