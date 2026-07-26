"""
Conversa com o agente pelo terminal, sem canal nenhum.

Testar o produto exigia WhatsApp conectado (Meta ou Evolution) — ou seja, um
canal externo no caminho de toda verificação de comportamento. Isso mistura dois
tipos de falha muito diferentes: "o agente respondeu errado" e "o canal caiu".
Aqui só o orquestrador roda, exatamente como roda em produção (mesmo gate de
sessão, mesmos tiers, mesma máquina de estados) — o que muda é que a resposta
sai no terminal em vez de ir pra um número.

    python manage.py testar_conversa "qual meu estoque?"
    python manage.py testar_conversa                      # modo interativo
    python manage.py testar_conversa --cnpj 11222333000181 "emite nota de 300 pro Joao"
"""
from django.core.management.base import BaseCommand, CommandError

from apps.clients.models import Cliente
from apps.core.orchestrator import Orquestrador

CNPJ_PADRAO = "11222333000181"  # o do `preparar_teste`


class Command(BaseCommand):
    help = "Conversa com o agente pelo terminal (sem WhatsApp)."

    def add_arguments(self, parser):
        parser.add_argument("mensagem", nargs="*", help="Mensagem. Vazio = modo interativo.")
        parser.add_argument("--cnpj", default=CNPJ_PADRAO, help="CNPJ do cliente de teste.")

    def handle(self, *args, **opcoes):
        cliente = Cliente.objects.filter(cnpj=opcoes["cnpj"]).select_related("perfil").first()
        if cliente is None:
            raise CommandError(
                f"Cliente {opcoes['cnpj']} não encontrado. "
                "Rode `python manage.py preparar_teste` primeiro."
            )

        orquestrador = Orquestrador()
        self.stdout.write(self.style.SUCCESS(f"Conversando como: {cliente}\n"))

        mensagem = " ".join(opcoes["mensagem"]).strip()
        if mensagem:
            self._trocar(orquestrador, cliente, mensagem)
            return

        self.stdout.write("Modo interativo — Ctrl+C para sair.\n")
        while True:
            try:
                entrada = input("você> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("\ntchau 👋")
                return
            if entrada:
                self._trocar(orquestrador, cliente, entrada)

    def _trocar(self, orquestrador, cliente, mensagem):
        # `message_id=None` de propósito: sem idempotência por mensagem, dá pra
        # repetir a mesma frase no teste sem cair no "essa eu já processei".
        resposta = orquestrador.processar(mensagem, cliente, message_id=None)
        self.stdout.write(self.style.HTTP_INFO(f"\nvocê> {mensagem}"))
        self.stdout.write(f"lumen> {resposta}\n")
