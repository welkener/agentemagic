"""Onboarding: cadastrar cliente puxando o que a Receita já publica.

A regra que estes testes protegem: **preencher o que é público, nunca deduzir o
que é julgamento fiscal.** O `cTribNac` (lista de serviços LC 116) não sai do
CNAE (atividade econômica) — deduzir um do outro daria nota com tributação
errada, que é pior que nota que não sai.

O payload usado é a resposta **real** da BrasilAPI (conferida com chamada de
verdade em 26/jul/2026), não um exemplo inventado.
"""
import httpx
import pytest

from apps.clients.models import Cliente
from apps.clients.receita import ErroConsultaCnpj, consultar_cnpj
from apps.fiscal.dps import conferir_cadastro

# Resposta real de GET /api/cnpj/v1/00000000000191 (campos que usamos).
RESPOSTA_REAL = {
    "razao_social": "BANCO DO BRASIL SA",
    "nome_fantasia": "DIRECAO GERAL",
    "municipio": "BRASILIA",
    "codigo_municipio": 9701,
    "codigo_municipio_ibge": 5300108,
    "uf": "DF",
    "cnae_fiscal": 6422100,
    "cnae_fiscal_descricao": "Bancos múltiplos, com carteira comercial",
    "descricao_situacao_cadastral": "ATIVA",
    "opcao_pelo_simples": False,
    "opcao_pelo_mei": False,
    "ddd_telefone_1": "6134939002",
    "email": None,
}


class _RespostaFalsa:
    def __init__(self, dados, status=200):
        self._dados = dados
        self.status_code = status

    def json(self):
        return self._dados

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erro", request=None, response=self)


class _HttpFalso:
    """Dublê do httpx — a fronteira externa é injetada, não monkeypatchada."""

    def __init__(self, resposta=None, erro=None):
        self._resposta = resposta
        self._erro = erro
        self.chamadas = []

    def get(self, url, **kwargs):
        self.chamadas.append(url)
        if self._erro:
            raise self._erro
        return self._resposta


# ---------------------------------------------------------------------------
# O que a consulta preenche
# ---------------------------------------------------------------------------
def test_consulta_traz_o_codigo_ibge_com_sete_digitos():
    """É o `cLocEmi` da DPS — o campo que mais dava erro de digitação."""
    dados = consultar_cnpj("00.000.000/0001-91", cliente_http=_HttpFalso(_RespostaFalsa(RESPOSTA_REAL)))

    assert dados.codigo_municipio_ibge == "5300108"
    assert len(dados.codigo_municipio_ibge) == 7
    assert dados.razao_social == "BANCO DO BRASIL SA"
    assert dados.ativa is True


def test_cnae_vem_formatado_como_o_cadastro_espera():
    """`6422100` (inteiro) → `6422-1/00`."""
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(RESPOSTA_REAL)))
    assert dados.cnae_padrao == "6422-1/00"


@pytest.mark.parametrize(
    "mei,simples,esperado",
    [(True, True, 2), (False, True, 3), (False, False, 1)],
)
def test_enquadramento_no_simples_e_mapeado(mei, simples, esperado):
    payload = {**RESPOSTA_REAL, "opcao_pelo_mei": mei, "opcao_pelo_simples": simples}
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(payload)))
    assert dados.opcao_simples_nacional == esperado


def test_pontuacao_do_cnpj_e_limpa_antes_da_chamada():
    http = _HttpFalso(_RespostaFalsa(RESPOSTA_REAL))
    consultar_cnpj("00.000.000/0001-91", cliente_http=http)
    assert http.chamadas[0].endswith("/00000000000191")


# ---------------------------------------------------------------------------
# O que a consulta NÃO preenche — e admite não preencher
# ---------------------------------------------------------------------------
def test_consulta_declara_o_que_fica_com_o_contador():
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(RESPOSTA_REAL)))
    pendentes = " ".join(dados.pendentes_do_contador)

    assert "cTribNac" in pendentes
    assert "inscrição municipal" in pendentes.lower()
    assert "ISS" in pendentes


def test_cnae_nao_vira_codigo_de_tributacao():
    """O erro que arruinaria a nota: usar o CNAE como cTribNac.

    A consulta traz CNAE e **não** traz cTribNac. Se algum dia alguém tentar
    derivar um do outro, este teste é o lembrete de que são taxonomias
    diferentes e o mapeamento não é 1:1.
    """
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(RESPOSTA_REAL)))
    assert not hasattr(dados, "codigo_tributacao_nacional")
    assert dados.cnae_padrao  # tem CNAE...
    assert any("cTribNac" in p for p in dados.pendentes_do_contador)  # ...e admite que falta o outro


# ---------------------------------------------------------------------------
# Degradação — consulta fora do ar nunca vira cadastro em branco
# ---------------------------------------------------------------------------
def test_consulta_fora_do_ar_levanta_erro_em_vez_de_devolver_vazio():
    http = _HttpFalso(erro=httpx.ConnectError("sem rede"))
    with pytest.raises(ErroConsultaCnpj, match="consulta pública"):
        consultar_cnpj("00000000000191", cliente_http=http)


def test_cnpj_com_tamanho_errado_nem_chega_na_rede():
    http = _HttpFalso(_RespostaFalsa(RESPOSTA_REAL))
    with pytest.raises(ErroConsultaCnpj, match="14 dígitos"):
        consultar_cnpj("123", cliente_http=http)
    assert http.chamadas == []


def test_ibge_ausente_na_resposta_nao_grava_valor_torto():
    payload = {**RESPOSTA_REAL, "codigo_municipio_ibge": None}
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(payload)))
    assert dados.codigo_municipio_ibge == ""
    assert "código IBGE do município" in dados.pendentes_do_contador


def test_empresa_baixada_e_sinalizada():
    payload = {**RESPOSTA_REAL, "descricao_situacao_cadastral": "BAIXADA"}
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(payload)))
    assert dados.ativa is False


# ---------------------------------------------------------------------------
# Efeito no cadastro: de sete campos manuais para três
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_apos_a_consulta_so_faltam_os_campos_de_julgamento(escritorio):
    dados = consultar_cnpj("00000000000191", cliente_http=_HttpFalso(_RespostaFalsa(RESPOSTA_REAL)))

    cliente = Cliente.objects.create(
        escritorio=escritorio,
        cnpj=dados.cnpj,
        nome=dados.razao_social,
        telefone_whatsapp="5511900001111",
        codigo_municipio_ibge=dados.codigo_municipio_ibge,
        cnae_padrao=dados.cnae_padrao,
        opcao_simples_nacional=dados.opcao_simples_nacional,
    )

    faltantes = conferir_cadastro(cliente)
    # O IBGE (o mais chato de digitar certo) já veio; sobra o julgamento fiscal.
    assert not any("IBGE" in f for f in faltantes)
    assert any("tributação nacional" in f for f in faltantes)

    cliente.codigo_tributacao_nacional = "010101"
    assert conferir_cadastro(cliente) == []
