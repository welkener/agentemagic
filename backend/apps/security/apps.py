from django.apps import AppConfig


class SecurityConfig(AppConfig):
    """Identidade de sessão: vínculo wa_id↔CNPJ, Magic Link, 2FA.

    Distinto de `apps.governance` (decide O QUE pode ser feito) e da
    custódia do certificado fiscal (`docs/magicbi-custodia-fiscal.md` — quem
    assina perante o governo). Este app decide QUEM está falando no
    WhatsApp. Spec completa: `docs/magicbi-seguranca-sessao.md`.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.security"
    label = "security"
    verbose_name = "Segurança de sessão"
