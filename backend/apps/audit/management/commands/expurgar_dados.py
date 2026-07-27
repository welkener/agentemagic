"""
Expurgo por prazo de retenção — do que PODE ser apagado.

Separado de `eliminar_dados_titular` porque são coisas diferentes: aquele
atende pedido individual; este é a política de retenção rodando periodicamente.

**Os prazos estão desligados por padrão** (`None` = reter indefinidamente, que é
o comportamento atual). Ligar cada um é decisão jurídica, não técnica — o
mecanismo está pronto e testado, e passa a valer quando alguém definir o número
em `settings.RETENCAO_*`. Ver `docs/lgpd-inventario-dados.md` §3.

Este comando **não toca na trilha de auditoria**: ela é imutável e a eliminação
do conteúdo dela é por crypto-shredding (`apps/audit/conteudo.py`). Aqui só
entram tabelas operacionais, onde apagar é seguro:

- `MensagemProcessada` — guarda telefone e id de mensagem, só para idempotência.
  Depois que a janela de reenvio do WhatsApp passa, não serve mais para nada.
- `TokenMagicLink` — só o `jti`, para detectar reuso. Expirado e usado, é lixo.
- `Codigo2FA` — hash de código de 6 dígitos, já usado ou expirado.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.channel_whatsapp.models import MensagemProcessada
from apps.security.models import Codigo2FA, TokenMagicLink

# (setting, model, campo de data, descrição)
POLITICAS = [
    ("RETENCAO_MENSAGENS_PROCESSADAS_DIAS", MensagemProcessada, "recebido_em",
     "ids de mensagem + telefone (idempotência do webhook)"),
    ("RETENCAO_TOKENS_MAGIC_LINK_DIAS", TokenMagicLink, "expira_em",
     "tokens de Magic Link expirados"),
    ("RETENCAO_CODIGOS_2FA_DIAS", Codigo2FA, "expira_em",
     "códigos 2FA expirados"),
]


class Command(BaseCommand):
    help = "Apaga dados operacionais fora do prazo de retenção configurado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--conferir", action="store_true", help="Só conta o que seria apagado."
        )

    def handle(self, *args, **opcoes):
        w = self.stdout.write
        agora = timezone.now()
        nenhuma_configurada = True

        for nome_setting, model, campo_data, descricao in POLITICAS:
            dias = getattr(settings, nome_setting, None)
            if not dias:
                w(f"{nome_setting}: não definido — retendo indefinidamente ({descricao})")
                continue

            nenhuma_configurada = False
            corte = agora - timedelta(days=int(dias))
            alvo = model.objects.filter(**{f"{campo_data}__lt": corte})
            quantos = alvo.count()

            if opcoes["conferir"]:
                w(f"{nome_setting}={dias}d: {quantos} registro(s) seriam apagados ({descricao})")
            else:
                alvo.delete()
                w(self.style.SUCCESS(f"{nome_setting}={dias}d: {quantos} apagado(s) ({descricao})"))

        if nenhuma_configurada:
            w(
                self.style.WARNING(
                    "\nNenhum prazo configurado — nada foi apagado. Isto é o padrão de "
                    "propósito: definir prazo de retenção é decisão jurídica. Quando "
                    "houver, defina RETENCAO_*_DIAS no settings."
                )
            )
        w(
            "\nA trilha de auditoria não é tocada aqui: ela é imutável, e a eliminação "
            "do conteúdo dela é por `eliminar_dados_titular`."
        )
