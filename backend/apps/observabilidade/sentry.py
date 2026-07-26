"""
Captura de exceção (Sentry) — com o dado fiscal raspado antes de sair daqui.

⚠ **Ligar o Sentry adiciona um subprocessador de dados.** Este sistema trafega
CNPJ, razão social, valores e telefone — dado pessoal e fiscal de terceiros. O
projeto já tem uma pendência de DPA por causa do Groq
(`docs/magicbi-ondas-desenvolvimento.md` §7.2); mandar payload de erro pra mais
um serviço sem o mesmo cuidado só criaria a segunda.

Por isso duas decisões:

1. **Desligado por padrão.** Sem `SENTRY_DSN` no ambiente, nada é enviado —
   dev, teste e CI seguem sem tocar a rede. Ligar é ato consciente de quem
   configura o deploy, não efeito colateral de um `pip install`.
2. **Raspagem antes do envio.** `send_default_pii=False` (Sentry não anexa
   corpo de request, cookies nem IP) **mais** um `before_send` que percorre o
   evento inteiro e substitui valores por `[raspado]` — por nome de campo e por
   formato (sequências que parecem CNPJ/CPF/telefone).

O que sobra é o que serve pra depurar: tipo da exceção, stack trace, módulo,
código de erro padronizado. O que identifica cliente, não.
"""
from __future__ import annotations

import re

# Campos cujo VALOR nunca deve sair, mesmo dentro de `extra`/`contexts`.
CAMPOS_SENSIVEIS = {
    "cnpj", "cpf", "cnpj_prestador", "certificado_cnpj", "telefone",
    "telefone_whatsapp", "wa_id", "email", "email_contato", "tomador",
    "tomador_documento", "nome", "razao_social", "valor", "senha", "password",
    "pfx_senha", "api_key", "token", "client_secret", "valor_cifrado",
    "authorization", "chave_nfse", "inscricao_municipal",
}

# Sequência longa de dígitos = provável documento (CNPJ 14, CPF 11, telefone 13,
# chave NFS-e 50). Raspa por formato, não só por nome — porque o dado costuma
# vazar dentro de uma string de mensagem, onde não há nome de campo nenhum.
_RE_DOCUMENTO = re.compile(r"\b\d{11,50}\b")

RASPADO = "[raspado]"


def _raspar(valor, nome_do_campo: str = ""):
    if nome_do_campo.lower() in CAMPOS_SENSIVEIS:
        return RASPADO
    if isinstance(valor, dict):
        return {k: _raspar(v, str(k)) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return type(valor)(_raspar(v) for v in valor)
    if isinstance(valor, str):
        return _RE_DOCUMENTO.sub(RASPADO, valor)
    return valor


def before_send(evento, hint):  # noqa: ARG001 — assinatura do Sentry
    """Última barreira antes do evento sair do processo."""
    try:
        return _raspar(evento)
    except Exception:  # noqa: BLE001
        # Falha na raspagem = não temos como garantir o que sairia. Descartar o
        # evento é melhor que vazar dado fiscal por causa de um bug do scrubber.
        return None


def configurar(dsn: str, ambiente: str = "desenvolvimento") -> bool:
    """Liga o Sentry. Sem DSN, não faz nada e devolve False."""
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=ambiente,
        integrations=[DjangoIntegration()],
        send_default_pii=False,  # sem corpo de request, cookies ou IP
        before_send=before_send,
        traces_sample_rate=0.0,  # performance tracing multiplicaria o payload
        max_request_body_size="never",
    )
    return True
