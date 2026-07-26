from django.apps import AppConfig


class ObservabilidadeConfig(AppConfig):
    """Alertas de negócio e captura de erro.

    Duas coisas diferentes, de propósito no mesmo lugar: **exceção** (bug, vai
    pro Sentry) e **rejeição fiscal** (fluxo normal, mas alguém precisa agir).
    Confundir as duas é o erro comum — rejeição da Sefin não é crash, e por isso
    nenhum error tracker do mundo ia avisar o contador sobre ela.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.observabilidade"
    verbose_name = "Observabilidade e alertas"
