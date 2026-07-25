"""Modelos de cliente e perfil (um perfil por cliente — princípio da arquitetura)."""
from django.db import models


class Cliente(models.Model):
    """Empresa atendida pela Magic BI (MEI/ME/EPP da base da Rotina)."""

    cnpj = models.CharField(max_length=14, unique=True)
    nome = models.CharField(max_length=200)
    telefone_whatsapp = models.CharField(
        max_length=20,
        unique=True,
        help_text="Número no formato internacional, ex.: 5511999998888",
    )
    email_contato = models.EmailField(
        blank=True,
        default="",
        help_text=(
            "E-mail cadastrado na Receita/ERP — canal do Magic Link e do código 2FA. "
            "Nunca reaproveitar o WhatsApp como canal desses segredos (2º fator de canal)."
        ),
    )
    cnae_padrao = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text=(
            "CNAE do serviço prestado, cadastrado pelo contador — nunca inferido "
            "pelo LLM (guard determinístico da emissão fiscal)."
        ),
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cliente"

    def __str__(self):
        return f"{self.nome} ({self.cnpj})"


class Perfil(models.Model):
    """Perfil de atendimento do cliente: persona, ferramentas e teto de tier.

    O motor de governança usa `tier_maximo` para recusar intenções acima do
    permitido (no piloto, ERP fica travado em Tier 0–1).
    """

    cliente = models.OneToOneField(
        Cliente, on_delete=models.CASCADE, related_name="perfil"
    )
    persona = models.CharField(max_length=40, default="lumen")
    ferramentas_habilitadas = models.JSONField(
        default=list,
        help_text='Adaptadores/ferramentas ativos, ex.: ["erp_mock", "nfse_mock"]',
    )
    tier_maximo = models.PositiveSmallIntegerField(default=1)
    valor_2fa_acima_de = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Emissões acima deste valor exigem código de 2FA por e-mail "
            "(apps/security). Vazio = 2FA desligado para este cliente."
        ),
    )

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfis"

    def __str__(self):
        return f"Perfil de {self.cliente} (tier máx. {self.tier_maximo})"
