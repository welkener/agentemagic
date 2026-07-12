"""
Adaptador REAL da NFS-e Nacional (Emissor Nacional/ADN) — Produção Restrita →
produção (Semana 3 do MVP). Mesma interface do mock (`nfse_mock.py`); troca
automática pelo resolver (`resolver.py`) quando o cliente está credenciado.

⚠ Auth ainda em spike (`magicbi-custodia-fiscal.md` §1): confirmar se a
outorga de procuração e-CAC/gov.br cobre a emissão via API é o item que muda
esta implementação. Por ora assume um identificador de sessão/token obtido no
credenciamento, guardado em `Credencial` (tipo=procuracao,
integracao="nfse_nacional"), enviado como Bearer. Se o spike apontar para
certificado em nuvem (PSC), troca-se só a resolução da credencial — o
contrato do adaptador não muda. `base_url` fica no Django admin
(`AplicativoIntegracao`): Produção Restrita e produção têm hosts diferentes.
"""
from __future__ import annotations

import httpx

from apps.core.resultado import ResultadoAcao
from apps.credentials.models import Credencial

from .base import AdapterBase
from .oauth2 import ErroIntegracaoNaoConfigurada, resolver_app, resolver_credencial

CAMPOS_OBRIGATORIOS_NFSE = (
    "cnpj_prestador",
    "cnae",
    "valor",
    "descricao_servico",
    "tomador",
)


def _cliente_de(ctx):
    return ctx["cliente"] if isinstance(ctx, dict) else getattr(ctx, "cliente", None)


class NfseNacionalAdapter(AdapterBase):
    """Adaptador real — mesmo contrato do `NfseMockAdapter`."""

    def capacidades(self) -> set[str]:
        return {"emitir_nfse", "consultar_nfse", "criar_rascunho_nfse"}

    def _credencial(self, cliente) -> Credencial:
        return resolver_credencial(cliente, "nfse_nacional")

    def consultar(self, recurso: str, filtros: dict, ctx) -> ResultadoAcao:
        if recurso != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        protocolo = filtros.get("protocolo")
        if not protocolo:
            return ResultadoAcao(ok=False, erro_padronizado="FILTRO_OBRIGATORIO_AUSENTE")

        try:
            app = resolver_app("nfse_nacional")
            credencial = self._credencial(_cliente_de(ctx))
        except ErroIntegracaoNaoConfigurada:
            return ResultadoAcao(ok=False, erro_padronizado="INTEGRACAO_NAO_CONFIGURADA")

        try:
            # ⚠ path de exemplo — confirmar na doc do ADN/Sefin em homologação.
            resposta = httpx.get(
                f"{app.base_url.rstrip('/')}/nfse/{protocolo}",
                headers={"Authorization": f"Bearer {credencial.valor}"},
                timeout=10.0,
            )
            resposta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return ResultadoAcao(ok=False, erro_padronizado="AUTH_EXPIRADA")
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
        except httpx.HTTPError:
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")

        return ResultadoAcao(ok=True, dados=resposta.json(), referencia_externa=protocolo)

    def criar_rascunho(self, recurso: str, dados: dict, ctx) -> ResultadoAcao:
        # NFS-e Nacional não tem rascunho server-side — só a validação local.
        if recurso != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        faltantes = [c for c in CAMPOS_OBRIGATORIOS_NFSE if not dados.get(c)]
        if faltantes:
            return ResultadoAcao(
                ok=False, erro_padronizado="CAMPO_OBRIGATORIO_AUSENTE", dados={"campos_faltantes": faltantes}
            )
        return ResultadoAcao(ok=True, dados={"dps": dados})

    def alterar(self, recurso: str, id_ext: str, mudancas: dict, ctx) -> ResultadoAcao:
        # NFS-e emitida não se altera — cancela e reemite (fora do escopo do MVP).
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")

    def emitir(self, documento: str, dados: dict, ctx) -> ResultadoAcao:
        if documento != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")

        # Guard determinístico local — nunca depende só da validação remota.
        if not dados.get("cnae"):
            return ResultadoAcao(
                ok=False,
                erro_padronizado="REJEITADA_CNAE_AUSENTE",
                dados={"mensagem_sefin": "Código CNAE não informado na DPS."},
            )
        faltantes = [c for c in CAMPOS_OBRIGATORIOS_NFSE if not dados.get(c)]
        if faltantes:
            return ResultadoAcao(
                ok=False, erro_padronizado="CAMPO_OBRIGATORIO_AUSENTE", dados={"campos_faltantes": faltantes}
            )

        try:
            app = resolver_app("nfse_nacional")
            credencial = self._credencial(_cliente_de(ctx))
        except ErroIntegracaoNaoConfigurada:
            return ResultadoAcao(ok=False, erro_padronizado="INTEGRACAO_NAO_CONFIGURADA")

        try:
            # ⚠ payload/path de exemplo — confirmar schema DPS oficial (campos
            # IBS/CBS incluídos) na doc do ADN em Produção Restrita.
            resposta = httpx.post(
                f"{app.base_url.rstrip('/')}/nfse",
                json={"dps": dados},
                headers={"Authorization": f"Bearer {credencial.valor}"},
                timeout=15.0,
            )
            resposta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return ResultadoAcao(ok=False, erro_padronizado="AUTH_EXPIRADA")
            if exc.response.status_code == 422:
                return ResultadoAcao(
                    ok=False, erro_padronizado="REJEITADA_SEFIN", dados={"mensagem_sefin": exc.response.text}
                )
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
        except httpx.HTTPError:
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")

        corpo = resposta.json()
        return ResultadoAcao(
            ok=True,
            dados={
                "protocolo": corpo.get("protocolo"),
                "situacao": corpo.get("situacao", "AUTORIZADA"),
                "danfse_url": corpo.get("danfse_url"),
                "valor": dados["valor"],
            },
            referencia_externa=corpo.get("protocolo"),
        )
