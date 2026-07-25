"""Envio de mensagem via Evolution API — canal de TESTE LOCAL apenas (ver apps.py)."""
from dataclasses import dataclass

import httpx
import structlog
from django.conf import settings

from .models import configuracao_ativa

logger = structlog.get_logger(__name__)


@dataclass
class CredenciaisEvolution:
    base_url: str
    api_key: str
    instancia: str

    @property
    def configurada(self) -> bool:
        return bool(self.base_url and self.api_key and self.instancia)


def resolver_credenciais() -> CredenciaisEvolution:
    """Configuração ativa no admin tem prioridade; `.env` é o fallback/bootstrap."""
    config = configuracao_ativa()
    if config is not None:
        return CredenciaisEvolution(base_url=config.base_url, api_key=config.api_key, instancia=config.instancia)
    return CredenciaisEvolution(
        base_url=settings.EVOLUTION_BASE_URL,
        api_key=settings.EVOLUTION_API_KEY,
        instancia=settings.EVOLUTION_INSTANCE,
    )


def enviar_mensagem(telefone: str, texto: str) -> bool:
    """Envia texto via `POST {base_url}/message/sendText/{instance}`.

    Sem configuração (nem no admin, nem no `.env`), só loga — mesmo padrão de
    degradação do canal oficial, mantém o fluxo testável offline.
    """
    cred = resolver_credenciais()
    if not cred.configurada:
        logger.info(
            "evolution_envio_simulado (sem configuração ativa nem EVOLUTION_* no .env)",
            telefone=telefone,
            texto=texto,
        )
        return True

    url = f"{cred.base_url.rstrip('/')}/message/sendText/{cred.instancia}"
    try:
        resposta = httpx.post(
            url,
            json={"number": telefone, "text": texto},
            headers={"apikey": cred.api_key},
            timeout=15.0,
        )
        resposta.raise_for_status()
        logger.info("evolution_mensagem_enviada", telefone=telefone)
        return True
    except httpx.HTTPError as exc:
        logger.error("evolution_envio_falhou", telefone=telefone, erro=str(exc))
        return False


def testar_conexao() -> tuple[bool, str]:
    """Chama `GET /instance/connectionState/{instance}` — usado pela ação 'Testar
    conexão' do admin. Devolve (ok, mensagem) já pronta pra mostrar ao usuário."""
    cred = resolver_credenciais()
    if not cred.configurada:
        return False, "Configuração incompleta (base_url/api_key/instância)."

    url = f"{cred.base_url.rstrip('/')}/instance/connectionState/{cred.instancia}"
    try:
        resposta = httpx.get(url, headers={"apikey": cred.api_key}, timeout=10.0)
        resposta.raise_for_status()
        estado = resposta.json().get("instance", {}).get("state", "desconhecido")
        if estado == "open":
            return True, "Conectado ✅"
        return False, f"Instância respondeu, mas não está conectada (estado: {estado})."
    except httpx.HTTPError as exc:
        return False, f"Não consegui falar com a instância: {exc}"
