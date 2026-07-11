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

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfis"

    def __str__(self):
        return f"Perfil de {self.cliente} (tier máx. {self.tier_maximo})"
