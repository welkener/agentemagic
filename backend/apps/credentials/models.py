"""
Credenciais por cliente + configuração de app por integração.

IMPORTANTE (seção 10 da arquitetura / LGPD):
em produção (AWS `sa-east-1`) os segredos vivem no Secrets Manager + KMS
("Sigillum") — `referencia_cofre` aponta para lá. Nesta fase (MVP/piloto,
ainda sem cloud), o segredo pode ser digitado no Django admin e fica cifrado
em repouso com Fernet (`valor_cifrado`, ver `crypto.py`) — nunca em texto
puro. É a troca de backend de armazenamento; o contrato não muda quando o
Secrets Manager entrar. Nunca logue/serialize o valor decifrado.
"""
from django.db import models

from apps.clients.models import Cliente

from .crypto import CampoTextoCifrado, cifrar, decifrar


class Credencial(models.Model):
    """Segredo por cliente: OAuth (Conta Azul/Bling), procuração ou token."""

    class Tipo(models.TextChoices):
        OAUTH = "oauth", "OAuth2"
        PROCURACAO = "procuracao", "Procuração eletrônica"
        CERTIFICADO = "certificado", "Certificado digital (via middleware)"
        TOKEN = "token", "Token de API"

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="credenciais"
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    integracao = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text='Ex.: "conta_azul", "bling", "nfse_nacional" — casa com AplicativoIntegracao.nome.',
    )
    referencia_cofre = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Produção: ARN/chave no Secrets Manager. Deixe em branco no piloto.",
    )
    valor_cifrado = CampoTextoCifrado(
        blank=True,
        null=True,
        help_text="Piloto: access/refresh token ou identificador da procuração, cifrado com Fernet.",
    )
    expira_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "credencial"
        verbose_name_plural = "credenciais"

    @property
    def valor(self) -> str:
        """Decifra sob demanda — nunca guardar o retorno em log/auditoria.

        `valor_cifrado` já vem decifrado (str) quando carregado do banco
        (`CampoTextoCifrado.from_db_value`); só é bytes cru entre o setter e
        o próximo save/refresh.
        """
        dado = self.valor_cifrado
        if not dado:
            return ""
        return dado if isinstance(dado, str) else decifrar(bytes(dado))

    @valor.setter
    def valor(self, texto_puro: str) -> None:
        self.valor_cifrado = cifrar(texto_puro) if texto_puro else None

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.cliente}"


class AplicativoIntegracao(models.Model):
    """Config do app (client_id/secret) registrado pela Magic BI numa integração.

    Diferente de `Credencial` (segredo POR CLIENTE): isto é o cadastro do app
    de desenvolvedor (ex.: app OAuth2 do Conta Azul), compartilhado por todos
    os clientes que conectam aquele adaptador. Editável só pelo admin.
    """

    class Nome(models.TextChoices):
        CONTA_AZUL = "conta_azul", "Conta Azul"
        BLING = "bling", "Bling"
        NFSE_NACIONAL = "nfse_nacional", "NFS-e Nacional (Emissor Nacional/ADN)"

    nome = models.CharField(max_length=30, choices=Nome.choices, unique=True)
    ambiente = models.CharField(
        max_length=20, default="homologacao", help_text='"homologacao" ou "producao".'
    )
    base_url = models.URLField(
        help_text="⚠ preencher com a URL vigente da API (muda entre homologação/produção)."
    )
    token_url = models.URLField(blank=True, default="", help_text="Endpoint OAuth2 de token, se aplicável.")
    client_id = models.CharField(max_length=255, blank=True, default="")
    client_secret_cifrado = CampoTextoCifrado(blank=True, null=True)
    ativo = models.BooleanField(default=False, help_text="Só usado pelo resolver de adapters se ativo.")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "aplicativo de integração"
        verbose_name_plural = "aplicativos de integração"

    @property
    def client_secret(self) -> str:
        """Ver nota em `Credencial.valor` sobre o campo já vir decifrado do banco."""
        dado = self.client_secret_cifrado
        if not dado:
            return ""
        return dado if isinstance(dado, str) else decifrar(bytes(dado))

    @client_secret.setter
    def client_secret(self, texto_puro: str) -> None:
        self.client_secret_cifrado = cifrar(texto_puro) if texto_puro else None

    def __str__(self):
        return f"{self.get_nome_display()} ({self.ambiente})"
