"""
Imprime a resposta CRUA de um ERP real, pra fechar o mapeamento de payload.

Por que existe: os adaptadores de Conta Azul/Bling têm os endpoints mapeados,
mas o **formato do corpo da resposta** não pôde ser confirmado sem uma conta de
acesso (ver `adapters/conta_azul.py` e `adapters/bling.py`). Mapear campo no
chute num painel financeiro não dá erro — dá número errado. Então em vez de
adivinhar, o código degrada com `PAYLOAD_NAO_MAPEADO` e este comando existe pra
transformar "bloqueado esperando sandbox" em "trabalho de minutos assim que a
conta existir":

    python manage.py inspecionar_erp 12345678000190 contas_receber

Com a resposta real na mão, escrever o normalizador em
`adapters/normalizacao.py` é copiar o molde de `bling_contas`.

⚠ A saída contém dado real do cliente (nomes, valores, documentos). É pra rodar
no terminal de quem opera, não pra colar em issue/chat. Por isso o comando
trunca por padrão e exige `--completo` pra despejar tudo.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.adapters.resolver import resolver_adapter_erp
from apps.clients.models import Cliente

RECURSOS = ("estoque", "pedidos", "contas_pagar", "contas_receber", "fluxo_caixa")


class Command(BaseCommand):
    help = "Mostra a resposta crua de um ERP real para um cliente (fechar mapeamento de payload)."

    def add_arguments(self, parser):
        parser.add_argument("cnpj", help="CNPJ do cliente (só dígitos).")
        parser.add_argument("recurso", choices=RECURSOS, help="Recurso a consultar.")
        parser.add_argument(
            "--integracao",
            default="",
            help="Força a integração (conta_azul/bling). Padrão: a do perfil do cliente.",
        )
        parser.add_argument(
            "--completo",
            action="store_true",
            help="Não trunca a saída. Cuidado: despeja dado real do cliente.",
        )

    def handle(self, *args, **opcoes):
        cliente = Cliente.objects.filter(cnpj=opcoes["cnpj"]).first()
        if cliente is None:
            raise CommandError(f"Cliente com CNPJ {opcoes['cnpj']} não encontrado.")

        if opcoes["integracao"]:
            candidatas = [opcoes["integracao"]]
        else:
            perfil = getattr(cliente, "perfil", None)
            candidatas = list(getattr(perfil, "ferramentas_habilitadas", []) or [])

        adapter = resolver_adapter_erp(cliente, candidatas)
        nome = type(adapter).__name__
        if "Mock" in nome:
            raise CommandError(
                f"Resolveu pro {nome} — este comando só serve pra ERP real. "
                "Confira a Credencial OAuth do cliente e o AplicativoIntegracao ativo."
            )

        self.stdout.write(f"Adaptador: {nome} | cliente: {cliente} | recurso: {opcoes['recurso']}")

        resultado = adapter.consultar(opcoes["recurso"], {}, {"cliente": cliente, "perfil": None})

        if not resultado.ok and resultado.erro_padronizado != "PAYLOAD_NAO_MAPEADO":
            raise CommandError(f"A consulta falhou: {resultado.erro_padronizado}")

        # PAYLOAD_NAO_MAPEADO guarda o corpo cru em `dados["bruto"]`; quando o
        # normalizador já existe, `dados` é a forma canônica — os dois casos
        # interessam (o primeiro pra escrever o mapa, o segundo pra conferi-lo).
        corpo = (resultado.dados or {}).get("bruto", resultado.dados)
        texto = json.dumps(corpo, indent=2, ensure_ascii=False, default=str)

        if not opcoes["completo"] and len(texto) > 4000:
            texto = texto[:4000] + f"\n... (truncado — {len(texto)} chars; use --completo)"

        if resultado.erro_padronizado == "PAYLOAD_NAO_MAPEADO":
            self.stdout.write(
                self.style.WARNING(
                    "Sem normalizador para este recurso — é este payload que precisa "
                    "virar um mapa em apps/adapters/normalizacao.py:"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Já normalizado — forma canônica abaixo:"))

        self.stdout.write(texto)
