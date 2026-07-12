"""
Base OAuth2 para adaptadores reais de ERP (Conta Azul, Bling — seção 8.2 dos
requisitos: Authorization Code por cliente, refresh token no cofre, renovação
automática). Erros externos viram o catálogo interno (seção 7.3): AUTH_EXPIRADA,
RATE_LIMIT, INDISPONIVEL — o núcleo trata igual, venha de qual ERP for.

`valor` da Credencial guarda o refresh_token (persistente); o access_token é
obtido a cada chamada e nunca persistido — menos segredo em repouso.
"""
from __future__ import annotations

import httpx

from apps.core.resultado import ResultadoAcao
from apps.credentials.models import AplicativoIntegracao, Credencial

from .base import AdapterBase


class ErroIntegracaoNaoConfigurada(Exception):
    """App ou credencial do cliente ausente — adaptador real não pode operar."""


def resolver_app(nome: str) -> AplicativoIntegracao:
    app = AplicativoIntegracao.objects.filter(nome=nome, ativo=True).first()
    if app is None:
        raise ErroIntegracaoNaoConfigurada(
            f"AplicativoIntegracao '{nome}' não está cadastrado/ativo no Django admin."
        )
    return app


def resolver_credencial(cliente, integracao: str) -> Credencial:
    credencial = (
        Credencial.objects.filter(cliente=cliente, integracao=integracao, tipo=Credencial.Tipo.OAUTH)
        .order_by("-atualizado_em")
        .first()
    )
    if credencial is None:
        raise ErroIntegracaoNaoConfigurada(
            f"Cliente {cliente} não tem credencial OAuth de '{integracao}' cadastrada no admin."
        )
    return credencial


class AdapterErpOAuth2Base(AdapterBase):
    """Esqueleto comum a adaptadores ERP com OAuth2 authorization-code.

    Subclasses definem `nome_integracao` e `mapa_endpoints` (recurso → path).
    Os paths exatos (⚠ verificar na doc oficial de cada API antes de ativar em
    produção) ficam centralizados aqui para não duplicar tratamento de erro.
    """

    nome_integracao: str = ""
    mapa_endpoints: dict[str, str] = {}

    def _renovar_access_token(self, app: AplicativoIntegracao, credencial: Credencial) -> str:
        resposta = httpx.post(
            app.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": credencial.valor,
                "client_id": app.client_id,
                "client_secret": app.client_secret,
            },
            timeout=10.0,
        )
        resposta.raise_for_status()
        dados = resposta.json()
        if dados.get("refresh_token"):
            credencial.valor = dados["refresh_token"]
            credencial.save(update_fields=["valor_cifrado", "atualizado_em"])
        return dados["access_token"]

    def _request(self, metodo: str, recurso: str, ctx, **kwargs) -> ResultadoAcao:
        cliente = ctx["cliente"] if isinstance(ctx, dict) else getattr(ctx, "cliente", None)
        path = self.mapa_endpoints.get(recurso)
        if path is None:
            return ResultadoAcao(ok=False, erro_padronizado="RECURSO_NAO_ENCONTRADO")

        try:
            app = resolver_app(self.nome_integracao)
            credencial = resolver_credencial(cliente, self.nome_integracao)
            access_token = self._renovar_access_token(app, credencial)
        except ErroIntegracaoNaoConfigurada:
            return ResultadoAcao(ok=False, erro_padronizado="INTEGRACAO_NAO_CONFIGURADA")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return ResultadoAcao(ok=False, erro_padronizado="AUTH_EXPIRADA")
            if exc.response.status_code == 429:
                return ResultadoAcao(ok=False, erro_padronizado="RATE_LIMIT")
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
        except httpx.HTTPError:
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
        except (KeyError, ValueError):
            # token_url mal configurada/resposta fora do formato esperado — app
            # de integração ainda não terminou de ser configurado no admin.
            return ResultadoAcao(ok=False, erro_padronizado="INTEGRACAO_NAO_CONFIGURADA")

        try:
            resposta = httpx.request(
                metodo,
                f"{app.base_url.rstrip('/')}/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
                **kwargs,
            )
            resposta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return ResultadoAcao(ok=False, erro_padronizado="AUTH_EXPIRADA")
            if exc.response.status_code == 429:
                return ResultadoAcao(ok=False, erro_padronizado="RATE_LIMIT")
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")
        except httpx.HTTPError:
            return ResultadoAcao(ok=False, erro_padronizado="INDISPONIVEL")

        return ResultadoAcao(ok=True, dados=resposta.json())

    # ------------------------------------------------------------------
    # Contrato AdapterBase — Tier 0-1 no piloto (ver governance/tiers.py)
    # ------------------------------------------------------------------
    def consultar(self, recurso: str, filtros: dict, ctx) -> ResultadoAcao:
        return self._request("GET", recurso, ctx, params=filtros)

    def criar_rascunho(self, recurso: str, dados: dict, ctx) -> ResultadoAcao:
        return self._request("POST", recurso, ctx, json=dados)

    def alterar(self, recurso: str, id_ext: str, mudancas: dict, ctx) -> ResultadoAcao:
        # Tier 2-3 — fora do escopo do piloto (ERP travado em 0-1, ver tiers.py).
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")

    def emitir(self, documento: str, dados: dict, ctx) -> ResultadoAcao:
        return ResultadoAcao(ok=False, erro_padronizado="OPERACAO_NAO_SUPORTADA")
