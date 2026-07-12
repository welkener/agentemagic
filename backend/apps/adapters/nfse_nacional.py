"""
Adaptador REAL da NFS-e Nacional (Emissor Nacional/ADN) — Produção Restrita →
produção (Semana 3 do MVP). Mesma interface do mock (`nfse_mock.py`); troca
automática pelo resolver (`resolver.py`) quando o cliente está credenciado.

⚠⚠ NÃO ESTÁ PRONTO PARA PRODUÇÃO — pesquisado e confirmado em 12/jul/2026
(`magicbi-custodia-fiscal.md` §1, fonte: FENACON/gov.br), mas dois pontos
seguem exigindo trabalho antes de emitir de verdade:

1. **Auth é mTLS, não Bearer.** A API do ADN/Sefin exige certificado ICP-Brasil
   (A1/A3) do prestador na conexão TLS (autenticação mútua) — confirmado, não
   é mais hipótese. Procuração eletrônica NÃO cobre chamada de API (só o
   portal web). O caminho é certificado em nuvem (PSC — BirdID/Soluti/VIDaaS/
   SafeID, ainda não escolhido); a credencial resolvida aqui precisa virar um
   certificado cliente (ou uma chamada de assinatura remota ao PSC) em vez do
   header `Authorization` abaixo, que é só um placeholder de transporte.
2. **Payload é híbrido, não JSON puro.** A chamada REST é JSON, mas o
   documento fiscal em si (DPS/NFS-e) é **XML assinado digitalmente
   (XMLDSig), comprimido em GZip e codificado em Base64** dentro do corpo
   JSON — `dados` aqui precisaria virar XML conforme o XSD oficial antes do
   envio, não ser mandado como dict solto.

URLs reais confirmadas (`base_url` no Django admin, `AplicativoIntegracao`):
Produção Restrita `https://adn.producaorestrita.nfse.gov.br` /
`https://sefin.producaorestrita.nfse.gov.br`; Produção
`https://adn.nfse.gov.br` / `https://sefin.nfse.gov.br`. Doc oficial:
gov.br/nfse → Biblioteca → Documentação Técnica.
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
        return resolver_credencial(cliente, "nfse_nacional", tipo=Credencial.Tipo.CERTIFICADO)

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
            # ⚠⚠ placeholder de transporte — a API real exige mTLS (cert=)
            # com o certificado do PSC, não um Bearer token; e o path exato de
            # consulta por protocolo não está confirmado (ver docstring do módulo).
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
            # ⚠⚠ placeholder — o endpoint POST /nfse é confirmado, mas o corpo
            # real é {"dpsXmlGZipB64": "<XML da DPS assinado, gzip, base64>"},
            # não um dict solto; `dados` precisa virar XML (schema oficial,
            # inclui o grupo IBSCBS — NT SE/CGNFS-e 004/007) e ser assinado via
            # PSC antes de chegar aqui. Auth é mTLS (cert=), não Bearer.
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
