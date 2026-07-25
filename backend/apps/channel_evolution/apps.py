from django.apps import AppConfig


class ChannelEvolutionConfig(AppConfig):
    """Canal WhatsApp via Evolution API — SÓ PARA TESTE LOCAL, nunca produção.

    A arquitetura já decidiu (docs/magicbi-hermes-comunicador.md §3,
    docs/requisitos-dev-piloto-rotina.md §7.1) que o canal oficial é
    exclusivamente a WhatsApp Business Cloud API — a Evolution API usa o
    protocolo não-oficial Baileys/whatsmeow por baixo, com risco de
    banimento documentado. Este app existe só para o time/escritório testar
    o fluxo ponta a ponta em uma instância Evolution que já existe, ANTES de
    configurar a Cloud API oficial da Meta (ver
    docs/magicbi-ondas-desenvolvimento.md). Nunca ativar em produção.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.channel_evolution"
    verbose_name = "Canal Evolution (SÓ TESTE LOCAL)"
