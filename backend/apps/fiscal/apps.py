from django.apps import AppConfig


class FiscalConfig(AppConfig):
    """Geração, assinatura e numeração da DPS da NFS-e Nacional.

    Separado de `apps/adapters` de propósito: o adapter é *transporte* (fala
    HTTP com o ADN/Sefin), isto aqui é *documento fiscal* (monta o XML pelo
    schema oficial e assina com o certificado ICP-Brasil). Misturar os dois
    deixaria a regra fiscal escondida dentro de código de rede.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fiscal"
    verbose_name = "Documento fiscal (DPS/NFS-e)"
