"""
Retenções na fonte, e o gate de arquitetura do Sprint 3.

O que estes testes protegem, em ordem de importância:

1. **A regra que inverte o senso comum**: optante do Simples — e o MEI é
   Simples — **não sofre** retenção de IRRF nem de CSRF. Como quase toda a base
   do produto é MEI e ME, o desfecho certo na maioria esmagadora das notas é
   "não há retenção federal". Reter indevidamente de optante obriga o tomador a
   devolver.
2. **O módulo não adivinha hipótese.** Se ninguém declarou que o serviço é do
   art. 714 ou do art. 30, não há retenção — e a dispensa vem com o motivo
   escrito, porque "não retive" e "não sei se deveria" são respostas diferentes.
3. **`fiscal/` não importa `agents/`** — item explícito do gate. Um motor fiscal
   que depende do agente não roda sozinho, não é auditável em separado e
   arrasta o agente para dentro de qualquer teste de tributo.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from apps.fiscal import retencoes, teto_mei

FISCAL = Path(__file__).resolve().parent.parent / "apps/fiscal"


def apurar(**kwargs):
    kwargs.setdefault("valor", "1000.00")
    return retencoes.apurar(**kwargs)


def tributos(apuracao) -> set[str]:
    return {r.tributo for r in apuracao.retencoes}


def motivo_de(apuracao, tributo) -> str:
    return next(d.motivo for d in apuracao.dispensas if d.tributo == tributo)


# ---------------------------------------------------------------------------
# A regra que mais importa para esta carteira
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("regime", [retencoes.MEI, retencoes.ME_EPP])
def test_optante_do_simples_nao_sofre_irrf_nem_csrf(regime):
    """Mesmo com as duas hipóteses declaradas. É o Simples que afasta, não a
    ausência de hipótese — e por isso o teste declara as duas."""
    apuracao = apurar(
        regime=regime,
        hipoteses={retencoes.SERVICO_PROFISSIONAL, retencoes.SERVICO_CSRF},
    )

    assert "IRRF" not in tributos(apuracao)
    assert "PIS/COFINS/CSLL" not in tributos(apuracao)
    assert "Simples Nacional" in motivo_de(apuracao, "IRRF")


def test_a_dispensa_do_simples_lembra_da_declaracao_ao_tomador():
    """A isenção não é automática na prática: sem a declaração, o tomador retém.
    Omitir isso daria ao cliente uma segurança que ele não tem."""
    apuracao = apurar(regime=retencoes.MEI, hipoteses={retencoes.SERVICO_CSRF})
    assert "declaração ao tomador" in motivo_de(apuracao, "PIS/COFINS/CSLL")


def test_nao_optante_sofre_irrf_de_um_e_meio_por_cento():
    apuracao = apurar(
        regime=retencoes.NAO_OPTANTE, hipoteses={retencoes.SERVICO_PROFISSIONAL}
    )
    irrf = next(r for r in apuracao.retencoes if r.tributo == "IRRF")

    assert irrf.aliquota == Decimal("1.5")
    assert irrf.valor == Decimal("15.00")
    assert "714" in irrf.fundamento


def test_nao_optante_sofre_csrf_de_quatro_virgula_sessenta_e_cinco():
    apuracao = apurar(regime=retencoes.NAO_OPTANTE, hipoteses={retencoes.SERVICO_CSRF})
    csrf = next(r for r in apuracao.retencoes if r.tributo == "PIS/COFINS/CSLL")

    assert csrf.aliquota == Decimal("4.65")
    assert csrf.valor == Decimal("46.50")
    assert "10.833" in csrf.fundamento


# ---------------------------------------------------------------------------
# O módulo não adivinha
# ---------------------------------------------------------------------------
def test_sem_hipotese_declarada_nao_ha_retencao_nenhuma():
    """O padrão, e o desfecho certo na maioria das notas desta carteira."""
    apuracao = apurar(regime=retencoes.NAO_OPTANTE)

    assert apuracao.retencoes == []
    assert apuracao.valor_liquido == apuracao.valor_bruto


def test_toda_dispensa_vem_com_motivo_escrito():
    """"Não retive" e "não sei se deveria" são respostas diferentes, e só a
    primeira dá segurança a quem emite."""
    apuracao = apurar(regime=retencoes.NAO_OPTANTE)

    assert {d.tributo for d in apuracao.dispensas} >= {
        "ISS", "IRRF", "PIS/COFINS/CSLL", "INSS"
    }
    assert all(d.motivo.strip() for d in apuracao.dispensas)


def test_hipotese_escrita_errada_levanta_em_vez_de_virar_nao_incide():
    """Erro de digitação viraria silenciosamente "não retém" — o tipo de falha
    que só a fiscalização encontra."""
    with pytest.raises(ValueError, match="desconhecida"):
        apurar(regime=retencoes.NAO_OPTANTE, hipoteses={"servico_profisional"})


# ---------------------------------------------------------------------------
# ISS
# ---------------------------------------------------------------------------
def test_iss_retido_usa_a_aliquota_do_cadastro():
    apuracao = apurar(
        regime=retencoes.MEI, aliquota_iss="2.5", hipoteses={retencoes.ISS_RETIDO}
    )
    iss = next(r for r in apuracao.retencoes if r.tributo == "ISS")

    assert iss.valor == Decimal("25.00")


def test_iss_sem_aliquota_no_cadastro_nao_chuta():
    apuracao = apurar(regime=retencoes.MEI, hipoteses={retencoes.ISS_RETIDO})

    assert "ISS" not in tributos(apuracao)
    assert "não tem alíquota" in motivo_de(apuracao, "ISS")


def test_iss_nao_tem_a_dispensa_dos_dez_reais():
    """A dispensa de R$ 10 é das federais. Aplicá-la ao ISS municipal seria
    estender uma regra para onde ela não vale."""
    apuracao = apurar(
        valor="100.00",
        regime=retencoes.MEI,
        aliquota_iss="2",
        hipoteses={retencoes.ISS_RETIDO},
    )
    assert Decimal("2.00") == next(
        r.valor for r in apuracao.retencoes if r.tributo == "ISS"
    )


# ---------------------------------------------------------------------------
# INSS — onde o módulo se recusa a responder
# ---------------------------------------------------------------------------
def test_nao_optante_com_cessao_de_mao_de_obra_retem_onze_por_cento():
    apuracao = apurar(
        regime=retencoes.NAO_OPTANTE, hipoteses={retencoes.CESSAO_MAO_DE_OBRA}
    )
    inss = next(r for r in apuracao.retencoes if r.tributo == "INSS")

    assert inss.aliquota == Decimal("11")
    assert inss.valor == Decimal("110.00")
    assert "8.212" in inss.fundamento


def test_mei_com_cessao_nao_devolve_onze_por_cento_e_explica_por_que():
    """A recusa é o comportamento certo.

    Na contratação de MEI para as atividades do art. 18-B não há retenção de 11%
    do prestador — há contribuição de 3% do CONTRATANTE, que é outra obrigação,
    de outra pessoa. Devolver 11% ali seria um erro com cara de resposta.
    """
    apuracao = apurar(regime=retencoes.MEI, hipoteses={retencoes.CESSAO_MAO_DE_OBRA})

    assert "INSS" not in tributos(apuracao)
    motivo = motivo_de(apuracao, "INSS")
    assert "18-B" in motivo
    assert "3%" in motivo and "contratante" in motivo.lower()


# ---------------------------------------------------------------------------
# Aritmética
# ---------------------------------------------------------------------------
def test_dispensa_quando_o_valor_a_reter_nao_passa_de_dez_reais():
    """Regra que vale para IRRF e federais. Numa nota de R$ 600 o IRRF dá R$ 9,00
    — abaixo do mínimo, não se retém."""
    apuracao = apurar(
        valor="600.00",
        regime=retencoes.NAO_OPTANTE,
        hipoteses={retencoes.SERVICO_PROFISSIONAL},
    )

    assert "IRRF" not in tributos(apuracao)
    assert "igual ou inferior ao mínimo" in motivo_de(apuracao, "IRRF")


def test_arredonda_meio_para_cima_como_a_guia():
    """Truncar produziria centavos de diferença entre o que a nota diz e o que o
    DARF cobra."""
    apuracao = apurar(
        valor="1000.10",
        regime=retencoes.NAO_OPTANTE,
        hipoteses={retencoes.SERVICO_PROFISSIONAL},
    )
    # 1000,10 × 1,5% = 15,0015 → 15,00
    assert next(r.valor for r in apuracao.retencoes if r.tributo == "IRRF") == Decimal("15.00")


def test_liquido_desconta_a_soma_das_retencoes():
    apuracao = apurar(
        valor="10000.00",
        regime=retencoes.NAO_OPTANTE,
        aliquota_iss="5",
        hipoteses={
            retencoes.ISS_RETIDO,
            retencoes.SERVICO_PROFISSIONAL,
            retencoes.SERVICO_CSRF,
        },
    )

    # ISS 500 + IRRF 150 + CSRF 465 = 1.115
    assert apuracao.total_retido == Decimal("1115.00")
    assert apuracao.valor_liquido == Decimal("8885.00")


# ---------------------------------------------------------------------------
# O gate de arquitetura
# ---------------------------------------------------------------------------
def test_fiscal_nao_importa_agents():
    """Gate do Sprint 3.

    Motor fiscal que depende do agente não roda sozinho, não é auditável em
    separado e arrasta o agente para dentro de qualquer teste de tributo. A
    dependência tem que apontar no sentido contrário: o agente conhece o fiscal.
    """
    infratores = []
    for arquivo in FISCAL.rglob("*.py"):
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if "apps.agents" in linha and not linha.strip().startswith("#"):
                infratores.append(f"{arquivo.relative_to(FISCAL.parent)}:{numero}")
    assert not infratores, f"fiscal/ importando agents/: {infratores}"


def test_fiscal_nao_depende_de_nenhum_app_do_projeto():
    """Mais forte que o gate pedia, e conseguido no mesmo commit.

    O `teto_mei` importava `clients.models` só para ler uma constante e anotar um
    tipo. Sem esse import, `apps/fiscal` roda sem o CRM em volta — que é o que
    permite testá-lo com objetos de mentira e reaproveitá-lo depois.
    """
    infratores = []
    for arquivo in FISCAL.rglob("*.py"):
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            despida = linha.strip()
            if despida.startswith("#"):
                continue
            if despida.startswith(("from apps.", "import apps.")):
                infratores.append(f"{arquivo.relative_to(FISCAL.parent)}:{numero} → {despida}")
    assert not infratores, f"fiscal/ acoplado a outro app: {infratores}"


@pytest.mark.django_db
def test_a_constante_do_mei_no_fiscal_nao_pode_andar_sozinha():
    """A trava da constante repetida.

    Inverter a dependência custou duplicar o código do MEI. A duplicação é dívida
    conhecida — e esta asserção é o juro: se alguém mudar o valor de um lado, o
    teste fecha antes de a conta do teto sair errada para toda a carteira.
    """
    from apps.clients.models import Cliente

    assert teto_mei.OPCAO_MEI == Cliente.OpcaoSimplesNacional.MEI
    assert retencoes.MEI == Cliente.OpcaoSimplesNacional.MEI
    assert retencoes.ME_EPP == Cliente.OpcaoSimplesNacional.ME_EPP
    assert retencoes.NAO_OPTANTE == Cliente.OpcaoSimplesNacional.NAO_OPTANTE
