"""
Retenções na fonte sobre serviços — ISS, IRRF, INSS e CSRF (PIS/COFINS/CSLL).

**A decisão de projeto mais importante deste módulo é o que ele se recusa a
fazer.** Ele não decide *se* uma retenção incide. Isso depende da natureza do
serviço, de quem é o tomador e, no ISS, do município — é julgamento jurídico, e
um cálculo que o adivinha erra nas duas direções, ambas caras: mandar um MEI
reter o que não deve tira dinheiro do bolso dele hoje; deixar de reter o que
devia deixa o tomador responsável pelo principal e pelos acréscimos.

O que ele faz é o oposto: recebe as hipóteses **declaradas** por quem sabe e
calcula os valores, com o fundamento legal de cada um ao lado.

**A regra que mais importa para esta carteira** é a que quase inverte o senso
comum: prestador optante pelo Simples Nacional (e o MEI, que é Simples)
**não sofre retenção de IRRF nem de CSRF** — desde que apresente a declaração ao
tomador. Como quase toda a base do produto é MEI e ME do Simples, o desfecho
correto na maioria esmagadora dos casos é "não há retenção federal", e é ele que
sai por padrão. Reter indevidamente de optante obriga o tomador a devolver.

**Números conferidos em 10/ago/2026** (busca pública, não de memória):

| Tributo | Alíquota | Fundamento |
|---|---|---|
| IRRF (serviço profissional PJ→PJ) | 1,5% | art. 714 do RIR/2018 (Decreto 9.580/2018), DARF 1708 |
| CSRF (PIS 0,65 + COFINS 3,00 + CSLL 1,00) | 4,65% | art. 30 da Lei 10.833/2003 |
| INSS (cessão de mão de obra / empreitada) | 11% | art. 31 da Lei 8.212/1991 |
| ISS | do cadastro do prestador | LC 116/2003 + lei do município |

Dispensa de retenção quando o **valor a reter** for igual ou inferior a R$ 10,00
— vale para IRRF e para as contribuições federais.

As alíquotas ficam em `settings` pelo mesmo motivo do teto do MEI: são números
do mundo, e mudança de lei não deveria exigir release.

⚠ **O que este módulo deliberadamente NÃO calcula:** a contribuição do
contratante de MEI nas hipóteses do art. 18-B da LC 123/2006 (hidráulica,
elétrica, pintura, alvenaria, carpintaria e manutenção de veículos). Ali não há
retenção de 11% do prestador — há recolhimento de 3% pelo contratante, que é
outra obrigação, de outra pessoa. Devolver 11% naquele caso seria um erro com
cara de resposta.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

CENTAVO = Decimal("0.01")

# Hipóteses que quem chama DECLARA. Nenhuma é inferida aqui.
ISS_RETIDO = "iss_retido_pelo_tomador"
SERVICO_PROFISSIONAL = "servico_profissional"      # IRRF, art. 714 RIR/2018
CESSAO_MAO_DE_OBRA = "cessao_de_mao_de_obra"        # INSS, art. 31 Lei 8.212/91
SERVICO_CSRF = "servico_do_art_30"                  # CSRF, art. 30 Lei 10.833/03

HIPOTESES = frozenset({ISS_RETIDO, SERVICO_PROFISSIONAL, CESSAO_MAO_DE_OBRA, SERVICO_CSRF})

# Regimes, nos mesmos códigos de `clients.Cliente.OpcaoSimplesNacional`. A
# igualdade é conferida por teste — ver o comentário em `teto_mei.OPCAO_MEI`.
NAO_OPTANTE = 1
MEI = 2
ME_EPP = 3
DO_SIMPLES = frozenset({MEI, ME_EPP})


def _percentual(nome: str, padrao: str) -> Decimal:
    return Decimal(str(getattr(settings, nome, padrao)))


def _dispensa_minima() -> Decimal:
    return Decimal(str(getattr(settings, "RETENCAO_DISPENSA_ATE", "10.00")))


@dataclass(frozen=True)
class Retencao:
    """Um tributo retido, com a conta aberta e a norma ao lado.

    O `fundamento` não é decoração: quem confere a nota — o contador, o tomador,
    a fiscalização — pergunta "com base em quê", e a resposta precisa vir junto
    do número, não numa documentação separada.
    """

    tributo: str
    base: Decimal
    aliquota: Decimal
    valor: Decimal
    fundamento: str


@dataclass(frozen=True)
class Dispensa:
    """Uma retenção que NÃO incidiu, e o motivo.

    Existe porque "não retive" e "não sei se deveria" são respostas diferentes, e
    só a primeira dá segurança a quem emite. Um resultado que apenas omitisse o
    tributo deixaria as duas indistinguíveis.
    """

    tributo: str
    motivo: str


@dataclass(frozen=True)
class Apuracao:
    valor_bruto: Decimal
    retencoes: list[Retencao]
    dispensas: list[Dispensa]

    @property
    def total_retido(self) -> Decimal:
        return sum((r.valor for r in self.retencoes), Decimal("0.00"))

    @property
    def valor_liquido(self) -> Decimal:
        return self.valor_bruto - self.total_retido


def _arredondar(valor: Decimal) -> Decimal:
    # Meio para cima, que é a convenção usada nas guias — truncar produziria
    # centavos de diferença entre o que a nota diz e o que o DARF cobra.
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def apurar(
    *,
    valor: Decimal | float | str,
    regime: int,
    aliquota_iss: Decimal | float | str | None = None,
    hipoteses: frozenset[str] | set[str] = frozenset(),
) -> Apuracao:
    """Calcula as retenções a partir das hipóteses **declaradas**.

    `hipoteses` vazio — o padrão — significa "nenhuma retenção declarada", e o
    resultado é uma apuração sem retenção nenhuma. É o desfecho certo para a
    esmagadora maioria das notas desta carteira, e é de propósito que ele seja o
    que sai sem ninguém pedir nada.
    """
    desconhecidas = set(hipoteses) - HIPOTESES
    if desconhecidas:
        # Hipótese escrita errada viraria silenciosamente "não incide", que é o
        # tipo de falha que ninguém percebe até a fiscalização.
        raise ValueError(f"Hipótese de retenção desconhecida: {sorted(desconhecidas)}")

    bruto = _arredondar(Decimal(str(valor)))
    retencoes: list[Retencao] = []
    dispensas: list[Dispensa] = []
    minimo = _dispensa_minima()

    def somar(tributo: str, aliquota: Decimal, fundamento: str) -> None:
        devido = _arredondar(bruto * aliquota / 100)
        if devido <= minimo:
            dispensas.append(
                Dispensa(
                    tributo,
                    f"valor a reter (R$ {devido}) igual ou inferior ao mínimo de "
                    f"R$ {minimo} — retenção dispensada.",
                )
            )
            return
        retencoes.append(Retencao(tributo, bruto, aliquota, devido, fundamento))

    # --- ISS -----------------------------------------------------------
    if ISS_RETIDO in hipoteses:
        if not aliquota_iss:
            dispensas.append(
                Dispensa(
                    "ISS",
                    "retenção declarada, mas o cadastro não tem alíquota de ISS — "
                    "sem ela não dá para calcular, e chutar seria pior.",
                )
            )
        else:
            aliquota = Decimal(str(aliquota_iss))
            # O ISS não tem a dispensa dos R$ 10 (ela é das federais), então
            # entra direto, sem passar por `somar`.
            retencoes.append(
                Retencao(
                    "ISS",
                    bruto,
                    aliquota,
                    _arredondar(bruto * aliquota / 100),
                    "LC 116/2003 e lei do município do tomador",
                )
            )
    else:
        dispensas.append(Dispensa("ISS", "retenção pelo tomador não declarada."))

    # --- Federais: IRRF e CSRF -----------------------------------------
    # A regra que inverte o senso comum, e que vale para quase toda esta
    # carteira: optante do Simples (o MEI incluído) não sofre estas duas.
    if regime in DO_SIMPLES:
        motivo = (
            "prestador optante pelo Simples Nacional — não sofre retenção, desde "
            "que apresente a declaração ao tomador (art. 1º da IN RFB 765/2007 "
            "para o IRRF; art. 3º da IN SRF 459/2004 para as contribuições)."
        )
        dispensas.append(Dispensa("IRRF", motivo))
        dispensas.append(Dispensa("PIS/COFINS/CSLL", motivo))
    else:
        if SERVICO_PROFISSIONAL in hipoteses:
            somar(
                "IRRF",
                _percentual("RETENCAO_IRRF_PERCENTUAL", "1.5"),
                "art. 714 do RIR/2018 (Decreto 9.580/2018) — DARF 1708",
            )
        else:
            dispensas.append(
                Dispensa("IRRF", "serviço profissional do art. 714 não declarado.")
            )

        if SERVICO_CSRF in hipoteses:
            somar(
                "PIS/COFINS/CSLL",
                _percentual("RETENCAO_CSRF_PERCENTUAL", "4.65"),
                "art. 30 da Lei 10.833/2003 — PIS 0,65% + COFINS 3% + CSLL 1%",
            )
        else:
            dispensas.append(
                Dispensa(
                    "PIS/COFINS/CSLL", "serviço do art. 30 da Lei 10.833/2003 não declarado."
                )
            )

    # --- INSS ----------------------------------------------------------
    if CESSAO_MAO_DE_OBRA not in hipoteses:
        dispensas.append(
            Dispensa("INSS", "cessão de mão de obra ou empreitada não declarada.")
        )
    elif regime == MEI:
        # Aqui o módulo se recusa a responder, e a recusa é o comportamento
        # certo: na contratação de MEI para as atividades do art. 18-B não há
        # retenção de 11% do prestador — há recolhimento de 3% PELO CONTRATANTE,
        # que é outra obrigação, de outra pessoa. Devolver 11% seria um erro com
        # cara de resposta.
        dispensas.append(
            Dispensa(
                "INSS",
                "prestador é MEI: a contratação nas hipóteses do art. 18-B da "
                "LC 123/2006 gera contribuição de 3% do CONTRATANTE, não retenção "
                "de 11% do prestador. Confira o caso com o contador.",
            )
        )
    else:
        somar(
            "INSS",
            _percentual("RETENCAO_INSS_PERCENTUAL", "11"),
            "art. 31 da Lei 8.212/1991 — cessão de mão de obra ou empreitada",
        )

    return Apuracao(valor_bruto=bruto, retencoes=retencoes, dispensas=dispensas)
