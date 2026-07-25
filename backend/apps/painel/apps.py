from django.apps import AppConfig


class PainelConfig(AppConfig):
    """Grimório mínimo: dashboard de demonstração/homologação para o contador.

    Não substitui o Django admin (continua sendo o CRUD completo) — é uma
    visão consolidada e legível de notas emitidas, atividade capturada
    (auditoria), status dos canais e credenciais, pensada pra mostrar o
    sistema funcionando sem precisar navegar entre várias telas do admin.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.painel"
    verbose_name = "Painel (Grimório)"
