"""
Adaptador REAL da NFS-e Nacional (Emissor Nacional/ADN) — Produção Restrita →
produção (Semana 3 do MVP). Mesma interface do mock (`nfse_mock.py`); troca
automática pelo resolver (`resolver.py`) quando o cliente está credenciado.

Estado em 26/jul/2026 — o `emitir()` deixou de ser placeholder
------------------------------------------------------------
Os dois bloqueios históricos deste módulo estão resolvidos **para o modo
`.pfx` em custódia**:

1. **Auth é mTLS, não Bearer** — resolvido: `_preparar_dps` extrai o par PEM do
   `.pfx` e o `POST` usa `cert=`. Não há mais header `Authorization` na
   emissão. (`consultar`/`cancelar` ainda usam o Bearer placeholder — só a
   emissão foi migrada, ver §2 das ondas.)
2. **Payload é XML assinado, não JSON puro** — resolvido: `apps/fiscal/dps.py`
   monta a DPS pelo XSD oficial (`nfelib`), assina em XMLDSig com o
   certificado e empacota em `dpsXmlGZipB64`.

⚠ **O que ainda impede produção:** falta o cadastro na Produção Restrita
(`adn.producaorestrita.nfse.gov.br`) — sem ele nada disto foi exercido contra
a Sefin de verdade; o grupo IBS/CBS (NT SE/CGNFS-e 004/007) não é preenchido; e
o modo **PSC (assinatura remota)** continua bloqueado na pendência de custódia
(`magicbi-custodia-fiscal.md`) — nesse modo o `emitir()` recusa explicitamente,
em vez de mandar documento sem assinatura válida.

URLs reais confirmadas (`base_url` no Django admin, `AplicativoIntegracao`):
Produção Restrita `https://adn.producaorestrita.nfse.gov.br` /
`https://sefin.producaorestrita.nfse.gov.br`; Produção
`https://adn.nfse.gov.br` / `https://sefin.nfse.gov.br`. Doc oficial:
gov.br/nfse → Biblioteca → Documentação Técnica.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from apps.core.resultado import ResultadoAcao
from apps.credentials.models import Credencial
from apps.fiscal.dps import (
    ErroDpsIncompleta,
    conferir_cadastro,
    assinar,
    empacotar,
    extrair_pem_para_mtls,
    montar_dps,
    montar_id,
)
from apps.fiscal.numeracao import proximo_numero

from .base import AdapterBase
from .oauth2 import ErroIntegracaoNaoConfigurada, resolver_app, resolver_credencial


class ErroCertificadoIndisponivel(Exception):
    """Credencial existe, mas não permite assinar (modo PSC, ou .pfx incompleto)."""

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
        # Aceita CERTIFICADO_PSC ou CERTIFICADO_PFX — qual modo cada cliente
        # usa é decisão de credenciamento, não de código (ver
        # docs/magicbi-custodia-fiscal.md, decisão 25/jul/2026).
        return resolver_credencial(
            cliente,
            "nfse_nacional",
            tipo=(Credencial.Tipo.CERTIFICADO_PSC, Credencial.Tipo.CERTIFICADO_PFX),
        )

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

    def cancelar(self, documento: str, referencia: str, motivo: str, ctx) -> ResultadoAcao:
        """Cancelamento na NFS-e Nacional = **evento assinado**, não DELETE.

        ⚠⚠ Mesmo bloqueio do `emitir()`: o corpo real é um XML de evento
        (`e101101` — Cancelamento de NFS-e) assinado com o certificado
        ICP-Brasil e enviado gzip+base64, e a auth é mTLS (`cert=`), não o
        Bearer que está aqui como placeholder de transporte. Além disso o
        cancelamento tem **prazo legal** (varia por município/NT vigente):
        fora do prazo a Sefin recusa e o caminho correto é substituição.
        Nada disto pode ser inventado — fica marcado até a decisão de custódia
        (docs/magicbi-custodia-fiscal.md) fechar.
        """
        if documento != "nfse":
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")
        if not referencia:
            return ResultadoAcao(ok=False, erro_padronizado="FILTRO_OBRIGATORIO_AUSENTE")
        if not (motivo or "").strip():
            return ResultadoAcao(ok=False, erro_padronizado="REJEITADA_MOTIVO_AUSENTE")

        try:
            app = resolver_app("nfse_nacional")
            credencial = self._credencial(_cliente_de(ctx))
        except ErroIntegracaoNaoConfigurada:
            return ResultadoAcao(ok=False, erro_padronizado="INTEGRACAO_NAO_CONFIGURADA")

        try:
            resposta = httpx.post(
                f"{app.base_url.rstrip('/')}/nfse/{referencia}/eventos",
                json={"evento": "cancelamento", "motivo": motivo},
                headers={"Authorization": f"Bearer {credencial.valor}"},
                timeout=15.0,
            )
            resposta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return ResultadoAcao(ok=False, erro_padronizado="AUTH_EXPIRADA")
            if exc.response.status_code == 422:
                return ResultadoAcao(
                    ok=False,
                    erro_padronizado="REJEITADA_SEFIN",
                    dados={"mensagem_sefin": exc.response.text},
                )
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
        except httpx.HTTPError:
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")

        corpo = resposta.json()
        return ResultadoAcao(
            ok=True,
            dados={
                "protocolo_cancelamento": corpo.get("protocolo"),
                "situacao": corpo.get("situacao", "CANCELADA"),
                "nfse_cancelada": referencia,
            },
            referencia_externa=corpo.get("protocolo"),
        )

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

        # DPS de verdade: XML pelo XSD oficial, assinado com o certificado em
        # custódia, gzip+base64 — não mais `{"dps": {...}}`, que nunca poderia
        # funcionar. Ver apps/fiscal/dps.py.
        try:
            corpo, cert_pem, chave_pem = self._preparar_dps(_cliente_de(ctx), dados, credencial)
        except ErroDpsIncompleta as exc:
            return ResultadoAcao(
                ok=False,
                erro_padronizado="CADASTRO_FISCAL_INCOMPLETO",
                dados={"faltantes": exc.faltantes},
            )
        except ErroCertificadoIndisponivel as exc:
            return ResultadoAcao(
                ok=False, erro_padronizado="CERTIFICADO_INDISPONIVEL", dados={"motivo": str(exc)}
            )

        with tempfile.TemporaryDirectory() as pasta:
            # httpx exige o par mTLS em ARQUIVO. Vai pra um diretório temporário
            # que some ao sair do bloco — chave privada de cliente nunca fica
            # em disco além do tempo da chamada.
            caminho_cert = Path(pasta) / "cliente.pem"
            caminho_chave = Path(pasta) / "cliente.key"
            caminho_cert.write_bytes(cert_pem)
            caminho_chave.write_bytes(chave_pem)

            try:
                resposta = httpx.post(
                    f"{app.base_url.rstrip('/')}/nfse",
                    json={"dpsXmlGZipB64": corpo},
                    # Auth do ADN/Sefin é mTLS, não Bearer — o certificado É a
                    # credencial. Não há header de autorização aqui.
                    cert=(str(caminho_cert), str(caminho_chave)),
                    timeout=15.0,
                )
                resposta.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    return ResultadoAcao(ok=False, erro_padronizado="AUTH_EXPIRADA")
                if exc.response.status_code == 422:
                    return ResultadoAcao(
                        ok=False,
                        erro_padronizado="REJEITADA_SEFIN",
                        dados={"mensagem_sefin": exc.response.text},
                    )
                return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
            except httpx.HTTPError:
                return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")

        retorno = resposta.json()
        return ResultadoAcao(
            ok=True,
            dados={
                "protocolo": retorno.get("protocolo"),
                "situacao": retorno.get("situacao", "AUTORIZADA"),
                "danfse_url": retorno.get("danfse_url"),
                "valor": dados["valor"],
            },
            referencia_externa=retorno.get("protocolo"),
        )

    # ------------------------------------------------------------------
    def _preparar_dps(self, cliente, dados: dict, credencial: Credencial):
        """Monta + assina + empacota a DPS e devolve `(corpo, cert_pem, chave_pem)`.

        Só o modo `.pfx` fecha o ciclo hoje: com a chave privada em custódia a
        assinatura é local e o mTLS sai daqui mesmo. O modo PSC (assinatura
        remota) segue bloqueado na pendência técnica registrada em
        `docs/magicbi-custodia-fiscal.md` — e falha explicitamente, em vez de
        emitir algo sem assinatura válida.
        """
        # Ordem importa por dois motivos. (1) O cadastro é conferido primeiro
        # porque não depende de certificado — dá o diagnóstico mais útil mesmo
        # a quem ainda não subiu o .pfx. (2) Nenhuma validação pode acontecer
        # DEPOIS de `proximo_numero`: número de DPS reservado não volta, e
        # queimar sequência fiscal por erro de cadastro vira pergunta do fisco
        # sobre nota faltando.
        faltantes = conferir_cadastro(cliente)
        if faltantes:
            raise ErroDpsIncompleta(faltantes)

        if credencial.tipo != Credencial.Tipo.CERTIFICADO_PFX:
            raise ErroCertificadoIndisponivel(
                "Emissão real hoje só com certificado .pfx em custódia — "
                "assinatura remota via PSC ainda não está resolvida."
            )
        pfx = credencial.pfx_bytes
        senha = credencial.pfx_senha
        if not pfx or not senha:
            raise ErroCertificadoIndisponivel("Credencial sem arquivo .pfx ou sem senha.")

        numero = proximo_numero(cliente)
        xml = montar_dps(cliente, dados, numero=numero)
        id_dps = montar_id(cliente, (cliente.serie_dps or "1").strip(), numero)
        assinado = assinar(xml, pfx, senha, id_dps)
        cert_pem, chave_pem = extrair_pem_para_mtls(pfx, senha)
        return empacotar(assinado), cert_pem, chave_pem
