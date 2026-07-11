"""
Credenciais — SOMENTE referências ao cofre.

IMPORTANTE (seção 10 da arquitetura / LGPD):
os segredos em si (tokens OAuth, procurações, certificados) vivem em um cofre
externo (AWS Secrets Manager + KMS ou Vault — Sigillum). Nesta tabela fica
apenas a REFERÊNCIA (ARN/chave) para resolver o segredo em tempo de execução.
Nunca armazene token, senha ou .pfx cru aqui.
"""
from django.db import models

from apps.clients.models import Cliente


class Credencial(models.Model):
    """Placeholder de credencial: aponta para o segredo no cofre."""

    class Tipo(models.TextChoices):
        OAUTH = "oauth", "OAuth2"
        PROCURACAO = "procuracao", "Procuração eletrônica"
        CERTIFICADO = "certificado", "Certificado digital (via middleware)"
        TOKEN = "token", "Token de API"

    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name="credenciais"
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    referencia_cofre = models.CharField(
        max_length=255,
        help_text="ARN/chave do segredo no cofre — NUNCA o segredo em si.",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "credencial"
        verbose_name_plural = "credenciais"

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.cliente}"
