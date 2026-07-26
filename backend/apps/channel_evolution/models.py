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
    escritorio = models.ForeignKey(
        "tenants.Escritorio",
        on_delete=models.CASCADE,
        related_name="configuracoes_evolution",
        null=True,
        blank=True,
        help_text=(
            "Escritório dono desta instância. Em branco = configuração global de "
            "bootstrap (só faz sentido em instalação de um tenant só)."
        ),
    )
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


def configuracao_ativa(escritorio=None) -> "ConfiguracaoEvolution | None":
    """Configuração ativa — do escritório, se informado.

    Com `escritorio`, aceita também a configuração global (`escritorio=None`)
    como fallback, pra não quebrar a instalação de teste que já está no ar.
    """
    qs = ConfiguracaoEvolution.objects.filter(ativo=True)
    if escritorio is not None:
        qs = qs.filter(models.Q(escritorio=escritorio) | models.Q(escritorio__isnull=True))
        # Preferir a do próprio escritório sobre a global.
        return qs.order_by(models.F("escritorio").desc(nulls_last=True), "-atualizado_em").first()
    return qs.order_by("-atualizado_em").first()


def escritorio_por_instancia(instancia: str):
    """Resolve o tenant pela instância Evolution que recebeu a mensagem.

    Mesmo contrato do canal oficial (`painel.models.escritorio_por_phone_number_id`):
    instalação de um tenant só continua funcionando por fallback; a partir do
    segundo, mensagem sem instância casada é recusada em vez de ir pro tenant
    errado.
    """
    from apps.tenants.models import Escritorio

    if instancia:
        config = (
            ConfiguracaoEvolution.objects.filter(instancia=instancia, escritorio__isnull=False)
            .select_related("escritorio")
            .first()
        )
        if config is not None:
            return config.escritorio

    ativos = list(Escritorio.objects.filter(ativo=True)[:2])
    return ativos[0] if len(ativos) == 1 else None
