"""
A chave de acesso da NF-e — 44 dígitos que se conferem sozinhos.

**Por que este módulo abre a fase de extração, e não o OCR.** A chave de acesso
não é um campo que se lê da nota: é um número que *carrega* a nota dentro dele.
Os 44 dígitos trazem a UF, o mês e o ano de emissão, o CNPJ de quem emitiu, o
modelo, a série e o número do documento — e o último dígito é um verificador em
módulo 11 sobre os outros 43.

Isso muda a natureza do problema. Um OCR que lê "R$ 1.240,00" pode ter lido
"R$ 1.240,00" ou "R$ 1.246,00", e não há como saber. Um OCR que lê a chave
errado **é descoberto pela aritmética**: erro de dígito e troca de posição
derrubam o verificador. O que sai daqui não é uma leitura provável, é uma
leitura conferida — e por isso pode seguir sem humano, enquanto o resto não pode.

**O CNPJ do emitente vem de graça, e ele decide entrada ou saída.** Se quem
emitiu é o próprio cliente, a nota é de saída (ele vendeu); se é outro, é de
entrada (ele comprou). Essa é uma pergunta contábil de verdade, e aqui ela se
responde por comparação de string, sem modelo nenhum opinando.

**O que este módulo NÃO faz:** dizer o valor da nota. Ele não está na chave. Quem
quiser o valor precisa do XML ou do texto do DANFE — e lá a conferência já não é
aritmética. Inventar valor a partir de chave seria exatamente o tipo de chute que
o gate do Sprint 4 existe para impedir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Código de UF do IBGE — os dois primeiros dígitos da chave.
UFS = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}

# Modelo do documento fiscal (posições 21-22).
MODELOS = {
    "55": "NF-e",
    "65": "NFC-e",
    "57": "CT-e",
    "58": "MDF-e",
    "59": "CF-e/SAT",
    "67": "CT-e OS",
}

# Uma chave pode aparecer no texto em bloco ou em grupos de quatro, com espaço
# ou ponto entre eles — os dois formatos são impressos no DANFE.
PADRAO_NO_TEXTO = re.compile(r"\b(?:\d[\s.]?){43}\d\b")


@dataclass(frozen=True)
class ChaveNFe:
    """Uma chave de acesso já conferida. Só se constrói se o dígito bater."""

    chave: str
    uf: str
    ano: int
    mes: int
    cnpj_emitente: str
    modelo: str
    serie: int
    numero: int

    @property
    def competencia(self) -> str:
        """`AAAA-MM` — o mesmo formato que `apps.rotina` usa."""
        return f"{self.ano:04d}-{self.mes:02d}"

    @property
    def modelo_legivel(self) -> str:
        return MODELOS.get(self.modelo, f"modelo {self.modelo}")

    def emitida_pelo_cliente(self, cliente) -> bool:
        """A nota saiu do próprio cliente (venda) ou veio de terceiro (compra)."""
        return re.sub(r"\D", "", cliente.cnpj or "") == self.cnpj_emitente

    def como_dados(self) -> dict:
        return {
            "chave_acesso": self.chave,
            "uf_emitente": self.uf,
            "competencia": self.competencia,
            "cnpj_emitente": self.cnpj_emitente,
            "modelo": self.modelo,
            "serie": self.serie,
            "numero": self.numero,
        }


def digito_verificador(base: str) -> int:
    """Módulo 11 com pesos 2..9 da direita para a esquerda, sobre 43 dígitos.

    Resto 0 ou 1 dá dígito 0 — é a regra do manual, e não um caso de borda
    esquecido: sem ela o dígito poderia dar 10 ou 11, que não cabem numa casa.
    """
    soma = 0
    peso = 2
    for digito in reversed(base):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def apenas_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def interpretar(bruto: str) -> ChaveNFe | None:
    """A chave conferida, ou `None`.

    `None` cobre tudo que não passa: tamanho errado, dígito que não bate, UF que
    não existe, mês fora de 1..12. Devolver um objeto "provavelmente certo" seria
    empurrar a dúvida para quem chama, que é onde ela deixa de ser tratada.
    """
    chave = apenas_digitos(bruto)
    if len(chave) != 44:
        return None
    if digito_verificador(chave[:43]) != int(chave[43]):
        return None

    uf = UFS.get(chave[0:2])
    if uf is None:
        return None

    ano, mes = 2000 + int(chave[2:4]), int(chave[4:6])
    if not 1 <= mes <= 12:
        return None

    return ChaveNFe(
        chave=chave,
        uf=uf,
        ano=ano,
        mes=mes,
        cnpj_emitente=chave[6:20],
        modelo=chave[20:22],
        serie=int(chave[22:25]),
        numero=int(chave[25:34]),
    )


def procurar_no_texto(texto: str) -> ChaveNFe | None:
    """A primeira chave válida de um texto de DANFE.

    Varre os candidatos e devolve o primeiro que **passa no verificador** — não o
    primeiro que tem 44 dígitos. Num DANFE há outros números longos por perto
    (código de barras do boleto anexo, protocolo de autorização), e é o dígito
    verificador que separa um do outro sem precisar entender o layout da página.
    """
    for candidato in PADRAO_NO_TEXTO.finditer(texto or ""):
        chave = interpretar(candidato.group())
        if chave is not None:
            return chave
    return None
