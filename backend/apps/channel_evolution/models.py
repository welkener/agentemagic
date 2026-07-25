"""
Configuração da instância Evolution — editável pelo painel (admin), não só
`.env`. SÓ TESTE LOCAL, nunca produção (ver `apps.py`).

Por que um model em vez de só variável de ambiente: o pedido foi "deixar o
painel do contador funcionando... configuração do whatsapp" — o contador não
mexe em `.env`/deploy, mexe no admin. `.env` continua funcionando como
fallback/bootstrap (`settings.EVOLUTION_*`) se não houver configuração ativa
no banco — nunca quebra quem já estava usando variável de ambiente.
"""
from django.db import models

from apps.credentials.crypto import CampoTextoCifrado, cifrar, decifrar


class ConfiguracaoEvolution(models.Model):
    nome = models.CharField(
        max_length=60, default="padrão", help_text="Só identificação — ex.: 'instância de testes'."
    )
    base_url = models.URLField(help_text="URL da instância, ex.: https://evolution.seudominio.com")
    instancia = models.CharField(max_length=100, help_text="Nome da instância cadastrada na Evolution.")
    api_key_cifrada = CampoTextoCifrado(
        blank=True, null=True, help_text="Cifrada em repouso — nunca reexibida depois de salva."
    )
    ativo = models.BooleanField(
        default=False, help_text="Só a configuração ativa mais recente é usada pelo canal."
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuração Evolution (teste local)"
        verbose_name_plural = "configurações Evolution (teste local)"

    @property
    def api_key(self) -> str:
        dado = self.api_key_cifrada
        if not dado:
            return ""
        return dado if isinstance(dado, str) else decifrar(bytes(dado))

    @api_key.setter
    def api_key(self, texto_puro: str) -> None:
        self.api_key_cifrada = cifrar(texto_puro) if texto_puro else None

    def __str__(self):
        return f"{self.nome} ({self.instancia}) — {'ativa' if self.ativo else 'inativa'}"


def configuracao_ativa() -> "ConfiguracaoEvolution | None":
    return ConfiguracaoEvolution.objects.filter(ativo=True).order_by("-atualizado_em").first()
