"""
Consulta pública de CNPJ — preenche o que a Receita já sabe.

Cadastrar cliente exigia sete campos digitados à mão, e errar qualquer um só
aparecia na primeira emissão. A Receita Federal publica boa parte deles; puxar
de lá elimina o trabalho **e** a chance de erro de digitação em CNPJ/município.

Fonte: **BrasilAPI** (`brasilapi.com.br/api/cnpj/v1/{cnpj}`) — pública, sem
autenticação, agregadora dos dados abertos da Receita. Formato confirmado com
chamada real em 26/jul/2026, não por leitura de doc.

O que ela resolve e o que NÃO resolve
-------------------------------------
Preenche: razão social, **código IBGE do município** (o `cLocEmi` da DPS, com os
7 dígitos certos), CNAE, enquadramento no Simples/MEI, e-mail e telefone.

**Não** preenche, e não há como derivar:

- **`cTribNac`** — é a classificação do *serviço* pela lista nacional (LC 116).
  A Receita publica o **CNAE**, que é classificação de *atividade econômica*.
  São taxonomias diferentes e o mapeamento entre elas não é 1:1 — deduzir uma da
  outra produziria nota com tributação errada.
- **Inscrição municipal** e **regras de ISS** (tributação, retenção, alíquota) —
  são municipais, não estão no cadastro federal.

Esses três continuam sendo decisão do contador, e o sistema diz isso em vez de
chutar. Sete campos manuais viram três — os três que exigem julgamento.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import structlog

logger = structlog.get_logger(__name__)

URL_BASE = "https://brasilapi.com.br/api/cnpj/v1"
TIMEOUT = 15.0


class ErroConsultaCnpj(Exception):
    """Consulta não pôde ser concluída (rede, CNPJ inexistente, formato)."""


@dataclass
class DadosReceita:
    cnpj: str
    razao_social: str
    nome_fantasia: str = ""
    codigo_municipio_ibge: str = ""
    municipio: str = ""
    uf: str = ""
    cnae_padrao: str = ""
    opcao_simples_nacional: int = 1
    email: str = ""
    telefone: str = ""
    situacao_cadastral: str = ""
    # Campos que a Receita não tem — ficam explícitos pra quem for cadastrar.
    pendentes_do_contador: list[str] = field(default_factory=list)

    @property
    def ativa(self) -> bool:
        return self.situacao_cadastral.upper() == "ATIVA"


def _digitos(valor) -> str:
    return "".join(c for c in str(valor or "") if c.isdigit())


def _formatar_cnae(bruto) -> str:
    """`6422100` → `6422-1/00`, que é o formato que o cadastro já usa."""
    d = _digitos(bruto).zfill(7)
    return f"{d[:4]}-{d[4]}/{d[5:7]}" if len(d) == 7 else ""


def _enquadramento(dados: dict) -> int:
    """`opcao_simples_nacional` da DPS (1 não optante, 2 MEI, 3 ME/EPP)."""
    from .models import Cliente

    if dados.get("opcao_pelo_mei"):
        return Cliente.OpcaoSimplesNacional.MEI
    if dados.get("opcao_pelo_simples"):
        return Cliente.OpcaoSimplesNacional.ME_EPP
    return Cliente.OpcaoSimplesNacional.NAO_OPTANTE


PENDENTES_SEMPRE = [
    "código de tributação nacional (cTribNac) — a Receita publica CNAE, "
    "que é outra classificação",
    "inscrição municipal — cadastro municipal, não federal",
    "regras de ISS (tributação, retenção, alíquota) — municipais",
]


def consultar_cnpj(cnpj: str, cliente_http=None) -> DadosReceita:
    """Consulta o CNPJ e devolve os dados normalizados.

    `cliente_http` permite injetar um cliente httpx nos testes — a fronteira
    externa fica explícita em vez de escondida atrás de monkeypatch global.
    """
    numero = _digitos(cnpj)
    if len(numero) != 14:
        raise ErroConsultaCnpj(f"CNPJ inválido: precisa de 14 dígitos (tem {len(numero)}).")

    http = cliente_http or httpx
    try:
        resposta = http.get(f"{URL_BASE}/{numero}", timeout=TIMEOUT)
        resposta.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ErroConsultaCnpj("CNPJ não encontrado na base da Receita.") from exc
        raise ErroConsultaCnpj(f"A consulta falhou (HTTP {exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        # Consulta indisponível NÃO pode virar cadastro em branco: quem chamou
        # decide se tenta de novo ou digita à mão.
        raise ErroConsultaCnpj(f"Não consegui falar com a consulta pública: {exc}") from exc

    try:
        dados = resposta.json()
    except ValueError as exc:
        raise ErroConsultaCnpj("Resposta da consulta veio em formato inesperado.") from exc

    ibge = _digitos(dados.get("codigo_municipio_ibge"))
    if len(ibge) != 7:
        # Sem IBGE não há DPS válida — melhor sinalizar do que gravar torto.
        logger.warning("consulta_cnpj_sem_ibge", cnpj=numero, recebido=dados.get("codigo_municipio_ibge"))
        ibge = ""

    return DadosReceita(
        cnpj=numero,
        razao_social=(dados.get("razao_social") or "").strip(),
        nome_fantasia=(dados.get("nome_fantasia") or "").strip(),
        codigo_municipio_ibge=ibge,
        municipio=(dados.get("municipio") or "").strip(),
        uf=(dados.get("uf") or "").strip(),
        cnae_padrao=_formatar_cnae(dados.get("cnae_fiscal")),
        opcao_simples_nacional=_enquadramento(dados),
        email=(dados.get("email") or "").strip(),
        telefone=_digitos(dados.get("ddd_telefone_1")),
        situacao_cadastral=(dados.get("descricao_situacao_cadastral") or "").strip(),
        pendentes_do_contador=list(PENDENTES_SEMPRE) + ([] if ibge else ["código IBGE do município"]),
    )
