"""
Radar de teto do MEI — quanto do limite anual o cliente já consumiu.

É o D3 do white space do produto (`docs/magicbi-analise-disrupcao.md`): o MEI
que estoura o teto descobre tarde, quando o desenquadramento já é retroativo.
O contador, olhando a carteira inteira, precisa ver isso em julho, não em
fevereiro do ano seguinte.

Números conferidos em 27/jul/2026 (busca pública, não de memória):

- teto anual **R$ 81.000**, sem reajuste desde 2018 (LC 155/2016);
- tolerância de **20%** (até R$ 97.200): acima do teto e dentro dela, o
  desenquadramento vale a partir de **janeiro do ano seguinte**; acima dos 20%,
  é **retroativo** ao mês do excesso, com DAS complementar e juros;
- **teto proporcional** no ano de abertura: R$ 6.750 × meses ativos, contando o
  mês de abertura.

Há projetos em tramitação para elevar o teto (PLP 60/2025, PLP 67/2025), nenhum
sancionado. Por isso os valores ficam em `settings` (`TETO_MEI_ANUAL`,
`TETO_MEI_TOLERANCIA`) e não constantes cravadas no código: quando a lei mudar,
muda-se a configuração, sem release.

⚠ **Limite honesto desta conta**: ela só enxerga o que foi emitido *pelo Magic
BI*. Nota emitida por fora (prefeitura, outro sistema, o próprio contador) não
entra. Por isso o resultado carrega `parcial=True` — a tela precisa dizer que é
um piso, não o faturamento real do cliente. Apresentar isso como verdade
absoluta seria dar ao contador uma falsa sensação de segurança justamente no
indicador em que errar custa desenquadramento.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings

from apps.clients.models import Cliente

MESES_NO_ANO = 12


@dataclass(frozen=True)
class UsoDoTeto:
    """Situação do cliente frente ao limite anual do MEI."""

    aplicavel: bool  # False para quem não é MEI — ME/EPP tem outro limite
    faturamento: Decimal
    teto: Decimal
    proporcional: bool  # teto reduzido por abertura no ano corrente
    parcial: bool  # sempre True hoje: só contamos nota emitida por aqui

    @property
    def percentual(self) -> Decimal:
        if not self.teto:
            return Decimal("0")
        return (self.faturamento / self.teto * 100).quantize(Decimal("0.1"))

    @property
    def restante(self) -> Decimal:
        return max(self.teto - self.faturamento, Decimal("0.00"))

    @property
    def situacao(self) -> str:
        """`tranquilo` | `atencao` | `critico` | `estourado` | `estourado_grave`.

        Os cortes em 70% e 90% não vêm de lei — são de operação: 70% é quando
        ainda dá para planejar a migração para ME sem correria, 90% é quando o
        contador precisa ligar para o cliente nesta semana.
        """
        if self.faturamento > self.teto * (1 + self.tolerancia):
            return "estourado_grave"
        if self.faturamento > self.teto:
            return "estourado"
        pct = self.percentual
        if pct >= 90:
            return "critico"
        if pct >= 70:
            return "atencao"
        return "tranquilo"

    @property
    def tolerancia(self) -> Decimal:
        return Decimal(str(getattr(settings, "TETO_MEI_TOLERANCIA", "0.20")))


def teto_anual() -> Decimal:
    return Decimal(str(getattr(settings, "TETO_MEI_ANUAL", "81000.00")))


def teto_do_cliente(cliente: Cliente, ano: int) -> tuple[Decimal, bool]:
    """Teto do cliente no ano, já proporcionalizado se ele abriu durante ele.

    Devolve `(teto, proporcional)`. Sem `data_inicio_atividade` cadastrada,
    assume o ano inteiro — superestimar o teto erra para o lado de não acusar
    um estouro que existe, então a tela avisa que a data falta em vez de
    esconder a incerteza.
    """
    cheio = teto_anual()
    abertura = cliente.data_inicio_atividade
    if abertura is None or abertura.year != ano:
        return cheio, False

    meses_ativos = MESES_NO_ANO - abertura.month + 1  # inclui o mês de abertura
    mensal = cheio / MESES_NO_ANO
    return (mensal * meses_ativos).quantize(Decimal("0.01")), True


def avaliar(cliente: Cliente, faturamento: Decimal, ano: int | None = None) -> UsoDoTeto:
    """Monta o `UsoDoTeto` a partir do faturamento já apurado no ano.

    O faturamento vem de fora (a query agregada vive em `apps/painel/metricas.py`)
    para que esta função continue pura e testável com números na mão.
    """
    ano = ano or date.today().year
    e_mei = cliente.opcao_simples_nacional == Cliente.OpcaoSimplesNacional.MEI
    teto, proporcional = teto_do_cliente(cliente, ano)
    return UsoDoTeto(
        aplicavel=e_mei,
        faturamento=Decimal(faturamento or 0).quantize(Decimal("0.01")),
        teto=teto,
        proporcional=proporcional,
        parcial=True,
    )
